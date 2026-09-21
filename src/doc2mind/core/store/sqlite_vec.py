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
import logging
import math
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
from doc2mind.core.store.tokenizer import (
    segment,
    segment_query,
    token_counts,
)

logger = logging.getLogger(__name__)

# 探针缓存：本进程内当前 sqlite-vec 扩展是否支持 INT8 列写入。
# None = 未探测；True/False = 缓存结果。避免每次 open() 都建表再删。
_INT8_SUPPORTED: bool | None = None


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


def _build_fts5_match_unicode(tokens: list[str]) -> str:
    """unicode61（jieba 分词）模式：把 token 用 OR + 引号拼接为 MATCH 表达式。

    unicode61 把连续 CJK 保留为单个 token（含多字词），故对 jieba 已按空格
    切分的中文词/英文词/数字做精确匹配即可；空返回 ""。
    token 内的双引号按 FTS5 规则转义为两个引号，否则查询含 `"` 时
    MATCH 表达式引号失配，FTS5 把后续裸词（如 ROW）当列名报
    "no such column"（真实故障：库内代码片段含 `"ROW"`）。
    """
    quoted = [f'"{t.replace(chr(34), chr(34) * 2)}"' for t in tokens if t]
    return " OR ".join(quoted) if quoted else ""


# ---- 短词（<3 chars）LIKE 兜底参数 ----
# trigram tokenizer 无法索引 2 字词/2 字符缩写（气缸/IP/5A），bm25_search 对它们
# 改用 `content LIKE '%词%'` 补充召回。单次命中的固定贡献分（非真实 BM25 值，
# 只需>0 让 match_type 正确呈现 hybrid）；最多取前 _MAX_SHORT_TOKENS 个短词参与，
# 限制 LIKE 全表扫描次数与噪声。
_SHORT_TOKEN_SCORE = 1.0
_MAX_SHORT_TOKENS = 3


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
-- 空集合也需要持久化，但不应伪装成 documents 表中的占位文档。
CREATE TABLE IF NOT EXISTS collections (
    name       TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

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
    deleted_at    TEXT                          -- 软删除标记 NULL=正常；非NULL=软删除时间
    -- 注意：原 UNIQUE 约束已替换为 _migrate_documents_meta 里的部分索引
    -- （WHERE deleted_at IS NULL），软删文档需让出 collection+source 给活跃文档
);
CREATE INDEX IF NOT EXISTS idx_documents_collection ON documents(collection);
CREATE INDEX IF NOT EXISTS idx_documents_hash      ON documents(file_hash);
-- 软删除标记：deleted_at 非 NULL = 已软删除（chunks/向量已物理删，但
-- documents 行保留以支持恢复与审计）。list/search 路径须过滤 IS NULL。
-- 列由 _migrate_documents_meta 幂等补齐；旧库缺列自动 ALTER。

-- 软删除审计表（trash）：软删除时写入 document_id + 整行 snapshot。
-- GC 由 purge_trash(older_than_days) 物理清空 trash 行 + documents 行。
CREATE TABLE IF NOT EXISTS trash (
    document_id   TEXT    PRIMARY KEY,            -- 与 documents.id 对齐
    source        TEXT    NOT NULL,
    collection    TEXT    NOT NULL,
    deleted_at    TEXT    NOT NULL,
    snapshot_json TEXT    NOT NULL,               -- documents 整行 JSON（不含 id 重复）
    purged_at     TEXT                             -- GC 完成时间；NULL = 仍在 30 天保留期
);
CREATE INDEX IF NOT EXISTS idx_trash_deleted_at ON trash(deleted_at);
CREATE INDEX IF NOT EXISTS idx_trash_purged_at  ON trash(purged_at);

-- AI 知识库自动整理（curate）留痕表：每次 curate() 跑完写一行。
-- 让质量看板显示「过去 7 天跑过几次整理、动了哪些文档」。
CREATE TABLE IF NOT EXISTS curate_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT    NOT NULL,
    finished_at     TEXT    NOT NULL,
    dry_run         INTEGER NOT NULL,              -- 0/1
    collection      TEXT,                          -- NULL = 跨集合
    actions_json    TEXT    NOT NULL,              -- JSON 列表
    changed_doc_ids TEXT    NOT NULL DEFAULT '[]', -- JSON 列表
    skipped_count   INTEGER NOT NULL DEFAULT 0,
    error_count     INTEGER NOT NULL DEFAULT 0,
    elapsed_ms      INTEGER NOT NULL DEFAULT 0,
    note            TEXT                            -- 触发来源（agent/http/auto_curate 等）
);
CREATE INDEX IF NOT EXISTS idx_curate_runs_started ON curate_runs(started_at);

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

_FTS_SQL_UNICODE61 = """
-- FTS5 全文索引（BM25 用，jieba 中文分词模式）
-- content 存储的是 jieba 分词后以空格拼接的文本；
-- unicode61 按空白切分，从而对中文多字词做精确 BM25 匹配。
CREATE VIRTUAL TABLE IF NOT EXISTS bm25_index USING fts5(
    content,
    collection UNINDEXED,
    chunk_id UNINDEXED,
    tokenize = 'unicode61'
);
"""

_VEC_SQL_TEMPLATE = """
CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
    id INTEGER PRIMARY KEY,
    embedding FLOAT[{dim}] distance_metric=cosine
);
"""

_VEC_SQL_TEMPLATE_INT8 = """
CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
    id INTEGER PRIMARY KEY,
    embedding INT8[{dim}] distance_metric=cosine
);
"""

# 稀疏向量倒排索引（D2）：每 (term, chunk_id) 存 L2 归一化词权重。
# 拿查询归一化稀疏向量与命中 chunk 权重做内积 = 稀疏向量余弦相似度，
# 作为第三条（词法稀疏）召回路，独立于 FTS5 BM25 的排名路径。
_SPARSE_SQL = """
CREATE TABLE IF NOT EXISTS sparse_terms (
    term       TEXT    NOT NULL,
    chunk_id   INTEGER NOT NULL,
    weight     REAL    NOT NULL,
    collection TEXT    NOT NULL,
    PRIMARY KEY (term, chunk_id)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_sparse_chunk ON sparse_terms(chunk_id);
"""


def sparse_token_weights(
    text: str, jieba_enabled: bool, max_terms: int = 256
) -> list[tuple[str, float]]:
    """文本 → L2 归一化稀疏词权重 [(term, weight), ...]。

    词权重 = (1 + log(tf))，再做 L2 归一化，使"查询·文档"内积 = 稀疏向量余弦。
    归一化后 `query · doc` 恰为两向量余弦 ∈ [0,1]，可直接与忠实余弦对齐，
    不会重现"60+"量纲污染。过滤单字/单字母噪声（见 tokenize.token_counts）。

    Args:
        text: 文档内容或查询文本
        jieba_enabled: 是否用 jieba 分词（与 BM25 中文路径一致）
        max_terms: 参与检索的查询词数量上限（性能保护，仅对查询有意义）
    """
    counts = token_counts(text, jieba_enabled)
    if not counts:
        return []
    norm = sum((1.0 + math.log(float(c))) ** 2 for c in counts.values()) ** 0.5
    if norm <= 0:
        return []
    items: list[tuple[str, float]] = []
    for term, count in counts.items():
        w = (1.0 + math.log(float(count))) / norm
        items.append((term, w))
    items.sort(key=lambda kv: (-kv[1], kv[0]))  # 权重降序，词字典序 tie-break
    return items[:max_terms]


