"""分块流水线 — 把 `LoadedDocument.elements` 切成 `list[Chunk]`。

执行顺序（保留文档顺序）：
1. `CodeChunker` 提取所有 code 元素 → 代码块
2. `TableChunker` 提取所有 table / table_row 元素 → 表格块
3. `SemanticChunker` 处理剩余元素（heading / paragraph / list）→ 语义块
4. 三个结果合并后按 `chunk_index` 重新编号

注：保护元素（table/code）在各自 chunker 内部已按文档顺序处理，
合并时按源元素在原文档中的位置排序，确保跨块语义连续性。
"""

from __future__ import annotations

from doc2mind.core.chunker.base import Chunk, ChunkerError
from doc2mind.core.chunker.code import CodeChunker
from doc2mind.core.chunker.semantic import SemanticChunker
from doc2mind.core.chunker.table import TableChunker
from doc2mind.core.config import Settings
from doc2mind.core.models import DocumentElement, LoadedDocument


def chunk_document(
    doc: LoadedDocument, settings: Settings | None = None
) -> list[Chunk]:
    """把已加载文档切成 Chunk 列表。

    Args:
        doc: `LoadedDocument`
        settings: 配置，默认用 `get_settings()`

    Returns:
        `list[Chunk]`，按文档顺序排列，`metadata.chunk_index` 已编号。
    """
    if settings is None:
        from doc2mind.core.config import get_settings

        settings = get_settings()

    if not doc.elements:
        return []

    try:
        code_chunker = CodeChunker(settings)
        table_chunker = TableChunker()
        semantic_chunker = SemanticChunker(settings)

        # 为每个元素分配原文档序号，便于最后重排
        indexed: list[tuple[int, DocumentElement]] = list(
            enumerate(doc.elements)
        )

        # 按类型分流（保留原始 index）
        code_els = [(i, e) for i, e in indexed if e.type.name == "CODE"]
        table_els = [
            (i, e)
            for i, e in indexed
            if e.type.name in ("TABLE", "TABLE_ROW")
        ]
        semantic_els = [(i, e) for i, e in indexed if e.type.name not in (
            "CODE", "TABLE", "TABLE_ROW"
        )]

        # 各分块器处理（传入纯元素列表）
        code_chunks = code_chunker.chunk([e for _, e in code_els])
        table_chunks = table_chunker.chunk([e for _, e in table_els])
        semantic_chunks = semantic_chunker.chunk([e for _, e in semantic_els])

        # 合并：每块回填原元素 index（用首元素 index 近似）
        annotated: list[tuple[int, Chunk]] = []

        for ci, chunk in enumerate(code_chunks):
            orig_idx = code_els[ci][0] if ci < len(code_els) else 0
            annotated.append((orig_idx, chunk))

        for ti, chunk in enumerate(table_chunks):
            orig_idx = table_els[ti][0] if ti < len(table_els) else 0
            annotated.append((orig_idx, chunk))

        si = 0
        for chunk in semantic_chunks:
            orig_idx = semantic_els[si][0] if si < len(semantic_els) else 0
            annotated.append((orig_idx, chunk))
            si += 1

        # 按原文档序号稳定排序
        annotated.sort(key=lambda x: x[0])

        # 编号 chunk_index
        result: list[Chunk] = []
        for idx, (_, chunk) in enumerate(annotated):
            meta = dict(chunk.metadata)
            meta["chunk_index"] = idx
            meta.setdefault("source_format", doc.format.value)
            result.append(
                Chunk(
                    content=chunk.content,
                    tokens=chunk.tokens,
                    metadata=meta,
                )
            )

        return result

    except ChunkerError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ChunkerError(f"分块流水线失败: {e}") from e
