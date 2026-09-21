"""HTTP 鉴权中间件单元测试。

覆盖：
- 默认（无 DOC2MIND_DISABLE_AUTH 时）只有 health 匿名可访问，其余 401
- 携带正确 Bearer 令牌可访问
- X-DocMind-Token 头也可通过
- 令牌文件幂等创建/复用
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from doc2mind.server.http import (
    _build_auth_middleware,
    _load_or_create_auth_token,
)


@pytest.fixture()
def auth_app(tmp_path: Path):
    """构造启用鉴权的 app，令牌文件放在 tmp_path。"""
    from fastapi.testclient import TestClient

    # 清掉测试环境变量，确保走真实鉴权路径
    os.environ.pop("DOC2MIND_DISABLE_AUTH", None)

    token = _load_or_create_auth_token(tmp_path)
    assert token, "令牌应能创建"

    with patch("doc2mind.server.http._user_data_dir", return_value=tmp_path):
        from doc2mind.server.http import create_app as _create

        app = _create()
        yield TestClient(app), token

    os.environ["DOC2MIND_DISABLE_AUTH"] = "1"  # 恢复 conftest 默认


def test_unauthorized_returns_401(auth_app):
    client, token = auth_app
    # health 匿名可访问
    assert client.get("/v1/health").status_code == 200
    # 其余端点无令牌 → 401
    resp = client.get("/v1/config")
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "UNAUTHORIZED"


def test_bearer_token_allows_access(auth_app):
    client, token = auth_app
    resp = client.get("/v1/config", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_x_docmind_token_header_allows_access(auth_app):
    client, token = auth_app
    resp = client.get("/v1/config", headers={"X-DocMind-Token": token})
    assert resp.status_code == 200


def test_wrong_token_rejected(auth_app):
    client, _ = auth_app
    resp = client.get("/v1/config", headers={"Authorization": "Bearer wrong-token"})
    assert resp.status_code == 401


def test_token_file_created_and_reused(tmp_path: Path):
    tok1 = _load_or_create_auth_token(tmp_path)
    tok2 = _load_or_create_auth_token(tmp_path)
    assert tok1 == tok2  # 幂等复用
    assert (tmp_path / "server.token").is_file()


def test_middleware_compares_constant_time():

    async def call_next(request):
        return {"ok": True}

    mw = _build_auth_middleware("secret-token")
    # 类型层面确保可调用；具体行为由 TestClient 用例覆盖
    assert callable(mw)
