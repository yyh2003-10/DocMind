"""Tests for the intent arbitration module (M1)."""

from __future__ import annotations

import pytest

from doc2mind.core.agent.arbiter import (
    ArbitrationResult,
    _l0_fast_path,
    _l1_arbitrate,
    _score_signals,
    arbitrate,
)
from doc2mind.core.agent.intent import map_creative_mode, map_research_task
from doc2mind.core.agent.planner import AgentPlan, ToolPlan


# ── L0 Fast Path Tests ──────────────────────────────────────────────────────
class TestL0FastPath:
    """L0 规则快路径：问候 / 明确创作 / 明确科研。"""

    def test_greeting(self) -> None:
        result = _l0_fast_path("你好", None)
        assert result is not None
        assert result.winning == "greeting"
        assert result.decider == "l0_regex"

    def test_creative_with_artifact(self) -> None:
        result = _l0_fast_path("帮我做个PPT", None)
        assert result is not None
        assert result.winning == "creative"
        assert result.sub_type == "ppt"

    def test_research_strong_hit(self) -> None:
        result = _l0_fast_path("把这些文献的观点对比一下", None)
        assert result is not None
        assert result.winning == "research"
        assert result.sub_type == "compare"

    def test_no_match_returns_none(self) -> None:
        result = _l0_fast_path("什么是机器学习", None)
        assert result is None

    def test_chitchat_not_greeting(self) -> None:
        """闲聊词不判 greeting：走 question（design A-4，不强制检索）。"""
        result = _l0_fast_path("随便聊聊", None)
        assert result is None  # L0 不直通，交给 L1/L2/LLM 判 question


# ── L1 Scoring Tests ────────────────────────────────────────────────────────
class TestL1Scoring:
    """L1 规则打分：creative / research / question / summary。"""

    def test_creative_signal(self) -> None:
        c, r, qs, s = _score_signals("帮我做个PPT", None)
        assert c >= 2
        assert r == 0
        assert qs == 0

    def test_research_signal(self) -> None:
        c, r, qs, s = _score_signals("把这些文献的观点对比一下", None)
        assert r >= 3
        assert c == 0

    def test_question_signal(self) -> None:
        c, r, qs, s = _score_signals("对比 A 和 B 的区别", None)
        assert qs >= 1
        assert c == 0
        assert r == 0

    def test_summary_signal(self) -> None:
        c, r, qs, s = _score_signals("总结一下这篇文章", None)
        assert s >= 1

    def test_weak_hit_with_history(self) -> None:
        history = [{"role": "user", "content": "我这里有 5 篇关于 RAG 的论文"}]
        c, r, qs, s = _score_signals("整理成对比报告", history)
        assert r >= 2  # 弱命中：历史上文含文献语境


class TestL1Arbitrate:
    """L1 仲裁：单胜 / 冲突 / 悬空。"""

    def test_single_winner_creative(self) -> None:
        result = _l1_arbitrate(c=3.0, r=0.0, qs=0.0, s=0.0)
        assert result is not None
        assert result.winning == "creative"
        assert result.decider == "l1_score"

    def test_single_winner_research(self) -> None:
        result = _l1_arbitrate(c=0.0, r=3.0, qs=0.0, s=0.0)
        assert result is not None
        assert result.winning == "research"

    def test_single_winner_question(self) -> None:
        result = _l1_arbitrate(c=0.0, r=0.0, qs=2.0, s=0.0)
        assert result is not None
        assert result.winning == "question"

    def test_conflict_delta_gt_threshold(self) -> None:
        # creative=3.5, research=1.5, delta=2.0 > 0.5 → creative 胜
        result = _l1_arbitrate(c=3.5, r=1.5, qs=0.0, s=0.0)
        assert result is not None
        assert result.winning == "creative"

    def test_conflict_delta_le_threshold(self) -> None:
        # creative=2.2, research=2.0, delta=0.2 ≤ 0.5 → 悬空
        result = _l1_arbitrate(c=2.2, r=2.0, qs=0.0, s=0.0)
        assert result is None  # 悬空，进 L2

    def test_no_signal(self) -> None:
        result = _l1_arbitrate(c=0.0, r=0.0, qs=0.0, s=0.0)
        assert result is None


