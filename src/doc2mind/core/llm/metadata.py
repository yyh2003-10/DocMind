"""模型能力元数据层：查询/缓存/推断各模型自身的输出上限信息。

背景：此前未知模型（如 nvidia/nemotron-3-super）一律走 registry 保守 fallback（8192/4096），
输出被客户端写死值系统性压短。本模块优先读取模型自身声明的能力（provider / 推理引擎元数据），
经降级链推导有效 max_tokens。

安全护栏：
- 上下文窗口 ≠ 输出上限：禁止把 context_window 等值当 max_tokens 透传；
- 元数据查询永不阻塞对话：3 秒短超时、失败静默落降级链；
- 仅进程内缓存（带 TTL），不落库、不持久化。
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 缓存 TTL（秒）：成功结果 10 分钟；失败结果 1 分钟（失败短缓存，避免打爆端点）
_SUCCESS_TTL_S = 10 * 60
_FAIL_TTL_S = 60

# T6 分档推断参数
_INFER_SMALL_BOUND = 8192      # context_length ≤ 8K：取 1/2
_INFER_LARGE_BOUND = 131072    # 8K < context_length ≤ 128K：取 1/4
_INFER_CAP = 32768             # 大窗口模型封顶（不无限放大）

# 元数据查询短超时（秒），永不阻塞对话
_METADATA_FETCH_TIMEOUT = 3.0


@dataclass(frozen=True)
class ModelMetadata:
    """模型能力元数据。

    - context_length: 上下文窗口（来自 context_length / max_model_len）
    - max_output_tokens: 模型明确声明的输出上限（罕见但优先级最高）
    - source_endpoint: 来源端点（"/v1/models" / "/api/show"）
    - raw_fields: 原始扩展字段（仅排障用，不参与推导）
    - fetched_at: 获取时间戳（monotonic）
    """

    model_id: str
    context_length: int | None
    max_output_tokens: int | None
    source_endpoint: str
    raw_fields: dict[str, object] = field(default_factory=dict)
    fetched_at: float = field(default_factory=lambda: time.monotonic())


class MetadataCache:
    """进程内 TTL 缓存：成功结果 10 分钟、失败结果 1 分钟；惰性删除。

    键为 (model_name_lower, provider)。无容量上限（活跃模型数通常 < 20）。
    """

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], tuple[float, ModelMetadata | None]] = {}

    @staticmethod
    def _key(model_name: str, provider: str) -> tuple[str, str]:
        return (model_name.strip().lower(), (provider or "").strip().lower())

    def get(self, model_name: str, provider: str) -> ModelMetadata | None:
        """返回缓存命中的元数据；未命中/过期/失败条目均返回 None。"""
        key = self._key(model_name, provider)
        entry = self._entries.get(key)
        if entry is None:
            return None
        fetched_at, meta = entry
        ttl = _SUCCESS_TTL_S if meta is not None else _FAIL_TTL_S
        if time.monotonic() - fetched_at > ttl:
            del self._entries[key]
            return None
        return meta

    def put(
        self,
        model_name: str,
        provider: str,
        meta: ModelMetadata | None,
        is_fail: bool = False,
    ) -> None:
        """写入缓存条目。

        - is_fail=False：写入成功元数据（TTL 10 分钟）
        - is_fail=True：写入失败占位（meta 应传 None，TTL 1 分钟）
        """
        if is_fail:
            meta = None
        self._entries[self._key(model_name, provider)] = (time.monotonic(), meta)

    def __len__(self) -> int:
        return len(self._entries)


# 模块级单例：两条链路复用
metadata_cache: MetadataCache = MetadataCache()


class ModelMetadataProvider(ABC):
    """模型能力元数据查询抽象接口。

    后置条件：成功返回 ModelMetadata；失败（404/超时/字段缺失）返回 None，
    不抛异常——所有异常内部捕获并记日志，对调用方透明。
    """

    @abstractmethod
    def fetch(self, model_name: str, timeout: float = _METADATA_FETCH_TIMEOUT) -> ModelMetadata | None:
        """查询模型能力元数据。"""
        raise NotImplementedError


def infer_output_from_context(context_length: int) -> int:
    """context_length → 输出上限推断（T6 方案 A 分档比例）。

    规则：
      - context_length ≤ 8192：取 1/2（小模型不超限）
      - 8192 < context_length ≤ 131072：取 1/4
      - context_length > 131072：封顶 32768（大模型更充分但不无限放大）
      - 永不超过 context_length 本身（禁止等值透传，硬性约束 6）
    """
    if context_length <= 0:
        logger.warning("infer_output_from_context 收到非法 context_length=%d，返回 1", context_length)
        return 1
    if context_length <= _INFER_SMALL_BOUND:
        value = context_length // 2
    elif context_length <= _INFER_LARGE_BOUND:
        value = context_length // 4
    else:
        value = _INFER_CAP
    return min(max(value, 1), context_length)

# ── OpenAI 兼容元数据提供者 ────────────────────────────────────────────────

_OPENAI_CONTEXT_FIELDS = ("context_length", "max_model_len", "max_context_length", "context_window")
_OPENAI_MAX_OUTPUT_FIELDS = ("max_output_tokens", "max_tokens", "max_completion_tokens")


def _extract_int(raw: dict[str, object], *names: str) -> int | None:
    """按候选字段名提取 int 值（容忍值为 str 的数字）。"""
    for name in names:
        if name not in raw:
            continue
        v = raw[name]
        if isinstance(v, bool):
            continue
        if isinstance(v, int):
            return v
        if isinstance(v, str):
            try:
                return int(v.strip())
            except ValueError:
                continue
    return None


class OpenAIMetadataProvider(ModelMetadataProvider):
    """OpenAI 兼容元数据查询：GET /v1/models 的扩展字段。

    复用既有 openai 客户端实例（base_url/api_key 连接配置由调用方传入），
    不新建独立 HTTP 客户端。部分服务不在模型列表返回扩展字段，此时返回 None。
    """

    def __init__(self, client: object) -> None:
        self._client = client

    def fetch(self, model_name: str, timeout: float = _METADATA_FETCH_TIMEOUT) -> ModelMetadata | None:
        try:
            resp = self._client.with_options(timeout=timeout).models.list()
            models = list(getattr(resp, "data", None) or [])
        except Exception as e:  # noqa: BLE001 — 元数据查询失败对调用方透明
            logger.info("OpenAI 元数据查询失败（%s 超时 %.1fs），降级: %s", model_name, timeout, e)
            return None

        target = (model_name or "").strip().lower()
        if not target:
            return None
        for m in models:
            mid = getattr(m, "id", None)
            if mid is None or str(mid).strip().lower() != target:
                continue
            raw: dict[str, object] = {}
            if hasattr(m, "model_dump"):
                try:
                    dumped = m.model_dump()
                    if isinstance(dumped, dict):
                        raw = dict(dumped)
                except Exception:  # noqa: BLE001
                    raw = {}
            if not raw and hasattr(m, "__dict__"):
                raw = dict(getattr(m, "__dict__") or {})
            for name in (*_OPENAI_CONTEXT_FIELDS, *_OPENAI_MAX_OUTPUT_FIELDS):
                if name not in raw:
                    v = getattr(m, name, None)
                    if v is not None and not callable(v):
                        raw[name] = v
            context_length = _extract_int(raw, *_OPENAI_CONTEXT_FIELDS)
            max_output = _extract_int(raw, *_OPENAI_MAX_OUTPUT_FIELDS)
            logger.info(
                "OpenAI 元数据命中 %s: context_length=%s max_output_tokens=%s",
                mid, context_length, max_output,
            )
            return ModelMetadata(
                model_id=str(mid),
                context_length=context_length,
                max_output_tokens=max_output,
                source_endpoint="/v1/models",
                raw_fields=raw,
            )
        logger.info("OpenAI 元数据列表未见目标模型 %s，降级", model_name)
        return None


# ── Ollama 元数据提供者 ─────────────────────────────────────────────────────

class OllamaMetadataProvider(ModelMetadataProvider):
    """Ollama 元数据查询：POST /api/show 的 model_info.context_length。"""

    def __init__(self, host: str | None = None) -> None:
        self._host = (host or os.environ.get("OLLAMA_HOST", "http://localhost:11434")).rstrip("/")

    def fetch(self, model_name: str, timeout: float = _METADATA_FETCH_TIMEOUT) -> ModelMetadata | None:
        import httpx

        try:
            resp = httpx.post(
                f"{self._host}/api/show",
                json={"name": model_name},
                timeout=timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # noqa: BLE001 — 元数据查询失败对调用方透明
            logger.info("Ollama 元数据查询失败（%s 超时 %.1fs），降级: %s", model_name, timeout, e)
            return None

        model_info = data.get("model_info") or {}
        context_length = _extract_int(model_info, "context_length", "context_window")
        logger.info("Ollama 元数据命中 %s: context_length=%s", model_name, context_length)
        return ModelMetadata(
            model_id=model_name,
            context_length=context_length,
            max_output_tokens=None,
            source_endpoint="/api/show",
            raw_fields={"model_info_keys": sorted(k for k in model_info.keys()) if isinstance(model_info, dict) else []},
        )


# ── 四级降级链 ──────────────────────────────────────────────────────────────

def resolve_max_output_tokens(
    model_name: str,
    provider: str,
    user_config: int | None,
    registry_spec: object,
    metadata_provider: ModelMetadataProvider | None = None,
    metadata_cache: MetadataCache | None = None,
) -> tuple[int | None, str, str]:
    """推导有效输出上限，返回 (effective_value, source_tag, derivation_log)。

    优先级：user-config（显式配置且有效）> metadata 声明值 > metadata context_length
    推断值 > registry > provider fallback。最终统一经 sanitize_max_tokens_v2 夹取。

    source_tag ∈ {"user-config", "metadata", "registry", "fallback"}。
    永不抛异常；元数据查询 3 秒短超时、失败静默落降级链。
    """
    from doc2mind.core.llm.base import sanitize_max_tokens_v2

    log: list[str] = []

    # 1) user-config（显式配置且有效）
    if user_config is not None and user_config >= 1:
        direct = sanitize_max_tokens_v2(user_config)
        if direct is not None:
            return direct, "user-config", f"用户显式配置 llm_max_tokens={user_config}"

    # 2) metadata：声明值 > context_length 推断值
    cache = metadata_cache if metadata_cache is not None else metadata_cache
    meta: ModelMetadata | None = None
    if cache is not None:
        meta = cache.get(model_name, provider)
    if meta is None and metadata_provider is not None:
        try:
            meta = metadata_provider.fetch(model_name)
        except Exception as e:  # noqa: BLE001 — 防御第三方 provider 不守「不抛异常」契约
            log.append(f"元数据查询异常（{provider}）: {e}")
            meta = None
        if cache is not None:
            cache.put(model_name, provider, meta, is_fail=meta is None)
            if meta is None:
                log.append(f"元数据查询失败（{provider}），1 分钟内不再重复查询")
    if meta is not None:
        if meta.max_output_tokens is not None and meta.max_output_tokens >= 1:
            effective = sanitize_max_tokens_v2(meta.max_output_tokens, metadata=meta)
            if effective is not None:
                return (
                    effective,
                    "metadata",
                    f"元数据声明输出上限 max_output_tokens={meta.max_output_tokens} "
                    f"({meta.source_endpoint}); {'; '.join(log)}".strip("; "),
                )
        if meta.context_length is not None and meta.context_length >= 1:
            inferred = infer_output_from_context(meta.context_length)
            effective = sanitize_max_tokens_v2(inferred, metadata=meta)
            if effective is not None:
                return (
                    effective,
                    "metadata",
                    f"元数据 context_length={meta.context_length} 按分档推断出 {inferred} "
                    f"({meta.source_endpoint}); {'; '.join(log)}".strip("; "),
                )
        log.append(f"元数据无可用输出上限信息（context_length={meta.context_length}, "
                   f"max_output_tokens={meta.max_output_tokens}）")

    # 3) registry（get_model_spec 结果）
    if registry_spec is not None:
        reg_value = getattr(registry_spec, "max_output_tokens", None)
        if reg_value is not None and reg_value >= 1:
            effective = sanitize_max_tokens_v2(reg_value, metadata=meta)
            if effective is not None:
                log.append(f"registry 规格 max_output_tokens={reg_value}")
                return effective, "registry", "; ".join(log).strip("; ")

    # 4) provider fallback（registry 缺失/异常时的安全兜底）
    log.append("registry 不可用，落安全兜底 8192")
    effective = sanitize_max_tokens_v2(8192, metadata=meta)
    return effective, "fallback", "; ".join(log).strip("; ")
