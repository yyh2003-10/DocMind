"""AI 整理引擎（curator）测试 — FakeLLM + 确定性向量 + 真实临时库。

FakeLLM 按提示词特征返回预设 JSON（enrich/categorize/dedup/consolidate 四类），
DummyEmbedder 按关键词生成 one-hot 向量（同关键词余弦距离 0 → 相似分 1.0，
不同关键词距离 1.0 → 相似分 0.5），使聚类/去重行为完全可控。
"""

from __future__ import annotations

from typing import Any

import pytest

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.config import Settings
from doc2mind.core.curator import (
    VALID_ACTIONS,
    CurateReport,
    categorize_document,
    consolidate_notes,
    curate,
    enrich_document,
    find_duplicates,
)
from doc2mind.core.llm.base import LLMClient
from doc2mind.core.store.sqlite_vec import StoredDocument, VectorStore

EMBEDDING_DIM = 8

_KEYWORDS = {
    "alpha": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "beta": [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "gamma": [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
}


def _vec_for(text: str) -> list[float]:
    for kw, vec in _KEYWORDS.items():
        if kw in (text or "").lower():
            return list(vec)
    return [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]


class DummyEmbedder:
    """确定性嵌入器：按关键词出 one-hot 向量。"""

    dimension = EMBEDDING_DIM

    def embed_query(self, text: str) -> list[float]:
        return _vec_for(text)

    def embed_texts(self, texts: list[str]) -> Any:
        return iter([_vec_for(t) for t in texts])

    def embed(self, chunks: list[Chunk]) -> Any:
        return iter([_vec_for(c.content) for c in chunks])


class FakeLLM(LLMClient):
    """按 user 消息内容特征返回预设 JSON 的假客户端。"""

    def __init__(self, fn) -> None:
        self._fn = fn
        self.calls: list[list[dict]] = []

    @property
    def model_name(self) -> str:
        return "fake-model"

    @property
    def provider(self) -> str:
        return "fake"

    def _do_chat(self, messages: list[dict], temperature: float | None = None,
                 max_tokens: int | None = None) -> str:
        self.calls.append(messages)
        return self._fn(messages)


def _default_reply(messages: list[dict]) -> str:
    user = messages[-1]["content"]
    if "文档内容（可能截断）" in user:
        return '{"title": "测试标题", "summary": "这是摘要。", "tags": ["bug", "sqlite", "修复"]}'
    if "现有集合列表" in user:
        return '{"collection": "auto-col", "is_new": true, "reason": "新主题"}'
    if "【A】" in user:
        return '{"is_duplicate": true, "keep": "A", "reason": "A 更完整"}'
    if "相关经验笔记" in user:
        return '{"title": "蒸馏笔记", "content": "# 蒸馏笔记\\n\\n合并后的要点。"}'
    return '{"ok": true}'


def _mkllm(fn=_default_reply) -> FakeLLM:
    return FakeLLM(fn)


def _add_doc(
    store: VectorStore, doc_id: str, collection: str, source: str,
    contents: list[str],
) -> None:
    store.upsert_document(StoredDocument(
        id=doc_id, source=source, collection=collection, format="md",
        file_hash=f"hash-{doc_id}", size_bytes=100, page_count=None,
        chunk_count=len(contents),
        created_at="2026-01-01T00:00:00+08:00",
        updated_at="2026-01-01T00:00:00+08:00",
    ))
    store.insert_chunks(
        document_id=doc_id, collection=collection, source=source, fmt="md",
        chunks=[Chunk(content=c, tokens=len(c), metadata={"chunk_index": i})
                for i, c in enumerate(contents)],
        embeddings=[_vec_for(c) for c in contents],
    )


@pytest.fixture()
def store(tmp_path) -> Any:
    vs = VectorStore(tmp_path / "curate.db", embedding_dim=EMBEDDING_DIM)
    try:
        vs.open()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"sqlite-vec 不可用: {e}")
    yield vs
    vs.close()


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        db_path=tmp_path / "curate.db",
        auto_curate_on_ingest=False,  # 单元测试显式调用 curator，不开自动
        curate_dedup_score_threshold=0.85,
        curate_max_chars=8000,
    )


