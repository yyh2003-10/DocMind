"""pytest 全局配置：测试环境关闭 HTTP 鉴权。

生产环境默认启用 Bearer 令牌鉴权（见 src/doc2mind/server/http.py）。
测试用例直接构造 create_app()，不应读写真实用户数据目录的 server.token，
统一通过 DOC2MIND_DISABLE_AUTH=1 显式关闭鉴权。
"""

from __future__ import annotations

import os

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
