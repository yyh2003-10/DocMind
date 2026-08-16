"""嵌入引擎工厂 — 按配置返回 embedder 实例。"""

from __future__ import annotations

from doc2mind.core.config import Settings
from doc2mind.core.embedder.base import Embedder


def get_embedder(settings: Settings | None = None) -> Embedder:
    """按配置返回 embedder 实例。

    决策逻辑：
    1. 若 `settings.embed_model` 以 `http://` / `https://` 开头 → ApiEmbedder
    2. 若环境变量 `DOC2MIND_API_KEY` 已设置 → ApiEmbedder
    3. 否则 → FastEmbedEmbedder（默认）

    Args:
        settings: 配置，默认 `get_settings()`

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

        return ApiEmbedder(
            api_key=os.environ.get("DOC2MIND_API_KEY") or os.environ.get("OPENAI_API_KEY"),
            base_url=os.environ.get("DOC2MIND_API_BASE_URL"),
            model=os.environ.get("DOC2MIND_API_MODEL", "text-embedding-3-small"),
            batch_size=settings.embed_batch_size,
        )

    from doc2mind.core.embedder.fastembed_impl import FastEmbedEmbedder

    return FastEmbedEmbedder(settings)
