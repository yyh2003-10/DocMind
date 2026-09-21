"""联网搜索 deadline 契约：到点带着已完成结果返回，不整段丢弃。"""

from __future__ import annotations

import time

from doc2mind.core.search.web_search import WebSearchResult, WebSearchService


def _force_scores(results: list[WebSearchResult]) -> None:
    for r in results:
        r.relevance_score = max(r.relevance_score, 0.8)
        r.quality_score = max(r.quality_score, 0.8)


class TestDeadlineContract:
    def test_deadline_returns_partial_results_from_fast_channel(self, monkeypatch) -> None:
        service = WebSearchService(fetch_pages=False, cache_ttl_seconds=0)

        def slow_baidu(query: str, limit: int):
            time.sleep(2.0)
            return []

        def fast_bing(query: str, limit: int):
            return [
                WebSearchResult(
                    title="GPT overview",
                    url="https://example.com/gpt",
                    snippet="GPT is a transformer language model from OpenAI",
                )
            ]

        monkeypatch.setattr(service, "_search_baidu", slow_baidu)
        monkeypatch.setattr(service, "_search_bing", fast_bing)
        monkeypatch.setattr(service, "_search_ddgs", lambda q, lim: [])
        monkeypatch.setattr(service, "_search_ddg_api", lambda q, lim: [])
        monkeypatch.setattr(service, "_search_searxng", lambda q, lim: [])
        monkeypatch.setattr(
            service, "_score_results",
            lambda results, q, latest: _force_scores(results),
        )

        # 预热分词/jieba：冷启动可能超过 0.6s deadline，会把契约测成假失败
        service._query_tokens("什么是GPT")
        deadline = time.monotonic() + 0.6
        results = service.search("什么是GPT", max_results=5, deadline=deadline)
        assert results, "deadline 内应返回快通道已完成结果"
        assert any("gpt" in r.url.lower() for r in results)

    def test_no_deadline_still_works_with_mocks(self, monkeypatch) -> None:
        service = WebSearchService(fetch_pages=False, cache_ttl_seconds=0)
        monkeypatch.setattr(
            service, "_search_baidu",
            lambda q, lim: [
                WebSearchResult(
                    title="GPT paper",
                    url="https://example.com/gpt-intro",
                    snippet="GPT generative pre-trained transformer",
                )
            ],
        )
        monkeypatch.setattr(service, "_search_bing", lambda q, lim: [])
        monkeypatch.setattr(service, "_search_ddgs", lambda q, lim: [])
        monkeypatch.setattr(service, "_search_ddg_api", lambda q, lim: [])
        monkeypatch.setattr(service, "_search_searxng", lambda q, lim: [])
        monkeypatch.setattr(
            service, "_score_results",
            lambda results, q, latest: _force_scores(results),
        )
        results = service.search("gpt transformer", max_results=5)
        assert len(results) == 1
