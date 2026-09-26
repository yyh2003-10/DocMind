"""P0 提示词多轨 / 输出预算 / 续写指令单测。"""

from __future__ import annotations

from doc2mind.core.agent.prompt_policy import (
    PROMPT_TRACK_DEEP_QA,
    PROMPT_TRACK_DELIVERY,
    PROMPT_TRACK_RAG,
    apply_prompt_track,
    boost_max_tokens,
    build_continue_query,
    done_frame_extras,
    is_continue_query,
    is_definition_query,
    resolve_prompt_track,
)


def test_resolve_track_creative_and_research():
    assert resolve_prompt_track(query_type="creative", creative_mode="ppt") == PROMPT_TRACK_DELIVERY
    assert resolve_prompt_track(query_type="research", creative_mode=None) == PROMPT_TRACK_DELIVERY
    assert resolve_prompt_track(query_type="question", creative_mode=None) == PROMPT_TRACK_RAG


def test_resolve_track_explicit_and_continue_override():
    assert resolve_prompt_track(query_type="question", explicit="delivery") == PROMPT_TRACK_DELIVERY
    assert resolve_prompt_track(query_type="creative", explicit="rag") == PROMPT_TRACK_RAG
    assert resolve_prompt_track(query_type="question", continue_writing=True) == PROMPT_TRACK_DELIVERY
    assert resolve_prompt_track(query_type="question", explicit="deep_qa") == PROMPT_TRACK_DEEP_QA


def test_is_definition_query():
    assert is_definition_query("什么是挠度")
    assert is_definition_query("挠度的定义是什么")
    assert is_definition_query("强度和刚度的区别")
    assert not is_definition_query("你好")
    assert not is_definition_query("帮我重启服务")


def test_resolve_deep_qa_needs_definition_and_evidence():
    # 定义题 + 无证据 → 仍 rag（防注水）
    assert resolve_prompt_track(
        query_type="question", query="什么是挠度",
        deep_web=False, citable_count=0, local_cite_count=0,
    ) == PROMPT_TRACK_RAG
    # 定义题 + 深度联网 → deep_qa
    assert resolve_prompt_track(
        query_type="question", query="什么是挠度",
        deep_web=True, citable_count=1, local_cite_count=0,
    ) == PROMPT_TRACK_DEEP_QA
    # 定义题 + 多源 → deep_qa
    assert resolve_prompt_track(
        query_type="question", query="什么是挠度",
        deep_web=False, citable_count=2, local_cite_count=0,
    ) == PROMPT_TRACK_DEEP_QA
    # 定义题 + 库内命中 → deep_qa
    assert resolve_prompt_track(
        query_type="question", query="什么是挠度",
        deep_web=False, citable_count=1, local_cite_count=1,
    ) == PROMPT_TRACK_DEEP_QA
    # 非定义题 + 多源 → 仍 rag
    assert resolve_prompt_track(
        query_type="task", query="重启后端服务",
        deep_web=True, citable_count=5, local_cite_count=2,
    ) == PROMPT_TRACK_RAG
    # 创作意图优先于 deep_qa
    assert resolve_prompt_track(
        query_type="creative", creative_mode="ppt", query="什么是挠度",
        deep_web=True, citable_count=3,
    ) == PROMPT_TRACK_DELIVERY


def test_apply_prompt_track_delivery_appends_override_once():
    base = "你是助手。\n"
    once = apply_prompt_track(base, PROMPT_TRACK_DELIVERY)
    assert "【覆盖·交付/长文模式】" in once
    twice = apply_prompt_track(once, PROMPT_TRACK_DELIVERY)
    assert twice.count("【覆盖·交付/长文模式】") == 1
    rag = apply_prompt_track(base, PROMPT_TRACK_RAG)
    assert rag == base


def test_apply_prompt_track_deep_qa():
    base = "系统提示"
    text = apply_prompt_track(base, PROMPT_TRACK_DEEP_QA)
    assert "【覆盖·定义/深度问答模式】" in text
    assert "证据强度：弱（单一来源）" in text
    assert "禁止输出 JSON 工具调用" in text
    once = apply_prompt_track(text, PROMPT_TRACK_DEEP_QA)
    assert once.count("【覆盖·定义/深度问答模式】") == 1


def test_apply_delivery_preserves_rag_ban_but_lifts_length():
    text = apply_prompt_track("系统提示", PROMPT_TRACK_DELIVERY)
    assert "禁止输出 JSON 工具调用" in text
    assert "不适用「简单事实题一段话" in text


def test_boost_max_tokens_tracks():
    assert boost_max_tokens(8192, PROMPT_TRACK_RAG) == 8192
    assert boost_max_tokens(8192, PROMPT_TRACK_DELIVERY) == 16384
    assert boost_max_tokens(10000, PROMPT_TRACK_DELIVERY) == 20000
    assert boost_max_tokens(40000, PROMPT_TRACK_DELIVERY, ceiling=65536) == 65536
    assert boost_max_tokens(None, PROMPT_TRACK_DELIVERY) is None
    # deep_qa：1.5×，下限 4096
    assert boost_max_tokens(2000, PROMPT_TRACK_DEEP_QA) == 4096
    assert boost_max_tokens(8000, PROMPT_TRACK_DEEP_QA) == 12000
    assert boost_max_tokens(None, PROMPT_TRACK_DEEP_QA) is None


def test_continue_query_helpers():
    assert is_continue_query("继续")
    assert is_continue_query("")
    assert not is_continue_query("补充第三章数据")
    assert build_continue_query("继续").startswith("请接着会话中上一条")
    assert "补充要求" in build_continue_query("再加上结论")


def test_done_frame_extras_truncated():
    extras = done_frame_extras(track=PROMPT_TRACK_DELIVERY, truncated=True, continue_writing=True)
    assert extras["truncated"] is True
    assert extras["prompt_track"] == PROMPT_TRACK_DELIVERY
    assert extras["response_mode"] == "continue"
    assert "continue_hint" in extras
    ok = done_frame_extras(track=PROMPT_TRACK_RAG, truncated=False)
    assert "continue_hint" not in ok
