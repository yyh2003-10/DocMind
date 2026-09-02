"""fastembed 重排实现 — 本地 ONNX cross-encoder（TextRanking）。

默认模型：`Xenova/bge-reranker-v2-m3`（多语言，中英混合检索效果最佳，
首次使用需联网下载约 1.3GB 到缓存目录；下载失败自动降级为不使用重排）。

与嵌入器相同的惰性加载策略：构造时不触碰模型，首次 rerank() 才加载；
加载失败抛 RerankerError，由调用方降级处理。
"""

from __future__ import annotations

import logging
import os

from doc2mind.core.reranker.base import Reranker, RerankerError

logger = logging.getLogger("doc2mind.reranker.fastembed")

# 国内网络直连 HuggingFace 常超时，下载失败后自动用该镜像重试一次
HF_MIRROR = "https://hf-mirror.com"


def _is_download_error(e: Exception) -> bool:
    """粗判异常是否属于"模型下载/网络"类错误（用于触发镜像重试与引导提示）。"""
    msg = str(e).lower()
    markers = (
        "connecttimeout", "connect timeout", "connectionerror",
        "connection timed out", "connection reset", "connection aborted",
        "winerror 10060", "winerror 10061", "winerror 10054",
        "timed out", "timeout", "failed to resolve", "could not resolve",
        "cannot resolve", "socket", "tls", "ssl", "network",
        "remote end closed", "resolve host", "repository not found",
        "404 client error", "client error", "eof",
    )
    return any(m in msg for m in markers)


class FastEmbedReranker(Reranker):
    """fastembed TextRanking 重排实现（本地 ONNX 推理）。"""

    def __init__(self, model_name: str, cache_dir, hf_endpoint: str | None = None) -> None:
        self._model_name = model_name
        self._cache_dir = cache_dir
        self._hf_endpoint = hf_endpoint
        self._impl = None  # 惰性初始化

    # --- 惰性加载 ---
    def _ensure_loaded(self) -> None:
        """首次 rerank 时加载模型（避免导入时下模型）。"""
        if self._impl is not None:
            return

        # 镜像/端点必须在 import fastembed 之前设置（同嵌入器约定）
        if not os.environ.get("HF_ENDPOINT"):
            os.environ["HF_ENDPOINT"] = self._hf_endpoint or HF_MIRROR

        try:
            from fastembed import TextRanking
        except ImportError as e:
            raise RerankerError(
                "fastembed 未安装，无法使用重排模型。请运行：pip install fastembed"
            ) from e

        kwargs: dict[str, object] = {
            "model_name": self._model_name,
            "cache_dir": str(self._cache_dir),
        }
        try:
            self._load_impl(TextRanking, kwargs)
        except Exception as e:  # noqa: BLE001
            # 模型下载/加载失败：再试一次（覆盖瞬时网络抖动）
            if _is_download_error(e):
                logger.warning("重排模型下载/加载失败，重试一次: %s", e)
                try:
                    self._load_impl(TextRanking, kwargs)
                except Exception as e2:  # noqa: BLE001
                    raise RerankerError(
                        f"重排模型 {self._model_name} 下载/加载失败：{e2}"
                    ) from e2
            else:
                raise RerankerError(
                    f"加载重排模型失败 ({self._model_name}): {e}"
                ) from e

    def _load_impl(self, ranking_cls, kwargs: dict[str, object]) -> None:
        """构造 TextRanking 并 probe 验证可用。"""
        self._impl = ranking_cls(**kwargs)
        # 用 probe 实际跑一次推理，验证模型真正可用（OOM / 维度错等在此暴露）
        list(self._impl.rerank("probe", ["文档 A", "文档 B"], batch_size=1))
        logger.info("fastembed 重排模型加载完成: model=%s", self._model_name)

    @property
    def model_name(self) -> str:
        """重排模型名。"""
        return self._model_name

    def rerank(self, query: str, documents: list[str], batch_size: int = 32) -> list[float]:
        """对 (query, 每个 document) 逐对打分。

        返回与 `documents` 索引一一对应的分数列表；fastembed 的
        `rerank` 返回按分数降序的 RankedDocument（含 index），这里还原回原序。
        """
        if not documents:
            return []
        self._ensure_loaded()
        assert self._impl is not None
        try:
            ranked = list(self._impl.rerank(query, documents, batch_size=batch_size))
        except Exception as e:  # noqa: BLE001
            raise RerankerError(f"重排推理失败: {e}") from e

        scores = [0.0] * len(documents)
        for r in ranked:
            idx = int(getattr(r, "index", -1))
            if 0 <= idx < len(documents):
                scores[idx] = float(getattr(r, "score", 0.0))
        return scores
