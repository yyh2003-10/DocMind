"""知识图谱持久化存储 — SQLite 表 entities / entity_relations / chunk_entities。

与向量库（sqlite_vec.VectorStore）共用同一个 DB 文件（`settings.db_path`），
采用独立连接与事务模式，不依赖 sqlite-vec 扩展。

设计要点：
- 实体去重合并：按 (collection, name, type) 唯一索引进行 upsert，重复出现则增加 doc_count；
- 关系存储：(from_id, to_id, relation) 唯一约束，幂等写入；
- 分块关联：chunk_entities 记录分块与实体的关联映射；
- 限制说明：MVP 阶段实体合并基于精确名称+类型匹配，暂未做跨实体模糊语义融合。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("doc2mind.graph_store")


def _now_iso() -> str:
    """微秒精度 ISO 8601 时间戳。"""
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="microseconds")


class GraphStoreError(Exception):
    """知识图谱存储异常。"""


class GraphStore:
    """知识图谱存储（线程安全的按操作连接）。"""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path)
        self._schema_lock = threading.Lock()
        self._schema_ready = False

    def close(self) -> None:
        """关闭存储（预留生命周期接口）。"""
        # 按操作开关连接，无需长连接池管理

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self._db_path),
            check_same_thread=False,
            timeout=30.0,
        )
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """按操作开关连接并自动管理事务。"""
        conn = self._connect()
        self._ensure_schema(conn)
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
                CREATE TABLE IF NOT EXISTS entities (
                    id          TEXT PRIMARY KEY,
                    name        TEXT NOT NULL,
                    type        TEXT NOT NULL,
                    collection  TEXT NOT NULL DEFAULT 'default',
                    doc_count   INTEGER NOT NULL DEFAULT 1,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_uniq
                    ON entities(collection, name, type);

                CREATE TABLE IF NOT EXISTS entity_relations (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_id     TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                    to_id       TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                    relation    TEXT NOT NULL,
                    created_at  TEXT NOT NULL,
                    UNIQUE(from_id, to_id, relation)
                );

                CREATE TABLE IF NOT EXISTS chunk_entities (
                    chunk_id    INTEGER NOT NULL,
                    entity_id   TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
                    PRIMARY KEY (chunk_id, entity_id)
                );
                """
            )
            self._schema_ready = True

    def _upsert_entity_conn(
        self, conn: sqlite3.Connection, name: str, etype: str, collection: str
    ) -> str:
        clean_name = name.strip()
        clean_type = etype.strip().lower() or "other"
        clean_coll = collection.strip() or "default"
        now = _now_iso()

        cur = conn.execute(
            "SELECT id FROM entities WHERE collection = ? AND name = ? AND type = ?",
            (clean_coll, clean_name, clean_type),
        )
        row = cur.fetchone()
        if row:
            entity_id = row["id"]
            conn.execute(
                "UPDATE entities SET doc_count = doc_count + 1, updated_at = ? WHERE id = ?",
                (now, entity_id),
            )
            return str(entity_id)

        entity_id = uuid.uuid4().hex
        conn.execute(
            """
            INSERT INTO entities (id, name, type, collection, doc_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, 1, ?, ?)
            """,
            (entity_id, clean_name, clean_type, clean_coll, now, now),
        )
        return entity_id

    def upsert_entity(self, name: str, etype: str, collection: str = "default") -> str:
        """插入或更新实体。

        已存在则 doc_count + 1 并更新 updated_at，返回 entity_id。
        """
        with self._conn() as conn:
            return self._upsert_entity_conn(conn, name, etype, collection)

    def upsert_relation(self, from_id: str, to_id: str, relation: str) -> None:
        """插入关系，已存在相同关系则忽略。"""
        clean_rel = relation.strip() or "related_to"
        now = _now_iso()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO entity_relations (from_id, to_id, relation, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (from_id, to_id, clean_rel, now),
            )

    def link_chunk(self, chunk_id: int, entity_id: str) -> None:
        """建立 chunk 与实体的关联。"""
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO chunk_entities (chunk_id, entity_id) VALUES (?, ?)",
                (chunk_id, entity_id),
            )

    def add_document_entities(
        self,
        doc_id: str,
        collection: str,
        entities: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        chunk_id: int | None = None,
    ) -> dict[str, int]:
        """批量录入文档抽取出的实体和关系（单事务提交）。

        若 relation 中的 from/to 未在 entities 中给出，自动以 type='other' upsert 兜底。
        """
        clean_coll = collection.strip() or "default"
        name_to_id: dict[str, str] = {}
        added_entities = 0
        added_relations = 0
        now = _now_iso()

        with self._conn() as conn:
            # 1. 录入实体
            for ent in entities:
                name = str(ent.get("name", "")).strip()
                etype = str(ent.get("type", "other")).strip()
                if not name:
                    continue
                eid = self._upsert_entity_conn(conn, name, etype, clean_coll)
                name_to_id[name] = eid
                added_entities += 1
                if chunk_id is not None:
                    conn.execute(
                        "INSERT OR IGNORE INTO chunk_entities (chunk_id, entity_id) VALUES (?, ?)",
                        (chunk_id, eid),
                    )

            # 2. 录入关系
            for rel in relations:
                from_name = str(rel.get("from", "")).strip()
                to_name = str(rel.get("to", "")).strip()
                rel_type = str(rel.get("type", rel.get("relation", "related_to"))).strip() or "related_to"

                if not from_name or not to_name or from_name == to_name:
                    continue

                if from_name not in name_to_id:
                    name_to_id[from_name] = self._upsert_entity_conn(conn, from_name, "other", clean_coll)
                if to_name not in name_to_id:
                    name_to_id[to_name] = self._upsert_entity_conn(conn, to_name, "other", clean_coll)

                from_id = name_to_id[from_name]
                to_id = name_to_id[to_name]

                conn.execute(
                    """
                    INSERT OR IGNORE INTO entity_relations (from_id, to_id, relation, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (from_id, to_id, rel_type, now),
                )
                added_relations += 1

        return {"entities": added_entities, "relations": added_relations}

    def get_extracted_doc_ids(self, collection: str | None = None) -> set[str]:
        """获取已有实体关联的文档 ID 集合（用于增量抽取与智能跳过已处理文档）。"""
        with self._conn() as conn:
            try:
                if collection:
                    cur = conn.execute(
                        """
                        SELECT DISTINCT c.document_id
                        FROM chunk_entities ce
                        JOIN chunks c ON ce.chunk_id = c.id
                        WHERE c.collection = ?
                        """,
                        (collection,),
                    )
                else:
                    cur = conn.execute(
                        """
                        SELECT DISTINCT c.document_id
                        FROM chunk_entities ce
                        JOIN chunks c ON ce.chunk_id = c.id
                        """
                    )
                return {row["document_id"] for row in cur.fetchall()}
            except Exception:
                return set()

    def get_graph(self, collection: str | None = None, limit: int = 200) -> dict[str, Any]:
        """获取用于力导向图可视化的节点与边数据。"""
        with self._conn() as conn:
            if collection:
                node_cur = conn.execute(
                    """
                    SELECT id, name, type, collection, doc_count
                    FROM entities
                    WHERE collection = ?
                    ORDER BY doc_count DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (collection, limit),
                )
            else:
                node_cur = conn.execute(
                    """
                    SELECT id, name, type, collection, doc_count
                    FROM entities
                    ORDER BY doc_count DESC, updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )

            nodes_raw = node_cur.fetchall()
            if not nodes_raw:
                return {"nodes": [], "edges": [], "total_nodes": 0}

            node_ids = {row["id"] for row in nodes_raw}
            nodes = [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "type": row["type"],
                    "group": row["type"],
                    "size": max(1, int(row["doc_count"])),
                    "collection": row["collection"],
                }
                for row in nodes_raw
            ]

            placeholders = ",".join("?" for _ in node_ids)
            edge_cur = conn.execute(
                f"""
                SELECT from_id, to_id, relation
                FROM entity_relations
                WHERE from_id IN ({placeholders}) AND to_id IN ({placeholders})
                """,
                tuple(node_ids) + tuple(node_ids),
            )
            edges = [
                {
                    "from": row["from_id"],
                    "to": row["to_id"],
                    "label": row["relation"],
                }
                for row in edge_cur.fetchall()
            ]

            return {
                "nodes": nodes,
                "edges": edges,
                "total_nodes": len(nodes),
            }

    def get_entity_relations(self, entity_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """按实体查询其关联的实体与关系（点击展开用）。"""
        with self._conn() as conn:
            cur = conn.execute(
                """
                SELECT r.id, r.from_id, r.to_id, r.relation,
                       e1.name as from_name, e1.type as from_type,
                       e2.name as to_name, e2.type as to_type
                FROM entity_relations r
                JOIN entities e1 ON r.from_id = e1.id
                JOIN entities e2 ON r.to_id = e2.id
                WHERE r.from_id = ? OR r.to_id = ?
                LIMIT ?
                """,
                (entity_id, entity_id, limit),
            )
            results: list[dict[str, Any]] = []
            for row in cur.fetchall():
                results.append(
                    {
                        "relation_id": row["id"],
                        "from_id": row["from_id"],
                        "from_name": row["from_name"],
                        "from_type": row["from_type"],
                        "to_id": row["to_id"],
                        "to_name": row["to_name"],
                        "to_type": row["to_type"],
                        "relation": row["relation"],
                    }
                )
            return results

    def find_entities_by_keyword(self, keyword: str, limit: int = 5) -> list[dict[str, Any]]:
        """按关键字模糊匹配实体列表。"""
        if not keyword or not keyword.strip():
            return []
        pattern = f"%{keyword.strip()}%"
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT id, name, type, collection, doc_count FROM entities WHERE name LIKE ? ORDER BY doc_count DESC LIMIT ?",
                (pattern, limit),
            )
            return [dict(row) for row in cur.fetchall()]

    def get_stats(self, collection: str | None = None) -> dict[str, Any]:
        """获取实体与关系统计。"""
        with self._conn() as conn:
            if collection:
                ent_count = conn.execute(
                    "SELECT COUNT(*) as c FROM entities WHERE collection = ?",
                    (collection,),
                ).fetchone()["c"]
                rel_count = conn.execute(
                    """
                    SELECT COUNT(*) as c
                    FROM entity_relations r
                    JOIN entities e ON r.from_id = e.id
                    WHERE e.collection = ?
                    """,
                    (collection,),
                ).fetchone()["c"]
            else:
                ent_count = conn.execute("SELECT COUNT(*) as c FROM entities").fetchone()["c"]
                rel_count = conn.execute("SELECT COUNT(*) as c FROM entity_relations").fetchone()["c"]

            return {
                "entity_count": ent_count,
                "relation_count": rel_count,
                "collection": collection,
            }

    def get_entity_detail(self, entity_id: str, snippet_limit: int = 8) -> dict[str, Any]:
        """获取实体的完整知识全景：基本信息、关联关系、来源文档和上下文内容切片。"""
        with self._conn() as conn:
            # 1. 实体基本信息
            ent_cur = conn.execute(
                "SELECT id, name, type, collection, doc_count, created_at, updated_at FROM entities WHERE id = ?",
                (entity_id,),
            )
            ent_row = ent_cur.fetchone()
            if not ent_row:
                return {}

            entity_info = {
                "id": ent_row["id"],
                "name": ent_row["name"],
                "type": ent_row["type"],
                "collection": ent_row["collection"],
                "doc_count": ent_row["doc_count"],
                "created_at": ent_row["created_at"],
                "updated_at": ent_row["updated_at"],
            }

            # 2. 关系网
            relations = self.get_entity_relations(entity_id, limit=50)

            # 3. 来源分块与上下文片段
            snippets: list[dict[str, Any]] = []
            seen_chunk_ids: set[int] = set()

            try:
                # 优先从 chunk_entities 关联表获取
                chunk_cur = conn.execute(
                    """
                    SELECT cm.id as chunk_id, cm.document_id, cm.content, cm.source, cm.heading, cm.page,
                           d.title as doc_title, d.summary as doc_summary
                    FROM chunk_entities ce
                    JOIN chunks_meta cm ON ce.chunk_id = cm.id
                    LEFT JOIN documents d ON cm.document_id = d.id
                    WHERE ce.entity_id = ?
                    LIMIT ?
                    """,
                    (entity_id, snippet_limit),
                )
                for r in chunk_cur.fetchall():
                    cid = r["chunk_id"]
                    seen_chunk_ids.add(cid)
                    snippets.append(
                        {
                            "chunk_id": cid,
                            "document_id": r["document_id"],
                            "content": r["content"],
                            "source": r["source"],
                            "heading": r["heading"] or "",
                            "page": r["page"] or 0,
                            "doc_title": r["doc_title"] or (Path(r["source"]).name if r["source"] else ""),
                            "doc_summary": r["doc_summary"] or "",
                        }
                    )
            except Exception as e:
                logger.debug("从 chunk_entities 查询分块失败: %s", e)

            # 如果不足，通过内容匹配兜底
            if len(snippets) < snippet_limit:
                rem = snippet_limit - len(snippets)
                try:
                    match_cur = conn.execute(
                        """
                        SELECT cm.id as chunk_id, cm.document_id, cm.content, cm.source, cm.heading, cm.page,
                               d.title as doc_title, d.summary as doc_summary
                        FROM chunks_meta cm
                        LEFT JOIN documents d ON cm.document_id = d.id
                        WHERE cm.collection = ? AND cm.content LIKE ?
                        LIMIT ?
                        """,
                        (entity_info["collection"], f"%{entity_info['name']}%", rem * 2),
                    )
                    for r in match_cur.fetchall():
                        cid = r["chunk_id"]
                        if cid in seen_chunk_ids:
                            continue
                        seen_chunk_ids.add(cid)
                        snippets.append(
                            {
                                "chunk_id": cid,
                                "document_id": r["document_id"],
                                "content": r["content"],
                                "source": r["source"],
                                "heading": r["heading"] or "",
                                "page": r["page"] or 0,
                                "doc_title": r["doc_title"] or (Path(r["source"]).name if r["source"] else ""),
                                "doc_summary": r["doc_summary"] or "",
                            }
                        )
                        if len(snippets) >= snippet_limit:
                            break
                except Exception as e:
                    logger.debug("从 chunks_meta 模糊匹配分块失败: %s", e)

            # 4. 汇总来源文档
            doc_map: dict[str, dict[str, Any]] = {}
            for s in snippets:
                src = s["source"]
                if not src:
                    continue
                if src not in doc_map:
                    doc_map[src] = {
                        "source": src,
                        "title": s["doc_title"] or (Path(src).name if src else ""),
                        "summary": s["doc_summary"],
                        "chunk_count": 0,
                    }
                doc_map[src]["chunk_count"] += 1

            source_documents = list(doc_map.values())

            return {
                "entity": entity_info,
                "relations": relations,
                "snippets": snippets,
                "source_documents": source_documents,
            }

