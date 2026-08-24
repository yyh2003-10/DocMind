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
    "3. relations：严格按照知识内容真实的因果、输入输出、组成或调用关系建立连接，推荐关系类型 [belongs_to, uses, depends_on, part_of, develops, feeds_into, combines_with, implements, related_to]；\n"
    "4. 严格使用 JSON 双引号，严禁使用单引号；\n"
    "5. 只输出合法 JSON 纯文本，不要输出 markdown 代码栅栏以外的任何文字。"
)


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

    topics = result.get("topics")
    entities = result.get("entities")
    relations = result.get("relations")

    clean_topics = topics if isinstance(topics, list) else []
    clean_entities = entities if isinstance(entities, list) else []
    clean_relations = relations if isinstance(relations, list) else []

    return {
        "topics": clean_topics,
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
