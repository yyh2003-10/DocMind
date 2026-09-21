"""M3-T12 科研引用支撑验证单测（design 决策 D-1 / D-4）。

覆盖 tasks.md 12.1 / 12.2：
- verify_citation_support 三类样本（supported / external_only / uncited）
- 免责声明窗口与文献编号交集
- per_sentence 省流 vs 详表
- audit_answer_citations 追加 literature_support（既有 key 语义不变）
- _build_evidence_summary 默认零污染、显式传入时输出新字段
"""

from __future__ import annotations

from typing import Any

import pytest

import doc2mind.core.rag as rag
from doc2mind.core.agent.research import (
    KEY_STATEMENT_MIN_LEN,
    OPINION_VERBS,
    split_sentences,
    verify_citation_support,
)


def _ref(index: int, *, literature: bool = False, source_type: str = "local") -> Any:
    """构造最小 SourceRef。"""
    return rag.SourceRef(
        index=index,
        source=f"doc_{index}.pdf" if source_type == "local" else "https://example.com/x",
        format="pdf" if source_type == "local" else "html",
        source_type=source_type,
        literature=literature,
    )


LIT_SOURCES = [_ref(1, literature=True), _ref(2, literature=False, source_type="web")]


# --------------------------------------------------------------------------
# 12.1 引用支撑验证算法
# --------------------------------------------------------------------------


def test_supported_when_citing_literature_index() -> None:
    """关键陈述引用文献编号 → supported，support_rate=1.0。"""
    reply = "根据实验数据，稠密向量检索在长文档上的召回明显优于关键词匹配。[1]"
    result = verify_citation_support(reply, LIT_SOURCES, literature_indices={1})

    assert result["key_statement_count"] == 1
    assert result["supported"] == 1
    assert result["unsupported_cited"] == 0
    assert result["unsupported_uncited"] == 0
    assert result["support_rate"] == 1.0
    assert result["per_sentence"][0]["reason"] == "literature_support"
    assert result["per_sentence"][0]["supported"] is True


def test_external_only_when_citing_non_literature_source() -> None:
    """仅引用非文献来源（web/附件）→ external_only，不算文献支撑。"""
    reply = "该方法优于其他方案。[2]"
    result = verify_citation_support(reply, LIT_SOURCES, literature_indices={1})

    assert result["supported"] == 0
    assert result["unsupported_cited"] == 1
    assert result["support_rate"] == 0.0
    assert result["per_sentence"][0]["reason"] == "external_only"


def test_uncited_when_key_statement_has_no_reference() -> None:
    """关键陈述无引用 → uncited（观点动词触发关键性）。"""
    reply = "因此稠密向量更适合长文档场景"
    result = verify_citation_support(reply, LIT_SOURCES, literature_indices={1})

    assert result["key_statement_count"] == 1
    assert result["unsupported_uncited"] == 1
    assert result["per_sentence"][0]["reason"] == "uncited"


def test_disclaimer_window_excludes_literature_support() -> None:
    """免责声明窗口：[1] 被判 disclaimer_only 时不计为文献支撑。"""
    reply = "文献 [1] 并未提及该方法。"
    audit = rag.audit_answer_citations(reply, LIT_SOURCES)
    assert 1 in audit["disclaimer_only"]

    result = verify_citation_support(reply, LIT_SOURCES, audit, literature_indices={1})
    assert result["supported"] == 0
    assert result["unsupported_cited"] == 1
    assert result["per_sentence"][0]["reason"] == "external_only"


def test_support_rate_aggregates_mixed_sentences() -> None:
    """混合样本：support_rate = supported / key_statement_count。"""
    reply = (
        "稠密向量在长文档上召回优于关键词匹配。[1]；"
        "该方法优于其他方案。[2]；"
        "因此稠密向量更适合长文档场景"
    )
    result = verify_citation_support(reply, LIT_SOURCES, literature_indices={1})

    assert result["key_statement_count"] == 3
    assert result["supported"] == 1
    assert result["unsupported_cited"] == 1
    assert result["unsupported_uncited"] == 1
    assert result["support_rate"] == pytest.approx(1 / 3, abs=1e-3)
    assert [item["reason"] for item in result["per_sentence"]] == [
        "literature_support",
        "external_only",
        "uncited",
    ]


def test_non_key_short_sentence_excluded_from_count() -> None:
    """短句无观点动词无引用 → 非关键陈述，不计入 key_statement_count。"""
    reply = "好的。我们开始。"
    result = verify_citation_support(reply, LIT_SOURCES, literature_indices={1})

    assert result["sentence_count"] == 2
    assert result["key_statement_count"] == 0
    assert result["support_rate"] == 0.0
    assert all(item["reason"] == "not_key" for item in result["per_sentence"])


