"""pytest 全局配置：鉴权关闭 + 隔离用户数据目录（禁止写生产库）。

生产环境默认启用 Bearer 令牌鉴权（见 src/doc2mind/server/http.py）。
测试用例直接构造 create_app() / Settings() 时，绝不能落到
%LOCALAPPDATA%/doc2mind/doc2mind.db——否则 pytest 会污染真实会话库。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest


def pytest_configure(config) -> None:  # pragma: no cover — 纯测试基建
    os.environ.setdefault("DOC2MIND_DISABLE_AUTH", "1")


@pytest.fixture(autouse=True)
def _restore_disable_auth():
    """每个用例前恢复全局「关闭鉴权」状态。

    个别用例（如 test_integration / test_auth_middleware）为验证鉴权路径会
    `os.environ.pop("DOC2MIND_DISABLE_AUTH")`，这会删掉本模块 pytest_configure
    设置的会话级变量，导致排在后面的用例走到真实令牌文件、鉴权开启而 401。
    此 fixture 保证每个用例开始时状态一致，免疫任何用例内的环境变量改动。
    """
    os.environ.setdefault("DOC2MIND_DISABLE_AUTH", "1")
    yield


@pytest.fixture(autouse=True)
def _isolate_doc2mind_storage(tmp_path_factory, monkeypatch):
    """把 Settings()/get_settings() 的用户数据目录指到用例临时目录。

    覆盖点：
    1. `_user_data_dir()` — Settings() 默认 db_path / embed_cache 由此拼出
    2. `DOC2MIND_DB_PATH` — Settings.from_env() 高优先级
    3. 全局 `_settings` 单例 — get_settings()/create_app() 实际读取的缓存
    """
    isolated_root: Path = tmp_path_factory.mktemp("doc2mind_user_data")
    isolated_db = isolated_root / "doc2mind.db"

    monkeypatch.setattr("doc2mind.core.config._user_data_dir", lambda: isolated_root)
    monkeypatch.setenv("DOC2MIND_DB_PATH", str(isolated_db))

    import doc2mind.core.config as config_mod

    previous = config_mod._settings
    config_mod._settings = None
    # 预热：让 get_settings() 在隔离路径上构建，避免用例内首个 create_app 撞生产库
    _ = config_mod.get_settings()
    yield
    config_mod._settings = previous
