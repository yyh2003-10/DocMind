"""表格分块器 — 整表一块（超长按行滑窗），跨页合并。

策略：
1. `type == table` 的元素直接作为一块（保留完整 markdown 表格）
2. 连续的 `table_row` 元素合并为一块（Excel 加载器输出的逐行元素）
3. 表格块 metadata 标记 `{type: "table", rows: n, cols: m}`
4. 超过 `chunk_max_chars` 的超长表格按数据行滑窗切分（表头+分隔行随每个
   子块保留），避免单个巨 Chunk 反复 join/copy 且被嵌入 512 token 截断丢内容

入口：`TableChunker.chunk(elements) -> list[Chunk]`
"""

from __future__ import annotations

from typing import Any

from doc2mind.core.chunker.base import Chunk, Chunker, ChunkerError
from doc2mind.core.config import Settings
from doc2mind.core.models import DocumentElement, ElementType


class TableChunker(Chunker):
    """表格分块器：保护整表完整，连续 table_row 合并，超长按行滑窗。"""

    def __init__(self, settings: Settings | None = None) -> None:
        if settings is None:
            from doc2mind.core.config import get_settings

            settings = get_settings()
        self.settings = settings

    def chunk(self, elements: list[DocumentElement]) -> list[Chunk]:
        """提取表格元素，生成 Chunk。

        Returns:
            表格 Chunk 列表（仅含 table / table_row 元素）。
        """
        if not elements:
            return []

        chunks: list[Chunk] = []
        i = 0
        n = len(elements)

        try:
            while i < n:
                el = elements[i]

                # 完整 table 元素 → 一块（超长按行滑窗）
                if el.type is ElementType.TABLE:
                    rows = int(el.metadata.get("rows", 0)) or _count_table_rows(el.content)
                    cols = int(el.metadata.get("cols", 0)) or _count_table_cols(el.content)
                    for md in _split_table(el.content, self.settings):
                        chunks.append(
                            Chunk(
                                content=md,
                                tokens=_estimate_tokens(md),
                                metadata={
                                    **el.metadata,
                                    "type": "table",
                                    "rows": rows,
                                    "cols": cols,
                                },
                            )
                        )
                    i += 1
                    continue

                # 连续 table_row 元素 → 合并成 markdown 表格
                if el.type is ElementType.TABLE_ROW:
                    row_texts: list[str] = []
                    sheet = el.metadata.get("sheet")
                    while i < n and elements[i].type is ElementType.TABLE_ROW:
                        row_el = elements[i]
                        # 同一 sheet 才合并（Excel 多 sheet 不串）
                        if sheet is not None and row_el.metadata.get("sheet") != sheet:
                            break
                        row_texts.append(row_el.content)
                        i += 1
                    if row_texts:
                        md = _rows_to_markdown(row_texts)
                        if md.strip():
                            for piece in _split_table(md, self.settings):
                                chunks.append(
                                    Chunk(
                                        content=piece,
                                        tokens=_estimate_tokens(piece),
                                        metadata={
                                            "type": "table",
                                            "rows": len(row_texts),
                                            "cols": _count_table_cols(md),
                                            "sheet": sheet,
                                        },
                                    )
                                )
                    continue

                # 其他元素跳过（由 SemanticChunker / CodeChunker 处理）
                i += 1

            return chunks

        except ChunkerError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ChunkerError(f"表格分块失败: {e}") from e


# --- 辅助函数 ---
def _estimate_tokens(text: str) -> int:
    """token 估算（与 semantic.py 一致的启发式）。"""
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def _count_table_rows(md: str) -> int:
    """数 markdown 表格的行数（含表头与分隔行）。

    保守做法：数 `|` 开头的行数。
    """
    return sum(1 for line in md.splitlines() if line.strip().startswith("|"))


def _count_table_cols(md: str) -> int:
    """数 markdown 表格的列数。"""
    for line in md.splitlines():
        if line.strip().startswith("|"):
            # | a | b | → ["", "a ", "b ", ""] → 3 个分隔但实际 2 列
            return max(line.count("|") - 1, 1)
    return 0


def _rows_to_markdown(rows: list[str]) -> str:
    """把逐行 markdown table_row 拼成完整 markdown 表格。

    `rows` 中每个元素形如 `"| col1 | col2 |"`。
    第一行视为表头，紧接一行分隔 `|---|---|`，其余为数据。
    """
    if not rows:
        return ""
    header = rows[0]
    col_count = _count_table_cols(header)
    sep = "|" + "---|" * col_count
    body = rows[1:] if len(rows) > 1 else []
    return "\n".join([header, sep, *body])


def _split_table(md: str, settings: Any) -> list[str]:
    """超长 markdown 表格按数据行滑窗切分（表头 + 分隔行随每个子块保留）。

    与代码块的滑窗策略一致：`chunk_max_chars` 为单块上限，
    `chunk_overlap_chars` 为相邻块重叠（行粒度取整）。
    短表原样返回单块。
    """
    max_chars = settings.chunk_max_chars
    overlap = settings.chunk_overlap_chars
    if len(md) <= max_chars:
        return [md]

    lines = md.splitlines()
    header = lines[0] if lines else ""
    sep = lines[1] if len(lines) > 1 else ""
    body = lines[2:] if len(lines) > 2 else []

    pieces: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in body:
        line_len = len(line) + 1  # +1 for \n
        if current_len + line_len > max_chars and current:
            pieces.append("\n".join([header, sep, *current]))
            # overlap：保留尾部若干行
            overlap_len = 0
            keep_from = len(current)
            for j in range(len(current) - 1, -1, -1):
                overlap_len += len(current[j]) + 1
                if overlap_len >= overlap:
                    keep_from = j
                    break
            current = current[keep_from:]
            current_len = sum(len(item) + 1 for item in current)
        current.append(line)
        current_len += line_len

    if current:
        pieces.append("\n".join([header, sep, *current]))

    stripped = [p for p in pieces if p.strip()]
    # 兜底：无法按行切（如只有表头）时保持原样，避免丢内容
    return stripped or [md]
