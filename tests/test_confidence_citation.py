"""人话相关度标签 + 答案引用编号审计。"""

from doc2mind.core.rag import (
    SourceRef,
    audit_answer_citations,
    confidence_label,
)


class TestConfidenceLabel:
    def test_high_medium_low_thresholds(self):
        assert confidence_label(0.85, "rerank") == "高"
        assert confidence_label(0.70, "vector") == "高"
        assert confidence_label(0.55, "bm25") == "中"
        assert confidence_label(0.30, "vector") == "低"
        assert confidence_label(0.0, "vector") == "未知"

    def test_rrf_never_mapped_to_level(self):
        # RRF 量纲 ~0.02，绝不能显示成「低/中/高」
        assert confidence_label(0.033, "rrf") == "排名参考"
        assert confidence_label(0.001, "rrf") == "排名参考"

    def test_attachment_and_web(self):
        assert confidence_label(1.0, "attachment") == "附件"
        assert confidence_label(0.8, "web_relevance") == "高"
        assert confidence_label(0.45, "web_relevance") == "中"
        assert confidence_label(0.1, "web_relevance") == "低"


class TestCitationAudit:
    def _src(self, idx: int) -> SourceRef:
        return SourceRef(
            index=idx, source=f"doc{idx}.md", format="markdown",
            score=0.8, score_type="vector", confidence_label="高",
        )

    def test_all_valid(self):
        sources = [self._src(1), self._src(2)]
        audit = audit_answer_citations("结论见 [1] 和 [2]。", sources)
        assert audit["ok"] is True
        assert audit["cited"] == [1, 2]
        assert audit["invalid"] == []

    def test_invalid_citation_detected(self):
        sources = [self._src(1)]
        audit = audit_answer_citations("见 [1] 以及 [9]", sources)
        assert audit["ok"] is False
        assert audit["invalid"] == [9]
        assert audit["valid"] == [1]
        assert audit["valid_ratio"] == 0.5

    def test_no_citation_is_ok(self):
        audit = audit_answer_citations("没有引用。", [self._src(1)])
        assert audit["ok"] is True
        assert audit["cited"] == []
        assert audit["valid_ratio"] == 1.0

    def test_disclaimer_citations_not_counted_as_support(self):
        """「并未提及」类免责声明引用不得算作 evidence_support。

        真实故障：答案写「[[1]]-[5] 并未提及豆包」，旧审计仍说已综合 5 条。
        """
        sources = [self._src(i) for i in range(1, 6)]
        answer = "在提供的参考资料中 [[1]]-[5] 并未提及豆包相关内容，知识库暂无该模型的具体描述。"
        audit = audit_answer_citations(answer, sources)
        assert set(audit["disclaimer_only"]) >= {1, 5}
        assert audit["evidence_support"] == []
        assert audit["valid"] == [1, 5]

    def test_support_citation_still_counted(self):
        sources = [self._src(1), self._src(2)]
        audit = audit_answer_citations("架构说明见 [1]，实现见 [2]。", sources)
        assert audit["evidence_support"] == [1, 2]
        assert audit["disclaimer_only"] == []

    def test_mixed_support_and_disclaimer(self):
        sources = [self._src(1), self._src(2)]
        audit = audit_answer_citations("根据 [1] 可知结论；[2] 与本主题无关/未提及。", sources)
        assert audit["evidence_support"] == [1]
        assert 2 in audit["disclaimer_only"]

    def test_fullwidth_citation_counts_as_support(self):
        """模型常写全角【1】，旧审计只认半角 → 误判「未作引用」。"""
        sources = [self._src(1)]
        audit = audit_answer_citations("挠度定义见教材【1】。", sources)
        assert audit["cited"] == [1]
        assert audit["evidence_support"] == [1]
        assert audit["disclaimer_only"] == []

    def test_fullwidth_disclaimer_still_excluded(self):
        sources = [self._src(1), self._src(2)]
        audit = audit_answer_citations("根据【1】可得定义；【2】并未提及该概念。", sources)
        assert audit["evidence_support"] == [1]
        assert 2 in audit["disclaimer_only"]

    def test_mixed_halfwidth_and_fullwidth(self):
        sources = [self._src(1), self._src(2)]
        audit = audit_answer_citations("见 [1] 与【2】。", sources)
        assert audit["cited"] == [1, 2]
        assert audit["evidence_support"] == [1, 2]
