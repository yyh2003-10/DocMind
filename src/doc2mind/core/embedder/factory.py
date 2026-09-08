"""嵌入引擎工厂 — 按配置返回 embedder 实例（带配置级缓存）。

缓存动机：历史上每次 `get_embedder` 都构造全新实例，一次目录摄入、
file_watcher 单文件事件、reindex 都会各自重新走一遍 provider 探测；
HTTP 服务的 state.embedder 与 pipeline 内部再建的实例还是两个独立对象，
同一进程内同时驻留多份 ONNX 会话（内存翻倍）、模型首次加载也无法共享。
按配置键缓存后，同一配置全进程只有一个实例——HTTP 启动预热、ingest、
检索、reindex 自动共享，`/v1/config` 换模型时键变化自然重建。
"""

from __future__ import annotations

import threading

from doc2mind.core.config import Settings
from doc2mind.core.embedder.base import Embedder

# 配置键 → 实例。键取影响实例身份的全部字段；batch_size 在 embed() 时
# 从 settings 现读，不参与实例身份（命中缓存后重绑最新 settings 即可）。
_EMBEDDER_CACHE: dict[tuple, Embedder] = {}
_CACHE_LOCK = threading.Lock()


def _cache_key(settings: Settings) -> tuple:
    # embed_max_length 是会话创建期参数（token 截断上限），变更必须重建
    # ONNX 会话，因此参与缓存键；embed_batch_size 在 embed() 时现读，不参与。
    return (
        settings.embed_model,
        settings.embed_model_path,
        settings.embed_threads,
        settings.embed_max_length,
        settings.embed_dim,
    )


def get_embedder(
    settings: Settings | None = None,
    model_name: str | None = None,
    dimension: int | None = None,
) -> Embedder:
    """按配置返回 embedder 实例（同配置复用缓存实例）。

    决策逻辑：
    1. 若 `settings.embed_model` 以 `http://` / `https://` 开头 → ApiEmbedder
    2. 若环境变量 `DOC2MIND_API_KEY` 已设置 → ApiEmbedder
    3. 否则 → FastEmbedEmbedder（默认）

    Args:
        settings: 配置，默认 `get_settings()`
        model_name: 覆盖嵌入模型名（reindex 换模型重建时用）；
            非空时绕过缓存直接构造独立实例（临时模型不应污染常驻缓存）
        dimension: 覆盖预期维度（与 model_name 搭配使用）

    Returns:
        `Embedder` 实例

    Raises:
        EmbedderError: 配置缺失或初始化失败
    """
    if settings is None:
        from doc2mind.core.config import get_settings

        settings = get_settings()

    import os

    use_api = (
        settings.embed_model.startswith(("http://", "https://"))
        or bool(os.environ.get("DOC2MIND_API_KEY"))
        or bool(os.environ.get("OPENAI_API_KEY"))
    )

    if use_api:
        from doc2mind.core.embedder.api_impl import ApiEmbedder

        # ApiEmbedder 构造成本低（无模型加载），不缓存
        return ApiEmbedder(
            api_key=os.environ.get("DOC2MIND_API_KEY") or os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("DOC2MIND_API_BASE_URL"),
            model=os.environ.get("DOC2MIND_API_MODEL", "text-embedding-3-small"),
            batch_size=settings.embed_batch_size,
        )

    from doc2mind.core.embedder.fastembed_impl import FastEmbedEmbedder

    if model_name is not None:
        # reindex 等场景的临时模型实例：不进缓存
        scoped = _scoped_settings(settings, model_name, dimension)
        return FastEmbedEmbedder(scoped)

    key = _cache_key(settings)
    with _CACHE_LOCK:
        cached = _EMBEDDER_CACHE.get(key)
        if cached is None:
            cached = FastEmbedEmbedder(settings)
            _EMBEDDER_CACHE[key] = cached
            return cached
    # 命中缓存：重绑最新 settings（batch_size 等在 embed() 时现读，
    # 保证 /v1/config 调整后无需重建会话即生效）
    if hasattr(cached, "settings"):
        cached.settings = settings
    return cached


def _scoped_settings(
    settings: Settings, model_name: str, dimension: int | None
) -> Settings:
    """复制配置并把 embed_model/embed_dim 覆盖为 reindex 目标值。"""
    from dataclasses import replace

    overrides: dict[str, object] = {"embed_model": model_name}
    if dimension is not None:
        overrides["embed_dim"] = dimension
    return replace(settings, **overrides)  # type: ignore[arg-type]
