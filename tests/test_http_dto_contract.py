"""HTTP DTO 契约：WPF camelCase 字段必须能被 pydantic 正确解析。"""

from __future__ import annotations

from doc2mind.server.http import ChatRequest, CreativeExportRequest


def test_chat_request_accepts_camelcase_persona_prompt():
    req = ChatRequest.model_validate(
        {
            "query": "你好",
            "persona": "custom_abc123",
            "personaPrompt": "你是资深法务顾问。",
            "enableWebSearch": False,
        }
    )
    assert req.persona == "custom_abc123"
    assert req.persona_prompt == "你是资深法务顾问。"


def test_chat_request_accepts_snake_case_persona_prompt():
    req = ChatRequest.model_validate(
        {
            "query": "你好",
            "persona": "custom_abc123",
            "persona_prompt": "你是资深法务顾问。",
        }
    )
    assert req.persona_prompt == "你是资深法务顾问。"


def test_export_request_accepts_theme_colors_camel():
    req = CreativeExportRequest.model_validate(
        {
            "content": "# 标题\n## 副标题\n",
            "format": "pptx",
            "theme": "custom_brand",
            "themeColors": {"name": "品牌红", "primary": "#DC2626", "bg": "#FFF7ED"},
        }
    )
    assert req.theme == "custom_brand"
    assert req.theme_colors is not None
    assert req.theme_colors["primary"] == "#DC2626"


def test_export_request_accepts_theme_colors_snake():
    req = CreativeExportRequest.model_validate(
        {
            "content": "# 标题",
            "theme": "custom_brand",
            "theme_colors": {"primary": "#112233", "bg": "#EEEEEE"},
        }
    )
    assert req.theme_colors["primary"] == "#112233"


def test_export_request_theme_colors_optional():
    req = CreativeExportRequest.model_validate(
        {"content": "# 标题", "theme": "tech_blue"}
    )
    assert req.theme_colors is None
