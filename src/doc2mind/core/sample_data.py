"""DocMind 内置新手示例文档与快速体验数据。

用于新安装用户在零素材情况下，一键灌入示例知识库，立即体验检索、图谱与对话能力。
"""

from __future__ import annotations

import time
from typing import Any

from doc2mind.core.config import Settings, get_settings
from doc2mind.core.pipeline import ingest_text

SAMPLE_DOCUMENT_TITLE = "DocMind 快速上手与全景操作指南"

SAMPLE_DOCUMENT_CONTENT = """# DocMind 快速上手与全景操作指南

> 欢迎使用 **DocMind** — 轻量、安全、高效的本地向量知识库工具。
> 本示例文档已自动分块并向量化，你可以立即在【搜索】页或【对话】页提问测试！

---

## 一、核心功能特性

DocMind 专为个人知识管理、研报归纳、代码检索与企业本地文档问答设计：

1. **多格式文档解析**：
   - 支持 **8 种常见格式**：PDF、Word (.docx)、Excel (.xlsx)、PPT (.pptx)、Markdown (.md)、HTML (.html)、纯文本/代码 (.py, .cs, .js, .json) 及图片/扫描件 (OCR)。
   - 自动识别段落层级、标题语义、代码函数块与复杂表格结构。

2. **双引擎混合检索 (Hybrid Search)**：
   - **语义向量检索**：基于本地 FastEmbed ONNX 模型（默认 `bge-small-zh-v1.5`，512 维），理解同义词与意图。
   - **BM25 关键词精确检索**：基于 SQLite FTS5 全文索引，精准命中专有名词、报错代码与型号。
   - **RRF 倒数排名融合**：智能归一化两种得分，兼顾“理解意图”与“精确匹配”。

3. **RAG 智能对话与大模型接入**：
   - 兼容 **DeepSeek**、**硅基流动 (SiliconFlow)**、**通义千问 (DashScope)**、**月之暗面 (Kimi)**、**智谱清言 (GLM)**、**OpenAI** 以及完全离线的 **本地 Ollama**。
   - 对话时自动携带精准引用的原著切片，提供出处标注 `[1]` 与原文溯源。

4. **知识图谱与实体关联**：
   - 自动提取实体与语义关联，提供力导向图可视化探索。

5. **格式互转引擎**：
   - PDF / Word / Excel / PPTX 一键批量转换为 Markdown、JSON、纯文本或 HTML。

---

## 二、支持的文档格式速查表

| 格式分类 | 支持扩展名 | 解析引擎 | 结构保护特点 |
|---|---|---|---|
| 文档 | `.pdf` | pdfminer / opendataloader | 跨栏文本重组、页码标注 |
| 办公办公 | `.docx`, `.pptx` | python-docx / python-pptx | 段落/列表/幻灯片层级切分 |
| 数据表格 | `.xlsx`, `.csv` | openpyxl | 表头保留、逐行语义转述 |
| 标记语言 | `.md`, `.html` | markdown-it-py / bs4 | 标题层级继承、代码块完整保留 |
| 源码文件 | `.py`, `.cs`, `.js`, `.go` | 纯内置解析器 | 函数/类边界识别、语法高亮 |
| 图片扫描件 | `.png`, `.jpg`, `.bmp` | PaddleOCR (可选扩展) | 图片文字识别提取 |

---

## 三、快速提问测试范例

你可以直接在【对话】页输入以下问题进行测试：
- *“DocMind 支持哪些文档格式？各自用什么引擎解析？”*
- *“什么是双引擎混合检索？它的原理是什么？”*
- *“如何配置 DeepSeek 或 Ollama 大模型？”*
- *“数据表格在分块时是如何保留表头信息的？”*

---

## 四、安全与隐私承诺

- **100% 数据本地化**：向量数据库 (`sqlite-vec`)、嵌入模型文件与文档切片全部存储在用户本地设备 (`%LOCALAPPDATA%\\doc2mind`)。
- **无需上传云端**：在仅使用检索与格式转换功能时，全程断网可用。
- **密钥安全**：API Key 经 Windows DPAPI 加密存储，绝不回显或外泄。
"""


def ingest_sample_knowledgebase(
    collection: str = "default",
    settings: Settings | None = None,
) -> dict[str, Any]:
    """将内置示例文档注入到指定知识库集合中。"""
    settings = settings or get_settings()
    start = time.perf_counter()

    result = ingest_text(
        text=SAMPLE_DOCUMENT_CONTENT,
        title=SAMPLE_DOCUMENT_TITLE,
        collection=collection,
        settings=settings,
    )
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    return {
        "ok": result.status in ("ingested", "updated"),
        "status": result.status,
        "title": SAMPLE_DOCUMENT_TITLE,
        "collection": collection,
        "chunk_count": result.chunk_count,
        "elapsed_ms": elapsed_ms,
        "error": result.error,
    }
