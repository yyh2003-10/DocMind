"""创作 Agent (Creative Studio) 数据模型与结构化表示。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ArtifactType(str, Enum):
    """产出物类型。"""
    PPTX = "pptx"
    DOCX = "docx"
    XLSX = "xlsx"
    HTML = "html"
    MARKDOWN = "md"


class SlideLayoutType(str, Enum):
    """幻灯片专业板式原型 (Archetypes)。"""
    COVER = "cover"        # 封面页
    AGENDA = "agenda"      # 目录/议程页
    CARDS = "cards"        # 多列卡片网格 (2/3/4-Column)
    METRICS = "metrics"    # 大数字 KPI / 核心数据看板
    TIMELINE = "timeline"  # 横向时间线 / 演进路线图
    TABLE = "table"        # 商务结构化对比矩阵
    QUOTE = "quote"        # 核心金句 / 结论卡片
    GENERAL = "general"    # 经典图文/要点正文页


@dataclass
class SlideCardItem:
    """卡片网格中的单个卡片。"""
    title: str
    content: str = ""
    bullets: list[str] = field(default_factory=list)
    badge: str = ""


@dataclass
class MetricItem:
    """数据看板中的单个大数字指标。"""
    value: str             # 如 "99.8%", "10x", "35ms", "0 显存"
    label: str             # 如 "高可用性", "检索提速", "嵌入延迟"
    description: str = ""  # 辅助说明


@dataclass
class TimelineNodeItem:
    """时间线路线图中的单个节点。"""
    stage: str             # 如 "阶段一", "Q1", "Step 1"
    title: str             # 如 "知识索引构建"
    details: list[str] = field(default_factory=list)


@dataclass
class SlideModel:
    """单个幻灯片页面模型。"""
    index: int
    title: str
    layout: SlideLayoutType = SlideLayoutType.GENERAL
    bullet_points: list[str] = field(default_factory=list)
    speaker_notes: str = ""
    table_data: list[list[str]] | None = None
    is_cover: bool = False
    subtitle: str = ""
    cards: list[SlideCardItem] = field(default_factory=list)
    metrics: list[MetricItem] = field(default_factory=list)
    timeline_nodes: list[TimelineNodeItem] = field(default_factory=list)
    quote_text: str = ""
    quote_author: str = ""


@dataclass
class ArtifactModel:
    """结构化创作交付物模型。"""
    artifact_type: ArtifactType
    title: str
    raw_content: str
    description: str = ""
    theme: str = "tech_blue"
    slides: list[SlideModel] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExportResult:
    """导出物理文件结果。"""
    ok: bool
    artifact_type: str
    file_path: str
    file_name: str
    file_size_bytes: int = 0
    error: str | None = None


class InspectionLevel(str, Enum):
    """自检问题严重度。"""
    ERROR = "error"          # 严重缺陷（如空页面）
    WARNING = "warning"      # 体验告警（如文字过密、缺少封面）
    SUGGESTION = "suggestion"# 优化建议（如建议添加卡片、补充演讲词）
    INFO = "info"            # 亮点与提示


@dataclass
class InspectionIssue:
    """自检诊断发现的具体问题项。"""
    level: InspectionLevel
    category: str            # 结构完整度 / 文字密度 / 视觉节奏 / 演讲配套
    message: str
    slide_index: int | None = None  # 具体涉及的幻灯片页码（None 为全局问题）
    fix_suggestion: str = ""        # 具体的修复建议动作


@dataclass
class PptInspectionReport:
    """PPT 效果自检与质量诊断报告。"""
    score: int                     # 0-100 分综合健康度得分
    grade: str                     # S / A+ / A / B / C
    summary: str                   # 综合评语
    slide_count: int = 0           # 幻灯片总页数
    notes_coverage_pct: float = 0.0# 演讲备注覆盖率 (0-100%)
    archetype_diversity: int = 0   # 使用的板式种类数
    total_words: int = 0           # 全文总字数
    avg_words_per_slide: float = 0.0 # 单页平均字数
    issues: list[InspectionIssue] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    highlights: list[str] = field(default_factory=list)

