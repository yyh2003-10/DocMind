"""RAG 编排 — 检索知识库 → 构建上下文 → 多轮对话 → 生成回答。

流程：
    query → Retriever.search()
         → 构建带来源标注的上下文
         → 合并会话历史（chat_id 维度，进程内存储）
         → 调 LLM 生成回答
         → 返回答案 + 来源列表

会话历史进程内保存（内存 LRU dict），重启失效；
单会话上限 20 条、进程内最多保留 100 个会话，防内存膨胀。
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Iterator

from doc2mind.core.config import Settings, get_settings
from doc2mind.core.embedder import get_embedder
from doc2mind.core.llm import LLMClient, LLMError, get_llm_client
from doc2mind.core.retriever.search import Retriever, SearchHit
from doc2mind.core.store.sqlite_vec import VectorStore

# 系统提示词：要求基于资料回答，注明来源，不知道就说不知道
_SYSTEM_PROMPT = (
    "你是一个知识库问答助手。请仅根据提供的资料回答问题，不要编造内容。\n"
    "回答要点：\n"
    "1. 优先使用资料中的原文信息，引用来源标注为 [1]、[2] 等编号；\n"
    "2. 资料不足以回答时，明确说明「资料中未找到相关信息」，不要猜测；\n"
    "3. 回答使用与问题相同的语言；\n"
    "4. 保持简洁、结构化，必要时用列表。"
)

# 会话历史上限（条），防止内存无限增长
_MAX_HISTORY = 20

# 进程内最多保留的会话数（LRU 淘汰最久未使用的会话）
_MAX_SESSIONS = 100


class RagError(Exception):
    """RAG 对话异常。"""


@dataclass(frozen=True)
class SourceRef:
    """回答引用来源。"""

    index: int
    source: str
    format: str
    page: int | None = None
    heading: str | None = None
    score: float = 0.0


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


def _load_history(chat_id: str | None) -> tuple[str, list[dict[str, str]]]:
    """按 chat_id 取历史；None 时新建会话。返回 (chat_id, history)。"""
    cid = chat_id or _new_chat_id()
    with _HISTORY_LOCK:
        if chat_id and chat_id in _CHAT_SESSIONS:
            _CHAT_SESSIONS.move_to_end(chat_id)  # LRU：最近使用移到尾部
            return chat_id, list(_CHAT_SESSIONS[chat_id])
        _CHAT_SESSIONS.setdefault(cid, [])
        _evict_sessions_locked()
        return cid, []


def _save_history(chat_id: str, history: list[dict[str, str]]) -> None:
    """截断并保存历史。"""
    with _HISTORY_LOCK:
        _CHAT_SESSIONS[chat_id] = history[-_MAX_HISTORY:]
        _CHAT_SESSIONS.move_to_end(chat_id)
        _evict_sessions_locked()


def clear_session(chat_id: str) -> bool:
    """清除指定会话历史。"""
    with _HISTORY_LOCK:
        return _CHAT_SESSIONS.pop(chat_id, None) is not None


def _format_source_ref(index: int, hit: SearchHit) -> str:
    """把检索命中渲染成带编号的来源引用行。"""
    c = hit.chunk
    loc = c.source
    if c.page is not None:
        loc += f" p.{c.page}"
    if c.heading:
        loc += f"（{c.heading}）"
    return f"[{index}] {loc}\n{c.content}"


def _build_context(hits: list[SearchHit]) -> tuple[str, list[SourceRef]]:
    """构建上下文文本 + 来源引用列表。"""
    context_lines = []
    sources: list[SourceRef] = []
    for i, h in enumerate(hits, start=1):
        context_lines.append(_format_source_ref(i, h))
        sources.append(
            SourceRef(
                index=i,
                source=h.chunk.source,
                format=h.chunk.format,
                page=h.chunk.page,
                heading=h.chunk.heading,
                score=round(h.score, 4),
            )
        )
    return "\n\n".join(context_lines), sources


def rag_answer(
    query: str,
    collection: str | None = "default",
    top_k: int | None = None,
    chat_id: str | None = None,
    settings: Settings | None = None,
    llm_client: LLMClient | None = None,
    collections: list[str] | None = None,
) -> RagAnswer:
    """RAG 问答主入口（非流式，一次性返回完整回答）。

    Args:
        query: 用户问题
        collection: 集合名，None 表示跨全部集合
        top_k: 引用 chunk 数，省略用 settings.rag_top_k
        chat_id: 会话 ID（多轮对话），None 创建新会话
        settings: 运行时配置，省略用全局单例
        llm_client: 注入的 LLM 客户端（测试用），省略按配置创建
        collections: 多选集合名列表（WPF 多选知识库用），优先于 collection

    Returns:
        RagAnswer（答案 + 来源 + 会话 ID）

    Raises:
        RagError: LLM 未配置 / 检索失败 / LLM 调用失败
    """
    s = settings or get_settings()
    t0 = time.perf_counter()

    # 1. 解析会话
    cid, history = _load_history(chat_id)

    # 2. LLM 客户端（配置错误如缺 API Key / 未知 provider 转成 RagError 给出明确提示）
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
    )

    # 4.5 无命中时提前返回（不调 LLM）
    if not context:
        answer = "知识库中未找到与问题相关的内容，我无法回答。请先摄入相关文档再提问。"
        _save_history(cid, history + [{"role": "user", "content": query}])
        return RagAnswer(
            answer=answer,
            sources=[],
            chat_id=cid,
            elapsed_ms=int((time.perf_counter() - t0) * 1000),
            total_chunks=0,
            model=client.model_name,
            provider=client.provider,
        )

    # 6. 调 LLM（从 settings 读取超时配置）
    llm_timeout = (s.llm_timeout if s.llm_timeout > 0 else None)
    try:
        reply = client.chat(messages, timeout=llm_timeout)
    except LLMError as e:
        raise RagError(str(e)) from e

    # 7. 保存历史（用户问题 + 回答）
    _save_history(cid, history + [{"role": "user", "content": query}, {"role": "assistant", "content": reply}])

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
) -> Iterator[str]:
    """RAG 流式问答，逐 token 产出 SSE 格式 JSON 行。

    终帧（done=True）包含完整元数据（chat_id, model, sources 等）。
    调用方负责逐 token 消费。

    Args:
        同 rag_answer

    Yields:
        JSON 行：{"token": "..."} 或 {"token": null, "done": true, ...}
    """
    s = settings or get_settings()
    t0 = time.perf_counter()

    # 1. 解析会话
    cid, history = _load_history(chat_id)

    # 2. LLM 客户端（配置错误如缺 API Key / 未知 provider 转成 RagError 给出明确提示）
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
    )

    # 如果没有上下文，直接返回空答案
    if not context:
        _save_history(cid, history + [{"role": "user", "content": query}])
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
    _save_history(cid, history + [{"role": "user", "content": query}, {"role": "assistant", "content": reply}])

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
            {"index": src.index, "source": src.source, "format": src.format,
             "page": src.page, "heading": src.heading, "score": src.score}
            for src in sources
        ],
    }, ensure_ascii=False)


def _build_context_and_messages(
    query: str,
    collection: str | None,
    top_k: int | None,
    s: Settings,
    collections: list[str] | None,
    history: list[dict[str, str]],
    t0: float,
) -> tuple[list[SearchHit], str, list[SourceRef], list[dict[str, str]]]:
    """检索 + 构建上下文 + 组装消息（rag_answer 与 rag_answer_stream 共享）。"""
    # 3. 检索（集合解析：多选列表优先，其次单集合）
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
    except Exception as e:
        raise RagError(f"检索失败: {e}") from e
    finally:
        store.close()

    # 3.5 RAG 噪声过滤
    if s.rag_min_score > 0:
        hits = [h for h in hits if max(h.vector_score, h.bm25_score) >= s.rag_min_score]

    # 4. 构建上下文
    context, sources = _build_context(hits)
    if not context:
        return hits, "", sources, []

    # 5. 组装消息
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": f"以下是知识库中检索到的相关资料：\n\n{context}\n\n---\n请基于以上资料回答：{query}"},
    ]
    messages = messages[:1] + history[-_MAX_HISTORY:] + messages[1:]

    return hits, context, sources, messages


def _open_store(settings: Settings) -> tuple[VectorStore, Any]:
    """打开向量存储 + 嵌入引擎。"""
    embedder = get_embedder(settings)
    store = VectorStore(settings.db_path, embedder.dimension)
    store.open()
    return store, embedder
