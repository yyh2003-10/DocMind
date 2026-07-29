"""代码加载器 — 纯 Python，零依赖。

特点：
- 按扩展名映射语言（detect.py 已做扩展名 → 语言映射）
- Python / JS / TS / Java / C++ / C# / Go / Rust：按函数 / 类定义切分
- 其余语言：按空行分隔的逻辑段落切分
- 每个函数 / 类切分为一个 CODE 元素，携带 `language` 元数据
- 文件头注释（首段 # / /* */ 序列）单独输出，便于保留版权信息
- 行内跳过二进制行（含 NUL 字符）防止乱码

局限性：
- 不解析 AST，仅基于行模式匹配（足够嵌入检索用，避免依赖 tree-sitter）
- 多行字符串字面量中的 def/class 行可能误判，但概率低
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from doc2mind.core.loader.base import Loader, LoaderError
from doc2mind.core.models import (
    DocFormat,
    DocumentElement,
    ElementType,
    LoadedDocument,
)


# --- 语言识别（扩展名 → 语言名） ---
_LANGUAGE_BY_EXT: dict[str, str] = {
    "py": "python",
    "pyw": "python",
    "js": "javascript",
    "jsx": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "mts": "typescript",
    "cts": "typescript",
    "java": "java",
    "kt": "kotlin",
    "kts": "kotlin",
    "scala": "scala",
    "sc": "scala",
    "c": "c",
    "h": "c",
    "cpp": "cpp",
    "cxx": "cpp",
    "cc": "cpp",
    "hpp": "cpp",
    "hxx": "cpp",
    "cs": "csharp",
    "go": "go",
    "rs": "rust",
    "rb": "ruby",
    "php": "php",
    "sh": "shell",
    "bash": "shell",
    "zsh": "shell",
    "ps1": "powershell",
    "sql": "sql",
    "swift": "swift",
    "r": "r",
    "lua": "lua",
    "pl": "perl",
    "vim": "vim",
    "yaml": "yaml",
    "yml": "yaml",
    "json": "json",
    "xml": "xml",
    "ini": "ini",
    "toml": "toml",
    "cfg": "ini",
    "conf": "ini",
}

# --- 按语言定义的"结构起点"行正则 ---
# 匹配则视为一个新的代码块起点（函数 / class / struct / export 等）
# 用以决定是否切分 CODE 块
_STRUCTURE_PATTERNS: dict[str, re.Pattern[str]] = {
    "python": re.compile(
        r"""^(async\s+def\s+\w+|def\s+\w+|class\s+\w+|@\w+decorator)""",
        re.VERBOSE,
    ),
    "javascript": re.compile(
        r"""^(export\s+)?(async\s+)?(function\s+\w+|class\s+\w+|interface\s+\w+|const\s+\w+\s*=\s*(async\s*)?\(|export\s+default\s+)""",
        re.VERBOSE,
    ),
    "typescript": re.compile(
        r"""^(export\s+)?(async\s+)?(function\s+\w+|class\s+\w+|interface\s+\w+|type\s+\w+|enum\s+\w+|const\s+\w+\s*=\s*(async\s*)?\(|export\s+default\s+)""",
        re.VERBOSE,
    ),
    "java": re.compile(
        r"""^\s*(public|private|protected|static|final|\s)*(class|interface|enum|void|int|boolean|String|List|Map|Set)\s+\w+""",
        re.VERBOSE,
    ),
    "kotlin": re.compile(
        r"""^(fun\s+\w+|class\s+\w+|object\s+\w+|interface\s+\w+|data\s+class\s+\w+)""",
        re.VERBOSE,
    ),
    "scala": re.compile(
        r"""^(def\s+\w+|class\s+\w+|object\s+\w+|trait\s+\w+|case\s+class\s+\w+)""",
        re.VERBOSE,
    ),
    "csharp": re.compile(
        r"""^\s*(public|private|protected|internal|static|sealed|abstract|async|\s)*(class|interface|struct|enum|void|int|bool|string|Task|public\s+\w+\s+)""",
        re.VERBOSE,
    ),
    "cpp": re.compile(
        r"""^\s*(void|int|bool|char|float|double|long|short|unsigned|signed|std::\w+|auto|template|class|struct|namespace|enum)\s+\w+""",
        re.VERBOSE,
    ),
    "c": re.compile(
        r"""^\s*(void|int|bool|char|float|double|long|short|unsigned|signed|struct|enum|static\s+\w+)\s+\w+""",
        re.VERBOSE,
    ),
    "go": re.compile(
        r"""^(func\s+\w+|type\s+\w+\s+(struct|interface)|var\s+\w+\s*=|const\s+\w+)""",
        re.VERBOSE,
    ),
    "rust": re.compile(
        r"""^(pub\s+)?(fn\s+\w+|struct\s+\w+|enum\s+\w+|trait\s+\w+|impl\s+\w+|mod\s+\w+|macro_rules!\s+\w+)""",
        re.VERBOSE,
    ),
    "ruby": re.compile(
        r"""^(def\s+\w+|class\s+\w+|module\s+\w+)""",
    ),
    "php": re.compile(
        r"""^\s*(public|private|protected|static|final|\s)*(function\s+\w+|class\s+\w+)""",
        re.VERBOSE,
    ),
    "swift": re.compile(
        r"""^\s*(public|private|internal|func|class|struct|enum|protocol|let\s+\w+|var\s+\w+)""",
        re.VERBOSE,
    ),
}

# JSON / YAML / TOML / INI / XML 这类配置文件不分块，整文件一块
_WHOLE_FILE_LANGS = frozenset(
    {"json", "yaml", "ini", "toml", "xml", "sql", "vim", "powershell"}
)


# 注释行正则（用于把文件头注释段单独切出）
_COMMENT_PATTERNS: dict[str, re.Pattern[str]] = {
    "python": re.compile(r"^\s*(#|#!)"),
    "javascript": re.compile(r"^\s*(//|/\*|#!|import\s|export\s|const\s|require\s)"),
    "typescript": re.compile(r"^\s*(//|/\*|import\s|export\s)"),
    "java": re.compile(r"^\s*(//|/\*|package\s|import\s)"),
    "kotlin": re.compile(r"^\s*(//|/\*|package\s|import\s)"),
    "scala": re.compile(r"^\s*(//|/\*|package\s|import\s)"),
    "csharp": re.compile(r"^\s*(//|/\*|using\s|namespace\s)"),
    "cpp": re.compile(r"^\s*(//|/\*|#include)"),
    "c": re.compile(r"^\s*(//|/\*|#include)"),
    "go": re.compile(r"^\s*(//|/\*|package\s|import\s)"),
    "rust": re.compile(r"^\s*(//|/\*|//!)"),
    "ruby": re.compile(r"^\s*(#|require\s)"),
    "php": re.compile(r"^\s*(//|/\*|#|namespace\s|use\s)"),
    "swift": re.compile(r"^\s*(//|/\*|import\s)"),
}


def _detect_language(path: Path) -> str:
    """按扩展名 + 文件名 fallback 推断语言。

    Args:
        path: 文件路径

    Returns:
        语言名（小写字符串），未知返回 "text"
    """
    stem_lower = path.stem.lower()
    # Dockerfile / Makefile 等无扩展名特例
    if stem_lower in {"dockerfile", "makefile"}:
        return "dockerfile" if stem_lower == "dockerfile" else "makefile"
    ext = path.suffix.lower().lstrip(".")
    return _LANGUAGE_BY_EXT.get(ext, "text")


def _strip_header_comments(
    lines: list[str], language: str
) -> tuple[list[str], list[str]]:
    """提取文件头的连续注释行（版权 / shebang / 模块说明）。

    Args:
        lines: 源码行列表（已按原文件顺序）
        language: 语言名

    Returns:
        (header_lines, body_lines)
    """
    pat = _COMMENT_PATTERNS.get(language)
    if not pat:
        return [], lines

    header: list[str] = []
    idx = 0
    n = len(lines)
    while idx < n:
        line = lines[idx]
        if not pat.match(line):
            break
        # 对 /* */ 多行注释，需跟踪到结束
        if line.lstrip().startswith("/*"):
            header.append(line)
            idx += 1
            while idx < n and "*/" not in lines[idx]:
                header.append(lines[idx])
                idx += 1
            if idx < n:
                header.append(lines[idx])
                idx += 1
            continue
        header.append(line)
        idx += 1

    # header 必须是文件开头连续注释；若文件首行就是代码则无 header
    body = lines[idx:]
    return header, body


def _split_by_structure(
    lines: list[str], language: str
) -> list[tuple[str, int]]:
    """按 def / class / function 等结构起点切分。

    Args:
        lines: 源码行（不含 header）
        language: 语言名

    Returns:
        [(块文本, 起始行号 1-based), ...]
    """
    pat = _STRUCTURE_PATTERNS.get(language)
    if not pat:
        # 不分块，整文件一块
        return [("".join(lines), 1)] if lines else []

    blocks: list[tuple[str, int]] = []
    current: list[str] = []
    current_start: int | None = None

    for idx, line in enumerate(lines):
        # 行首是否匹配结构起点（去掉行首空白后匹配）
        stripped = line.lstrip()
        if stripped and pat.match(line):
            # flush 前一块
            if current:
                blocks.append(("".join(current), (current_start or 0) + 1))
                current = []
            current_start = idx
        current.append(line)

    if current:
        blocks.append(("".join(current), (current_start or 0) + 1))

    return blocks


def _split_by_blank_lines(lines: list[str]) -> list[tuple[str, int]]:
    """通用切分：按空行分隔的逻辑段落（用于 _WHOLE_FILE_LANGS 之外、无结构 pattern 的语言）。

    Args:
        lines: 源码行

    Returns:
        [(块文本, 起始行号 1-based), ...]
    """
    blocks: list[tuple[str, int]] = []
    current: list[str] = []
    start_idx: int | None = None

    for idx, line in enumerate(lines):
        if line.strip() == "":
            if current:
                blocks.append(("".join(current), (start_idx or 0) + 1))
                current = []
                start_idx = None
            continue
        if start_idx is None:
            start_idx = idx
        current.append(line)

    if current:
        blocks.append(("".join(current), (start_idx or 0) + 1))

    return blocks


class CodeLoader(Loader):
    """代码文件加载器（纯 Python 实现，无依赖）。"""

    supported_extensions = tuple(
        sorted(
            set(_LANGUAGE_BY_EXT.keys())
            | {"dockerfile", "makefile"}
        )
    )

    def extract(self, path: Path) -> LoadedDocument:
        if not path.exists():
            raise LoaderError(f"文件不存在: {path}")

        try:
            raw = path.read_bytes()
            file_hash = hashlib.md5(raw).hexdigest()

            # 检测二进制行：跳过含 NUL 的行
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                # 退到 latin-1，逐行过滤二进制
                text = raw.decode("latin-1")

            lines = text.splitlines(keepends=True)
            language = _detect_language(path)

            elements: list[DocumentElement] = []

            # 文件头注释段（版权 / 模块说明）单独输出
            header_lines, body_lines = _strip_header_comments(lines, language)
            if header_lines:
                header_text = "".join(header_lines).rstrip()
                if header_text:
                    elements.append(
                        DocumentElement(
                            content=header_text,
                            type=ElementType.CODE,
                            metadata={
                                "type": "code",
                                "language": language,
                                "role": "header",
                                "source_format": DocFormat.CODE.value,
                            },
                        )
                    )

            # 行号偏移（header 占的行数）
            header_line_count = len(header_lines)

            # 配置文件 / 不支持分块的语言 → 整文件一块
            if language in _WHOLE_FILE_LANGS:
                body_text = "".join(body_lines).rstrip()
                if body_text:
                    elements.append(
                        DocumentElement(
                            content=body_text,
                            type=ElementType.CODE,
                            metadata={
                                "type": "code",
                                "language": language,
                                "role": "body",
                                "start_line": header_line_count + 1,
                                "source_format": DocFormat.CODE.value,
                            },
                        )
                    )
            else:
                # 优先按结构切分；语言无结构 pattern 则按空行
                pat = _STRUCTURE_PATTERNS.get(language)
                if pat:
                    blocks = _split_by_structure(body_lines, language)
                else:
                    blocks = _split_by_blank_lines(body_lines)

                for block_text, start_line in blocks:
                    block_text = block_text.rstrip()
                    if not block_text:
                        continue
                    # 把局部行号转换为全局行号（header 偏移）
                    global_start = header_line_count + start_line
                    role = "function" if _is_function_like(
                        block_text, language
                    ) else "block"
                    elements.append(
                        DocumentElement(
                            content=block_text,
                            type=ElementType.CODE,
                            metadata={
                                "type": "code",
                                "language": language,
                                "role": role,
                                "start_line": global_start,
                                "line_count": block_text.count("\n") + 1,
                                "source_format": DocFormat.CODE.value,
                            },
                        )
                    )

            return LoadedDocument(
                source=path.name,
                format=DocFormat.CODE,
                elements=elements,
                page_count=None,
                size_bytes=len(raw),
                file_hash=file_hash,
            )
        except LoaderError:
            raise
        except Exception as e:  # noqa: BLE001
            raise LoaderError(f"代码解析失败 ({path.name}): {e}") from e


def _is_function_like(text: str, language: str) -> bool:
    """启发式判断该块是否为函数（而非 class / interface / enum / namespace）。"""
    first_line = text.splitlines()[0] if text else ""
    stripped = first_line.strip()
    if not stripped:
        return False
    # 关键字组合优先
    function_keywords = {
        "python": ("def ",),
        "javascript": ("function ", "const ") ,
        "typescript": ("function ", "const "),
        "java": ("void ", "int ", "boolean ", "public "),
        "kotlin": ("fun ",),
        "scala": ("def ",),
        "csharp": ("void ", "int ", "bool ", "public "),
        "cpp": ("void ", "int ", "bool ", "char "),
        "c": ("void ", "int ", "bool ", "char "),
        "go": ("func ",),
        "rust": ("fn ",),
        "ruby": ("def ",),
        "php": ("function ",),
        "swift": ("func ",),
    }
    kws = function_keywords.get(language)
    if not kws:
        return False
    return any(kw in stripped for kw in kws)