# ----------------------------------------------------------------------
# enrich
# ----------------------------------------------------------------------
class TestEnrich:
    def test_writes_meta(self, store) -> None:
        _add_doc(store, "d-1", "default", "note:alpha 笔记", ["alpha electron 内容"])
        item = enrich_document(store, _mkllm(), store.get_document_by_id("d-1"),
                               dry_run=False)
        assert item["status"] == "enriched"
        doc = store.get_document_by_id("d-1")
        assert doc is not None
        assert doc.title == "测试标题"
        assert doc.summary == "这是摘要。"
        assert doc.tags == ["bug", "sqlite", "修复"]
        assert doc.enriched_at is not None

    def test_dry_run_no_write(self, store) -> None:
        _add_doc(store, "d-1", "default", "note:alpha 笔记", ["alpha electron 内容"])
        item = enrich_document(store, _mkllm(), store.get_document_by_id("d-1"),
                               dry_run=True)
        assert item["status"] == "planned"
        doc = store.get_document_by_id("d-1")
        assert doc is not None and doc.title is None

    def test_bad_json_skipped(self, store) -> None:
        _add_doc(store, "d-1", "default", "note:alpha 笔记", ["alpha 内容"])
        llm = _mkllm(lambda messages: "抱歉我无法输出 JSON~~~")
        item = enrich_document(store, llm, store.get_document_by_id("d-1"),
                               dry_run=False)
        assert item["status"] == "skipped"
        assert "无法解析" in item["reason"]
        # 重试过一次（2 次调用）
        assert len(llm.calls) == 2

    def test_code_fence_json_parsed(self, store) -> None:
        _add_doc(store, "d-1", "default", "note:alpha 笔记", ["alpha 内容"])
        llm = _mkllm(lambda m: '```json\n{"title": "栅栏标题", "summary": "s", "tags": ["t"]}\n```')
        item = enrich_document(store, llm, store.get_document_by_id("d-1"),
                               dry_run=False)
        assert item["status"] == "enriched"
        assert item["title"] == "栅栏标题"

    def test_llm_none_skipped(self, store) -> None:
        _add_doc(store, "d-1", "default", "note:alpha 笔记", ["alpha 内容"])
        item = enrich_document(store, None, store.get_document_by_id("d-1"),
                               dry_run=False)
        assert item["status"] == "skipped"

    def test_already_enriched_skips_unless_force(self, store) -> None:
        _add_doc(store, "d-1", "default", "note:alpha 笔记", ["alpha 内容"])
        enrich_document(store, _mkllm(), store.get_document_by_id("d-1"), dry_run=False)
        doc = store.get_document_by_id("d-1")
        assert doc is not None
        item = enrich_document(store, _mkllm(), doc, dry_run=False)
        assert item["status"] == "skipped"
        item2 = enrich_document(store, _mkllm(), doc, dry_run=False, force=True)
        assert item2["status"] == "enriched"


# ----------------------------------------------------------------------
# categorize
# ----------------------------------------------------------------------
class TestCategorize:
    def test_move_to_existing(self, store) -> None:
        store.ensure_collection("bugs")
        _add_doc(store, "d-1", "default", "note:alpha 笔记", ["alpha electron 内容"])
        llm = _mkllm(lambda m: '{"collection": "bugs", "is_new": false, "reason": "r"}')
        item = categorize_document(store, llm, store.get_document_by_id("d-1"),
                                   dry_run=False)
        assert item["status"] == "moved"
        assert item["to"] == "bugs"
        doc = store.get_document_by_id("d-1")
        assert doc is not None and doc.collection == "bugs"

    def test_new_collection_slugified(self, store) -> None:
        _add_doc(store, "d-1", "default", "note:alpha 笔记", ["alpha electron 内容"])
        llm = _mkllm(lambda m: '{"collection": "Bug Fixes!!", "is_new": true, "reason": "r"}')
        item = categorize_document(store, llm, store.get_document_by_id("d-1"),
                                   dry_run=False)
        assert item["status"] == "moved"
        assert item["new_collection"] is True
        assert item["to"] == "bug-fixes"
        # 新集合已登记且文档已移动
        assert "bug-fixes" in store.get_stats().collections
        doc = store.get_document_by_id("d-1")
        assert doc is not None and doc.collection == "bug-fixes"

    def test_same_collection_unchanged(self, store) -> None:
        _add_doc(store, "d-1", "bugs", "note:alpha 笔记", ["alpha electron 内容"])
        llm = _mkllm(lambda m: '{"collection": "bugs", "is_new": false, "reason": "r"}')
        item = categorize_document(store, llm, store.get_document_by_id("d-1"),
                                   dry_run=False)
        assert item["status"] == "unchanged"

    def test_placeholder_skipped(self, store) -> None:
        store.ensure_collection("empty-col")
        placeholder = [
            d for d in store.list_documents(collection="empty-col")
            if d.source == "__collection_placeholder__"
        ][0]
        item = categorize_document(store, _mkllm(), placeholder, dry_run=False)
        assert item["status"] == "skipped"

    def test_dry_run_planned(self, store) -> None:
        store.ensure_collection("bugs")
        _add_doc(store, "d-1", "default", "note:alpha 笔记", ["alpha electron 内容"])
        llm = _mkllm(lambda m: '{"collection": "bugs", "is_new": false, "reason": "r"}')
        item = categorize_document(store, llm, store.get_document_by_id("d-1"),
                                   dry_run=True)
        assert item["status"] == "planned"
        doc = store.get_document_by_id("d-1")
        assert doc is not None and doc.collection == "default"


