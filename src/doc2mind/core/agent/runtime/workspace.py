"""会话工作区沙箱（P1 骨架）。"""

from __future__ import annotations

from pathlib import Path


class PathDeniedError(PermissionError):
    """路径越出工作区或未通过策略校验。"""

    def __init__(self, reason: str, path: str | None = None) -> None:
        self.reason = reason
        self.path = path
        super().__init__(reason)


_ALLOWED_READ_SUFFIXES = {
    ".md",
    ".txt",
    ".csv",
    ".json",
    ".html",
    ".htm",
    ".py",
    ".cs",
    ".ts",
    ".js",
    ".yml",
    ".yaml",
    ".log",
}

_ALLOWED_WRITE_SUFFIXES = {
    ".md",
    ".txt",
    ".csv",
    ".json",
    ".html",
    ".log",
}

_MAX_WRITE_BYTES = 2 * 1024 * 1024


class Workspace:
    """把所有文件操作限制在 chat 级 workspace_root 内。"""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "inputs").mkdir(exist_ok=True)
        (self.root / "drafts").mkdir(exist_ok=True)
        (self.root / "artifacts").mkdir(exist_ok=True)
        (self.root / "notes").mkdir(exist_ok=True)

    def resolve(self, relative_or_abs: str | Path) -> Path:
        """解析用户/模型给出的路径，必须落在 root 内。"""
        raw = str(relative_or_abs or "").strip()
        if not raw:
            raise PathDeniedError("empty path")
        p = Path(raw)
        if not p.is_absolute():
            p = self.root / p
        try:
            resolved = p.resolve()
        except Exception as exc:  # noqa: BLE001
            raise PathDeniedError(f"unresolvable path: {exc}", raw) from exc
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise PathDeniedError("path escapes workspace root", raw) from exc
        return resolved

    def list_files(self, sub: str = ".") -> list[str]:
        base = self.resolve(sub)
        if not base.exists():
            return []
        if base.is_file():
            return [str(base.relative_to(self.root))]
        out: list[str] = []
        for child in sorted(base.rglob("*")):
            if child.is_file():
                out.append(str(child.relative_to(self.root)))
        return out

    def read_text(self, rel_path: str, *, max_chars: int = 50_000) -> str:
        path = self.resolve(rel_path)
        suffix = path.suffix.lower()
        if suffix and suffix not in _ALLOWED_READ_SUFFIXES:
            raise PathDeniedError(f"read suffix not allowed: {suffix}", rel_path)
        if not path.is_file():
            raise FileNotFoundError(rel_path)
        text = path.read_text(encoding="utf-8", errors="replace")
        return text[:max_chars]

    def write_text(self, rel_path: str, content: str) -> Path:
        path = self.resolve(rel_path)
        suffix = path.suffix.lower()
        if suffix and suffix not in _ALLOWED_WRITE_SUFFIXES:
            raise PathDeniedError(f"write suffix not allowed: {suffix}", rel_path)
        data = (content or "").encode("utf-8")
        if len(data) > _MAX_WRITE_BYTES:
            raise PathDeniedError("content too large for workspace write", rel_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content or "", encoding="utf-8")
        return path
