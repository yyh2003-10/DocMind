"""免 Key 实时联网搜索服务 (WebSearchService)

基于可商用开源协议 (MIT)，提供无需 API Key 的零配置实时网页技术检索。
支持获取网页标题、网页摘要 Snippet、原始 URL 与清洗后的 Markdown 文本。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class WebSearchResult:
    """单条网页检索结果。"""
    title: str
    url: str
    snippet: str
    source_name: str = "Web"


class WebSearchService:
    """联网搜索引擎服务封装。"""

    def __init__(self, timeout: int = 8, max_results: int = 5):
        self.timeout = timeout
        self.max_results = max_results

    def search(self, query: str, max_results: int | None = None) -> list[WebSearchResult]:
        """执行联网搜索。若无网络或搜索库未安装，安全降级返回空列表，绝不中断主对话流程。"""
        if not query or not query.strip():
            return []

        limit = max_results or self.max_results
        cleaned_query = query.strip()

        # 1. 尝试使用 duckduckgo_search (MIT 协议，免 Key 体验)
        try:
            from duckduckgo_search import DDGS  # type: ignore

            results: list[WebSearchResult] = []
            with DDGS(timeout=self.timeout) as ddgs:
                ddg_results = ddgs.text(cleaned_query, max_results=limit)
                if ddg_results:
                    for item in ddg_results:
                        title = item.get("title", "").strip()
                        href = item.get("href", "").strip()
                        body = item.get("body", "").strip()
                        if title and href:
                            results.append(WebSearchResult(
                                title=title,
                                url=href,
                                snippet=self._clean_snippet(body),
                                source_name="DuckDuckGo",
                            ))
            if results:
                logger.info("联网搜索成功: query=%s, 获得 %d 条结果", cleaned_query, len(results))
                return results
        except ImportError:
            logger.warning("duckduckgo_search 未安装，准备使用轻量 Web API 回退")
        except Exception as e:  # noqa: BLE001
            logger.warning("DuckDuckGo 搜索失败: %s", e)

        # 2. 轻量 HTTP 备用搜索通道 (通过公共元搜索引擎或 html 解析)
        try:
            import json
            import urllib.parse
            import urllib.request

            encoded_query = urllib.parse.quote(cleaned_query)
            url = f"https://api.duckduckgo.com/?q={encoded_query}&format=json&no_html=1&skip_disambig=1"
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                fallback_results: list[WebSearchResult] = []
                abstract = data.get("AbstractText", "").strip()
                abstract_url = data.get("AbstractURL", "").strip()
                heading = data.get("Heading", "").strip()
                if abstract and abstract_url:
                    fallback_results.append(WebSearchResult(
                        title=heading or cleaned_query,
                        url=abstract_url,
                        snippet=self._clean_snippet(abstract),
                        source_name="Web Instant",
                    ))
                for topic in data.get("RelatedTopics", [])[:limit]:
                    if isinstance(topic, dict) and "Text" in topic and "FirstURL" in topic:
                        fallback_results.append(WebSearchResult(
                            title=topic.get("Text", "")[:60] + "...",
                            url=topic.get("FirstURL", ""),
                            snippet=self._clean_snippet(topic.get("Text", "")),
                            source_name="Web Reference",
                        ))
                if fallback_results:
                    return fallback_results
        except Exception as ex:  # noqa: BLE001
            logger.warning("备用 Web 搜索失败: %s", ex)

        return []

    def _clean_snippet(self, text: str) -> str:
        """轻量清洗文本，去除 HTML 标签与多余空格。"""
        if not text:
            return ""
        cleaned = re.sub(r"<[^>]+>", "", text)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned.strip()

    def format_as_context(self, results: list[WebSearchResult]) -> str:
        """将搜索结果格式化为可直接注入 Prompt 的 Markdown 上下文。"""
        if not results:
            return ""
        lines = ["【实时联网检索资料 (Live Web Search Context)】"]
        for idx, r in enumerate(results, start=1):
            lines.append(f"[{idx}] 来源: {r.title} ({r.url})\n摘要: {r.snippet}")
        return "\n\n".join(lines)


# 全局单例
_global_search_service: WebSearchService | None = None


def get_web_search_service() -> WebSearchService:
    """获取全局联网搜索服务单例。"""
    global _global_search_service
    if _global_search_service is None:
        _global_search_service = WebSearchService()
    return _global_search_service
