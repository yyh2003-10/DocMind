"""sqlite-vec 向量存储 + 元数据存储。

设计：
- `vec_chunks` 虚拟表（vec0）存向量，余弦距离
- `chunks_meta` 普通表存分块元数据，与 `vec_chunks.id` 对齐
- `documents` 表存文档级元数据（file_hash 去重）
- `bm25_index` 虚拟表（FTS5）用于 BM25 关键词检索

事务模型：
- 单次 `ingest_chunks` 调用包在一个事务里
- WAL 模式提升并发读

错误处理：
- sqlite-vec 缺失 → StoreError，提示 pip install
- FTS5 缺失（罕见）→ 关闭 BM25，仅用向量检索
"""

from __future__ import annotations

import contextlib
import functools
import json
import re as _re
import sqlite3
import threading
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doc2mind.core.chunker.base import Chunk


class StoreError(Exception):
    """存储异常。"""


def _normalize_collections(
    collection: str | Sequence[str] | None,
) -> frozenset[str] | None:
    """集合过滤参数归一化：str / 序列 → frozenset；None/空 → None（不过滤）。"""
    if collection is None:
        return None
    items = [collection] if isinstance(collection, str) else list(collection)
    cleaned = frozenset(c.strip() for c in items if isinstance(c, str) and c.strip())
    return cleaned or None


def _is_locked_error(exc: Exception) -> bool:
    """判断异常链中是否包含 SQLite 锁冲突（database is locked / busy）。

    跨进程并发写（HTTP 服务与 MCP 服务共用同一 db 文件）时，
    WAL 下写-写互斥仍可能触发锁冲突，需有限重试。
    """
    while exc is not None:
        if isinstance(exc, sqlite3.OperationalError):
            msg = str(exc).lower()
            if "locked" in msg or "busy" in msg:
                return True
        exc = exc.__cause__
    return False


def _retry_on_locked(func):
    """装饰器：SQLite 写锁冲突时整体重试（最多 4 次，指数退避）。

    方法内部把 OperationalError 包装成 StoreError（raise ... from e），
    因此沿 __cause__ 链判断是否为锁冲突，命中则重跑整个方法（含事务）。
    """

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        last_exc: Exception | None = None
        for attempt in range(4):
            try:
                return func(self, *args, **kwargs)
            except Exception as e:  # noqa: BLE001
                if not _is_locked_error(e) or attempt == 3:
                    raise
                last_exc = e
                time.sleep(0.1 * (attempt + 1))
        # 理论不可达：最后一轮失败会直接 raise
        raise last_exc  # type: ignore[misc]

    return wrapper


# --- FTS5 MATCH 表达式构造（中文友好）---

# CJK 统一表意文字范围（中日韩常用汉字）
_CJK_RE = _re.compile(r"[\u4e00-\u9fff]+")


def _build_fts5_match(query: str) -> str:
    """把用户 query 转成 FTS5 MATCH 表达式（配合 `trigram` tokenizer）。

    trigram tokenizer 入库时把任意文本切成 3-char 子串存储，
    查询时 FTS5 会自动对 ≥3 chars 的 token 做子串匹配。
    策略：
    - 按空格切分为独立 token，整体用 OR 连接（任一命中即加分）
    - 中文段：≥3 chars 直接整段引号包裹（trigram 自子串匹配）
      <3 chars 用前缀 `token*` 走子串
    - 英文 token：≥3 chars 直接引号包裹；<3 chars 用前缀 `token*`
    - 全空返回 ""（调用方据此跳过 BM25）

    例：
        "向量存储架构" → '"向量存储架构"'
        "向量 存储" → '"向量存储" OR "存储"'
        "vector storage" → '"vector" OR "storage"'
        "向" → '"向*"'
    """
    if not query or not query.strip():
        return ""

    tokens: list[str] = []
    for chunk in query.split():
        chunk = chunk.strip()
        if not chunk:
            continue
        if len(chunk) >= 3:
            # trigram tokenizer 对 ≥3 chars 的 token 自动做子串匹配
            tokens.append(f'"{chunk}"')
        else:
            # <3 chars：trigram 无法精确 MATCH，用前缀走子串
            tokens.append(f'"{chunk}*"')

    # 各 split token 整体用 OR 连接（更宽松，任一命中即返回）
    return " OR ".join(tokens) if tokens else ""


# --- 数据类型 ---
@dataclass(frozen=True)
class StoredChunk:
    """存储中的分块记录。"""

    id: int
    content: str
    source: str
    format: str
    doc_type: str | None
    page: int | None
    heading: str | None
    file_hash: str
    collection: str
    created_at: str
    tokens: int
    chunk_index: int
    extra_metadata: dict[str, Any]


@dataclass(frozen=True)
class StoredDocument:
    """存储中的文档记录。

    title / tags / summary / enriched_at 为 AI 整理（curate）生成的元数据，
    旧库迁移后为 NULL；tags 在库中以 JSON 文本存储，读出时解析为列表。
    """

    id: str
    source: str
    collection: str
    format: str
    file_hash: str
    size_bytes: int
    page_count: int | None
    chunk_count: int
    created_at: str
    updated_at: str
    title: str | None = None
    tags: list[str] | None = None
    summary: str | None = None
    enriched_at: str | None = None


@dataclass(frozen=True)
class StoreStats:
    """存储统计。"""

    total_documents: int
    total_chunks: int
    # name -> (doc_count, chunk_count, size_bytes)
    collections: dict[str, tuple[int, int, int]]


