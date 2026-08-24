"""一次性修复：为无切片关联的图谱实体回填 chunk_entities，删除彻底无源的孤儿实体。

背景：chunk_entities 关联机制于 2026-08-18（90c8e48）才引入，此前写入的实体没有切片
关联；8-22 图谱重构期间存在多进程版本错配，又落了一批零关联实体。本脚本：
1. 对每个无关联实体，在其所属集合的切片正文里按名字 LIKE 匹配，回填 chunk_entities；
2. 完全匹配不到正文的实体（LLM 归一化过名称，字面不存在于原文），备份后删除，
   其关系随外键 ON DELETE CASCADE 一并清理。

用法：
    python scripts/repair_graph_chunk_links.py             # 预览（dry-run，零写入）
    python scripts/repair_graph_chunk_links.py --apply     # 实际执行（删除前备份 JSON）
    python scripts/repair_graph_chunk_links.py --db PATH   # 指定数据库文件
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 每个实体最多回填的切片数（get_entity_detail 详情页上限为 8，略留余量）
MAX_LINKS_PER_ENTITY = 10
# 短于该长度的名字不做 LIKE 匹配（避免过度误匹配）
MIN_NAME_LEN = 2


def default_db_path() -> Path:
    local = os.environ.get("LOCALAPPDATA") or Path.home() / ".local"
    return Path(local) / "doc2mind" / "doc2mind.db"


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.row_factory = sqlite3.Row
    return conn


def escape_like(name: str) -> str:
    """转义 LIKE 通配符，配合 ESCAPE '\\' 使用。"""
    return name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def find_orphans(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    cur = conn.execute(
        """
        SELECT id, name, type, collection, doc_count, created_at
        FROM entities e
        WHERE NOT EXISTS (SELECT 1 FROM chunk_entities ce WHERE ce.entity_id = e.id)
        ORDER BY doc_count DESC, name
        """
    )
    return cur.fetchall()


def match_chunks(conn: sqlite3.Connection, entity: sqlite3.Row) -> list[int]:
    """在实体所属集合的切片正文里按名字模糊匹配，返回 chunk id 列表。"""
    name = entity["name"].strip()
    if len(name) < MIN_NAME_LEN:
        return []
    cur = conn.execute(
        f"""
        SELECT cm.id
        FROM chunks_meta cm
        WHERE cm.collection = ? AND cm.content LIKE ? ESCAPE '\\'
        ORDER BY cm.document_id, cm.chunk_index
        LIMIT ?
        """,
        (entity["collection"], f"%{escape_like(name)}%", MAX_LINKS_PER_ENTITY),
    )
    return [r["id"] for r in cur.fetchall()]


def export_backup(
    conn: sqlite3.Connection, orphans: list[sqlite3.Row], db_path: Path
) -> Path:
    """把待删除实体及其关系导出为 JSON 备份（可人工恢复）。"""
    payload: list[dict] = []
    for ent in orphans:
        rels = conn.execute(
            """
            SELECT r.relation, r.created_at,
                   ef.name AS from_name, et.name AS to_name
            FROM entity_relations r
            JOIN entities ef ON ef.id = r.from_id
            JOIN entities et ON et.id = r.to_id
            WHERE r.from_id = ? OR r.to_id = ?
            """,
            (ent["id"], ent["id"]),
        ).fetchall()
        payload.append(
            {
                "id": ent["id"],
                "name": ent["name"],
                "type": ent["type"],
                "collection": ent["collection"],
                "doc_count": ent["doc_count"],
                "created_at": ent["created_at"],
                "relations": [dict(r) for r in rels],
            }
        )
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.parent / f"graph_orphan_backup_{ts}.json"
    backup_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return backup_path


def main() -> int:
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="修复图谱实体的切片关联")
    parser.add_argument("--apply", action="store_true", help="实际执行（默认只预览）")
    parser.add_argument("--db", type=Path, default=default_db_path(), help="数据库路径")
    args = parser.parse_args()

    if not args.db.exists():
        print(f"数据库不存在: {args.db}")
        return 1

    conn = connect(args.db)
    try:
        orphans = find_orphans(conn)
        print(f"数据库: {args.db}")
        print(f"无切片关联实体: {len(orphans)} 个")
        print(f"模式: {'APPLY（实写）' if args.apply else 'DRY-RUN（预览）'}")
        print("-" * 60)

        link_plan: list[tuple[str, sqlite3.Row, list[int]]] = []
        delete_plan: list[sqlite3.Row] = []
        for ent in orphans:
            chunk_ids = match_chunks(conn, ent)
            if chunk_ids:
                link_plan.append((ent["name"], ent, chunk_ids))
            else:
                delete_plan.append(ent)

        print(f"[回填] {len(link_plan)} 个实体可按名字匹配到正文：")
        for name, ent, chunk_ids in link_plan:
            print(f"  [{ent['type']}] {name} -> {len(chunk_ids)} 个切片")

        print(f"[删除] {len(delete_plan)} 个实体完全无源（备份后删除）：")
        for ent in delete_plan:
            print(f"  [{ent['type']}] {ent['name']} (coll={ent['collection']})")

        if not args.apply:
            print("-" * 60)
            print("预览完成，未写入任何数据。加 --apply 执行。")
            return 0

        backup_path = export_backup(conn, delete_plan, args.db)
        print("-" * 60)
        print(f"已备份待删实体 -> {backup_path}")

        with conn:  # 单事务提交全部修复
            linked = 0
            for _, ent, chunk_ids in link_plan:
                for cid in chunk_ids:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO chunk_entities (chunk_id, entity_id) VALUES (?, ?)",
                        (cid, ent["id"]),
                    )
                    linked += cur.rowcount
            deleted = 0
            for ent in delete_plan:
                cur = conn.execute("DELETE FROM entities WHERE id = ?", (ent["id"],))
                deleted += cur.rowcount

        remaining = len(find_orphans(conn))
        print(
            f"完成：回填关联 {linked} 行，删除实体 {deleted} 个"
            f"（关系随外键级联清理），剩余无关联实体 {remaining} 个"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
