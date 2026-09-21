"""/v1/chat providerConfig 按请求携带服务商配置的测试。

覆盖：
- 未携带 providerConfig → 用后端全局配置（helper 返回 None）
- provider 为 none/空 → 视为未指定（返回 None）
- 携带完整配置 → 用该配置构造临时 Settings/LLM 客户端（provider/key/url/模型/温度/token）
- 字段缺省时沿用后端全局配置
"""

from __future__ import annotations

from unittest.mock import patch

from doc2mind.core.config import Settings
from doc2mind.server.http import ChatRequest, ProviderConfigIn, _build_llm_client_from_provider


class TestBuildLlmClientFromProvider:
    def test_no_provider_config_returns_none(self) -> None:
        """未携带 providerConfig → 返回 None，用后端全局配置。"""
        req = ChatRequest(query="hi")
        assert _build_llm_client_from_provider(req, Settings()) is None

    def test_provider_none_returns_none(self) -> None:
        """provider 为 none/空 → 视为未指定，返回 None。"""
        req = ChatRequest(query="hi", provider_config=ProviderConfigIn(provider="none"))
        assert _build_llm_client_from_provider(req, Settings()) is None

        req2 = ChatRequest(query="hi", provider_config=ProviderConfigIn(provider="  "))
        assert _build_llm_client_from_provider(req2, Settings()) is None

    def test_full_config_constructs_temp_client(self) -> None:
        """携带完整配置 → 用该配置构造临时 Settings（复用 get_llm_client）。"""
        req = ChatRequest(
            query="hi",
            provider_config=ProviderConfigIn(
                provider="openai",
                api_key="sk-x",
                base_url="https://api.deepseek.com/v1",
                model="deepseek-chat",
                temperature=0.3,
                max_tokens=2048,
            ),
        )
        with patch("doc2mind.server.http.get_llm_client") as mock_get:
            mock_get.return_value = object()
            client = _build_llm_client_from_provider(req, Settings())
            assert client is not None
            tmp = mock_get.call_args.args[0]
            assert tmp.llm_provider == "openai"
            assert tmp.llm_api_key == "sk-x"
            assert tmp.llm_base_url == "https://api.deepseek.com/v1"
            assert tmp.llm_model == "deepseek-chat"
            assert tmp.llm_temperature == 0.3
            assert tmp.llm_max_tokens == 2048

    def test_missing_fields_fall_back_to_global(self) -> None:
        """字段缺省（None/空）时沿用后端全局配置，不覆盖。"""
        req = ChatRequest(
            query="hi",
            provider_config=ProviderConfigIn(
                provider="openai",
                api_key="sk-new",
                # base_url/model/temperature/max_tokens 缺省
            ),
        )
        s = Settings(
            llm_provider="openai",
            llm_api_key="sk-global",
            llm_base_url="https://api.global.com/v1",
            llm_model="global-model",
            llm_temperature=0.5,
            llm_max_tokens=4096,
        )
        with patch("doc2mind.server.http.get_llm_client") as mock_get:
            mock_get.return_value = object()
            client = _build_llm_client_from_provider(req, s)
            assert client is not None
            tmp = mock_get.call_args.args[0]
            # 显式传入的覆盖
            assert tmp.llm_provider == "openai"
            assert tmp.llm_api_key == "sk-new"
            # 缺省字段沿用全局
            assert tmp.llm_base_url == "https://api.global.com/v1"
            assert tmp.llm_model == "global-model"
            assert tmp.llm_temperature == 0.5
            assert tmp.llm_max_tokens == 4096
