"""AI 知识库整理引擎（curate）— 让知识库自组织。

四个动作（可任意组合）：
    enrich       给文档生成标题/摘要/标签，写回 documents 元数据（全自动）
    categorize   基于现有集合列表判断归属，必要时新建集合并移动文档（全自动）
    dedup        向量近邻找语义重复对，LLM 判定后删除冗余篇（dry-run 先行）
    consolidate  把小而散的经验笔记聚类，LLM 归纳成「蒸馏笔记」替换原条目（dry-run 先行）

设计约定：
- 所有 LLM 交互要求「只输出 JSON」，解析容错（剥代码栅栏 + 一次重试），
  失败降级为 skipped 项写入报告，绝不中断整批整理。
- dry_run=True 时全程只读：enrich/categorize 只生成计划，
  dedup/consolidate 只输出「将删除/将合并」清单，数据库零变化。
- LLM 未配置（get_llm_client 返回 None）时调用方应短路；本模块内所有
  接收 llm 的函数对 None 也做防御，返回 skipped 而不是抛异常。
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from doc2mind.core.config import Settings
from doc2mind.core.store.sqlite_vec import StoredDocument, StoreError, VectorStore

logger = logging.getLogger("doc2mind.curate")

# 空集合占位行的 source（ensure_collection 写入），整理时跳过
PLACEHOLDER_SOURCE = "__collection_placeholder__"

# 支持的整理动作
VALID_ACTIONS = ("enrich", "categorize", "dedup", "consolidate", "extract")

# enrich/categorize/extract 单次整理默认处理的文档上限（LLM 调用成本护栏）
DEFAULT_TOP_K = 200


class CuratorError(Exception):
    """知识库整理异常。"""


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# --- 报告 ---
@dataclass
class CurateReport:
    """一次整理的结构化报告（HTTP / MCP 序列化用）。"""

    dry_run: bool
    actions: list[str]
    collection: str | None = None
    enriched: list[dict[str, Any]] = field(default_factory=list)
    categorized: list[dict[str, Any]] = field(default_factory=list)
    duplicates: list[dict[str, Any]] = field(default_factory=list)
    consolidated: list[dict[str, Any]] = field(default_factory=list)
    extracted: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "actions": self.actions,
            "collection": self.collection,
            "enriched": self.enriched,
            "categorized": self.categorized,
            "duplicates": self.duplicates,
            "consolidated": self.consolidated,
            "extracted": self.extracted,
            "skipped": self.skipped,
            "errors": self.errors,
            "elapsed_ms": self.elapsed_ms,
        }


# --- LLM JSON 辅助 ---
def _extract_json(text: str) -> dict[str, Any] | None:
    """从 LLM 回复中提取 JSON 对象（超强容错：支持 ast.literal_eval、单引号转换、中文全角引号、尾随逗号、think 标签剥离等）。"""
    if not text:
        return None
    cleaned = text.strip()

    # 1. 过滤 <think>...</think> 推理过程（DeepSeek-R1 / Qwen / SenseNova 推理模型容错）
    cleaned = re.sub(r"<think>[\s\S]*?</think>", "", cleaned, flags=re.IGNORECASE).strip()

    # 2. 剥离 markdown 代码块 ```json ... ```
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", cleaned, re.IGNORECASE)
    if m:
        cleaned = m.group(1).strip()
    else:
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE).strip()
        cleaned = re.sub(r"\s*```$", "", cleaned).strip()

    # 3. 定位最外层 JSON 结构
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        return None
    json_candidate = cleaned[start : end + 1]

    # 4. 尝试标准反序列化
    try:
        parsed = json.loads(json_candidate)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, TypeError):
        pass

    # 5. 宽松容错 1：去除尾随逗号 (",\s*}" -> "}" / ",\s*]" -> "]")
    try:
        sanitized = re.sub(r",\s*([\]}])", r"\1", json_candidate)
        parsed = json.loads(sanitized)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, TypeError):
        pass

    # 6. 宽松容错 2：修复中文全角标点与未转义控制符
    try:
        sanitized = (
            json_candidate.replace("“", '"')
            .replace("”", '"')
            .replace("‘", "'")
            .replace("’", "'")
            .replace("：", ":")
            .replace("，", ",")
        )
        sanitized = re.sub(r",\s*([\]}])", r"\1", sanitized)
        parsed = json.loads(sanitized)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, TypeError):
        pass

    # 7. 宽松容错 3：尝试 ast.literal_eval（通吃 Python 风格单引号 dict）
    try:
        import ast

        parsed = ast.literal_eval(json_candidate)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    # 8. 宽松容错 4：单引号转双引号替换
    try:
        sanitized = re.sub(r"'(.*?)'\s*:", r'"\1":', json_candidate)
        sanitized = re.sub(r":\s*'(.*?)'", r': "\1"', sanitized)
        sanitized = re.sub(r",\s*([\]}])", r"\1", sanitized)
        parsed = json.loads(sanitized)
        if isinstance(parsed, dict):
            return parsed
    except (ValueError, TypeError):
        pass

    return None


def _llm_json(
    client: Any,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1024,
) -> dict[str, Any] | None:
    """调 LLM 并解析 JSON 输出；失败重试一次（附加更严格的指令），仍失败返回 None。"""
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    for attempt in range(2):
        try:
            reply = client.chat(messages, temperature=0.2, max_tokens=max_tokens)
        except Exception as e:  # noqa: BLE001 — LLM 失败降级为 skipped，不中断整理
            logger.warning("curate LLM 调用失败: %s", e)
            return None
        parsed = _extract_json(reply)
        if parsed is not None:
            return parsed
        logger.warning(
            "curate LLM 输出无法解析为 JSON（第 %d 次），原始输出片段: %s",
            attempt + 1,
            repr(reply)[:300],
        )
        messages = messages + [
            {"role": "assistant", "content": reply},
            {
                "role": "user",
                "content": "上面的输出无法解析为标准 JSON。请务必只输出标准 JSON 格式，严格使用双引号，不要任何解释说明。",
            },
        ]
    return None


def _normalize_tags(raw: Any, limit: int = 5) -> list[str]:
    """标签清洗：转字符串列表、去空去重、截断长度与数量。"""
    if isinstance(raw, str):
        raw = re.split(r"[,，;；\s]+", raw)
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        tag = str(item).strip()[:30]
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
        if len(out) >= limit:
            break
    return out


def _slugify(name: str) -> str:
    """集合名规范化：小写、空格转连字符，仅保留字母/数字/连字符/中文。"""
    s = (name or "").strip().lower().replace(" ", "-").replace("_", "-")
    s = re.sub(r"[^a-z0-9\-\u4e00-\u9fff]", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s[:32]


def _doc_full_text(store: VectorStore, doc: StoredDocument, max_chars: int) -> str:
    """拼接文档全部分块作为 LLM 输入（截断到 max_chars）。"""
    chunks = store.list_chunks_by_document(doc.id, limit=100)
    return "\n\n".join(c.content for c in chunks)[:max_chars]


def _doc_representative_text(
    store: VectorStore, doc: StoredDocument, max_chars: int = 1500
) -> str:
    """文档代表文本：优先 AI 摘要，其次前几个分块（去重/归类/判重的轻量输入）。"""
    if doc.summary:
        return doc.summary[:max_chars]
    chunks = store.list_chunks_by_document(doc.id, limit=3)
    return "\n\n".join(c.content for c in chunks)[:max_chars]


def _is_placeholder(doc: StoredDocument) -> bool:
    return doc.source == PLACEHOLDER_SOURCE or doc.format == "placeholder"


def _doc_brief(doc: StoredDocument) -> dict[str, Any]:
    """报告里描述一个文档的精简信息。"""
    return {
        "doc_id": doc.id,
        "source": doc.source,
        "collection": doc.collection,
        "title": doc.title,
    }


# --- 动作 1：enrich（打标签 + 摘要 + 标题）---
_ENRICH_SYSTEM = (
    "你是知识库整理助手。阅读给定的知识库文档内容，提取元数据。"
    '只输出 JSON 对象：{"title": "...", "summary": "...", "tags": ["...", "..."]}\n'
    "要求：title 用一句话概括主题（≤30 字）；summary 保留关键事实、根因与解法"
    "（2-5 句）；tags 给 3-5 个主题标签，中文优先，专有名词可用英文。"
    "不要输出 JSON 以外的任何文字。"
)


def enrich_document(
    store: VectorStore,
    llm: Any,
    doc: StoredDocument,
    max_chars: int = 8000,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """给单篇文档生成 title/summary/tags 并写回（dry_run 时只生成计划）。"""
    base = _doc_brief(doc)
    if llm is None:
        return {**base, "status": "skipped", "reason": "未配置 LLM"}
    if _is_placeholder(doc):
        return {**base, "status": "skipped", "reason": "集合占位记录"}
    if doc.enriched_at and not force:
        return {**base, "status": "skipped", "reason": "已有整理元数据（force=False）"}

    body = _doc_full_text(store, doc, max_chars)
    if not body.strip():
        return {**base, "status": "skipped", "reason": "文档无有效内容"}

    parsed = _llm_json(llm, _ENRICH_SYSTEM, f"文档内容（可能截断）：\n{body}")
    if parsed is None:
        return {**base, "status": "skipped", "reason": "LLM 输出无法解析"}

    title = str(parsed.get("title") or "").strip()[:80] or None
    summary = str(parsed.get("summary") or "").strip() or None
    tags = _normalize_tags(parsed.get("tags"))

    item = {**base, "title": title, "summary": summary, "tags": tags}
    if dry_run:
        return {**item, "status": "planned"}

    try:
        store.update_document_meta(
            doc.id, title=title, tags=tags, summary=summary, enriched_at=_now_iso()
        )
    except StoreError as e:
        return {**item, "status": "error", "reason": str(e)}
    return {**item, "status": "enriched"}


# --- 动作 2：categorize（自动归类，必要时新建集合）---
_CATEGORIZE_SYSTEM = (
    "你是知识库归类助手。根据文档内容判断它应归属哪个知识库集合。\n"
    '只输出 JSON 对象：{"collection": "...", "is_new": false, "reason": "..."}\n'
    "规则：优先放入语义匹配的现有集合（collection 必须与列表中的名称完全一致，"
    '此时 is_new=false）；都不合适才新建（is_new=true，collection 给一个简短集合名：'
    "小写字母/数字/连字符，可用中文，≤24 字符）。reason 一句话。"
    "不要输出 JSON 以外的任何文字。"
)


def categorize_document(
    store: VectorStore,
    llm: Any,
    doc: StoredDocument,
    dry_run: bool = False,
) -> dict[str, Any]:
    """判断文档归属集合并移动（dry_run 时只生成计划）。

    显式指定集合入库的文档不会走到这里（pipeline 只在未指定集合时自动归类）。
    """
    base = _doc_brief(doc)
    if llm is None:
        return {**base, "status": "skipped", "reason": "未配置 LLM"}
    if _is_placeholder(doc):
        return {**base, "status": "skipped", "reason": "集合占位记录"}

    try:
        existing = sorted(store.get_stats().collections.keys())
    except StoreError as existing_err:  # pragma: no cover — 统计失败罕见
        return {**base, "status": "error", "reason": str(existing_err)}

    body = _doc_representative_text(store, doc, max_chars=2000)
    if not body.strip():
        return {**base, "status": "skipped", "reason": "文档无有效内容"}

    listing = "\n".join(f"- {name}" for name in existing) or "（暂无集合）"
    user = (
        f"现有集合列表：\n{listing}\n\n"
        f"文档标题：{doc.title or doc.source}\n"
        f"文档内容：\n{body}"
    )
    parsed = _llm_json(llm, _CATEGORIZE_SYSTEM, user)
    if parsed is None:
        return {**base, "status": "skipped", "reason": "LLM 输出无法解析"}

    name = str(parsed.get("collection") or "").strip()
    is_new = name not in existing
    if is_new:
        name = _slugify(name)
    if not name:
        return {**base, "status": "skipped", "reason": "集合名规范化后为空"}
    if name == doc.collection:
        return {**base, "status": "unchanged", "to": name}

    item = {**base, "from": doc.collection, "to": name, "new_collection": is_new,
            "reason": str(parsed.get("reason") or "").strip()[:200] or None}
    if dry_run:
        return {**item, "status": "planned"}

    try:
        if is_new:
            store.ensure_collection(name)
        moved = store.move_document(doc.id, name)
    except StoreError as e:
        return {**item, "status": "error", "reason": str(e)}
    return {**item, "status": "moved" if moved else "unchanged"}


# --- 动作 3：dedup（语义去重）---
_DEDUP_SYSTEM = (
    "你是知识库去重助手。判断两段知识是否为语义重复"
    "（同一问题/同一结论的重复记录，而非仅主题相近）。\n"
    '只输出 JSON 对象：{"is_duplicate": false, "keep": "A", "reason": "..."}\n'
    "若重复，keep 指出保留信息更完整的一篇（A 或 B）。"
    "不要输出 JSON 以外的任何文字。"
)


def _judge_duplicate(
    store: VectorStore,
    llm: Any,
    doc_a: StoredDocument,
    doc_b: StoredDocument,
    score: float,
    dry_run: bool,
) -> dict[str, Any]:
    """对一对候选文档做 LLM 判重；执行模式下删除冗余篇。"""
    item: dict[str, Any] = {
        "score": round(score, 4),
        "keep": _doc_brief(doc_a),
        "remove": _doc_brief(doc_b),
    }
    if llm is None:
        return {**item, "status": "skipped", "reason": "未配置 LLM"}

    ta = _doc_representative_text(store, doc_a, 1500)
    tb = _doc_representative_text(store, doc_b, 1500)
    user = (
        f"【A】（{doc_a.source}，{doc_a.chunk_count} 个分块）\n{ta}\n\n"
        f"【B】（{doc_b.source}，{doc_b.chunk_count} 个分块）\n{tb}"
    )
    parsed = _llm_json(llm, _DEDUP_SYSTEM, user)
    if parsed is None:
        return {**item, "status": "skipped", "reason": "LLM 输出无法解析"}
    if not parsed.get("is_duplicate"):
        return {**item, "status": "not_duplicate"}

    # 保留方：LLM 明确选择；未选时按分块数多者（信息更全的启发式）
    keep_choice = str(parsed.get("keep") or "").strip().upper()
    if keep_choice == "B" or keep_choice not in ("A", "B") and doc_b.chunk_count > doc_a.chunk_count:
        doc_a, doc_b = doc_b, doc_a
    item["keep"], item["remove"] = _doc_brief(doc_a), _doc_brief(doc_b)
    item["reason"] = str(parsed.get("reason") or "").strip()[:200] or None

    if dry_run:
        return {**item, "status": "planned"}
    try:
        deleted = store.delete_document(doc_b.id)
    except StoreError as e:
        return {**item, "status": "error", "reason": str(e)}
    logger.info(
        "dedup 删除冗余文档: %s（保留 %s，相似分 %.3f，删除 %d 个分块）",
        doc_b.source, doc_a.source, score, max(deleted, 0),
    )
    return {**item, "status": "merged"}


def find_duplicates(
    store: VectorStore,
    embedder: Any,
    llm: Any,
    collection: str,
    threshold: float = 0.85,
    max_pairs: int = 20,
    dry_run: bool = True,
) -> list[dict[str, Any]]:
    """在同一集合内找语义重复对（向量近邻候选 + LLM 判定）。

    去重限同一集合内，避免跨集合误判（不同项目刻意收录的相同资料）。
    """
    docs = [
        d for d in store.list_documents(collection=collection, limit=10000)
        if not _is_placeholder(d) and d.chunk_count > 0
    ]
    by_source = {d.source: d for d in docs}
    seen: set[frozenset[str]] = set()
    results: list[dict[str, Any]] = []

    for doc in docs:
        if len(results) >= max_pairs:
            break
        text = _doc_representative_text(store, doc, max_chars=2000)
        if not text.strip():
            continue
        try:
            query_vec = embedder.embed_query(text)
        except Exception as e:  # noqa: BLE001 — 嵌入失败跳过该文档
            logger.warning("dedup 嵌入失败（跳过 %s）: %s", doc.source, e)
            continue
        try:
            hits = store.vector_search(query_vec, top_k=5, collection=collection)
        except StoreError as e:  # pragma: no cover — 检索失败跳过该文档
            logger.warning("dedup 向量检索失败（跳过 %s）: %s", doc.source, e)
            continue
        for chunk_id, dist in hits:
            score = 1.0 / (1.0 + dist)
            if score < threshold:
                continue
            chunks = store.get_chunks([chunk_id])
            if not chunks:
                continue
            other = by_source.get(chunks[0].source)
            if other is None or other.id == doc.id:
                continue
            pair = frozenset((doc.id, other.id))
            if pair in seen:
                continue
            seen.add(pair)
            results.append(_judge_duplicate(store, llm, doc, other, score, dry_run))
            if len(results) >= max_pairs:
                break
    return results


# --- 动作 4：consolidate（归纳合并蒸馏笔记）---
_CONSOLIDATE_SYSTEM = (
    "你是知识库归纳助手。把多条相关经验笔记归纳成一篇结构化的「蒸馏笔记」。\n"
    '只输出 JSON 对象：{"title": "...", "content": "..."}\n'
    "要求：content 为 Markdown 正文；合并重复要点，保留所有不同的根因、解法与"
    "结论（不得丢失信息）；按主题分节（## 标题）；title 一句话概括（≤30 字）。"
    "不要输出 JSON 以外的任何文字。"
)


def _cosine_sim(a: Any, b: Any) -> float:
    """纯 Python 余弦相似度（避免 curator 模块依赖 numpy）。"""
    va, vb = list(a), list(b)
    if len(va) != len(vb) or not va:
        return 0.0
    dot = sum(x * y for x, y in zip(va, vb, strict=False))
    na = sum(x * x for x in va) ** 0.5
    nb = sum(x * x for x in vb) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return float(dot / (na * nb))


def consolidate_notes(
    store: VectorStore,
    embedder: Any,
    llm: Any,
    settings: Settings,
    collection: str,
    dry_run: bool = True,
    min_cluster: int = 3,
    max_clusters: int = 10,
    sim_threshold: float = 0.78,
) -> list[dict[str, Any]]:
    """把集合内小而散的经验笔记（note: 开头、ingest_text 沉淀）聚类归纳。

    每个簇（≥min_cluster 条）由 LLM 归纳成一篇蒸馏笔记：dry_run 只输出预览；
    执行模式先入库新笔记，成功后再删除原条目（删除失败不影响已入库的新笔记）。
    """
    notes = [
        d for d in store.list_documents(collection=collection, limit=10000)
        if d.source.startswith("note:") and d.chunk_count > 0
    ]
    if len(notes) < min_cluster:
        return []

    # 计算每篇笔记的代表向量（用于聚类）
    vectors: dict[str, Any] = {}
    texts: dict[str, str] = {}
    for d in notes:
        text = _doc_representative_text(store, d, max_chars=1500)
        if not text.strip():
            continue
        try:
            vectors[d.id] = embedder.embed_query(text)
            texts[d.id] = text
        except Exception as e:  # noqa: BLE001 — 嵌入失败跳过该笔记
            logger.warning("consolidate 嵌入失败（跳过 %s）: %s", d.source, e)

    # 贪心单链接聚类：以每篇为种子吸收相似笔记
    remaining = [d for d in notes if d.id in vectors]
    clusters: list[list[StoredDocument]] = []
    while remaining:
        seed = remaining.pop(0)
        cluster = [seed]
        rest: list[StoredDocument] = []
        for other in remaining:
            if _cosine_sim(vectors[seed.id], vectors[other.id]) >= sim_threshold:
                cluster.append(other)
            else:
                rest.append(other)
        remaining = rest
        if len(cluster) >= min_cluster:
            clusters.append(cluster)

    results: list[dict[str, Any]] = []
    for cluster in clusters[:max_clusters]:
        members = sorted(cluster, key=lambda d: d.source)
        user = "以下是相关经验笔记：\n" + "\n\n".join(
            f"【{i}】（{d.source}）\n{texts.get(d.id, '')}"
            for i, d in enumerate(members, start=1)
        )
        parsed = _llm_json(llm, _CONSOLIDATE_SYSTEM, user, max_tokens=2048)
        if parsed is None:
            results.append({
                "status": "skipped",
                "cluster_size": len(members),
                "members": [d.source for d in members],
                "reason": "LLM 输出无法解析",
            })
            continue

        title = str(parsed.get("title") or "").strip()[:80] or "蒸馏笔记"
        content = str(parsed.get("content") or "").strip()
        item: dict[str, Any] = {
            "cluster_size": len(members),
            "members": [d.source for d in members],
            "title": title,
            "preview": content[:300],
        }
        if not content:
            results.append({**item, "status": "skipped", "reason": "归纳内容为空"})
            continue
        if dry_run:
            results.append({**item, "status": "planned"})
            continue

        # 执行：先入库蒸馏笔记（复用 ingest_text 的分块/嵌入/去重逻辑），
        # 再删除原条目。ingest_text 失败/跳过（如内容 MD5 重复）时不删原条目。
        # 蒸馏笔记本身不需要再触发入库自动整理（标题/内容已由 LLM 生成），
        # 用关闭 auto_curate 的配置副本调用。
        from dataclasses import replace as dc_replace

        from doc2mind.core.pipeline import ingest_text

        res = ingest_text(
            text=content, title=title, collection=collection,
            force=False,
            settings=dc_replace(settings, auto_curate_on_ingest=False),
            store=store,
        )
        if res.status != "ingested":
            results.append({
                **item, "status": "failed",
                "reason": f"蒸馏笔记入库失败: {res.error or res.status}",
            })
            continue
        item["new_document_id"] = res.document_id
        # 蒸馏笔记自带元数据（省去后续 enrich 的 LLM 调用）
        try:
            store.update_document_meta(
                res.document_id, title=title, tags=["distilled"],
                summary=content[:200], enriched_at=_now_iso(),
            )
        except StoreError as e:  # noqa: BLE001 — 元数据失败不影响合并结果
            logger.warning("consolidate 写蒸馏笔记元数据失败: %s", e)
        for d in members:
            try:
                store.delete_document(d.id)
            except StoreError as e:  # noqa: BLE001 — 单条删除失败继续
                logger.warning("consolidate 删除原笔记失败（%s）: %s", d.source, e)
        logger.info(
            "consolidate 合并 %d 条笔记为「%s」（collection=%s）",
            len(members), title, collection,
        )
        results.append({**item, "status": "consolidated"})
    return results


# --- 汇总入口 ---
def curate(
    store: VectorStore,
    embedder: Any,
    llm: Any,
    settings: Settings,
    collection: str | None = None,
    actions: list[str] | None = None,
    dry_run: bool = True,
    top_k: int | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> CurateReport:
    """知识库整理汇总入口。

    Args:
        store: 已打开的向量存储。
        embedder: 嵌入器（dedup / consolidate 用；可为 None，届时这两项跳过）。
        llm: LLM 客户端；None 时所有动作记 skipped 并返回报告。
        settings: 运行时配置。
        collection: 目标集合；None = 全部集合。
        actions: 动作列表，默认全部四项。
        dry_run: True = 只读预览（零写入）；False = 执行（删除/合并类生效）。
        top_k: enrich/categorize 处理的文档上限（默认 200，护栏防 LLM 失控）。
        progress: 进度回调 (done, total)。

    Returns:
        `CurateReport`
    """
    t0 = time.perf_counter()
    requested = list(actions or list(VALID_ACTIONS))
    invalid = [a for a in requested if a not in VALID_ACTIONS]
    report = CurateReport(dry_run=dry_run, actions=[a for a in requested if a in VALID_ACTIONS],
                          collection=collection)

    if invalid:
        report.errors.append(
            f"未知动作: {invalid}（可选: {list(VALID_ACTIONS)}）"
        )
    if not report.actions:
        report.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return report

    if llm is None:
        report.skipped.append(
            "未配置 LLM（llm_provider=none），整理动作需要大模型；"
            "请先在设置页配置或在环境变量设置 DOC2MIND_LLM_PROVIDER"
        )
        report.elapsed_ms = int((time.perf_counter() - t0) * 1000)
        return report

    collections = (
        [collection] if collection else sorted(store.get_stats().collections.keys())
    )

    # 阶段 1：enrich + categorize + extract（逐文档）
    need_doc_actions = any(a in report.actions for a in ("enrich", "categorize", "extract"))
    limit = top_k or DEFAULT_TOP_K
    if need_doc_actions:
        docs = [
            d for c in collections
            for d in store.list_documents(collection=c, limit=limit)
            if not _is_placeholder(d)
        ][:limit]
        total = len(docs)

        # 增量优化：获取已提取过图谱实体的文档列表，避免重复消耗 LLM
        extracted_doc_ids: set[str] = set()
        if "extract" in report.actions:
            from doc2mind.core.store.graph_store import GraphStore

            try:
                g_store = GraphStore(settings.db_path)
                extracted_doc_ids = g_store.get_extracted_doc_ids(collection)
                g_store.close()
            except Exception:
                pass

        for i, doc in enumerate(docs, start=1):
            if "enrich" in report.actions:
                report.enriched.append(
                    enrich_document(store, llm, doc,
                                    max_chars=settings.curate_max_chars,
                                    dry_run=dry_run)
                )
            if "categorize" in report.actions:
                report.categorized.append(
                    categorize_document(store, llm, doc, dry_run=dry_run)
                )
            if "extract" in report.actions:
                from doc2mind.core.extractor import extract_and_store, extract_entities

                if doc.id in extracted_doc_ids and not dry_run:
                    report.skipped.append(f"文档「{doc.title or doc.source}」已有图谱实体，增量跳过")
                else:
                    # 优先使用代表文本/摘要（精炼 1800 字符），极大提升 LLM 响应速度并减少 token 开销
                    doc_text = _doc_representative_text(store, doc, max_chars=1800)
                    if len(doc_text) < 100:
                        doc_text = _doc_full_text(store, doc, max_chars=2500)

                    if not doc_text or not doc_text.strip():
                        report.skipped.append(f"文档「{doc.title or doc.source}」无有效正文内容，跳过实体抽取")
                    elif dry_run:
                        extracted_res = extract_entities(doc_text, llm, max_chars=2500)
                        report.extracted.append({
                            "doc_id": doc.id,
                            "title": doc.title,
                            "collection": doc.collection,
                            "entities": extracted_res.get("entities", []),
                            "relations": extracted_res.get("relations", []),
                            "dry_run": True,
                        })
                    else:
                        first_chunk = store.list_chunks_by_document(doc.id, limit=1)
                        first_chunk_id = first_chunk[0].id if first_chunk else None
                        store_res = extract_and_store(
                            doc_text, doc.collection, llm,
                            doc_id=doc.id, db_path=settings.db_path,
                            chunk_id=first_chunk_id,
                        )
                        report.extracted.append({
                            "doc_id": doc.id,
                            "title": doc.title,
                            "collection": doc.collection,
                            **store_res,
                            "dry_run": False,
                        })
            if progress is not None:
                progress(i, total)

    # 阶段 2：dedup + consolidate（逐集合）
    for c in collections:
        if "dedup" in report.actions:
            try:
                report.duplicates.extend(
                    find_duplicates(
                        store, embedder, llm, c,
                        threshold=settings.curate_dedup_score_threshold,
                        dry_run=dry_run,
                    )
                )
            except Exception as e:  # noqa: BLE001 — 单集合失败不中断其它集合
                report.errors.append(f"dedup 失败（collection={c}）: {e}")
        if "consolidate" in report.actions:
            try:
                report.consolidated.extend(
                    consolidate_notes(store, embedder, llm, settings, c, dry_run=dry_run)
                )
            except Exception as e:  # noqa: BLE001
                report.errors.append(f"consolidate 失败（collection={c}）: {e}")

    report.elapsed_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        "curate 完成: dry_run=%s actions=%s collection=%s enriched=%d categorized=%d "
        "duplicates=%d consolidated=%d extracted=%d skipped=%d errors=%d elapsed=%dms",
        dry_run, report.actions, collection, len(report.enriched),
        len(report.categorized), len(report.duplicates),
        len(report.consolidated), len(report.extracted),
        len(report.skipped), len(report.errors),
        report.elapsed_ms,
    )
    return report
