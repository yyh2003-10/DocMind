"""知识图谱存储层 GraphStore 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from doc2mind.core.store.graph_store import GraphStore


@pytest.fixture
def graph_store(tmp_path: Path) -> GraphStore:
    db_file = tmp_path / "test_docmind.db"
    return GraphStore(db_file)


def test_upsert_entity_dedups(graph_store: GraphStore) -> None:
    # 首次插入，doc_count 为 1
    eid1 = graph_store.upsert_entity("SQLite", "tech", "default")
    assert eid1

    # 再次插入相同 (collection, name, type)，应返回相同 ID，doc_count 增加
    eid2 = graph_store.upsert_entity("SQLite", "tech", "default")
    assert eid1 == eid2

    graph = graph_store.get_graph(collection="default")
    assert len(graph["nodes"]) == 1
    assert graph["nodes"][0]["name"] == "SQLite"
    assert graph["nodes"][0]["size"] == 2


def test_relation_idempotent(graph_store: GraphStore) -> None:
    eid1 = graph_store.upsert_entity("DocMind", "tech", "default")
    eid2 = graph_store.upsert_entity("FastAPI", "tech", "default")

    # 插入关系
    graph_store.upsert_relation(eid1, eid2, "uses")
    # 重复插入相同关系不报错
    graph_store.upsert_relation(eid1, eid2, "uses")

    rels = graph_store.get_entity_relations(eid1)
    assert len(rels) == 1
    assert rels[0]["relation"] == "uses"
    assert rels[0]["from_name"] == "DocMind"
    assert rels[0]["to_name"] == "FastAPI"


def test_link_chunk(graph_store: GraphStore) -> None:
    eid = graph_store.upsert_entity("向量检索", "concept", "default")
    graph_store.link_chunk(101, eid)
    # 重复 link 不报错
    graph_store.link_chunk(101, eid)


def test_add_document_entities_and_get_graph(graph_store: GraphStore) -> None:
    entities = [
        {"name": "Python", "type": "tech"},
        {"name": "FastAPI", "type": "tech"},
    ]
    relations = [
        {"from": "FastAPI", "to": "Python", "type": "written_in"},
        # 容忍 LLM 抽取了未在 entities 列表出现的名称
        {"from": "FastAPI", "to": "Starlette", "type": "depends_on"},
    ]

    res = graph_store.add_document_entities("doc_01", "default", entities, relations, chunk_id=1)
    assert res["entities"] == 2
    assert res["relations"] == 2

    graph = graph_store.get_graph("default")
    assert graph["total_nodes"] == 3  # Python, FastAPI, Starlette(自动兜底)
    assert len(graph["edges"]) == 2

    stats = graph_store.get_stats("default")
    assert stats["entity_count"] == 3
    assert stats["relation_count"] == 2
