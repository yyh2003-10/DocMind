"""embed_max_length 参数化与工厂缓存键的单元测试。

背景：fastembed 会话曾把 token 截断硬编码 512 —— chunk_max_tokens=1500
的分块被静默截断（超出部分不参与向量）。切换长上下文模型（如
jina-embeddings-v2-base-zh）时需把 embed_max_length 调大（如 2048），
该参数是会话创建期参数，变更必须触发工厂缓存重建。
"""

from __future__ import annotations

from doc2mind.core.config import Settings
from doc2mind.core.embedder.factory import _cache_key
from doc2mind.core.embedder.fastembed_impl import _build_model_kwargs


class TestBuildModelKwargs:
    def test_default_max_length_is_512(self) -> None:
        kwargs = _build_model_kwargs(Settings(), "BAAI/bge-small-zh-v1.5")
        assert kwargs["max_length"] == 512
        assert kwargs["model_name"] == "BAAI/bge-small-zh-v1.5"

    def test_explicit_max_length_applies(self) -> None:
        s = Settings(embed_max_length=2048)
        kwargs = _build_model_kwargs(s, "jinaai/jina-embeddings-v2-base-zh")
        assert kwargs["max_length"] == 2048

    def test_invalid_values_fall_back_to_512(self) -> None:
        assert _build_model_kwargs(Settings(embed_max_length=0), "m")["max_length"] == 512
        assert _build_model_kwargs(Settings(embed_max_length=-5), "m")["max_length"] == 512

    def test_threads_included_when_positive(self) -> None:
        kwargs = _build_model_kwargs(Settings(embed_threads=4), "m")
        assert kwargs["threads"] == 4
        assert "threads" not in _build_model_kwargs(Settings(), "m")


class TestFactoryCacheKey:
    def test_max_length_changes_cache_key(self) -> None:
        """embed_max_length 是会话创建期参数，变更必须重建 ONNX 会话。"""
        assert _cache_key(Settings(embed_max_length=512)) != _cache_key(
            Settings(embed_max_length=2048)
        )

    def test_batch_size_not_in_cache_key(self) -> None:
        """embed_batch_size 在 embed() 时现读，不应触发会话重建。"""
        assert _cache_key(Settings(embed_batch_size=32)) == _cache_key(
            Settings(embed_batch_size=128)
        )
