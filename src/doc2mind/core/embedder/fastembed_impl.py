"""fastembed 嵌入实现 — ONNX 本地嵌入（core，默认）。

模型：`BAAI/bge-small-zh-v1.5`（~35MB，首次自动下载到用户缓存目录）
特性：
- 纯 CPU 推理，无需 PyTorch / CUDA
- 内置 tokenizer（tokenizers 库）
- 自动归一化（bge 系列需 `query:` / `passage:` 前缀，但 small-zh-v1.5 不强制）

性能基准（i5-1240P）：
- bge-small-zh-v1.5：~200 texts/s，512 维
- bge-base-zh-v1.5：~80 texts/s，768 维
"""

from __future__ import annotations

from typing import Iterator, Sequence

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.config import Settings
from doc2mind.core.embedder.base import Embedder, EmbedderError


# 已知模型 → 维度映射（避免每次都构造 embedder 探测）
_MODEL_DIM: dict[str, int] = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-small-zh-v1.5": 512,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-base-zh-v1.5": 768,
    "BAAI/bge-large-en-v1.5": 1024,
    "BAAI/bge-large-zh-v1.5": 1024,
    "sentence-transformers/all-MiniLM-L6-v2": 384,
    "nomic-ai/nomic-embed-text-v1": 768,
}


class FastEmbedEmbedder(Embedder):
    """fastembed ONNX 嵌入实现。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._impl = None  # 惰性初始化
        self._model = settings.embed_model
        self._dim = settings.embed_dim

    # --- 惰性加载 ---
    def _ensure_loaded(self) -> None:
        """首次调用时加载模型（避免导入时下模型）。"""
        if self._impl is not None:
            return

        try:
            from fastembed import TextEmbedding
        except ImportError as e:
            raise EmbedderError(
                "fastembed 未安装。请运行：pip install fastembed"
            ) from e

        try:
            self._impl = TextEmbedding(
                model_name=self._model,
                max_length=512,
                cache_dir=None,  # 用 fastembed 默认 ~/.cache/fastembed
            )
            # 探测真实维度
            probe = next(self._impl.embed(["probe"]))
            self._dim = int(probe.shape[0])
            _MODEL_DIM[self._model] = self._dim
        except Exception as e:  # noqa: BLE001
            raise EmbedderError(f"加载 fastembed 模型失败 ({self._model}): {e}") from e

    # --- 公开属性 ---
    @property
    def dimension(self) -> int:
        """向量维度。"""
        return self._dim

    @property
    def model_name(self) -> str:
        """模型名。"""
        return self._model

    # --- 嵌入方法 ---
    def embed(self, chunks: Sequence[Chunk]) -> Iterator:
        """批量嵌入，逐批 yield 向量。"""
        self._ensure_loaded()
        texts = [c.content for c in chunks]
        try:
            yield from self._impl.embed(texts, batch_size=self.settings.embed_batch_size)
        except Exception as e:  # noqa: BLE001
            raise EmbedderError(f"嵌入失败: {e}") from e

    def embed_query(self, text: str) -> "object":
        """嵌入单条查询。"""
        self._ensure_loaded()
        try:
            return next(self._impl.query_embed([text]))
        except Exception as e:  # noqa: BLE001
            raise EmbedderError(f"查询嵌入失败: {e}") from e

    def embed_texts(self, texts: Sequence[str]) -> Iterator:
        """嵌入纯文本列表（重建索引用）。"""
        self._ensure_loaded()
        try:
            yield from self._impl.embed(list(texts), batch_size=self.settings.embed_batch_size)
        except Exception as e:  # noqa: BLE001
            raise EmbedderError(f"嵌入失败: {e}") from e


def get_model_dim(model_name: str) -> int | None:
    """查询已知模型的维度。"""
    return _MODEL_DIM.get(model_name)
