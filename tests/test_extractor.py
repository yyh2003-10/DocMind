"""实体抽取模块 extractor 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from doc2mind.core.extractor import extract_and_store, extract_entities
from doc2mind.core.store.graph_store import GraphStore


class MockLLMClient:
    def __init__(self, response: str | None = None, raise_error: bool = False) -> None:
        self.response = response
        self.raise_error = raise_error

    def chat(self, messages: list[dict[str, str]], **kwargs: Any) -> str:
        if self.raise_error:
            raise RuntimeError("LLM service unavailable")
        if self.response is not None:
            return self.response
        return json.dumps(
            {
                "entities": [
                    {"name": "SQLite", "type": "tech"},
                    {"name": "Python", "type": "tech"},
                ],
                "relations": [
                    {"from": "Python", "to": "SQLite", "type": "uses"},
                ],
            }
        )


def test_extract_entities_success() -> None:
    mock_llm = MockLLMClient()
    res = extract_entities("Python has built-in sqlite3 support.", mock_llm)
    assert len(res["entities"]) == 2
    assert len(res["relations"]) == 1
    assert res["entities"][0]["name"] == "SQLite"


def test_extract_entities_llm_failure() -> None:
    mock_llm = MockLLMClient(raise_error=True)
    res = extract_entities("some text", mock_llm)
    assert res == {}


def test_extract_and_store_no_llm(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    res = extract_and_store("some text", "default", None, db_path=db_file)
    assert "skipped" in res
    assert res["entities_count"] == 0


def test_extract_and_store_success(tmp_path: Path) -> None:
    db_file = tmp_path / "test.db"
    mock_llm = MockLLMClient()
    res = extract_and_store("Python and SQLite", "default", mock_llm, db_path=db_file)
    assert res["entities_count"] == 2
    assert res["relations_count"] == 1

    store = GraphStore(db_file)
    graph = store.get_graph("default")
    assert graph["total_nodes"] == 2
    assert len(graph["edges"]) == 1


def test_drops_fragment_entity_from_long_model_token() -> None:
    """ASDA-B3 文档不得抽出碎片实体 AS。"""
    response = json.dumps({
        "entities": [
            {"name": "ASDA-B3", "type": "tech"},
            {"name": "AS", "type": "concept"},
            {"name": "AtomCode", "type": "tech"},
        ],
        "relations": [
            {"from": "AS", "to": "ASDA-B3", "type": "uses"},
            {"from": "ASDA-B3", "to": "AtomCode", "type": "related_to"},
        ],
    })
    text = "DELTA_IA-ASD_ASDA-B3 伺服手册说明 AtomCode 集成。"
    res = extract_entities(text, MockLLMClient(response=response))
    names = {e["name"] for e in res["entities"]}
    assert "AS" not in names
    assert "ASDA-B3" in names
    assert "AtomCode" in names
    # related_to 兜底关系被丢弃
    assert all(r["type"] != "related_to" for r in res["relations"])


def test_drops_latin_entity_not_in_source() -> None:
    response = json.dumps({
        "entities": [
            {"name": "agent skills", "type": "concept"},
            {"name": "AtomCode", "type": "tech"},
        ],
        "relations": [],
    })
    res = extract_entities("文档只提到 AtomCode 宿主。", MockLLMClient(response=response))
    names = {e["name"] for e in res["entities"]}
    assert "AtomCode" in names
    # 含空格的短语不按纯拉丁名拦截；但 AS 碎片应被拦
    response2 = json.dumps({
        "entities": [{"name": "AS", "type": "concept"}],
        "relations": [],
    })
    res2 = extract_entities(
        "使用 ASDA-B3 伺服。", MockLLMClient(response=response2)
    )
    assert res2["entities"] == []


def test_graph_store_skips_related_to_edges(tmp_path: Path) -> None:
    db_file = tmp_path / "g.db"
    store = GraphStore(db_file)
    store.add_document_entities(
        doc_id="d1",
        collection="default",
        entities=[{"name": "A", "type": "tech"}, {"name": "B", "type": "tech"}],
        relations=[
            {"from": "A", "to": "B", "type": "uses"},
            {"from": "A", "to": "B", "type": "related_to"},
            {"from": "A", "to": "B", "type": ""},
        ],
    )
    graph = store.get_graph("default")
    rel_types = {e.get("label") for e in graph["edges"]}
    assert "uses" in rel_types
    assert "related_to" not in rel_types
    store.close()