# ----------------------------------------------------------------------
# dedup
# ----------------------------------------------------------------------
class TestDedup:
    def _setup_pair(self, store) -> None:
        # 两条 alpha 关键词笔记（向量完全同向 → 相似分 1.0）
        _add_doc(store, "d-a", "default", "note:alpha 原始", ["alpha root cause fix"])
        _add_doc(store, "d-b", "default", "note:alpha 重复", ["alpha root cause fix again"])
        # 一条 beta 笔记（不同方向 → 不应命中）
        _add_doc(store, "d-c", "default", "note:beta 无关", ["beta other topic"])

    def test_duplicate_merged(self, store, settings) -> None:
        self._setup_pair(store)
        results = find_duplicates(
            store, DummyEmbedder(), _mkllm(), "default",
            threshold=settings.curate_dedup_score_threshold, dry_run=False,
        )
        merged = [r for r in results if r["status"] == "merged"]
        assert len(merged) == 1
        assert merged[0]["keep"]["source"] == "note:alpha 原始"
        assert merged[0]["remove"]["source"] == "note:alpha 重复"
        # 冗余篇已删除，保留篇与无关篇仍在
        assert store.get_document_by_id("d-b") is None
        assert store.get_document_by_id("d-a") is not None
        assert store.get_document_by_id("d-c") is not None

    def test_dry_run_keeps_both(self, store, settings) -> None:
        self._setup_pair(store)
        results = find_duplicates(
            store, DummyEmbedder(), _mkllm(), "default",
            threshold=settings.curate_dedup_score_threshold, dry_run=True,
        )
        assert [r["status"] for r in results if r["status"] == "planned"]
        assert store.get_document_by_id("d-a") is not None
        assert store.get_document_by_id("d-b") is not None

    def test_not_duplicate_kept(self, store, settings) -> None:
        self._setup_pair(store)
        llm = _mkllm(lambda m: '{"is_duplicate": false, "keep": "A", "reason": "主题不同"}')
        results = find_duplicates(
            store, DummyEmbedder(), llm, "default",
            threshold=settings.curate_dedup_score_threshold, dry_run=False,
        )
        assert all(r["status"] == "not_duplicate" for r in results)
        assert store.get_document_by_id("d-a") is not None
        assert store.get_document_by_id("d-b") is not None

    def test_keep_b_choice_respected(self, store, settings) -> None:
        self._setup_pair(store)
        llm = _mkllm(lambda m: '{"is_duplicate": true, "keep": "B", "reason": "B 更全"}')
        results = find_duplicates(
            store, DummyEmbedder(), llm, "default",
            threshold=settings.curate_dedup_score_threshold, dry_run=False,
        )
        merged = [r for r in results if r["status"] == "merged"]
        assert len(merged) == 1
        assert merged[0]["keep"]["source"] == "note:alpha 重复"
        assert store.get_document_by_id("d-a") is None


