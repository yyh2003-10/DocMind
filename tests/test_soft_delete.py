"""软删除（trash 表 + deleted_at 列 + restore/purge）测试。

L2 自动化「dedup/consolidate 静默跑」的安全网前提：用户误删后 30 天内可恢复。
本测试覆盖：软删除语义、恢复语义、列表/统计过滤、trash 表内容、purge GC 行为。
"""

from __future__ import annotations

import datetime as _dt

import pytest

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.store.sqlite_vec import StoredDocument, VectorStore

EMBEDDING_DIM = 4


def _make_doc(
    doc_id: str = "doc1",
    source: str = "alpha.md",
    collection: str = "default",
) -> StoredDocument:
    return StoredDocument(
        id=doc_id,
        source=source,
        collection=collection,
        format="md",
        file_hash=doc_id + "_hash",
        size_bytes=100,
        page_count=None,
        chunk_count=0,
        created_at="2026-09-01T00:00:00+08:00",
        updated_at="2026-09-01T00:00:00+08:00",
    )


def _ingest_simple(store: VectorStore, doc_id: str, source: str, text: str) -> None:
    """建文档 + 1 个 chunk + 1 个向量。"""
    store.ensure_collection("default")
    store.upsert_document(_make_doc(doc_id=doc_id, source=source))
    chunks = [Chunk(content=text, tokens=len(text), metadata={"chunk_index": 0})]
    embeddings = [[0.1] * EMBEDDING_DIM]
    store.insert_chunks(doc_id, "default", source, "md", chunks, embeddings)


@pytest.fixture()
def store(tmp_path):
    vs = VectorStore(tmp_path / "soft.db", embedding_dim=EMBEDDING_DIM)
    try:
        vs.open()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"sqlite-vec 不可用: {e}")
    yield vs
    vs.close()


# --- 软删除基础语义 ---


def test_soft_delete_writes_trash_and_marks_deleted_at(store):
    _ingest_simple(store, "doc1", "alpha.md", "hello world")
    n = store.delete_document("doc1")
    assert n == 1  # 删了 1 个 chunk

    # documents 行还在，deleted_at 非空
    row = store._conn.execute(
        "SELECT deleted_at FROM documents WHERE id = ?", ("doc1",)
    ).fetchone()
    assert row[0] is not None

    # trash 表有 1 行
    trash = store.list_trash()
    assert len(trash) == 1
    assert trash[0]["document_id"] == "doc1"
    assert trash[0]["source"] == "alpha.md"
    assert trash[0]["purged_at"] is None  # 还在保留期


def test_soft_delete_idempotent_returns_minus_two(store):
    _ingest_simple(store, "doc1", "alpha.md", "hello")
    assert store.delete_document("doc1") == 1
    # 第二次删 = 已处于软删除态 → -2
    assert store.delete_document("doc1") == -2
    # 第三次仍然 -2（幂等）
    assert store.delete_document("doc1") == -2


def test_soft_delete_nonexistent_returns_minus_one(store):
    assert store.delete_document("nope") == -1


def test_soft_delete_removes_chunks_and_vectors_but_keeps_doc(store):
    _ingest_simple(store, "doc1", "alpha.md", "hello")
    store.delete_document("doc1")

    # chunks_meta / vec_chunks 已物理删
    cm = store._conn.execute(
        "SELECT COUNT(*) FROM chunks_meta WHERE document_id = ?", ("doc1",)
    ).fetchone()[0]
    assert cm == 0

    vc = store._conn.execute(
        "SELECT COUNT(*) FROM vec_chunks WHERE id IN"
        " (SELECT id FROM chunks_meta WHERE document_id = ?)",
        ("doc1",),
    ).fetchone()[0]
    assert vc == 0


# --- 列表/统计过滤 ---


def test_list_documents_excludes_soft_deleted(store):
    _ingest_simple(store, "doc1", "alpha.md", "a")
    _ingest_simple(store, "doc2", "beta.md", "b")

    store.delete_document("doc1")

    docs = store.list_documents()
    ids = [d.id for d in docs]
    assert "doc2" in ids
    assert "doc1" not in ids