def test_long_sentence_is_key_even_without_verbs() -> None:
    """句长超过阈值 → 视为关键陈述（观点性长句）。"""
    long_sentence = "x" * (KEY_STATEMENT_MIN_LEN + 1)
    result = verify_citation_support(long_sentence, LIT_SOURCES, literature_indices={1})
    assert result["key_statement_count"] == 1
    assert result["per_sentence"][0]["reason"] == "uncited"


def test_literature_indices_fallback_from_sources() -> None:
    """literature_indices 为 None 时从 SourceRef.literature 兜底推导。"""
    reply = "稠密向量召回优于关键词匹配。[1]"
    result = verify_citation_support(reply, LIT_SOURCES, None, None)
    assert result["supported"] == 1
    assert result["per_sentence"][0]["reason"] == "literature_support"


def test_empty_reply_returns_zero_metrics() -> None:
    """空回答 → 零指标，不抛错。"""
    result = verify_citation_support("", [], None, set())
    assert result["sentence_count"] == 0
    assert result["key_statement_count"] == 0
    assert result["support_rate"] == 0.0
    assert result["per_sentence"] == []


def test_per_sentence_detail_toggles() -> None:
    """省流模式只给前 3 条样例并截断文本；详表模式给完整 per_sentence。"""
    payload = "稠密向量召回优于关键词匹配" * 16  # 单句长度 > 160，用于校验截断
    reply = "；".join(f"第 {i} 句：{payload}。[1]" for i in range(1, 8))
    compact = verify_citation_support(reply, LIT_SOURCES, literature_indices={1})
    detailed = verify_citation_support(
        reply, LIT_SOURCES, literature_indices={1}, include_detail=True
    )

    assert len(compact["per_sentence"]) == 3
    assert len(detailed["per_sentence"]) == 7
    assert all(len(item["text"]) <= 160 for item in compact["per_sentence"])
    assert any(len(item["text"]) > 160 for item in detailed["per_sentence"])


def test_trailing_reference_merges_into_previous_sentence() -> None:
    """句号隔开的引用编号并入前一句，避免引用与所属陈述分离（支撑率被低估）。"""
    assert split_sentences("稠密向量召回优于关键词匹配。[1]") == [
        "稠密向量召回优于关键词匹配 [1]"
    ]
    assert split_sentences("结论来自资料 [1]。[2]") == ["结论来自资料 [1] [2]"]


def test_split_sentences_and_opinion_verbs_contract() -> None:
    """拆句口径与观点动词常量契约稳定（供离线评估脚本复用）。"""
    assert split_sentences("第一句。第二句！第三句\n第四句；第五句") == [
        "第一句", "第二句", "第三句", "第四句", "第五句",
    ]
    for verb in ("认为", "表明", "指出", "证实", "对比", "差异",
                 "优于", "劣于", "支持", "反对", "结论", "因此"):
        assert verb in OPINION_VERBS


# --------------------------------------------------------------------------
# 12.2 引用审计增强与 evidence 组装
# --------------------------------------------------------------------------


def test_audit_adds_literature_support_without_touching_existing_keys() -> None:
    """audit_answer_citations 追加 literature_support，既有 key 语义不变。"""
    answer = "结论来自资料 [1] 与网页 [2]。"
    audit = rag.audit_answer_citations(answer, LIT_SOURCES)

    assert audit["cited"] == [1, 2]
    assert audit["valid"] == [1, 2]
    assert audit["invalid"] == []
    assert audit["evidence_support"] == [1, 2]
    assert audit["ok"] is True
    assert audit["literature_support"] == [1]


def test_evidence_summary_has_no_research_keys_by_default() -> None:
    """默认 flag 下 evidence 逐 key 与现状一致：新增字段为零（不插入 key）。"""
    summary = rag._build_evidence_summary(LIT_SOURCES)
    assert "routing" not in summary
    assert summary.get("routing") is None
    assert summary.get("research_citation_support") is None

    with_audit = rag._build_evidence_summary(
        LIT_SOURCES, citation_audit=rag.audit_answer_citations("结论 [1]。", LIT_SOURCES)
    )
    assert with_audit["citation_audit"]["evidence_support"] == [1]
    assert with_audit.get("routing") is None
    assert with_audit.get("research_citation_support") is None


def test_evidence_summary_passes_through_research_fields() -> None:
    """显式传入时输出 routing / research_citation_support（旧客户端忽略未知字段）。"""
    routing = {
        "query_type": "research", "sub_type": "compare",
        "degraded": False, "decider": None, "confidence": None,
    }
    support = verify_citation_support(
        "稠密向量召回优于关键词匹配。[1]", LIT_SOURCES, literature_indices={1}
    )
    summary = rag._build_evidence_summary(
        LIT_SOURCES, routing=routing, research_citation_support=support
    )

    assert summary["routing"] == routing
    assert summary["research_citation_support"]["support_rate"] == 1.0
    # 既有 key 不受影响
    assert summary["local_count"] == 1
    assert summary["web_fetched_count"] == 0