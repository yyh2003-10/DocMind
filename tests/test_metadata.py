"""回答长度控制 — 元数据层单元测试（对应 tasks.md T2）。"""

from __future__ import annotations

from unittest.mock import patch

from doc2mind.core.llm.metadata import (
    MetadataCache,
    ModelMetadata,
    OpenAIMetadataProvider,
    OllamaMetadataProvider,
    infer_output_from_context,
    metadata_cache,
    resolve_max_output_tokens,
)
from doc2mind.core.llm.base import MAX_TOKENS_CEILING, sanitize_max_tokens, sanitize_max_tokens_v2


def _meta(
    model: str = "nemotron-3-super",
    context_length: int | None = 131072,
    max_output: int | None = None,
    endpoint: str = "/v1/models",
) -> ModelMetadata:
    return ModelMetadata(
        model_id=model,
        context_length=context_length,
        max_output_tokens=max_output,
        source_endpoint=endpoint,
        raw_fields={},
    )


# ── ModelMetadata ────────────────────────────────────────────────────────────

class TestModelMetadata:
    def test_fields(self) -> None:
        m = _meta(max_output=16384)
        assert m.model_id == "nemotron-3-super"
        assert m.context_length == 131072
        assert m.max_output_tokens == 16384
        assert m.source_endpoint == "/v1/models"
        assert m.raw_fields == {}
        assert m.fetched_at > 0

    def test_frozen(self) -> None:
        m = _meta()
        try:
            m.max_output_tokens = 999  # type: ignore[misc]
        except Exception:
            assert True
        else:
            raise AssertionError("ModelMetadata 应为 frozen 不可变")

    def test_context_only_fields_noneable(self) -> None:
        m = _meta(context_length=None, max_output=None)
        assert m.context_length is None
        assert m.max_output_tokens is None


# ── MetadataCache ────────────────────────────────────────────────────────────

class TestMetadataCache:
    def test_hit_within_ttl(self) -> None:
        c = MetadataCache()
        m = _meta()
        c.put("nemotron-3-super", "openai", m)
        assert c.get("nemotron-3-super", "openai") is m

    def test_expire_after_success_ttl(self) -> None:
        c = MetadataCache()
        m = _meta()
        with patch("doc2mind.core.llm.metadata.time.monotonic", side_effect=[0.0, 1.0, 600.01, 601.0, 602.0]):
            c.put("nemotron-3-super", "openai", m)
            assert c.get("nemotron-3-super", "openai") is m
            assert c.get("nemotron-3-super", "openai") is None

    def test_fail_cache_keeps_entry_within_short_ttl(self) -> None:
        c = MetadataCache()
        with patch("doc2mind.core.llm.metadata.time.monotonic", side_effect=[0.0, 59.0, 60.0]):
            c.put("m", "ollama", None, is_fail=True)
            assert c.get("m", "ollama") is None
            assert ("m", "ollama") in c._entries

    def test_fail_cache_expires(self) -> None:
        c = MetadataCache()
        with patch("doc2mind.core.llm.metadata.time.monotonic", side_effect=[0.0, 61.0, 62.0]):
            c.put("m", "ollama", None, is_fail=True)
            assert c.get("m", "ollama") is None
            assert ("m", "ollama") not in c._entries

    def test_key_normalization(self) -> None:
        c = MetadataCache()
        m = _meta()
        c.put("Nemotron-3-Super ", " OpenAI", m)
        assert c.get("nemotron-3-super", "openai") is m
        assert c.get("nemotron-3-super", "OpenAI") is m

    def test_put_success_overwrites_fail(self) -> None:
        c = MetadataCache()
        m = _meta()
        with patch("doc2mind.core.llm.metadata.time.monotonic", side_effect=[0.0, 1.0, 2.0]):
            c.put("m", "openai", None, is_fail=True)
            c.put("m", "openai", m)
            assert c.get("m", "openai") is m


# ── infer_output_from_context（T6 分档比例） ─────────────────────────────────

class TestInferOutputFromContext:
    def test_small_window_half(self) -> None:
        assert infer_output_from_context(8192) == 4096
        assert infer_output_from_context(4096) == 2048

    def test_mid_window_quarter(self) -> None:
        assert infer_output_from_context(131072) == 32768
        assert infer_output_from_context(16384) == 4096

    def test_large_window_capped(self) -> None:
        assert infer_output_from_context(131073) == 32768
        assert infer_output_from_context(1000000) == 32768

    def test_never_exceeds_context(self) -> None:
        for ctx in (1, 10, 100, 2048, 8192, 8193, 16000, 131072, 131073, 999999):
            assert infer_output_from_context(ctx) <= ctx

    def test_extreme_small(self) -> None:
        assert infer_output_from_context(1) == 1
        assert infer_output_from_context(0) == 1
        assert infer_output_from_context(-5) == 1


# ── sanitize_max_tokens_v2（T5 天花板动态放行） ─────────────────────────────

class TestSanitizeMaxTokensV2:
    def test_value_within_ceiling_passthrough(self) -> None:
        assert sanitize_max_tokens_v2(4096) == 4096
        assert sanitize_max_tokens_v2(MAX_TOKENS_CEILING) == MAX_TOKENS_CEILING

    def test_below_one_clamped(self) -> None:
        assert sanitize_max_tokens_v2(0) == 1
        assert sanitize_max_tokens_v2(-3) == 1

    def test_none_passthrough(self) -> None:
        assert sanitize_max_tokens_v2(None) is None

    def test_above_ceiling_with_declared_metadata_released(self) -> None:
        m = _meta(max_output=100000)
        assert sanitize_max_tokens_v2(70000, metadata=m) == 70000

    def test_above_ceiling_without_declared_normalized(self) -> None:
        m = _meta(context_length=1000000, max_output=None)
        assert sanitize_max_tokens_v2(70000, metadata=m) is None

    def test_declared_smaller_than_value_still_normalized(self) -> None:
        m = _meta(max_output=60000)
        assert sanitize_max_tokens_v2(70000, metadata=m) is None

    def test_no_metadata_degrades_to_original(self) -> None:
        assert sanitize_max_tokens_v2(65537) is None
        assert sanitize_max_tokens_v2(256000) is None
        assert sanitize_max_tokens_v2(2048) == 2048

    def test_original_sanitize_untouched(self) -> None:
        assert sanitize_max_tokens(65537) is None
        assert sanitize_max_tokens(256000) is None
        assert sanitize_max_tokens(1) == 1


