"""核心配置管理 — 全局默认参数与持久化。

配置来源优先级（高 → 低）：
1. CLI 参数（`doc2mind --config ...`）
2. 环境变量 `DOC2MIND_*`
3. 配置文件 `config.toml`（用户目录）
4. 内置默认值（本文件 `DEFAULTS`）
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


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

    # 本地模型目录（可选）：指向一个含 ONNX 模型文件的目录，优先于 embed_model
    # 使用（fastembed specific_model_path）。留空则用 embed_model 从网络下载。
    embed_model_path: str | None = None

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

    # --- LLM / RAG 对话 ---
    # 大模型提供商：none（不启用）| openai（OpenAI 兼容 API）| ollama（本地 Ollama）
    llm_provider: str = "none"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str = ""
    llm_temperature: float = 0.7
    llm_max_tokens: int = 2048

    # RAG 检索上下文参数
    rag_top_k: int = 5
    rag_min_score: float = 0.0

    # 自定义 RAG 系统提示词（人设/回答风格）；None/空 = 用内置默认提示词。
    # 环境变量 DOC2MIND_RAG_SYSTEM_PROMPT 可覆盖。
    rag_system_prompt: str | None = None

    # 多轮对话历史 token 预算：从最新消息向前保留,直到累计 token 超过此值。
    # 0 = 不按 token 截断(仍受 _MAX_HISTORY=20 条上限保护)。
    # 环境变量 DOC2MIND_RAG_MAX_HISTORY_TOKENS 可覆盖。
    rag_max_history_tokens: int = 4096

    # LLM 调用超时（秒），0 = 使用默认值 120s
    llm_timeout: float = 0.0

    # --- AI 自动整理（curate）---
    # 入库成功后自动打标签/生成摘要；ingest_text 未指定集合时还会自动归类。
    auto_curate_on_ingest: bool = True
    # 语义去重候选阈值（向量相似分 0-1，越高越严格，默认 0.85）
    curate_dedup_score_threshold: float = 0.85
    # 整理时送入 LLM 的文档内容截断上限（字符）
    curate_max_chars: int = 8000
    # 目录摄入超过该文件数时跳过入库自动整理（防一次触发海量 LLM 调用，
    # 此时改用 curate 工具/接口批量整理）
    curate_auto_max_files: int = 20

    # --- 嵌入模型下载 ---
    # HuggingFace 端点/镜像。国内网络直连 HF 常超时，设为
    # `https://hf-mirror.com` 可正常下载模型（fastembed 首次使用约 90MB）。
    # 环境变量 `DOC2MIND_HF_ENDPOINT` 可覆盖；留空时自动使用镜像
    # hf-mirror.com，无需手动配置。
    hf_endpoint: str | None = None

    # --- 嵌入模型缓存目录 ---
    # 默认与知识库同目录：%LOCALAPPDATA%\doc2mind\fastembed_cache
    embed_cache_dir: Path = field(
        default_factory=lambda: _user_data_dir() / "fastembed_cache"
    )

    # --- 文件系统监控（文件变更自动摄入）---
    watch_paths: list[str] = field(default_factory=list)
    watch_debounce_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> Settings:
        """从配置文件 + 环境变量加载配置（覆盖默认值）。

        优先级（高 → 低）：
        1. 环境变量 `DOC2MIND_<UPPER_FIELD>`
        2. 配置文件 `config.toml`（用户目录，`doc2mind config --set-model` 写入）
        3. 内置默认值

        例如：
        - `DOC2MIND_EMBED_MODEL`
        - `DOC2MIND_DB_PATH`
        - `DOC2MIND_SERVER_PORT`
        """
        # 先读 config.toml（低优先级），再用环境变量覆盖（高优先级）
        kwargs: dict[str, object] = dict(load_config_file())
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
                elif f.type is list or "list" in str(f.type):
                    kwargs[f.name] = [x.strip() for x in raw.split(",") if x.strip()]
                else:
                    kwargs[f.name] = raw
            except (ValueError, TypeError):
                continue
        s = cls(**kwargs)  # type: ignore[arg-type]

        # embed_dim 与 embed_model 对齐：catalog 已收录的模型直接查维度，
        # 避免用预设 512 建 vec_chunks 表后与模型实际输出维度不符（切换
        # 嵌入模型后 Dimension mismatch 的根因）。显式配置（config.toml /
        # 环境变量的 embed_dim）优先，此处不覆盖；catalog 未收录的自定义
        # 模型保持预设值，由 reindex 的 probe 重建兜底。
        if "embed_dim" not in kwargs:
            from doc2mind.core.embedder.catalog import get_model_info

            info = get_model_info(s.embed_model)
            if info is not None:
                s.embed_dim = info.dim
        return s

    def ensure_dirs(self) -> None:
        """确保数据目录存在。"""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


# --- config.toml 持久化 ---
# 允许写入 config.toml 的字段（其余字段由环境变量 / 默认值决定）
_PERSIST_FIELDS: tuple[str, ...] = (
    "embed_model",
    "embed_dim",
    "embed_model_path",
    "embed_batch_size",
    "chunk_max_tokens",
    "chunk_min_chars",
    "chunk_overlap_chars",
    "chunk_max_chars",
    "search_top_k",
    "rrf_k",
    "hf_endpoint",
    # LLM / RAG 对话（llm_api_key 除外 — 见 save_settings）
    "llm_provider",
    "llm_base_url",
    "llm_model",
    "llm_temperature",
    "llm_max_tokens",
    "rag_top_k",
    "rag_min_score",
    "rag_system_prompt",
    "rag_max_history_tokens",
    "llm_timeout",
    # AI 自动整理（curate）
    "auto_curate_on_ingest",
    "curate_dedup_score_threshold",
    "curate_max_chars",
    "curate_auto_max_files",
    # 文件监控
    "watch_paths",
    "watch_debounce_seconds",
)

# 敏感字段：不写入 config.toml（API Key 明文落盘有泄漏风险）。
# 运行时 key 由 WPF 前端通过环境变量 / POST /v1/config 注入；
# 手动编辑 config.toml 写入的 llm_api_key 仍可被读取（向后兼容 CLI 用户）。
_SENSITIVE_FIELDS: frozenset[str] = frozenset({"llm_api_key"})


def config_file_path() -> Path:
    """用户配置文件路径：Windows %APPDATA%\\doc2mind\\config.toml。"""
    return _user_config_dir() / "config.toml"


def server_port_file_path() -> Path:
    """后端实际监听端口状态文件：Windows %LOCALAPPDATA%\\doc2mind\\server.port。

    `doc2mind serve` 在端口被占用自动 +1 探测后写入，供 WPF 客户端
    读取以跟随实际端口（默认 8765 被占时后端会在 8766/8767… 上服务）。
    """
    return _user_data_dir() / "server.port"


def load_config_file() -> dict[str, object]:
    """读取 config.toml（若存在），返回字段字典；缺失/损坏时返回空 dict。

    支持顶层 `[doc2mind]` 小节（推荐），也兼容平铺键值。

    损坏（语法错误/读失败）时记录告警并可通过 `get_config_load_error()`
    获取原因，供启动界面 / `/v1/config` 提示用户，而不是静默丢弃全部自定义配置。
    """
    global _config_load_error
    path = config_file_path()
    if not path.is_file():
        return {}
    try:
        try:
            import tomllib  # Python 3.11+
        except ImportError:  # pragma: no cover — Python 3.10 回退
            import tomli as tomllib  # type: ignore[no-redef]

        with open(path, "rb") as f:
            data = tomllib.load(f)
        root = data.get("doc2mind", data) if isinstance(data, dict) else {}
        # 读取集合 = 持久化字段 + 敏感字段（手写 toml 的 llm_api_key 仍生效，
        # 只是 save_settings 不会把它写回去）
        readable = set(_PERSIST_FIELDS) | _SENSITIVE_FIELDS
        _config_load_error = None
        return {k: v for k, v in root.items() if k in readable}
    except Exception as e:  # noqa: BLE001 — 配置损坏时回退默认值
        _config_load_error = f"config.toml 解析失败（{path}）：{e}，已临时回退默认配置"
        logger.warning("%s；请修复或删除该文件后重启", _config_load_error)
        return {}


# 最近一次 load_config_file 的失败原因（None = 正常）。config 在进程启动时
# 加载一次，这里缓存错误供 /v1/config 等查询；save_settings 成功写入后清除。
_config_load_error: str | None = None


def get_config_load_error() -> str | None:
    """返回启动时 config.toml 的解析错误（无则 None）。"""
    return _config_load_error


def _toml_repr(value: object) -> str:
    """把 Python 值渲染为 TOML 字面量。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return json.dumps(list(value), ensure_ascii=False)
    return json.dumps(str(value), ensure_ascii=False)


def save_settings(settings: Settings) -> bool:
    """把当前配置持久化到 config.toml（下次启动自动生效）。

    Returns:
        True = 写入成功；False = 失败（目录不可创建/磁盘满/权限不足，
        已记录 error 日志，调用方应向用户提示"重启后配置可能回退"）。
    """
    global _config_load_error
    path = config_file_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error("创建配置目录失败（%s）：%s，配置未持久化", path.parent, e)
        return False
    lines = [
        "# DocMind 配置文件（`doc2mind config` 命令写入）",
        "# 可用 `doc2mind models` 查看可选嵌入模型",
        "",
        "[doc2mind]",
    ]
    for name in _PERSIST_FIELDS:
        value = getattr(settings, name, None)
        if value is None:
            continue
        lines.append(f"{name} = {_toml_repr(value)}")
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as e:
        logger.error("写入配置文件失败（%s）：%s，配置未持久化", path, e)
        return False
    # 成功写入后，此前启动时的解析错误已不复存在
    _config_load_error = None
    return True


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
