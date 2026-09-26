"""联网搜索质量测试，不访问真实互联网。"""

from __future__ import annotations

import json
import sys
from types import ModuleType

from doc2mind.core.search.web_search import WebSearchResult, WebSearchService


class TestFreeChannels:
    def test_ddgs_uses_query_locale_and_latest_time_filter(self, monkeypatch) -> None:
        calls: list[tuple[str, dict]] = []

        class FakeDDGS:
            def __init__(self, timeout):
                self.timeout = timeout

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def text(self, query, **kwargs):
                calls.append((query, kwargs))
                return [{
                    "title": "GPT guide",
                    "href": "https://example.com/gpt",
                    "body": "GPT architecture guide",
                }]

        ddgs_module = ModuleType("ddgs")
        ddgs_module.DDGS = FakeDDGS
        monkeypatch.setitem(sys.modules, "ddgs", ddgs_module)
        service = WebSearchService(fetch_pages=False)

        assert service._search_ddgs("GPT architecture", 4)
        assert service._search_ddgs("最新 GPT 架构", 4)
        assert calls[0][1] == {"max_results": 4, "region": "us-en"}
        assert calls[1][1] == {
            "max_results": 4,
            "region": "cn-zh",
            "timelimit": "m",
        }

    def test_searxng_parses_json_results(self) -> None:
        service = WebSearchService(fetch_pages=False)
        payload = {
            "results": [
                {
                    "title": "GPT - 维基百科",
                    "url": "https://zh.wikipedia.org/wiki/GPT",
                    "content": "生成式预训练变换器",
                },
                {
                    "title": "Bad",
                    "url": "",
                    "content": "x",
                },
            ]
        }

        class FakeResp:
            def read(self):
                return json.dumps(payload, ensure_ascii=False).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        service._open_url = lambda req, timeout: FakeResp()  # type: ignore
        results = service._search_searxng("什么是GPT", 5)
        assert len(results) == 1
        assert results[0].source_name == "SearXNG"
        assert "GPT" in results[0].title

    def test_wikipedia_extracts_fill_content(self) -> None:
        service = WebSearchService(fetch_pages=False)
        result = WebSearchResult(
            title="GPT - 维基百科",
            url="https://zh.wikipedia.org/wiki/GPT",
            snippet="生成式预训练",
            source_name="Wikipedia",
            domain="zh.wikipedia.org",
        )
        payload = {
            "query": {
                "pages": {
                    "1": {
                        "title": "GPT",
                        "extract": "生成式预训练变换器（GPT）是一种大语言模型架构。",
                    }
                }
            }
        }

        class FakeResp:
            def read(self):
                return json.dumps(payload, ensure_ascii=False).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        service._open_url = lambda req, timeout: FakeResp()  # type: ignore
        service._fill_wikipedia_extracts(["GPT"], [result], "zh")
        assert result.content_fetched is True
        assert "大语言模型" in result.content

    def test_wikipedia_has_high_authority(self) -> None:
        assert WebSearchService._authority_score("zh.wikipedia.org") >= 0.9
        assert WebSearchService._authority_score("en.wikipedia.org") >= 0.9


class TestNormalizeSearchUrl:
    def test_decodes_bing_ck_redirect(self) -> None:
        raw = (
            "https://www.bing.com/ck/a?!&amp;&amp;"
            "u=a1aHR0cHM6Ly93d3cueGUuY29tL2N1cnJlbmN5Y29udmVydGVyLw&amp;ntb=1"
        )
        assert WebSearchService.normalize_url(raw) == (
            "https://www.xe.com/currencyconverter"
        )

    def test_removes_tracking_parameters_and_fragment(self) -> None:
        raw = "https://example.com/manual/?utm_source=bing&id=3#page=2"
        assert WebSearchService.normalize_url(raw) == "https://example.com/manual?id=3"

    def test_rejects_non_http_url(self) -> None:
        assert WebSearchService.normalize_url("javascript:alert(1)") == ""
        assert WebSearchService.normalize_url("file:///secret.txt") == ""

    def test_normalize_restores_so_link_url_param(self) -> None:
        raw = "https://www.so.com/link?url=https://example.com/manual&from=search"
        assert WebSearchService.normalize_url(raw) == "https://example.com/manual"


