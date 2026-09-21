"""父子/同标题上下文 + docs 档案收短。"""

from pathlib import Path

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.config import Settings, profile_persona_hint
from doc2mind.core.rag import _format_context
from doc2mind.core.retriever.search import SearchHit, StoredChunkMeta
from doc2mind.core.store.sqlite_vec import StoredDocument, VectorStore


def _store_with_chapter(tmp_path: Path) -> VectorStore:
    store = VectorStore(tmp_path / "t.db", embedding_dim=4, bm25_jieba_enabled=True)
    store.open()
    contents = [
        "选型总览：先定负载再定缸径。",
        "缸径计算：F=P×A，考虑负载率。",
        "安装注意：气管走向与接头密封。",
    ]
    chunks = [
        Chunk(content=c, tokens=10, metadata={"chunk_index": i, "heading": "气缸选型"})
        for i, c in enumerate(contents)
    ]
    # 第三块改无 heading
    chunks[2] = Chunk(content=contents[2], tokens=10, metadata={"chunk_index": 2})
    store.replace_document(
        StoredDocument(
            id="d1", source="a.md", collection="c", format="md",
            file_hash="h", size_bytes=100, page_count=None, chunk_count=3,
            created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00",
        ),
        chunks=chunks,
        embeddings=[[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]],
    )
    return store


def _hit(store: VectorStore, chunk_id: int) -> SearchHit:
    st = store.get_chunks([chunk_id])[0]
    meta = StoredChunkMeta(
        id=st.id, content=st.content, source=st.source, format=st.format,
        doc_type=st.doc_type, page=st.page, heading=st.heading, tokens=st.tokens,
        chunk_index=st.chunk_index, collection=st.collection, extra=st.extra_metadata,
    )
    return SearchHit(
        chunk=meta, score=0.03, match_type="vector",
        vector_score=0.8, bm25_score=0.0, rank=0,
    )


class TestParentHeadingContext:
    def test_heading_mode_pulls_siblings(self, tmp_path):
        store = _store_with_chapter(tmp_path)
        try:
            # chunk 0 与 1 同 heading「气缸选型」
            ctx, sources = _format_context(
                [_hit(store, 1)], store=store, neighbor_window=0, parent_mode="heading"
            )
            assert "选型总览" in ctx
            assert "安装注意" not in ctx  # 无 heading，不应并入
            assert "同章节上下文" in ctx
            assert len(sources) == 1  # 引用仍只指向命中块
        finally:
            store.close()

    def test_heading_falls_back_to_neighbor(self, tmp_path):
        store = _store_with_chapter(tmp_path)
        try:
            # chunk 2 无 heading → 回退邻块
            ctx, _ = _format_context(
                [_hit(store, 2)], store=store, neighbor_window=1, parent_mode="heading"
            )
            assert "缸径计算" in ctx
        finally:
            store.close()

    def test_off_mode_no_extra(self, tmp_path):
        store = _store_with_chapter(tmp_path)
        try:
            # 命中块自身内容会出现在上下文中；off 只保证不并入兄弟/邻块
            ctx, _ = _format_context(
                [_hit(store, 1)], store=store, neighbor_window=1, parent_mode="off"
            )
            assert "相邻上下文" not in ctx
            assert "同章节上下文" not in ctx
            assert "缸径计算" not in ctx  # 同 heading 兄弟块不应并入
            assert "安装注意" not in ctx  # 邻块不应并入
        finally:
            store.close()


class TestDocsProfilePrompt:
    def test_docs_discourages_empty_insight_and_default_actions(self):
        hint = profile_persona_hint("docs")
        assert "架构洞察" in hint
        assert "ACTIONS" in hint
        assert "出处" in hint or "[n]" in hint

    def test_notes_still_forbids_actions(self):
        hint = profile_persona_hint("notes")
        assert "ACTIONS" in hint or "行动" in hint

    def test_settings_default_parent_mode(self):
        s = Settings()
        assert s.parent_context_mode == "neighbor"
