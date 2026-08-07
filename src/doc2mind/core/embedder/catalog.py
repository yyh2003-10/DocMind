"""嵌入模型清单 — 供 CLI / HTTP / WPF 展示"可选模型"并引导用户选择。

原则：
- 只收录当前 fastembed 版本真实支持（`TextEmbedding.list_supported_models()`
  验证过）的文本嵌入模型，避免用户在设置页选到无法加载的模型。
- 每个条目给出中文场景说明与维度，切换模型后若维度变化需要重建索引。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmbedModelInfo:
    """一个可选的嵌入模型。"""

    name: str          # 模型名（传给 fastembed / DOC2MIND_EMBED_MODEL）
    dim: int           # 输出向量维度
    lang: str          # 语言：zh / en / multilingual
    desc: str          # 中文适用场景说明
    size_gb: float     # 模型大小（GB）
    recommended: bool = False  # 是否推荐（默认选项）


# 按 fastembed 0.8 真实支持筛选后的清单（bge-base-zh-v1.5 / bge-large-zh-v1.5
# 不在 fastembed 0.8 支持列表里，故不收录，避免选了加载失败）
EMBED_MODEL_CATALOG: tuple[EmbedModelInfo, ...] = (
    EmbedModelInfo(
        name="BAAI/bge-small-zh-v1.5",
        dim=512,
        lang="zh",
        desc="中文小模型，最快最省资源，日常中文知识库推荐（默认）",
        size_gb=0.09,
        recommended=True,
    ),
    EmbedModelInfo(
        name="BAAI/bge-small-en-v1.5",
        dim=384,
        lang="en",
        desc="英文小模型，快、省资源，英文文档/代码注释场景",
        size_gb=0.07,
    ),
    EmbedModelInfo(
        name="BAAI/bge-base-en-v1.5",
        dim=768,
        lang="en",
        desc="英文中模型，效果与速度均衡",
        size_gb=0.21,
    ),
    EmbedModelInfo(
        name="BAAI/bge-large-en-v1.5",
        dim=1024,
        lang="en",
        desc="英文大模型，效果最好但最慢、占用内存最多",
        size_gb=1.20,
    ),
    EmbedModelInfo(
        name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        dim=384,
        lang="multilingual",
        desc="多语言小模型，中英日韩等多语言混合检索",
        size_gb=0.22,
    ),
    EmbedModelInfo(
        name="jinaai/jina-embeddings-v2-base-zh",
        dim=768,
        lang="multilingual",
        desc="中英混合模型，支持超长文本（8192 token），中文长文档效果好",
        size_gb=0.64,
    ),
    EmbedModelInfo(
        name="intfloat/multilingual-e5-large",
        dim=1024,
        lang="multilingual",
        desc="多语言大模型，效果最强，需按 E5 格式加 query:/passage: 前缀",
        size_gb=2.24,
    ),
)


def get_model_info(name: str) -> EmbedModelInfo | None:
    """按模型名查清单；不在清单内返回 None（用户自定义模型也允许）。"""
    for info in EMBED_MODEL_CATALOG:
        if info.name == name:
            return info
    return None


def default_model() -> str:
    """返回推荐默认模型名。"""
    for info in EMBED_MODEL_CATALOG:
        if info.recommended:
            return info.name
    return EMBED_MODEL_CATALOG[0].name


# --- 本地模型登记 ---
_LOCAL_ONNX_NAMES = ("model.onnx", "model_optimized.onnx")
_TOKENIZER_NAMES = ("tokenizer.json", "tokenizer_config.json")


def validate_local_model_dir(path) -> tuple[bool, str]:
    """检查一个目录是否可作为本地嵌入模型（fastembed 约定）。

    fastembed 的 `specific_model_path` 直接使用该目录加载：
    - 需要 ONNX 模型文件（model.onnx 或 model_optimized.onnx）
    - 需要 tokenizer 文件（tokenizer.json 或 tokenizer_config.json）

    Returns:
        (ok, message)：ok=False 时 message 说明缺什么。
    """
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return False, f"路径不存在: {path}"
    if not p.is_dir():
        return False, f"不是目录: {path}"
    onnx = [f for f in _LOCAL_ONNX_NAMES if (p / f).is_file()]
    if not onnx:
        return False, (
            f"目录里没找到 ONNX 模型文件（需要 {_LOCAL_ONNX_NAMES} 之一）"
        )
    tok = [f for f in _TOKENIZER_NAMES if (p / f).is_file()]
    if not tok:
        return False, (
            f"目录里没找到 tokenizer 文件（需要 {_TOKENIZER_NAMES} 之一），"
            "fastembed 无法分词"
        )
    size_gb = sum(f.stat().st_size for f in p.iterdir() if f.is_file()) / (1024**3)
    return True, (
        f"本地模型就绪：{onnx[0]} + {tok[0]}，共 {size_gb:.2f}G"
    )


# --- 推荐模型下载引导 ---
def download_recommended_model(
    model_name: str | None = None,
    settings=None,
) -> str:
    """把推荐模型下载到本地缓存（首次会联网，之后直接用缓存）。

    Args:
        model_name: 模型名；None 用默认推荐模型。
        settings: 配置；None 用全局配置。

    Returns:
        结果说明（含维度）。

    Raises:
        EmbedderError: 下载/加载失败（含网络引导信息）。
    """
    from doc2mind.core.config import get_settings
    from doc2mind.core.embedder.base import EmbedderError
    from doc2mind.core.embedder.fastembed_impl import (
        FastEmbedEmbedder,
        _download_error_message,
        is_model_cached,
    )

    if settings is None:
        settings = get_settings()
    name = model_name or default_model()
    info = get_model_info(name)
    if info is None:
        return f"模型 {name} 不在推荐清单中；可直接用 fastembed 支持的其他模型名。"
    if is_model_cached() and settings.embed_model == name:
        return f"模型 {name} 已在本地缓存，无需重复下载。"

    # 临时切换模型后触发加载（下载 + probe）
    from dataclasses import replace

    tmp = replace(settings, embed_model=name, embed_model_path=None)
    embedder = FastEmbedEmbedder(tmp)
    try:
        vec = next(embedder.embed_texts(["模型下载引导 probe"]))
        dim = int(vec.shape[0])
    except EmbedderError as e:
        return _download_error_message(name, e)
    except Exception as e:  # noqa: BLE001
        return f"模型下载/加载失败：{e}"
    return (
        f"模型 {name} 已就绪（{dim} 维）。"
        "如要切换使用，请运行 `doc2mind config --set-model <模型名>`。"
    )


def render_catalog_table() -> str:
    """渲染成 CLI 表格文本（便于 `doc2mind models` 展示）。"""
    lines = ["可选嵌入模型（切换模型后需要重建索引才能生效）："]
    lines.append(f"{'模型':<52} {'维度':>6} {'语言':<12} {'大小':>8} 说明")
    lines.append("-" * 110)
    for info in EMBED_MODEL_CATALOG:
        mark = "（推荐）" if info.recommended else ""
        lang_map = {"zh": "中文", "en": "英文", "multilingual": "多语言"}
        lines.append(
            f"{info.name:<52} {info.dim:>6} {lang_map.get(info.lang, info.lang):<12} "
            f"{info.size_gb:>5.2f}G  {info.desc}{mark}"
        )
    lines.append(
        "\n提示：也可以在设置页下拉选择，或设置环境变量 "
        "DOC2MIND_EMBED_MODEL=<模型名>。\n"
        "切换模型后请用 `doc2mind reindex --collection default --model <模型名>` 重建索引。"
    )
    return "\n".join(lines)
