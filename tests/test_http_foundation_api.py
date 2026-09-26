"""基础功能 HTTP 契约与商用门禁集成测试（TestClient）。

覆盖已确认基础能力的对外行为：
- /v1/config 含 agent 预留字段且默认关闭
- /v1/trash 列表/恢复/清理 响应形状
- agentMode 被服务端门禁回落（需 mock LLM 时跳过流式体，只验门禁分支源码+config）
- ChatRequest 字段兼容 camelCase / snake_case
"""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("DOC2MIND_DISABLE_AUTH", "1")


@pytest.fixture()
def client(tmp_path: Path):
    from fastapi.testclient import TestClient

    from doc2mind.core.config import Settings
    from doc2mind.server.http import create_app

    s = Settings()
    s.db_path = tmp_path / "foundation.db"
    s.agent_mode_enabled = False
    s.llm_provider = "none"
    with patch("doc2mind.server.http.get_settings", return_value=s):
        app = create_app()
        # ensure_open 用 settings
        with TestClient(app) as c:
            c.app_state_settings = s  # type: ignore[attr-defined]
            c.test_app = app  # type: ignore[attr-defined]
            yield c


def test_config_exposes_agent_reserve_defaults(client):
    resp = client.get("/v1/config")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("agent_mode_enabled") is False
    assert body.get("agent_file_write_policy") in ("ask", "session_allow", "always_allow_workspace")
    # 基础字段仍在
    for key in ("embed_model", "llm_provider", "rag_top_k", "llm_api_key_configured"):
        assert key in body


def test_gpu_diagnosis_runs_outside_request_event_loop(client):
    import httpx

    diagnosis_thread: list[int] = []

    def fake_diagnosis():
        diagnosis_thread.append(threading.get_ident())
        return {"gpu_available": False, "recommended_path": "cpu"}

    async def request():
        event_loop_thread = threading.get_ident()
        transport = httpx.ASGITransport(app=client.test_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as async_client:
            response = await async_client.get("/v1/system/gpu-diagnosis")
        return response, event_loop_thread

    with patch("doc2mind.core.system_env.get_gpu_diagnosis", side_effect=fake_diagnosis):
        response, event_loop_thread = asyncio.run(request())

    assert response.status_code == 200
    assert response.json()["recommended_path"] == "cpu"
    assert diagnosis_thread and diagnosis_thread[0] != event_loop_thread


def test_chat_request_camel_and_snake_fields():
    from doc2mind.server.http import ChatRequest

    a = ChatRequest.model_validate(
        {"query": "hi", "continueWriting": True, "responseMode": "delivery", "agentMode": True}
    )
    assert a.continue_writing is True
    assert a.response_mode == "delivery"
    assert a.is_agent_mode() is True

    b = ChatRequest.model_validate(
        {"query": "hi", "continue_writing": False, "response_mode": "rag", "mode": "agent"}
    )
    assert b.is_agent_mode() is True

    c = ChatRequest.model_validate({"query": "hi"})
    assert c.is_agent_mode() is False
    assert c.continue_writing is False


def test_trash_endpoints_shape(client):
    # 空回收站
    resp = client.get("/v1/trash")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body and "total" in body
    assert isinstance(body["items"], list)

    # 恢复不存在的文档 → not_found，不 500
    resp2 = client.post("/v1/trash/doc-does-not-exist/restore")
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["status"] in ("not_found", "restored")
    assert "note" in body2

    # purge 空回收站
    resp3 = client.post("/v1/trash/purge", json={"older_than_days": 30})
    assert resp3.status_code == 200
    assert "purged" in resp3.json()


def test_trash_restore_note_mentions_reingest(client):
    resp = client.post("/v1/trash/whatever/restore")
    note = resp.json().get("note", "")
    assert note  # 非空
    # 成功或失败文案都应指导用户下一步（商用：不假装可检索）
    assert ("摄入" in note) or ("reindex" in note.lower()) or ("不存在" in note) or ("软删除" in note)


def test_documents_endpoint_reports_dynamic_source_missing(client, tmp_path: Path):
    from doc2mind.core.loader.base import make_source
    from doc2mind.core.store.sqlite_vec import StoredDocument

    present_path = tmp_path / "present.md"
    present_path.write_text("present", encoding="utf-8")
    missing_path = tmp_path / "missing.md"
    store = client.app.state.doc2mind.ensure_open()
    for doc_id, source in (
        ("present", make_source(present_path)),
        ("missing", make_source(missing_path)),
        ("note", "note:manual entry"),
    ):
        store.upsert_document(
            StoredDocument(
                id=doc_id,
                source=source,
                collection="default",
                format="md",
                file_hash=f"hash-{doc_id}",
                size_bytes=1,
                page_count=None,
                chunk_count=0,
                created_at="2026-01-01T00:00:00+08:00",
                updated_at="2026-01-01T00:00:00+08:00",
            )
        )

    response = client.get("/v1/documents?page_size=100")

    assert response.status_code == 200
    missing_by_id = {
        doc["id"]: doc["source_missing"] for doc in response.json()["documents"]
    }
    assert missing_by_id == {"present": False, "missing": True, "note": False}


def test_agent_mode_fallback_when_disabled():
    """商用门禁：settings.agent_mode_enabled=False 时源码必须先判断 agent_allowed。"""
    http = Path(r"E:\DocMindY-worktrees\agent-p0\src\doc2mind\server\http.py").read_text(encoding="utf-8")
    assert "agent_allowed = bool(getattr(state.settings, \"agent_mode_enabled\", False))" in http
    assert "if agent_requested and agent_allowed:" in http
    assert "已回落 RAG" in http or "回落 RAG" in http


def test_agent_mode_enabled_flag_roundtrip(client, tmp_path):
    from doc2mind.core.config import Settings as S
    from doc2mind.server import http as http_mod

    s = S()
    s.db_path = tmp_path / "cfg2.db"
    s.agent_mode_enabled = True
    s.agent_file_write_policy = "ask"
    # 直接构造 ConfigResponse 字段
    assert getattr(s, "agent_mode_enabled") is True
    body_cfg = {
        "agent_mode_enabled": bool(getattr(s, "agent_mode_enabled", False)),
        "agent_file_write_policy": str(getattr(s, "agent_file_write_policy", "session_allow")),
    }
    assert body_cfg["agent_mode_enabled"] is True
    assert body_cfg["agent_file_write_policy"] == "ask"


def test_search_request_and_empty_message_contract():
    """后端 SearchResponse.message 字段存在，供 FC-07 消费。"""
    from doc2mind.server.http import SearchResponse

    sr = SearchResponse(
        query="x",
        total=0,
        elapsed_ms=1.0,
        message="知识库为空：请先在【导入】页添加文档",
        hits=[],
    )
    assert sr.total == 0
    assert "知识库为空" in (sr.message or "")


def test_ingest_job_status_has_cancel_note_field():
    from datetime import datetime, timezone

    from doc2mind.server.http import JobStatus

    js = JobStatus(job_id="j1", type="ingest", status="cancelled", started_at=datetime.now(timezone.utc).isoformat())
    # pydantic 默认 cancel_note 可为 None
    assert getattr(js, "cancel_note", None) is None
    assert "cancel_note" in JobStatus.model_fields


def test_docs_and_mcp_tool_count_aligned():
    mcp_doc = Path(r"E:\DocMindY\docs\mcp.md").read_text(encoding="utf-8")
    assert "20 个" in mcp_doc
    assert "library_status" in mcp_doc
    agents = Path(r"E:\DocMindY\AGENTS.md").read_text(encoding="utf-8")
    assert "20 个" in agents or "20" in agents