class VectorStore:
    """sqlite-vec 向量存储封装。

    线程安全：单个 connection 由 lock 保护；多线程建议每线程一个 VectorStore。
    """

    def __init__(
        self,
        db_path: Path,
        embedding_dim: int,
        *,
        bm25_jieba_enabled: bool = False,
        vector_quantize: str = "none",
        sparse_retrieval_enabled: bool = False,
    ) -> None:
        self.db_path = Path(db_path)
        self.embedding_dim = int(embedding_dim)
        self.bm25_jieba_enabled = bool(bm25_jieba_enabled)
        self.vector_quantize = str(vector_quantize) if vector_quantize in ("int8", "none") else "none"
        # 稀疏向量召回路（D2）：独立倒排索引（sparse_terms）+ sparse_search，
        # 作为第三条词法稀疏召回路并入三路 RRF 融合。默认关闭（向后兼容，
        # 旧库不受影响）；开启后对存量索引一次性回填稀疏词。
        self.sparse_retrieval_enabled = bool(sparse_retrieval_enabled)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        # FTS5 查询独立 connection（避开 vec0 扩展与 trigram BM25 评估冲突）
        self._fts_conn: sqlite3.Connection | None = None
        self._fts_available = False
        # 存储类型（在 open() 中设立）："FLOAT" 或 "INT8"
        self._vec_storage_type: str = "FLOAT"
        # INT8 列是否受本机 sqlite-vec 支持（open() 中由探针填充）
        self._int8_supported: bool | None = None

    def _to_bytes(self, vec) -> bytes:
        """按当前存储类型序列化向量（FLOAT=float32 / INT8=量化）。"""
        if self._vec_storage_type == "INT8":
            return _vector_to_bytes_int8(vec)
        return _vector_to_bytes(vec)

    @property
    def dimension(self) -> int:
        """向量维度别名（等价 embedding_dim）。"""
        return self.embedding_dim

    # --- FTS5 tokenizer（jieba 中文模式）---
    def _fts_create_sql(self) -> str:
        """按当前配置返回 FTS5 建表 SQL。"""
        return _FTS_SQL_UNICODE61 if self.bm25_jieba_enabled else _FTS_SQL

    def _detect_fts_tokenizer(self, conn: sqlite3.Connection) -> str | None:
        """读回磁盘上 bm25_index 的 tokenizer（trigram/unicode61）；不存在返回 None。"""
        row = conn.execute(
            "SELECT sql FROM sqlite_master"
            " WHERE type='table' AND name='bm25_index'"
        ).fetchone()
        if row and row[0]:
            sql = str(row[0])
            if "trigram" in sql:
                return "trigram"
            if "unicode61" in sql:
                return "unicode61"
        return None

    def _sync_bm25_tokenizer(self, conn: sqlite3.Connection) -> None:
        """确保磁盘 FTS tokenizer 与配置一致。

        配置（bm25_jieba_enabled）与实际 FTS tokenizer 不符、且索引非空时，
        一次性重建 bm25_index（DELETE + 按 jieba segment(content) 重灌）。
        空库/新库直接由调用方 executescript 创建，此处为 no-op。
        """
        desired = "unicode61" if self.bm25_jieba_enabled else "trigram"
        actual = self._detect_fts_tokenizer(conn)
        if actual is None or actual == desired:
            return
        # tokenizer 不一致 → 校验是否已有数据需要重建
        try:
            (cnt,) = conn.execute(
                "SELECT COUNT(*) FROM chunks_meta"
            ).fetchone()
        except Exception:  # noqa: BLE001
            cnt = 0
        if cnt == 0:
            # 空库：仅需切换 tokenizer，无需回填
            conn.execute("DROP TABLE IF EXISTS bm25_index")
            return
        conn.execute("BEGIN")
        try:
            conn.execute("DROP TABLE IF EXISTS bm25_index")
            conn.execute(self._fts_create_sql())
            rows = conn.execute(
                "SELECT id, content, collection FROM chunks_meta"
            ).fetchall()
            conn.executemany(
                "INSERT INTO bm25_index(content, collection, chunk_id) VALUES (?, ?, ?)",
                [
                    (
                        segment(content, self.bm25_jieba_enabled),
                        collection,
                        chunk_id,
                    )
                    for chunk_id, content, collection in rows
                ],
            )
            conn.execute("COMMIT")
        except Exception:  # noqa: BLE001
            conn.execute("ROLLBACK")
            raise

    # --- 稀疏向量倒排索引（D2）维护 ---
    def _insert_sparse_terms_in_txn(
        self,
        conn: sqlite3.Connection,
        chunk_id: int,
        content: str,
        collection: str,
    ) -> None:
        """在已开启事务内为单个 chunk 写入稀疏倒排项（首删后插，幂等）。"""
        self._delete_sparse_in_txn(conn, [chunk_id])
        items = sparse_token_weights(content, self.bm25_jieba_enabled)
        if items:
            conn.executemany(
                "INSERT INTO sparse_terms(term, chunk_id, weight, collection)"
                " VALUES (?, ?, ?, ?)",
                [(t, chunk_id, w, collection) for t, w in items],
            )

    @staticmethod
    def _delete_sparse_in_txn(
        conn: sqlite3.Connection, chunk_ids: Sequence[int]
    ) -> None:
        """删除一批 chunk 的稀疏倒排项（幂等，空列表 no-op）。

        稀疏关闭（sparse_terms 未建表）的库也应正常删除/替换 —— 不做表存在性
        探测会抛 no such table，污染 delete/replace 事务路径。
        """
        ids = list(chunk_ids)
        if not ids:
            return
        has_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sparse_terms'"
        ).fetchone()
        if has_table is None:
            return
        placeholders = ",".join("?" * len(ids))
        conn.execute(
            f"DELETE FROM sparse_terms WHERE chunk_id IN ({placeholders})",
            ids,
        )

    def _sync_sparse_index(self, conn: sqlite3.Connection) -> None:
        """稀疏索引建表后对存量分块一次性回填（D2，幂等）。

        仅当 sparse_terms 为空、且库里已有分块时执行（新库/空库跳过，避免
        每次 open() 全量重算开销）。开启 sparse_retrieval_enabled 的旧库升级
        由此生效。
        """
        try:
            (existing,) = conn.execute("SELECT COUNT(*) FROM sparse_terms").fetchone()
            (chunks,) = conn.execute("SELECT COUNT(*) FROM chunks_meta").fetchone()
        except Exception:  # noqa: BLE001 — 建表前的稀疏表不存在视为 0
            return
        if existing > 0 or chunks == 0:
            return
        rows = conn.execute(
            "SELECT id, content, collection FROM chunks_meta"
        ).fetchall()
        conn.execute("BEGIN")
        try:
            for chunk_id, content, collection in rows:
                self._insert_sparse_terms_in_txn(conn, chunk_id, content, collection)
            conn.execute("COMMIT")
        except Exception:  # noqa: BLE001
            conn.execute("ROLLBACK")
            raise

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
                # 兼容旧版本的占位文档：先登记集合，再删除伪文档，避免污染统计。
                conn.execute(
                    "INSERT OR IGNORE INTO collections(name, created_at) "
                    "SELECT DISTINCT collection, MIN(created_at) FROM documents "
                    "WHERE source = '__collection_placeholder__' GROUP BY collection"
                )
                conn.execute(
                    "DELETE FROM documents WHERE source = '__collection_placeholder__' "
                    "AND format = 'placeholder'"
                )
                # 真实文档所属集合也登记到集合表，保证旧库升级后集合不丢。
                conn.execute(
                    "INSERT OR IGNORE INTO collections(name, created_at) "
                    "SELECT collection, MIN(created_at) FROM documents GROUP BY collection"
                )
                # 向量存储类型（B4 量化）：默认 FLOAT（最高精度）。仅当配置
                # vector_quantize=="int8" 且本机 sqlite-vec 探测支持 INT8 列写入时
                # 才建 INT8 表；探测不支持则回退 FLOAT 并告警，绝不破坏建库。
                self._int8_supported = _probe_vec_int8_supported(conn, self.embedding_dim)
                if self.vector_quantize == "int8" and self._int8_supported:
                    conn.execute(
                        _VEC_SQL_TEMPLATE_INT8.format(dim=self.embedding_dim)
                    )
                    self._vec_storage_type = "INT8"
                else:
                    conn.execute(_VEC_SQL_TEMPLATE.format(dim=self.embedding_dim))
                    if self.vector_quantize == "int8":
                        logger.warning(
                            "vector_quantize=int8 已请求但当前 sqlite-vec 不支持 "
                            "INT8 列写入，已回退 float32 存储（零风险）。"
                        )
                    self._vec_storage_type = "FLOAT"
                # 维度以磁盘上已有表的实际建表 SQL 为准：CREATE ... IF NOT
                # EXISTS 在表已存在时静默跳过，而构造传入的维度可能仍是
                # 模型加载前的预设值（如默认 512）。回读真实维度，让
                # reindex 的维度判断、后续写入都以表为准。存储类型同样以
                # 表实为准（老库恒为 FLOAT，回退除量化建表）。
                row = conn.execute(
                    "SELECT sql FROM sqlite_master"
                    " WHERE type='table' AND name='vec_chunks'"
                ).fetchone()
                if row and row[0]:
                    m = _re.search(r"(INT8|FLOAT)\[(\d+)\]", str(row[0]))
                    if m:
                        self._vec_storage_type = m.group(1)
                        self.embedding_dim = int(m.group(2))

                # FTS5（可选）：先按配置兜底 tokenizer 一致性（老库切换 jieba 模式
                # 时重建索引），再建表/回填。
                try:
                    self._sync_bm25_tokenizer(conn)
                    conn.executescript(self._fts_create_sql())
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

                # 稀疏向量倒排索引（D2）：开启时建表 + 对存量分块一次性回填
                if self.sparse_retrieval_enabled:
                    conn.executescript(_SPARSE_SQL)
                    self._sync_sparse_index(conn)

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
    # documents 表 AI 整理元数据列（curate 功能 + 软删除）。旧库缺列时补齐。
    # 注意：deleted_at 必须与 curate 元数据列在同一处补齐，否则会因迁移顺序
    # 不一致导致 list_documents 报「no such column」。
    _DOCUMENTS_META_COLUMNS: tuple[tuple[str, str], ...] = (
        ("title", "TEXT"),
        ("tags", "TEXT"),
        ("summary", "TEXT"),
        ("enriched_at", "TEXT"),
        # 软删除标记：NULL = 正常；非 NULL = 软删除时间（ISO8601）。
        ("deleted_at", "TEXT"),
    )

    @staticmethod
    def _migrate_documents_meta(conn: sqlite3.Connection) -> None:
        """documents 表幂等补齐 AI 整理元数据列（ALTER TABLE ADD COLUMN）。

        CREATE TABLE IF NOT EXISTS 对已存在的旧表是 no-op，缺的列需在这里
        显式补上；重复调用安全（已存在的列跳过）。

        顺带把旧库"完整 UNIQUE(collection, source)"索引升级为部分索引
        "WHERE deleted_at IS NULL"——软删文档需让出 (collection, source) 给
        重新摄入的新文档，否则会触发 UNIQUE 冲突。
        """
        existing = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(documents)").fetchall()
        }
        for name, decl in VectorStore._DOCUMENTS_META_COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE documents ADD COLUMN {name} {decl}")

        # 索引迁移：旧库（CREATE TABLE 内的 UNIQUE(collection, source) 约束）
        # 无法在线移除（SQLite 不支持 DROP CONSTRAINT，只能重建表），因此
        # **保留**旧库的完整 UNIQUE 约束——其已知限制是"软删后重新摄入同源
        # 文件会触发 UNIQUE 冲突"；新库（_SCHEMA_SQL）已改用部分索引
        # WHERE deleted_at IS NULL，无此限制。为减少旧库用户受此影响的概率，
        # 这里只确保新库/部分索引存在，不尝试 drop 隐式索引。
        #
        # 注意：不要 DROP 任何 sqlite_autoindex_*（PRIMARY KEY 的隐式索引，
        # 报 "index associated with UNIQUE or PRIMARY KEY constraint cannot be
        # dropped"）。自命名 UNIQUE 索引（若有）也不主动 drop——重建表才是
        # 干净方案，但风险高，留待 compile engine（L3）阶段处理。
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_documents_active_source"
            " ON documents(collection, source) WHERE deleted_at IS NULL"
        )

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
                    "SELECT 1 FROM collections WHERE name = ? LIMIT 1",
                    (name,),
                ).fetchone()
                if existing is not None:
                    return
                now = _now_iso()
                self._conn.execute(
                    "INSERT INTO collections(name, created_at) VALUES (?, ?)",
                    (name, now),
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
                    "INSERT OR IGNORE INTO collections(name, created_at) VALUES (?, ?)",
                    (doc.collection, doc.created_at),
                )
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

                # 1. 删除同 (collection, source) 的旧**活跃**文档及分块/向量/FTS。
                # 软删除文档（deleted_at 非空）不动——保留 trash 审计链，避免
                # 重新摄入同源文件时静默破坏 30 天后悔期。如果同源文件既被软删
                # 又被重新摄入，恢复后会出现元数据 vs. 实际 chunks 不一致，用户
                # 在 restore 提示里需明确看到「需重新摄入完成迁移」。
                old_ids = [
                    r[0] for r in conn.execute(
                        "SELECT id FROM documents"
                        " WHERE source = ? AND collection = ?"
                        " AND deleted_at IS NULL",
                        (doc.source, doc.collection),
                    ).fetchall()
                ]
                for old_id in old_ids:
                    self._delete_document_chunks_in_txn(conn, old_id)
                    conn.execute("DELETE FROM documents WHERE id = ?", (old_id,))

                # 2. 登记集合并写新文档记录
                conn.execute(
                    "INSERT OR IGNORE INTO collections(name, created_at) VALUES (?, ?)",
                    (doc.collection, doc.created_at),
                )
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
        """在已开启的事务内插入分块（调用方负责锁 / BEGIN / COMMIT / ROLLBACK）。

        两段式批量写：先逐行写 vec_chunks 拿 rowid（vec0 虚拟表的 rowid
        由 C 层分配，保持与历史一致的分配方式），随后把 chunks_meta /
        bm25_index / sparse_terms 合并为 executemany 批量写。jieba 分词、
        sparse 词项、extra JSON 等纯 Python 开销在收集期完成，长文档
        （数千分块）的 SQL 往返从每 chunk 3-4 次降为 3 次。
        """
        excluded_keys = {
            "type", "page", "sheet", "slide", "heading",
            "language", "level", "chunk_index",
        }
        meta_rows: list[tuple] = []
        fts_rows: list[tuple] = []
        sparse_rows: list[tuple] = []
        vec_ids: list[int] = []
        inserted = 0

        # --- 第一段：向量表逐行插入，收集分配到的 chunk id ---
        for chunk, emb in zip(chunks, embeddings, strict=False):
            # 序列化向量为 bytes（vec0 接受 BLOB）
            emb_bytes = self._to_bytes(emb)
            cur = conn.execute(
                "INSERT INTO vec_chunks(embedding) VALUES (?)",
                (emb_bytes,),
            )
            vec_id = cur.lastrowid
            if vec_id is None:
                raise StoreError("无法获取 vec_chunks.id")
            vec_ids.append(vec_id)

            meta = chunk.metadata
            extra = {k: v for k, v in meta.items() if k not in excluded_keys}
            meta_rows.append((
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
            ))
            if self._fts_available:
                # jieba 分词在收集期完成，写库走 executemany
                fts_rows.append((
                    segment(chunk.content, self.bm25_jieba_enabled),
                    collection,
                    vec_id,
                ))
            if self.sparse_retrieval_enabled:
                sparse_rows.extend(
                    (t, vec_id, w, collection)
                    for t, w in sparse_token_weights(
                        chunk.content, self.bm25_jieba_enabled
                    )
                )
            inserted += 1

        # --- 第二段：普通表批量写 ---
        if meta_rows:
            conn.executemany(
                """
                INSERT INTO chunks_meta
                    (id, document_id, content, tokens, chunk_index,
                     collection, source, format, doc_type, page,
                     sheet, slide, heading, language, extra)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                meta_rows,
            )
        if fts_rows:
            conn.executemany(
                "INSERT INTO bm25_index(content, collection, chunk_id) VALUES (?, ?, ?)",
                fts_rows,
            )
        if sparse_rows:
            # 首删后插保持幂等（批量合并为一次，替代逐 chunk 删除）
            self._delete_sparse_in_txn(conn, vec_ids)
            conn.executemany(
                "INSERT INTO sparse_terms(term, chunk_id, weight, collection)"
                " VALUES (?, ?, ?, ?)",
                sparse_rows,
            )

        # 更新文档 chunk_count（绝对值写入，不累加）
        conn.execute(
            "UPDATE documents SET chunk_count = ?, updated_at = ? WHERE id = ?",
            (inserted, _now_iso(), document_id),
        )
        return inserted

    def _delete_graph_chunk_links_in_txn(
        self, conn: sqlite3.Connection, chunk_ids: Sequence[int]
    ) -> None:
        """防御式清理知识图谱的分块关联（chunk_entities）悬空行。

        与 GraphStore 共用同一 DB 文件但 schema 独立，因此先探测表是否存在
        （从未跑过 extract 的新库没有这些表），存在才删，避免依赖耦合。
        这同时解决两个问题：
        - 软删后 chunk_entities 悬空行永不回收（存储缓慢增长）；
        - 软删 + 恢复后重新 extract 会重复建图（chunk_entities 清了就不重复）。
        """
        if not chunk_ids:
            return
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='chunk_entities'"
        ).fetchone()
        if row is None:
            return
        placeholders = ",".join("?" * len(chunk_ids))
        conn.execute(
            f"DELETE FROM chunk_entities WHERE chunk_id IN ({placeholders})",
            list(chunk_ids),
        )

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
            self._delete_sparse_in_txn(conn, chunk_ids)
            self._delete_graph_chunk_links_in_txn(conn, chunk_ids)
            conn.execute(
                f"DELETE FROM chunks_meta WHERE id IN ({placeholders})",
                chunk_ids,
            )
        return chunk_ids

    # --- 删除（软删除：保留 documents 行 30 天，chunks/向量物理删） ---
    @_retry_on_locked
    def delete_document(self, document_id: str) -> int:
        """软删除文档：写 trash 表 + documents.deleted_at 标记 + 物理删 chunks/向量。

        与旧版差异：
        - 不再 DELETE FROM documents（行保留以支持 restore_document 恢复元数据）
        - chunks_meta / vec_chunks / bm25_index / sparse_terms 仍物理删，检索不可见
        - 30 天内可调用 restore_document(id) 把 deleted_at 置 NULL 恢复元数据；
          恢复后需要重新索引才能被检索（向量/FTS 已物理删）
        - 空集合清理逻辑禁用：软删除时 documents 行仍在，无法用 COUNT=0 判定空集合

        Returns:
            删除的 chunk 数；-1 表示文档不存在（哨兵值，调用方据此返回 404）；
            -2 表示文档已处于软删除状态（idempotent：重复调用不抛错）。
        """
        with self._lock:
            self._require_open()
            conn = self._conn
            try:
                # 先查文档是否存在（避免误删孤儿 chunk）
                doc_row = conn.execute(
                    "SELECT * FROM documents WHERE id = ?", (document_id,)
                ).fetchone()
                if doc_row is None:
                    return -1

                # 解析行：与 list_documents / upsert_document 的列顺序对齐
                # (id, source, collection, format, file_hash, size_bytes, page_count,
                #  chunk_count, created_at, updated_at, title, tags, summary, enriched_at,
                #  deleted_at)
                if doc_row[14]:  # deleted_at 非空 = 已软删
                    return -2

                conn.execute("BEGIN")
                # 取该文档所有 chunk_id
                rows = conn.execute(
                    "SELECT id FROM chunks_meta WHERE document_id = ?",
                    (document_id,),
                ).fetchall()
                chunk_ids = [r[0] for r in rows]

                # 物理删向量 / FTS / 稀疏 / meta（与硬删除同逻辑）
                if chunk_ids:
                    placeholders = ",".join("?" * len(chunk_ids))
                    conn.execute(
                        f"DELETE FROM vec_chunks WHERE id IN ({placeholders})",
                        chunk_ids,
                    )
                    if self._fts_available:
                        conn.execute(
                            f"DELETE FROM bm25_index WHERE CAST(chunk_id AS INTEGER) IN ({placeholders})",
                            chunk_ids,
                        )
                    self._delete_sparse_in_txn(conn, chunk_ids)
                    self._delete_graph_chunk_links_in_txn(conn, chunk_ids)
                    conn.execute(
                        f"DELETE FROM chunks_meta WHERE id IN ({placeholders})",
                        chunk_ids,
                    )

                # 写 trash 审计行（snapshot_json 存整行便于人工核验/恢复元数据）
                now = _now_iso()
                snapshot = {
                    "id": doc_row[0],
                    "source": doc_row[1],
                    "collection": doc_row[2],
                    "format": doc_row[3],
                    "file_hash": doc_row[4],
                    "size_bytes": doc_row[5],
                    "page_count": doc_row[6],
                    "chunk_count": doc_row[7],
                    "created_at": doc_row[8],
                    "updated_at": doc_row[9],
                    "title": doc_row[10],
                    "tags": doc_row[11],
                    "summary": doc_row[12],
                    "enriched_at": doc_row[13],
                }
                conn.execute(
                    "INSERT OR REPLACE INTO trash"
                    "(document_id, source, collection, deleted_at, snapshot_json, purged_at)"
                    " VALUES (?, ?, ?, ?, ?, NULL)",
                    (doc_row[0], doc_row[1], doc_row[2], now, json.dumps(snapshot, ensure_ascii=False)),
                )

                # 软删除 documents 行（保留行 + deleted_at 标记）
                conn.execute(
                    "UPDATE documents SET deleted_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, document_id),
                )
                # 不再清空集合：documents 行还在，COUNT(*) > 0 保持原状。
                # 用户恢复后集合自然在；GC 物理删 trash + documents 时再联动清空集合。

                conn.execute("COMMIT")
                return len(chunk_ids)
            except Exception as e:  # noqa: BLE001
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK")
                raise StoreError(f"删除文档失败: {e}") from e

    @_retry_on_locked
    def restore_document(self, document_id: str) -> bool:
        """恢复软删除的文档（仅元数据层面：deleted_at 置 NULL）。

        注意：chunks_meta / vec_chunks / bm25_index / sparse_terms 在软删除时已
        物理删除，本接口**不重建**。恢复后文档出现在 list_documents 中，但
        不会被检索命中，直到用户重新摄入（ingest 同源文件，file_hash 去重会
        自动跳过）后调用 reindex 才能恢复全文检索。

        **chunk_count 同步清零**——软删前 chunk_count=N 但实际 chunks_meta 为空，
        保留 N 会让 list_docs 报告"42 分块"而实际 0 命中，元数据与现实不一致
        会让 quality_check（只看 ==0 告警）漏报。

        Returns:
            True = 恢复成功；False = 文档不存在、未处于软删除状态、或
            恢复会与活跃文档冲突（同 source 已被重新摄入的活跃文档占用）。
        """
        with self._lock:
            self._require_open()
            conn = self._conn
            try:
                now = _now_iso()
                # 仅当 deleted_at 非空时才置 NULL；文档不存在返回 False。
                # chunk_count 同步置 0 避免元数据错乱。
                cur = conn.execute(
                    "UPDATE documents SET deleted_at = NULL, updated_at = ?,"
                    " chunk_count = 0"
                    " WHERE id = ? AND deleted_at IS NOT NULL",
                    (now, document_id),
                )
                if cur.rowcount == 0:
                    return False
                # trash 行不删：保留作为审计；purge_trash 时再清
                return True
            except sqlite3.IntegrityError:
                # UNIQUE 冲突：软删后同 source 被重新摄入的活跃文档占用了。
                # 恢复会让两个同 source 文档并存（部分索引 WHERE deleted_at IS NULL
                # 阻止），语义上返回"无法恢复"而非抛 500。
                return False
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"恢复文档失败: {e}") from e

    @_retry_on_locked
    def list_trash(self, limit: int = 100) -> list[dict[str, Any]]:
        """列出回收站中的软删除文档（按 deleted_at 倒序）。"""
        with self._lock:
            self._require_open()
            try:
                rows = self._conn.execute(
                    "SELECT document_id, source, collection, deleted_at, purged_at"
                    " FROM trash ORDER BY deleted_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [
                    {
                        "document_id": r[0],
                        "source": r[1],
                        "collection": r[2],
                        "deleted_at": r[3],
                        "purged_at": r[4],
                    }
                    for r in rows
                ]
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"列出回收站失败: {e}") from e

    @_retry_on_locked
    def purge_trash(self, older_than_days: int = 30) -> int:
        """物理清空超过 N 天的 trash 行 + 联动 documents 行 + 联动空集合清理。

        与 list_documents 不同：purge_trash 删的是已软删除的 documents 行
        （deleted_at 非空），不是活跃文档。

        Returns:
            物理删除的 trash 行数。
        """
        with self._lock:
            self._require_open()
            conn = self._conn
            try:
                cutoff = _now_iso_offset(days=-older_than_days)
                # 找出待清的 document_id 与所在集合
                rows = conn.execute(
                    "SELECT document_id, collection FROM trash"
                    " WHERE purged_at IS NULL AND deleted_at < ?",
                    (cutoff,),
                ).fetchall()
                if not rows:
                    return 0
                doc_ids = [r[0] for r in rows]
                affected_collections = {r[1] for r in rows if r[1]}

                conn.execute("BEGIN")
                now = _now_iso()
                placeholders = ",".join("?" * len(doc_ids))
                # 物理删 documents 行（连带 trash 行一并清）
                conn.execute(
                    f"DELETE FROM documents WHERE id IN ({placeholders})"
                    " AND deleted_at IS NOT NULL",
                    doc_ids,
                )
                conn.execute(
                    f"DELETE FROM trash WHERE document_id IN ({placeholders})",
                    doc_ids,
                )
                # 联动清空无活跃文档的集合（documents.deleted_at IS NULL = 活跃）
                for col in affected_collections:
                    remaining = conn.execute(
                        "SELECT COUNT(*) FROM documents"
                        " WHERE collection = ? AND deleted_at IS NULL",
                        (col,),
                    ).fetchone()
                    if remaining and remaining[0] == 0:
                        conn.execute(
                            "DELETE FROM collections WHERE name = ?",
                            (col,),
                        )
                conn.execute("COMMIT")
                # 标记成功条数（不记录 purged_at：物理删后行已不存在）
                _ = now
                return len(doc_ids)
            except Exception as e:  # noqa: BLE001
                with contextlib.suppress(Exception):
                    conn.execute("ROLLBACK")
                raise StoreError(f"清空回收站失败: {e}") from e

    @_retry_on_locked
    def record_curate_run(
        self,
        started_at: str,
        finished_at: str,
        dry_run: bool,
        collection: str | None,
        actions: list[str],
        changed_doc_ids: list[str],
        skipped_count: int,
        error_count: int,
        elapsed_ms: int,
        note: str | None = None,
    ) -> int:
        """记录一次 curate 运行的留痕（让 L2 自动化「可观测」）。"""
        with self._lock:
            self._require_open()
            try:
                cur = self._conn.execute(
                    "INSERT INTO curate_runs"
                    "(started_at, finished_at, dry_run, collection, actions_json,"
                    " changed_doc_ids, skipped_count, error_count, elapsed_ms, note)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        started_at,
                        finished_at,
                        1 if dry_run else 0,
                        collection,
                        json.dumps(actions, ensure_ascii=False),
                        json.dumps(changed_doc_ids, ensure_ascii=False),
                        skipped_count,
                        error_count,
                        elapsed_ms,
                        note,
                    ),
                )
                return int(cur.lastrowid or 0)
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"记录 curate 运行失败: {e}") from e

    @_retry_on_locked
    def list_curate_runs(self, days: int = 7, limit: int = 50) -> list[dict[str, Any]]:
        """列出近 N 天的 curate 运行记录（按 started_at 倒序）。"""
        with self._lock:
            self._require_open()
            try:
                cutoff = _now_iso_offset(days=-days)
                rows = self._conn.execute(
                    "SELECT id, started_at, finished_at, dry_run, collection,"
                    " actions_json, changed_doc_ids, skipped_count, error_count,"
                    " elapsed_ms, note FROM curate_runs"
                    " WHERE started_at >= ? ORDER BY started_at DESC LIMIT ?",
                    (cutoff, limit),
                ).fetchall()
                return [
                    {
                        "id": r[0],
                        "started_at": r[1],
                        "finished_at": r[2],
                        "dry_run": bool(r[3]),
                        "collection": r[4],
                        "actions": json.loads(r[5]) if r[5] else [],
                        "changed_doc_ids": json.loads(r[6]) if r[6] else [],
                        "skipped_count": r[7],
                        "error_count": r[8],
                        "elapsed_ms": r[9],
                        "note": r[10],
                    }
                    for r in rows
                ]
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"列出 curate 运行失败: {e}") from e

    def delete_by_source(self, source: str, collection: str = "default") -> int:
        """按 source 精确匹配删除（用于 `doc2mind remove <path>` 全路径场景）。

        `documents.source` 存的是 loader 解析后的绝对路径，所以 caller 必须传
        完整绝对路径而非 basename。**普通用户按文件名删除请用
        delete_by_source_basename**，否则会找不到记录。

        Returns:
            删除的文档数（0 或 1）
        """
        with self._lock:
            self._require_open()
            try:
                rows = self._conn.execute(
                    "SELECT id FROM documents"
                    " WHERE source = ? AND collection = ?"
                    " AND deleted_at IS NULL",
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
                raise StoreError(f"按 source 精确删除失败: {e}") from e

    def delete_by_source_basename(self, basename: str, collection: str = "default") -> int:
        """按文件名（basename）模糊匹配删除（`doc2mind remove a.md` 这类场景）。

        `documents.source` 是绝对路径，所以用 basename 配合 `LIKE '%/<basename>'`
        后缀匹配（POSIX 与 Windows 都用 `/` 作路径分隔符——`loader.base.make_source`
        内部统一替换为 `/`）。仅匹配**活跃**文档；软删文档保留 trash 审计。

        Returns:
            删除的文档数（0、1 或多个同名文件）
        """
        with self._lock:
            self._require_open()
            try:
                # 转义 LIKE 通配符（用户输入的 % / _ 不应被当通配符）
                escaped = basename.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                rows = self._conn.execute(
                    "SELECT id FROM documents"
                    " WHERE source LIKE ? ESCAPE '\\'"
                    " AND collection = ?"
                    " AND deleted_at IS NULL",
                    (f"%/{escaped}", collection),
                ).fetchall()
                if not rows:
                    return 0
                # 多份同名文件全部软删
                count = 0
                for (doc_id,) in rows:
                    if self.delete_document(doc_id) >= 0:
                        count += 1
                return count
            except StoreError:
                raise
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"按文件名删除失败: {e}") from e

    def find_soft_deleted_document_id(
        self, target: str, collection: str = "default"
    ) -> str | None:
        """在软删除文档中定位 document_id（供 restore_doc 按文件名恢复）。

        匹配策略（与 delete_by_source_basename 保持一致）：
        1. target 是 26 位 ULID → 直接按 id 查（须处于软删除态）；
        2. target 含路径分隔符 → 先按完整 source 精确匹配；
        3. 兜底按 basename `LIKE '%/<name>'` 后缀匹配（documents.source 是绝对路径）。

        走 self._lock（RLock），供 MCP 工具调用，避免裸访问 store._conn
        绕过锁（同进程共库时是并发读写竞态）。

        Returns:
            软删除文档的 id；未找到返回 None。
        """
        with self._lock:
            self._require_open()
            try:
                # 1. ULID 直查
                if _looks_like_ulid(target):
                    row = self._conn.execute(
                        "SELECT id FROM documents"
                        " WHERE id = ? AND deleted_at IS NOT NULL",
                        (target,),
                    ).fetchone()
                    return row[0] if row else None

                # 2. 完整 source 精确匹配（target 是绝对路径）
                if "/" in target or "\\" in target:
                    row = self._conn.execute(
                        "SELECT id FROM documents"
                        " WHERE source = ? AND collection = ?"
                        " AND deleted_at IS NOT NULL LIMIT 1",
                        (target, collection),
                    ).fetchone()
                    if row:
                        return row[0]

                # 3. basename 后缀匹配
                basename = target.replace("\\", "/").rsplit("/", 1)[-1]
                escaped = basename.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                row = self._conn.execute(
                    "SELECT id FROM documents"
                    " WHERE source LIKE ? ESCAPE '\\'"
                    " AND collection = ?"
                    " AND deleted_at IS NOT NULL LIMIT 1",
                    (f"%/{escaped}", collection),
                ).fetchone()
                return row[0] if row else None
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"定位软删除文档失败: {e}") from e

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
                    # 稀疏向量倒排项同步集合名（D2）
                    if self.sparse_retrieval_enabled:
                        placeholders = ",".join("?" * len(chunk_ids))
                        conn.execute(
                            f"UPDATE sparse_terms SET collection = ? "
                            f"WHERE chunk_id IN ({placeholders})",
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
                    (self._to_bytes(query_vec), fetch_n),
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

        短词（<3 字符，如中文 2 字词"气缸"、英文缩写"IP"）trigram 分词器
        无法索引/匹配，用 `content LIKE '%词%'` 兜底召回，避免 BM25 静默
        失效（混合检索退化为纯向量检索）。

        注意：FTS5 用独立 `_fts_conn` 跑查询，避开 vec0 扩展与 trigram
        tokenizer 在同 connection 上评估中文 bm25() 时的
        IntegrityError: datatype mismatch；LIKE 兜底走常规 `_conn`。
        """
        if not self._fts_available:
            return []
        # 分词模式对查询做统一预处理：jieba → 空格拼接；否则原样空格切分。
        normalized_query = segment_query(query, self.bm25_jieba_enabled)
        tokens = [t.strip() for t in normalized_query.split() if t.strip()]
        if not tokens:
            return []
        with self._lock:
            self._require_open()
            if self._fts_conn is None:
                return []
            try:
                cols = _normalize_collections(collection)
                col_filter = ""
                params: list[Any] = []
                if cols:
                    placeholders = ",".join("?" for _ in sorted(cols))
                    col_filter = f"AND collection IN ({placeholders})"
                    params.extend(sorted(cols))

                merged: dict[int, float] = {}

                if self.bm25_jieba_enabled:
                    # unicode61（jieba 分词）：全部 token 走 FTS5，含 2 字中文词，
                    # 无需 LIKE 兜底。
                    match_expr = _build_fts5_match_unicode(tokens)
                    if match_expr:
                        # LIMIT 用字符串拼接（int 已强类型校验）；FTS5 的 bm25()
                        # 返回负分且越小越相关，取反为正分后"越大越相关"。
                        cur = self._fts_conn.execute(
                            f"""
                            SELECT chunk_id, -bm25(bm25_index) AS score
                            FROM bm25_index
                            WHERE bm25_index MATCH ? {col_filter}
                            ORDER BY score DESC
                            LIMIT {int(top_k * 4)}
                            """,
                            [match_expr, *params],
                        )
                        for cid, score in cur.fetchall():
                            merged[int(str(cid))] = float(score)
                else:
                    # trigram 模式：只能匹配 ≥3 字符连续子串；短词拆出去走 LIKE
                    fts_tokens = [t for t in tokens if len(t) >= 3]
                    short_tokens = [t for t in tokens if len(t) < 3][:_MAX_SHORT_TOKENS]
                    # 1) FTS5：≥3 chars 的 token（trigram 可子串匹配）
                    if fts_tokens:
                        match_expr = _build_fts5_match(" ".join(fts_tokens))
                        if match_expr:
                            cur = self._fts_conn.execute(
                                f"""
                                SELECT chunk_id, -bm25(bm25_index) AS score
                                FROM bm25_index
                                WHERE bm25_index MATCH ? {col_filter}
                                ORDER BY score DESC
                                LIMIT {int(top_k * 4)}
                                """,
                                [match_expr, *params],
                            )
                            for cid, score in cur.fetchall():
                                # chunk_id 在 FTS5 UNINDEXED 列中存为 TEXT，需 cast
                                merged[int(str(cid))] = float(score)

                    # 2) LIKE 兜底：<3 chars 短词（trigram 无法命中）
                    if short_tokens:
                        logger.debug(
                            "BM25 短词路径: tokenizer=trigram short_tokens=%s "
                            "(FTS 无法匹配，走 LIKE 子串)",
                            short_tokens[:8],
                        )
                    for tok in short_tokens:
                        cur = self._conn.execute(
                            f"""
                            SELECT id FROM chunks_meta
                            WHERE content LIKE ? {col_filter}
                            LIMIT {int(top_k * 4)}
                            """,
                            [f"%{tok}%", *params],
                        )
                        for (cid,) in cur.fetchall():
                            merged[cid] = merged.get(cid, 0.0) + _SHORT_TOKEN_SCORE

                ranked = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)
                return ranked[:top_k]
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"BM25 检索失败: {e}") from e

    def sparse_search(
        self,
        query: str,
        top_k: int = 10,
        collection: str | Sequence[str] | None = None,
    ) -> list[tuple[int, float]]:
        """稀疏向量检索（D2），返回 [(chunk_id, cosine_score), ...]。

        第三条召回路：文本 → jieba + log-TF/L2 归一化稀疏向量，经倒排索引求
        「查询·文档」内积 = 稀疏向量余弦 ∈ [0,1]（与忠实余弦同量纲）。是与 FTS5
        BM25（IDF 排名）独立的词法稀疏路；换真实稀疏嵌入器（如 BGE-M3 sparse）
        时仅需替换 `sparse_token_weights` 喂入的查询/文档表示。

        collection 支持单集合名或集合名列表（多选知识库）。
        """
        if not self.sparse_retrieval_enabled:
            return []
        q_weights = sparse_token_weights(query, self.bm25_jieba_enabled)
        if not q_weights:
            return []
        cols = _normalize_collections(collection)
        with self._lock:
            self._require_open()
            conn = self._conn
            try:
                col_sql = ""
                params: list[Any] = []
                if cols:
                    placeholders = ",".join("?" for _ in sorted(cols))
                    col_sql = f" AND collection IN ({placeholders})"
                    params = list(sorted(cols))
                scores: dict[int, float] = {}
                for term, qw in q_weights:
                    cur = conn.execute(
                        f"SELECT chunk_id, weight FROM sparse_terms"
                        f" WHERE term = ?{col_sql}",
                        [term, *params],
                    )
                    for cid, w in cur.fetchall():
                        scores[int(cid)] = scores.get(int(cid), 0.0) + qw * w
                ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
                return ranked[:top_k]
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"稀疏向量检索失败: {e}") from e

    @property
    def sparse_available(self) -> bool:
        """稀疏向量召回路（D2）是否启用。"""
        return self.sparse_retrieval_enabled

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

    def get_neighbor_chunks(
        self, chunk_id: int, window: int = 1
    ) -> list[StoredChunk]:
        """按命中 chunk 取出同源相邻分块（父子/邻块上下文）。

        规则：定位命中 chunk 的 (source, chunk_index)，取同 source 且
        chunk_index 落在 [idx-window, idx+window] 除命中自身外的分块，
        按 chunk_index 升序返回。

        Args:
            chunk_id: 命中分块 ID
            window: 前后各取几块，>=0；0 原样返回空（不做合并）。

        Returns:
            邻块 StoredChunk 列表（升序，不含命中自身）。
        """
        window = max(0, int(window))
        if window == 0:
            return []
        with self._lock:
            self._require_open()
            try:
                anchor = self._conn.execute(
                    "SELECT source, chunk_index FROM chunks_meta WHERE id = ?",
                    (chunk_id,),
                ).fetchone()
                if anchor is None:
                    return []
                source, idx = anchor
                lo, hi = idx - window, idx + window + 1  # 半开区间 → 含 idx+window
                cur = self._conn.execute(
                    """
                    SELECT cm.id, cm.content, cm.source, cm.format, cm.doc_type, cm.page,
                           cm.heading, d.file_hash, cm.collection, d.created_at,
                           cm.tokens, cm.chunk_index, cm.extra, cm.sheet, cm.slide, cm.language
                    FROM chunks_meta cm
                    LEFT JOIN documents d ON d.id = cm.document_id
                    WHERE cm.source = ? AND cm.chunk_index >= ? AND cm.chunk_index < ?
                    ORDER BY cm.chunk_index ASC
                    """,
                    (source, lo, hi),
                )
                return [
                    StoredChunk(
                        id=r[0], content=r[1], source=r[2], format=r[3],
                        doc_type=r[4], page=r[5], heading=r[6],
                        file_hash=r[7] or "", collection=r[8], created_at=r[9],
                        tokens=r[10], chunk_index=r[11],
                        extra_metadata=json.loads(r[12]) if r[12] else {},
                    )
                    for r in cur.fetchall()
                    if r[0] != chunk_id
                ]
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"获取邻块上下文失败: {e}") from e

    def get_heading_siblings(
        self, chunk_id: int, limit: int = 8
    ) -> list[StoredChunk]:
        """按命中 chunk 取同 (source, heading) 的兄弟分块（小到大 / 章节上下文）。

        命中小块时返回同标题下其余块，把「父级章节」文本并入上下文；
        无 heading 的块回退为空（调用方可再走邻块）。
        """
        limit = max(0, int(limit))
        if limit == 0:
            return []
        with self._lock:
            self._require_open()
            try:
                anchor = self._conn.execute(
                    "SELECT source, heading, chunk_index FROM chunks_meta WHERE id = ?",
                    (chunk_id,),
                ).fetchone()
                if anchor is None or not anchor[1]:
                    return []
                source, heading, idx = anchor
                cur = self._conn.execute(
                    """
                    SELECT cm.id, cm.content, cm.source, cm.format, cm.doc_type, cm.page,
                           cm.heading, d.file_hash, cm.collection, d.created_at,
                           cm.tokens, cm.chunk_index, cm.extra, cm.sheet, cm.slide, cm.language
                    FROM chunks_meta cm
                    LEFT JOIN documents d ON d.id = cm.document_id
                    WHERE cm.source = ? AND cm.heading = ? AND cm.id != ?
                    ORDER BY ABS(cm.chunk_index - ?) ASC, cm.chunk_index ASC
                    LIMIT ?
                    """,
                    (source, heading, chunk_id, idx, limit),
                )
                return [
                    StoredChunk(
                        id=r[0], content=r[1], source=r[2], format=r[3],
                        doc_type=r[4], page=r[5], heading=r[6],
                        file_hash=r[7] or "", collection=r[8], created_at=r[9],
                        tokens=r[10], chunk_index=r[11],
                        extra_metadata=json.loads(r[12]) if r[12] else {},
                    )
                    for r in cur.fetchall()
                ]
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"获取同标题上下文失败: {e}") from e

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

    def list_chunk_contexts(
        self, collection: str | None = None
    ) -> list[tuple[int, str, str | None]]:
        """按集合列出 (chunk_id, content, doc_summary)，上下文检索重新嵌入用。

        与 `list_chunk_contents` 等价，但额外 JOIN 出每个 chunk 所属文档的
        AI 摘要（`documents.summary`，enrich 生成）。供 `contextual_retrieval`
        在嵌入前拼接 `[文档摘要]...` 前缀用；无摘要时返回 None。
        """
        with self._lock:
            self._require_open()
            try:
                sql = (
                    "SELECT cm.id, cm.content, d.summary FROM chunks_meta cm "
                    "LEFT JOIN documents d ON d.id = cm.document_id"
                )
                params: list[Any] = []
                if collection:
                    sql += " WHERE cm.collection = ?"
                    params.append(collection)
                rows = self._conn.execute(sql, params).fetchall()
                return [(int(r[0]), str(r[1]), r[2]) for r in rows]
            except Exception as e:  # noqa: BLE001
                raise StoreError(f"列出分块上下文失败: {e}") from e

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
                    emb_bytes = self._to_bytes(emb)
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
                if self.vector_quantize == "int8" and self._int8_supported:
                    conn.execute(_VEC_SQL_TEMPLATE_INT8.format(dim=int(new_dim)))
                    self._vec_storage_type = "INT8"
                else:
                    conn.execute(_VEC_SQL_TEMPLATE.format(dim=int(new_dim)))
                    self._vec_storage_type = "FLOAT"
                inserted = 0
                for cid, emb in chunk_id_emb_pairs:
                    emb_bytes = self._to_bytes(emb)
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
                # 兼容极旧数据库：即使迁移尚未清理占位行，也不把它暴露为真实文档。
                # 同时排除软删除的文档（deleted_at 非空 → 不算活跃文档）。
                conds: list[str] = [
                    "source != '__collection_placeholder__'",
                    "deleted_at IS NULL",
                ]
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
                # 兼容极旧数据库：占位行不计入真实文档总数。
                # 软删除文档（deleted_at 非空）也不计入。
                conds: list[str] = [
                    "source != '__collection_placeholder__'",
                    "deleted_at IS NULL",
                ]
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
                    FROM documents WHERE id = ? AND deleted_at IS NULL
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

        软删除文档（deleted_at 非空）不算"已存在"——增量摄入需要重新创建
        文档，让 chunks_meta/vec_chunks 重新被填充。
        """
        with self._lock:
            if self._conn is None:
                return None
            try:
                row = self._conn.execute(
                    "SELECT id FROM documents"
                    " WHERE file_hash = ? AND collection = ?"
                    " AND deleted_at IS NULL",
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
                    " WHERE source != '__collection_placeholder__'"
                    " AND deleted_at IS NULL"
                ).fetchone()[0]
                chunk_total = self._conn.execute(
                    "SELECT COUNT(*) FROM chunks_meta"
                ).fetchone()[0]
                # 各集合 (doc_count, chunk_count, size_bytes)，包含空集合。
                # 软删除文档（deleted_at 非空）不计入 doc_count。
                rows = self._conn.execute(
                    """
                    SELECT c.name,
                           COUNT(DISTINCT d.id),
                           COUNT(ch.id),
                           COALESCE(SUM(d.size_bytes), 0)
                    FROM collections c
                    LEFT JOIN documents d ON d.collection = c.name
                        AND d.source != '__collection_placeholder__'
                        AND d.deleted_at IS NULL
                    LEFT JOIN chunks_meta ch ON ch.document_id = d.id
                    GROUP BY c.name
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