# ── Main Entry Point Tests ──────────────────────────────────────────────────
class TestArbitrate:
    """arbitrate() 主入口：三级仲裁完整链路。"""

    def _make_plan(self, query_type: str, creative_mode=None, research_task=None) -> AgentPlan:
        return AgentPlan(
            analysis="test",
            query_type=query_type,
            tools=[],
            expected_output="test",
            creative_mode=creative_mode,
            research_task=research_task,
        )

    def test_l0_greeting(self) -> None:
        result = arbitrate(
            query="你好",
            history=None,
            llm_plan=None,
            llm_client=None,
            settings=type("S", (), {"intent_conflict_arbitration": "rules"})(),
        )
        assert result.winning == "greeting"
        assert result.decider == "l0_regex"

    def test_l0_creative(self) -> None:
        result = arbitrate(
            query="帮我做个PPT",
            history=None,
            llm_plan=None,
            llm_client=None,
            settings=type("S", (), {"intent_conflict_arbitration": "rules"})(),
        )
        assert result.winning == "creative"
        assert result.decider == "l0_regex"

    def test_l0_research(self) -> None:
        result = arbitrate(
            query="把这些文献的观点对比一下",
            history=None,
            llm_plan=None,
            llm_client=None,
            settings=type("S", (), {"intent_conflict_arbitration": "rules"})(),
        )
        assert result.winning == "research"
        assert result.decider == "l0_regex"

    def test_l1_question_no_llm(self) -> None:
        """LLM 不可用：疑问句式规则判定 question（l1_score）。"""
        result = arbitrate(
            query="什么是机器学习",
            history=None,
            llm_plan=None,
            llm_client=None,
            settings=type("S", (), {"intent_conflict_arbitration": "rules"})(),
        )
        assert result.winning == "question"
        assert result.decider == "l1_score"

    def test_use_llm_plan_when_no_conflict(self) -> None:
        """LLM 初判无冲突 → 沿用 LLM 初判。"""
        plan = self._make_plan("question")
        result = arbitrate(
            query="什么是机器学习",
            history=None,
            llm_plan=plan,
            llm_client=None,
            settings=type("S", (), {"intent_conflict_arbitration": "rules"})(),
        )
        assert result.winning == "question"
        assert result.decider == "llm_plan"

    def test_arbitration_none_uses_llm_plan(self) -> None:
        """仲裁模式为 none → 沿用 LLM 初判。"""
        plan = self._make_plan("creative", creative_mode="ppt")
        result = arbitrate(
            query="做个PPT",
            history=None,
            llm_plan=plan,
            llm_client=None,
            settings=type("S", (), {"intent_conflict_arbitration": "none"})(),
        )
        assert result.winning == "creative"
        assert result.decider == "llm_plan"

    def test_weak_hit_with_history(self) -> None:
        """多轮弱命中：历史上文含文献语境。"""
        history = [{"role": "user", "content": "我这里有 5 篇关于 RAG 的论文"}]
        result = arbitrate(
            query="整理成对比报告",
            history=history,
            llm_plan=None,
            llm_client=None,
            settings=type("S", (), {"intent_conflict_arbitration": "rules"})(),
        )
        assert result.winning == "research"

    def test_false_positive_prevented(self) -> None:
        """防误判：仅「对比」无文献语境 → 不触发科研。"""
        result = arbitrate(
            query="对比 A 和 B 的区别",
            history=None,
            llm_plan=None,
            llm_client=None,
            settings=type("S", (), {"intent_conflict_arbitration": "rules"})(),
        )
        assert result.winning == "question"


# ── ArbitrationResult Contract Tests ────────────────────────────────────────
class TestArbitrationResult:
    """ArbitrationResult 契约完整性。"""

    def test_fields(self) -> None:
        r = ArbitrationResult(
            winning="research",
            sub_type="compare",
            confidence=0.85,
            reason="测试依据",
            decider="l1_score",
        )
        assert r.winning == "research"
        assert r.sub_type == "compare"
        assert r.confidence == 0.85
        assert r.reason == "测试依据"
        assert r.decider == "l1_score"

    def test_frozen(self) -> None:
        r = ArbitrationResult(
            winning="question",
            sub_type=None,
            confidence=0.5,
            reason="test",
            decider="l0_regex",
        )
        with pytest.raises(Exception):
            r.winning = "creative"  # type: ignore[misc]


# ── Helper Function Tests ───────────────────────────────────────────────────
class TestMapHelpers:
    """map_creative_mode / map_research_task 在 arbiter 中的行为。"""

    def test_map_creative_mode(self) -> None:
        assert map_creative_mode("帮我做个PPT") == "ppt"
        assert map_creative_mode("写一份研报") == "doc"
        assert map_creative_mode("做个对比表") == "table"
        assert map_creative_mode("随便聊聊") is None

    def test_map_research_task(self) -> None:
        assert map_research_task("帮我写个综述") == "review"
        assert map_research_task("观点对比一下") == "compare"
        assert map_research_task("带引用草稿") == "draft"
        assert map_research_task("什么是机器学习") is None