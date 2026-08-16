"""语义分块器 — 按标题边界 / 段落合并 / 滑窗。

策略：
1. 按 heading 元素切分文档为"章节"
2. 章节内的 paragraph / list 合并为一块
3. 超过 `chunk_max_chars` 的块 → 递归滑窗（overlap = chunk_overlap_chars）
4. 过短块（< chunk_min_chars）→ 并入相邻块
5. table / code 元素交给 `TableChunker` / `CodeChunker` 单独处理

入口：`SemanticChunker.chunk(elements) -> list[Chunk]`
"""

from __future__ import annotations

from dataclasses import dataclass

from doc2mind.core.chunker.base import Chunk, Chunker, ChunkerError
from doc2mind.core.config import Settings
from doc2mind.core.models import DocumentElement, ElementType

# 需要保护的特殊元素类型：不参与语义滑窗
_PROTECTED_TYPES = frozenset({ElementType.TABLE, ElementType.CODE})


@dataclass
class SemanticChunker(Chunker):
    """语义分块器。"""

    settings: Settings

    # --- token 估算 ---
    def _estimate_tokens(self, text: str) -> int:
        """粗略估算 token 数：中文 ~2.5 字/token，英文 ~4 字符/token。

        用混合启发式：CJK 字符按 1:1，其他按 4:1。
        """
        cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
        other = len(text) - cjk
        # CJK 每 1 字 ≈ 1 token；其他每 4 字符 ≈ 1 token
        return cjk + (other + 3) // 4

    # --- 主流程 ---
    def chunk(self, elements: list[DocumentElement]) -> list[Chunk]:
        """把元素列表切成 Chunk。"""
        if not elements:
            return []

        try:
            # Step 1: 保护元素（table/code）单独切，其他走语义流程
            protected_chunks: list[Chunk] = []
            semantic_elements: list[DocumentElement] = []

            for el in elements:
                if el.type in _PROTECTED_TYPES:
                    # 保护元素直接作为一个 Chunk（table/code 内部不再切）
                    protected_chunks.append(
                        Chunk(
                            content=el.content,
                            tokens=self._estimate_tokens(el.content),
                            metadata=dict(el.metadata),
                        )
                    )
                else:
                    semantic_elements.append(el)

            # Step 2: 按标题边界切分章节
            sections = self._split_by_heading(semantic_elements)

            # Step 3: 章节内合并段落 + 滑窗
            raw_chunks: list[Chunk] = []
            for heading_stack, section_els in sections:
                chunks = self._chunk_section(heading_stack, section_els)
                raw_chunks.extend(chunks)

            # Step 4: 合并过短块
            merged = self._merge_short(raw_chunks)

            # Step 5: 插入保护块（按 chunk_index 排序由 pipeline 负责）
            return protected_chunks + merged

        except ChunkerError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ChunkerError(f"语义分块失败: {e}") from e

    # --- 章节切分 ---
    def _split_by_heading(
        self, elements: list[DocumentElement]
    ) -> list[tuple[list[str], list[DocumentElement]]]:
        """按 heading 切分章节。

        Returns:
            `[(heading_stack, section_elements), ...]`
            heading_stack 是从文档顶部到当前章节的标题路径（H1→H2→H3）。
        """
        sections: list[tuple[list[str], list[DocumentElement]]] = []
        # 初始空标题栈
        current_stack: list[str] = []
        current_els: list[DocumentElement] = []
        # 各级别当前标题文本
        level_titles: dict[int, str] = {}

        def flush() -> None:
            if current_els or current_stack:
                sections.append((list(current_stack), list(current_els)))
            current_els.clear()

        for el in elements:
            if el.type is ElementType.HEADING:
                # 先把上一节封存
                flush()
                level = int(el.metadata.get("level", 1))
                # 更新标题栈：清除 ≥ 当前级别的标题
                level_titles = {k: v for k, v in level_titles.items() if k < level}
                level_titles[level] = el.content
                # 重建栈：按级别排序
                current_stack = [level_titles[k] for k in sorted(level_titles)]
            else:
                current_els.append(el)

        flush()
        return sections

    # --- 章节内分块 ---
    def _chunk_section(
        self, heading_stack: list[str], elements: list[DocumentElement]
    ) -> list[Chunk]:
        """章节内分块：合并段落 + 超长滑窗。"""
        if not elements:
            return []

        heading_text = heading_stack[-1] if heading_stack else ""
        heading_level = 0
        # 从 heading_stack 无法反推 level，用 metadata 中首个 heading 的 level
        # 此处 heading_level 仅用于 metadata，不影响逻辑
        if heading_stack:
            heading_level = len(heading_stack)  # 近似

        base_meta = {
            "heading": heading_text,
            "heading_path": " > ".join(heading_stack) if heading_stack else "",
            "level": heading_level,
        }

        # 合并所有段落文本（保留段落分隔）
        chunk_texts: list[str] = []
        current_parts: list[str] = []
        current_tokens = 0
        max_chars = self.settings.chunk_max_chars
        max_tokens = self.settings.chunk_max_tokens

        for el in elements:
            text = el.content
            est_tokens = self._estimate_tokens(text)

            # 当前累积超出阈值 → 切一块
            if current_parts and (
                sum(len(p) for p in current_parts) + len(text) > max_chars
                or current_tokens + est_tokens > max_tokens
            ):
                chunk_texts.append("\n\n".join(current_parts))
                current_parts = []
                current_tokens = 0

            current_parts.append(text)
            current_tokens += est_tokens

        if current_parts:
            chunk_texts.append("\n\n".join(current_parts))

        # 超长文本走滑窗
        final_texts: list[str] = []
        for t in chunk_texts:
            if len(t) <= max_chars:
                final_texts.append(t)
            else:
                final_texts.extend(self._sliding_window(t))

        # 生成 Chunk
        chunks: list[Chunk] = []
        for text in final_texts:
            if not text.strip():
                continue
            meta = {**base_meta, "type": "paragraph"}
            chunks.append(
                Chunk(
                    content=text,
                    tokens=self._estimate_tokens(text),
                    metadata=meta,
                )
            )
        return chunks

    # --- 滑窗 ---
    def _sliding_window(self, text: str) -> list[str]:
        """超长文本递归滑窗。overlap = chunk_overlap_chars。"""
        max_chars = self.settings.chunk_max_chars
        overlap = self.settings.chunk_overlap_chars
        step = max(1, max_chars - overlap)

        # 优先在句号 / 换行处切，避免切断句子
        pieces: list[str] = []
        start = 0
        n = len(text)
        while start < n:
            end = min(start + max_chars, n)
            # 尝试在 end 之前找一个换行或句号
            cut = end
            if end < n:
                for sep in ("\n\n", "\n", "。", ". ", "! ", "? "):
                    idx = text.rfind(sep, start, end)
                    if idx > start + step // 2:
                        cut = idx + len(sep)
                        break
            pieces.append(text[start:cut].strip())
            start = cut if cut > start else start + step
        return [p for p in pieces if p]

    # --- 合并过短块 ---
    def _merge_short(self, chunks: list[Chunk]) -> list[Chunk]:
        """过短块并入相邻块。"""
        min_chars = self.settings.chunk_min_chars
        if not chunks:
            return chunks

        result: list[Chunk] = []
        for chunk in chunks:
            if len(chunk.content) < min_chars and result:
                # 并入前一块
                prev = result[-1]
                merged_text = prev.content + "\n\n" + chunk.content
                merged = Chunk(
                    content=merged_text,
                    tokens=prev.tokens + chunk.tokens,
                    metadata=prev.metadata,
                )
                result[-1] = merged
            else:
                result.append(chunk)

        # 二次检查：如果最后一块过短，并入前一块
        if len(result) >= 2 and len(result[-1].content) < min_chars:
            last = result.pop()
            prev = result[-1]
            merged = Chunk(
                content=prev.content + "\n\n" + last.content,
                tokens=prev.tokens + last.tokens,
                metadata=prev.metadata,
            )
            result[-1] = merged

        return result
