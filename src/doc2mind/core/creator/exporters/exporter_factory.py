"""导出引擎工厂与分发入口。"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from doc2mind.core.creator.exporters.docx_exporter import DocxExporter
from doc2mind.core.creator.exporters.excel_exporter import ExcelExporter
from doc2mind.core.creator.exporters.html_exporter import HtmlExporter
from doc2mind.core.creator.exporters.pptx_exporter import PptxExporter
from doc2mind.core.creator.models import ExportResult
from doc2mind.core.creator.parser import extract_artifact

logger = logging.getLogger("doc2mind.creator.factory")


def _builtin_theme_ids() -> set[str]:
    from doc2mind.core.creator.themes import THEMES

    return set(THEMES.keys())


def get_default_export_dir() -> Path:
    """获取默认导出物理文件保存目录。"""
    user_docs = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents" / "DocMind_Exports"
    user_docs.mkdir(parents=True, exist_ok=True)
    return user_docs


def sanitize_filename(name: str) -> str:
    """清洗文件名中的非法字符。"""
    clean = re.sub(r'[\\/*?:"<>|]', "_", name).strip()
    return clean or "DocMind_Creative_Artifact"


def export_artifact(
    content: str,
    target_format: str | None = None,
    output_path: str | Path | None = None,
    title_override: str | None = None,
    theme: str | None = None,
    theme_colors: dict | None = None,
) -> ExportResult:
    """将文本或 Artifact 语法内容导出为指定格式的物理文件。

    Args:
        content: 包含 Artifact 语法或纯 Markdown 的文本
        target_format: pptx / docx / xlsx / html / md（若为空则从 content 自动嗅探）
        output_path: 输出文件路径（可选，未指定时自动生成在用户文档目录下）
        title_override: 覆盖标题（可选）
        theme: 主题配色 ID（内置 18 套或 custom_xxx）
        theme_colors: 自定义主题色字典；theme 不在内置库时用它构建完整配色
    """
    try:
        artifact = extract_artifact(content, default_type=target_format or "docx")
        if title_override:
            artifact.title = title_override
        if theme:
            artifact.theme = theme
        # 自定义主题：注册到运行时表（不污染内置 THEMES），供 get_theme(artifact.theme) 取用
        if theme_colors and (not theme or theme not in _builtin_theme_ids()):
            from doc2mind.core.creator.themes import build_theme_from_colors, register_runtime_theme

            custom = build_theme_from_colors(
                (theme or "custom").lower().strip().replace("-", "_"),
                str(theme_colors.get("name") or theme or "自定义主题"),
                str(theme_colors.get("description") or ""),
                str(theme_colors.get("primary") or "#3B82F6"),
                str(theme_colors.get("bg") or "#F8FAFC"),
                secondary_hex=theme_colors.get("secondary"),
                accent_hex=theme_colors.get("accent"),
                card_bg_hex=theme_colors.get("card_bg"),
                card_border_hex=theme_colors.get("card_border"),
                text_main_hex=theme_colors.get("text_main"),
                text_muted_hex=theme_colors.get("text_muted"),
                is_dark=theme_colors.get("is_dark"),
            )
            register_runtime_theme(custom)
            artifact.theme = custom.id

        # 格式确定
        fmt_str = (target_format or artifact.artifact_type.value).lower().strip()
        if fmt_str.startswith("."):
            fmt_str = fmt_str[1:]

        # 确定输出路径
        if output_path is None:
            safe_title = sanitize_filename(artifact.title)
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{safe_title}_{timestamp}.{fmt_str}"
            out_file = get_default_export_dir() / filename
        else:
            out_file = Path(output_path)

        out_file.parent.mkdir(parents=True, exist_ok=True)

        # 分发给对应导出引擎
        if fmt_str in ("pptx", "ppt"):
            # P0 硬门禁：结构不合格不写盘
            from doc2mind.core.creator.inspector import validate_pptx_export
            from doc2mind.core.creator.parser import parse_pptx_slides

            if not artifact.slides:
                artifact.slides = parse_pptx_slides(artifact.raw_content, deck_title=artifact.title)
            gate_errors = validate_pptx_export(artifact)
            if gate_errors:
                msg = "结构不合格，请重生成：" + "；".join(gate_errors)
                logger.warning("PPTX 导出门禁拒绝: %s", msg)
                return ExportResult(
                    ok=False,
                    artifact_type=fmt_str,
                    file_path="",
                    file_name="",
                    error=msg,
                )
            PptxExporter().export(artifact, out_file)
        elif fmt_str in ("docx", "doc"):
            DocxExporter().export(artifact, out_file)
        elif fmt_str in ("xlsx", "xls", "csv"):
            ExcelExporter().export(artifact, out_file)
        elif fmt_str in ("html", "htm"):
            HtmlExporter().export(artifact, out_file)
        elif fmt_str in ("md", "markdown", "txt"):
            out_file.write_text(artifact.raw_content, encoding="utf-8")
        else:
            # 默认 docx
            DocxExporter().export(artifact, out_file)

        file_size = out_file.stat().st_size if out_file.exists() else 0

        return ExportResult(
            ok=True,
            artifact_type=fmt_str,
            file_path=str(out_file.resolve()),
            file_name=out_file.name,
            file_size_bytes=file_size,
        )
    except Exception as e:
        logger.error("导出 Artifact 异常: %s", e, exc_info=True)
        return ExportResult(
            ok=False,
            artifact_type=target_format or "unknown",
            file_path="",
            file_name="",
            error=str(e),
        )
