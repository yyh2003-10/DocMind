"""核心配置管理 — 全局默认参数与持久化。

配置来源优先级（高 → 低）：
1. CLI 参数（`doc2mind --config ...`）
2. 环境变量 `DOC2MIND_*`
3. 配置文件 `config.toml`（用户目录）
4. 内置默认值（本文件 `DEFAULTS`）
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


# --- 平台相关目录 ---
def _user_config_dir() -> Path:
    """跨平台用户配置目录。

    Windows: %APPDATA%\\doc2mind
    macOS:   ~/Library/Application Support/doc2mind
    Linux:   ~/.config/doc2mind
    """
    if os.name == "nt":  # Windows
        base = os.environ.get("APPDATA", str(Path.home()))
        return Path(base) / "doc2mind"
    if os.name == "posix":
        xdg = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        return Path(xdg) / "doc2mind"
    return Path.home() / ".doc2mind"


def _user_data_dir() -> Path:
    """跨平台用户数据目录（向量库、嵌入缓存）。

    Windows: %LOCALAPPDATA%\\doc2mind
    macOS:   ~/Library/Application Support/doc2mind
    Linux:   ~/.local/share/doc2mind
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", str(Path.home()))
        return Path(base) / "doc2mind"
    if os.name == "posix":
        xdg = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
        return Path(xdg) / "doc2mind"
    return Path.home() / ".doc2mind"


@dataclass
class Settings:
    """运行时配置。

    所有字段都有默认值，构造后通常通过 `Settings.from_env()` 加载用户配置。
    """

    # --- 嵌入引擎 ---
    embed_model: str = "BAAI/bge-small-zh-v1.5"
    embed_dim: int = 512  # bge-small-zh-v1.5 输出维度
    embed_batch_size: int = 32

    # --- 分块 ---
    chunk_max_tokens: int = 1500
    chunk_min_chars: int = 50
    chunk_overlap_chars: int = 200
    chunk_max_chars: int = 4000  # 1500 token × ~2.5 字符/token

    # --- 检索 ---
    search_top_k: int = 10
    rrf_k: int = 60  # Reciprocal Rank Fusion 常数

    # --- 存储 ---
    db_path: Path = field(default_factory=lambda: _user_data_dir() / "doc2mind.db")
    collection_default: str = "default"

    # --- 字符 ↔ token 估算 ---
    # 中文 ~1 token ≈ 2-3 字符，英文 ~1 token ≈ 4 字符
    # 用 tiktoken 精确计数；fallback 用 chars_per_token 估算
    chars_per_token: float = 2.5

    # --- 服务 ---
    server_host: str = "127.0.0.1"
    server_port: int = 8765

    @classmethod
    def from_env(cls) -> Settings:
        """从环境变量加载配置（覆盖默认值）。

        环境变量命名：`DOC2MIND_<UPPER_FIELD>`，例如：
        - `DOC2MIND_EMBED_MODEL`
        - `DOC2MIND_DB_PATH`
        - `DOC2MIND_SERVER_PORT`
        """
        kwargs: dict[str, object] = {}
        for f in cls.__dataclass_fields__.values():
            env_key = f"DOC2MIND_{f.name.upper()}"
            if (raw := os.environ.get(env_key)) is None:
                continue
            try:
                if f.type is int or f.type == "int":
                    kwargs[f.name] = int(raw)
                elif f.type is float or f.type == "float":
                    kwargs[f.name] = float(raw)
                elif f.type is bool or f.type == "bool":
                    kwargs[f.name] = raw.lower() in ("1", "true", "yes", "on")
                elif f.type is Path or f.type == "Path":
                    kwargs[f.name] = Path(raw).expanduser().resolve()
                else:
                    kwargs[f.name] = raw
            except (ValueError, TypeError):
                continue
        return cls(**kwargs)  # type: ignore[arg-type]

    def ensure_dirs(self) -> None:
        """确保数据目录存在。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


# --- 全局单例（惰性）---
_settings: Settings | None = None


def get_settings() -> Settings:
    """获取全局配置单例。"""
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def set_settings(s: Settings) -> None:
    """注入配置（测试用）。"""
    global _settings
    _settings = s
