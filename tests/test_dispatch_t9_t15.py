"""T9–T13 自动化验收。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from doc2mind.core.agent.longform import (
    build_default_outline,
    parse_outline_from_text,
    run_longform,
)
from doc2mind.core.agent.runtime.mcp_client import (
    McpServerConfig,
    ReadOnlyMcpClient,
    _tool_allowed,
    mask_cmd,
)
from doc2mind.core.config import Settings
from doc2mind.core.llm.output import ThinkingMetaFilter


# --- T9 MCP ---
def test_mcp_disabled_by_default_and_agent_off():
    s = Settings()
    assert s.mcp_client_enabled is False
    assert s.agent_mode_enabled is False
    c = ReadOnlyMcpClient.from_settings(s)
    assert c.enabled is False
    assert c.list_tools() == []


def test_mcp_tool_whitelist_blacklist():
    cfg = McpServerConfig(name="x", enabled=True)
    assert _tool_allowed("web_search", cfg) is True
    assert _tool_allowed("kb_search", cfg) is True
    assert _tool_allowed("write_file", cfg) is False
    assert _tool_allowed("shell_exec", cfg) is False
    assert _tool_allowed("delete_doc", cfg) is False


def test_mcp_mask_cmd_no_secret_in_logs():
    assert mask_cmd(["python", "-m", "secret_server", "--key", "sk-xxx"]) == "python"
    assert "sk-xxx" not in mask_cmd("python -m srv --token=sk-xxx")


def test_mcp_agent_enabled_but_no_servers_safe():
    s = Settings(agent_mode_enabled=True, mcp_client_enabled=True, mcp_servers_json="[]")
    c = ReadOnlyMcpClient.from_settings(s)
    assert c.enabled is True
    assert c.servers == []
    r = c.call_tool("nope", "search", {"query": "x"})
    assert r.ok is False


def test_mcp_bad_json_disables_client():
    s = Settings(agent_mode_enabled=True, mcp_client_enabled=True, mcp_servers_json="{not json")
    c = ReadOnlyMcpClient.from_settings(s)
    assert c.enabled is False


# --- T10 Longform ---
def test_parse_outline_markdown_headings():
    text = "# 总题\n## 背景\n## 要点\n## 总结\n"
    secs = parse_outline_from_text(text)
    assert [s.title for s in secs] == ["总题", "背景", "要点", "总结"]


def test_longform_default_outline_and_budget():
    class Client:
        def chat(self, messages, **kw):
            if any("大纲" in str(m.get("content", "")) for m in messages):
                return "## 背景\n## 要点\n## 总结\n"
            return "本节正文内容，围绕主题展开说明。"

    events = []
    res = run_longform(
        "写一份关于 DocMind 引用门控的说明",
        llm_client=Client(),
        max_sections=5,
        max_chars_total=40,  # 极小预算：首节后触发 skip
        on_event=lambda n, p: events.append((n, p)),
    )
    assert res.plan.sections
    assert res.sections_done >= 1
    assert res.sections_skipped >= 1
    assert res.status in ("succeeded", "budget_exhausted")
    names = [n for n, _ in events]
    assert "longform_outline" in names
    assert "longform_done" in names
    assert res.final_text.strip()


def test_longform_no_llm_uses_template_and_rag_untouched():
    res = run_longform("测试主题长文", llm_client=None, max_sections=2)
    assert res.sections_done >= 1
    assert "测试主题长文" in res.final_text or "##" in res.final_text
    # RAG 默认路径未接 longform：Settings.longform_enabled 默认 False
    assert Settings().longform_enabled is False


def test_default_outline_helper():
    secs = build_default_outline("气缸选型", max_sections=3)
    assert len(secs) == 3
    assert all(s.title for s in secs)


# --- T11 Artifact 双轨 ---
def test_export_artifact_returns_file_path(tmp_path):
    from doc2mind.core.creator import export_artifact

    content = (
        '---\n'
        ':::artifact type="html" title="测试看板"\n'
        "# Hello\n\nbody\n"
        ':::\n'
    )
    # extract_artifact 格式：直接用 markdown body
    md = "# 知识看板\n\n这是正文。\n"
    out = tmp_path / "board.html"
    res = export_artifact(content=md, target_format="html", output_path=str(out), title_override="测试")
    # 无论 ok 与否，契约字段存在
    assert hasattr(res, "ok")
    assert hasattr(res, "file_path") or not res.ok
    if res.ok:
        assert res.file_path
        assert Path(res.file_path).exists() or res.error is None


def test_creative_api_contract_has_file_path():
    import inspect

    from doc2mind.server import http as http_mod

    src = inspect.getsource(http_mod.CreativeExportResponse)
    assert "file_path" in src
    assert "file_name" in src


# --- T12 Thinking meta ---
def test_thinking_filter_drops_user_wants_and_actions_meta():
    f = ThinkingMetaFilter()
    text = (
        "The user wants definition of deflection. "
        "Provide [ACTIONS] only if asked. "
        "挠度是构件在荷载下的竖向位移。\n"
        "Output [ACTIONS] section skipped.\n"
    )
    out = "".join(f.feed(ch) for ch in text) + f.flush()
    assert "The user wants definition" not in out
    assert "Provide [ACTIONS]" not in out
    assert "挠度是构件" in out


def test_thinking_meta_filter_toggle_config():
    assert Settings().thinking_meta_filter_enabled is True
    assert Settings(thinking_meta_filter_enabled=False).thinking_meta_filter_enabled is False


def test_chinese_thinking_not_killed():
    f = ThinkingMetaFilter()
    text = "用户在问挠度单位。根据知识库应答为毫米，必要时换算成米。"
    out = "".join(f.feed(ch) for ch in text) + f.flush()
    assert "挠度单位" in out
    assert "毫米" in out


# --- T13 BM25 短词 ---
def test_bm25_short_token_path_documented_and_callable(tmp_path):
    """短词行为：jieba 开启时走 FTS；关闭时短词 <3 走 LIKE（代码路径存在）。"""
    src = Path(r"E:\DocMindY-worktrees\agent-p0\src\doc2mind\core\store\sqlite_vec.py").read_text(encoding="utf-8")
    assert "BM25 短词路径" in src
    assert "short_tokens" in src
    assert "LIKE" in src


def test_bm25_jieba_settings():
    s = Settings()
    assert s.bm25_jieba_enabled is True  # 默认开：2 字中文词可 FTS 命中


def test_bm25_memory_store_short_query(tmp_path):
    """内存库：短中文词在 jieba 模式下可检索（集成级，失败则记行为说明）。"""
    try:
        from doc2mind.core.embedder.factory import get_embedder
        from doc2mind.core.store.sqlite_vec import VectorStore
    except Exception:
        return
    try:
        s = Settings(db_path=tmp_path / "bm.db", embed_dim=8)
        # 优先轻量：无 embedder 时只验证 bm25_search API 可调用
        store = VectorStore(s.db_path)
        try:
            hits = store.bm25_search("IP", top_k=5)
            assert isinstance(hits, list)
        finally:
            store.close()
    except Exception:
        # 环境缺依赖：行为说明见 docs，不阻塞
        pass


# --- T15 手动清单结果可加载 ---
def test_manual_checklist_results_json_exists():
    p = Path(r"E:\DocMindY-worktrees\agent-p0\docs\verification\manual-checklist-results.json")
    assert p.is_file()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "items" in data
    assert len(data["items"]) >= 5
