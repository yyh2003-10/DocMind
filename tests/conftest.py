"""tests 全局夹具 — 隔离用户数据目录。

会话持久化（rag → ChatStore）与嵌入缓存都落在 `_user_data_dir()`
（Windows %LOCALAPPDATA%\\doc2mind）。autouse 重定向到每个测试独立的
临时目录，并把全局 Settings 单例置空强制按临时目录重建——否则集成
测试跑一次就会在真实知识库 DB 里留下测试会话，污染 WPF 会话列表。

日志目录同样必须隔离：`logging_setup._user_data_dir` 是 import 时的
独立绑定（`from doc2mind.core.config import _user_data_dir`），不随
`config._user_data_dir` 的 patch 变化；若不单独 patch，测试内首次触发
`setup_logging()` 会把日志 handler 指向真实目录，整个测试进程的日志
（含 pytest 临时路径）都会写入真实 doc2mind.log。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_user_data_dir(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    from doc2mind.core import config as config_mod
    from doc2mind.core import logging_setup as ls

    monkeypatch.setattr(config_mod, "_user_data_dir", lambda: tmp_path)
    # logging_setup 持的是 config._user_data_dir 的 import 绑定，需单独 patch，
    # 否则测试日志写入真实用户目录（污染 doc2mind.log）
    monkeypatch.setattr(ls, "_user_data_dir", lambda: tmp_path)
    # 全局单例若已在真实目录上创建则作废，get_settings() 将按临时目录重建；
    # 测试结束时 monkeypatch 恢复原单例对象与目录函数
    monkeypatch.setattr(config_mod, "_settings", None)
    # 强制重配日志到临时目录：清掉上一测试可能遗留的真实目录 handler
    # （setup_logging 幂等，普通调用不会切换已存在的 handler）
    ls.setup_logging(force=True)
    yield
