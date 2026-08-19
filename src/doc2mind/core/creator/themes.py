"""PPT 演示文稿企业级专业主题配色方案库。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RgbColor:
    r: int
    g: int
    b: int

    def to_hex(self) -> str:
        return f"#{self.r:02X}{self.g:02X}{self.b:02X}"

    @property
    def hex(self) -> str:
        return self.to_hex()


@dataclass(frozen=True)
class PptTheme:
    id: str
    name: str
    description: str
    primary: RgbColor      # 主品牌色（用于封面大标题、卡片强调顶条、关键图标）
    secondary: RgbColor    # 次主色（用于副标题、辅助几何块）
    accent: RgbColor       # 强调色（用于高亮数字、小徽章、重要标签）
    bg: RgbColor           # 画布底色
    card_bg: RgbColor      # 卡片背景色
    card_border: RgbColor  # 卡片边框色
    text_main: RgbColor    # 主文字色（加粗大字、正文）
    text_muted: RgbColor   # 弱化辅助文字色
    is_dark: bool = False  # 是否暗黑主题

    @property
    def primary_light(self) -> RgbColor:
        """主色浅色调（用于卡片底色/高亮区块背景）。"""
        if self.is_dark:
            return self.card_bg
        r = int(self.primary.r * 0.12 + 255 * 0.88)
        g = int(self.primary.g * 0.12 + 255 * 0.88)
        b = int(self.primary.b * 0.12 + 255 * 0.88)
        return RgbColor(min(255, max(0, r)), min(255, max(0, g)), min(255, max(0, b)))

    @property
    def text_title(self) -> RgbColor:
        """标题文字色（别名 text_main）。"""
        return self.text_main

    @property
    def text_body(self) -> RgbColor:
        """正文文字色（别名 text_main）。"""
        return self.text_main


# 5 套企业级品牌调色板
THEMES: dict[str, PptTheme] = {
    "tech_blue": PptTheme(
        id="tech_blue",
        name="🔷 科技商务蓝",
        description="深邃科技、稳健商务、严谨架构汇报首选",
        primary=RgbColor(15, 76, 129),       # #0F4C81 Classic Navy Blue
        secondary=RgbColor(30, 136, 229),    # #1E88E5 Vivid Tech Blue
        accent=RgbColor(0, 180, 216),        # #00B4D8 Cyan Accent
        bg=RgbColor(246, 248, 252),          # #F6F8FC Soft Gray-Blue
        card_bg=RgbColor(255, 255, 255),     # Pure White
        card_border=RgbColor(220, 228, 240), # Light Blue-Gray Border
        text_main=RgbColor(26, 38, 57),      # Deep Navy Dark Text
        text_muted=RgbColor(100, 116, 139),  # Slate Gray
        is_dark=False,
    ),
    "emerald_green": PptTheme(
        id="emerald_green",
        name="🌿 清新自然绿",
        description="战略规划、ESG 汇报、医疗健康与教育培训",
        primary=RgbColor(27, 77, 62),        # #1B4D3E Deep Emerald
        secondary=RgbColor(46, 139, 87),     # #2E8B57 Sea Green
        accent=RgbColor(245, 158, 11),       # #F59E0B Amber
        bg=RgbColor(244, 247, 245),          # #F4F7F5 Soft Mint White
        card_bg=RgbColor(255, 255, 255),     # Pure White
        card_border=RgbColor(218, 230, 222), # Light Green-Gray Border
        text_main=RgbColor(20, 42, 33),      # Forest Dark Text
        text_muted=RgbColor(87, 107, 98),    # Muted Green Gray
        is_dark=False,
    ),
    "modern_purple": PptTheme(
        id="modern_purple",
        name="🟣 AI 智能紫",
        description="人工智能、前沿创新、未来科技与数字化转型",
        primary=RgbColor(74, 20, 140),       # #4A148C Deep Indigo Purple
        secondary=RgbColor(124, 77, 255),    # #7C4DFF Vivid Purple
        accent=RgbColor(0, 229, 255),        # #00E5FF Neon Cyan
        bg=RgbColor(247, 245, 253),          # #F7F5FD Soft Lavender White
        card_bg=RgbColor(255, 255, 255),     # Pure White
        card_border=RgbColor(226, 220, 245), # Light Lavender Border
        text_main=RgbColor(34, 24, 53),      # Deep Violet Dark Text
        text_muted=RgbColor(112, 100, 136),  # Muted Purple Gray
        is_dark=False,
    ),
    "warm_orange": PptTheme(
        id="warm_orange",
        name="🔶 活力暖橙红",
        description="商业营销、产品发布、运营战报与激励总结",
        primary=RgbColor(183, 50, 37),       # #B73225 Brick Crimson
        secondary=RgbColor(245, 124, 0),     # #F57C00 Bright Orange
        accent=RgbColor(41, 121, 255),       # #2979FF Contrast Blue
        bg=RgbColor(254, 248, 246),          # #FEF8F6 Warm Light Sand
        card_bg=RgbColor(255, 255, 255),     # Pure White
        card_border=RgbColor(243, 224, 218), # Warm Peach Border
        text_main=RgbColor(46, 26, 23),      # Dark Espresso Text
        text_muted=RgbColor(125, 99, 93),    # Muted Terracotta
        is_dark=False,
    ),
    "dark_elegant": PptTheme(
        id="dark_elegant",
        name="⬛ 极简暗黑风",
        description="高端发布会、极客科技、夜间演示与现代沉浸感",
        primary=RgbColor(96, 165, 250),      # #60A5FA Electric Sky Blue
        secondary=RgbColor(147, 197, 253),   # #93C5FD Soft Sky Blue
        accent=RgbColor(52, 211, 153),       # #34D399 Emerald Accent
        bg=RgbColor(24, 26, 32),             # #181A20 Deep Charcoal Black
        card_bg=RgbColor(34, 37, 45),        # #22252D Dark Elevated Card
        card_border=RgbColor(51, 56, 68),    # #333844 Dark Border
        text_main=RgbColor(243, 244, 246),   # #F3F4F6 Crisp White Text
        text_muted=RgbColor(156, 163, 175),  # #9CA3AF Silver Gray
        is_dark=True,
    ),
}


def get_theme(theme_id: str | None) -> PptTheme:
    """获取指定主题，默认返回科技商务蓝。"""
    if not theme_id:
        return THEMES["tech_blue"]
    clean_id = theme_id.lower().strip().replace("-", "_")
    return THEMES.get(clean_id, THEMES["tech_blue"])
