"""知识图谱实体与关系抽取模块。

调用 LLM 从文档分块/全文中提取关键实体及语义关系，
并支持持久化写入 GraphStore。具备完善的降级保护与容错兜底。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from doc2mind.core.config import get_settings
from doc2mind.core.curator import _llm_json
from doc2mind.core.store.graph_store import GraphStore

logger = logging.getLogger("doc2mind.extractor")

_ENTITY_SYSTEM = (
    "你是知识图谱实体抽取助手。从文档中提取关键实体及其语义关系。\n"
    "请严格按以下标准 JSON 格式输出，不要输出任何解释说明：\n"
    "{\n"
    '  "entities": [\n'
    '    {"name": "Python", "type": "tech"},\n'
    '    {"name": "Guido van Rossum", "type": "person"}\n'
    "  ],\n"
    '  "relations": [\n'
    '    {"from": "Guido van Rossum", "to": "Python", "type": "develops"}\n'
    "  ]\n"
    "}\n"
    "规则要求：\n"
    "1. type 必须在 [person, org, tech, concept, event, place, other] 之间；\n"
    "2. relations 中的 from 与 to 必须在 entities 的 name 中已出现；\n"
    "3. relation 的 type 必须在 [belongs_to, uses, depends_on, part_of, develops, related_to] 之间；\n"
    "4. 严格使用 JSON 双引号，严禁使用单引号；\n"
    "5. 实体数量 3~25 个，关系 2~35 条；\n"
    "6. 只输出合法 JSON 纯文本，不要输出 markdown 代码栅栏以外的任何文字。"
)


def extract_entities(
    text: str,
    llm: Any,
    max_chars: int = 8000,
) -> dict[str, Any]:
    """使用 LLM 从文本中抽取实体与关系。

    返回:
        `{"entities": [...], "relations": [...]}`。
        LLM 失败或未配置时返回空 dict（降级）。
    """
    if not text or not text.strip() or llm is None:
        return {}

    truncated = text[:max_chars].strip()
    user_prompt = f"请从以下文档中提取实体和关系：\n\n{truncated}"

    result = _llm_json(
        client=llm,
        system_prompt=_ENTITY_SYSTEM,
        user_prompt=user_prompt,
        max_tokens=2048,
    )

    if not result or not isinstance(result, dict):
        return {}

    entities = result.get("entities")
    relations = result.get("relations")

    clean_entities = entities if isinstance(entities, list) else []
    clean_relations = relations if isinstance(relations, list) else []

    return {
        "entities": clean_entities,
        "relations": clean_relations,
    }


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
            "entities_count": len(extracted["entities"]),
            "relations_count": len(extracted.get("relations", [])),
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
            chunk_id=chunk_id,
        )
        return {
            "entities_count": counts["entities"],
            "relations_count": counts["relations"],
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
