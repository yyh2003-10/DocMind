"""契约 P0：LLM 前置门禁与离线横幅联动的逻辑单测（C# 不可编译时用 Python 锁语义）。"""

from __future__ import annotations


def test_llm_gate_rules():
    # 与 GraphViewModel.IsLlmConfigured / QualityViewModel 同源规则
    def is_llm_configured(provider, api_key, profiles_keys):
        if provider is None:
            return True  # 未知不阻断
        p = (provider or "").strip()
        if not p or p.lower() == "none":
            return False
        if p.lower() == "ollama":
            return True
        if api_key:
            return True
        return any(profiles_keys)

    assert is_llm_configured("none", None, []) is False
    assert is_llm_configured("", "sk-x", []) is False
    assert is_llm_configured("openai", "sk-x", []) is True
    assert is_llm_configured("openai", None, []) is False
    assert is_llm_configured("openai", None, ["sk-profile"]) is True
    assert is_llm_configured("ollama", None, []) is True
    assert is_llm_configured(None, None, []) is True


def test_offline_banner_events_wired():
    # 源码级：MainViewModel 订阅了各页 BackendUnreachable
    from pathlib import Path

    main = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\ViewModels\MainViewModel.cs").read_text(
        encoding="utf-8"
    )
    for vm in (
        "_chatViewModel.BackendUnreachable",
        "_searchViewModel.BackendUnreachable",
        "_importViewModel.BackendUnreachable",
        "_graphViewModel.BackendUnreachable",
        "_qualityViewModel.BackendUnreachable",
        "_documentsViewModel.BackendUnreachable",
        "_convertViewModel.BackendUnreachable",
    ):
        assert vm in main, vm
    assert "OnPageBackendUnreachable" in main
    assert "UpdateBackendState(BackendState.Offline)" in main
    assert "_graphViewModel.NavigateToSettingsRequested" in main
    assert "_qualityViewModel.NavigateToSettingsRequested" in main

    graph = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\ViewModels\GraphViewModel.cs").read_text(
        encoding="utf-8"
    )
    assert "CanExtractGraph" in graph
    assert "IsLlmConfigured" in graph
    assert "BackendUnreachable?.Invoke()" in graph

    quality = Path(r"E:\DocMindY-worktrees\agent-p0\DocMind\ViewModels\QualityViewModel.cs").read_text(
        encoding="utf-8"
    )
    assert "IsLlmConfigured" in quality
    assert "BackendUnreachable?.Invoke()" in quality
    assert "NotifyLlmGateChanged" in quality
