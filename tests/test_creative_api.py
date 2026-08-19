"""创作 HTTP 端点集成测试。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from doc2mind.core.config import Settings
from doc2mind.server.http import create_app


def test_creative_export_endpoint(tmp_path: Path):
    settings = Settings(db_path=tmp_path / "test.db")
    app = create_app(settings)
    client = TestClient(app)

    # 1. 测试成功导出 DOCX
    req_body = {
        "content": "# 测试研报\n## 章节一\n内容详情",
        "format": "docx",
        "title": "API测试报告",
    }
    resp = client.post("/v1/creative/export", json=req_body)
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["format"] == "docx"
    assert Path(data["file_path"]).exists()

    # 2. 测试空内容 400 校验
    empty_req = {"content": "   ", "format": "docx"}
    resp_empty = client.post("/v1/creative/export", json=empty_req)
    assert resp_empty.status_code == 400
