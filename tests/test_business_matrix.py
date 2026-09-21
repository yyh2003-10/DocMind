"""全量业务矩阵结构检测：端点/VM/契约/测试文件齐套性。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(r"E:\DocMindY-worktrees\agent-p0")
HTTP = (ROOT / "src/doc2mind/server/http.py").read_text(encoding="utf-8")
VM = ROOT / "DocMind/ViewModels"
TESTS = ROOT / "tests"

REQUIRED_ENDPOINTS = [
    "/v1/health",
    "/v1/config",
    "/v1/llm/test",
    "/v1/ingest",
    "/v1/ingest/job",
    "/v1/ingest/text",
    "/v1/search",
    "/v1/chat",
    "/v1/chat/stream",
    "/v1/chats",
    "/v1/documents",
    "/v1/trash",
    "/v1/trash/{doc_id}/restore",
    "/v1/trash/purge",
    "/v1/quality",
    "/v1/curate",
    "/v1/graph/visualize",
    "/v1/graph/extract",
    "/v1/creative/export",
    "/v1/creative/inspect",
    "/v1/convert",
    "/v1/reindex",
    "/v1/jobs/{job_id}",
    "/v1/library/status",
]

REQUIRED_VMS = [
    "ChatViewModel.cs",
    "SearchViewModel.cs",
    "ImportViewModel.cs",
    "DocumentsViewModel.cs",
    "GraphViewModel.cs",
    "QualityViewModel.cs",
    "SettingsViewModel.cs",
    "ConvertViewModel.cs",
    "MainViewModel.cs",
]

REQUIRED_TESTS = [
    "test_rag.py",
    "test_prompt_policy.py",
    "test_continue_merge.py",
    "test_agent_chat_stream.py",
    "test_agent_runtime.py",
    "test_agent_executors.py",
    "test_fc01_import_cancel.py",
    "test_fc07_search_empty.py",
    "test_contract_p0_gates.py",
    "test_commercial_gates.py",
    "test_foundations_upgrade.py",
    "test_http_foundation_api.py",
    "test_soft_delete.py",
    "test_cancel_contracts.py",
    "test_auth_middleware.py",
    "test_config.py",
    "test_http_dto_contract.py",
    "test_chat_store.py",
    "test_creative_api.py",
    "test_curator.py",
]

REQUIRED_FEATURE_MARKERS = {
    "offline_banner": ("MainViewModel.cs", "BackendUnreachable"),
    "llm_gate_graph": ("GraphViewModel.cs", "CanExtractGraph"),
    "llm_gate_quality": ("QualityViewModel.cs", "IsLlmConfigured"),
    "search_empty_fc07": ("SearchViewModel.cs", "ShowGoImportAction"),
    "trash_ui": ("DocumentsViewModel.cs", "TrashItems"),
    "settings_effective_fc04": ("SettingsViewModel.cs", "EffectiveConfigItems"),
    "import_cancel_note": ("ImportViewModel.cs", "CancelNote"),
    "continue_writing": ("ChatViewModel.cs", "ContinueWriting"),
    "agent_reserve_chat": ("ChatRequest.cs", "agentMode"),
    "prompt_policy": ("prompt_policy.py", "PROMPT_TRACK_DELIVERY"),
    "workspace_sandbox": ("workspace.py", "PathDeniedError"),
    "agent_gate_http": ("http.py", "agent_mode_enabled"),
    "cancel_note_http": ("http.py", "cancel_note"),
    "on_result_pipeline": ("pipeline.py", "on_result"),
}


def _find_in_docmind_or_src(filename: str) -> str:
    hits = list(ROOT.rglob(filename))
    hits = [h for h in hits if "obj" not in h.parts and "bin" not in h.parts]
    assert hits, f"missing file {filename}"
    return hits[0].read_text(encoding="utf-8", errors="replace")


def test_all_required_endpoints_exist():
    missing = [e for e in REQUIRED_ENDPOINTS if f'"{e}"' not in HTTP and f"'{e}'" not in HTTP]
    assert not missing, f"missing endpoints: {missing}"


def test_all_required_viewmodels_exist():
    missing = [v for v in REQUIRED_VMS if not (VM / v).is_file()]
    assert not missing, f"missing VMs: {missing}"


def test_all_required_tests_exist():
    missing = [t for t in REQUIRED_TESTS if not (TESTS / t).is_file()]
    assert not missing, f"missing tests: {missing}"


def test_feature_markers_present():
    bad = []
    for name, (fname, marker) in REQUIRED_FEATURE_MARKERS.items():
        try:
            text = _find_in_docmind_or_src(fname)
        except AssertionError:
            bad.append(f"{name}:file:{fname}")
            continue
        if marker not in text:
            bad.append(f"{name}:{fname}:{marker}")
    assert not bad, f"missing markers: {bad}"


def test_contract_readme_tracks_fc_status():
    readme = Path(r"E:\DocMindY\docs\specs\功能契约\README.md").read_text(encoding="utf-8")
    for fc in ("FC-01a", "FC-01b", "FC-03", "FC-04", "FC-06", "FC-07", "FC-08"):
        assert fc in readme, fc
    # worktree 已实现项应标注已接
    assert "worktree 已接" in readme


def test_mcp_handler_count_at_least_20():
    mcp = (ROOT / "src/doc2mind/server/mcp.py").read_text(encoding="utf-8")
    # handlers 表中的工具名
    import re
    names = re.findall(r'^\s+"([a-z_]+)": _tool_', mcp, re.M)
    assert len(set(names)) >= 20, sorted(set(names))


def test_no_agent_default_on_in_settings():
    app_settings = (ROOT / "DocMind/AppSettings.cs").read_text(encoding="utf-8")
    assert "AgentModeEnabled" in app_settings
    assert "false" in app_settings.split("AgentModeEnabled", 1)[1][:80]
