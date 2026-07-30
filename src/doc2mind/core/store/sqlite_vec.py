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

import json
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

from doc2mind.core.chunker.base import Chunk


class StoreError(Exception):
    """存储异常。"""


# --- FTS5 MATCH 表达式构造（中文友好）---
import re as _re

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
    """存储中的文档记录。"""

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


@dataclass(frozen=True)
class StoreStats:
    """存储统计。"""

    total_documents: int
    total_chunks: int
    collections: dict[str, tuple[int, int]]  # name -> (doc_count, chunk_count)


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
                )
                # WAL 提升并发读
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("PRAGMA foreign_keys=ON")

                # 加载 sqlite-vec 扩展
                self._load_vec_extension(conn)

                # 建表
                conn.executescript(_SCHEMA_SQL)
                conn.execute(_VEC_SQL_TEMPLATE.format(dim=self.embedding_dim))

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
                    )
                    fts_conn.execute("PRAGMA journal_mode=WAL")
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

    def __enter__(self) -> "VectorStore":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

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
            )
        except Exception as e:  # noqa: BLE001
            raise StoreError(
                "sqlite-vec 扩展加载失败。请运行：pip install sqlite-vec"
            ) from e

    @property
    def fts_available(self) -> bool:
        """BM25 (FTS5) 是否可用。"""
        return self._fts_available

    # --- 写入 ---
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
                inserted = 0
                for chunk, emb in zip(chunks, embeddings):
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
                        conn.execute("ROLLBACK")
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

                # 4. 更新文档 chunk_count
                conn.execute(
                    "UPDATE documents SET chunk_count = chunk_count + ?, updated_at = ? WHERE id = ?",
                    (inserted, _now_iso(), document_id),
                )
                conn.execute("COMMIT")
                return inserted
            except StoreError:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise
            except Exception as e:  # noqa: BLE001
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                raise StoreError(f"插入分块失败: {e}") from e

    # --- 删除 ---
    def delete_document(self, document_id: str) -> int:
        """删除文档及其所有分块与向量。"""
        with self._lock:
            self._require_open()
            conn = self._conn
            try:
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
                    # 删 FTS
                    if self._fts_available:
                        conn.execute(
                            f"DELETE FROM bm25_index WHERE chunk_id IN ({placeholders})",
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
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
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

    # --- 查询 ---
    def vector_search(
        self,
        query_vec,
        top_k: int = 10,
        collection: str | None = None,
    ) -> list[tuple[int, float]]:
        """向量余弦检索，返回 [(chunk_id, distance), ...]。

        distance 越小越相似（vec0 cosine 距离），调用方自行转 score。
        """
        with self._lock:
            self._require_open()
            try:
                # vec0 MATCH：embedding 字段 + k 参数
                # 注意：collection 过滤在 chunks_meta 层做，需先取 top_k*N 再过滤
                fetch_n = top_k * 4 if collection else top_k
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
                if collection is None:
                    return [(int(r[0]), float(r[1])) for r in rows][:top_k]
                # collection 过滤
                result: list[tuple[int, float]] = []
                for cid, dist in rows:
                    meta = self._conn.execute(
                        "SELECT collection FROM chunks_meta WHERE id = ?",
                        (cid,),
                    ).fetchone()
                    if meta and meta[0] == collection:
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
        collection: str | None = None,
    ) -> list[tuple[int, float]]:
        """BM25 关键词检索，返回 [(chunk_id, bm25_score), ...]。

        score 越大越相关。

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
                if collection:
                    col_filter = "AND collection = ?"
                    params = [match_expr, collection]
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

    def list_documents(
        self,
        collection: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[StoredDocument]:
        """列出文档。"""
        with self._lock:
            self._require_open()
            try:
                sql = "SELECT id, source, collection, format, file_hash, size_bytes, page_count, chunk_count, created_at, updated_at FROM documents"
                params: list[Any] = []
                if collection:
                    sql += " WHERE collection = ?"
                    params.append(collection)
                sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
                params.extend([limit, offset])
                cur = self._conn.execute(sql, params)
                rows = cur.fetchall()
                return [
                    StoredDocument(
                        id=r[0], source=r[1], collection=r[2], format=r[3],
                        file_hash=r[4], size_bytes=r[5], page_count=r[6],
                        chunk_count=r[7], created_at=r[8], updated_at=r[9],
                    )
                    for r in rows
                ]
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"列出文档失败: {e}") from e

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
                # 各集合 (doc_count, chunk_count)
                rows = self._conn.execute(
                    """
                    SELECT d.collection,
                           COUNT(DISTINCT d.id),
                           COUNT(c.id)
                    FROM documents d
                    LEFT JOIN chunks_meta c ON c.document_id = d.id
                    GROUP BY d.collection
                    """
                ).fetchall()
                collections = {
                    r[0]: (int(r[1]), int(r[2])) for r in rows
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