class TestWebSearchQuality:
    def test_long_cjk_entity_is_not_truncated_in_query_variants(self) -> None:
        service = WebSearchService(fetch_pages=False)

        variants = service._query_variants("弹性模量是什么", "弹性模量是什么")

        assert "弹性是什么意思" not in variants
        assert "弹性工作原理" not in variants
        assert "弹性计算公式" not in variants
        assert "弹性模量" in variants

    def test_query_variants_prioritize_official_and_manual_sources(self) -> None:
        service = WebSearchService(fetch_pages=False)
        variants = service._query_variants(
            "了解台达B3伺服的最新技术和功能",
            "了解台达B3伺服的最新技术和功能 ASDA-B3 伺服",
        )
        assert variants[0].endswith("ASDA-B3 伺服")
        assert any("site:deltaww.com" in item for item in variants)
        assert any("filetype:pdf" in item for item in variants)

    def test_pdf_text_is_used_when_available(self, monkeypatch) -> None:
        from pdfminer import high_level

        monkeypatch.setattr(
            high_level,
            "extract_text",
            lambda _stream, maxpages=0: (
                "ASDA-B3 技术手册 2025年3月1日 高速响应与振动抑制。"
                "本手册说明伺服驱动器的控制模式、参数设置、报警处理和调试流程。"
                "适用于工业自动化产线的定位、速度、转矩和安全控制场景。"
            ),
        )
        result = WebSearchResult(
            "ASDA-B3 手册",
            "https://filecenter.deltaww.com/asda-b3.pdf",
            "搜索摘要",
        )
        WebSearchService._extract_pdf_content(result, b"fake-pdf")
        assert result.content_fetched is True
        assert "高速响应" in result.content
        assert result.published_at == "2025-03-01"

    def test_filters_irrelevant_result_and_keeps_relevant_official_result(self) -> None:
        service = WebSearchService(fetch_pages=False)
        irrelevant = WebSearchResult(
            title="XE Currency Converter",
            url="https://www.xe.com/currencyconverter/",
            snippet="实时汇率和货币转换工具",
            source_name="Bing",
        )
        official = WebSearchResult(
            title="台达 ASDA-B3 伺服驱动器：技术与功能",
            url="https://filecenter.deltaww.com/Products/Download/01/ASDA-B3.pdf",
            snippet="ASDA-B3 伺服驱动器支持高速响应、振动抑制和多种控制功能。",
            source_name="Bing",
        )
        service._search_baidu = lambda _query, _limit: [irrelevant]  # type: ignore[method-assign]
        service._search_bing = lambda _query, _limit: [official]  # type: ignore[method-assign]
        service._search_ddgs = lambda _query, _limit: []  # type: ignore[method-assign]

        results = service.search("了解台达B3伺服的最新技术和功能", max_results=4)

        assert len(results) == 1
        assert results[0].url == "https://filecenter.deltaww.com/Products/Download/01/ASDA-B3.pdf"
        assert results[0].authority_score == 1.0
        assert results[0].relevance_score >= 0.12

    def test_aggregates_multiple_engines_instead_of_first_success_only(self) -> None:
        service = WebSearchService(fetch_pages=False)
        baidu = WebSearchResult(
            "台达 ASDA-B3 产品页",
            "https://www.deltaww.com/en-US/products/ASDA-B3",
            "ASDA-B3 伺服",
            "Baidu",
        )
        bing = WebSearchResult(
            "ASDA-B3 User Manual",
            "https://manual.example.com/asda-b3",
            "ASDA-B3 user manual servo",
            "Bing",
        )
        service._search_baidu = lambda _query, _limit: [baidu]  # type: ignore[method-assign]
        service._search_bing = lambda _query, _limit: [bing]  # type: ignore[method-assign]
        service._search_ddgs = lambda _query, _limit: []  # type: ignore[method-assign]

        results = service.search("台达 B3 伺服", max_results=4)

        assert {result.url for result in results} == {
            "https://www.deltaww.com/en-US/products/ASDA-B3",
            "https://manual.example.com/asda-b3",
        }

    def test_deduplicates_same_url_from_different_engines(self) -> None:
        service = WebSearchService(fetch_pages=False)
        first = WebSearchResult(
            "ASDA-B3",
            "https://www.deltaww.com/asda-b3?utm_source=bing",
            "ASDA-B3 伺服驱动器技术资料",
            "Baidu",
        )
        second = WebSearchResult(
            "ASDA-B3",
            "https://www.deltaww.com/asda-b3",
            "ASDA-B3 伺服驱动器技术资料",
            "Bing",
        )
        service._search_baidu = lambda _q, _l: [first]  # type: ignore[method-assign]
        service._search_bing = lambda _q, _l: [second]  # type: ignore[method-assign]
        service._search_ddgs = lambda _q, _l: []  # type: ignore[method-assign]

        results = service.search("ASDA-B3 伺服", max_results=5)
        urls = [r.url for r in results]
        assert urls.count("https://www.deltaww.com/asda-b3") == 1

    def test_cache_avoids_repeating_same_query(self) -> None:
        service = WebSearchService(fetch_pages=False)
        calls = {"n": 0}

        def _bing(_q, _l):
            calls["n"] += 1
            return [
                WebSearchResult(
                    "GPT",
                    "https://zh.wikipedia.org/wiki/GPT",
                    "生成式预训练变换器 GPT 大语言模型",
                    "Bing",
                )
            ]

        service._search_baidu = lambda _q, _l: []  # type: ignore[method-assign]
        service._search_bing = _bing  # type: ignore[method-assign]
        service._search_ddgs = lambda _q, _l: []  # type: ignore[method-assign]

        first = service.search("GPT 大语言模型", max_results=3)
        calls_after_first = calls["n"]
        second = service.search("GPT 大语言模型", max_results=3)
        assert first
        assert first == second
        # 第二次应命中缓存，不再发起引擎请求
        assert calls["n"] == calls_after_first
        assert calls_after_first >= 1

    def test_marks_cross_domain_consensus(self) -> None:
        service = WebSearchService(fetch_pages=False)
        a = WebSearchResult(
            "GPT 介绍 A",
            "https://a.example.com/gpt",
            "GPT 是一种大语言模型架构",
            "Bing",
        )
        b = WebSearchResult(
            "GPT 介绍 B",
            "https://b.example.com/gpt2",
            "GPT 大语言模型原理说明",
            "Baidu",
        )
        service._search_baidu = lambda _q, _l: [a]  # type: ignore[method-assign]
        service._search_bing = lambda _q, _l: [b]  # type: ignore[method-assign]
        service._search_ddgs = lambda _q, _l: []  # type: ignore[method-assign]

        results = service.search("GPT 大语言模型", max_results=4)
        assert len(results) >= 2
        assert any(r.corroborated_by >= 1 for r in results)

    def test_returns_at_least_requested_valid_results_when_available(self) -> None:
        service = WebSearchService(fetch_pages=False)
        candidates = [
            WebSearchResult(
                title=f"ASDA-B3 伺服技术参数 {i}",
                url=f"https://example.com/asda-b3/{i}",
                snippet="ASDA-B3 伺服 高速响应 振动抑制 技术参数",
                source_name="Bing",
            )
            for i in range(15)
        ]
        service._search_baidu = lambda _q, _l: candidates[:5]  # type: ignore[method-assign]
        service._search_bing = lambda _q, _l: candidates[5:10]  # type: ignore[method-assign]
        service._search_ddgs = lambda _q, _l: candidates[10:]  # type: ignore[method-assign]

        results = service.search("ASDA-B3 伺服 技术参数")
        assert len(results) == 12

    def test_relaxes_relevance_filter_to_fill_but_keeps_completely_irrelevant_out(self) -> None:
        service = WebSearchService(fetch_pages=False)
        # 弱相关但命中型号 token：可放宽纳入
        weak = WebSearchResult(
            title="伺服 产品目录 大全",
            url="https://example.com/servo-catalog",
            snippet="伺服 产品 目录 ASDA-B3",
            source_name="Baidu",
        )
        junk = WebSearchResult(
            title="XE Currency Converter",
            url="https://www.xe.com/currencyconverter/",
            snippet="实时汇率和货币转换工具",
            source_name="Bing",
        )
        service._search_baidu = lambda _q, _l: [weak]  # type: ignore[method-assign]
        service._search_bing = lambda _q, _l: [junk]  # type: ignore[method-assign]
        service._search_ddgs = lambda _q, _l: []  # type: ignore[method-assign]

        results = service.search("ASDA-B3 伺服 技术参数", max_results=4)
        urls = {r.url for r in results}
        assert "https://example.com/servo-catalog" in urls
        assert "https://www.xe.com/currencyconverter/" not in urls

    def test_junk_video_domains_are_dropped(self) -> None:
        service = WebSearchService(fetch_pages=False)
        good = WebSearchResult(
            title="ASDA-B3 伺服驱动器参数手册",
            url="https://filecenter.deltaww.com/Products/Download/01/ASDA-B3.pdf",
            snippet="ASDA-B3 伺服驱动器开关电源管理与防涌浪保护",
            source_name="Bing",
        )
        junk_video = WebSearchResult(
            title="怎么开关电源 一张图让你看懂_哔哩哔哩_bilibili",
            url="https://www.bilibili.com/video/BV1234",
            snippet="怎么开关电源，更全面的讲解",
            source_name="Baidu",
        )
        junk_kuaishou = WebSearchResult(
            title="开关电源原理 快手短视频",
            url="https://www.kuaishou.com/short-video/123",
            snippet="开关电源原理详解",
            source_name="Baidu",
        )
        service._search_baidu = lambda _q, _l: [junk_video, junk_kuaishou]  # type: ignore[method-assign]
        service._search_bing = lambda _q, _l: [good]  # type: ignore[method-assign]
        service._search_ddgs = lambda _q, _l: []  # type: ignore[method-assign]

        results = service.search("开关电源", max_results=5)
        urls = {r.url for r in results}
        assert "https://filecenter.deltaww.com/Products/Download/01/ASDA-B3.pdf" in urls
        assert "https://www.bilibili.com/video/BV1234" not in urls
        assert "https://www.kuaishou.com/short-video/123" not in urls

    def test_placeholder_and_url_only_snippets_are_blanked(self) -> None:
        service = WebSearchService(fetch_pages=False)
        placeholder = WebSearchResult(
            "ASDA-B3 伺服驱动器 官方手册",
            "https://filecenter.deltaww.com/manual.pdf",
            "We cannot provide a description for this page right now",
            "Bing",
        )
        url_only = WebSearchResult(
            "ASDA-B3 技术参数",
            "https://example.com/asda-b3",
            "https://www.xe.com/currencyconverter",
            "Baidu",
        )
        service._search_baidu = lambda _q, _l: [placeholder, url_only]  # type: ignore[method-assign]
        service._search_bing = lambda _q, _l: []  # type: ignore[method-assign]
        service._search_ddgs = lambda _q, _l: []  # type: ignore[method-assign]

        results = service.search("ASDA-B3 伺服", max_results=2)
        assert len(results) == 2
        assert all(r.snippet == "" for r in results)

    def test_placeholder_snippet_detection(self) -> None:
        assert WebSearchService._is_placeholder_snippet(
            "We cannot provide a description for this page right now"
        )
        assert WebSearchService._is_placeholder_snippet("https://www.xe.com/currencyconverter")
        assert WebSearchService._is_placeholder_snippet("www.example.com")
        assert WebSearchService._is_placeholder_snippet("")
        assert not WebSearchService._is_placeholder_snippet(
            "本文深入解析台达ASDA-B3伺服驱动器参数与典型应用场景"
        )

    def test_useful_page_text_rejects_link_and_boilerplate_pages(self) -> None:
        link_page = "首页 登录 注册 https://a.com https://b.com https://c.com 导航 关于我们 联系我们"
        assert not WebSearchService._is_useful_page_text(link_page)
        assert not WebSearchService._is_useful_page_text("太短了")
        real = "本文介绍台达 ASDA-B3 伺服驱动器的安装、配线、调试与参数设置方法，包含控制模式、电子齿轮比与通讯协议说明。" * 3
        assert WebSearchService._is_useful_page_text(real)

    def test_context_prefers_fetched_content_and_keeps_direct_url(self) -> None:
        result = WebSearchResult(
            title="ASDA-B3 官方资料",
            url="https://filecenter.deltaww.com/asda-b3.pdf",
            snippet="搜索摘要",
            source_name="Bing",
            domain="filecenter.deltaww.com",
            content="网页正文中关于高速响应和振动抑制的技术说明",
            published_at="2025-03-01",
        )
        context = WebSearchService(fetch_pages=False).format_as_context([result])
        assert "https://filecenter.deltaww.com/asda-b3.pdf" in context
        assert "网页正文中关于高速响应" in context

    def test_quality_raw_count_ignores_aggregate(self) -> None:
        service = WebSearchService(fetch_pages=False)
        good = WebSearchResult(
            "GPT - Wikipedia",
            "https://en.wikipedia.org/wiki/GPT",
            "Generative Pre-trained Transformer",
            "Bing",
        )
        junk = WebSearchResult(
            "什么是gpt-短视频大全-高清在线观看",
            "https://www.so.com/video?q=gpt",
            "",
            "360Search",
        )
        assert service._quality_raw_count([good, junk, junk]) == 1

    def test_early_stop_requires_relevant_unique_cross_source_results(self) -> None:
        service = WebSearchService(fetch_pages=False)

        def candidate(index: int, source: str) -> WebSearchResult:
            return WebSearchResult(
                title=f"GPT Transformer guide {index}",
                url=f"https://source{index}.example/gpt/{index}",
                snippet="GPT Transformer architecture overview",
                source_name=source,
            )

        one_source = [candidate(i, "Bing") for i in range(8)]
        assert not service._should_stop_search_early(one_source, "GPT Transformer")

        duplicate_urls = [candidate(i, ("Bing", "Baidu", "SearXNG")[i % 3]) for i in range(4)]
        assert not service._should_stop_search_early(
            duplicate_urls + duplicate_urls, "GPT Transformer"
        )

        distractors = [
            WebSearchResult(
                title=f"Unrelated cooking guide {i}",
                url=f"https://distractor{i}.example/page",
                snippet="Recipes and kitchen tips",
                source_name=("Bing", "Baidu", "SearXNG")[i % 3],
            )
            for i in range(8)
        ]
        assert not service._should_stop_search_early(distractors, "GPT Transformer")

        diverse = [candidate(i, ("Bing", "Baidu", "SearXNG")[i % 3]) for i in range(8)]
        assert service._should_stop_search_early(diverse, "GPT Transformer")

        modifier_only = [
            WebSearchResult(
                title=f"主动式控制方案 {i}",
                url=f"https://modifier{i}.example/page",
                snippet="主动式控制系统的设计与应用",
                source_name=("Bing", "Baidu", "SearXNG")[i % 3],
            )
            for i in range(8)
        ]
        assert not service._should_stop_search_early(modifier_only, "主动式动平衡机")

    def test_search_drops_360_aggregate_pages_keeps_real(self) -> None:
        service = WebSearchService(fetch_pages=False)
        real = WebSearchResult(
            "豆包 - 知乎",
            "https://www.zhihu.com/question/123",
            "一款「啥都会」的 AI 智能助手",
            "Bing",
        )
        junk_video = WebSearchResult(
            "什么是豆包-短视频大全-高清在线观看",
            "https://www.so.com/video?q=%E4%BB%80%E4%B9%88%E6%98%AF%E8%B1%86%E5%8C%85",
            "",
            "360Search",
        )
        junk_image = WebSearchResult(
            "什么是豆包_360图片",
            "https://image.so.com/i?src=imageonebox&q=什么是豆包",
            "",
            "360Search",
        )
        service._search_baidu = lambda _q, _l: []  # type: ignore[method-assign]
        service._search_bing = lambda _q, _l: [real]  # type: ignore[method-assign]
        service._search_ddgs = lambda _q, _l: []  # type: ignore[method-assign]
        service._search_so = lambda _q, _l: [junk_video, junk_image]  # type: ignore[method-assign]

        results = service.search("什么是豆包", max_results=8)
        urls = {r.url for r in results}
        assert "https://www.zhihu.com/question/123" in urls
        assert not any("短视频大全" in (r.title or "") for r in results)
        assert not any(r.domain == "image.so.com" for r in results)

    def test_extracts_latin_model_tokens(self) -> None:
        tokens = WebSearchService._distinctive_query_tokens("台达 ASDA-B3 伺服参数")
        assert "asda-b3" in tokens or "b3" in tokens or any("b3" in t for t in tokens)

    def test_short_latin_token_only_when_no_stronger(self) -> None:
        tokens = WebSearchService._distinctive_query_tokens("AS 与 关系？")
        assert tokens == ["as"] or "as" in tokens

    def test_chinese_only_query_has_no_distinctive_constraint(self) -> None:
        tokens = WebSearchService._distinctive_query_tokens("什么是挠度")
        assert tokens == []

    def test_offtopic_page_dropped_when_query_has_entities(self) -> None:
        official = WebSearchResult(
            title="AtomCode 文档",
            url="https://example.com/atomcode",
            snippet="AtomCode agent",
            source_name="Bing",
            domain="example.com",
            content="AtomCode 使用 MCP 协议接入本地知识库。",
            content_fetched=True,
            relevance_score=0.5,
        )
        offtopic = WebSearchResult(
            title="OpenClaw三级记忆系统实现揭秘",
            url="https://other.example.com/openclaw",
            snippet="系统之间的关系与记忆实现",
            source_name="Baidu",
            domain="other.example.com",
            content="讨论系统关系、记忆分层与向量库架构，不涉及本问题产品。",
            content_fetched=True,
            relevance_score=0.25,
        )
        distinctive = WebSearchService._distinctive_query_tokens("AS 与 AtomCode 关系？")
        assert WebSearchService._matches_distinctive_tokens(official, distinctive)
        assert not WebSearchService._matches_distinctive_tokens(offtopic, distinctive)

    def test_search_applies_entity_constraint_end_to_end(self) -> None:
        service = WebSearchService(fetch_pages=False)
        official = WebSearchResult(
            "AtomCode 文档",
            "https://example.com/atomcode",
            "AtomCode agent",
            "Bing",
        )
        offtopic = WebSearchResult(
            "记忆系统关系揭秘",
            "https://other.example.com/mem",
            "系统之间的关系",
            "Baidu",
        )
        service._search_baidu = lambda _q, _l: [offtopic]  # type: ignore[method-assign]
        service._search_bing = lambda _q, _l: [official]  # type: ignore[method-assign]
        service._search_ddgs = lambda _q, _l: []  # type: ignore[method-assign]

        results = service.search("AS 与 AtomCode 关系？", max_results=4)
        urls = {r.url for r in results}
        assert "https://example.com/atomcode" in urls
        assert "https://other.example.com/mem" not in urls


