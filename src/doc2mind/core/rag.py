"""RAG 编排 — 检索知识库 → 构建上下文 → 多轮对话 → 生成回答。

流程：
    query → Retriever.search()
         → 构建带来源标注的上下文
         → 合并会话历史（chat_id 维度）
         → 调 LLM 生成回答
         → 返回答案 + 来源列表

会话历史双层存储：SQLite（chat_sessions/chat_messages 表，重启可恢复，
供 /v1/chats 回看）+ 进程内 LRU dict（快速路径 / DB 故障降级）。
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field, replace as dc_replace
from pathlib import Path
from typing import Any, Iterator

from doc2mind.core.config import Settings, get_settings
from doc2mind.core.embedder import get_embedder
from doc2mind.core.llm import LLMClient, LLMError, get_llm_client
from doc2mind.core.retriever.search import Retriever, SearchHit
from doc2mind.core.store.chat_store import ChatStore, ChatStoreError
from doc2mind.core.store.sqlite_vec import VectorStore

logger = logging.getLogger(__name__)

# 系统提示词：DocMind 智能知识专家与 Agent 思考准则
_SYSTEM_PROMPT = (
    "你是 DocMind 知识库的智能架构师与技术专家 Copilot Agent。\n"
    "你的任务是深入、严谨、条理清晰地解答用户关于技术、设计原理、踩坑排错和选型对比的问题。\n\n"
    "【思考与回答准则】\n"
    "1. 【深入透彻】：不要给出死板机械的简单复述，要结合上下文深入剖析「核心机制、设计考量、最佳实践、潜在隐患/踩坑防范」；\n"
    "2. 【多维溯源】：优先参考【本地知识库原著切片】（本地 Ground Truth），并融合【知识图谱实体拓扑】与【实时联网资料】；对关键事实标注对应的引用编号（如 [1]、[2]）；\n"
    "3. 【实战导向】：涉及代码或实现时，提供结构良好、带有中文注释的代码片段或架构逻辑；\n"
    "4. 【结构清晰】：善用 Markdown 标题、清晰层级、表格对比与加粗强调；\n"
    "5. 【Agent 主动洞察】：在回答末尾，简明提炼出 1-2 条高价值的「💡 架构洞察 / 知识沉淀建议」，帮助用户沉淀核心经验。"
)

# 会话历史上限（条），防止内存无限增长
_MAX_HISTORY = 20

# 进程内最多保留的会话数（LRU 淘汰最久未使用的会话）
_MAX_SESSIONS = 100


def _estimate_tokens(text: str, chars_per_token: float = 2.5) -> int:
    """粗略估算文本 token 数。中文 ~1 token ≈ 2-3 字符,英文 ~1 token ≈ 4 字符。
    用 chars_per_token 配置值做字符数除法,避免引入 tiktoken 依赖。"""
    if not text:
        return 0
    return max(1, int(len(text) / max(0.1, chars_per_token)))


def _truncate_history_by_token_budget(
    history: list[dict[str, str]],
    max_tokens: int,
    chars_per_token: float = 2.5,
) -> list[dict[str, str]]:
    """按 token 预算从最新消息向前截断历史。

    - max_tokens <= 0: 不截断,返回原列表(仍受 _MAX_HISTORY 条上限保护)
    - 从最新消息向前累计 token,超过预算时停止;被截断的早期历史用占位消息替代
    """
    if max_tokens <= 0 or not history:
        return history

    kept: list[dict[str, str]] = []
    used = 0
    for msg in reversed(history):
        msg_tokens = _estimate_tokens(msg.get("content", ""), chars_per_token)
        if used + msg_tokens > max_tokens and kept:
            break
        kept.append(msg)
        used += msg_tokens

    kept.reverse()  # 恢复时间顺序
    dropped = len(history) - len(kept)
    if dropped > 0:
        # 在历史开头插入占位消息,让 LLM 知道有早期对话被省略
        kept.insert(0, {"role": "system", "content": f"…(已省略 {dropped} 条早期对话)"})
    return kept


class RagError(Exception):
    """RAG 对话异常。"""


@dataclass(frozen=True)
class SourceRef:
    """回答引用来源。"""

    index: int
    source: str
    format: str
    chunk_id: int | None = None
    page: int | None = None
    heading: str | None = None
    score: float = 0.0
    source_type: str = "local"  # "local" | "web"
    url: str | None = None
    title: str | None = None


@dataclass(frozen=True)
class RagAnswer:
    """RAG 对话回答。"""

    answer: str
    sources: list[SourceRef] = field(default_factory=list)
    chat_id: str | None = None
    elapsed_ms: int = 0
    total_chunks: int = 0
    model: str = ""
    provider: str = ""


# --- 会话历史（进程内 LRU） ---
_HISTORY_LOCK = threading.Lock()
_CHAT_SESSIONS: OrderedDict[str, list[dict[str, str]]] = OrderedDict()


def _new_chat_id() -> str:
    return f"chat-{uuid.uuid4().hex[:12]}"


def _evict_sessions_locked() -> None:
    """淘汰最久未使用的会话（调用方必须已持有 _HISTORY_LOCK）。"""
    while len(_CHAT_SESSIONS) > _MAX_SESSIONS:
        _CHAT_SESSIONS.popitem(last=False)


def _load_history(chat_id: str | None, db_path: Path | None = None) -> tuple[str, list[dict[str, str]]]:
    """按 chat_id 取历史；None 时新建会话。返回 (chat_id, history)。

    db_path 提供时，内存未命中（典型：后端重启后继续旧会话）会从
    SQLite 恢复最近 _MAX_HISTORY 条并回填内存缓存。
    """
    cid = chat_id or _new_chat_id()
    with _HISTORY_LOCK:
        if chat_id and chat_id in _CHAT_SESSIONS:
            _CHAT_SESSIONS.move_to_end(chat_id)  # LRU：最近使用移到尾部
            return chat_id, list(_CHAT_SESSIONS[chat_id])
        _CHAT_SESSIONS.setdefault(cid, [])
        _evict_sessions_locked()
    if chat_id and db_path is not None:
        try:
            db_history = ChatStore(db_path).get_history(chat_id, _MAX_HISTORY)
        except ChatStoreError as e:  # pragma: no cover — get_history 内部已吞 DB 错
            logger.warning("从 DB 恢复会话历史失败: %s", e)
            db_history = []
        if db_history:
            with _HISTORY_LOCK:
                _CHAT_SESSIONS[chat_id] = db_history[-_MAX_HISTORY:]
                _CHAT_SESSIONS.move_to_end(chat_id)
            return chat_id, list(db_history)
    return cid, []


def _save_history(chat_id: str, history: list[dict[str, str]]) -> None:
    """截断并保存历史（仅内存 LRU；持久化走 _append_turn）。"""
    with _HISTORY_LOCK:
        _CHAT_SESSIONS[chat_id] = history[-_MAX_HISTORY:]
        _CHAT_SESSIONS.move_to_end(chat_id)
        _evict_sessions_locked()


def _append_turn(
    chat_id: str,
    user_content: str,
    assistant_content: str | None,
    db_path: Path | None = None,
) -> None:
    """记录一轮对话：内存 LRU 更新 + SQLite 持久化。

    DB 写失败时降级为仅内存（记 warning），不阻断对话——持久化是增强
    能力而非硬依赖。assistant_content 为 None 表示本轮无回答（如无检索
    命中时的提前返回）。
    """
    with _HISTORY_LOCK:
        history = _CHAT_SESSIONS.get(chat_id, [])
        history = history + [{"role": "user", "content": user_content}]
        if assistant_content is not None:
            history = history + [{"role": "assistant", "content": assistant_content}]
        _CHAT_SESSIONS[chat_id] = history[-_MAX_HISTORY:]
        _CHAT_SESSIONS.move_to_end(chat_id)
        _evict_sessions_locked()

    if db_path is None:
        return
    try:
        store = ChatStore(db_path)
        # 首条用户消息生成会话标题（问题前 50 字）
        store.append_message(chat_id, "user", user_content, title_hint=user_content)
        if assistant_content is not None:
            store.append_message(chat_id, "assistant", assistant_content)
    except ChatStoreError as e:
        logger.warning(
            "会话持久化失败（已降级为仅内存，重启后该会话历史丢失）: %s", e
        )

def clear_session(chat_id: str, db_path: Path | None = None) -> None:
    """清除指定会话历史（同时清内存和 SQLite）。"""
    with _HISTORY_LOCK:
        _CHAT_SESSIONS.pop(chat_id, None)
    if db_path is not None:
        try:
            ChatStore(db_path).delete_session(chat_id)
        except ChatStoreError as e:
            logger.warning("删除 SQLite 会话失败（内存已清）: %s", e)


def rag_answer(
    query: str,
    collection: str | None = "default",
    top_k: int | None = None,
    chat_id: str | None = None,
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
    collections: list[str] | None = None,
    model_override: str | None = None,
    enable_web_search: bool = False,
    entity_context: str | None = None,
) -> RagAnswer:
    """RAG 问答主入口（非流式，一次性返回完整回答）。"""
    s = settings or get_settings()
    t0 = time.perf_counter()

    # 0. 按请求覆盖模型名
    if model_override and not llm_client:
        s = dc_replace(s, llm_model=model_override.strip())

    # 1. 解析会话
    cid, history = _load_history(chat_id, s.db_path)

    # 2. LLM 客户端
    try:
        client = llm_client or get_llm_client(s)
    except LLMError as e:
        raise RagError(f"LLM 配置错误: {e}") from e
    if client is None:
        raise RagError(
            "未配置 LLM。请在 WPF「设置 → 大模型对话」选择提供商并填写 API Key，"
            "或设置环境变量 DOC2MIND_LLM_PROVIDER（openai/ollama/anthropic/gemini）。"
        )

    # 3-5. 检索 + 构建上下文 + 组装消息 (融合本地切片 + 实体拓扑 + 实时联网资料)
    hits, context, sources, messages = _build_context_and_messages(
        query=query, collection=collection, top_k=top_k, s=s,
        collections=collections, history=history, t0=t0,
        enable_web_search=enable_web_search, entity_context=entity_context,
    )

    # 4.5 无命中且无外部/实体上下文时提前返回
    if not context and not entity_context and not enable_web_search:
        answer = "知识库中未找到与问题相关的内容，我无法回答。请先摄入相关文档再提问。"
        _append_turn(cid, query, None, s.db_path)
        return RagAnswer(
            answer=answer,
            sources=[],
            chat_id=cid,
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
            total_chunks=0,
            model=client.model_name,
            provider=client.provider,
        )

    # 6. 调 LLM
    llm_timeout = (s.llm_timeout if s.llm_timeout > 0 else None)
    try:
        reply = client.chat(messages, timeout=llm_timeout)
    except LLMError as e:
        raise RagError(str(e)) from e

    # 7. 保存历史
    _append_turn(cid, query, reply, s.db_path)

    return RagAnswer(
        answer=reply,
        sources=sources,
        chat_id=cid,
        elapsed_ms=int((time.perf_counter() - t0) * 1000),
        total_chunks=len(sources),
        model=client.model_name,
        provider=client.provider,
    )


def rag_answer_stream(
    query: str,
    collection: str | None = "default",
    top_k: int | None = None,
    chat_id: str | None = None,
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
    collections: list[str] | None = None,
    model_override: str | None = None,
    enable_web_search: bool = False,
    entity_context: str | None = None,
) -> Iterator[str]:
    """RAG 流式问答，逐 token 产出 SSE 格式 JSON 行。"""
    s = settings or get_settings()
    t0 = time.perf_counter()

    # 0. 按请求覆盖模型名
    if model_override and not llm_client:
        s = dc_replace(s, llm_model=model_override.strip())

    # 1. 解析会话
    cid, history = _load_history(chat_id, s.db_path)

    # 2. LLM 客户端
    try:
        client = llm_client or get_llm_client(s)
    except LLMError as e:
        raise RagError(f"LLM 配置错误: {e}") from e
    if client is None:
        raise RagError(
            "未配置 LLM。请在 WPF「设置 → 大模型对话」选择提供商并填写 API Key，"
            "或设置环境变量 DOC2MIND_LLM_PROVIDER（openai/ollama/anthropic/gemini）。"
        )

    # 3-5. 检索 + 构建上下文 + 组装消息
    hits, context, sources, messages = _build_context_and_messages(
        query=query, collection=collection, top_k=top_k, s=s,
        collections=collections, history=history, t0=t0,
        enable_web_search=enable_web_search, entity_context=entity_context,
    )

    if not context and not entity_context and not enable_web_search:
        _append_turn(cid, query, None, s.db_path)
        yield json.dumps({
            "token": "知识库中未找到与问题相关的内容，我无法回答。请先摄入相关文档再提问。",
        }, ensure_ascii=False)
        yield json.dumps({
            "done": True,
            "chat_id": cid,
            "model": client.model_name,
            "provider": client.provider,
            "total_chunks": 0,
            "elapsed_ms": int((time.perf_counter() - t0) * 1000),
            "sources": [],
        }, ensure_ascii=False)
        return

    # 6. 流式调 LLM
    llm_timeout = (s.llm_timeout if s.llm_timeout > 0 else None)
    collected = []
    try:
        for token in client.stream_chat(messages, timeout=llm_timeout):
            collected.append(token)
            yield json.dumps({"token": token}, ensure_ascii=False)
    except LLMError as e:
        raise RagError(str(e)) from e

    reply = "".join(collected)

    # 7. 保存历史
    _append_turn(cid, query, reply, s.db_path)

    # 终帧
    elapsed = int((time.perf_counter() - t0) * 1000)
    yield json.dumps({
        "done": True,
        "chat_id": cid,
        "model": client.model_name,
        "provider": client.provider,
        "total_chunks": len(sources),
        "elapsed_ms": elapsed,
        "sources": [
            {
                "index": src.index,
                "source": src.source,
                "chunk_id": src.chunk_id,
                "format": src.format,
                "page": src.page,
                "heading": src.heading,
                "score": src.score,
                "source_type": src.source_type,
                "url": src.url,
                "title": src.title,
            }
            for src in sources
        ],
    }, ensure_ascii=False)


def _format_context(hits: list[SearchHit]) -> tuple[str, list[SourceRef]]:
    """将检索命中的 SearchHit 格式化为上下文文本与 SourceRef 引用列表。"""
    blocks: list[str] = []
    sources: list[SourceRef] = []
    for i, h in enumerate(hits, start=1):
        meta = h.chunk
        page_info = f", P{meta.page}" if meta.page is not None else ""
        heading_info = f", 章节: {meta.heading}" if meta.heading else ""
        source_label = f"[{i}] 《{meta.source}》{page_info}{heading_info} (相似度: {h.score:.2f})"
        blocks.append(f"{source_label}\n{meta.content}")
        sources.append(
            SourceRef(
                index=i,
                source=meta.source,
                format=meta.format,
                chunk_id=meta.id,
                page=meta.page,
                heading=meta.heading,
                score=h.score,
                source_type="local",
                title=meta.source,
            )
        )
    return "\n\n".join(blocks), sources


def _build_context_and_messages(
    query: str,
    collection: str | None,
    top_k: int | None,
    s: Settings,
    collections: list[str] | None,
    history: list[dict[str, str]],
    t0: float,
    enable_web_search: bool = False,
    entity_context: str | None = None,
) -> tuple[list[SearchHit], str, list[SourceRef], list[dict[str, str]]]:
    """检索 + 构建多源上下文 + 组装消息（融合本地切片 + 实体拓扑 + 实时联网检索）。"""
    hits: list[SearchHit] = []
    sources: list[SourceRef] = []
    context_blocks: list[str] = []

    # 1. 实体 High-level 拓扑上下文注入（LightRAG 拓扑思想）
    if entity_context and entity_context.strip():
        context_blocks.append(f"【知识图谱当前实体拓扑与背景】\n{entity_context.strip()}")

    # 2. 本地 Low-level 原著切片检索
    try:
        if collections:
            cleaned = [c.strip() for c in collections if c and c.strip()]
            search_collection: str | list[str] | None = cleaned or None
        else:
            search_collection = collection

        store, embedder = _open_store(s)
        try:
            retriever = Retriever(store=store, embedder=embedder)
            hits, _ = retriever.search(
                query=query,
                collection=search_collection,
                top_k=top_k or s.rag_top_k,
                min_score=0.0,
            )
        finally:
            store.close()

        if s.rag_min_score > 0:
            hits = [h for h in hits if max(h.vector_score, h.bm25_score) >= s.rag_min_score]

        if hits:
            local_ctx, local_sources = _format_context(hits)
            if local_ctx:
                context_blocks.append(f"【本地知识库原著切片 (Local Knowledge)】\n{local_ctx}")
                sources.extend(local_sources)
    except Exception as e:
        logger.warning("本地切片检索异常 (已继续执行): %s", e)

    # 3. 实时联网搜索融合 (免 Key 引擎 WebSearchService)
    if enable_web_search:
        try:
            from doc2mind.core.search.web_search import get_web_search_service
            web_results = get_web_search_service().search(query, max_results=4)
            if web_results:
                web_ctx_lines = []
                start_idx = len(sources) + 1
                for i, wr in enumerate(web_results, start=start_idx):
                    web_ctx_lines.append(f"[{i}] {wr.title} ({wr.url})\n{wr.snippet}")
                    sources.append(
                        SourceRef(
                            index=i,
                            source=wr.url,
                            format="web",
                            source_type="web",
                            url=wr.url,
                            title=wr.title,
                            score=1.0,
                        )
                    )
                context_blocks.append(f"【实时联网检索资料 (Live Web Search)】\n" + "\n\n".join(web_ctx_lines))
        except Exception as ex:
            logger.warning("联网搜索执行失败: %s", ex)

    full_context = "\n\n---\n\n".join(context_blocks)

    # 4. 组装消息
    system_prompt = _SYSTEM_PROMPT
    if s.rag_system_prompt and s.rag_system_prompt.strip():
        system_prompt = s.rag_system_prompt.strip()

    if enable_web_search or entity_context:
        system_prompt += (
            "\n你同时具备本地工程知识与全域技术视野。"
            "请结合本地事实与联网前沿资料给出深入透彻、带有代码示例与注释的专业解答。"
        )

    if full_context:
        user_content = f"以下是相关参考资料与上下文：\n\n{full_context}\n\n---\n请基于以上背景与资料回答：{query}"
    else:
        user_content = query

    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    # 历史截断
    truncated_history = _truncate_history_by_token_budget(
        history, s.rag_max_history_tokens, s.chars_per_token
    )[-_MAX_HISTORY:]
    messages = messages[:1] + truncated_history + messages[1:]

    return hits, full_context, sources, messages


def _open_store(settings: Settings) -> tuple[VectorStore, Any]:
    """打开向量存储 + 嵌入引擎。"""
    embedder = get_embedder(settings)
    store = VectorStore(settings.db_path, embedder.dimension)
    store.open()
    return store, embedder

