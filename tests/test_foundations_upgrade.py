"""基础能力完善：FC-04 生效配置 / 回收站 / Agent 升级预留。"""

from __future__ import annotations

from pathlib import Path


def test_fc04_effective_config_view():
    vm = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\ViewModels\SettingsViewModel.cs").read_text(encoding="utf-8")
    assert "EffectiveConfigItems" in vm
    assert "RebuildEffectiveConfigItems" in vm
    xaml = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\Views\SettingsView.xaml").read_text(encoding="utf-8")
    assert "EffectiveConfigItems" in xaml
    assert "生效明细" in xaml


def test_trash_ui_and_api():
    iface = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\Services\IDoc2kbApiService.cs").read_text(encoding="utf-8")
    assert "ListTrashAsync" in iface
    assert "RestoreTrashedDocumentAsync" in iface
    assert "PurgeTrashAsync" in iface
    vm = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\ViewModels\DocumentsViewModel.cs").read_text(encoding="utf-8")
    assert "TrashItems" in vm
    assert "RestoreTrashItemAsync" in vm
    assert "重新摄入" in vm or "reindex" in vm.lower()
    xaml = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\Views\DocumentsView.xaml").read_text(encoding="utf-8")
    assert "ToggleTrashCommand" in xaml
    assert "RestoreTrashItemCommand" in xaml
    http = Path(r"E:\DocMindY-worktrees\agent-p0\src\doc2mind\server\http.py").read_text(encoding="utf-8")
    assert "/v1/trash" in http


def test_agent_upgrade_hooks_default_off():
    settings = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\AppSettings.cs").read_text(encoding="utf-8")
    assert "AgentModeEnabled" in settings
    assert "AgentFileWritePolicy" in settings
    # 默认关闭
    assert "AgentModeEnabled { get; set; } = false" in settings or "AgentModeEnabled { get; set; } = false;" in settings.replace(" ", "")
    chat_req = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\Models\ChatRequest.cs").read_text(encoding="utf-8")
    assert "agentMode" in chat_req
    cfg = Path(r"E:\DocMindY-worktrees\agent-p0\src\doc2mind\core\config.py").read_text(encoding="utf-8")
    assert "agent_mode_enabled" in cfg
    assert "agent_file_write_policy" in cfg
    http = Path(r"E:\DocMindY-worktrees\agent-p0\src\doc2mind\server\http.py").read_text(encoding="utf-8")
    assert "agent_mode_enabled" in http
    # ChatViewModel 默认不打开 Agent
    cvm = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\ViewModels\ChatViewModel.cs").read_text(encoding="utf-8")
    assert "AgentModeEnabled" in cvm


def test_mcp_tool_count_documented():
    mcp = Path(r"E:\DocMindY\docs\mcp.md").read_text(encoding="utf-8")
    assert "20 个" in mcp
    assert "library_status" in mcp
    http_mcp = Path(r"E:\DocMindY-worktrees\agent-p0\src\doc2mind\server\mcp.py").read_text(encoding="utf-8")
    assert "library_status" in http_mcp
