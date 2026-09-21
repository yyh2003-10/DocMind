"""科研写作子链路：文献集合判定、科研上下文组装（design 决策 C-3 / C-4）。

对应设计文档 .codeartsdoer/specs/ai_capability/design.md：
- 决策 C-3：科研写作检索子链路（resolve_literature_collections / build_research_context）
- 决策 C-4：图谱 topic 只读查询（graph_store.get_topic_context，见 graph_store.py）
- 决策 D-1：科研引用支撑验证（verify_citation_support，M3-T12 追加）

硬约束（沿用 M1/M2 已确立的约束）：
- 纯增量：不改检索内核 / 图谱引擎 / LLM 客户端；只读复用 Retriever.search 与 GraphStore
- 不新增工具 id（工具链由 planner.build_research_plan 复用既有 5 工具）
- 降级可见：检索/图谱异常不抛出到主链路，通过 on_status 回调显式提示
"""

from __future__ import annotations

import dataclasses
import logging
import re
from pathlib import Path
from typing import Any, Callable, Iterable

from doc2mind.core.config import Settings

logger = logging.getLogger("doc2mind.agent.research")


# 科研子任务中文名（planner 展示帧 / 状态帧共用，避免关键词漂移）
RESEARCH_TASK_LABELS: dict[str, str] = {
    "review": "文献综述",
    "compare": "观点对比",
    "draft": "写作草稿",
}

# 文献块类别头：_truncate_context_to_budget 据此把文献块排在最后裁剪
LITERATURE_BLOCK_HEAD = "【文献原著切片 (Literature)】"

# 科研写作提示约束段（design 决策 D-2）：仅 research 场景注入，不改全局 _SYSTEM_PROMPT。
# 块头含「科研写作准则」，供 _truncate_context_to_budget 识别为文献类（最后裁剪）。
RESEARCH_CONSTRAINTS = (
    "【科研写作准则】\n"
    "1. 综述/观点对比/写作草稿的主论点必须来自【文献原著切片】集合，并逐条标注 [n]；\n"
    "2. 若观点来自图谱关联或通用知识，不得标注文献编号，"
    "需以「图谱关联表明……」「一般观点认为……」显式区分；\n"
    "3. 文献集合未直接支持的观点，需在开头显式标注"
    "「文献集合未直接支持，以下为通用知识延伸」，不得冒充文献结论。"
)

# topic 注入上限（design 决策 C-3 步骤 3：去重 ≤ 30 条）
_TOPIC_EDGE_LIMIT = 30
_TOPIC_SOURCE_LIMIT = 30


