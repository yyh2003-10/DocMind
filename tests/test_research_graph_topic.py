"""M3-T9 科研 topic 层扩散查询（GraphStore.get_topic_context）单元测试。

覆盖任务清单 9.1 单测要求：
- 空库 / 无命中 → 空结构不抛错
- depth=0 仅 seed 不扩散
- depth 扩散命中关联边
- limit_entities 封顶
- collections 限定（跨集合隔离）
- related_sources 文献标题聚合（复用 chunks_meta / documents 既有表）
- topic_label 优先 topic 类型实体
- 损坏 DB / 旧库异常 → 返回空结构不抛错
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from doc2mind.core.store.graph_store import GraphStore


EMPTY_RESULT = {"topic_edges": [], "related_sources": [], "topic_label": ""}


def _ensure_chunks_tables(db: Path) -> None:
    """在既有库上补齐 chunks_meta / documents 表（与 sqlite_vec 同结构，仅供聚合查询）。"""
    conn = sqlite3.connect(str(db))
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS documents ("
            " id TEXT PRIMARY KEY, title TEXT, summary TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS chunks_meta ("
            " id INTEGER PRIMARY KEY, document_id TEXT, source TEXT, "
            " heading TEXT, collection TEXT, content TEXT)"
        )
    finally:
        conn.close()


def _insert_source(db: Path, chunk_id: int, source: str, collection: str) -> None:
    """写入一条分块元数据 + 文档标题，用于 related_sources 聚合断言。"""
    conn = sqlite3.connect(str(db))
    try:
        doc_id = f"doc-{chunk_id}"
        conn.execute(
            "INSERT OR REPLACE INTO documents(id, title, summary) VALUES (?, ?, ?)",
            (doc_id, f"文献{chunk_id}", ""),
        )
        conn.execute(
            "INSERT OR REPLACE INTO chunks_meta(id, document_id, source, heading, collection, content)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (chunk_id, doc_id, source, f"分块{chunk_id}", collection, f"content-{chunk_id}"),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def store(tmp_path: Path) -> GraphStore:
    db_file = tmp_path / "graph.db"
    s = GraphStore(db_file)
    s.get_stats()  # 触发 schema 初始化
    _ensure_chunks_tables(db_file)
    s._db_path = db_file
    return s


# ---------- 空库 / 无命中 ----------


def test_empty_store_returns_empty_structure(store: GraphStore) -> None:
    """空库（零实体）返回空结构，三键齐全。"""
    result = store.get_topic_context(seed_keywords=["不存在的关键词"])
    assert result == EMPTY_RESULT


def test_no_seed_match_returns_empty(store: GraphStore) -> None:
    """库内有实体但关键词无匹配 → 空结构。"""
    store.upsert_entity("稠密向量", "concept", "lit")
    result = store.get_topic_context(seed_keywords=["量子纠缠"])
    assert result == EMPTY_RESULT


def test_negative_depth_clamped_to_zero(store: GraphStore) -> None:
    """负 depth 按 0 处理：仅 seed 不扩散，无关联边。"""
    a = store.upsert_entity("多模态检索", "topic", "lit")
    b = store.upsert_entity("稠密向量", "concept", "lit")
    store.upsert_relation(a, b, "uses")

    result = store.get_topic_context(seed_keywords=["多模态检索"], depth=-3)
    # depth=0：visited 仅 seed，不扩散到邻居，故无 topic_edges
    assert result["topic_edges"] == []
    assert result["topic_label"] == "多模态检索"


def test_corrupted_db_returns_empty_not_raises(tmp_path: Path) -> None:
    """DB 文件损坏（非 sqlite 格式）→ 外层容错返回空结构，不抛错。"""
    bad = tmp_path / "bad.db"
    bad.write_text("this is not a sqlite database", encoding="utf-8")
    store = GraphStore(bad)
    result = store.get_topic_context(seed_keywords=["任意"])
    assert result == EMPTY_RESULT


# ---------- 扩散与封顶 ----------


def test_depth_spread_collects_edges_and_sources(store: GraphStore) -> None:
    """depth=1 扩散到邻居，聚合 topic_edges 与 related_sources。"""
    a = store.upsert_entity("多模态检索", "topic", "lit")
    b = store.upsert_entity("稠密向量", "concept", "lit")
    store.upsert_relation(a, b, "uses")
    _insert_source(store._db_path, 1, r"E:\lit\paper_a.pdf", "lit")
    store.link_chunk(1, a)

    result = store.get_topic_context(seed_keywords=["多模态检索"], depth=1)

    assert len(result["topic_edges"]) == 1
    edge = result["topic_edges"][0]
    assert edge["from"] == "多模态检索"
    assert edge["from_type"] == "topic"
    assert edge["to"] == "稠密向量"
    assert edge["to_type"] == "concept"
    assert edge["relation"] == "uses"

    assert result["related_sources"] == [
        {
            "source": r"E:\lit\paper_a.pdf",
            "title": "文献1",
            "chunk_count": 1,
        }
    ]
    assert result["topic_label"] == "多模态检索"


def test_limit_entities_caps_spread(store: GraphStore) -> None:
    """limit_entities 封顶扩散范围，超出部分不进入结果。"""
    hub = store.upsert_entity("检索中枢", "topic", "lit")
    leaves = [
        store.upsert_entity(name, "concept", "lit") for name in ("A概念", "B概念", "C概念", "D概念")
    ]
    for leaf in leaves:
        store.upsert_relation(hub, leaf, "uses")  # upsert_relation 会跳过 related_to 脏边

    result = store.get_topic_context(
        seed_keywords=["检索中枢"], depth=2, limit_entities=3
    )

    names = {e["from"] for e in result["topic_edges"]} | {e["to"] for e in result["topic_edges"]}
    assert result["topic_edges"], "封顶后仍应保留部分关联边"
    assert len(names) <= 3


def test_seed_entity_ids_bypass_keyword_matching(store: GraphStore) -> None:
    """显式 seed_entity_ids 命中时不依赖关键词模糊匹配。"""
    a = store.upsert_entity("Alpha", "entity", "lit")
    b = store.upsert_entity("Beta", "entity", "lit")
    store.upsert_relation(a, b, "depends_on")

    result = store.get_topic_context(seed_entity_ids=[a], depth=1)

    assert len(result["topic_edges"]) == 1
    assert result["topic_edges"][0]["relation"] == "depends_on"
    assert result["topic_label"] in {"Alpha", "Beta"}


# ---------- 集合限定 ----------


def test_collections_limits_seed_and_sources(store: GraphStore) -> None:
    """collections 限定同时约束种子解析与 related_sources 来源集合。"""
    lit_entity = store.upsert_entity("文献概念", "concept", "lit")
    note_entity = store.upsert_entity("笔记概念", "concept", "notes")
    store.upsert_relation(lit_entity, note_entity, "mentions")
    _insert_source(store._db_path, 1, r"E:\lit\a.pdf", "lit")
    _insert_source(store._db_path, 2, r"E:\notes\scratch.md", "notes")
    store.link_chunk(1, lit_entity)
    store.link_chunk(2, note_entity)

    result = store.get_topic_context(
        seed_keywords=["文献概念"], depth=1, collections=["lit"]
    )

    # related_sources 仅返回文献集合内来源
    sources = {s["source"] for s in result["related_sources"]}
    assert r"E:\lit\a.pdf" in sources
    assert r"E:\notes\scratch.md" not in sources

    # 种子限定后仍能扩散出跨集合关联边（topic 联动价值）
    assert any(e["to"] == "笔记概念" for e in result["topic_edges"])


def test_collections_excludes_other_collection_seed(store: GraphStore) -> None:
    """集合限定外的种子实体不参与扩散 → 空结果。"""
    note_entity = store.upsert_entity("笔记概念", "concept", "notes")
    lit_entity = store.upsert_entity("文献概念", "concept", "lit")
    store.upsert_relation(note_entity, lit_entity, "mentions")

    result = store.get_topic_context(
        seed_keywords=["笔记概念"], depth=1, collections=["lit"]
    )
    assert result == EMPTY_RESULT


# ---------- topic_label 选取 ----------


def test_topic_label_prefers_topic_type_entity(store: GraphStore) -> None:
    """topic_label 优先取 topic 类型实体，而非 doc_count 更高的普通实体。"""
    topic = store.upsert_entity("大模型评测", "topic", "lit")
    heavy = store.upsert_entity("通用实体", "concept", "lit")
    # 提升普通实体的 doc_count，使其成为 doc_count 最高者
    for _ in range(5):
        store.upsert_entity("通用实体", "concept", "lit")
    store.upsert_relation(topic, heavy, "uses")

    result = store.get_topic_context(seed_keywords=["大模型评测"], depth=1)
    assert result["topic_label"] == "大模型评测"


def test_topic_label_falls_back_to_non_topic_entity(store: GraphStore) -> None:
    """无 topic 类型实体时 topic_label 回落到扩散层内普通实体。"""
    a = store.upsert_entity("向量量化", "concept", "lit")
    b = store.upsert_entity("产品量化", "concept", "lit")
    store.upsert_relation(a, b, "similar_to")  # related_to 被脏边治理跳过，改用合法关系名

    result = store.get_topic_context(seed_keywords=["向量量化"], depth=1)
    assert result["topic_label"] in {"向量量化", "产品量化"}