# --- SQL 建表 ---
_SCHEMA_SQL = """
-- 文档级元数据
CREATE TABLE IF NOT EXISTS documents (
    id            TEXT    PRIMARY KEY,         -- ULID
    source        TEXT    NOT NULL,            -- 原始文件名
    collection    TEXT    NOT NULL DEFAULT 'default',
    format        TEXT    NOT NULL,            -- pdf/docx/...
    file_hash     TEXT    NOT NULL,            -- MD5，去重
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    page_count    INTEGER,                      -- NULL 表示无分页概念
    chunk_count   INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    title         TEXT,                         -- AI 生成标题（curate）
    tags          TEXT,                         -- AI 标签，JSON 数组文本（curate）
    summary       TEXT,                         -- AI 摘要（curate）
    enriched_at   TEXT,                         -- 最近一次 AI 整理时间（curate）
    UNIQUE (collection, source)
);
CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection);
CREATE INDEX IF NOT EXISTS idx_documents_hash      ON documents(file_hash);

-- 分块元数据（与向量表对齐）
CREATE TABLE IF NOT EXISTS chunks_meta (
    id            INTEGER PRIMARY KEY,          -- 与 vec_chunks.id 对齐
    document_id   TEXT    NOT NULL,
    content       TEXT    NOT NULL,
    tokens        INTEGER NOT NULL DEFAULT 0,
    chunk_index   INTEGER NOT NULL DEFAULT 0,
    collection    TEXT    NOT NULL DEFAULT 'default',
    source        TEXT    NOT NULL,             -- 冗余，加速检索结果渲染
    format        TEXT    NOT NULL,
    doc_type      TEXT,                          -- heading/paragraph/table/code...
    page          INTEGER,
    sheet         TEXT,
    slide         INTEGER,
    heading       TEXT,
    language      TEXT,
    extra         TEXT    NOT NULL DEFAULT '{}'  -- JSON，存其余 metadata
);
CREATE INDEX IF NOT EXISTS idx_chunks_collection   ON chunks_meta(collection);
CREATE INDEX IF NOT EXISTS idx_chunks_document     ON chunks_meta(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_source       ON chunks_meta(source);
"""

_FTS_SQL = """
-- FTS5 全文索引（BM25 用）
-- trigram tokenizer：把任意文本（含中文）切成 3-char 子串，
-- 中文 BM25 评估不再触发 datatype mismatch。
CREATE VIRTUAL TABLE IF NOT EXISTS bm25_index USING fts5(
    content,
    collection UNINDEXED,
    chunk_id UNINDEXED,
    tokenize = 'trigram'
);
"""

_VEC_SQL_TEMPLATE = """
CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
    id INTEGER PRIMARY KEY,
    embedding FLOAT[{dim}] distance_metric=cosine
);
"""


