"""主仓 E:\\DocMindY 合并后新功能适配检验。"""

from __future__ import annotations

import inspect
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(r"E:\DocMindY")
sys.path.insert(0, str(ROOT / "src"))

results: list[tuple[str, bool, str]] = []


def ok(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), name, detail)


def main() -> int:
    # 1. imports
    try:
        from doc2mind.core import config, rag
        from doc2mind.core.agent.longform import parse_outline_from_text, run_longform
        from doc2mind.core.agent.prompt_policy import done_frame_extras
        from doc2mind.core.agent.runtime.mcp_client import ReadOnlyMcpClient
        from doc2mind.core.llm.base import ChatToolTurn
        from doc2mind.core.llm.output import ThinkingMetaFilter
        from doc2mind.core.llm.openai_impl import OpenAIClient
        from doc2mind.core.search.provider import BuiltinSearchProvider, resolve_search_provider
        from doc2mind.server import http as http_mod

        ok("import.all", True)
    except Exception as e:
        ok("import.all", False, str(e))
        return 1

    s = config.Settings()
    field_checks = [
        ("config.citation_min_score", s.citation_min_score == 0.45),
        ("config.citation_bg_ratio", s.citation_bg_score_ratio == 0.6),
        ("config.background_hit_limit", s.background_hit_limit == 5),
        ("config.stage_elapsed_enabled", s.stage_elapsed_enabled is True),
        ("config.llm_first_token_slow_ms", s.llm_first_token_slow_ms == 30000),
        ("config.search_provider", s.search_provider == "builtin"),
        ("config.mcp_client_enabled", s.mcp_client_enabled is False),
        ("config.longform_enabled", s.longform_enabled is False),
        ("config.agent_mode_enabled", s.agent_mode_enabled is False),
        ("config.agent_native_tool_calling", s.agent_native_tool_calling is True),
        ("config.thinking_meta_filter_enabled", s.thinking_meta_filter_enabled is True),
        ("config.longform_max_sections", s.longform_max_sections == 8),
    ]
    for n, c in field_checks:
        ok(n, c)

    ok("rag.partition_citation_hits", hasattr(rag, "partition_citation_hits"))
    ok("rag._hit_supports_query", hasattr(rag, "_hit_supports_query"))
    ok("rag._stage_fields_from_timing", hasattr(rag, "_stage_fields_from_timing"))
    rag_src = (ROOT / "src/doc2mind/core/rag.py").read_text(encoding="utf-8", errors="replace")
    for key in [
        "stage_retrieval_ms",
        "stage_first_token_ms",
        "已等待",
        "citation_gate",
        "longform_outline",
        "thinking_meta_filter_enabled",
        "无可用库内依据",
        "search_provider",
        "partition_citation_hits",
    ]:
        ok(f"rag.src[{key}]", key in rag_src)

    cfg_src = inspect.getsource(http_mod.ConfigResponse)
    upd_src = inspect.getsource(http_mod.ConfigUpdate)
    for k in [
        "search_provider",
        "citation_min_score",
        "mcp_client_enabled",
        "longform_enabled",
        "agent_mode_enabled",
        "agent_native_tool_calling",
        "thinking_meta_filter_enabled",
        "background_hit_limit",
        "stage_elapsed_enabled",
        "llm_first_token_slow_ms",
    ]:
        ok(f"http.ConfigResponse.{k}", k in cfg_src)
    for k in ["search_provider", "citation_min_score", "agent_mode_enabled", "mcp_client_enabled"]:
        ok(f"http.ConfigUpdate.{k}", k in upd_src)
    # keys not echoed as plaintext
    resp_body = cfg_src.split("class ConfigResponse")[1].split("class ")[0]
    ok(
        "http.api_key_masked",
        "search_provider_api_key_configured" in resp_body
        and "search_provider_api_key: str" not in resp_body,
    )

    @dataclass
    class C:
        content: str = ""
        heading: str | None = None
        source: str = ""
        page: int | None = None
        format: str = "md"
        id: int = 1

    @dataclass
    class H:
        chunk: object
        rerank_score: float | None = 0.9
        vector_score: float = 0.0
        bm25_score: float = 0.0
        score: float = 0.9
        source: str = ""

    q = "什么是挠度"
    good = H(C("挠度是指构件位移", source="mech.md"), rerank_score=0.9)
    off = H(C("DocMind 导入步骤", source="guide.md"), rerank_score=0.95)
    weak = H(C("菜单说明", source="ui.md"), rerank_score=0.05)
    part = rag.partition_citation_hits(
        q, [good, off, weak], citation_min_score=0.45, reranked_usable=True
    )
    ok("gate.cite_good", good in part.cite_hits)
    ok("gate.off_to_bg", off in part.bg_hits and off not in part.cite_hits)
    ok("gate.weak_discard", weak in part.discarded_hits)
    ok("gate.cross_check", part.gate.get("cross_check") is True)
    ok(
        "gate.dropped_fields",
        "dropped_by_score" in part.gate and "dropped_by_topic" in part.gate,
    )

    ok(
        "provider.builtin_default",
        isinstance(resolve_search_provider("builtin", None), BuiltinSearchProvider),
    )
    ok(
        "provider.nokey_fallback",
        isinstance(resolve_search_provider("tavily", None), BuiltinSearchProvider),
    )
    ok("mcp.disabled_by_default", ReadOnlyMcpClient.from_settings(s).enabled is False)
    ok("base.ChatToolTurn.wants_tools", hasattr(ChatToolTurn, "wants_tools"))
    ok("openai.chat_with_tools", hasattr(OpenAIClient, "chat_with_tools"))
    ok("openai.supports_tool_calling", hasattr(OpenAIClient, "supports_tool_calling"))
    ok("prompt_policy.done_frame_extras", callable(done_frame_extras))

    f = ThinkingMetaFilter()
    out = "".join(f.feed(c) for c in "The user wants definition. 挠度是构件位移。") + f.flush()
    ok("thinking.drop_english_meta", "The user wants" not in out)
    ok("thinking.keep_chinese", "挠度是构件位移" in out)

    events: list[str] = []
    res = run_longform(
        "主仓适配长文测试",
        llm_client=None,
        max_sections=2,
        on_event=lambda n, p: events.append(n),
    )
    ok("longform.outline_event", "longform_outline" in events)
    ok("longform.produces_text", bool(res.final_text.strip()))
    ok("longform.parse_outline", len(parse_outline_from_text("## A\n## B")) == 2)

    for p in [
        "src/doc2mind/core/agent/longform.py",
        "src/doc2mind/core/agent/runtime/mcp_client.py",
        "src/doc2mind/core/search/provider.py",
        "tests/test_dispatch_t0_t8_acceptance.py",
        "tests/test_dispatch_t9_t15.py",
        "tests/test_commercial_gates.py",
        "tests/test_business_matrix.py",
        "docs/api.md",
        "docs/agents/dispatch/MERGE-STRATEGY-T0.md",
        "scripts/selftest_t0_t15.py",
        "tools/calibrate_citation_threshold.py",
        "index.html",
        "docs/verification/manual-checklist-results.json",
    ]:
        ok(f"file.{p}", (ROOT / p).is_file())

    for path, sym in [
        ("DocMind/AppSettings.cs", "AgentModeEnabled"),
        ("DocMind/ViewModels/SettingsViewModel.cs", "AgentModeEnabled"),
        ("DocMind/Views/SettingsView.xaml", "AgentModeEnabled"),
        ("DocMind/Services/IDoc2kbApiService.cs", "onAgentEvent"),
        ("DocMind/Services/Doc2kbApiService.cs", "onAgentEvent"),
        ("DocMind/ViewModels/ChatViewModel.cs", "onAgentEvent"),
    ]:
        fp = ROOT / path
        txt = fp.read_text(encoding="utf-8", errors="replace") if fp.is_file() else ""
        ok(f"wpf.{path}:{sym}", sym in txt)

    # docs/api.md mentions new fields
    api = (ROOT / "docs/api.md").read_text(encoding="utf-8", errors="replace")
    for k in ["stage_retrieval_ms", "citation_gate", "search_provider", "llm_first_token_slow_ms"]:
        ok(f"api.md[{k}]", k in api)

    passed = sum(1 for _, c, _ in results if c)
    failed = sum(1 for _, c, _ in results if not c)
    print("=" * 40)
    print(f"主仓新功能适配: PASS={passed} FAIL={failed} TOTAL={len(results)}")
    if failed:
        print("失败项:")
        for n, c, d in results:
            if not c:
                print(f"  - {n}: {d}")
    report = ROOT / "docs/verification/main-adaptation-check.json"
    import json
    import time

    report.write_text(
        json.dumps(
            {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "repo": str(ROOT),
                "pass": passed,
                "fail": failed,
                "items": [{"name": n, "ok": c, "detail": d} for n, c, d in results],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"报告: {report}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
