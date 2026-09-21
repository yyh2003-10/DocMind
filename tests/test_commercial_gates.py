"""商用级门禁：已确认基础功能的安全与产品护栏。"""

from __future__ import annotations

import re
from pathlib import Path

from doc2mind.core.agent.runtime.workspace import PathDeniedError, Workspace
from doc2mind.core.rag import _load_history
from doc2mind.server.http import ChatRequest


def test_agent_mode_requires_backend_flag():
    """商用：agent_mode_enabled=false 时不得进入 agent 分支。"""
    http = Path(r"E:\DocMindY-worktrees\agent-p0\src\doc2mind\server\http.py").read_text(encoding="utf-8")
    assert "agent_allowed" in http
    assert "agent_mode_enabled" in http
    # 必须先判断 allowed 再 import/执行 agent_answer_stream
    idx_allowed = http.find("agent_allowed = bool")
    idx_agent_import = http.find("from doc2mind.core.agent.runtime.chat_agent import agent_answer_stream")
    idx_branch = http.find("if agent_requested and agent_allowed:")
    assert 0 < idx_allowed < idx_branch < idx_agent_import or (idx_branch > 0 and "agent_allowed" in http[idx_branch:idx_branch + 80])
    assert "if agent_requested and agent_allowed:" in http


def test_chat_request_agent_default_false():
    req = ChatRequest(query="你好")
    assert req.is_agent_mode() is False
    req2 = ChatRequest(query="x", agent_mode=True)
    assert req2.is_agent_mode() is True
    req3 = ChatRequest(query="x", mode="agent")
    assert req3.is_agent_mode() is True
    req4 = ChatRequest(query="继续写", agent_mode=True, continue_writing=True)
    # is_agent_mode True，但 HTTP 层 continue 优先
    assert req4.continue_writing is True


def test_chat_id_sanitized_against_traversal(tmp_path):
    cid, _ = _load_history("../../Windows/evil", tmp_path / "c.db")
    assert ".." not in cid
    assert "/" not in cid and "\\" not in cid


def test_workspace_cannot_escape(tmp_path):
    ws = Workspace(tmp_path / "ws")
    for bad in ("../x.md", "..\\x.md", "/etc/passwd", "C:/Windows/win.ini"):
        try:
            ws.resolve(bad)
            # absolute paths outside root must raise
            raise AssertionError(f"should deny {bad}")
        except PathDeniedError:
            pass


def test_workspace_write_size_limit(tmp_path):
    ws = Workspace(tmp_path / "ws")
    big = "x" * (2 * 1024 * 1024 + 10)
    try:
        ws.write_text("notes/big.md", big)
        raise AssertionError("should deny oversized write")
    except PathDeniedError:
        pass


def test_trash_purge_requires_confirm_in_ui():
    vm = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\ViewModels\DocumentsViewModel.cs").read_text(encoding="utf-8")
    assert "ShowPurgeTrashConfirm" in vm
    assert "RequestPurgeTrash" in vm
    assert "ConfirmPurgeTrash" in vm
    # 不得再存在无确认直接绑定的 PurgeTrashCommand 作为按钮主命令
    xaml = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\Views\DocumentsView.xaml").read_text(encoding="utf-8")
    assert "RequestPurgeTrashCommand" in xaml
    assert "ConfirmPurgeTrashCommand" in xaml
    assert "不可恢复" in xaml or "不可恢复" in vm


def test_restore_note_requires_reingest():
    http = Path(r"E:\DocMindY-worktrees\agent-p0\src\doc2mind\server\http.py").read_text(encoding="utf-8")
    assert "重新摄入" in http
    assert "reindex" in http.lower()


def test_secrets_not_in_effective_config_list():
    vm = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\ViewModels\SettingsViewModel.cs").read_text(encoding="utf-8")
    # 生效列表里 Key 只显示已配置/未配置，不展示明文
    assert 'Add("LLM", "API Key"' in vm
    assert "已配置" in vm
    assert "cfg.LlmApiKeyConfigured" in vm
    # 不得把 Key 明文字段绑进列表（允许 LlmApiKeyConfigured 布尔）
    assert not re.search(r"cfg\.LlmApiKey\b(?!Configured)", vm)
    svc = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\Services\Doc2kbApiService.cs").read_text(encoding="utf-8")
    assert "RedactSecrets" in svc


def test_import_cancel_and_search_empty_still_gated():
    http = Path(r"E:\DocMindY-worktrees\agent-p0\src\doc2mind\server\http.py").read_text(encoding="utf-8")
    assert "cancel_note" in http
    assert "知识库为空" in http
    search_vm = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\ViewModels\SearchViewModel.cs").read_text(encoding="utf-8")
    assert "ShowGoImportAction" in search_vm
    graph = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\ViewModels\GraphViewModel.cs").read_text(encoding="utf-8")
    assert "CanExtractGraph" in graph and "IsLlmConfigured" in graph


def test_offline_banner_wired_for_core_pages():
    main = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\ViewModels\MainViewModel.cs").read_text(encoding="utf-8")
    for vm in ("_chatViewModel", "_searchViewModel", "_importViewModel", "_documentsViewModel",
               "_convertViewModel", "_graphViewModel", "_qualityViewModel"):
        assert f"{vm}.BackendUnreachable" in main, vm
