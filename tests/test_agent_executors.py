"""P1 执行器接线单测（全部注入 stub，不依赖真实 DB/LM）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from doc2mind.core.agent.runtime import (
    MockModelTurn,
    PermissionGate,
    ToolCall,
    ToolRegistry,
    ToolStatus,
    Workspace,
    bind_runtime_executors,
    builtin_tool_specs,
    make_web_search_executor,
    run_mock_loop,
)
from doc2mind.core.agent.runtime.executors import make_inspect_executor


@dataclass
class FakeChunk:
    content: str
    source: str
    heading: str | None = None


@dataclass
class FakeHit:
    chunk: FakeChunk
    score: float = 0.9


@dataclass
class FakeExportResult:
    ok: bool
    artifact_type: str
    file_path: str
    file_name: str
    error: str | None = None


def _search_fn(query, collection=None, top_k=5):
    return [
        FakeHit(FakeChunk(content=f"关于 {query} 的要点", source="kb/a.md"), 0.88),
        FakeHit(FakeChunk(content="补充说明", source="kb/b.md"), 0.7),
    ], None


def test_workspace_executors_roundtrip(tmp_path):
    ws = Workspace(tmp_path / "ws")
    reg = ToolRegistry(builtin_tool_specs())
    bind_runtime_executors(reg, workspace=ws)

    w = reg.execute(
        ToolCall(call_id="w1", tool_id="write_workspace_file", arguments={"path": "notes/a.md", "content": "# hi"})
    )
    assert w.status == ToolStatus.OK
    assert (tmp_path / "ws" / "notes" / "a.md").exists()

    r = reg.execute(ToolCall(call_id="r1", tool_id="read_workspace_file", arguments={"path": "notes/a.md"}))
    assert r.status == ToolStatus.OK
    assert r.data["text"] == "# hi"

    ls = reg.execute(ToolCall(call_id="l1", tool_id="list_workspace", arguments={"sub": "notes"}))
    assert ls.status == ToolStatus.OK
    assert any("notes/a.md" in str(x) or x.endswith("a.md") for x in ls.data["files"])


def test_workspace_write_escape_denied(tmp_path):
    ws = Workspace(tmp_path / "ws")
    reg = ToolRegistry(builtin_tool_specs())
    bind_runtime_executors(reg, workspace=ws)
    res = reg.execute(
        ToolCall(call_id="w2", tool_id="write_workspace_file", arguments={"path": "../evil.md", "content": "x"})
    )
    assert res.status == ToolStatus.DENIED


def test_kb_search_executor_with_stub(tmp_path):
    ws = Workspace(tmp_path / "ws")
    reg = ToolRegistry(builtin_tool_specs())
    bind_runtime_executors(reg, workspace=ws, search_fn=_search_fn)
    res = reg.execute(ToolCall(call_id="k1", tool_id="kb_search", arguments={"query": "动平衡", "top_k": 2}))
    assert res.status == ToolStatus.OK
    assert res.data["count"] == 2
    assert "动平衡" in res.summary or "kb/a.md" in res.summary


def test_export_executor_with_stub(tmp_path):
    ws = Workspace(tmp_path / "ws")
    reg = ToolRegistry(builtin_tool_specs())

    def fake_export(content, target_format=None, output_path=None, title_override=None, theme=None, **kwargs):
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return FakeExportResult(
            ok=True,
            artifact_type=target_format or "docx",
            file_path=str(p),
            file_name=p.name,
        )

    bind_runtime_executors(reg, workspace=ws, export_fn=fake_export)
    res = reg.execute(
        ToolCall(
            call_id="e1",
            tool_id="export_artifact",
            arguments={"content": "报告正文", "format": "md", "title": "测试"},
        )
    )
    assert res.status == ToolStatus.OK
    rel = res.data["relative_path"]
    assert (tmp_path / "ws" / rel).exists() or Path(res.data["file_path"]).exists()


def test_inspect_executor_uses_default_inspector(monkeypatch):
    @dataclass
    class Report:
        score: int = 92
        grade: str = "A"
        summary: str = "ok"
        slide_count: int = 3

    monkeypatch.setattr(
        "doc2mind.core.creator.inspect_presentation",
        lambda content: Report(),
    )
    res = make_inspect_executor()(ToolCall(call_id="i1", tool_id="inspect_artifact", arguments={"content": "# x"}))
    assert res.status == ToolStatus.OK
    assert res.data["score"] == 92


@dataclass
class FakeWebHit:
    title: str
    url: str
    snippet: str = ""
    content: str = ""
    domain: str = "example.com"
    source_name: str = "Web"
    relevance_score: float = 0.7
    evidence_level: str = "单一来源"
    content_fetched: bool = True
    published_at: str | None = None
    corroborated_by: int = 0


def _web_search_fn(query, mode="normal", max_results=8, github_token=None):
    return [
        FakeWebHit(
            title=f"{query} 定义与工程意义",
            url="https://example.com/def",
            content=f"关于 {query} 的网页正文摘录",
            content_fetched=True,
        ),
        FakeWebHit(
            title=f"{query} 规范限值",
            url="https://example.com/spec",
            content="仅标题与摘要",
            content_fetched=False,
        ),
    ][:max_results]


def test_web_search_executor_binds_and_returns_results(tmp_path):
    reg = ToolRegistry(builtin_tool_specs())
    bind_runtime_executors(
        reg,
        workspace=Workspace(tmp_path / "ws"),
        web_search_fn=_web_search_fn,
        web_search_mode="deep",
    )
    res = reg.execute(
        ToolCall(
            call_id="wsearch1",
            tool_id="web_search",
            arguments={"query": "挠度", "mode": "deep"},
        )
    )
    assert res.status == ToolStatus.OK
    assert res.data["count"] >= 1
    assert res.data["mode"] == "deep"
    assert res.data["results"][0]["url"].startswith("https://")
    assert "挠度" in res.summary


def test_web_search_executor_empty_query(tmp_path):
    executor = make_web_search_executor(_web_search_fn)
    res = executor(ToolCall(call_id="wsearch2", tool_id="web_search", arguments={"query": "  "}))
    assert res.status == ToolStatus.ERROR


def test_loop_with_web_search_tool(tmp_path):
    reg = ToolRegistry(builtin_tool_specs())
    bind_runtime_executors(
        reg,
        workspace=Workspace(tmp_path / "ws"),
        search_fn=_search_fn,
        web_search_fn=_web_search_fn,
        web_search_mode="deep",
    )
    turns = [
        MockModelTurn(
            tool_calls=[
                ToolCall(call_id="c1", tool_id="kb_search", arguments={"query": "挠度"}),
                ToolCall(call_id="c2", tool_id="web_search", arguments={"query": "挠度", "mode": "deep"}),
            ]
        ),
        MockModelTurn(
            tool_calls=[
                ToolCall(call_id="c3", tool_id="web_search", arguments={"query": "deflection 定义", "mode": "normal"}),
            ]
        ),
        MockModelTurn(final_text="基于库内与联网多轮检索作答"),
    ]
    result = run_mock_loop(
        turns,
        reg,
        gate=PermissionGate(write_policy="always_allow_workspace"),
        auto_approve_session=True,
    )
    assert result.status == "succeeded"
    web_results = [r for r in result.tool_results if r.tool_id == "web_search"]
    assert len(web_results) == 2
    assert all(r.status == ToolStatus.OK for r in web_results)


def test_loop_with_bound_executors(tmp_path):
    ws = Workspace(tmp_path / "ws")
    reg = ToolRegistry(builtin_tool_specs())
    bind_runtime_executors(reg, workspace=ws, search_fn=_search_fn)

    turns = [
        MockModelTurn(tool_calls=[ToolCall(call_id="c1", tool_id="kb_search", arguments={"query": "gpt"})]),
        MockModelTurn(
            tool_calls=[
                ToolCall(
                    call_id="c2",
                    tool_id="write_workspace_file",
                    arguments={"path": "notes/out.md", "content": "结论"},
                )
            ]
        ),
        MockModelTurn(final_text="已完成检索并写入笔记"),
    ]
    result = run_mock_loop(turns, reg, gate=PermissionGate(write_policy="always_allow_workspace"), auto_approve_session=True)
    assert result.status == "succeeded"
    assert result.final_text == "已完成检索并写入笔记"
    assert any(r.tool_id == "kb_search" and r.status == ToolStatus.OK for r in result.tool_results)
    assert any(r.tool_id == "write_workspace_file" and r.status == ToolStatus.OK for r in result.tool_results)
    assert (tmp_path / "ws" / "notes" / "out.md").exists()
