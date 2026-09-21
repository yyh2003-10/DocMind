"""/v1/config 暴露搜索插件与门控字段（T3/T5/T6/T8）。"""

from __future__ import annotations

from doc2mind.core.config import Settings


def test_config_defaults_expose_new_fields():
    s = Settings()
    assert s.search_provider == "builtin"
    assert s.citation_min_score == 0.45
    assert s.agent_mode_enabled is False
    assert s.agent_native_tool_calling is True
    assert s.background_hit_limit == 5
    assert s.stage_elapsed_enabled is True


def test_http_config_response_fields_exist():
    import inspect

    from doc2mind.server import http as http_mod

    src = inspect.getsource(http_mod.ConfigResponse)
    for key in (
        "search_provider",
        "search_provider_api_key_configured",
        "citation_min_score",
        "background_hit_limit",
        "stage_elapsed_enabled",
        "agent_native_tool_calling",
        "agent_mode_enabled",
    ):
        assert key in src, key
    src2 = inspect.getsource(http_mod.ConfigUpdate)
    for key in (
        "search_provider",
        "search_provider_api_key",
        "citation_min_score",
        "background_hit_limit",
        "stage_elapsed_enabled",
        "agent_mode_enabled",
        "agent_native_tool_calling",
    ):
        assert key in src2, key


def test_citation_min_sanitize_via_settings():
    import logging
    from doc2mind.core.config import Settings

    s = Settings(citation_min_score=-1)
    s._sanitize_citation_gate()
    assert s.citation_min_score == 0.0

    s2 = Settings(citation_min_score="abc")  # type: ignore[arg-type]
    s2._sanitize_citation_gate()
    assert s2.citation_min_score == 0.0

    s3 = Settings(citation_min_score=1.5)
    s3._sanitize_citation_gate()
    assert s3.citation_min_score == 1.0
