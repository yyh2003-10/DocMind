"""对话会话持久化 — SQLite 表 chat_sessions / chat_messages。

与向量库（sqlite_vec.VectorStore）共用同一个 DB 文件（`settings.db_path`），
但独立模块、独立连接：会话读写是低频小事务，不依赖 sqlite-vec 扩展，
按操作开关连接，避免与 vec_chunks 表的写互斥逻辑耦合。

设计要点：
- 表结构简单（会话 + 消息两级，级联删除），`CREATE TABLE IF NOT EXISTS` 幂等建表；
- `append_message` 首条用户消息自动生成会话标题（问题前 50 字）；
- rag.py 的会话历史（LLM 上下文）与 WPF 会话列表（UI 回看）共用本存储，
  重启后会话上下文可恢复。
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

# 会话标题最大长度（首条用户问题截断）
_TITLE_MAX = 50


def _now_iso() -> str:
    # 微秒精度：同秒内的多次更新也要能按时间排序（会话列表按 updated_at 倒序）
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="microseconds")


@dataclass(frozen=True)
class ChatSessionSummary:
    """会话列表项。"""

    chat_id: str
    title: str
    message_count: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ChatMessageRow:
    """会话内单条消息。"""

    role: str
    content: str
    created_at: str
    sources_json: str | None = None


class ChatStoreError(Exception):
    """会话存储异常。"""


class ChatStore:
    """对话会话存储（线程安全的按操作连接）。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        # 建表互斥：并发首写（多请求同时持久化会话）需幂等
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10.0)
        conn.execute("PRAGMA foreign_keys = ON")
        # 与 sqlite_vec / graph_store 保持一致：WAL 提升并发读吞吐，
        # 显式 busy_timeout 避免并发写会话时瞬间抛 SQLITE_BUSY。
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        """按操作开关连接：内层 with 管事务（成功提交/异常回滚），finally 关连接。"""
        conn = self._connect()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS chat_sessions (
                    id         TEXT PRIMARY KEY,
                    title      TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id      TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
                    role         TEXT NOT NULL,
                    content      TEXT NOT NULL,
                    created_at   TEXT NOT NULL,
                    sources_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_chat_messages_chat_id
                    ON chat_messages(chat_id, id);
                """
            )
            # 兼容旧版本数据库：如果 chat_messages 没有 sources_json 列则自动添加
            try:
                cols = [r[1] for r in conn.execute("PRAGMA table_info(chat_messages)").fetchall()]
                if "sources_json" not in cols:
                    conn.execute("ALTER TABLE chat_messages ADD COLUMN sources_json TEXT")
            except sqlite3.Error:
                pass
            conn.commit()
            self._schema_ready = True

    def _ensure_parent_dir(self) -> None:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ChatStoreError(f"创建数据目录失败: {e}") from e

    def append_message(
        self,
        chat_id: str,
        role: str,
        content: str,
        title_hint: str | None = None,
        sources_json: str | None = None,
    ) -> None:
        """追加一条消息；会话不存在时隐式创建（标题取 title_hint / 首条内容截断）。

        Raises:
            ChatStoreError: DB 写失败（调用方降级处理，不阻断对话）
        """
        if not chat_id or not role or role not in ("user", "assistant", "system"):
            raise ChatStoreError(f"非法消息参数: chat_id={chat_id!r} role={role!r}")
        self._ensure_parent_dir()
        now = _now_iso()
        try:
            with self._conn() as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    "SELECT id, title FROM chat_sessions WHERE id = ?", (chat_id,)
                ).fetchone()
                if row is None:
                    hint = (title_hint or content or "").strip().replace("\n", " ")
                    title = hint[:_TITLE_MAX] or "新会话"
                    conn.execute(
                        "INSERT INTO chat_sessions (id, title, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?)",
                        (chat_id, title, now, now),
                    )
                conn.execute(
                    "INSERT INTO chat_messages (chat_id, role, content, created_at, sources_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (chat_id, role, content, now, sources_json),
                )
                conn.execute(
                    "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
                    (now, chat_id),
                )
        except sqlite3.Error as e:
            raise ChatStoreError(f"写入会话消息失败: {e}") from e

    def update_last_assistant_content(self, chat_id: str, content: str) -> bool:
        """把会话中最后一条 assistant 消息的 content 整段替换为 content。

        用于「继续写」合并：避免 append_turn 把合并全文再写成新轮，
        导致重启后历史重复。返回是否更新到行。
        """
        if not chat_id or content is None:
            raise ChatStoreError(f"非法参数: chat_id={chat_id!r}")
        self._ensure_parent_dir()
        now = _now_iso()
        try:
            with self._conn() as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    "SELECT id FROM chat_messages "
                    "WHERE chat_id = ? AND role = 'assistant' "
                    "ORDER BY id DESC LIMIT 1",
                    (chat_id,),
                ).fetchone()
                if row is None:
                    return False
                conn.execute(
                    "UPDATE chat_messages SET content = ? WHERE id = ?",
                    (content, row[0]),
                )
                conn.execute(
                    "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
                    (now, chat_id),
                )
                return True
        except sqlite3.Error as e:
            raise ChatStoreError(f"更新会话 assistant 消息失败: {e}") from e

    def append_turn(
        self,
        chat_id: str,
        user_content: str,
        assistant_content: str | None,
        title_hint: str | None = None,
        sources_json: str | None = None,
    ) -> None:
        """一轮对话（user + 可选 assistant）在**单事务**内落库。

        与分两次 `append_message`（各开一个事务）相比：
        - assistant 写失败时 user 轮一并回滚，不留孤儿轮；
        - 并发下同一 chat_id 两次请求不会交错成
          `A_user, B_user, A_asst, B_asst`（`get_history` 按 id 升序取回会错序）。

        assistant_content 为 None 表示本轮无回答（如无检索命中时的提前返回），
        仅落库用户消息。
        """
        if not chat_id or not user_content:
            raise ChatStoreError(f"非法消息参数: chat_id={chat_id!r}")
        self._ensure_parent_dir()
        now = _now_iso()
        try:
            with self._conn() as conn:
                self._ensure_schema(conn)
                row = conn.execute(
                    "SELECT id, title FROM chat_sessions WHERE id = ?", (chat_id,)
                ).fetchone()
                if row is None:
                    hint = (title_hint or user_content or "").strip().replace("\n", " ")
                    title = hint[:_TITLE_MAX] or "新会话"
                    conn.execute(
                        "INSERT INTO chat_sessions (id, title, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?)",
                        (chat_id, title, now, now),
                    )
                conn.execute(
                    "INSERT INTO chat_messages (chat_id, role, content, created_at, sources_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (chat_id, "user", user_content, now, None),
                )
                if assistant_content is not None:
                    conn.execute(
                        "INSERT INTO chat_messages (chat_id, role, content, created_at, sources_json) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (chat_id, "assistant", assistant_content, now, sources_json),
                    )
                conn.execute(
                    "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
                    (now, chat_id),
                )
        except sqlite3.Error as e:
            raise ChatStoreError(f"写入会话消息失败: {e}") from e

    def get_history(self, chat_id: str, limit: int = 20) -> list[dict[str, str]]:
        """取会话最近 limit 条消息（时间正序），供 LLM 多轮上下文使用。

        会话不存在 / DB 异常时返回空列表（调用方按新会话处理）。
        """
        try:
            with self._conn() as conn:
                self._ensure_schema(conn)
                rows = conn.execute(
                    "SELECT role, content FROM chat_messages WHERE chat_id = ? "
                    "ORDER BY id DESC LIMIT ?",
                    (chat_id, limit),
                ).fetchall()
                return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
        except sqlite3.Error:
            return []

    def get_messages(self, chat_id: str) -> list[ChatMessageRow]:
        """取会话全部消息（时间正序），供 UI 回看完整历史。"""
        try:
            with self._conn() as conn:
                self._ensure_schema(conn)
                rows = conn.execute(
                    "SELECT role, content, created_at, sources_json FROM chat_messages "
                    "WHERE chat_id = ? ORDER BY id",
                    (chat_id,),
                ).fetchall()
                return [
                    ChatMessageRow(
                        role=r["role"],
                        content=r["content"],
                        created_at=r["created_at"],
                        sources_json=r["sources_json"],
                    )
                    for r in rows
                ]
        except sqlite3.Error as e:
            raise ChatStoreError(f"读取会话消息失败: {e}") from e

    def get_session(self, chat_id: str) -> ChatSessionSummary | None:
        """取会话摘要；不存在返回 None。"""
        try:
            with self._conn() as conn:
                self._ensure_schema(conn)
                r = conn.execute(
                    "SELECT s.id, s.title, s.created_at, s.updated_at, "
                    "(SELECT COUNT(*) FROM chat_messages m WHERE m.chat_id = s.id) AS msg_count "
                    "FROM chat_sessions s WHERE s.id = ?",
                    (chat_id,),
                ).fetchone()
                if r is None:
                    return None
                return ChatSessionSummary(
                    chat_id=r["id"],
                    title=r["title"],
                    message_count=r["msg_count"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                )
        except sqlite3.Error as e:
            raise ChatStoreError(f"读取会话失败: {e}") from e

    def list_sessions(self, limit: int = 50, offset: int = 0) -> list[ChatSessionSummary]:
        """按更新时间倒序列出会话（默认最近 50 个）。"""
        try:
            with self._conn() as conn:
                self._ensure_schema(conn)
                rows = conn.execute(
                    "SELECT s.id, s.title, s.created_at, s.updated_at, "
                    "(SELECT COUNT(*) FROM chat_messages m WHERE m.chat_id = s.id) AS msg_count "
                    "FROM chat_sessions s "
                    "ORDER BY s.updated_at DESC, s.id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
                return [
                    ChatSessionSummary(
                        chat_id=r["id"],
                        title=r["title"],
                        message_count=r["msg_count"],
                        created_at=r["created_at"],
                        updated_at=r["updated_at"],
                    )
                    for r in rows
                ]
        except sqlite3.Error as e:
            raise ChatStoreError(f"列出会话失败: {e}") from e

    def count_sessions(self) -> int:
        """返回会话总数（供分页列表的 total 字段使用，AUD-013）。"""
        try:
            with self._conn() as conn:
                self._ensure_schema(conn)
                r = conn.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()
                return int(r[0]) if r else 0
        except sqlite3.Error as e:
            raise ChatStoreError(f"统计会话失败: {e}") from e

    def delete_session(self, chat_id: str) -> bool:
        """删除会话及其全部消息（级联）；不存在返回 False。"""
        try:
            with self._conn() as conn:
                self._ensure_schema(conn)
                cur = conn.execute("DELETE FROM chat_sessions WHERE id = ?", (chat_id,))
                return cur.rowcount > 0
        except sqlite3.Error as e:
            raise ChatStoreError(f"删除会话失败: {e}") from e
