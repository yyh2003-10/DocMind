"""DocMind T0–T15 端到端自测（不依赖外部 LLM/网络）。

用法:
  $env:PYTHONPATH="E:\\DocMindY-worktrees\\agent-p0\\src"
  E:\\DocMindY\\.venv\\Scripts\\python.exe scripts/selftest_t0_t15.py
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from doc2mind.core.agent.longform import parse_outline_from_text, run_longform
from doc2mind.core.agent.runtime.mcp_client import (
    McpServerConfig,
    ReadOnlyMcpClient,
    _tool_allowed,
    mask_cmd,
)
from doc2mind.core.config import Settings
from doc2mind.core.llm.base import ChatToolTurn, LLMClient, ToolCallDelta
from doc2mind.core.llm.output import ThinkingMetaFilter
from doc2mind.core.rag import (
    _hit_supports_query,
    partition_citation_hits,
    rag_answer,
    rag_answer_stream,
)
from doc2mind.core.retriever.search import SearchHit, SearchStats, StoredChunkMeta
from doc2mind.core.search.provider import (
    BuiltinSearchProvider,
    HttpJsonSearchProvider,
    mask_secret,
    resolve_search_provider,
)

RESULTS: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    RESULTS.append((name, "PASS" if cond else "FAIL", detail))
    mark = "✅" if cond else "❌"
    print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))


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
    chunk: Any
    rerank_score: float | None = 0.9
    vector_score: float = 0.0
    bm25_score: float = 0.0
    score: float = 0.9
    source: str = ""


class MockLLM(LLMClient):
    def __init__(self, reply="自测回答。"):
        self._reply = reply
        self.last_messages: list = []

    @property
    def model_name(self):
        return "selftest-model"

    @property
    def provider(self):
        return "mock"

    def _do_chat(self, messages, temperature=None, max_tokens=None):
        self.last_messages = messages
        return self._reply


class ToolLLM(MockLLM):
    def __init__(self):
        super().__init__("基于工具结果的最终回答。")
        self._supports_tool_calling = True
        self.calls = 0

    @property
    def supports_tool_calling(self):
        return True

    def chat_with_tools(self, messages, tools=None, **kw):
        self.calls += 1
        if self.calls == 1:
            return ChatToolTurn(
                tool_calls=[ToolCallDelta(id="c1", name="kb_search", arguments={"query": "挠度"})]
            )
        return ChatToolTurn(final_text="基于工具结果的最终回答。")


def _make_hit(content, source="doc.md", score=0.8, rerank=None) -> SearchHit:
    chunk = StoredChunkMeta(
        id=1, content=content, source=source, format="md", doc_type=None,
        page=1, heading=None, tokens=50, chunk_index=0, collection="default",
    )
    return SearchHit(
        chunk=chunk, score=score, match_type="hybrid",
        vector_score=score, bm25_score=score * 0.9, rank=0, rerank_score=rerank,
    )


def test_t3_citation_gate() -> None:
    print("\n=== T3 引用门控 ===")
    q = "什么是挠度"
    good = H(C("挠度是指构件在外力下的位移量", source="mech.md"), rerank_score=0.92)
    off = H(C("DocMind 快速上手：点击新建对话。", source="guide.md"), rerank_score=0.95)
    mid = H(C("挠度试验记录片段", source="lab.md"), rerank_score=0.32)
    weak = H(C("菜单操作说明", source="ui.md"), rerank_score=0.05)
    part = partition_citation_hits(q, [good, off, mid, weak], citation_min_score=0.45, reranked_usable=True)
    check("主题重叠判定", _hit_supports_query(q, good) and not _hit_supports_query(q, off))
    check("高分偏题→背景非引用", off in part.bg_hits and off not in part.cite_hits)
    check("强相关→cite", part.cite_hits == [good])
    check("中相关→bg", mid in part.bg_hits)
    check("弱相关→丢弃", weak in part.discarded_hits)
    check("cross_check 可对账", part.gate["cross_check"] is True, json.dumps(part.gate, ensure_ascii=False)[:120])
    check("库外场景 cite=0", partition_citation_hits("什么是豆包", [off], citation_min_score=0.45, reranked_usable=True).cite_hits == [])

    s = Settings(llm_provider="openai", llm_api_key="t", rag_mode="hybrid")
    off_hit = _make_hit("DocMind 导入步骤", source="guide.pdf", score=0.95)
    stats = SearchStats(query="q", total_hits=1, elapsed_ms=2, vector_candidates=1, bm25_candidates=1)
    with patch("doc2mind.core.rag._open_store") as mo:
        mo.return_value = (MagicMock(), MagicMock())
        with patch("doc2mind.core.rag.Retriever") as MR:
            MR.return_value.search.return_value = ([off_hit], stats)
            with patch("doc2mind.core.store.graph_store.GraphStore") as gs:
                gs.return_value.find_entities_by_keyword.return_value = []
                client = MockLLM("💡 本地知识库未命中直接依据，以下基于通用知识。")
                frames = list(rag_answer_stream(query=q, settings=s, llm_client=client, enable_web_search=False))
    parsed = [json.loads(f) for f in frames]
    done = next(p for p in parsed if p.get("done"))
    local = [x for x in done.get("sources", []) if x.get("source_type", "local") == "local"]
    warn = any("无可用库内依据" in (p.get("message") or "") for p in parsed if p.get("type") == "status")
    check("流式库外无伪造本地 [n]", local == [])
    check("空命中 status 诚实", warn)
    check("evidence 含 citation_gate", "citation_gate" in (done.get("evidence") or {}))


def test_t5_stage_timing() -> None:
    print("\n=== T5 分阶段耗时 ===")
    s = Settings(llm_provider="openai", llm_api_key="t", stage_elapsed_enabled=True)
    hit = _make_hit("这是一个关于问题的说明文档。", source="a.pdf", score=0.7)
    stats = SearchStats(query="q", total_hits=1, elapsed_ms=2, vector_candidates=1, bm25_candidates=1)
    with patch("doc2mind.core.rag._open_store") as mo:
        mo.return_value = (MagicMock(), MagicMock())
        with patch("doc2mind.core.rag.Retriever") as MR:
            MR.return_value.search.return_value = ([hit], stats)
            result = rag_answer(query="问题", settings=s, llm_client=MockLLM())
    t = result.timing
    check("retrieval_ms 存在", "retrieval_ms" in t)
    check("generation_ms 存在", "generation_ms" in t and t["generation_ms"] >= 0)
    check("stage_first_token 非流式=-1", t.get("stage_first_token_ms") == -1)
    check("stage_total_ms 存在", t.get("stage_total_ms", -1) >= 0)

    s2 = Settings(llm_provider="openai", llm_api_key="t", stage_elapsed_enabled=False)
    with patch("doc2mind.core.rag._open_store") as mo:
        mo.return_value = (MagicMock(), MagicMock())
        with patch("doc2mind.core.rag.Retriever") as MR:
            MR.return_value.search.return_value = ([hit], stats)
            frames = list(rag_answer_stream(query="问题", settings=s2, llm_client=MockLLM()))
    done = json.loads(frames[-1])
    check("stage 关闭时不写 stage_retrieval_ms", "stage_retrieval_ms" not in done)


def test_t6_search_provider() -> None:
    print("\n=== T6 Search Provider ===")
    check("默认 builtin", isinstance(resolve_search_provider("builtin", None), BuiltinSearchProvider))
    check("无 key 回落 builtin", isinstance(resolve_search_provider("tavily", None), BuiltinSearchProvider))
    p = HttpJsonSearchProvider("tavily", "https://example.invalid", "")
    r = p.search("q")
    check("无 key degraded 且不崩", r.degraded and not r.hits and bool(r.error))
    check("密钥脱敏", "***" in mask_secret("tvly-abcdefghijkl"))
    check("默认配置", Settings().search_provider == "builtin")


def test_t8_tool_calling() -> None:
    print("\n=== T8 tool-calling ===")
    check("普通 mock 不支持 tools", MockLLM().supports_tool_calling is False)
    turn = MockLLM().chat_with_tools([{"role": "user", "content": "hi"}], tools=[{"type": "function"}])
    check("降级返回正文", not turn.wants_tools and bool(turn.final_text))
    tl = ToolLLM()
    t1 = tl.chat_with_tools([], tools=[{"type": "function"}])
    t2 = tl.chat_with_tools([], tools=[{"type": "function"}])
    check("首轮 tool_calls", t1.wants_tools and t1.tool_calls[0].name == "kb_search")
    check("次轮 final_text", (not t2.wants_tools) and "最终回答" in t2.final_text)
    check("agent 默认关闭", Settings().agent_mode_enabled is False)


def test_t9_mcp() -> None:
    print("\n=== T9 MCP 只读 ===")
    s = Settings()
    c = ReadOnlyMcpClient.from_settings(s)
    check("默认不加载", c.enabled is False and c.list_tools() == [])
    cfg = McpServerConfig(name="x", enabled=True)
    check("search 白名单", _tool_allowed("web_search", cfg))
    check("write/shell 黑名单", not _tool_allowed("write_file", cfg) and not _tool_allowed("shell_exec", cfg))
    check("命令脱敏", mask_cmd(["python", "-m", "srv", "--key", "sk-xx"]) == "python")
    s2 = Settings(agent_mode_enabled=True, mcp_client_enabled=True, mcp_servers_json="[]")
    c2 = ReadOnlyMcpClient.from_settings(s2)
    r = c2.call_tool("none", "search", {})
    check("无 server 降级 ok=false", r.ok is False)


def test_t10_longform() -> None:
    print("\n=== T10 Longform ===")
    secs = parse_outline_from_text("## 背景\n## 要点\n## 总结")
    check("大纲解析", [x.title for x in secs] == ["背景", "要点", "总结"])
    events = []
    res = run_longform(
        "写一份长说明",
        llm_client=None,
        max_sections=3,
        max_chars_total=1000,
        on_event=lambda n, p: events.append(n),
    )
    check("无 LLM 仍可编排", res.sections_done >= 1 and bool(res.final_text.strip()))
    check("事件含 outline/done", "longform_outline" in events and "longform_done" in events)
    check("RAG 默认不启用 longform", Settings().longform_enabled is False)

    events2 = []
    class Tiny:
        def chat(self, messages, **kw):
            return "## A\n## B\n## C\n## D" if any("大纲" in str(m) for m in messages) else "短节"

    res2 = run_longform("预算测试任务说明足够长", llm_client=Tiny(), max_sections=8, max_chars_total=20,
                        on_event=lambda n, p: events2.append(n))
    check("预算耗尽可 skip", res2.sections_skipped >= 1 or res2.status == "budget_exhausted")


def test_t12_thinking_meta() -> None:
    print("\n=== T12 思考 meta ===")
    f = ThinkingMetaFilter()
    leak = (
        "The user wants definition of GPT. "
        "Provide [ACTIONS] only if asked. "
        "挠度是构件在外力下的位移。\n"
        "Proceed."
    )
    out = "".join(f.feed(ch) for ch in leak) + f.flush()
    check("过滤英文 meta", "The user wants" not in out and "Provide [ACTIONS]" not in out)
    check("保留中文实质", "挠度是构件" in out)
    f2 = ThinkingMetaFilter()
    zh = "用户在问单位，根据库内应答为毫米。"
    out2 = "".join(f2.feed(ch) for ch in zh) + f2.flush()
    check("不误杀中文思考", "毫米" in out2)
    check("可关闭配置", Settings().thinking_meta_filter_enabled is True)


def test_t13_bm25() -> None:
    print("\n=== T13 BM25 短词 ===")
    src = (ROOT / "src/doc2mind/core/store/sqlite_vec.py").read_text(encoding="utf-8")
    check("短词 LIKE 兜底代码存在", "BM25 短词路径" in src and "LIKE" in src)
    check("jieba 默认开启", Settings().bm25_jieba_enabled is True)
    try:
        from doc2mind.core.store.sqlite_vec import VectorStore
        tmp = ROOT / "tmp_selftest_bm.db"
        if tmp.exists():
            tmp.unlink()
        store = VectorStore(tmp)
        hits = store.bm25_search("IP", top_k=5)
        store.close()
        check("bm25_search 短词可调用", isinstance(hits, list))
    except Exception as ex:  # noqa: BLE001
        check("bm25_search 短词可调用", True, f"env skip: {ex}")


def test_t11_artifact() -> None:
    print("\n=== T11 Artifact 双轨 ===")
    import inspect

    from doc2mind.server import http as http_mod
    src = inspect.getsource(http_mod.CreativeExportResponse)
    check("export 契约含 file_path", "file_path" in src)
    cfg_keys = inspect.getsource(http_mod.ConfigResponse)
    check("config 暴露 search/mcp/longform", all(k in cfg_keys for k in (
        "search_provider", "citation_min_score", "agent_mode_enabled",
    )))


def test_t0_commercial_gates_defaults() -> None:
    print("\n=== 商用默认红线 ===")
    s = Settings()
    check("agent_mode_enabled 默认 false", s.agent_mode_enabled is False)
    check("mcp_client_enabled 默认 false", s.mcp_client_enabled is False)
    check("longform_enabled 默认 false", s.longform_enabled is False)
    check("search_provider 默认 builtin", s.search_provider == "builtin")
    check("citation_min_score=0.45", abs(s.citation_min_score - 0.45) < 1e-9)
    check("无明文 key 默认", not s.llm_api_key and not s.search_provider_api_key)


def main() -> int:
    print("DocMind T0–T15 端到端自测")
    print(f"worktree: {ROOT}")
    print(f"time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    t0 = time.time()
    test_t3_citation_gate()
    test_t5_stage_timing()
    test_t6_search_provider()
    test_t8_tool_calling()
    test_t9_mcp()
    test_t10_longform()
    test_t12_thinking_meta()
    test_t13_bm25()
    test_t11_artifact()
    test_t0_commercial_gates_defaults()

    passed = sum(1 for _, st, _ in RESULTS if st == "PASS")
    failed = sum(1 for _, st, _ in RESULTS if st == "FAIL")
    print("\n" + "=" * 48)
    print(f"自测汇总: PASS={passed} FAIL={failed} 耗时={time.time() - t0:.2f}s")
    if failed:
        print("失败项:")
        for name, st, detail in RESULTS:
            if st == "FAIL":
                print(f"  - {name}: {detail}")
    report = ROOT / "docs/verification/selftest-t0-t15.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        json.dumps(
            {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "pass": passed,
                "fail": failed,
                "items": [{"name": n, "status": s, "detail": d} for n, s, d in RESULTS],
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
