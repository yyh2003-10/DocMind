"""M3-T11 科研子链路挂接测试：rag 主链路的门控、注入、降级路径。

覆盖任务清单 11.x 单测要求：
- intent_research_enabled 关闭（默认）时零差异（不进入科研分支）
- 规划非 research 时不进入分支
- 文献集合判空 → 显式降级状态帧（不静默）
- 集合可判定 → 文献块注入 context、SourceRef 编号续编
- 科研链路异常 → 降级状态帧，主链路照常返回
- 预算裁剪：文献块最后裁剪（design 决策 C-3 步骤 5）
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import doc2mind.core.agent.research as research_mod
import doc2mind.core.rag as rag
from doc2mind.core.config import Settings


# ---------- 测试替身 ----------


class _FakeRetriever:
    """最小 Retriever 替身：主链路检索返回空命中（科研链路单独替换）。"""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    def search(
        self, query: str, collection: str | list[str] | None = "default",
        top_k: int = 10, min_score: float = 0.0,
    ) -> tuple[list[Any], Any]:
        return [], SimpleNamespace(degraded=False, degraded_reason=None)


@pytest.fixture
def fake_store_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """无向量库 / 无重排器的最小运行环境。"""
    monkeypatch.setattr(rag, "Retriever", _FakeRetriever)
    monkeypatch.setattr(rag, "_open_store", lambda s: (None, None))
    monkeypatch.setattr(rag, "get_reranker", lambda s: None)


def _settings(**overrides: Any) -> Settings:
    s = Settings()
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def _plan(query_type: str = "research", research_task: str = "review") -> Any:
    return SimpleNamespace(
        query_type=query_type, research_task=research_task,
        enabled_tools=["knowledge_base"],
    )


def _run(s: Settings, agent_plan: Any, collections: list[str] | None = None) -> tuple[list[str], str, list[Any], list[dict[str, str]]]:
    """驱动 _build_context_and_messages 生成器，收集状态帧与最终结果。"""
    gen = rag._build_context_and_messages(
        query="综述多模态检索的方法",
        collection=None,
        top_k=5,
        s=s,
        collections=collections,
        history=[],
        t0=0.0,
        agent_plan=agent_plan,
    )
    statuses: list[str] = []
    try:
        while True:
            statuses.append(next(gen))
    except StopIteration as exc:
        hits, context, sources, messages = exc.value
    return statuses, context, sources, messages


def _lit_ref(index: int = 1) -> Any:
    """科研文献来源（SourceRef.literature=True）。"""
    return rag.SourceRef(
        index=index, source=r"E:\lit\paper_a.pdf", format="pdf",
        title="多模态检索综述", snippet="文献正文", literature=True,
    )


# ---------- 门控 ----------


def test_research_hook_disabled_by_default(fake_store_env: None) -> None:
    """默认配置（intent_research_enabled=False）下完全零差异。"""
    statuses, context, _sources, _messages = _run(_settings(), _plan())
    assert not any("文献检索" in msg for msg in statuses)
    assert "文献原著切片" not in context
    assert "科研写作准则" not in context


def test_research_hook_skips_non_research_plan(fake_store_env: None) -> None:
    """开关开启但规划判为普通问答 → 不进入科研分支。"""
    statuses, context, _sources, _messages = _run(
        _settings(intent_research_enabled=True), _plan(query_type="question")
    )
    assert not any("文献检索" in msg for msg in statuses)
    assert "文献原著切片" not in context


# ---------- 判空降级 ----------


def test_empty_scope_yields_visible_degradation(fake_store_env: None) -> None:
    """无勾选集合 / 无附件 / 无首轮命中 → 显式降级状态帧（不静默）。"""
    statuses, context, _sources, _messages = _run(
        _settings(intent_research_enabled=True), _plan(), collections=None
    )
    assert any("未能确定文献集合" in msg for msg in statuses)
    assert "文献原著切片" not in context


# ---------- 正常注入 ----------


def test_literature_context_injected_when_scope_resolved(
    fake_store_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """集合可判定 → 文献块注入 context，来源带 literature 标记，状态帧可见。"""
    calls: list[dict[str, Any]] = []

    def _fake_build(**kwargs: Any) -> tuple[str, list[Any], dict[str, Any]]:
        calls.append(kwargs)
        kwargs["on_status"]("正在检索所选文献集合（1 个集合）...")
        kwargs["on_status"]("✔ 文献检索：命中 1 个原著切片")
        return (
            "【科研写作准则】\n1. 主论点须来自文献\n"
            "【文献原著切片 (Literature)】\n[1] 《多模态检索综述》正文片段",
            [_lit_ref(1)],
            {"literature_scope": ["lit"], "topic_injected": False, "literature_count": 1},
        )

    monkeypatch.setattr(research_mod, "build_research_context", _fake_build)

    statuses, context, sources, _messages = _run(
        _settings(intent_research_enabled=True), _plan(research_task="compare"),
        collections=["lit"],
    )

    assert calls, "科研子链路未被调用"
    assert calls[0]["literature_collections"] == ["lit"]
    assert calls[0]["research_task"] == "compare"
    assert calls[0]["start_idx"] == 1
    assert "科研写作准则" in context
    assert "文献原著切片" in context
    assert any("文献检索" in msg for msg in statuses)
    assert any(src.literature for src in sources)


def test_research_exception_degrades_gracefully(
    fake_store_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """科研链路抛异常 → 降级状态帧，主链路照常产出结果（不阻断对话）。"""
    def _explode(**kwargs: Any) -> tuple[str, list[Any], dict[str, Any]]:
        raise RuntimeError("文献链路不可用")

    monkeypatch.setattr(research_mod, "build_research_context", _explode)

    statuses, context, _sources, messages = _run(
        _settings(intent_research_enabled=True), _plan(), collections=["lit"]
    )
    assert any("科研写作降级" in msg for msg in statuses)
    assert messages, "主链路消息必须照常组装"


def test_research_zero_hit_yields_fallback_status(
    fake_store_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """文献链路产出空上下文 → 显式提示将基于通用知识整理。"""
    monkeypatch.setattr(
        research_mod, "build_research_context",
        lambda **kwargs: ("", [], {"literature_scope": ["lit"], "literature_count": 0}),
    )
    statuses, context, _sources, _messages = _run(
        _settings(intent_research_enabled=True), _plan(), collections=["lit"]
    )
    assert any("未产出可用上下文" in msg for msg in statuses)
    assert "文献原著切片" not in context


# ---------- 预算裁剪优先级 ----------


def test_literature_block_is_trimmed_last(monkeypatch: pytest.MonkeyPatch) -> None:
    """预算不足时文献块保留最多：web 块优先裁剪（design 决策 C-3 步骤 5）。"""
    monkeypatch.setattr(
        "doc2mind.core.llm.model_registry.get_model_spec",
        lambda model, provider: SimpleNamespace(context_window=2000, max_output_tokens=500),
    )
    lit_block = "【文献原著切片 (Literature)】\n" + ("文献正文内容" * 300)
    web_block = "【联网检索资料】\n" + ("网页内容填充" * 300)
    blocks = [lit_block, web_block]
    full = "\n\n---\n\n".join(blocks)

    trimmed = rag._truncate_context_to_budget(
        full, list(blocks), [], _settings(), "综述查询", []
    )

    lit_kept = trimmed.count("文献正文内容")
    web_kept = trimmed.count("网页内容填充")
    assert lit_kept > web_kept, "文献块应比 web 块保留更多原文"
    assert lit_kept > 250  # 文献块几乎完整保留（裁剪比例 5%）


def test_research_constraints_block_classified_as_literature(
    monkeypatch: pytest.MonkeyPatch
) -> None:
    """科研写作准则块归类为文献类（最后裁剪），不被误判为普通 local。"""
    monkeypatch.setattr(
        "doc2mind.core.llm.model_registry.get_model_spec",
        lambda model, provider: SimpleNamespace(context_window=2000, max_output_tokens=500),
    )
    constraint_block = research_mod.RESEARCH_CONSTRAINTS + "\n" + ("补充约束说明" * 40)
    local_block = "【本地知识库原著切片 (Local Knowledge)】\n" + ("普通知识内容" * 300)
    blocks = [constraint_block, local_block]
    full = "\n\n---\n\n".join(blocks)

    trimmed = rag._truncate_context_to_budget(
        full, list(blocks), [], _settings(), "综述查询", []
    )
    # 本地块先被裁剪（30%），文献类约束块最后裁剪（5%）
    assert trimmed.count("普通知识内容") < 300
    assert trimmed.count("补充约束说明") >= 36  # 文献类仅裁 5%，几乎完整保留
    assert "科研写作准则" in trimmed