# ----------------------------------------------------------------------
# consolidate
# ----------------------------------------------------------------------
class TestConsolidate:
    def _setup_notes(self, store, n: int = 4) -> None:
        for i in range(n):
            _add_doc(store, f"note-{i}", "default", f"note:alpha 经验{i}",
                     [f"alpha experience number {i} root cause"])

    def test_execute_merges_cluster(self, store, settings, monkeypatch) -> None:
        # ingest_text 内部会 get_embedder(settings) 加载真模型，换成确定性嵌入器
        monkeypatch.setattr("doc2mind.core.pipeline.get_embedder", lambda s: DummyEmbedder())
        self._setup_notes(store, 4)

        results = consolidate_notes(
            store, DummyEmbedder(), _mkllm(), settings, "default", dry_run=False,
        )
        assert len(results) == 1
        item = results[0]
        assert item["status"] == "consolidated"
        assert item["cluster_size"] == 4
        # 蒸馏笔记已入库，原 4 条已删除
        new_doc = store.get_document_by_id(item["new_document_id"])
        assert new_doc is not None
        assert new_doc.title == "蒸馏笔记"
        assert new_doc.tags == ["distilled"]
        for i in range(4):
            assert store.get_document_by_id(f"note-{i}") is None

    def test_dry_run_preview_only(self, store, settings) -> None:
        self._setup_notes(store, 4)
        results = consolidate_notes(
            store, DummyEmbedder(), _mkllm(), settings, "default", dry_run=True,
        )
        assert len(results) == 1
        assert results[0]["status"] == "planned"
        assert results[0]["title"] == "蒸馏笔记"
        for i in range(4):
            assert store.get_document_by_id(f"note-{i}") is not None

    def test_below_min_cluster_ignored(self, store, settings) -> None:
        self._setup_notes(store, 2)
        results = consolidate_notes(
            store, DummyEmbedder(), _mkllm(), settings, "default", dry_run=False,
        )
        assert results == []
        for i in range(2):
            assert store.get_document_by_id(f"note-{i}") is not None


# ----------------------------------------------------------------------
# curate 汇总
# ----------------------------------------------------------------------
class TestCurateOrchestration:
    def test_llm_none_reports_skipped(self, store, settings) -> None:
        _add_doc(store, "d-1", "default", "note:alpha 笔记", ["alpha 内容"])
        report = curate(store, DummyEmbedder(), None, settings, collection="default")
        assert isinstance(report, CurateReport)
        assert report.skipped and "未配置 LLM" in report.skipped[0]
        doc = store.get_document_by_id("d-1")
        assert doc is not None and doc.title is None  # 零写入

    def test_unknown_action_noted(self, store, settings) -> None:
        report = curate(store, DummyEmbedder(), _mkllm(), settings,
                        collection="default", actions=["enrich", "bogus"])
        assert report.actions == ["enrich"]
        assert any("未知动作" in e for e in report.errors)

    def test_full_dry_run_zero_writes(self, store, settings) -> None:
        _add_doc(store, "d-1", "default", "note:alpha 笔记", ["alpha 内容"])
        before = store.get_document_by_id("d-1")
        report = curate(store, DummyEmbedder(), _mkllm(), settings,
                        collection="default",
                        actions=list(VALID_ACTIONS),
                        dry_run=True)
        assert report.dry_run is True
        assert report.actions == list(VALID_ACTIONS)
        after = store.get_document_by_id("d-1")
        assert after is not None
        assert after.title == before.title  # None
        assert after.collection == "default"
        assert after.enriched_at is None

    def test_report_serializable(self, store, settings) -> None:
        _add_doc(store, "d-1", "default", "note:alpha 笔记", ["alpha 内容"])
        report = curate(store, DummyEmbedder(), _mkllm(), settings,
                        collection="default", actions=["enrich"])
        d = report.to_dict()
        assert d["dry_run"] is True
        assert d["actions"] == ["enrich"]
        assert isinstance(d["enriched"], list) and d["enriched"]
        assert d["elapsed_ms"] >= 0

    def test_progress_callback_called(self, store, settings) -> None:
        _add_doc(store, "d-1", "default", "note:alpha 笔记", ["alpha 内容"])
        _add_doc(store, "d-2", "default", "note:beta 笔记", ["beta 内容"])
        seen: list[tuple[int, int]] = []
        curate(store, DummyEmbedder(), _mkllm(), settings, collection="default",
               actions=["enrich"], progress=lambda done, total: seen.append((done, total)))
        assert seen and seen[-1] == (2, 2)


