"""可插拔 Search Provider 接口（T6）。

默认 `builtin`：走现有 WebSearchService 多引擎抓取。
外部 API（如 tavily/bocha/serpapi 风格）仅在用户显式配置时启用；
无 key 不崩溃，回落 builtin 或报明确错误。密钥不进日志/响应。
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

logger = logging.getLogger(__name__)


def mask_secret(value: str | None) -> str:
    """密钥脱敏：仅保留前 4 / 后 2。"""
    raw = value or ""
    if len(raw) <= 6:
        return "***" if raw else ""
    return f"{raw[:4]}***{raw[-2:]}"


@dataclass
class ProviderSearchHit:
    title: str
    url: str
    snippet: str = ""
    content: str = ""
    score: float = 0.0
    source_name: str = ""
    domain: str = ""


@dataclass
class ProviderSearchResult:
    hits: list[ProviderSearchHit] = field(default_factory=list)
    provider: str = "builtin"
    elapsed_ms: int = 0
    degraded: bool = False
    error: str | None = None


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, *, max_results: int = 8, timeout: float = 20.0) -> ProviderSearchResult:
        ...


class BuiltinSearchProvider:
    """包装现有内置搜索引擎。"""

    name = "builtin"

    def search(self, query: str, *, max_results: int = 8, timeout: float = 20.0) -> ProviderSearchResult:
        import time as _time
        from urllib.parse import urlparse

        from doc2mind.core.search.web_search import get_web_search_service

        t0 = _time.perf_counter()
        try:
            svc = get_web_search_service()
            results = svc.search(query, max_results=max_results, deadline=_time.monotonic() + timeout)
            hits = [
                ProviderSearchHit(
                    title=getattr(r, "title", "") or "",
                    url=getattr(r, "url", "") or "",
                    snippet=getattr(r, "snippet", "") or "",
                    content=(getattr(r, "content", "") or "")[:2000],
                    score=float(getattr(r, "relevance_score", 0.0) or 0.0),
                    source_name=getattr(r, "source_name", "") or "builtin",
                    domain=urlparse(getattr(r, "url", "") or "").netloc,
                )
                for r in results
            ]
            return ProviderSearchResult(
                hits=hits,
                provider="builtin",
                elapsed_ms=int((_time.perf_counter() - t0) * 1000),
            )
        except Exception as ex:  # noqa: BLE001
            logger.warning("builtin 搜索失败: %s", ex)
            return ProviderSearchResult(
                hits=[],
                provider="builtin",
                elapsed_ms=int((_time.perf_counter() - t0) * 1000),
                degraded=True,
                error=str(ex)[:200],
            )


class HttpJsonSearchProvider:
    """通用 HTTP JSON 搜索 Provider（OpenAPI 风格可配置端点）。

    约定响应：`{"results":[{"title","url","snippet"|"content","score"?}]}`。
    用于 tavily/博查/serpapi 等兼容形态；不内置任何商业实现细节。
    """

    def __init__(self, name: str, endpoint: str, api_key: str, *, key_header: str = "Authorization") -> None:
        self.name = name
        self.endpoint = endpoint
        self._api_key = api_key or ""
        self._key_header = key_header

    def search(self, query: str, *, max_results: int = 8, timeout: float = 20.0) -> ProviderSearchResult:
        import time as _time
        from urllib.parse import urlparse

        t0 = _time.perf_counter()
        if not self._api_key or not self.endpoint:
            return ProviderSearchResult(
                hits=[],
                provider=self.name,
                degraded=True,
                error="search_provider_api_key 未配置，请到设置填写后重试",
            )
        body = json.dumps({"query": query, "max_results": max_results}).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "DocMind-SearchProvider/1.0",
        }
        if self._key_header.lower() == "authorization":
            headers["Authorization"] = f"Bearer {self._api_key}"
        else:
            headers[self._key_header] = self._api_key
        req = urllib.request.Request(self.endpoint, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — 用户配置的搜索端点
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
            raw_hits = payload.get("results") or payload.get("data") or []
            hits: list[ProviderSearchHit] = []
            for item in raw_hits[:max_results]:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or item.get("link") or "")
                if not url:
                    continue
                hits.append(
                    ProviderSearchHit(
                        title=str(item.get("title") or ""),
                        url=url,
                        snippet=str(item.get("snippet") or item.get("description") or "")[:500],
                        content=str(item.get("content") or item.get("text") or "")[:2000],
                        score=float(item.get("score") or 0.0),
                        source_name=self.name,
                        domain=urlparse(url).netloc,
                    )
                )
            return ProviderSearchResult(
                hits=hits,
                provider=self.name,
                elapsed_ms=int((_time.perf_counter() - t0) * 1000),
            )
        except urllib.error.HTTPError as e:
            code = e.code
            if code in (401, 403):
                msg = f"{self.name} 鉴权失败（HTTP {code}），请检查 API Key"
            else:
                msg = f"{self.name} 搜索失败 HTTP {code}"
            logger.warning("%s；key=%s", msg, mask_secret(self._api_key))
            return ProviderSearchResult(
                hits=[],
                provider=self.name,
                elapsed_ms=int((_time.perf_counter() - t0) * 1000),
                degraded=True,
                error=msg,
            )
        except Exception as ex:  # noqa: BLE001
            logger.warning("%s 搜索异常（已降级）: %s", self.name, ex)
            return ProviderSearchResult(
                hits=[],
                provider=self.name,
                elapsed_ms=int((_time.perf_counter() - t0) * 1000),
                degraded=True,
                error=f"{self.name} 不可用: {str(ex)[:160]}",
            )


# 可扩展注册表：只登记接口形态，不预置付费默认
PROVIDER_ENDPOINTS: dict[str, str] = {
    "tavily": "https://api.tavily.com/search",
    "bocha": "https://api.bochaai.com/v1/web-search",
    "serpapi": "https://serpapi.com/search",
}


def resolve_search_provider(
    provider: str | None,
    api_key: str | None,
    *,
    endpoint_override: str | None = None,
) -> SearchProvider:
    """按配置解析 provider；非法/缺 key 时回落 builtin。"""
    name = (provider or "builtin").strip().lower()
    if name in ("", "builtin", "none"):
        return BuiltinSearchProvider()
    endpoint = endpoint_override or PROVIDER_ENDPOINTS.get(name, "")
    if not endpoint:
        logger.warning("未知 search_provider=%s，回落 builtin", name)
        return BuiltinSearchProvider()
    if not (api_key or "").strip():
        logger.warning("search_provider=%s 未配置 API Key，回落 builtin", name)
        return BuiltinSearchProvider()
    return HttpJsonSearchProvider(name, endpoint, (api_key or "").strip())


def provider_to_web_results(result: ProviderSearchResult) -> list[Any]:
    """把 Provider 命中转成 WebSearchResult 兼容结构，进入现有引用链路。"""
    from doc2mind.core.search.web_search import WebSearchResult

    out: list[Any] = []
    for h in result.hits:
        out.append(
            WebSearchResult(
                title=h.title,
                url=h.url,
                snippet=h.snippet,
                content=h.content,
                content_fetched=bool(h.content.strip()),
                relevance_score=h.score or 0.5,
                source_name=h.source_name or result.provider,
                domain=h.domain,
                corroborated_by=0,
                evidence_level="单一来源",
            )
        )
    return out
