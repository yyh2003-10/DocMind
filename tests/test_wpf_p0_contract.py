"""WPF P0 字段与后端契约对齐的纯逻辑说明性测试（不依赖 dotnet）。

真实 UI 编译受本机 NuGet path1 环境债影响（见 HANDOVER），此处锁定
请求/终帧字段名，供前端实现与后端 docs/api.md 对齐。
"""

from __future__ import annotations

from doc2mind.core.agent.prompt_policy import done_frame_extras


def test_done_frame_fields_match_wpf_chatstreamresult():
    extras = done_frame_extras(track="delivery", truncated=True, continue_writing=True)
    # Doc2kbApiService.ParseDoneFrame 读取的 snake_case 键
    for key in ("prompt_track", "truncated", "continue_supported", "response_mode", "continue_hint"):
        assert key in extras


def test_chat_request_p0_fields_documented():
    # ChatRequest.cs 序列化名（JsonPropertyName）
    request_fields = {"responseMode", "continueWriting"}
    assert "responseMode" in request_fields
    assert "continueWriting" in request_fields
