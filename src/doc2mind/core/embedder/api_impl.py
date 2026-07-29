"""OpenAI 兼容 API 嵌入实现 — extras `api`。

适用场景：
- 大规模文档需要更高质量的嵌入
- 想用云端模型（text-embedding-3-small 等）
- 不愿在本地装 ONNX runtime

配置（环境变量或 Settings）：
- `DOC2MIND_API_BASE_URL`：API base URL（默认 OpenAI）
- `DOC2MIND_API_KEY`：API key
- `DOC2MIND_API_MODEL`：模型名（默认 text-embedding-3-small）
"""

from __future__ import annotations

import os
from typing import Iterator, Sequence

from doc2mind.core.chunker.base import Chunk
from doc2mind.core.embedder.base import Embedder, EmbedderError


# 默认模型 → 维度
_API_MODEL_DIM: dict[str, int] = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class ApiEmbedder(Embedder):
    """OpenAI 兼容 API 嵌入实现。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str = "text-embedding-3-small",
        batch_size: int = 100,
    ) -> None:
        self._api_key = api_key or os.environ.get("DOC2MIND_API_KEY") or os.environ.get("OPENAI_API_KEY")
        self._base_url = base_url or os.environ.get("DOC2MIND_API_BASE_URL")
        self._model = model
        self._batch_size = min(batch_size, 2048)  # OpenAI 单次上限 2048
        self._client = None

        if not self._api_key:
            raise EmbedderError(
                "API key 未设置。请设置环境变量 DOC2MIND_API_KEY 或 OPENAI_API_KEY"
            )

        # 已知模型预填维度，避免探测消耗 token
        self._dim = _API_MODEL_DIM.get(self._model, 1536)

    # --- 惰性初始化 ---
    def _ensure_client(self) -> None:
        if self._client is not None:
            return
        try:
            from openai import OpenAI
        except ImportError as e:
            raise EmbedderError(
                "openai 未安装。请运行：pip install doc2mind[api]"
            ) from e

        kwargs: dict[str, object] = {"api_key": self._api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        self._client = OpenAI(**kwargs)  # type: ignore[arg-type]

    # --- 属性 ---
    @property
    def dimension(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return self._model

    # --- 嵌入 ---
    def embed(self, chunks: Sequence[Chunk]) -> Iterator:
        self._ensure_client()
        texts = [c.content for c in chunks]
        yield from self._embed_batched(texts)

    def embed_query(self, text: str) -> "object":
        self._ensure_client()
        try:
            resp = self._client.embeddings.create(model=self._model, input=text)
            return resp.data[0].embedding
        except Exception as e:  # noqa: BLE001
            raise EmbedderError(f"API 嵌入失败: {e}") from e

    def embed_texts(self, texts: Sequence[str]) -> Iterator:
        self._ensure_client()
        yield from self._embed_batched(list(texts))

    # --- 内部 ---
    def _embed_batched(self, texts: list[str]) -> Iterator:
        """分批调 API，避免单请求 input 过长。"""
        try:
            for i in range(0, len(texts), self._batch_size):
                batch = texts[i : i + self._batch_size]
                resp = self._client.embeddings.create(
                    model=self._model, input=batch
                )
                # 按 index 排序，确保顺序一致
                for d in sorted(resp.data, key=lambda x: x.index):
                    yield d.embedding
        except Exception as e:  # noqa: BLE001
            raise EmbedderError(f"API 批量嵌入失败: {e}") from e