# ----------------------------------------------------------------------
# pipeline 入库自动整理（_auto_curate_after_ingest 集成）
# ----------------------------------------------------------------------
class TestPipelineAutoCurate:
    def _patch_llm(self, monkeypatch, fn=_default_reply) -> FakeLLM:
        llm = FakeLLM(fn)

        def _fake_get_llm_client(settings=None):
            return llm

        monkeypatch.setattr(
            "doc2mind.core.llm.factory.get_llm_client", _fake_get_llm_client
        )
        return llm

    def _settings(self, tmp_path, **kw) -> Settings:
        defaults: dict[str, Any] = {
            "db_path": tmp_path / "curate.db",
            "auto_curate_on_ingest": True,
        }
        defaults.update(kw)
        return Settings(**defaults)

    def test_ingest_text_auto_categorize(self, tmp_path, monkeypatch) -> None:
        """collection 不传 → 入默认集合后 AI 自动打标并归类到新集合。"""
        from doc2mind.core.pipeline import ingest_text

        monkeypatch.setattr(
            "doc2mind.core.pipeline.get_embedder", lambda s: DummyEmbedder()
        )
        self._patch_llm(monkeypatch)
        settings = self._settings(tmp_path)

        store = VectorStore(settings.db_path, embedding_dim=EMBEDDING_DIM)
        store.open()
        try:
            result = ingest_text(
                text="alpha electron 经验内容",
                title="alpha 经验",
                settings=settings,
                store=store,
            )
            assert result.status == "ingested"
            # 最终集合是 AI 归类结果（_default_reply 的 categorize → auto-col）
            assert result.collection == "auto-col"
            assert result.curation is not None
            assert result.curation["enrich"]["status"] == "enriched"
            assert result.curation["categorize"]["status"] == "moved"
            doc = store.get_document_by_id(result.document_id)
            assert doc is not None
            assert doc.collection == "auto-col"
            assert doc.title == "测试标题"
            assert "auto-col" in store.get_stats().collections
        finally:
            store.close()

    def test_explicit_collection_no_categorize(self, tmp_path, monkeypatch) -> None:
        from doc2mind.core.pipeline import ingest_text

        monkeypatch.setattr(
            "doc2mind.core.pipeline.get_embedder", lambda s: DummyEmbedder()
        )
        llm = self._patch_llm(monkeypatch)
        settings = self._settings(tmp_path)

        store = VectorStore(settings.db_path, embedding_dim=EMBEDDING_DIM)
        store.open()
        try:
            result = ingest_text(
                text="alpha electron 经验内容",
                title="alpha 经验",
                collection="my-col",
                settings=settings,
                store=store,
            )
            assert result.status == "ingested"
            assert result.collection == "my-col"  # 显式集合不被移动
            assert result.curation is not None
            assert "categorize" not in result.curation  # 只 enrich
            # 只有 enrich 一次 LLM 调用
            assert len(llm.calls) == 1
        finally:
            store.close()

    def test_llm_failure_does_not_break_ingest(self, tmp_path, monkeypatch) -> None:
        from doc2mind.core.pipeline import ingest_text

        monkeypatch.setattr(
            "doc2mind.core.pipeline.get_embedder", lambda s: DummyEmbedder()
        )

        def _broken_get_llm_client(settings=None):
            from doc2mind.core.llm.base import LLMError

            raise LLMError("api key 缺失")

        monkeypatch.setattr(
            "doc2mind.core.llm.factory.get_llm_client", _broken_get_llm_client
        )
        settings = self._settings(tmp_path)

        store = VectorStore(settings.db_path, embedding_dim=EMBEDDING_DIM)
        store.open()
        try:
            result = ingest_text(
                text="alpha electron 经验内容",
                title="alpha 经验",
                settings=settings,
                store=store,
            )
            # LLM 挂了入库依然成功，curation=None，留在默认集合
            assert result.status == "ingested"
            assert result.curation is None
            assert result.collection == "default"
        finally:
            store.close()

    def test_auto_curate_disabled(self, tmp_path, monkeypatch) -> None:
        from doc2mind.core.pipeline import ingest_text

        monkeypatch.setattr(
            "doc2mind.core.pipeline.get_embedder", lambda s: DummyEmbedder()
        )
        llm = self._patch_llm(monkeypatch)
        settings = self._settings(tmp_path, auto_curate_on_ingest=False)

        store = VectorStore(settings.db_path, embedding_dim=EMBEDDING_DIM)
        store.open()
        try:
            result = ingest_text(
                text="alpha electron 经验内容",
                title="alpha 经验",
                settings=settings,
                store=store,
            )
            assert result.status == "ingested"
            assert result.curation is None
            assert result.collection == "default"
            assert llm.calls == []
        finally:
            store.close()