def test_count_documents_excludes_soft_deleted(store):
    _ingest_simple(store, "doc1", "alpha.md", "a")
    _ingest_simple(store, "doc2", "beta.md", "b")
    assert store.count_documents() == 2

    store.delete_document("doc1")
    assert store.count_documents() == 1


def test_get_stats_excludes_soft_deleted(store):
    _ingest_simple(store, "doc1", "alpha.md", "a")
    _ingest_simple(store, "doc2", "beta.md", "b")
    store.delete_document("doc1")

    stats = store.get_stats()
    assert stats.total_documents == 1
    # default 集合 doc_count 应该是 1
    assert stats.collections["default"][0] == 1


def test_get_document_returns_none_for_soft_deleted(store):
    _ingest_simple(store, "doc1", "alpha.md", "a")
    store.delete_document("doc1")
    assert store.get_document_by_id("doc1") is None


def test_find_by_hash_excludes_soft_deleted(store):
    """增量摄入：file_hash 命中应忽略软删除文档（否则会跳过重摄入）。"""
    _ingest_simple(store, "doc1", "alpha.md", "a")
    store.delete_document("doc1")

    # 用同 file_hash 再查 → 应返回 None（不算已存在）
    assert store.find_document_id_by_hash("doc1_hash", "default") is None


# --- 恢复语义 ---


def test_restore_document_clears_deleted_at(store):
    _ingest_simple(store, "doc1", "alpha.md", "a")
    store.delete_document("doc1")
    assert store.restore_document("doc1") is True

    # documents 行的 deleted_at 重新为 NULL
    row = store._conn.execute(
        "SELECT deleted_at FROM documents WHERE id = ?", ("doc1",)
    ).fetchone()
    assert row[0] is None

    # 文档重新出现在 list_documents
    docs = store.list_documents()
    assert any(d.id == "doc1" for d in docs)

    # 但 chunks_meta 仍为空（恢复不重建嵌入，文档元数据回来了但检索不可用）
    cm = store._conn.execute(
        "SELECT COUNT(*) FROM chunks_meta WHERE document_id = ?", ("doc1",)
    ).fetchone()[0]
    assert cm == 0


def test_restore_nonexistent_returns_false(store):
    assert store.restore_document("nope") is False


def test_restore_already_active_returns_false(store):
    """恢复一个未软删除的文档应返回 False（不是错误）。"""
    _ingest_simple(store, "doc1", "alpha.md", "a")
    assert store.restore_document("doc1") is False


# --- purge_trash GC ---


def test_purge_trash_deletes_documents_and_trash_rows(store):
    _ingest_simple(store, "doc1", "alpha.md", "a")
    _ingest_simple(store, "doc2", "beta.md", "b")
    store.delete_document("doc1")
    store.delete_document("doc2")

    # 把所有 trash 行的 deleted_at 改成 31 天前
    store._conn.execute(
        "UPDATE trash SET deleted_at = '2026-08-01T00:00:00+08:00'"
    )
    store._conn.commit()

    purged = store.purge_trash(older_than_days=30)
    assert purged == 2

    # documents 行物理删
    n = store._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    assert n == 0
    # trash 行也物理删
    n = store._conn.execute("SELECT COUNT(*) FROM trash").fetchone()[0]
    assert n == 0


def test_purge_trash_keeps_recent_entries(store):
    _ingest_simple(store, "doc1", "alpha.md", "a")
    store.delete_document("doc1")
    # 不改 deleted_at = 30 天 GC 不到
    purged = store.purge_trash(older_than_days=30)
    assert purged == 0
    # trash 仍在
    assert len(store.list_trash()) == 1