class VectorStore:
    """sqlite-vec 向量存储封装。

    线程安全：单个 connection 由 lock 保护；多线程建议每线程一个 VectorStore。
    """

    def __init__(self, db_path: Path, embedding_dim: int) -> None:
        self.db_path = Path(db_path)
        self.embedding_dim = int(embedding_dim)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        # FTS5 查询独立 connection（避开 vec0 扩展与 trigram BM25 评估冲突）
        self._fts_conn: sqlite3.Connection | None = None
        self._fts_available = False

    # --- 生命周期 ---
    def open(self) -> None:
        """打开数据库，加载 sqlite-vec 扩展，建表。"""
        with self._lock:
            if self._conn is not None:
                return
            try:
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(
                    str(self.db_path),
                    check_same_thread=False,
                    isolation_level=None,  # 手动事务
                    timeout=30,  # busy 等待上限（秒）：跨进程并发写时避免立即抛 database is locked
                )
                # WAL 提升并发读
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA busy_timeout=30000")

                # 加载 sqlite-vec 扩展
                self._load_vec_extension(conn)

                # 建表
                conn.executescript(_SCHEMA_SQL)
                # 旧库补齐 AI 整理元数据列（幂等；新库建表已含，此处为 no-op）
                self._migrate_documents_meta(conn)
                conn.execute(_VEC_SQL_TEMPLATE.format(dim=self.embedding_dim))
                # 维度以磁盘上已有表的实际建表 SQL 为准：CREATE ... IF NOT
                # EXISTS 在表已存在时静默跳过，而构造传入的维度可能仍是
                # 模型加载前的预设值（如默认 512）。回读真实维度，让
                # reindex 的维度判断、后续写入都以表为准。
                row = conn.execute(
                    "SELECT sql FROM sqlite_master"
                    " WHERE type='table' AND name='vec_chunks'"
                ).fetchone()
                if row and row[0]:
                    m = _re.search(r"FLOAT\[(\d+)\]", str(row[0]))
                    if m:
                        self.embedding_dim = int(m.group(1))

                # FTS5（可选）
                try:
                    conn.executescript(_FTS_SQL)
                    self._fts_available = True
                    # 独立 connection 跑 BM25 查询：
                    # vec0 扩展与 trigram tokenizer 在同 connection 上
                    # 评估中文 bm25() 时会触发 IntegrityError: datatype mismatch
                    fts_conn = sqlite3.connect(
                        str(self.db_path),
                        check_same_thread=False,
                        isolation_level=None,
                        timeout=30,
                    )
                    fts_conn.execute("PRAGMA journal_mode=WAL")
                    fts_conn.execute("PRAGMA busy_timeout=30000")
                    self._fts_conn = fts_conn
                except sqlite3.OperationalError:
                    # FTS5 缺失，关闭 BM25
                    self._fts_available = False

                self._conn = conn
            except StoreError:
                raise
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"打开存储失败 ({self.db_path}): {e}") from e

    def close(self) -> None:
        """关闭数据库连接。"""
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
            if self._fts_conn is not None:
                self._fts_conn.close()
                self._fts_conn = None

    def __enter__(self) -> VectorStore:
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def ping(self) -> bool:
        """轻量健康探测：连接存活且 vec0 扩展可用。

        供 /v1/health 做真实健康检查（数据库损坏 / sqlite-vec 扩展缺失时
        返回 False，而不是绿灯报 ok）。探测失败不改变连接状态。
        """
        try:
            with self._lock:
                if self._conn is None:
                    return False
                self._conn.execute("SELECT 1").fetchone()
                # vec0 扩展可用性：查已注册的虚拟表模块（不触碰业务表）
                self._conn.execute(
                    "SELECT 1 FROM vec_chunks LIMIT 0"
                ).fetchall()
            return True
        except Exception:  # noqa: BLE001 — 健康探测任何异常都视为不可用
            return False

    # --- sqlite-vec 扩展加载 ---
    @staticmethod
    def _load_vec_extension(conn: sqlite3.Connection) -> None:
        """加载 sqlite-vec 扩展。

        策略：
        1. 调用 `sqlite_vec.load(conn)`（需要先 enable_load_extension）
        2. 若 sqlite_vec 模块不可用，用 `loadable_path()` 直接加载
        """
        try:
            import sqlite_vec  # type: ignore

            conn.enable_load_extension(True)  # type: ignore[attr-defined]
            sqlite_vec.load(conn)
            return
        except ImportError:
            pass  # sqlite_vec 未安装，走路径 2
        except Exception:  # noqa: BLE001
            pass  # 走路径 2

        # 路径 2: 通过 loadable_path 直接加载
        try:
            import sqlite_vec

            conn.enable_load_extension(True)  # type: ignore[attr-defined]
            conn.load_extension(sqlite_vec.loadable_path())  # type: ignore[attr-defined]
            return
        except ImportError:
            raise StoreError(
                "sqlite-vec 未安装。请运行：pip install sqlite-vec"
            ) from None
        except Exception as e:  # noqa: BLE001
            raise StoreError(
                "sqlite-vec 扩展加载失败。请运行：pip install sqlite-vec"
            ) from e

    @property
    def fts_available(self) -> bool:
        """BM25 (FTS5) 是否可用。"""
        return self._fts_available

    # --- schema 迁移 ---
    # documents 表 AI 整理元数据列（curate 功能）。旧库缺列时补齐。
    _DOCUMENTS_META_COLUMNS: tuple[tuple[str, str], ...] = (
        ("title", "TEXT"),
        ("tags", "TEXT"),
        ("summary", "TEXT"),
        ("enriched_at", "TEXT"),
    )

    @staticmethod
    def _migrate_documents_meta(conn: sqlite3.Connection) -> None:
        """documents 表幂等补齐 AI 整理元数据列（ALTER TABLE ADD COLUMN）。

        CREATE TABLE IF NOT EXISTS 对已存在的旧表是 no-op，缺的列需在这里
        显式补上；重复调用安全（已存在的列跳过）。
        """
        existing = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(documents)").fetchall()
        }
        for name, decl in VectorStore._DOCUMENTS_META_COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE documents ADD COLUMN {name} {decl}")

    # --- 写入 ---
    @_retry_on_locked
    def ensure_collection(self, name: str) -> None:
        """登记一个空集合（仅插入占位文档记录，chunk_count=0），使其出现在集合列表中。

        若集合已存在（documents 表已有该 collection 记录）则幂等跳过。
        """
        with self._lock:
            self._require_open()
            try:
                existing = self._conn.execute(
                    "SELECT 1 FROM documents WHERE collection = ? LIMIT 1",
                    (name,),
                ).fetchone()
                if existing is not None:
                    return
                now = _now_iso()
                self._conn.execute(
                    """
                    INSERT INTO documents
                        (id, source, collection, format, file_hash,
                         size_bytes, page_count, chunk_count, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 0, 0, 0, ?, ?)
                    """,
                    (
                        _new_id(),
                        "__collection_placeholder__",
                        name,
                        "placeholder",
                        f"placeholder-{name}",
                        now,
                        now,
                    ),
                )
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"创建集合失败: {e}") from e

    @_retry_on_locked
    def upsert_document(self, doc: StoredDocument) -> None:
        """插入或更新文档记录。"""
        with self._lock:
            self._require_open()
            try:
                self._conn.execute(
                    """
                    INSERT INTO documents
                        (id, source, collection, format, file_hash,
                         size_bytes, page_count, chunk_count, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source=excluded.source,
                        chunk_count=excluded.chunk_count,
                        updated_at=excluded.updated_at
                    """,
                    (
                        doc.id, doc.source, doc.collection, doc.format,
                        doc.file_hash, doc.size_bytes, doc.page_count,
                        doc.chunk_count, doc.created_at, doc.updated_at,
                    ),
                )
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"写入文档失败: {e}") from e

    @_retry_on_locked
    def insert_chunks(
        self,
        document_id: str,
        collection: str,
        source: str,
        fmt: str,
        chunks: Sequence[Chunk],
        embeddings: Sequence,
    ) -> int:
        """批量插入分块 + 向量。

        Args:
            document_id: 所属文档 ID
            collection: 集合名
            source: 原始文件名
            fmt: 文档格式
            chunks: `Chunk` 列表
            embeddings: 与 chunks 顺序对应的向量列表（list[np.ndarray 或 bytes]）

        Returns:
            实际插入的行数
        """
        if len(chunks) != len(embeddings):
            raise StoreError(
                f"chunks ({len(chunks)}) 与 embeddings ({len(embeddings)}) 长度不一致"
            )
        if not chunks:
            return 0

        with self._lock:
            self._require_open()
            conn = self._conn
            try:
                conn.execute("BEGIN")
                inserted = self._insert_chunks_in_txn(
                    conn, document_id, collection, source, fmt, chunks, embeddings
                )
                conn.execute("COMMIT")
                return inserted
            except StoreError:
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK")
                raise
            except Exception as e:  # noqa: BLE001
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK")
                raise StoreError(f"插入分块失败: {e}") from e

    @_retry_on_locked
    def replace_document(
        self,
        doc: StoredDocument,
        chunks: Sequence[Chunk],
        embeddings: Sequence,
    ) -> int:
        """原子替换文档：删旧（同 collection+source）→ 写文档 → 写分块，单事务。

        旧流程分三步各自提交（delete_by_source → upsert_document → insert_chunks），
        insert_chunks 中途失败会留下"文档记录存在、chunk_count>0、但没有任何
        分块"的孤儿状态，质量页随即误报。本方法把三步放进同一事务，失败整体
        回滚，旧文档保持原样。

        Args:
            doc: 新文档记录（chunk_count 应为 len(chunks)）
            chunks: `Chunk` 列表
            embeddings: 与 chunks 顺序对应的向量列表

        Returns:
            插入的 chunk 数
        """
        if len(chunks) != len(embeddings):
            raise StoreError(
                f"chunks ({len(chunks)}) 与 embeddings ({len(embeddings)}) 长度不一致"
            )

        with self._lock:
            self._require_open()
            conn = self._conn
            try:
                conn.execute("BEGIN")

                # 1. 删除同 (collection, source) 的旧文档及分块/向量/FTS
                old_ids = [
                    r[0] for r in conn.execute(
                        "SELECT id FROM documents WHERE source = ? AND collection = ?",
                        (doc.source, doc.collection),
                    ).fetchall()
                ]
                for old_id in old_ids:
                    self._delete_document_chunks_in_txn(conn, old_id)
                    conn.execute("DELETE FROM documents WHERE id = ?", (old_id,))

                # 2. 写新文档记录
                conn.execute(
                    """
                    INSERT INTO documents
                        (id, source, collection, format, file_hash,
                         size_bytes, page_count, chunk_count, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source=excluded.source,
                        chunk_count=excluded.chunk_count,
                        updated_at=excluded.updated_at
                    """,
                    (
                        doc.id, doc.source, doc.collection, doc.format,
                        doc.file_hash, doc.size_bytes, doc.page_count,
                        doc.chunk_count, doc.created_at, doc.updated_at,
                    ),
                )

                # 3. 写分块（空列表也允许 = 显式清空该 source）
                inserted = 0
                if chunks:
                    inserted = self._insert_chunks_in_txn(
                        conn, doc.id, doc.collection, doc.source,
                        doc.format, chunks, embeddings,
                    )

                conn.execute("COMMIT")
                return inserted
            except StoreError:
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK")
                raise
            except Exception as e:  # noqa: BLE001
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK")
                raise StoreError(f"替换文档失败: {e}") from e

    def _insert_chunks_in_txn(
        self,
        conn: sqlite3.Connection,
        document_id: str,
        collection: str,
        source: str,
        fmt: str,
        chunks: Sequence[Chunk],
        embeddings: Sequence,
    ) -> int:
        """在已开启的事务内插入分块（调用方负责锁 / BEGIN / COMMIT / ROLLBACK）。"""
        inserted = 0
        for chunk, emb in zip(chunks, embeddings, strict=False):
            # 序列化向量为 bytes（vec0 接受 BLOB）
            emb_bytes = _vector_to_bytes(emb)
            meta = chunk.metadata

            # 1. 插入向量，拿 id
            cur = conn.execute(
                "INSERT INTO vec_chunks(embedding) VALUES (?)",
                (emb_bytes,),
            )
            vec_id = cur.lastrowid
            if vec_id is None:
                raise StoreError("无法获取 vec_chunks.id")

            # 2. 插入 chunks_meta
            excluded_keys = {
                "type", "page", "sheet", "slide", "heading",
                "language", "level", "chunk_index",
            }
            extra = {k: v for k, v in meta.items() if k not in excluded_keys}
            conn.execute(
                """
                INSERT INTO chunks_meta
                    (id, document_id, content, tokens, chunk_index,
                     collection, source, format, doc_type, page,
                     sheet, slide, heading, language, extra)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    vec_id, document_id, chunk.content, chunk.tokens,
                    int(meta.get("chunk_index", 0)),
                    collection, source, fmt,
                    meta.get("type"),
                    meta.get("page"),
                    meta.get("sheet"),
                    meta.get("slide"),
                    meta.get("heading"),
                    meta.get("language"),
                    json.dumps(extra, ensure_ascii=False),
                ),
            )

            # 3. 插入 FTS5 索引
            if self._fts_available:
                conn.execute(
                    "INSERT INTO bm25_index(content, collection, chunk_id) VALUES (?, ?, ?)",
                    (chunk.content, collection, vec_id),
                )
            inserted += 1

        # 4. 更新文档 chunk_count（绝对值写入，不累加）
        conn.execute(
            "UPDATE documents SET chunk_count = ?, updated_at = ? WHERE id = ?",
            (inserted, _now_iso(), document_id),
        )
        return inserted

    def _delete_document_chunks_in_txn(
        self, conn: sqlite3.Connection, document_id: str
    ) -> list[int]:
        """在已开启的事务内删除文档的全部分块（向量/FTS/meta），返回 chunk id 列表。"""
        rows = conn.execute(
            "SELECT id FROM chunks_meta WHERE document_id = ?",
            (document_id,),
        ).fetchall()
        chunk_ids = [r[0] for r in rows]
        if chunk_ids:
            placeholders = ",".join("?" * len(chunk_ids))
            conn.execute(
                f"DELETE FROM vec_chunks WHERE id IN ({placeholders})",
                chunk_ids,
            )
            if self._fts_available:
                # chunk_id 列 TEXT affinity 存的是文本，与整型参数直接比较
                # 永不相等（删除会静默漏删 FTS 行），必须 CAST 后匹配
                conn.execute(
                    f"DELETE FROM bm25_index WHERE CAST(chunk_id AS INTEGER) IN ({placeholders})",
                    chunk_ids,
                )
            conn.execute(
                f"DELETE FROM chunks_meta WHERE id IN ({placeholders})",
                chunk_ids,
            )
        return chunk_ids

    # --- 删除 ---
    @_retry_on_locked
    def delete_document(self, document_id: str) -> int:
        """删除文档及其所有分块与向量。

        Returns:
            删除的 chunk 数；-1 表示文档不存在（哨兵值，调用方据此返回 404）。
        """
        with self._lock:
            self._require_open()
            conn = self._conn
            try:
                # 先查文档是否存在（避免误删孤儿 chunk）
                exists = conn.execute(
                    "SELECT 1 FROM documents WHERE id = ?", (document_id,)
                ).fetchone()
                if exists is None:
                    return -1

                conn.execute("BEGIN")
                # 取该文档所有 chunk_id
                rows = conn.execute(
                    "SELECT id FROM chunks_meta WHERE document_id = ?",
                    (document_id,),
                ).fetchall()
                chunk_ids = [r[0] for r in rows]

                # 删向量
                if chunk_ids:
                    placeholders = ",".join("?" * len(chunk_ids))
                    conn.execute(
                        f"DELETE FROM vec_chunks WHERE id IN ({placeholders})",
                        chunk_ids,
                    )
                    # 删 FTS（chunk_id TEXT affinity，需 CAST 才能匹配整型参数）
                    if self._fts_available:
                        conn.execute(
                            f"DELETE FROM bm25_index WHERE CAST(chunk_id AS INTEGER) IN ({placeholders})",
                            chunk_ids,
                        )
                    # 删 meta
                    conn.execute(
                        f"DELETE FROM chunks_meta WHERE id IN ({placeholders})",
                        chunk_ids,
                    )
                # 删文档
                conn.execute("DELETE FROM documents WHERE id = ?", (document_id,))
                conn.execute("COMMIT")
                return len(chunk_ids)
            except Exception as e:  # noqa: BLE001
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK")
                raise StoreError(f"删除文档失败: {e}") from e

    def delete_by_source(self, source: str, collection: str = "default") -> int:
        """按文件名删除（用于 `doc2mind remove <path>`）。

        Returns:
            删除的文档数（0 或 1）
        """
        with self._lock:
            self._require_open()
            try:
                rows = self._conn.execute(
                    "SELECT id FROM documents WHERE source = ? AND collection = ?",
                    (source, collection),
                ).fetchall()
                if not rows:
                    return 0
                doc_id = rows[0][0]
                self.delete_document(doc_id)
                return 1
            except StoreError:
                raise
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"按文件名删除失败: {e}") from e

    # --- AI 整理（curate）写入 ---
    @_retry_on_locked
    def update_document_meta(
        self,
        document_id: str,
        title: str | None = None,
        tags: list[str] | None = None,
        summary: str | None = None,
        enriched_at: str | None = None,
    ) -> bool:
        """更新文档的 AI 整理元数据（title/tags/summary/enriched_at，部分更新）。

        tags 序列化为 JSON 文本存储；enriched_at 传 None 时不覆盖原值。
        同时刷新 updated_at。

        Returns:
            True = 更新成功；False = 文档不存在。
        """
        with self._lock:
            self._require_open()
            try:
                sets: list[str] = ["updated_at = ?"]
                params: list[Any] = [_now_iso()]
                if title is not None:
                    sets.append("title = ?")
                    params.append(title)
                if tags is not None:
                    sets.append("tags = ?")
                    params.append(json.dumps(tags, ensure_ascii=False))
                if summary is not None:
                    sets.append("summary = ?")
                    params.append(summary)
                if enriched_at is not None:
                    sets.append("enriched_at = ?")
                    params.append(enriched_at)
                params.append(document_id)
                cur = self._conn.execute(
                    f"UPDATE documents SET {', '.join(sets)} WHERE id = ?",
                    params,
                )
                return cur.rowcount > 0
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"更新文档元数据失败: {e}") from e

    @_retry_on_locked
    def update_chunk_extra(
        self,
        chunk_id: int,
        extra_dict: dict[str, Any],
    ) -> bool:
        """更新分块的 extra JSON 字段（部分合并，不覆盖其他 key）。

        用于笔记批注等场景，app 层只需传 {key: value}，已有 key 保留。

        Returns:
            True = 更新成功（chunk 存在）；False = chunk 不存在。
        """
        with self._lock:
            self._require_open()
            try:
                # 单事务包裹 read-modify-write,防止跨进程并发 lost update
                # (与 replace_document / move_document 等写方法的事务模式一致)
                conn = self._conn
                conn.execute("BEGIN IMMEDIATE")
                try:
                    row = conn.execute(
                        "SELECT extra FROM chunks_meta WHERE id = ?", (chunk_id,)
                    ).fetchone()
                    if row is None:
                        conn.execute("ROLLBACK")
                        return False
                    current = json.loads(row[0]) if row[0] else {}
                    current.update(extra_dict)
                    conn.execute(
                        "UPDATE chunks_meta SET extra = ? WHERE id = ?",
                        (json.dumps(current, ensure_ascii=False), chunk_id),
                    )
                    conn.execute("COMMIT")
                    return True
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"更新分块 extra 失败: {e}") from e

    @_retry_on_locked
    def move_document(self, document_id: str, new_collection: str) -> bool:
        """把文档移动到另一个集合（单事务同步 documents / chunks_meta / bm25_index）。

        bm25_index 的 collection 是 UNINDEXED 列但参与检索过滤，必须同步更新，
        否则移动后 BM25 一路仍按旧集合过滤、结果与向量检索不一致。

        Returns:
            True = 已移动；False = 文档不存在或已在目标集合（no-op）。

        Raises:
            StoreError: 目标集合已有同名 source（UNIQUE(collection, source) 冲突）
                或数据库错误。
        """
        new_collection = (new_collection or "").strip()
        if not new_collection:
            raise StoreError("目标集合名不能为空")

        with self._lock:
            self._require_open()
            conn = self._conn
            try:
                row = conn.execute(
                    "SELECT collection FROM documents WHERE id = ?",
                    (document_id,),
                ).fetchone()
                if row is None or row[0] == new_collection:
                    return False

                conn.execute("BEGIN")
                conn.execute(
                    "UPDATE documents SET collection = ?, updated_at = ? WHERE id = ?",
                    (new_collection, _now_iso(), document_id),
                )
                chunk_ids = [
                    r[0] for r in conn.execute(
                        "SELECT id FROM chunks_meta WHERE document_id = ?",
                        (document_id,),
                    ).fetchall()
                ]
                if chunk_ids:
                    conn.execute(
                        "UPDATE chunks_meta SET collection = ? WHERE document_id = ?",
                        (new_collection, document_id),
                    )
                    if self._fts_available:
                        placeholders = ",".join("?" * len(chunk_ids))
                        # chunk_id 列 TEXT affinity 会把整数值存成文本，
                        # 与整型参数直接比较永不相等，必须 CAST 后再匹配
                        conn.execute(
                            f"UPDATE bm25_index SET collection = ? "
                            f"WHERE CAST(chunk_id AS INTEGER) IN ({placeholders})",
                            [new_collection, *chunk_ids],
                        )
                conn.execute("COMMIT")
                return True
            except StoreError:
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK")
                raise
            except Exception as e:  # noqa: BLE001
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK")
                raise StoreError(
                    f"移动文档失败: {e}（若为唯一约束冲突，目标集合已存在同名文档）"
                ) from e

    # --- 查询 ---
    def vector_search(
        self,
        query_vec,
        top_k: int = 10,
        collection: str | Sequence[str] | None = None,
    ) -> list[tuple[int, float]]:
        """向量余弦检索，返回 [(chunk_id, distance), ...]。

        distance 越小越相似（vec0 cosine 距离），调用方自行转 score。
        collection 支持单集合名或集合名列表（多选知识库）。
        """
        with self._lock:
            self._require_open()
            try:
                # vec0 MATCH：embedding 字段 + k 参数
                # 注意：collection 过滤在 chunks_meta 层做，需先取 top_k*N 再过滤
                cols = _normalize_collections(collection)
                fetch_n = top_k * 4 if cols else top_k
                cur = self._conn.execute(
                    """
                    SELECT id, distance
                    FROM vec_chunks
                    WHERE embedding MATCH ? AND k = ?
                    ORDER BY distance
                    """,
                    (_vector_to_bytes(query_vec), fetch_n),
                )
                rows = cur.fetchall()
                if not cols:
                    return [(int(r[0]), float(r[1])) for r in rows][:top_k]
                # collection 过滤（支持多集合）
                result: list[tuple[int, float]] = []
                for cid, dist in rows:
                    meta = self._conn.execute(
                        "SELECT collection FROM chunks_meta WHERE id = ?",
                        (cid,),
                    ).fetchone()
                    if meta and meta[0] in cols:
                        result.append((int(cid), float(dist)))
                        if len(result) >= top_k:
                            break
                return result
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"向量检索失败: {e}") from e

    def bm25_search(
        self,
        query: str,
        top_k: int = 10,
        collection: str | Sequence[str] | None = None,
    ) -> list[tuple[int, float]]:
        """BM25 关键词检索，返回 [(chunk_id, bm25_score), ...]。

        score 越大越相关。collection 支持单集合名或集合名列表。

        注意：用独立 `_fts_conn` 跑查询，避开 vec0 扩展与 trigram tokenizer
        在同 connection 上评估中文 bm25() 时的 IntegrityError: datatype mismatch。
        """
        if not self._fts_available:
            return []
        # 构造中文友好的 MATCH 表达式
        match_expr = _build_fts5_match(query)
        if not match_expr:
            return []
        with self._lock:
            self._require_open()
            if self._fts_conn is None:
                return []
            try:
                col_filter = ""
                params: list[Any] = [match_expr]
                cols = _normalize_collections(collection)
                if cols:
                    placeholders = ",".join("?" for _ in sorted(cols))
                    col_filter = f"AND collection IN ({placeholders})"
                    params.extend(sorted(cols))
                # LIMIT 用字符串拼接（int 已强类型校验，无注入风险）：
                # FTS5 trigram + LIMIT ? placeholder 触发 IntegrityError: datatype mismatch
                cur = self._fts_conn.execute(
                    f"""
                    SELECT chunk_id, bm25(bm25_index) AS score
                    FROM bm25_index
                    WHERE bm25_index MATCH ? {col_filter}
                    ORDER BY score DESC
                    LIMIT {int(top_k * 4)}
                    """,
                    params,
                )
                rows = cur.fetchall()
                # chunk_id 在 FTS5 UNINDEXED 列中存为 TEXT，需 cast
                return [(int(str(r[0])), float(r[1])) for r in rows][:top_k]
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"BM25 检索失败: {e}") from e

    def get_chunks(self, chunk_ids: Sequence[int]) -> list[StoredChunk]:
        """按 chunk_id 批量取元数据。"""
        if not chunk_ids:
            return []
        with self._lock:
            self._require_open()
            try:
                placeholders = ",".join("?" * len(chunk_ids))
                cur = self._conn.execute(
                    f"""
                    SELECT cm.id, cm.content, cm.source, cm.format, cm.doc_type, cm.page,
                           cm.heading, d.file_hash, cm.collection, d.created_at,
                           cm.tokens, cm.chunk_index, cm.extra, cm.sheet, cm.slide, cm.language
                    FROM chunks_meta cm
                    LEFT JOIN documents d ON d.id = cm.document_id
                    WHERE cm.id IN ({placeholders})
                    """,
                    list(chunk_ids),
                )
                rows = cur.fetchall()
                # 保持调用顺序
                id_to_row = {r[0]: r for r in rows}
                result: list[StoredChunk] = []
                for cid in chunk_ids:
                    r = id_to_row.get(cid)
                    if r is None:
                        continue
                    result.append(
                        StoredChunk(
                            id=r[0], content=r[1], source=r[2], format=r[3],
                            doc_type=r[4], page=r[5], heading=r[6],
                            file_hash=r[7] or "", collection=r[8], created_at=r[9],
                            tokens=r[10], chunk_index=r[11],
                            extra_metadata=json.loads(r[12]) if r[12] else {},
                        )
                    )
                return result
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"获取分块失败: {e}") from e

    def list_chunks_by_document(
        self, document_id: str, limit: int = 100
    ) -> list[StoredChunk]:
        """按文档取分块（文档详情预览用），按 chunk_index 排序。"""
        if limit <= 0:
            return []
        with self._lock:
            self._require_open()
            try:
                cur = self._conn.execute(
                    """
                    SELECT cm.id, cm.content, cm.source, cm.format, cm.doc_type, cm.page,
                           cm.heading, d.file_hash, cm.collection, d.created_at,
                           cm.tokens, cm.chunk_index, cm.extra, cm.sheet, cm.slide, cm.language
                    FROM chunks_meta cm
                    LEFT JOIN documents d ON d.id = cm.document_id
                    WHERE cm.document_id = ?
                    ORDER BY cm.chunk_index ASC
                    LIMIT ?
                    """,
                    (document_id, limit),
                )
                rows = cur.fetchall()
                return [
                    StoredChunk(
                        id=r[0], content=r[1], source=r[2], format=r[3],
                        doc_type=r[4], page=r[5], heading=r[6],
                        file_hash=r[7] or "", collection=r[8], created_at=r[9],
                        tokens=r[10], chunk_index=r[11],
                        extra_metadata=json.loads(r[12]) if r[12] else {},
                    )
                    for r in rows
                ]
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"获取文档分块失败: {e}") from e

    def list_chunk_contents(
        self, collection: str | None = None
    ) -> list[tuple[int, str]]:
        """按集合列出 (chunk_id, content)，重建索引（重新嵌入）用。"""
        with self._lock:
            self._require_open()
            try:
                sql = "SELECT id, content FROM chunks_meta"
                params: list[Any] = []
                if collection:
                    sql += " WHERE collection = ?"
                    params.append(collection)
                rows = self._conn.execute(sql, params).fetchall()
                return [(int(r[0]), str(r[1])) for r in rows]
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"列出分块内容失败: {e}") from e

    @_retry_on_locked
    def update_embeddings(
        self, chunk_id_emb_pairs: Sequence[tuple[int, object]]
    ) -> int:
        """批量更新已有 chunk 的向量（重建索引用）。

        Args:
            chunk_id_emb_pairs: [(chunk_id, embedding), ...]

        Returns:
            实际更新的行数
        """
        if not chunk_id_emb_pairs:
            return 0
        with self._lock:
            self._require_open()
            conn = self._conn
            try:
                conn.execute("BEGIN")
                updated = 0
                for cid, emb in chunk_id_emb_pairs:
                    emb_bytes = _vector_to_bytes(emb)
                    cur = conn.execute(
                        "UPDATE vec_chunks SET embedding = ? WHERE id = ?",
                        (emb_bytes, int(cid)),
                    )
                    updated += cur.rowcount
                conn.execute("COMMIT")
                return updated
            except Exception as e:  # noqa: BLE001
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK")
                raise StoreError(f"更新向量失败: {e}") from e

    @_retry_on_locked
    def rebuild_chunk_embeddings(
        self,
        chunk_id_emb_pairs: Sequence[tuple[int, object]],
        new_dim: int,
    ) -> int:
        """按新维度重建向量表并回填全部向量（换模型且维度变化时调用）。

        Args:
            chunk_id_emb_pairs: [(chunk_id, embedding), ...]，该集合全部 chunk；
                空列表时仅重建空表结构（切换模型后首次摄入前的对齐）。
            new_dim: 新模型向量维度。

        Returns:
            实际插入的行数
        """
        with self._lock:
            self._require_open()
            conn = self._conn
            try:
                conn.execute("BEGIN")
                conn.execute("DROP TABLE IF EXISTS vec_chunks")
                conn.execute(_VEC_SQL_TEMPLATE.format(dim=int(new_dim)))
                inserted = 0
                for cid, emb in chunk_id_emb_pairs:
                    emb_bytes = _vector_to_bytes(emb)
                    conn.execute(
                        "INSERT INTO vec_chunks(id, embedding) VALUES (?, ?)",
                        (int(cid), emb_bytes),
                    )
                    inserted += 1
                conn.execute("COMMIT")
                # 同步存储维度，供后续表结构操作保持一致
                self.embedding_dim = int(new_dim)
                return inserted
            except Exception as e:  # noqa: BLE001
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK")
                raise StoreError(f"重建向量表失败: {e}") from e

    # sort 白名单 → SQL 片段（避免任意列名注入）
    SORT_CLAUSES: dict[str, str] = {
        "created_at_desc": "created_at DESC",
        "created_at_asc": "created_at ASC",
        "updated_at_desc": "updated_at DESC",
        "updated_at_asc": "updated_at ASC",
        "name_asc": "source COLLATE NOCASE ASC",
        "name_desc": "source COLLATE NOCASE DESC",
    }

    def list_documents(
        self,
        collection: str | None = None,
        limit: int = 100,
        offset: int = 0,
        format: str | None = None,
        sort: str = "created_at_desc",
        q: str | None = None,
    ) -> list[StoredDocument]:
        """列出文档（可按 collection / format / q 过滤，sort 白名单排序）。"""
        with self._lock:
            self._require_open()
            try:
                sql = (
                    "SELECT id, source, collection, format, file_hash, size_bytes,"
                    " page_count, chunk_count, created_at, updated_at,"
                    " title, tags, summary, enriched_at FROM documents"
                )
                params: list[Any] = []
                conds: list[str] = []
                if collection:
                    conds.append("collection = ?")
                    params.append(collection)
                if format:
                    conds.append("format = ?")
                    params.append(format)
                if q:
                    conds.append("(source LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\')")
                    # 转义 LIKE 通配符,避免用户输入的 % / _ 被当作通配符
                    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    pattern = f"%{escaped}%"
                    params.extend([pattern, pattern, pattern])
                if conds:
                    sql += " WHERE " + " AND ".join(conds)
                order = self.SORT_CLAUSES.get(sort, "created_at DESC")
                sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
                params.extend([limit, offset])
                cur = self._conn.execute(sql, params)
                return [self._row_to_document(r) for r in cur.fetchall()]
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"列出文档失败: {e}") from e

    def count_documents(
        self, collection: str | None = None, format: str | None = None, q: str | None = None
    ) -> int:
        """统计文档数（可按 collection / format / q 过滤）。"""
        with self._lock:
            self._require_open()
            try:
                conds: list[str] = []
                params: list[Any] = []
                if collection:
                    conds.append("collection = ?")
                    params.append(collection)
                if format:
                    conds.append("format = ?")
                    params.append(format)
                if q:
                    conds.append("(source LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\')")
                    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    pattern = f"%{escaped}%"
                    params.extend([pattern, pattern, pattern])
                sql = "SELECT COUNT(*) FROM documents"
                if conds:
                    sql += " WHERE " + " AND ".join(conds)
                row = self._conn.execute(sql, params).fetchone()
                return int(row[0]) if row else 0
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"统计文档失败: {e}") from e

    def get_document_by_id(self, document_id: str) -> StoredDocument | None:
        """按主键取单条文档（避免全表扫描）。"""
        with self._lock:
            self._require_open()
            try:
                row = self._conn.execute(
                    """
                    SELECT id, source, collection, format, file_hash,
                           size_bytes, page_count, chunk_count,
                           created_at, updated_at, title, tags, summary, enriched_at
                    FROM documents WHERE id = ?
                    """,
                    (document_id,),
                ).fetchone()
                if row is None:
                    return None
                return self._row_to_document(row)
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"获取文档失败: {e}") from e

    @staticmethod
    def _row_to_document(row: Sequence) -> StoredDocument:
        """把 SELECT 行（14 列，含 AI 整理元数据）转为 StoredDocument。"""
        tags: list[str] | None = None
        if row[11]:
            try:
                parsed = json.loads(row[11])
                if isinstance(parsed, list):
                    tags = [str(t) for t in parsed]
            except (ValueError, TypeError):
                tags = None
        return StoredDocument(
            id=row[0], source=row[1], collection=row[2], format=row[3],
            file_hash=row[4], size_bytes=row[5], page_count=row[6],
            chunk_count=row[7], created_at=row[8], updated_at=row[9],
            title=row[10], tags=tags, summary=row[12], enriched_at=row[13],
        )

    def find_document_id_by_hash(
        self, file_hash: str, collection: str
    ) -> str | None:
        """按 file_hash 找已存在的文档 ID（增量去重用）。

        必须走 self._lock：store 可能是跨线程共享的单例（HTTP 服务），
        裸访问 _conn 会与其它线程的写操作并发，触发
        `Recursive use of cursors not allowed` / SQLITE_BUSY。
        """
        with self._lock:
            if self._conn is None:
                return None
            try:
                row = self._conn.execute(
                    "SELECT id FROM documents WHERE file_hash = ? AND collection = ?",
                    (file_hash, collection),
                ).fetchone()
                return row[0] if row else None
            except sqlite3.Error:
                return None

    def get_stats(self) -> StoreStats:
        """获取存储统计。"""
        with self._lock:
            self._require_open()
            try:
                doc_total = self._conn.execute(
                    "SELECT COUNT(*) FROM documents"
                ).fetchone()[0]
                chunk_total = self._conn.execute(
                    "SELECT COUNT(*) FROM chunks_meta"
                ).fetchone()[0]
                # 各集合 (doc_count, chunk_count, size_bytes)
                rows = self._conn.execute(
                    """
                    SELECT d.collection,
                           COUNT(DISTINCT d.id),
                           COUNT(c.id),
                           COALESCE(SUM(d.size_bytes), 0)
                    FROM documents d
                    LEFT JOIN chunks_meta c ON c.document_id = d.id
                    GROUP BY d.collection
                    """
                ).fetchall()
                collections = {
                    r[0]: (int(r[1]), int(r[2]), int(r[3])) for r in rows
                }
                return StoreStats(
                    total_documents=doc_total,
                    total_chunks=chunk_total,
                    collections=collections,
                )
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"获取统计失败: {e}") from e

    # --- 内部 ---
    def _require_open(self) -> None:
        if self._conn is None:
            raise StoreError("存储未打开，请先调用 open() 或用 with 语句")


# --- 工具函数 ---
def _vector_to_bytes(vec) -> bytes:
    """把向量序列化为 vec0 接受的 BLOB（小端 float32）。"""
    import struct

    import numpy as np  # 局部导入，减少冷启动

    arr = np.asarray(vec, dtype=np.float32)
    return struct.pack(f"{arr.size}f", *arr.flatten())


def _now_iso() -> str:
    """当前 ISO8601 时间（带本地时区）。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _new_id() -> str:
    """生成文档/占位记录主键（与 HTTP 层一致：uuid4 hex）。"""
    return uuid.uuid4().hex
