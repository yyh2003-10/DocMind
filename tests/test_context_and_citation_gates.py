"""多轮上下文与弱相关引用门控回归测试。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from doc2mind.core.config import Settings
from doc2mind.core.rag import (
    _CHAT_SESSIONS,
    _HISTORY_LOCK,
    _hit_supports_query,
    _load_history,
    _append_turn,
    partition_citation_hits,
)
from doc2mind.core.store.chat_store import ChatStore


@dataclass
class _C:
    content: str = ""
    heading: str | None = None
    source: str = ""
    page: int | None = None
    format: str = "md"
    id: int = 1


@dataclass
class _H:
    chunk: _C
    rerank_score: float | None = 0.9
    vector_score: float = 0.0
    bm25_score: float = 0.0
    score: float = 0.9
    source: str = ""


def test_hit_supports_query_overlap():
    q = "什么是挠度"
    good = _H(_C(content="挠度是指构件在外力下的位移量…", source="mech.md"))
    bad = _H(_C(content="DocMind 导入步骤与快捷键说明", source="guide.md"))
    assert _hit_supports_query(q, good) is True
    assert _hit_supports_query(q, bad) is False


def test_citation_min_default_raised():
    s = Settings()
    assert float(s.citation_min_score) >= 0.4
    assert 0.0 < float(s.citation_bg_score_ratio) <= 1.0
    assert int(s.llm_first_token_slow_ms) >= 5000


def test_high_score_no_lexical_overlap_not_cited():
    """与 query 无词汇重叠的高分切片不进 cite_hits（验收核心）。"""
    q = "什么是挠度"
    good = _H(_C(content="挠度是指构件在外力下的位移量…", source="mech.md"), rerank_score=0.92)
    off = _H(
        _C(content="DocMind 快速上手：点击新建对话导入文档。", source="guide.md"),
        rerank_score=0.95,
    )
    part = partition_citation_hits(
        q, [off, good], citation_min_score=0.45, top_k=5, reranked_usable=True
    )
    assert good in part.cite_hits
    assert off not in part.cite_hits
    assert off in part.bg_hits
    assert part.gate["topic_demoted_count"] == 1


def test_three_tier_gate_cite_bg_discard():
    """重排可用时：强=引用，中=背景，弱=丢弃。"""
    q = "挠度定义"
    strong = _H(_C(content="挠度定义：构件中点位移", source="a.md"), rerank_score=0.80)
    mid = _H(_C(content="挠度相关实验记录片段", source="b.md"), rerank_score=0.32)
    weak = _H(_C(content="菜单操作说明", source="c.md"), rerank_score=0.05)
    part = partition_citation_hits(
        q, [strong, mid, weak],
        citation_min_score=0.45, top_k=5, reranked_usable=True, bg_ratio=0.6,
    )
    assert part.cite_hits == [strong]
    assert part.bg_hits == [mid]
    assert part.discarded_hits == [weak]
    assert part.gate["cite_count"] == 1
    assert part.gate["bg_count"] == 1
    assert part.gate["discarded_count"] == 1


def test_partition_without_rerank_topic_gate():
    """无重排时：主题不匹配的高分命中也不进引用。"""
    q = "什么是GPT"
    guide = _H(
        _C(content="DocMind 快速上手：点击新建对话导入文档。", source="guide.md"),
        rerank_score=None, vector_score=0.91, bm25_score=0.1,
    )
    real = _H(
        _C(content="GPT（Generative Pre-trained Transformer）是大语言模型。", source="gpt.md"),
        rerank_score=None, vector_score=0.80, bm25_score=0.2,
    )
    part = partition_citation_hits(
        q, [guide, real], citation_min_score=0.45, top_k=5, reranked_usable=False
    )
    assert real in part.cite_hits
    assert guide not in part.cite_hits
    assert guide in part.bg_hits


def test_citation_min_score_configurable():
    s = Settings(citation_min_score=0.70)
    q = "挠度"
    strong = _H(_C(content="挠度定义…", source="a.md"), rerank_score=0.72)
    mid = _H(_C(content="挠度试验数据…", source="b.md"), rerank_score=0.50)
    part = partition_citation_hits(
        q, [strong, mid],
        citation_min_score=float(s.citation_min_score),
        top_k=5,
        reranked_usable=True,
        bg_ratio=float(s.citation_bg_score_ratio),
    )
    assert part.cite_hits == [strong]
    assert part.bg_hits == [mid]
    assert part.gate["citation_min_score"] == 0.70


def test_failed_turn_still_written_to_history(tmp_path):
    cid = "chat-fail-hist"
    db = tmp_path / "c.db"
    # 模拟：本轮失败也会写入用户问题
    _append_turn(cid, "上句问了挠度", "（本轮生成失败：LLMError）", db)
    cid2, hist = _load_history(cid, db)
    assert cid2 == cid
    assert any(m.get("role") == "user" and "挠度" in m.get("content", "") for m in hist)
    assert any(m.get("role") == "assistant" for m in hist)
    with _HISTORY_LOCK:
        _CHAT_SESSIONS.pop(cid, None)


def test_rag_source_has_multi_turn_hint_and_cite_gate():
    rag = Path(r"E:\DocMindY-worktrees\agent-p0\src\doc2mind\core\rag.py").read_text(encoding="utf-8")
    assert "多轮对话上下文" in rag
    assert "_hit_supports_query" in rag
    assert "partition_citation_hits" in rag
    assert "生成失败" in rag or "本轮未生成有效回答" in rag
    assert "禁止把记忆内容标成" in rag


def test_cite_gate_logic_in_source():
    rag = Path(r"E:\DocMindY-worktrees\agent-p0\src\doc2mind\core\rag.py").read_text(encoding="utf-8")
    assert "partition_citation_hits" in rag
    assert "citation_gate" in rag
    assert "llm_first_token_ms" in rag
    assert "模型响应较慢" in rag

def test_mixed_score_and_topic_demotions():
    """混合失败场景：低分丢弃 + 高分偏题 → 两类独立计数且可对账（FR-05）。"""
    q = "什么是挠度"
    low_with_support = _H(
        _C(content="挠度试验记录片段", source="b.md"), rerank_score=0.20
    )
    off_low = _H(
        _C(content="DocMind 导入步骤清晰文档", source="guide.md"), rerank_score=0.15
    )
    off_high = _H(
        _C(content="DocMind 快捷键说明", source="guide.md"), rerank_score=0.93
    )
    good = _H(
        _C(content="挠度定义：构件挠曲位移量", source="mech.md"), rerank_score=0.85
    )
    part = partition_citation_hits(
        q, [low_with_support, off_low, off_high, good],
        citation_min_score=0.45, top_k=5, reranked_usable=True, bg_ratio=0.6,
    )
    assert part.cite_hits == [good]
    assert part.gate["cite_count"] == 1
    assert off_high in part.bg_hits
    # 低分丢弃（rel<bg_floor 且主题重叠）与主题不符丢弃互不重叠
    assert part.gate["dropped_by_score"] == 1
    assert part.gate["dropped_by_topic"] == 1
    assert len(part.discarded_hits) == 2
    assert (
        part.gate["dropped_by_score"] + part.gate["dropped_by_topic"]
        == len(part.discarded_hits)
    )
    assert part.gate["cross_check"] is True


def test_gate_counts_reconcile_to_hit_count():
    """任意混合输入下 cross_check 恒可对账（FR-03/FR-05）。"""
    q = "Dify 工作流如何配置 HTTP 节点"
    hits = [
        _H(_C(content="Dify 工作流 HTTP 节点配置说明", source="a.md"), rerank_score=0.88),
        _H(_C(content="工作流拖拽交互文档", source="b.md"), rerank_score=0.50),
        _H(_C(content="挠度定义：构件挠曲位移量", source="mech.md"), rerank_score=0.90),
        _H(_C(content="随手笔记，无主题相关性", source="c.md"), rerank_score=0.10),
        _H(_C(content="低分背景片段", source="d.md"), rerank_score=0.31),
    ]
    part = partition_citation_hits(
        q, hits, citation_min_score=0.45, top_k=3, reranked_usable=True, bg_ratio=0.6,
    )
    gate = part.gate
    assert gate["cross_check"] is True
    total = (
        gate["cite_count"] + gate["bg_count"]
        + gate["dropped_by_score"] + gate["dropped_by_topic"]
    )
    assert total == gate["hit_count"] == len(hits)


def test_gate_exception_falls_back_to_citable(monkeypatch):
    """FR-06：门控分流自身抛异常时，命中按可引用处理，绝不静默丢弃。"""
    import doc2mind.core.rag as rag_mod
    from doc2mind.core.rag import rag_answer
    from unittest.mock import MagicMock, patch

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated gate failure")

    q = "什么是挠度"
    mock_client = MagicMock()
    mock_client.model_name = "mock-model"
    mock_client.provider = "mock"
    mock_client.chat.return_value = "根据挠度资料，答案为测试内容。"
    mock_client.last_truncated = False
    s = Settings(llm_provider="openai", llm_api_key="test", citation_min_score=0.45, rag_mode="hybrid")
    good = _H(_C(content="挠度是指构件在外力下的位移量", source="mech.md"), rerank_score=0.85)
    stats = MagicMock()
    stats.reranked = True

    with patch("doc2mind.core.rag.partition_citation_hits", side_effect=_boom):
        with patch("doc2mind.core.rag._open_store") as mock_open:
            mock_open.return_value = (MagicMock(), MagicMock())
            with patch("doc2mind.core.rag.Retriever") as MockRetriever:
                mock_retriever = MagicMock()
                mock_retriever.search.return_value = ([good], stats)
                MockRetriever.return_value = mock_retriever
                with patch("doc2mind.core.store.graph_store.GraphStore") as gs:
                    gs.return_value.find_entities_by_keyword.return_value = []
                    result = rag_answer(query=q, settings=s, llm_client=mock_client,
                                        enable_web_search=False)

    assert len(result.sources) == 1
    assert result.sources[0].source == "mech.md"
    assert rag_mod.logger is not None  # 门控异常已走回退路径（不抛错）