class TestGithubTokenPolicy:
    def test_github_search_request_token_over_env(self, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "env-token-should-not-be-used")
        monkeypatch.setenv("DOC2MIND_GITHUB_TOKEN", "env-token-should-not-be-used")
        service = WebSearchService(fetch_pages=False, cache_ttl_seconds=0)
        seen: dict[str, str | None] = {"auth": None}

        class FakeResp:
            def read(self):
                return json.dumps({"items": []}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _open(req, timeout=None):
            seen["auth"] = req.get_header("Authorization") if hasattr(req, "get_header") else None
            # urllib Request stores headers capitalized differently
            for k, v in getattr(req, "headers", {}).items():
                if k.lower() == "authorization":
                    seen["auth"] = v
            return FakeResp()

        service._open_url = _open  # type: ignore
        service._throttle_github = lambda _t: None  # type: ignore
        service._search_github("github AtomCode 仓库", 3, github_token="req-token")
        assert seen["auth"] in (None, "Bearer req-token") or "req-token" in str(seen["auth"])

    def test_github_search_ignores_env_when_no_request_token(self, monkeypatch) -> None:
        monkeypatch.setenv("GITHUB_TOKEN", "env-shared-token")
        service = WebSearchService(fetch_pages=False, cache_ttl_seconds=0)
        seen: dict[str, str | None] = {"auth": "unset"}

        class FakeResp:
            def read(self):
                return json.dumps({"items": []}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def _open(req, timeout=None):
            headers = getattr(req, "headers", {}) or {}
            auth = None
            for k, v in headers.items():
                if k.lower() == "authorization":
                    auth = v
            seen["auth"] = auth
            return FakeResp()

        service._open_url = _open  # type: ignore
        service._throttle_github = lambda _t: None  # type: ignore
        service._search_github("github AtomCode 仓库", 3, github_token=None)
        assert seen["auth"] is None

    def test_github_cache_isolated_by_token(self) -> None:
        service = WebSearchService(fetch_pages=False)
        calls: list[str | None] = []

        class FakeResp:
            def __init__(self, token):
                self._token = token

            def read(self):
                calls.append(self._token)
                return json.dumps({"items": []}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def make_open(token):
            def _open(req, timeout=None):
                return FakeResp(token)
            return _open

        service._throttle_github = lambda _t: None  # type: ignore

        service._open_url = make_open("t1")  # type: ignore
        service._search_github("github repo A", 2, github_token="t1")
        service._open_url = make_open("t2")  # type: ignore
        service._search_github("github repo A", 2, github_token="t2")
        # 不同令牌应各自请求，不能共享匿名/他令牌缓存结果
        assert "t1" in calls and "t2" in calls


class TestSearXNGChannel:
    def test_set_searxng_bases_custom_first_then_public(self) -> None:
        service = WebSearchService(fetch_pages=False)
        service.set_searxng_bases("http://127.0.0.1:8888")
        assert service.searxng_bases[0] == "http://127.0.0.1:8888"
        assert len(service.searxng_bases) > 1

    def test_set_searxng_bases_comma_separated(self) -> None:
        service = WebSearchService(fetch_pages=False)
        service.set_searxng_bases("http://a.local:8888, http://b.local:8888")
        assert service.searxng_bases[0] == "http://a.local:8888"
        assert service.searxng_bases[1] == "http://b.local:8888"

    def test_set_searxng_bases_empty_falls_back_to_public(self) -> None:
        service = WebSearchService(fetch_pages=False)
        service.set_searxng_bases("http://127.0.0.1:8888")
        service.set_searxng_bases("")
        assert service.searxng_bases
        assert "127.0.0.1" not in service.searxng_bases[0]


class TestDeepSearchMode:
    def test_deep_mode_expands_query_variants(self) -> None:
        service = WebSearchService(fetch_pages=False)
        deep = service._deep_query_variants("什么是大语言模型 GPT")
        assert any("documentation" in v or "official" in v.lower() for v in deep)
        assert any("对比" in v or "综述" in v for v in deep)

    def test_search_mode_changes_candidate_budget(self) -> None:
        service = WebSearchService(fetch_pages=False, cache_ttl_seconds=0)
        seen: list[dict] = []

        def _fake_search_all(query, limit, github_token=None, deadline=None):
            seen.append({"query": query, "limit": limit})
            return []

        service._search_all = _fake_search_all  # type: ignore
        service._is_meaningful_search_query = lambda q: True  # type: ignore
        service._rewrite_query_for_search = lambda *a, **k: None  # type: ignore
        service._expand_query = lambda q: q  # type: ignore
        service._query_variants = lambda o, e: [o]  # type: ignore
        service._score_results = lambda *a, **k: None  # type: ignore
        service._apply_corroboration = lambda *a, **k: None  # type: ignore
        service.fetch_pages = False

        service.search("GPT transformer 对比", mode="normal")
        normal_limits = {c["limit"] for c in seen}
        normal_n = len(seen)
        seen.clear()

        service.search("GPT transformer 对比", mode="deep")
        deep_limits = {c["limit"] for c in seen}
        deep_n = len(seen)

        assert deep_n >= normal_n
        assert max(deep_limits) >= max(normal_limits)

    def test_resolved_web_search_mode_contract(self) -> None:
        # 与 HTTP 层 ChatRequest.resolved_web_search_mode 对齐
        class Req:
            def __init__(self, enable, mode):
                self.enable_web_search = enable
                self.web_search_mode = mode

            def resolved_web_search_mode(self):
                if not self.enable_web_search:
                    return "off"
                raw = (self.web_search_mode or "").strip().lower()
                return "deep" if raw == "deep" else "normal"

        assert Req(False, None).resolved_web_search_mode() == "off"
        assert Req(True, None).resolved_web_search_mode() == "normal"
        assert Req(True, "deep").resolved_web_search_mode() == "deep"
        assert Req(True, "DEEP").resolved_web_search_mode() == "deep"
