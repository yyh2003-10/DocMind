"""代码分块器 — 按函数 / 类签名切分，回退到空行分隔。

策略：
1. 仅处理 `type == code` 的元素
2. 按语言识别"定义起点"：
   - Python: `def ` / `class ` / `async def `（行首，去缩进）
   - JS/TS: `function ` / `class ` / `const XXX = ` / `export function`
   - Java/C#: `public ` / `private ` / `protected ` + `class` / `method`
   - Go: `func `
   - Rust: `fn ` / `impl ` / `struct ` / `enum `
3. 切分时保留定义前的注释 / 装饰器
4. 切出的块超过 `chunk_max_chars` 时递归滑窗
5. 不识别的语言 → 按空行分隔的逻辑段落

入口：`CodeChunker.chunk(elements) -> list[Chunk]`
"""

from __future__ import annotations

import re

from doc2mind.core.chunker.base import Chunk, Chunker, ChunkerError
from doc2mind.core.config import Settings
from doc2mind.core.models import DocumentElement, ElementType

# 各语言"定义起点"正则（行首匹配，允许可选缩进）
# 匹配整行，返回是否为"定义起点"
_DEFINITION_PATTERNS: dict[str, re.Pattern[str]] = {
    "python": re.compile(
        r"^\s*(async\s+def\s+|def\s+|class\s+|@)", re.MULTILINE
    ),
    "javascript": re.compile(
        r"^\s*(export\s+)?(async\s+)?(function\s+|class\s+|const\s+\w+\s*=|let\s+\w+\s*=)",
        re.MULTILINE,
    ),
    "typescript": re.compile(
        r"^\s*(export\s+)?(async\s+)?(function\s+|class\s+|interface\s+|const\s+\w+\s*=|type\s+\w+\s*=)",
        re.MULTILINE,
    ),
    "go": re.compile(r"^\s*func\s+", re.MULTILINE),
    "rust": re.compile(
        r"^\s*(fn\s+|impl\s+|struct\s+|enum\s+|trait\s+|mod\s+)", re.MULTILINE
    ),
    "java": re.compile(
        r"^\s*(public|private|protected|static|\s)*\s*(class|void|interface|enum)\s+",
        re.MULTILINE,
    ),
    "csharp": re.compile(
        r"^\s*(public|private|protected|internal|static|\s)*\s*(class|interface|enum|struct|void)\s+",
        re.MULTILINE,
    ),
    "cpp": re.compile(
        r"^\s*(\w+(\s+::\s+\w+)*\s+)?\w+\s*\([^;]*\)\s*\{",
        re.MULTILINE,
    ),
    "c": re.compile(
        r"^\s*(\w+(\s+\*+\s*|\s+))?(\w+)\s*\([^;]*\)\s*\{",
        re.MULTILINE,
    ),
    "ruby": re.compile(r"^\s*(def\s+|class\s+|module\s+)", re.MULTILINE),
    "php": re.compile(
        r"^\s*(public\s+|private\s+|protected\s+|static\s+)*function\s+",
        re.MULTILINE,
    ),
    "kotlin": re.compile(
        r"^\s*(fun\s+|class\s+|object\s+|interface\s+|data\s+class\s+)",
        re.MULTILINE,
    ),
    "swift": re.compile(
        r"^\s*(func\s+|class\s+|struct\s+|enum\s+|protocol\s+)",
        re.MULTILINE,
    ),
    "scala": re.compile(
        r"^\s*(def\s+|class\s+|object\s+|trait\s+|case\s+class\s+)",
        re.MULTILINE,
    ),
}