# ── 模块级单例 ─────────────────────────────────────────────────────────────

class TestModuleSingleton:
    def test_metadata_cache_singleton(self) -> None:
        assert metadata_cache is not None
        assert isinstance(metadata_cache, MetadataCache)

# ── 降级链（T3） ────────────────────────────────────────────────────────────

class _StubRegistry:
    def __init__(self, max_output: int = 8192):
        self.max_output_tokens = max_output


class _StubProvider:
    def __init__(self, result, exc=None):
        self.result = result
        self.exc = exc
        self.calls = 0

    def fetch(self, model_name: str, timeout: float = 3.0):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.result


class TestResolveMaxOutputTokens:
    def test_user_config_wins(self) -> None:
        value, source, log = resolve_max_output_tokens(
            "m", "openai", user_config=4096, registry_spec=_StubRegistry(),
        )
        assert value == 4096
        assert source == "user-config"

    def test_user_config_invalid_falls_to_registry(self) -> None:
        meta = _meta(max_output=100000)
        provider = _StubProvider(meta)
        value, source, log = resolve_max_output_tokens(
            "m", "openai", user_config=None, registry_spec=_StubRegistry(),
            metadata_provider=provider, metadata_cache=MetadataCache(),
        )
        assert source == "metadata"
        assert value == 100000

    def test_metadata_declared_value_priority(self) -> None:
        meta = _meta(context_length=131072, max_output=16384)
        provider = _StubProvider(meta)
        value, source, _ = resolve_max_output_tokens(
            "m", "openai", user_config=None, registry_spec=_StubRegistry(),
            metadata_provider=provider, metadata_cache=MetadataCache(),
        )
        assert source == "metadata"
        assert value == 16384

    def test_metadata_inferred_from_context(self) -> None:
        meta = _meta(context_length=131072, max_output=None)
        provider = _StubProvider(meta)
        value, source, _ = resolve_max_output_tokens(
            "m", "openai", user_config=None, registry_spec=_StubRegistry(),
            metadata_provider=provider, metadata_cache=MetadataCache(),
        )
        assert source == "metadata"
        assert value == 32768  # 131072 // 4

    def test_metadata_unavailable_falls_to_registry(self) -> None:
        provider = _StubProvider(None)
        value, source, _ = resolve_max_output_tokens(
            "m", "openai", user_config=None, registry_spec=_StubRegistry(max_output=8192),
            metadata_provider=provider, metadata_cache=MetadataCache(),
        )
        assert source == "registry"
        assert value == 8192

    def test_provider_exception_still_resolves(self) -> None:
        provider = _StubProvider(None, exc=RuntimeError("boom"))
        value, source, log = resolve_max_output_tokens(
            "m", "openai", user_config=None, registry_spec=_StubRegistry(),
            metadata_provider=provider, metadata_cache=MetadataCache(),
        )
        assert value == 8192
        assert source == "registry"

    def test_no_registry_falls_back(self) -> None:
        provider = _StubProvider(None)
        value, source, _ = resolve_max_output_tokens(
            "m", "openai", user_config=None, registry_spec=None,
            metadata_provider=provider, metadata_cache=MetadataCache(),
        )
        assert value == 8192
        assert source == "fallback"

    def test_user_config_above_ceiling_with_declared_metadata_released(self) -> None:
        meta = _meta(max_output=100000)
        provider = _StubProvider(meta)
        value, source, _ = resolve_max_output_tokens(
            "m", "openai", user_config=70000, registry_spec=_StubRegistry(),
            metadata_provider=provider, metadata_cache=MetadataCache(),
        )
        # user_config=70000 超天花板 65536，归一为 None，降级到 metadata 声明值
        assert value == 100000
        assert source == "metadata"

    def test_never_equals_context_passthrough(self) -> None:
        meta = _meta(context_length=131072, max_output=None)
        provider = _StubProvider(meta)
        value, source, _ = resolve_max_output_tokens(
            "m", "openai", user_config=None, registry_spec=_StubRegistry(),
            metadata_provider=provider, metadata_cache=MetadataCache(),
        )
        assert value < 131072
        assert value == 32768

    def test_cache_controls_fetch_calls(self) -> None:
        meta = _meta(context_length=131072, max_output=None)
        provider = _StubProvider(meta)
        cache = MetadataCache()
        for _ in range(3):
            resolve_max_output_tokens(
                "m", "openai", user_config=None, registry_spec=_StubRegistry(),
                metadata_provider=provider, metadata_cache=cache,
            )
        assert provider.calls == 1

    def test_never_raises_on_any_input(self) -> None:
        for user_config in (None, 0, -5, 1, 4096, 65536):
            for reg in (None, _StubRegistry()):
                value, source, log = resolve_max_output_tokens(
                    "m", "openai", user_config=user_config, registry_spec=reg,
                    metadata_provider=_StubProvider(None, exc=RuntimeError("x")),
                    metadata_cache=MetadataCache(),
                )
                assert value is None or value >= 1
                assert source in {"user-config", "metadata", "registry", "fallback"}
                assert isinstance(log, str)
