"""上游过载错误识别与对话重试契约。"""

from __future__ import annotations

from doc2mind.core.llm.base import LLMError, is_provider_overloaded_error


class TestOverloadDetection:
    def test_detects_nvidia_temporarily_overloaded(self) -> None:
        err = LLMError("OpenAI API 流式调用失败: Service temporarily overloaded")
        assert is_provider_overloaded_error(err) is True

    def test_detects_status_503(self) -> None:
        class FakeHTTPStatus(Exception):
            status_code = 503

        assert is_provider_overloaded_error(FakeHTTPStatus("boom")) is True

    def test_plain_auth_error_not_overload(self) -> None:
        err = LLMError("OpenAI API 流式调用失败（API Key 无效或无权限）: 401")
        assert is_provider_overloaded_error(err) is False

    def test_none_is_false(self) -> None:
        assert is_provider_overload_none() is False


def is_provider_overload_none() -> bool:
    return is_provider_overloaded_error(None)
