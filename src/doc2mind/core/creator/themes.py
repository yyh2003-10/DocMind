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

    @property
    def luminance(self) -> float:
        """相对亮度（0=黑，1=白），用于自动判定浅/深主题。"""
        def _ch(c: int) -> float:
            s = c / 255.0
            return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

        return 0.2126 * _ch(self.r) + 0.7152 * _ch(self.g) + 0.0722 * _ch(self.b)

    def lighten(self, ratio: float) -> RgbColor:
        r = int(self.r + (255 - self.r) * ratio)
        g = int(self.g + (255 - self.g) * ratio)
        b = int(self.b + (255 - self.b) * ratio)
        return RgbColor(min(255, max(0, r)), min(255, max(0, g)), min(255, max(0, b)))

    def darken(self, ratio: float) -> RgbColor:
        return RgbColor(
            max(0, int(self.r * (1 - ratio))),
            max(0, int(self.g * (1 - ratio))),
            max(0, int(self.b * (1 - ratio))),
        )


def parse_hex_color(value: str | None, fallback: RgbColor | None = None) -> RgbColor:
    """解析 #RGB / #RRGGBB；失败时返回 fallback 或中性灰。"""
    if fallback is None:
        fallback = RgbColor(100, 116, 139)
    if not value or not str(value).strip():
        return fallback
    s = str(value).strip().lstrip("#")
    try:
        if len(s) == 3:
            s = "".join(ch * 2 for ch in s)
        if len(s) != 6:
            return fallback
        return RgbColor(int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
    except ValueError:
        return fallback


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


def build_theme_from_colors(
    theme_id: str,
    name: str,
    description: str,
    primary_hex: str,
    bg_hex: str,
    *,
    secondary_hex: str | None = None,
    accent_hex: str | None = None,
    card_bg_hex: str | None = None,
    card_border_hex: str | None = None,
    text_main_hex: str | None = None,
    text_muted_hex: str | None = None,
    is_dark: bool | None = None,
) -> PptTheme:
    """由主色/底色推导完整 PPT 主题；其余色未提供时按对比度自动派生。"""
    primary = parse_hex_color(primary_hex, RgbColor(15, 76, 129))
    bg = parse_hex_color(bg_hex, RgbColor(246, 248, 252))
    dark = bg.luminance < 0.35 if is_dark is None else is_dark

    secondary = (
        parse_hex_color(secondary_hex)
        if secondary_hex
        else primary.lighten(0.35) if not dark else primary.lighten(0.25)
    )
    accent = (
        parse_hex_color(accent_hex)
        if accent_hex
        else primary.lighten(0.55) if not dark else secondary.lighten(0.2)
    )
    card_bg = (
        parse_hex_color(card_bg_hex)
        if card_bg_hex
        else RgbColor(255, 255, 255) if not dark else bg.lighten(0.08)
    )
    card_border = (
        parse_hex_color(card_border_hex)
        if card_border_hex
        else bg.darken(0.08) if not dark else bg.lighten(0.16)
    )
    text_main = (
        parse_hex_color(text_main_hex)
        if text_main_hex
        else RgbColor(20, 24, 32) if not dark else RgbColor(243, 244, 246)
    )
    text_muted = (
        parse_hex_color(text_muted_hex)
        if text_muted_hex
        else RgbColor(100, 116, 139) if not dark else RgbColor(156, 163, 175)
    )

    return PptTheme(
        id=theme_id,
        name=name or theme_id,
        description=description or "",
        primary=primary,
        secondary=secondary,
        accent=accent,
        bg=bg,
        card_bg=card_bg,
        card_border=card_border,
        text_main=text_main,
        text_muted=text_muted,
        is_dark=dark,
    )


# 18 套企业级品牌调色板（与 WPF 前端 AvailableThemes 一一对应）
THEMES: dict[str, PptTheme] = {
    "tech_blue": PptTheme(
        id="tech_blue",
        name="🔷 科技商务蓝",
        description="深邃科技、稳健商务、严谨架构汇报首选",
        primary=RgbColor(15, 76, 129),
        secondary=RgbColor(30, 136, 229),
        accent=RgbColor(0, 180, 216),
        bg=RgbColor(246, 248, 252),
        card_bg=RgbColor(255, 255, 255),
        card_border=RgbColor(220, 228, 240),
        text_main=RgbColor(26, 38, 57),
        text_muted=RgbColor(100, 116, 139),
        is_dark=False,
    ),
    "emerald_green": PptTheme(
        id="emerald_green",
        name="🌿 清新自然绿",
        description="战略规划、ESG 汇报、医疗健康与教育培训",
        primary=RgbColor(27, 77, 62),
        secondary=RgbColor(46, 139, 87),
        accent=RgbColor(245, 158, 11),
        bg=RgbColor(244, 247, 245),
        card_bg=RgbColor(255, 255, 255),
        card_border=RgbColor(218, 230, 222),
        text_main=RgbColor(20, 42, 33),
        text_muted=RgbColor(87, 107, 98),
        is_dark=False,
    ),
    "modern_purple": PptTheme(
        id="modern_purple",
        name="🟣 AI 智能紫",
        description="人工智能、前沿创新、未来科技与数字化转型",
        primary=RgbColor(74, 20, 140),
        secondary=RgbColor(124, 77, 255),
        accent=RgbColor(0, 229, 255),
        bg=RgbColor(247, 245, 253),
        card_bg=RgbColor(255, 255, 255),
        card_border=RgbColor(226, 220, 245),
        text_main=RgbColor(34, 24, 53),
        text_muted=RgbColor(112, 100, 136),
        is_dark=False,
    ),
    "warm_orange": PptTheme(
        id="warm_orange",
        name="🔶 活力暖橙红",
        description="商业营销、产品发布、运营战报与激励总结",
        primary=RgbColor(183, 50, 37),
        secondary=RgbColor(245, 124, 0),
        accent=RgbColor(41, 121, 255),
        bg=RgbColor(254, 248, 246),
        card_bg=RgbColor(255, 255, 255),
        card_border=RgbColor(243, 224, 218),
        text_main=RgbColor(46, 26, 23),
        text_muted=RgbColor(125, 99, 93),
        is_dark=False,
    ),
    "dark_elegant": PptTheme(
        id="dark_elegant",
        name="⬛ 极简暗黑风",
        description="高端发布会、极客科技、夜间演示与现代沉浸感",
        primary=RgbColor(96, 165, 250),
        secondary=RgbColor(147, 197, 253),
        accent=RgbColor(52, 211, 153),
        bg=RgbColor(24, 26, 32),
        card_bg=RgbColor(34, 37, 45),
        card_border=RgbColor(51, 56, 68),
        text_main=RgbColor(243, 244, 246),
        text_muted=RgbColor(156, 163, 175),
        is_dark=True,
    ),
    "rose_pink": build_theme_from_colors(
        "rose_pink", "🩷 浪漫玫瑰粉", "时尚品牌、产品发布与活动策划",
        "#BE185D", "#FDF2F8",
    ),
    "candy_bright": build_theme_from_colors(
        "candy_bright", "🍬 糖果明快", "活泼创意、团队协作与内部培训",
        "#C026D3", "#FDF4FF",
    ),
    "ocean_turquoise": build_theme_from_colors(
        "ocean_turquoise", "🐬 海洋碧蓝", "清新通透、科技产品与海洋生态",
        "#0E7490", "#F0FDFA",
    ),
    "forest_deep": build_theme_from_colors(
        "forest_deep", "🌲 深林墨绿", "自然环保、农业与可持续发展",
        "#065F46", "#ECFDF5",
    ),
    "earth_warm": build_theme_from_colors(
        "earth_warm", "🪨 大地暖岩", "建筑材料、地质勘探与户外运动",
        "#78350F", "#FFFBEB",
    ),
    "golden_luxury": build_theme_from_colors(
        "golden_luxury", "✨ 奢华金棕", "高端金融、奢侈品与年度盛典",
        "#92400E", "#FFFBEB",
    ),
    "deep_wine": build_theme_from_colors(
        "deep_wine", "🍷 醇酿酒红", "高端商务晚宴、品牌联名与尊享活动",
        "#7F1D1D", "#FEF2F2",
    ),
    "medical_calm": build_theme_from_colors(
        "medical_calm", "🏥 医疗清蓝", "医疗健康、临床研究与生命科学",
        "#1E40AF", "#EFF6FF",
    ),
    "scholar_cream": build_theme_from_colors(
        "scholar_cream", "📚 学术象牙", "论文答辩、学术会议与期刊发表",
        "#78350F", "#FFFBEB",
    ),
    "gov_red": build_theme_from_colors(
        "gov_red", "🏛️ 庄重中国红", "政府公文、党建汇报与公共服务",
        "#991B1B", "#FEF2F2",
    ),
    "cyber_neon": build_theme_from_colors(
        "cyber_neon", "🤖 赛博霓虹", "游戏电竞、元宇宙与前沿科技发布会",
        "#06B6D4", "#0F172A", is_dark=True,
    ),
    "sunset_gradient": build_theme_from_colors(
        "sunset_gradient", "🌅 日落渐变", "温暖叙事、品牌故事与年终总结",
        "#DC2626", "#FFF7ED",
    ),
    "nordic_ice": build_theme_from_colors(
        "nordic_ice", "🧊 北欧冰川", "极简冷淡风、学术会议与研究报告",
        "#334155", "#F8FAFC",
    ),
}

# 运行时自定义主题（导出请求带 themeColors 时注册；不污染 THEMES 内置表）
_RUNTIME_THEMES: dict[str, PptTheme] = {}


def register_runtime_theme(theme: PptTheme) -> None:
    """注册一次运行时自定义主题，供同进程 get_theme(artifact.theme) 取用。"""
    _RUNTIME_THEMES[theme.id] = theme


def clear_runtime_themes() -> None:
    """清空运行时自定义主题（测试/维护用）。"""
    _RUNTIME_THEMES.clear()


def get_theme(
    theme_id: str | None,
    *,
    custom_colors: dict | None = None,
) -> PptTheme:
    """获取指定主题；内置 ID 优先，其次运行时自定义，再次用 custom_colors 构建。

    custom_colors 支持键：
      primary / bg（必填才有意义）、secondary、accent、card_bg、card_border、
      text_main、text_muted、is_dark、name、description
    """
    if not theme_id and not custom_colors:
        return THEMES["tech_blue"]

    clean_id = (theme_id or "custom").lower().strip().replace("-", "_")
    # 内置主题优先（保证 18 套精调配色不被前端 Primary/Bg 二色覆盖）
    if clean_id in THEMES:
        return THEMES[clean_id]
    if clean_id in _RUNTIME_THEMES and not custom_colors:
        return _RUNTIME_THEMES[clean_id]

    if custom_colors:
        try:
            return build_theme_from_colors(
                clean_id or "custom",
                str(custom_colors.get("name") or clean_id or "自定义主题"),
                str(custom_colors.get("description") or ""),
                str(custom_colors.get("primary") or "#3B82F6"),
                str(custom_colors.get("bg") or "#F8FAFC"),
                secondary_hex=custom_colors.get("secondary"),
                accent_hex=custom_colors.get("accent"),
                card_bg_hex=custom_colors.get("card_bg"),
                card_border_hex=custom_colors.get("card_border"),
                text_main_hex=custom_colors.get("text_main"),
                text_muted_hex=custom_colors.get("text_muted"),
                is_dark=custom_colors.get("is_dark"),
            )
        except Exception:
            return THEMES["tech_blue"]

    if clean_id in _RUNTIME_THEMES:
        return _RUNTIME_THEMES[clean_id]
    return THEMES.get(clean_id, THEMES["tech_blue"])
