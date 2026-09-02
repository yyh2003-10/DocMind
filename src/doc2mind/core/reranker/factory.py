"""重排器工厂 — 按配置返回（带进程内缓存的）reranker 实例。

缓存说明：
    重排模型加载较重（约 1.3GB ONNX），必须跨多次检索复用同一实例。
    这里按模型名缓存已构造的 reranker 实例；模型真正加载仍是惰性的
    （首次 rerank() 才触发），缓存只避免重复构造与重复探测。
    配置热更新（rerank_enabled / rerank_model 变更）需重启后端生效，
    与嵌入器 state.embedder 一致。
"""

from __future__ import annotations

from doc2mind.core.config import Settings
from doc2mind.core.reranker.base import Reranker

# 按模型名缓存已构造实例（模型加载是惰性、进程内复用）
_reranker_cache: dict[str, Reranker] = {}


def get_reranker(settings: Settings | None = None) -> Reranker | None:
    """按配置返回重排器实例；未启用或不可用返回 None。

    Args:
        settings: 配置；None 用全局 `get_settings()`

    Returns:
        `Reranker` 实例，或 `None`（未启用重排）

    注意：构造失败（如 fastembed 未装）不会抛异常，仅返回 None；
    真正的模型加载/推理错误在 rerank() 调用时以 RerankerError 暴露，
    由 Retriever 捕获并降级。
    """
    if settings is None:
        from doc2mind.core.config import get_settings

        settings = get_settings()

    if not settings.rerank_enabled:
        return None

    key = settings.rerank_model
    cached = _reranker_cache.get(key)
    if cached is not None:
        return cached

    try:
        from doc2mind.core.reranker.fastembed_impl import FastEmbedReranker

        reranker: Reranker = FastEmbedReranker(
            model_name=settings.rerank_model,
            cache_dir=settings.embed_cache_dir,
            hf_endpoint=settings.hf_endpoint,
        )
        _reranker_cache[key] = reranker
        return reranker
    except Exception as e:  # noqa: BLE001 — 构造期异常（极少见）也降级
        from doc2mind.core.reranker.base import RerankerError

        logger = __import__("logging").getLogger("doc2mind.reranker")
        logger.warning("重排器构造失败，已禁用重排: %s", e)
        # 避免反复构造失败：缓存一个 None 哨兵无意义，交给下次重试
        return None
