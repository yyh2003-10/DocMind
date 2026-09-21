"""知识图谱实体与关系抽取模块。

调用 LLM 从文档分块/全文中提取关键实体及语义关系，
并支持持久化写入 GraphStore。具备完善的降级保护与容错兜底。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from doc2mind.core.config import get_settings
from doc2mind.core.curator import _llm_json
from doc2mind.core.store.graph_store import GraphStore

logger = logging.getLogger("doc2mind.extractor")

_ENTITY_SYSTEM = (
    "你是深度知识图谱与内容语义分析专家。从文档中精准提取【业务内容核心主题(Topics)】、【关联实体(Entities)】及其【真实内容逻辑关系网(Relations)】。\n"
    "【核心原则】：必须完全依据文档内容的【实质业务脉络、因果依赖、组成关系】来提炼和建立连接，严禁按实体词性性质（如把所有技术堆一起、把所有概念堆一起）机械分组。\n\n"
    "请严格按以下标准 JSON 格式输出，不要输出任何解释说明：\n"
    "{\n"
    '  "topics": [\n'
    '    {"name": "向量存储与混合检索架构", "summary": "向量底座选型、SQLite-vec 扩展与 BM25 混合排序机制"},\n'
    '    {"name": "客户端交互与状态管理", "summary": "WPF 界面渲染流、WebView2 通信与异步任务响应"}\n'
    '  ],\n'
    '  "entities": [\n'
    '    {"name": "SQLite-vec", "type": "tech", "topic": "向量存储与混合检索架构"},\n'
    '    {"name": "BM25 检索", "type": "concept", "topic": "向量存储与混合检索架构"},\n'
    '    {"name": "混合重排算法", "type": "concept", "topic": "向量存储与混合检索架构"},\n'
    '    {"name": "WebView2 桥接", "type": "tech", "topic": "客户端交互与状态管理"}\n'
    '  ],\n'
    '  "relations": [\n'
    '    {"from": "SQLite-vec", "to": "BM25 检索", "type": "combines_with"},\n'
    '    {"from": "BM25 检索", "to": "混合重排算法", "type": "feeds_into"}\n'
    '  ]\n'
    "}\n"
    "规则要求：\n"
    "1. topics：提取 1~4 个概括本文核心业务脉络或功能模块的高维主题，名称精炼有力；\n"
    "2. entities：提取 3~25 个核心实体，每个实体依据内容逻辑归属到对应的主题；\n"
    "3. relations：严格按照知识内容真实的因果、输入输出、组成或调用关系建立连接，"
    "推荐关系类型 [belongs_to, uses, depends_on, part_of, develops, feeds_into, combines_with, implements]；"
    "禁止用 related_to 兜底——没有明确语义关系就不要输出该条；\n"
    "4. 实体命名必须与文档原文一致：禁止把长型号/缩写再切碎（例如文档写 ASDA-B3，"
    "不得输出实体 AS 或 ASD；文档写 AtomCode，不得输出 Atom）；禁止凭空扩写英文全称"
    "（不得把缩写解释成 agent skills 之类文档中未出现的词）；\n"
    "5. 实体名须能在原文中找到字面出现；仅 1~2 个字母的碎片名一律不要输出；\n"
    "6. 严格使用 JSON 双引号，严禁使用单引号；\n"
    "7. 只输出合法 JSON 纯文本，不要输出 markdown 代码栅栏以外的任何文字。"
)


# 文档中出现的长型号/缩写；用于拦截 LLM 把其切成 1~2 字母碎片实体
_LONG_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{2,}(?:[-_][A-Za-z0-9]+)*")


def _source_long_tokens(text: str) -> set[str]:
    """原文中的长拉丁/型号 token（小写），用于碎片实体拦截。"""
    return {m.group().lower() for m in _LONG_TOKEN_RE.finditer(text or "")}


def _is_fragment_of_long_token(name: str, long_tokens: set[str]) -> bool:
    """实体名是否只是原文更长 token 的碎片（如 AS ⊂ ASDA-B3）。"""
    clean = (name or "").strip()
    if not clean or len(clean) > 2:
        return False
    if not re.fullmatch(r"[A-Za-z]{1,2}", clean):
        return False
    frag = clean.lower()
    # 本身就在原文以完整形式出现过（如真的实体「AI」）则放行
    # 碎片判定：存在某个更长 token 包含该碎片，且碎片不是独立词
    for token in long_tokens:
        if len(token) <= len(frag):
            continue
        if frag in token:
            # 独立词例外：token 以碎片开头且后跟非字母（如 as 在 "as "）——
            # 长 token 集合来自连续拉丁串，这里只要求「包含且更长」即视为碎片
            return True
    return False


def _sanitize_extracted(
    extracted: dict[str, Any],
    source_text: str,
) -> dict[str, Any]:
    """过滤脏实体/空关系，避免 ASD→AS、related_to 兜底污染图谱。"""
    long_tokens = _source_long_tokens(source_text)
    source_cf = (source_text or "").casefold()

    clean_entities: list[dict[str, Any]] = []
    kept_names: set[str] = set()
    for ent in extracted.get("entities") or []:
        if not isinstance(ent, dict):
            continue
        name = str(ent.get("name", "")).strip()
        if not name or len(name) < 2:
            continue
        if _is_fragment_of_long_token(name, long_tokens):
            logger.debug("丢弃碎片实体: %s", name)
            continue
        # 仅拦截明显未在原文出现的拉丁实体；中文实体多为语义归纳，不强制字面
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9._/-]*", name) and name.casefold() not in source_cf:
            logger.debug("丢弃原文未出现的拉丁实体: %s", name)
            continue
        ent = dict(ent)
        ent["name"] = name
        clean_entities.append(ent)
        kept_names.add(name)

    clean_relations: list[dict[str, Any]] = []
    for rel in extracted.get("relations") or []:
        if not isinstance(rel, dict):
            continue
        frm = str(rel.get("from", "")).strip()
        to = str(rel.get("to", "")).strip()
        rtype = str(rel.get("type", "")).strip()
        if not frm or not to or frm == to:
            continue
        # 空关系或 related_to 兜底：没有明确语义就不要建边
        if not rtype or rtype == "related_to":
            logger.debug("丢弃 related_to/空类型关系: %s -> %s", frm, to)
            continue
        # 端点须是已保留实体或主题
        topic_names = {
            str(t.get("name", "")).strip()
            for t in (extracted.get("topics") or [])
            if isinstance(t, dict)
        }
        if frm not in kept_names and frm not in topic_names:
            continue
        if to not in kept_names and to not in topic_names:
            continue
        clean_relations.append({"from": frm, "to": to, "type": rtype})

    return {
        "topics": extracted.get("topics") or [],
        "entities": clean_entities,
        "relations": clean_relations,
    }


def extract_entities(
    text: str,
    llm: Any,
    max_chars: int = 8000,
) -> dict[str, Any]:
    """使用 LLM 从文本中抽取主题、实体与关系。

    返回:
        `{"topics": [...], "entities": [...], "relations": [...]}`。
        LLM 失败或未配置时返回空 dict（降级）。
    """
    if not text or not text.strip() or llm is None:
        return {}

    truncated = text[:max_chars].strip()
    user_prompt = f"请从以下文档中提取高维主题、实体和关系网：\n\n{truncated}"

    result = _llm_json(
        client=llm,
        system_prompt=_ENTITY_SYSTEM,
        user_prompt=user_prompt,
        max_tokens=2048,
    )

    if not result or not isinstance(result, dict):
        return {}

    raw = {
        "topics": result.get("topics") if isinstance(result.get("topics"), list) else [],
        "entities": result.get("entities") if isinstance(result.get("entities"), list) else [],
        "relations": result.get("relations") if isinstance(result.get("relations"), list) else [],
    }
    return _sanitize_extracted(raw, text)


def extract_and_store(
    text: str,
    collection: str,
    llm: Any | None,
    doc_id: str = "",
    chunk_id: int | None = None,
    db_path: Path | None = None,
    settings: Any | None = None,
    dry_run: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """抽取实体与关系并持久化写入 SQLite（支持 dry_run）。

    LLM 未配置或调用失败时返回带有 skipped 说明的报告，不抛出异常。
    """
    if llm is None:
        return {
            "skipped": "未配置 LLM，跳过实体抽取",
            "entities_count": 0,
            "relations_count": 0,
        }

    extracted = extract_entities(text, llm)
    if not extracted or not extracted.get("entities"):
        return {
            "skipped": "抽取结果为空或 LLM 解析失败",
            "entities_count": 0,
            "relations_count": 0,
        }

    if dry_run:
        return {
            "topics_count": len(extracted.get("topics", [])),
            "entities_count": len(extracted["entities"]),
            "relations_count": len(extracted.get("relations", [])),
            "topics": extracted.get("topics", []),
            "entities": extracted["entities"],
            "relations": extracted.get("relations", []),
            "dry_run": True,
        }

    effective_db_path = db_path or (settings.db_path if settings else get_settings().db_path)
    store = GraphStore(effective_db_path)
    try:
        counts = store.add_document_entities(
            doc_id=doc_id,
            collection=collection,
            entities=extracted["entities"],
            relations=extracted.get("relations", []),
            topics=extracted.get("topics", []),
            chunk_id=chunk_id,
        )
        return {
            "topics_count": counts.get("topics", len(extracted.get("topics", []))),
            "entities_count": counts["entities"],
            "relations_count": counts["relations"],
            "topics": extracted.get("topics", []),
            "entities": extracted["entities"],
            "relations": extracted.get("relations", []),
        }
    except Exception as e:  # noqa: BLE001 — 图谱存储失败降级，不阻断主流程
        logger.warning("图谱写入失败: %s", e)
        return {
            "error": str(e),
            "entities_count": 0,
            "relations_count": 0,
        }
    finally:
        store.close()
