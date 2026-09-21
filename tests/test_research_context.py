"""M3-T10 科研写作子链路单元测试：文献集合判定 + 科研上下文组装。

覆盖任务清单 10.1 / 10.2 单测要求：
- 四步回退各分支（显式 → 勾选 → 附件/首轮命中来源 → 判空 None）
- 限定集合检索只返回集合内切片（断言 collection / top_k 入参）
- topic 注入结构正确（概念/实体 + 关联文献，去重 ≤30）
- 判空 / 零命中 / 异常 / 降级路径均不阻断主链路
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from doc2mind.core.agent.research import (
    LITERATURE_BLOCK_HEAD,
    RESEARCH_TASK_LABELS,
    LiteratureScopeError,
    _format_topic_block,
    build_research_context,
    resolve_literature_collections,
)
from doc2mind.core.config import Settings
from doc2mind.core.retriever.search import SearchHit, StoredChunkMeta


# ---------- fixtures ----------


@dataclass(frozen=True)
class _FakeDoc:
    """store.list_documents 返回的最小文档视图（仅科研集合推导所需字段）。"""

    source: str
    collection: str


@dataclass(frozen=True)
class _FakeStore:
    """最小 store 替身：仅提供 list_documents（limit 可选）。"""

    docs: list[_FakeDoc]
    limit_supported: bool = True
    raise_error: bool = False

    def list_documents(self, **kwargs: Any) -> list[_FakeDoc]:
        if self.raise_error:
            raise RuntimeError("store unavailable")
        if "limit" in kwargs and not self.limit_supported:
            raise TypeError("unexpected keyword argument 'limit'")
        return list(self.docs)


def _make_hit(chunk_id: int, source: str, content: str, heading: str | None = None) -> SearchHit:
    """构造检索命中（字段与 Retriever.search 输出一致）。"""
    chunk = StoredChunkMeta(
        id=chunk_id,
        content=content,
        source=source,
        format="pdf",
        doc_type="paper",
        page=1,
        heading=heading,
        tokens=128,
        chunk_index=0,
        collection="lit",
    )
    return SearchHit(
        chunk=chunk,
        score=0.9,
        match_type="vector",
        vector_score=0.9,
        bm25_score=0.0,
        rank=1,
        sparse_score=0.0,
        rerank_score=None,
    )


@dataclass
class _FakeRetriever:
    """最小 retriever 替身：记录入参并返回预设命中，可注入异常/降级。"""

    hits: list[SearchHit] = field(default_factory=list)
    degraded: bool = False
    raise_error: bool = False
    calls: list[dict[str, Any]] = field(default_factory=list)

    def search(
        self,
        query: str,
        collection: str | list[str] | None = "default",
        top_k: int = 10,
        min_score: float = 0.0,
    ) -> tuple[list[SearchHit], Any]:
        self.calls.append(
            {"query": query, "collection": collection, "top_k": top_k, "min_score": min_score}
        )
        if self.raise_error:
            raise RuntimeError("retriever exploded")
        stats = SimpleNamespace(
            degraded=self.degraded,
            degraded_reason="嵌入服务不可用，本次为纯 BM25 检索" if self.degraded else None,
        )
        return list(self.hits), stats


def _retriever(
    hits: list[SearchHit] | None = None,
    *,
    degraded: bool = False,
    raise_error: bool = False,
) -> _FakeRetriever:
    return _FakeRetriever(
        hits=list(hits or []), degraded=degraded, raise_error=raise_error
    )


@dataclass(frozen=True)
class _FakeGraphStore:
    """最小 graph_store 替身：find_entities_by_keyword + get_topic_context。"""

    topic: dict[str, Any] | None = None
    raise_error: bool = False

    def find_entities_by_keyword(self, keyword: str, limit: int = 5) -> list[dict[str, Any]]:
        return [{"id": "e1", "name": keyword, "type": "concept"}]

    def get_topic_context(self, **kwargs: Any) -> dict[str, Any]:
        if self.raise_error:
            raise RuntimeError("graph store unavailable")
        return dict(self.topic or {})


def _settings(**overrides: Any) -> Settings:
    s = Settings()
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


LIT_HITS = [
    _make_hit(1, r"E:\lit\paper_a.pdf", "论文 A 正文片段", heading="方法"),
    _make_hit(2, r"E:\lit\paper_b.pdf", "论文 B 正文片段", heading="结论"),
]


# ---------- 10.1 文献集合判定与回退链 ----------


def test_resolve_prefers_explicit_request_collections() -> None:
    """请求显式 literature_collections 优先，且去重、封顶。"""
    result = resolve_literature_collections(
        req_collections=["lit-a", "lit-a", " lit-b "],
        chat_collections=["checked"],
        s=_settings(),
    )
    assert result == ["lit-a", "lit-b"]


def test_resolve_explicit_collections_capped_by_research_max_literature() -> None:
    """显式集合数超过 research_max_literature 时截断（design C-3 步骤 1）。"""
    result = resolve_literature_collections(
        req_collections=["a", "b", "c"], s=_settings(research_max_literature=2)
    )
    assert result == ["a", "b"]


def test_resolve_falls_back_to_checked_collections() -> None:
    """无显式集合时采用本轮勾选集合（兼容字符串与列表）。"""
    assert resolve_literature_collections(chat_collections="checked", s=_settings()) == ["checked"]
    assert resolve_literature_collections(
        chat_collections=["x", "y"], s=_settings()
    ) == ["x", "y"]


def test_resolve_derives_collections_from_first_round_hits() -> None:
    """勾选为空时，从首轮命中来源反查所属集合（去重保序）。"""
    store = _FakeStore(
        docs=[
            _FakeDoc(source=r"E:\lit\paper_a.pdf", collection="lit"),
            _FakeDoc(source=r"E:\notes\scratch.md", collection="notes"),
        ]
    )
    hits = [
        _make_hit(1, r"E:\lit\paper_a.pdf", "x"),
        _make_hit(2, r"E:\lit\paper_b.pdf", "y"),  # 同名不同文件，同集合 → 去重
        _make_hit(3, r"E:\notes\scratch.md", "z"),
    ]
    result = resolve_literature_collections(first_round_hits=hits, store=store, s=_settings())
    assert result == ["lit", "notes"]


def test_resolve_uses_basename_fallback_for_attachments() -> None:
    """附件路径按 basename 兜底映射集合（绝对路径不一致时仍可推导）。"""
    store = _FakeStore(docs=[_FakeDoc(source=r"D:\repo\paper.pdf", collection="lit")])
    result = resolve_literature_collections(
        attachments=[r"E:\downloads\paper.pdf"], store=store, s=_settings()
    )
    assert result == ["lit"]


def test_resolve_returns_none_when_no_signal() -> None:
    """四步回退均无信号 → 判空返回 None（触发 rag 降级到通用知识）。"""
    store = _FakeStore(docs=[_FakeDoc(source=r"E:\notes\a.md", collection="notes")])
    assert resolve_literature_collections(
        attachments=[r"E:\downloads\unknown.txt"],
        first_round_hits=[_make_hit(1, r"E:\notes\a.md", "x")],
        store=_FakeStore(docs=[]),
        s=_settings(),
    ) is None
    assert resolve_literature_collections(s=_settings()) is None
    assert resolve_literature_collections(
        attachments=[r"E:\downloads\unknown.txt"], store=store, s=_settings()
    ) is None


def test_resolve_tolerates_unavailable_store() -> None:
    """store 不可用 / 旧签名不支持 limit → 不抛错，判空返回 None。"""
    assert resolve_literature_collections(
        first_round_hits=LIT_HITS,
        store=_FakeStore(docs=[], raise_error=True),
        s=_settings(),
    ) is None
    assert resolve_literature_collections(
        first_round_hits=LIT_HITS,
        store=_FakeStore(docs=[_FakeDoc(r"E:\lit\paper_a.pdf", "lit")], limit_supported=False),
        s=_settings(),
    ) == ["lit"]


# ---------- 10.2 科研上下文组装 ----------


def test_build_raises_when_scope_empty() -> None:
    """文献集合判空 → LiteratureScopeError（由 rag 侧显式降级，不静默）。"""
    with pytest.raises(LiteratureScopeError) as exc_info:
        build_research_context(
            query="综述一下", retriever=_retriever(), s=_settings(), literature_collections=None
        )
    assert "未能确定文献集合" in str(exc_info.value)


def test_build_retrieves_only_within_literature_scope() -> None:
    """限定集合检索：collection / top_k 入参正确，命中切片带 literature 标记。"""
    retriever = _retriever(LIT_HITS)
    context, sources, meta = build_research_context(
        query="综述多模态检索的方法",
        retriever=retriever,
        s=_settings(),
        literature_collections=["lit"],
        research_task="review",
        start_idx=1,
    )

    call = retriever.calls[0]
    assert call["collection"] == ["lit"]
    assert call["top_k"] == 20  # research_max_literature 默认 20
    assert call["query"] == "综述多模态检索的方法"

    assert LITERATURE_BLOCK_HEAD in context
    assert "[1]" in context and "[2]" in context
    assert meta["literature_count"] == 2
    assert meta["literature_indices"] == {1, 2}
    assert meta["citation_support_ready"] is True
    assert meta["literature_scope"] == ["lit"]
    assert meta["research_task"] == "review"
    assert meta["task_label"] == RESEARCH_TASK_LABELS["review"]
    assert meta["topic_injected"] is False

    for src in sources:
        assert src.literature is True
        assert src.source_type == "local"
        assert src.literature is True


def test_build_top_k_follows_research_max_literature() -> None:
    """top_k 跟随 research_max_literature 配置。"""
    retriever = _retriever(LIT_HITS)
    build_research_context(
        query="对比", retriever=retriever, s=_settings(research_max_literature=5),
        literature_collections=["lit"],
    )
    assert retriever.calls[0]["top_k"] == 5


def test_build_injects_graph_topic_block() -> None:
    """图谱 topic 注入：概念/实体 + 关联文献结构正确，状态帧含图谱提示。"""
    statuses: list[str] = []
    topic = {
        "topic_edges": [
            {"from": "多模态检索", "from_type": "topic", "relation": "uses", "to": "稠密向量", "to_type": "concept"},
            {"from": "多模态检索", "from_type": "topic", "relation": "uses", "to": "稠密向量", "to_type": "concept"},  # 重复，应去重
            {"from": "稠密向量", "from_type": "concept", "relation": "related_to", "to": "BM25", "to_type": "concept"},
        ],
        "related_sources": [
            {"source": r"E:\lit\paper_a.pdf", "title": "多模态检索综述", "chunk_count": 3},
            {"source": r"E:\lit\paper_b.pdf", "title": "向量检索实践", "chunk_count": 1},
        ],
        "topic_label": "多模态检索",
    }
    retriever = _retriever(LIT_HITS)
    context, sources, meta = build_research_context(
        query="综述多模态检索",
        retriever=retriever,
        s=_settings(),
        literature_collections=["lit"],
        graph_store=_FakeGraphStore(topic=topic),
        on_status=statuses.append,
    )

    assert "【知识图谱 Topic 层：多模态检索】" in context
    assert "多模态检索 --[uses]--> 稠密向量" in context
    assert context.count("多模态检索 --[uses]--> 稠密向量") == 1  # 去重
    assert "关联文献：多模态检索综述；向量检索实践" in context
    # 组装顺序：文献块在前，图谱块在后
    assert context.index(LITERATURE_BLOCK_HEAD) < context.index("知识图谱 Topic 层")
    assert meta["topic_injected"] is True
    assert meta["topic_label"] == "多模态检索"
    assert any("图谱 Topic" in s for s in statuses)


def test_build_skips_graph_block_when_topic_empty() -> None:
    """图谱无命中 → 不注入图谱块，并给出可见的「未发现关联」状态。"""
    statuses: list[str] = []
    context, sources, meta = build_research_context(
        query="综述",
        retriever=_retriever(LIT_HITS),
        s=_settings(),
        literature_collections=["lit"],
        graph_store=_FakeGraphStore(topic={"topic_edges": [], "related_sources": [], "topic_label": ""}),
        on_status=statuses.append,
    )
    assert "知识图谱 Topic 层" not in context
    assert meta["topic_injected"] is False
    assert any("未发现相关 topic 关联" in s for s in statuses)


def test_build_graph_error_does_not_block_context() -> None:
    """图谱查询异常 → 沿用 try/except 跳过模式，文献块照常返回。"""
    statuses: list[str] = []
    context, sources, meta = build_research_context(
        query="综述",
        retriever=_retriever(LIT_HITS),
        s=_settings(),
        literature_collections=["lit"],
        graph_store=_FakeGraphStore(raise_error=True),
        on_status=statuses.append,
    )
    assert LITERATURE_BLOCK_HEAD in context
    assert meta["citation_support_ready"] is True
    assert any("未发现相关 topic 关联" in s for s in statuses)


def test_build_survives_zero_hits() -> None:
    """集合存在但零命中 → 空上下文、citation_support_ready=False，状态显式标注。"""
    statuses: list[str] = []
    context, sources, meta = build_research_context(
        query="综述一个不存在的主题",
        retriever=_retriever([]),
        s=_settings(),
        literature_collections=["lit"],
        on_status=statuses.append,
    )
    assert context == ""
    assert sources == []
    assert meta["citation_support_ready"] is False
    assert meta["literature_count"] == 0
    assert any("未命中任何切片" in s for s in statuses)


def test_build_retriever_error_raises_scope_error() -> None:
    """检索异常 → LiteratureScopeError（rag 侧显式降级，不透传原始异常）。"""
    with pytest.raises(LiteratureScopeError) as exc_info:
        build_research_context(
            query="综述",
            retriever=_retriever(raise_error=True),
            s=_settings(),
            literature_collections=["lit"],
        )
    assert "检索失败" in str(exc_info.value)


def test_build_exposes_retrieval_degradation() -> None:
    """检索降级 → meta 记录原因并通过状态帧告知用户（降级可见硬约束）。"""
    statuses: list[str] = []
    _, _, meta = build_research_context(
        query="综述",
        retriever=_retriever(LIT_HITS, degraded=True),
        s=_settings(),
        literature_collections=["lit"],
        on_status=statuses.append,
    )
    assert meta["degraded"] is True
    assert "纯 BM25" in str(meta["degraded_reason"])
    assert any("文献检索降级" in s for s in statuses)


def test_build_continues_source_indexing_from_start_idx() -> None:
    """start_idx 续编：与主链路既有 sources 编号不冲突。"""
    _, sources, meta = build_research_context(
        query="综述",
        retriever=_retriever(LIT_HITS),
        s=_settings(),
        literature_collections=["lit"],
        start_idx=4,
    )
    assert meta["literature_indices"] == {4, 5}
    assert [src.index for src in sources] == [4, 5]


def test_build_without_graph_store_is_fine() -> None:
    """graph_store 为 None → 跳过图谱注入，不报错。"""
    context, sources, meta = build_research_context(
        query="综述",
        retriever=_retriever(LIT_HITS),
        s=_settings(),
        literature_collections=["lit"],
        graph_store=None,
    )
    assert "知识图谱 Topic 层" not in context
    assert meta["topic_injected"] is False
    assert meta["citation_support_ready"] is True


# ---------- topic 块渲染 ----------


def test_format_topic_block_dedups_and_caps_edges() -> None:
    """topic_edges 去重并封顶 30 条（design C-3 步骤 3）。"""
    edges = [
        {
            "from": f"实体{i}",
            "from_type": "concept",
            "relation": "related_to",
            "to": f"关联{i}",
            "to_type": "concept",
        }
        for i in range(40)
    ]
    edges.append(dict(edges[0]))  # 重复项
    block = _format_topic_block("话题", {"topic_edges": edges, "related_sources": []})
    rendered = [line for line in block.splitlines() if line.startswith("概念/实体：")]
    assert len(rendered) == 1
    item_count = rendered[0].split("：", 1)[1].count("--[")
    assert item_count == 30


def test_format_topic_block_handles_missing_edges_gracefully() -> None:
    """空 topic / 缺字段 → 仍返回合法块头，不抛错。"""
    block = _format_topic_block("", {"topic_edges": [], "related_sources": []})
    assert block.startswith("【知识图谱 Topic 层：未命名 topic】")


def test_topic_context_path_uses_settings_depth(tmp_path: Path) -> None:
    """图谱扩散层数跟随 research_graph_topic_depth 配置。"""

    class _RecordingGraphStore:
        def find_entities_by_keyword(self, keyword: str, limit: int = 5) -> list[dict[str, Any]]:
            return []

        def get_topic_context(self, **kwargs: Any) -> dict[str, Any]:
            self.last_kwargs = kwargs
            return {"topic_edges": [], "related_sources": [], "topic_label": ""}

    graph = _RecordingGraphStore()  # type: ignore[assignment]
    build_research_context(
        query="综述",
        retriever=_retriever(LIT_HITS),
        s=_settings(research_graph_topic_depth=3),
        literature_collections=["lit"],
        graph_store=graph,
    )
    assert graph.last_kwargs["depth"] == 3
    assert graph.last_kwargs["collections"] == ["lit"]
    assert graph.last_kwargs["limit_entities"] == 30