def _vector_to_bytes_int8(vec) -> bytes:
    """把向量量化为 int8 并序列化为 vec0 INT8 列接受的 BLOB。

    输入 float 向量先做 [-1,1] 钳制后缩放到 int8 满量程（round(x*127)），
    近零值量化为 0，保号向下映射。仅当 `open()` 探针确认本机 sqlite-vec
    支持 INT8 列时才会被使用。
    """
    import numpy as np  # 局部导入，减少冷启动

    arr = np.clip(np.asarray(vec, dtype=np.float32), -1.0, 1.0)
    q = np.rint(arr * 127.0).astype(np.int8)
    return q.tobytes()


def _probe_vec_int8_supported(conn: sqlite3.Connection, dim: int) -> bool:
    """探测当前 sqlite-vec 扩展能否写入/检索 INT8 向量列。

    实测：sqlite-vec 0.1.9 的 PyPI wheel 把 INT8 列插入的 blob 一律按
    float32 解释（无类型头），抛 `expected int8, but float32 vector was
    provided`，即 int8 量化存储在该构建不可用。本函数用一个临时表
    往返一次来判定，结果按进程缓存。
    """
    global _INT8_SUPPORTED
    if _INT8_SUPPORTED is not None:
        return _INT8_SUPPORTED
    _INT8_SUPPORTED = False  # 默认视为不支持，避免探测异常时误开
    try:
        conn.execute(f"CREATE VIRTUAL TABLE _quant_probe USING vec0(embedding INT8[{dim}])")
        conn.execute(
            "INSERT INTO _quant_probe(embedding) VALUES (?)",
            (_vector_to_bytes_int8([1.0, -1.0][:dim] + [0.0] * max(0, dim - 2)),),
        )
        conn.execute("DROP TABLE IF EXISTS _quant_probe")
        _INT8_SUPPORTED = True
    except Exception:  # noqa: BLE001 —— 不支持时回退 float32，不阻断建库
        conn.execute("DROP TABLE IF EXISTS _quant_probe")
        _INT8_SUPPORTED = False
    return _INT8_SUPPORTED


def _now_iso() -> str:
    """当前 ISO8601 时间（带本地时区）。"""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _now_iso_offset(*, days: int = 0) -> str:
    """相对当前时间的 ISO8601（days 负数 = 过去），用于 cutoff 边界计算。"""
    from datetime import datetime, timedelta, timezone

    return (datetime.now(timezone.utc).astimezone() + timedelta(days=days)).isoformat(
        timespec="seconds"
    )


def _new_id() -> str:
    """生成文档/占位记录主键（与 HTTP 层一致：uuid4 hex）。"""
    return uuid.uuid4().hex


def _looks_like_ulid(s: str) -> bool:
    """ULID 是 26 字符的 Crockford Base32 字符串。简化判断：长度 26 且仅含字母数字。"""
    return len(s) == 26 and s.isalnum()
