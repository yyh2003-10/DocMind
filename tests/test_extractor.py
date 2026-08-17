"""实体抽取模块 extractor 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

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
