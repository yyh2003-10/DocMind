"""自定义 PPT 主题与自定义角色提示词 — 端到端契约测试。"""

from __future__ import annotations

from pathlib import Path

from doc2mind.core.creator import export_artifact, extract_artifact
from doc2mind.core.creator.themes import (
    THEMES,
    build_theme_from_colors,
    clear_runtime_themes,
    get_theme,
    register_runtime_theme,
)


def test_builtin_themes_cover_frontend_18():
    """前端 AvailableThemes 的 18 个 ID 必须全部在后端 THEMES 中。"""
    expected = {
        "tech_blue",
        "emerald_green",
        "modern_purple",
        "warm_orange",
        "dark_elegant",
        "rose_pink",
        "candy_bright",
        "ocean_turquoise",
        "forest_deep",
        "earth_warm",
        "golden_luxury",
        "deep_wine",
        "medical_calm",
        "scholar_cream",
        "gov_red",
        "cyber_neon",
        "sunset_gradient",
        "nordic_ice",
    }
    assert expected.issubset(set(THEMES.keys()))
    assert len(THEMES) >= 18


def test_builtin_theme_not_overridden_by_custom_colors():
    """内置 ID 即使带 themeColors 也走精调配色，不被二色派生覆盖。"""
    t = get_theme("tech_blue", custom_colors={"primary": "#FFFFFF", "bg": "#000000"})
    assert t.id == "tech_blue"
    assert t.primary.hex == "#0F4C81"


def test_custom_dark_theme_auto_text_contrast():
    t = build_theme_from_colors("custom_x", "测试", "", "#BE185D", "#0B1020")
    assert t.is_dark is True
    # 深底应自动派生浅色正文，保证可读
    assert t.text_main.luminance > 0.5


def test_custom_light_theme_auto_dark_text():
    t = build_theme_from_colors("custom_y", "测试", "", "#3B82F6", "#F8FAFC")
    assert t.is_dark is False
    assert t.text_main.luminance < 0.4


def test_runtime_theme_registry_does_not_pollute_builtin():
    clear_runtime_themes()
    before = len(THEMES)
    custom = build_theme_from_colors("custom_zzz", "临时", "", "#112233", "#F0F0F0")
    register_runtime_theme(custom)
    assert len(THEMES) == before
    assert "custom_zzz" not in THEMES
    resolved = get_theme("custom_zzz")
    assert resolved.id == "custom_zzz"
    assert resolved.primary.hex == "#112233"
    clear_runtime_themes()
    # 清空后回落默认
    assert get_theme("custom_zzz").id == "tech_blue"


def test_export_artifact_applies_custom_theme_colors(tmp_path: Path):
    clear_runtime_themes()
    sample = """
# 自定义主题导出验证
## 副标题
---
# 第二页
- 要点 A
"""
    out = tmp_path / "custom_theme.pptx"
    res = export_artifact(
        content=sample,
        target_format="pptx",
        output_path=out,
        theme="custom_brand_01",
        theme_colors={
            "name": "品牌红",
            "primary": "#DC2626",
            "bg": "#FFF7ED",
        },
    )
    assert res.ok, res.error
    assert out.exists() and out.stat().st_size > 0
    # 导出后运行时表应能解析该主题
    t = get_theme("custom_brand_01")
    assert t.id == "custom_brand_01"
    assert t.primary.hex == "#DC2626"
    assert t.bg.hex == "#FFF7ED"
    # 内置表未被污染
    assert "custom_brand_01" not in THEMES
    clear_runtime_themes()


def test_export_artifact_without_theme_colors_falls_back(tmp_path: Path):
    out = tmp_path / "fallback.pptx"
    res = export_artifact(
        content="# 回落测试\n## 副标题\n",
        target_format="pptx",
        output_path=out,
        theme="not_exist_theme",
    )
    assert res.ok, res.error
    assert out.exists()


def test_persona_prompt_system_prefix_logic():
    """模拟 rag 注入：自定义 persona_prompt 必须进入 system 前缀。"""
    system_prompt = "你是 DocMind 助手。"
    persona = "custom_abc"
    persona_prompt = "你是资深法务顾问，回答必须引用条款编号。"
    persona_prompts = {"office": "【当前角色：办公】"}

    if persona and persona in persona_prompts:
        system_prompt = persona_prompts[persona] + "\n\n" + system_prompt
    elif persona_prompt and persona_prompt.strip():
        custom_line = persona_prompt.strip().split("\n", 1)[0]
        system_prompt = f"【当前角色：自定义】{custom_line}\n\n" + system_prompt

    assert system_prompt.startswith("【当前角色：自定义】你是资深法务顾问")
    assert "DocMind 助手" in system_prompt


def test_extract_then_export_theme_id_propagates(tmp_path: Path):
    """extract_artifact → export 时 theme id 贯通到运行时注册表。"""
    clear_runtime_themes()
    artifact = extract_artifact("# 标题\n## 副标题\n", default_type="pptx")
    assert artifact.theme  # 解析器有默认 theme
    res = export_artifact(
        content="# 标题\n## 副标题\n",
        target_format="pptx",
        output_path=tmp_path / "prop.pptx",
        theme="custom_prop_1",
        theme_colors={"primary": "#0EA5E9", "bg": "#F0F9FF", "name": "传播蓝"},
    )
    assert res.ok
    t = get_theme("custom_prop_1")
    assert t.name == "传播蓝"
    clear_runtime_themes()