class LiteratureScopeError(Exception):
    """文献集合判定 / 检索失败：rag 侧据此降级到通用知识并显式标注（不静默）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# --------------------------------------------------------------------------
# 工具函数
# --------------------------------------------------------------------------


def _norm_collections(collections: str | list[str] | None) -> list[str]:
    """集合名归一化：兼容字符串/列表，去空白、保序去重。"""
    if collections is None:
        return []
    if isinstance(collections, str):
        collections = [collections]
    out: list[str] = []
    for item in collections:
        name = str(item).strip()
        if name and name not in out:
            out.append(name)
    return out


def _sources_from_hits(hits: Iterable[Any] | None) -> list[str]:
    """从 SearchHit 列表提取来源路径（duck typing，不强依赖 retriever 模块）。"""
    out: list[str] = []
    for hit in hits or []:
        chunk = getattr(hit, "chunk", hit)
        source = getattr(chunk, "source", None)
        if source and str(source) not in out:
            out.append(str(source))
    return out


def _hit_headings(hits: Iterable[Any] | None) -> list[str]:
    """提取命中切片的章节标题，作为图谱 topic 扩散 seed（design C-3 步骤 3）。"""
    out: list[str] = []
    for hit in hits or []:
        heading = getattr(getattr(hit, "chunk", None), "heading", None)
        if heading and str(heading).strip() and str(heading) not in out:
            out.append(str(heading).strip())
    return out


def _build_source_collection_map(store: Any | None, limit: int = 20000) -> dict[str, str]:
    """建立 {来源路径 / 文件名: 集合名} 映射，供四步回退第 3 步推导文献集合。

    仅使用 store 的公共只读接口；库不可用或表结构异常时返回空映射（不阻断主链路）。
    """
    mapping: dict[str, str] = {}
    if store is None:
        return mapping
    try:
        try:
            docs = store.list_documents(limit=limit)
        except TypeError:
            # 旧版签名不支持 limit 参数
            docs = store.list_documents()
    except Exception as exc:  # noqa: BLE001 —— 读取失败仅跳过集合推导
        logger.debug("读取文档-集合映射失败，跳过文献集合推导: %s", exc)
        return mapping
    for doc in docs or []:
        source = getattr(doc, "source", None)
        coll = getattr(doc, "collection", None)
        if not source or not coll:
            continue
        src = str(source)
        mapping.setdefault(src, str(coll))
        mapping.setdefault(Path(src).name, str(coll))
    return mapping


def _extract_query_keywords(query: str) -> list[str]:
    """抽取 query 关键词作为图谱 seed（复用 rag 的 token 抽取，保持口径一致）。"""
    try:
        from doc2mind.core.rag import _extract_query_tokens  # 延迟导入，避免循环依赖

        return [t for t in _extract_query_tokens(query or "") if t][:8]
    except Exception as exc:  # noqa: BLE001
        logger.debug("科研关键词抽取失败，回退整句: %s", exc)
        text = (query or "").strip()
        return [text] if text else []


# --------------------------------------------------------------------------
# 1. 文献集合判定（design 决策 C-3 步骤 1）
# --------------------------------------------------------------------------


def resolve_literature_collections(
    req_collections: str | list[str] | None = None,
    chat_collections: str | list[str] | None = None,
    attachments: list[str] | None = None,
    first_round_hits: Iterable[Any] | None = None,
    store: Any | None = None,
    s: Settings | None = None,
) -> list[str] | None:
    """判定本轮科研写作的文献集合（四步回退，design 决策 C-3 步骤 1）。

    回退顺序：
    1. 请求显式携带 literature_collections → 采用；
    2. 本轮对话勾选的 collections（非空）→ 采用；
    3. 附件来源集合 + 首轮命中来源集合（去重，封顶 research_max_literature）；
    4. 判空 → 返回 None，由 rag 走通用知识兜底并标注「未能确定文献集合」（spec 5.5-3）。

    说明：附件为全文引入的文件路径，其集合归属需经 store 文档索引映射；
    无法映射时该来源不贡献集合（避免把文件路径误当作集合名）。
    """
    limit = max(1, int(getattr(s, "research_max_literature", 20) or 20))

    # 步骤 1：请求显式指定
    explicit = _norm_collections(req_collections)
    if explicit:
        return explicit[:limit]

    # 步骤 2：本轮勾选集合
    checked = _norm_collections(chat_collections)
    if checked:
        return checked[:limit]

    # 步骤 3：附件来源集合 + 首轮命中来源集合
    src_to_coll = _build_source_collection_map(store)
    candidates: list[str] = []
    for path in attachments or []:
        text = str(path).strip()
        if not text:
            continue
        coll = src_to_coll.get(text) or src_to_coll.get(Path(text).name)
        if coll:
            candidates.append(coll)
    for source in _sources_from_hits(first_round_hits):
        coll = src_to_coll.get(source) or src_to_coll.get(Path(source).name)
        if coll:
            candidates.append(coll)

    deduped: list[str] = []
    for coll in candidates:
        if coll not in deduped:
            deduped.append(coll)
    if deduped:
        return deduped[:limit]

    # 步骤 4：判空降级
    return None


# --------------------------------------------------------------------------
# 2. 科研上下文组装（design 决策 C-3 步骤 2-4）
# --------------------------------------------------------------------------


def _format_topic_block(topic_label: str, topic_ctx: dict[str, Any]) -> str:
    """把 get_topic_context 结果渲染为图谱 Topic 上下文块（去重 ≤ 30 条）。"""
    edges = [e for e in (topic_ctx.get("topic_edges") or []) if e.get("from") and e.get("to")]
    lines: list[str] = [f"【知识图谱 Topic 层：{topic_label or '未命名 topic'}】"]

    seen: set[tuple[str, str, str]] = set()
    items: list[str] = []
    for edge in edges:
        relation = str(edge.get("relation") or "关联")
        key = (str(edge["from"]), relation, str(edge["to"]))
        if key in seen or len(items) >= _TOPIC_EDGE_LIMIT:
            continue
        seen.add(key)
        items.append(f"{edge['from']} --[{relation}]--> {edge['to']}")
    if items:
        lines.append("概念/实体：" + "；".join(items))

    titles: list[str] = []
    for src in topic_ctx.get("related_sources") or []:
        title = str(src.get("title") or src.get("source") or "").strip()
        if title and title not in titles and len(titles) < _TOPIC_SOURCE_LIMIT:
            titles.append(title)
    if titles:
        lines.append("关联文献：" + "；".join(titles))

    return "\n".join(lines)


def build_research_context(
    *,
    query: str,
    retriever: Any,
    s: Settings,
    literature_collections: list[str] | None = None,
    research_task: str | None = None,
    graph_store: Any | None = None,
    store: Any | None = None,
    llm_client: Any | None = None,  # noqa: ARG001 —— 预留：后续交叉验证/摘要改写使用
    on_status: Callable[[str], None] | None = None,
    start_idx: int = 1,
) -> tuple[str, list[Any], dict[str, Any]]:
    """组装科研写作上下文（design 决策 C-3 步骤 2-4）。

    组装顺序固定：【文献原著切片(Literature)】 → 【知识图谱 Topic 层】
    （可选 web 交叉验证块由 rag 侧在本函数返回后追加，见 build_research_plan 的
    web_search 工具位）。

    Args:
        query: 用户查询文本（不动搜索词，集合限定在检索入参完成）
        retriever: 既有 Retriever 实例（复用同一检索实现，不新增检索内核）
        s: Settings（读取 research_max_literature / research_graph_topic_depth）
        literature_collections: 文献集合限定（由 resolve_literature_collections 得出）
        research_task: review|compare|draft（仅用于 meta 透传与展示）
        graph_store: GraphStore 实例（None 时跳过图谱注入）
        store: VectorStore 实例（供 _format_context 邻块上下文）
        on_status: 状态帧回调（yield 兼容包装）
        start_idx: 引用编号起点（与主链路 sources 续编）

    Returns:
        (context, sources, meta)
        - context: 拼接后的上下文文本
        - sources: SourceRef 列表（source_type="local"，literature=True）
        - meta: {literature_scope, literature_count, research_task, task_label,
                 topic_injected, topic_label, citation_support_ready,
                 literature_indices, degraded, degraded_reason}

    Raises:
        LiteratureScopeError: 文献集合判空或检索失败 → rag 侧降级到通用知识并显式标注
    """
    on_status = on_status or (lambda _msg: None)
    collections = _norm_collections(literature_collections)
    if not collections:
        raise LiteratureScopeError("未能确定文献集合")

    task = research_task or "review"
    top_k = max(1, int(getattr(s, "research_max_literature", 20) or 20))
    meta: dict[str, Any] = {
        "literature_scope": list(collections),
        "literature_count": 0,
        "research_task": task,
        "task_label": RESEARCH_TASK_LABELS.get(task, task),
        "topic_injected": False,
        "topic_label": "",
        "citation_support_ready": False,
        "literature_indices": set(),
        "degraded": False,
        "degraded_reason": None,
    }

    # ---- 步骤 2：文献限定检索（复用 Retriever.search，仅切 collection / top_k）----
    on_status(f"正在检索所选文献集合（{len(collections)} 个集合，最多 {top_k} 篇）...")
    try:
        hits, stats = retriever.search(
            query=query,
            collection=collections,
            top_k=top_k,
            min_score=0.0,
        )
    except Exception as exc:  # noqa: BLE001 —— 检索异常显式降级，不阻断对话
        raise LiteratureScopeError(f"文献集合检索失败：{exc}") from exc

    hits = list(hits or [])
    degraded_reason = getattr(stats, "degraded_reason", None) if stats is not None else None
    if degraded_reason:
        meta["degraded"] = True
        meta["degraded_reason"] = str(degraded_reason)
        on_status(f"⚠ 文献检索降级：{degraded_reason}")

    # 渲染为带 [n] 编号的可引用切片，并打 literature 标记（SourceRef 为 frozen dataclass）
    from doc2mind.core.rag import _format_context  # 延迟导入，避免与 rag 循环依赖

    literature_ctx, sources = _format_context(
        hits,
        start_idx=start_idx,
        store=store,
        neighbor_window=0,
        parent_mode="neighbor",
        as_citable=True,
    )
    sources = [dataclasses.replace(src, literature=True) for src in sources]
    meta["literature_count"] = len(sources)
    meta["literature_indices"] = {src.index for src in sources}

    blocks: list[str] = []
    if literature_ctx:
        # 约束段前置：LLM 先读科研准则再看文献原文，避免把通用观点冒充文献结论
        blocks.append(RESEARCH_CONSTRAINTS)
        blocks.append(f"{LITERATURE_BLOCK_HEAD}\n{literature_ctx}")
        on_status(f"✔ 文献检索：命中 {len(sources)} 个原著切片")
    else:
        # 集合存在但零命中：仍返回空上下文块，由 rag 侧追加「文献集合未命中」标注
        meta["citation_support_ready"] = False
        on_status("⚠ 文献检索：所选集合未命中任何切片")

    # ---- 步骤 3：图谱 Topic 层注入（只读扩散，空/异常不阻断）----
    topic_injected = False
    if graph_store is not None:
        try:
            seed_keywords = _extract_query_keywords(query)
            for heading in _hit_headings(hits)[:6]:
                if heading not in seed_keywords:
                    seed_keywords.append(heading)

            seed_ids: list[str] = []
            for keyword in seed_keywords[:6]:
                try:
                    for entity in graph_store.find_entities_by_keyword(keyword, limit=3):
                        entity_id = entity.get("id")
                        if entity_id and entity_id not in seed_ids:
                            seed_ids.append(str(entity_id))
                except Exception:  # noqa: BLE001 —— 单关键词查询失败继续下一个
                    continue

            topic_ctx = graph_store.get_topic_context(
                seed_keywords=seed_keywords,
                seed_entity_ids=seed_ids[:20],
                depth=int(getattr(s, "research_graph_topic_depth", 2) or 2),
                limit_entities=30,
                collections=collections,
            )
        except Exception as exc:  # noqa: BLE001 —— 沿用既有 try/except 跳过模式
            logger.debug("科研 topic 查询降级，跳过图谱注入: %s", exc)
            topic_ctx = None
            on_status("✔ 图谱：未发现相关 topic 关联")

        if topic_ctx and (topic_ctx.get("topic_edges") or topic_ctx.get("related_sources")):
            topic_label = str(topic_ctx.get("topic_label") or "")
            blocks.append(_format_topic_block(topic_label, topic_ctx))
            topic_injected = True
            meta["topic_label"] = topic_label
            meta["topic_injected"] = True
            on_status(
                f"✔ 图谱 Topic：「{topic_label or '未命名'}」"
                f"找到 {len(topic_ctx.get('topic_edges') or [])} 条关联"
            )
        else:
            on_status("✔ 图谱：未发现相关 topic 关联")

    meta["citation_support_ready"] = bool(sources)
    return "\n\n".join(blocks), sources, meta

# --------------------------------------------------------------------------
# 决策 D-1：科研引用支撑验证
# --------------------------------------------------------------------------

# 观点动词：命中任一即视为关键陈述（design 决策 D-1 步骤 2）
OPINION_VERBS: tuple[str, ...] = (
    "认为", "表明", "指出", "证实", "对比", "差异",
    "优于", "劣于", "支持", "反对", "结论", "因此",
)
# 关键陈述长句阈值：超过该长度默认视为关键（观点性长句）
KEY_STATEMENT_MIN_LEN = 40
# 句内文本样例长度上限（省流模式下 per_sentence 截断）
_DETAIL_TEXT_LIMIT = 160

_SENTENCE_SPLIT_PATTERN = re.compile(r"[。！？\n；]+")
_CITATION_PATTERN = re.compile(r"\[(\d{1,3})\]")
# 仅由引用编号组成的片段（如 "[1]"、"[1][2]"、"[1], [2]"）
_REF_ONLY_PATTERN = re.compile(r"^[\[\]0-9\s,，、/]+$")
# 句首编号簇：形如「[1] 正文」「[1][2]，正文」。这类编号紧跟上一条句末标点，
# 若不归前会把引用粘到下一条陈述上（上一条被判无引用、下一条被判仅外部引用）
_LEADING_REF_PATTERN = re.compile(
    r"^((?:\[[0-9]{1,3}\]\s*){1,8}[,，、/]?\s*)(\S.*)$"
)

# 免责声明模式：与 rag.audit_answer_citations 的判定口径一致，另加科研写作准则
# （决策 D-2）约定的提示句「文献集合未直接支持……」。按句复核用。
_DISCLAIMER_PATTERN = re.compile(
    r"并未?提及|未提及|未包含|未找到|不相关|并无|暂无|没有提及|没有找到|"
    r"未在.{0,12}中|缺乏.{0,8}依据|无.{0,6}依据|未直接引用|未直接支持|未直接说明",
    re.I,
)


def split_sentences(reply: str) -> list[str]:
    """按 。！？换行 ； 拆句（复用 audit_answer_citations 的切窗口径，保持风格一致）。

    两处归属修正（否则引用与所属陈述错配、支撑率被系统性低估）：
    1. 「…匹配。[1]」——纯编号片段被句号拆出，并入前一句；
    2. 「…分歧。[1] 下一句正文」——句首编号簇归回上一条陈述（编号剥到前句尾，
       正文留作本句）。真实回答里 LLM 常把编号紧跟句号写，若不修正会让上一条
       陈述被判「无引用」、下一条被判「仅外部引用」。
    """
    parts = [p.strip() for p in _SENTENCE_SPLIT_PATTERN.split(reply or "") if p.strip()]
    merged: list[str] = []
    for part in parts:
        # 句首编号簇归前：编号附加到上一条陈述，正文作为本句
        if merged:
            leading = _LEADING_REF_PATTERN.match(part)
            if leading:
                merged[-1] = f"{merged[-1]} {leading.group(1).strip()}"
                part = leading.group(2).strip()

        if not part:
            continue
        if _REF_ONLY_PATTERN.match(part):
            # 纯编号片段并入前一句（无关键陈述内容）
            if merged:
                merged[-1] = f"{merged[-1]} {part}"
            else:
                continue
        else:
            merged.append(part)
    return merged


def _cited_refs_in(text: str) -> set[int]:
    """提取句内引用编号集合。"""
    return {int(x) for x in _CITATION_PATTERN.findall(text)}


def _disclaimer_refs_in_sentence(sentence: str) -> set[int]:
    """按句判定句内哪些引用编号落在免责声明语境。

    不复用 audit 的全局 disclaimer_only：全局口径是「编号在全文任一位置出现免责
    声明窗口 → 整体免责」，一旦同一编号既作真引用又出现在免责声明句里，会被整体
    误伤，支撑率被系统性低估（真实样例：3 条真引用全判 external_only）。
    """
    out: set[int] = set()
    for n in _cited_refs_in(sentence):
        for match in re.finditer(rf"\[{n}\]", sentence):
            window = sentence[max(0, match.start() - 30) : min(len(sentence), match.end() + 60)]
            if _DISCLAIMER_PATTERN.search(window):
                out.add(n)
                break
    return out


def verify_citation_support(
    reply: str,
    sources: list[Any],
    audit: dict[str, Any] | None = None,
    literature_indices: set[int] | Iterable[int] | None = None,
    *,
    include_detail: bool = False,
    detail_limit: int = 3,
) -> dict[str, Any]:
    """把回答拆句，识别关键陈述并逐句校验文献引用支撑（design 决策 D-1）。

    判定规则：
    - 句内 [n] ∩ literature_indices 非空，且这些编号未被审计判为 disclaimer_only
      → supported（reason="literature_support"）
    - 有引用但均未落在文献集合（仅 web / 附件）→ external_only（不算文献支撑）
    - 无引用 → uncited
    - 非关键陈述 → 不参与计数（reason="not_key"）

    Args:
        reply: 模型回答全文
        sources: 本轮来源列表；literature_indices 为 None 时按 src.literature 兜底推导
        audit: audit_answer_citations 返回结果（提供 disclaimer_only 判定）
        literature_indices: 文献集合来源编号集
        include_detail: True 输出完整 per_sentence 详表；False 只给聚合值 + 前 N 条样例

    Returns:
        {sentence_count, key_statement_count, supported, unsupported_cited,
         unsupported_uncited, support_rate, per_sentence}
    """
    audit = audit or {}
    # 审计的全局免责判定仅透传展示，不用于扣减——扣减改按句复核（见
    # _disclaimer_refs_in_sentence 说明），避免真引用被免责声明句整体误伤。

    if literature_indices is None:
        lit_set: set[int] = {
            src.index for src in (sources or []) if bool(getattr(src, "literature", False))
        }
    else:
        lit_set = {int(n) for n in literature_indices}

    sentences = split_sentences(reply)
    per_sentence: list[dict[str, Any]] = []
    key_statement_count = 0
    supported = 0
    unsupported_cited = 0
    unsupported_uncited = 0

    for pos, sentence in enumerate(sentences):
        refs = _cited_refs_in(sentence)
        is_key = (
            bool(refs)
            or len(sentence) > KEY_STATEMENT_MIN_LEN
            or any(verb in sentence for verb in OPINION_VERBS)
        )
        if not is_key:
            per_sentence.append(
                {
                    "index": pos,
                    "text": sentence,
                    "is_key": False,
                    "cited_refs": sorted(refs),
                    "supported": False,
                    "reason": "not_key",
                }
            )
            continue

        key_statement_count += 1
        sentence_disclaimer = _disclaimer_refs_in_sentence(sentence)
        lit_refs = sorted((refs & lit_set) - sentence_disclaimer)
        if lit_refs:
            supported += 1
            reason = "literature_support"
        elif refs:
            unsupported_cited += 1
            reason = "external_only"
        else:
            unsupported_uncited += 1
            reason = "uncited"
        per_sentence.append(
            {
                "index": pos,
                "text": sentence,
                "is_key": True,
                "cited_refs": sorted(refs),
                "supported": bool(lit_refs),
                "reason": reason,
            }
        )

    support_rate = round(supported / key_statement_count, 4) if key_statement_count else 0.0
    result: dict[str, Any] = {
        "sentence_count": len(sentences),
        "key_statement_count": key_statement_count,
        "supported": supported,
        "unsupported_cited": unsupported_cited,
        "unsupported_uncited": unsupported_uncited,
        "support_rate": support_rate,
    }
    if include_detail:
        result["per_sentence"] = per_sentence
    else:
        result["per_sentence"] = [
            {**item, "text": item["text"][:_DETAIL_TEXT_LIMIT]}
            for item in per_sentence[:detail_limit]
        ]
    return result