class CodeChunker(Chunker):
    """代码分块器：按函数 / 类签名切分。"""

    settings: Settings

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def chunk(self, elements: list[DocumentElement]) -> list[Chunk]:
        """提取代码元素，按语言切分。"""
        if not elements:
            return []

        chunks: list[Chunk] = []
        try:
            for el in elements:
                if el.type is not ElementType.CODE:
                    continue
                language = (el.metadata.get("language") or "text").lower()
                chunks.extend(self._chunk_code_block(el.content, language, el.metadata))

            return chunks
        except ChunkerError:
            raise
        except Exception as e:  # noqa: BLE001
            raise ChunkerError(f"代码分块失败: {e}") from e

    def _chunk_code_block(
        self, code: str, language: str, base_meta: dict
    ) -> list[Chunk]:
        """切分单个代码块。

        Args:
            code: 代码文本
            language: 语言小写名
            base_meta: 源元素 metadata

        Returns:
            `Chunk` 列表（按代码顺序）
        """
        max_chars = self.settings.chunk_max_chars

        # 1. 短代码块 → 整块返回
        if len(code) <= max_chars:
            return [
                Chunk(
                    content=code,
                    tokens=_estimate_tokens(code),
                    metadata={**base_meta, "type": "code", "language": language},
                )
            ]

        # 2. 识别语言 → 按定义起点切分
        pattern = _DEFINITION_PATTERNS.get(language)
        if pattern is not None:
            pieces = self._split_by_definitions(code, pattern)
        else:
            # 3. 未识别语言 → 空行分隔
            pieces = re.split(r"\n\s*\n", code)

        # 4. 生成 Chunk，超长走滑窗
        chunks: list[Chunk] = []
        for piece in pieces:
            piece = piece.strip()
            if not piece:
                continue
            if len(piece) <= max_chars:
                chunks.append(self._make_chunk(piece, language, base_meta))
            else:
                for sub in self._sliding_window(piece):
                    chunks.append(self._make_chunk(sub, language, base_meta))

        return chunks

    def _split_by_definitions(self, code: str, pattern: re.Pattern[str]) -> list[str]:
        """按定义起点正则切分代码。

        保留定义前的装饰器 / 注释。
        """
        # 找到所有定义起点的行号
        matches = list(pattern.finditer(code))
        if not matches:
            return [code]

        pieces: list[str] = []
        # 第一个定义之前的内容（imports / 模块文档）作为一块
        first_start = matches[0].start()
        if first_start > 0:
            prefix = code[:first_start].rstrip()
            if prefix:
                pieces.append(prefix)

        # 各定义起点之间的内容作为一块
        for i, m in enumerate(matches):
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(code)
            piece = code[start:end].rstrip()
            if piece:
                pieces.append(piece)

        return pieces

    def _sliding_window(self, text: str) -> list[str]:
        """超长代码块滑窗（按换行切）。"""
        max_chars = self.settings.chunk_max_chars
        overlap = self.settings.chunk_overlap_chars

        pieces: list[str] = []
        lines = text.splitlines()
        current: list[str] = []
        current_len = 0

        for line in lines:
            line_len = len(line) + 1  # +1 for \n
            if current_len + line_len > max_chars and current:
                pieces.append("\n".join(current))
                # overlap：保留尾部若干行
                overlap_len = 0
                keep_from = len(current)
                for j in range(len(current) - 1, -1, -1):
                    overlap_len += len(current[j]) + 1
                    if overlap_len >= overlap:
                        keep_from = j
                        break
                current = current[keep_from:]
                current_len = sum(len(l) + 1 for l in current)
            current.append(line)
            current_len += line_len

        if current:
            pieces.append("\n".join(current))
        return [p for p in pieces if p.strip()]

    def _make_chunk(
        self, content: str, language: str, base_meta: dict
    ) -> Chunk:
        """生成代码 Chunk。"""
        return Chunk(
            content=content,
            tokens=_estimate_tokens(content),
            metadata={**base_meta, "type": "code", "language": language},
        )


# --- 辅助 ---
def _estimate_tokens(text: str) -> int:
    """token 估算（同 semantic.py）。"""
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other = len(text) - cjk
    return cjk + (other + 3) // 4