def test_purge_trash_cleans_up_empty_collections(store):
    """软删除后 documents 集合下应被联动清空。"""
    _ingest_simple(store, "doc1", "alpha.md", "a")
    store.ensure_collection("custom")
    store.delete_document("doc1")
    # 软删后 collection 表里 default 仍在（documents 行还在）
    cols = [r[0] for r in store._conn.execute("SELECT name FROM collections").fetchall()]
    assert "default" in cols

    # 把 trash 改成 31 天前
    store._conn.execute(
        "UPDATE trash SET deleted_at = '2026-08-01T00:00:00+08:00'"
    )
    store._conn.commit()
    purged = store.purge_trash(older_than_days=30)
    assert purged == 1

    # default 集合应被联动清空
    cols = [r[0] for r in store._conn.execute("SELECT name FROM collections").fetchall()]
    assert "default" not in cols


# --- 关键回归：soft-delete 不应清空 collection（与旧版硬删除差异） ---


def test_soft_delete_does_not_drop_collection(store):
    _ingest_simple(store, "doc1", "alpha.md", "a")
    # 软删前：collection 表有 default
    cols_before = [r[0] for r in store._conn.execute("SELECT name FROM collections").fetchall()]
    assert "default" in cols_before

    store.delete_document("doc1")

    # 软删后：documents 行还在，collection 表里 default 仍在
    cols_after = [r[0] for r in store._conn.execute("SELECT name FROM collections").fetchall()]
    assert "default" in cols_after


# --- schema 迁移：旧库无 deleted_at 列也能跑 ---


def test_schema_migration_adds_deleted_at(tmp_path):
    """模拟旧库：建表时无 deleted_at 列；open() 时迁移应补齐。"""
    import sqlite3

    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    # 极简 documents 表（无 deleted_at），模拟旧版
    conn.executescript("""
        CREATE TABLE documents (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            collection TEXT NOT NULL DEFAULT 'default',
            format TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            page_count INTEGER,
            chunk_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()

    # 用 VectorStore.open() 触发迁移
    vs = VectorStore(db, embedding_dim=EMBEDDING_DIM)
    try:
        vs.open()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"sqlite-vec 不可用: {e}")

    # 验证 deleted_at 列已被补齐
    cols = {row[1] for row in vs._conn.execute("PRAGMA table_info(documents)").fetchall()}
    assert "deleted_at" in cols

    # 验证 trash / curate_runs 表都存在
    tables = {row[0] for row in vs._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "trash" in tables
    assert "curate_runs" in tables
    vs.close()


# --- record_curate_run + list_curate_runs ---


def test_record_and_list_curate_runs(store):
    # 相对当前时间构造 started_at（避免硬编码日期随时间推移变成「8 天前」被
    # days=7 过滤掉——曾经的 9-01 硬编码在 9-08 之后必然失败）
    started = (_dt.datetime.now() - _dt.timedelta(hours=1)).isoformat()
    finished = (_dt.datetime.now() - _dt.timedelta(hours=1, seconds=-5)).isoformat()
    rid = store.record_curate_run(
        started_at=started,
        finished_at=finished,
        dry_run=True,
        collection="default",
        actions=["enrich", "categorize"],
        changed_doc_ids=["doc1", "doc2"],
        skipped_count=0,
        error_count=0,
        elapsed_ms=5000,
        note="agent_test",
    )
    assert rid > 0

    runs = store.list_curate_runs(days=7)
    assert len(runs) == 1
    run = runs[0]
    assert run["dry_run"] is True
    assert run["actions"] == ["enrich", "categorize"]
    assert run["changed_doc_ids"] == ["doc1", "doc2"]
    assert run["note"] == "agent_test"


def test_list_curate_runs_filters_by_days(store):
    # 相对当前时间构造：8 天前 / 1 天前（硬编码日期会随时间推移失效）
    eight_days_ago = (_dt.datetime.now() - _dt.timedelta(days=8)).isoformat()
    one_day_ago = (_dt.datetime.now() - _dt.timedelta(days=1)).isoformat()
    # 8 天前的记录
    store.record_curate_run(
        started_at=eight_days_ago,
        finished_at=eight_days_ago,
        dry_run=True,
        collection=None,
        actions=["enrich"],
        changed_doc_ids=[],
        skipped_count=0,
        error_count=0,
        elapsed_ms=1000,
    )
    # 1 天前的记录
    store.record_curate_run(
        started_at=one_day_ago,
        finished_at=one_day_ago,
        dry_run=False,
        collection="default",
        actions=["dedup"],
        changed_doc_ids=["x"],
        skipped_count=0,
        error_count=1,
        elapsed_ms=1000,
    )

    recent = store.list_curate_runs(days=7)
    assert len(recent) == 1
    assert recent[0]["dry_run"] is False
    assert recent[0]["error_count"] == 1


# --- curator 跑完自动写留痕的端到端验证 ---


def test_curate_records_run_with_changed_doc_ids(tmp_path):
    """curate() 跑完应自动调 record_curate_run 写一行留痕，包含 enriched doc id。"""
    from doc2mind.core.config import Settings
    from doc2mind.core.curator import curate
    from doc2mind.core.llm.base import LLMClient

    from tests.test_curator import DummyEmbedder, _default_reply

    class _LLM(LLMClient):
        @property
        def model_name(self): return "fake"

        @property
        def provider(self): return "fake"

        def _do_chat(self, messages, temperature=None, max_tokens=None):
            return _default_reply(messages)

    vs = VectorStore(tmp_path / "curate_e2e.db", embedding_dim=8)
    try:
        vs.open()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"sqlite-vec 不可用: {e}")

    # 摄入 1 个文档
    vs.ensure_collection("default")
    vs.upsert_document(_make_doc(doc_id="d1", source="alpha.md"))
    chunks = [Chunk(content="alpha is here", tokens=3, metadata={"chunk_index": 0})]
    embeddings = [[0.0] * 8]
    vs.insert_chunks("d1", "default", "alpha.md", "md", chunks, embeddings)

    settings = Settings(
        db_path=tmp_path / "curate_e2e.db",
        llm_provider="openai",
        llm_api_key="fake",
    )
    report = curate(
        store=vs,
        embedder=DummyEmbedder(),
        llm=_LLM(),
        settings=settings,
        collection="default",
        actions=["enrich"],
        dry_run=True,
        note="agent_e2e",
    )
    assert report.errors == []

    # 验证 curate_runs 表有 1 行
    runs = vs.list_curate_runs(days=1)
    assert len(runs) == 1
    run = runs[0]
    assert run["dry_run"] is True
    assert run["actions"] == ["enrich"]
    assert run["note"] == "agent_e2e"
    # d1 应出现在 changed_doc_ids
    assert "d1" in run["changed_doc_ids"]

    vs.close()


# --- 必做 1：replace_document 软删除交叉场景 ---


def test_replace_document_skips_soft_deleted(tmp_path):
    """replace_document 不应物理删软删行——保留 trash 审计链。"""
    vs = VectorStore(tmp_path / "rep.db", embedding_dim=4)
    vs.open()

    # 1. 摄入 doc1
    _ingest_simple(vs, "doc1", "alpha.md", "hello")

    # 2. 软删 doc1
    vs.delete_document("doc1")
    assert vs.list_trash()[0]["document_id"] == "doc1"

    # 3. 重新摄入同名同源文件（走 replace_document——真实重新摄入路径）
    new_doc = _make_doc(doc_id="doc2", source="alpha.md")
    chunks = [Chunk(content="new content", tokens=2, metadata={"chunk_index": 0})]
    vs.replace_document(new_doc, chunks, [[0.1] * 4])

    # 4. trash 里的 doc1 应仍在（未被物理删），trash 行的 document_id 仍是 doc1
    trash_after = vs.list_trash()
    assert any(t["document_id"] == "doc1" for t in trash_after), \
        "软删行被 replace_document 物理删，破坏 trash 审计链"

    # 5. 恢复 doc1 应返回 False——doc2（活跃）已占同 source，恢复会产生
    #    两个同 source 文档并存（部分索引 WHERE deleted_at IS NULL 阻止）
    assert vs.restore_document("doc1") is False
    vs.close()


def test_replace_document_only_replaces_active_doc(tmp_path):
    """活跃文档被 replace_document 物理删（trash 不会有它的条目，因本来就没被软删过）。"""
    vs = VectorStore(tmp_path / "rep2.db", embedding_dim=4)
    vs.open()

    _ingest_simple(vs, "old", "alpha.md", "first")
    # 重新摄入（覆盖，走 replace_document）
    new_doc = _make_doc(doc_id="new", source="alpha.md")
    chunks = [Chunk(content="second", tokens=1, metadata={"chunk_index": 0})]
    vs.replace_document(new_doc, chunks, [[0.2] * 4])

    # "old" 的 documents 行已被物理删（从未软删过）
    assert vs.get_document_by_id("old") is None
    # trash 应为空
    assert vs.list_trash() == []
    # "new" 正常
    assert vs.get_document_by_id("new") is not None
    vs.close()


# --- 必做 2：restore_document 清 chunk_count ---


def test_restore_clears_chunk_count(tmp_path):
    """恢复后 chunk_count 必须清零，否则 list_docs 报告"42 分块"但实际 0 命中。"""
    vs = VectorStore(tmp_path / "rst.db", embedding_dim=4)
    vs.open()

    # 摄入 + 软删
    _ingest_simple(vs, "doc1", "alpha.md", "hello")
    assert vs.get_document_by_id("doc1").chunk_count == 1
    vs.delete_document("doc1")
    # 软删后 documents 行的 chunk_count 还是 1（没动）
    row = vs._conn.execute(
        "SELECT chunk_count FROM documents WHERE id = ?", ("doc1",)
    ).fetchone()
    assert row[0] == 1

    # 恢复
    vs.restore_document("doc1")
    # 关键断言：chunk_count 必须被清零
    restored = vs.get_document_by_id("doc1")
    assert restored.chunk_count == 0
    vs.close()


# --- 必做 3：按文件名删除（basename 模糊匹配） ---


def test_delete_by_source_basename_matches_absolute_path(tmp_path):
    """delete_by_source_basename 应能匹配 documents.source 中的绝对路径结尾。"""
    vs = VectorStore(tmp_path / "bn.db", embedding_dim=4)
    vs.open()

    # 模拟"绝对路径"形式的 source（loader.base.make_source 的实际行为）
    doc = StoredDocument(
        id="doc1",
        source="E:/projects/notes/alpha.md",  # 绝对路径
        collection="default",
        format="md",
        file_hash="h1",
        size_bytes=100,
        page_count=None,
        chunk_count=0,
        created_at="2026-09-01T00:00:00+08:00",
        updated_at="2026-09-01T00:00:00+08:00",
    )
    vs.ensure_collection("default")
    vs.upsert_document(doc)
    chunks = [Chunk(content="hi", tokens=1, metadata={"chunk_index": 0})]
    vs.insert_chunks("doc1", "default", doc.source, "md", chunks, [[0.1] * 4])

    # 仅用 basename 删
    n = vs.delete_by_source_basename("alpha.md")
    assert n == 1
    assert vs.get_document_by_id("doc1") is None
    vs.close()


def test_delete_by_source_basename_escapes_like_wildcards(tmp_path):
    """用户输入的 % / _ 不应被当通配符。"""
    vs = VectorStore(tmp_path / "esc.db", embedding_dim=4)
    vs.open()
    doc = StoredDocument(
        id="doc1",
        source="E:/x/a_special.md",  # basename 含 _ 和字母
        collection="default",
        format="md",
        file_hash="h",
        size_bytes=0,
        page_count=None,
        chunk_count=0,
        created_at="2026-09-01T00:00:00+08:00",
        updated_at="2026-09-01T00:00:00+08:00",
    )
    vs.ensure_collection("default")
    vs.upsert_document(doc)
    chunks = [Chunk(content="x", tokens=1, metadata={"chunk_index": 0})]
    vs.insert_chunks("doc1", "default", doc.source, "md", chunks, [[0.1] * 4])

    # 注入通配符攻击：传 "%" 当 basename
    n = vs.delete_by_source_basename("%")
    assert n == 0  # 0 匹配因为没文档以 "/%" 结尾
    n = vs.delete_by_source_basename("a_special.md")
    assert n == 1
    vs.close()


# --- 必做 4：note 字段透传 ---


def test_curate_note_round_trip(tmp_path):
    """curate(note=...) 端到端：note 应写入 curate_runs.note。"""
    from doc2mind.core.config import Settings
    from doc2mind.core.curator import curate
    from doc2mind.core.llm.base import LLMClient

    from tests.test_curator import DummyEmbedder, _default_reply

    class _LLM(LLMClient):
        @property
        def model_name(self): return "fake"
        @property
        def provider(self): return "fake"
        def _do_chat(self, messages, temperature=None, max_tokens=None):
            return _default_reply(messages)

    vs = VectorStore(tmp_path / "note.db", embedding_dim=8)
    vs.open()
    vs.ensure_collection("default")
    vs.upsert_document(_make_doc(doc_id="d1", source="alpha.md"))
    chunks = [Chunk(content="alpha", tokens=1, metadata={"chunk_index": 0})]
    vs.insert_chunks("d1", "default", "alpha.md", "md", chunks, [[0.0] * 8])

    settings = Settings(
        db_path=tmp_path / "note.db",
        llm_provider="openai", llm_api_key="fake",
    )
    curate(
        store=vs, embedder=DummyEmbedder(), llm=_LLM(),
        settings=settings, collection="default", actions=["enrich"],
        dry_run=True, note="agent_test_note",
    )

    runs = vs.list_curate_runs(days=1)
    assert len(runs) == 1
    assert runs[0]["note"] == "agent_test_note"
    vs.close()


# --- 可选 G：软删联动清理 graph chunk_entities ---


def test_soft_delete_cleans_graph_chunk_links(tmp_path):
    """软删应联动清除 chunk_entities 悬空行（与 GraphStore 共用 DB 文件）。"""
    vs = VectorStore(tmp_path / "graph.db", embedding_dim=4)
    vs.open()

    _ingest_simple(vs, "doc1", "alpha.md", "hello graph")

    # 模拟 GraphStore 已写入 chunk_entities（拿到 doc1 的 chunk_id）
    cm = vs._conn.execute(
        "SELECT id FROM chunks_meta WHERE document_id = ?", ("doc1",)
    ).fetchone()
    chunk_id = cm[0]
    vs._conn.execute(
        "CREATE TABLE chunk_entities (chunk_id INTEGER NOT NULL, entity_id TEXT NOT NULL,"
        " PRIMARY KEY (chunk_id, entity_id))"
    )
    vs._conn.execute(
        "INSERT INTO chunk_entities(chunk_id, entity_id) VALUES (?, 'ent_1')",
        (chunk_id,),
    )
    vs._conn.commit()

    # 软删
    vs.delete_document("doc1")

    # chunk_entities 关联行应被清除
    n = vs._conn.execute("SELECT COUNT(*) FROM chunk_entities").fetchone()[0]
    assert n == 0, "软删后 chunk_entities 悬空行未清除"
    vs.close()


def test_delete_document_without_graph_tables_is_safe(tmp_path):
    """没有 graph 表（从未 extract）时软删不应报错（防御式探测）。"""
    vs = VectorStore(tmp_path / "nog.db", embedding_dim=4)
    vs.open()
    _ingest_simple(vs, "doc1", "alpha.md", "hello")
    # 不建 chunk_entities 表直接软删
    assert vs.delete_document("doc1") == 1
    vs.close()
