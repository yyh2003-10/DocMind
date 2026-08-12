# 🏗️ DocMind — 项目交接报告

> 生成日期：2026-07-28  
> 许可证：AGPL-3.0（见根目录 LICENSE）  
> 上一会话：技术方案讨论 + 架构设计  
> 下一任务：进入 BUILD 模式，开始编码实现

---

## 一、项目总览

**DocMind** — 轻量向量知识库工具，支持：

- **8 种文档格式**解析（PDF/DOCX/XLSX/PPTX/MD/HTML/图片/代码）
- **智能语义分块**，保持表格和代码块完整
- **ONNX 本地嵌入**（默认 bge-small-zh-v1.5，~35MB，无需 PyTorch）
- **sqlite-vec 向量存储**（零依赖嵌入式，~5MB）
- **BM25 + 向量混合检索**（RRF 融合）
- **格式互转**（PDF→MD/JSON/TXT/HTML 等）
- **MCP Server**（供 Cursor/Windsurf/Claude Code 等 AI 工具调用）
- **FastAPI 服务**（HTTP API）
- **WPF 桌面客户端**（Visual Studio + C# 开发）
- **增量更新**（文件 MD5 去重）

---

## 二、技术选型最终决策

| 模块 | 选型 | 许可证 | 体积 | 原因 |
|------|------|--------|:----:|------|
| PDF 解析 | **pdfminer.six** (core) / opendataloader-pdf (extras) | MIT / Apache 2.0 | ~5MB / ~400MB | 纯 Python 免 Java |
| Word | **python-docx** | MIT | ~3MB | 唯一成熟方案 |
| Excel | **openpyxl** | MIT | ~8MB | 业界标准 |
| PPT | **python-pptx** | MIT | ~3MB | 唯一成熟方案 |
| HTML | **beautifulsoup4 + lxml** | MIT + BSD-3 | ~5MB | 稳定 |
| Markdown | **markdown-it-py** | MIT | ~1MB | token stream 精确解析 |
| 图片 OCR | **PaddleOCR** (extras) | Apache 2.0 | +~350MB | 默认不含 |
| 代码 | **纯 Python 内置** | — | 0MB | 无依赖 |
| 嵌入引擎 | **fastembed** (ONNX) | Apache 2.0 | ~45MB | 替代 PyTorch 2GB |
| 默认模型 | **BAAI/bge-small-zh-v1.5** | MIT | ~35MB(首下) | 精度/体积平衡 |
| 备选模型 | BAAI/bge-base/large-zh-v1.5 | MIT | ~220MB/~670MB | 用户按需切换 |
| 向量存储 | **sqlite-vec** | MIT/Apache 2.0 | ~5MB | 零依赖嵌入式 |
| 混合检索 | **rank-bm25** | Apache 2.0 | ~0.1MB | 纯 Python |
| MCP 协议 | **mcp** (Python SDK v1.x) | MIT | ~1MB | 官方 SDK |
| API 服务 | **FastAPI + uvicorn** (extras) | MIT | +~5MB | 按需 |
| 桌面 UI | **C# WPF + MVVM** | — | 独立安装包 | Visual Studio 开发 |

---

## 三、安装模式

```bash
pip install doc2mind                 # core: ~70MB, 全功能
pip install doc2mind[native-pdf]     # +Java + opendataloader-pdf (最高精度 PDF)
pip install doc2mind[ocr]            # +PaddleOCR (图片文字识别)
pip install doc2mind[gpu]            # +GPU 加速嵌入
pip install doc2mind[server]         # +FastAPI RAG 服务
pip install doc2mind[all]            # 全部安装
```

---

## 四、架构设计

```
┌──────────────────────────────────────────────────────────────────┐
│                        三层架构                                   │
├──────────────────────────────────────────────────────────────────┤
│                                                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────────────┐   │
│  │  CLI     │  │  MCP     │  │  WPF 桌面 (C#)               │   │
│  │ 终端命令  │  │ AI 工具  │  │  Visual Studio 开发          │   │
│  └────┬─────┘  └────┬─────┘  └──────────────┬───────────────┘   │
│       │             │ stdio                  │ HTTP               │
│       ▼             ▼                        ▼                    │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │              Python 后端 (FastAPI)                       │    │
│  │              localhost:8765                              │    │
│  │                                                          │    │
│  │  ┌─────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐  │    │
│  │  │ Loader  │→│ Chunker  │→│ Embedder │→│  Store  │  │    │
│  │  │ (8种)   │  │ (语义)   │  │(ONNX/API)│  │sqlite-vec│  │    │
│  │  └─────────┘  └──────────┘  └──────────┘  └─────────┘  │    │
│  │                                                          │    │
│  │  ┌──────────┐  ┌────────────┐  ┌──────────────────────┐ │    │
│  │  │ Converter│  │ BM25+向量  │  │ MCP Server           │ │    │
│  │  │ (格式互转)│  │ RRF 混合   │  │ (stdio 传输)         │ │    │
│  │  └──────────┘  └────────────┘  └──────────────────────┘ │    │
│  └──────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
```

### 核心数据流

```
用户输入(文件/目录)
    │
    ▼
detect.py 按扩展名自动路由 → 对应 Loader
    │
    ▼
Loader → List[DocumentElement]  (content + metadata)
    │
    ▼
Chunker → List[Chunk]  (语义边界合并/表格保护/代码函数分块)
    │
    ▼
fastembed → List[ndarray]  (ONNX 量化模型)
    │
    ▼
sqlite-vec INSERT (向量 + 文本 + 元数据 + 文件MD5)
    │
    ▼
检索: 向量余弦搜索 + BM25关键词 → RRF融合 → Top-K
```

---

## 五、目录结构

```
E:\DocMind\
├── HANDOVER.md                    ← 本文件
│
├── pyproject.toml                 # Python 包配置
├── README.md
├── LICENSE                        # Apache 2.0
│
├── src/
│   └── doc2mind/                  # Python 包
│       ├── __init__.py
│       ├── __main__.py            # python -m doc2mind
│       ├── cli.py                 # Typer CLI
│       │
│       ├── core/
│       │   ├── config.py          # 配置管理
│       │   │
│       │   ├── loader/            # ⚡ 文档加载器
│       │   │   ├── detect.py      # 按扩展名自动路由
│       │   │   ├── pdf_loader.py  # pdfminer.six 解析
│       │   │   ├── pdf_native.py  # opendataloader-pdf (extras)
│       │   │   ├── docx_loader.py # python-docx, XML body 交错段落/表格
│       │   │   ├── xlsx_loader.py # openpyxl, 逐 sheet 逐行
│       │   │   ├── pptx_loader.py # python-pptx, 逐 slide
│       │   │   ├── md_loader.py   # markdown-it-py token stream
│       │   │   ├── html_loader.py # bs4, 递归遍历 body
│       │   │   ├── image_loader.py# PaddleOCR (extras)
│       │   │   └── code_loader.py # 按扩展名+语义分块
│       │   │
│       │   ├── chunker/           # ⚡ 分块器
│       │   │   ├── semantic.py    # 语义边界 (标题→段落→滑窗)
│       │   │   ├── table.py       # 表格保护，整表一块
│       │   │   └── code.py        # 按函数/类分块
│       │   │
│       │   ├── embedder/          # ⚡ 嵌入引擎
│       │   │   ├── base.py        # 抽象接口
│       │   │   ├── fastembed_impl.py  # fastembed ONNX (core)
│       │   │   └── api_impl.py    # OpenAI/兼容 API
│       │   │
│       │   ├── store/             # ⚡ 向量存储
│       │   │   └── sqlite_vec.py  # sqlite-vec 封装
│       │   │
│       │   ├── retriever/         # ⚡ 检索
│       │   │   └── search.py      # 向量+BM25 RRF 融合
│       │   │
│       │   └── converter/         # ⚡ 格式互转
│       │       └── formatter.py   # DocumentElement→MD/JSON/TXT/HTML
│       │
│       └── server/
│           ├── http.py            # FastAPI (extras)
│           └── mcp.py             # MCP Server (core)
│
├── wpf/                           # C# WPF 桌面客户端
│   ├── DocMind.sln
│   ├── DocMind/
│   │   ├── App.xaml
│   │   ├── MainWindow.xaml
│   │   ├── Models/
│   │   ├── ViewModels/
│   │   ├── Views/
│   │   ├── Services/
│   │   └── Converters/
│   └── README.md
│
├── tasks/                         # VS Copilot 任务文件
│
└── docs/
    ├── api.md                     # API 接口文档
    └── mcp.md                     # MCP 接入文档
```

---

## 六、各 Loader 核心实现要点

### 6.1 PDF Loader (`pdf_loader.py`)

**库：** `pdfminer.six` (MIT)

**核心逻辑：**
```python
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextBox, LTFigure, LTChar, LTAnno

def extract(path: str) -> List[DocumentElement]:
    elements = []
    for page_layout in extract_pages(path):
        for element in page_layout:
            if isinstance(element, LTTextBox):
                text = element.get_text().strip()
                if not text:
                    continue
                # 通过字体属性判断标题
                font_sizes = []
                for char in element:
                    if isinstance(char, LTChar):
                        font_sizes.append(char.size)
                avg_size = sum(font_sizes)/len(font_sizes) if font_sizes else 0
                if avg_size > 16:  # 启发式阈值
                    elements.append(DocumentElement(
                        content=text,
                        metadata={"type": "heading", "level": 1, ...}
                    ))
                else:
                    elements.append(DocumentElement(
                        content=text,
                        metadata={"type": "paragraph", ...}
                    ))
    return elements
```

**关键注意：**
- 表格检测：pdfminer 不直接支持表格结构，需要通过坐标启发式判断
- 多栏布局：使用 LAParams(detect_vertical=True) 参数
- 回退：当 pdfminer 提取质量差时，用户可装 extras 用 opendataloader-pdf

### 6.2 Word Loader (`docx_loader.py`)

**库：** `python-docx` (MIT)

**关键注意：**
- `python-docx` 的 `paragraphs` 和 `tables` 是分开迭代的，但正文中它们交错排列。
- **必须**通过 `doc.element.body` 遍历 XML 子节点，判断 `w:p` 还是 `w:tbl`

```python
from docx import Document
from docx.oxml.ns import qn

def extract(path: str) -> List[DocumentElement]:
    doc = Document(path)
    elements = []
    for child in doc.element.body:
        if child.tag == qn('w:p'):  # 段落
            para = ... # 从 w:p 反向找到对应 paragraph 对象
        elif child.tag == qn('w:tbl'):  # 表格
            table = ... # 从 w:tbl 反向找到对应 table 对象
    return elements
```

### 6.3 Excel Loader (`xlsx_loader.py`)

**库：** `openpyxl` (MIT)

```python
from openpyxl import load_workbook

def extract(path: str) -> List[DocumentElement]:
    wb = load_workbook(path, data_only=True)
    elements = []
    for sheet in wb.worksheets:
        elements.append(DocumentElement(
            content=f"## Sheet: {sheet.title}",
            metadata={"type": "heading", "level": 2, "sheet": sheet.title}
        ))
        for row in sheet.iter_rows(values_only=True):
            row_text = [str(c) if c is not None else "" for c in row]
            if any(c.strip() for c in row_text):
                elements.append(DocumentElement(
                    content="| " + " | ".join(row_text) + " |",
                    metadata={"type": "table_row", "sheet": sheet.title}
                ))
    return elements
```

### 6.4 PPT Loader (`pptx_loader.py`)

**库：** `python-pptx` (MIT)

- 遍历每张 slide
- 遍历每个 shape：`has_text_frame` → 文本框, `has_table` → 表格
- 标题检测启发式：字号 > 页面平均 × 1.5 或 position.y 在页面顶部 20%

### 6.5 HTML Loader (`html_loader.py`)

**库：** `beautifulsoup4` + `lxml`

- 递归遍历 body，跳过 `<script>` / `<style>` / `<nav>` / `<footer>`
- 标签映射：h1-h6 → heading, p → paragraph, ul/ol/li → list, pre/code → code

### 6.6 Markdown Loader (`md_loader.py`)

**库：** `markdown-it-py`

- 不直接读文本（无法区分结构），使用 token stream
- token.type = heading_open / paragraph_open / list_item_open / table_open

### 6.7 代码 Loader (`code_loader.py`)

**纯 Python，无依赖**

- 按扩展名映射语言
- Python/JS/TS/Java/C++：按函数/类定义切分
- 其余：按空行分隔的逻辑段落切分

### 6.8 图片 OCR (`image_loader.py`) — extras

**库：** `PaddleOCR` (Apache 2.0)

**特别注意：** 不要调用 `ocr.ocr(pdf_path, type='pdf')` — 这会触发 PyMuPDF (AGPL-3.0)。只对图片调用：

```python
from paddleocr import PaddleOCR
ocr = PaddleOCR(use_angle_cls=True, lang='ch')
result = ocr.ocr(image_path)  # ✅ 安全
# 不要: ocr.ocr(pdf_path, type='pdf')  # ❌ AGPL
```

> ✅ **已解决（2026-08-12）**：`pdf_loader.py` 的 `_ocr_fallback()` 已从 PyMuPDF (AGPL-3.0)
> 替换为 `pdf2image`（基于 poppler，BSD 兼容）。扫描型 PDF 回退渲染不再依赖 AGPL 库。
>
> **poppler 安装方式**（三选一，代码自动探测优先级从高到低）：
> 1. **项目自带**（推荐）：将 poppler 解压到 `tools/poppler/poppler-<版本>/`，代码自动探测
> 2. **系统 PATH**：安装 poppler 后将 bin 目录加入系统 PATH
> 3. **常见目录**：安装到 `C:/tools/poppler/`、`C:/Program Files/poppler/` 等
>
> 下载地址：https://github.com/oschwartz10612/poppler-windows/releases
> 本项目已验证版本：v26.02.0-0（`tools/poppler/poppler-26.02.0/Library/bin/`）。

---

## 七、分块器设计 (`chunker/`)

### 语义分块 (`semantic.py`)

```
输入: List[DocumentElement]
    │
    ├── 按 heading 为分隔（metadata.type == "heading"）
    │    同一 heading 下的元素合并
    │
    ├── 表格保护：type == "table" 或 "table_row" → 整表一块
    │    即使跨页也合并
    │
    ├── 超出 max_size (默认 1500 token ≈ 4000 chars)
    │    → 递归滑窗，overlap=200 chars
    │
    └── 合并过短块（< min_size 默认 50 chars）
         → 并入前一块或后一块
```

### 表格保护 (`table.py`)

- Excel 的连续 table_row 合并为一块
- PDF/Word 中检测到表格结构时整表完整保留
- 表格块 metadata 标记 `{"type": "table", "rows": n, "cols": m}`

### 代码分块 (`code.py`)

```
按函数签名 / class 定义切分：
  Python: def / class 语句
  JS/TS: export function / class / interface / const fn =
  Java: public class / public method
  回退: 空行分隔的逻辑段落
```

---

## 八、嵌入引擎 (`embedder/`)

### fastembed_impl.py (core, 默认)

```python
from fastembed import TextEmbedding

embedder = TextEmbedding(
    model_name="BAAI/bge-small-zh-v1.5",  # ~35MB, 自动下载
    max_length=512
)
embeddings = embedder.embed(chunks, batch_size=32)  # Generator[np.ndarray]
```

### api_impl.py (可选)

```python
from openai import OpenAI

client = OpenAI(api_key=..., base_url=...)
resp = client.embeddings.create(
    model="text-embedding-3-small",
    input=chunks
)
embeddings = [d.embedding for d in resp.data]
```

---

## 九、向量存储 (`store/sqlite_vec.py`)

### 建表

```sql
CREATE VIRTUAL TABLE vec_chunks USING vec0(
    id INTEGER PRIMARY KEY,
    embedding FLOAT[384] distance_metric=cosine
);

CREATE TABLE chunks_meta (
    id INTEGER PRIMARY KEY,
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    format TEXT NOT NULL,
    doc_type TEXT,
    page INTEGER,
    heading TEXT,
    file_hash TEXT,        -- MD5 去重用
    created_at TEXT,
    collection TEXT DEFAULT 'default'
);
```

### 增量去重

```python
file_hash = hashlib.md5(open(path, 'rb').read()).hexdigest()
existing = cur.execute(
    "SELECT id FROM chunks_meta WHERE file_hash = ?", (file_hash,)
).fetchone()
if existing and not force:
    return  # 跳过未变更文件
```

### 检索

```python
# 向量检索
cur.execute("""
    SELECT id, distance FROM vec_chunks
    WHERE embedding MATCH ? AND k = ?
""", [query_vec, top_k * 2])

# BM25 检索
bm25_scores = bm25.get_scores(query_tokens)

# RRF 融合
rrf_score = sum(1 / (60 + rank) for rank in [vec_rank, bm25_rank])
```

---

## 十、MCP Server 设计 (`server/mcp.py`)

**库：** `mcp` v1.x (MIT) — 生产稳定版

### 暴露的工具 (7个)

| Tool 名 | 参数 | 说明 |
|---------|------|------|
| `ingest` | path, collection="default", recursive=False | 摄入文档 |
| `search` | query, collection="default", top_k=10 | 搜索知识库 |
| `list_docs` | collection="default" | 列出文档 |
| `remove_doc` | doc_id, collection="default" | 删除文档 |
| `quality_check` | collection="default" | 质量检查报告 |
| `convert_file` | input_path, output_format="md" | 格式转换 |
| `reindex` | collection="default" | 重建索引 |

### 启动方式

```bash
doc2mind mcp    # stdio 传输，供 Cursor/Claude Desktop 等工具调用
```

### AI 工具接入配置

```json
// claude_desktop_config.json / Cursor MCP 配置
{
  "mcpServers": {
    "doc2mind": {
      "command": "doc2mind",
      "args": ["mcp"]
    }
  }
}
```

---

## 十一、FastAPI 服务 (`server/http.py`) — extras

**库：** `fastapi` + `uvicorn`

| 方法 | 路径 | 参数 | 说明 |
|------|------|------|------|
| GET | `/v1/health` | — | 健康检查 |
| POST | `/v1/ingest` | `{path, collection, recursive}` | 摄入文档 |
| POST | `/v1/search` | `{query, collection, top_k}` | 搜索 |
| GET | `/v1/documents` | `?collection=` | 列出文档 |
| DELETE | `/v1/documents/{id}` | — | 删除文档 |
| GET | `/v1/stats` | `?collection=` | 统计 |
| POST | `/v1/convert` | `{input_path, output_format}` | 格式转换 |
| GET | `/v1/quality` | `?collection=` | 质量报告 |

---

## 十二、CLI 命令设计 (`cli.py`)

**库：** `typer`

```bash
doc2mind ingest ./docs/                          # 摄入文档
doc2mind ingest ./report.pdf --collection 论文    # 指定集合
doc2mind search "transformer 注意力"                # 搜索
doc2mind search "..." --collection 论文 --top-k 5
doc2mind list                                     # 列出文档
doc2mind list --collection 论文
doc2mind remove ./old.pdf                         # 删除
doc2mind stats                                    # 统计
doc2mind convert input.docx output.md             # 格式转换
doc2mind convert ./批量/ --format md --out ./out/
doc2mind serve                                    # 启动 HTTP 服务 (extras)
doc2mind mcp                                      # 启动 MCP Server
```

---

## 十三、WPF 桌面客户端 — 你的任务

**文件位置：** `E:\DocMind\wpf\`

| # | 任务 | 说明 | 优先级 |
|:-:|------|------|:----:|
| 1 | 项目骨架 + MVVM 框架 | 主窗口 + 左侧导航 + 右侧内容区 | P0 |
| 2 | HttpClient 封装 | Doc2kbApiService，全部 API 调用 | P0 |
| 3 | 搜索页面 | 搜索框 + 结果列表 + 分页 | P0 |
| 4 | 导入页面 | 拖拽 + 文件选择 + 进度条 | P0 |
| 5 | 格式转换页面 | 文件选择 + 格式选择 + 预览 | P1 |
| 6 | 质量看板 | 图表 (LiveCharts) + 概览卡片 | P1 |
| 7 | 设置页面 | 后端地址/模型/分块参数 | P1 |
| 8 | 系统托盘 | 后台运行 + 进程管理 | P2 |

**通信方式：** WPF 通过 `HttpClient` 调 `localhost:8765` 的 FastAPI，启动时自动拉起 Python 子进程。

---

## 十四、构建顺序（推荐）

| 阶段 | Python 后端 | 你的 WPF 并行任务 |
|:----:|-------------|-------------------|
| **1** | 项目骨架 + pyproject.toml + 依赖安装 | 搭建 WPF 项目骨架 |
| **2** | detect.py + 4 个核心 Loader (pdf/docx/md/html) | HttpClient 封装 + 测试 |
| **3** | 分块器 (semantic + table + code) | 搜索页面 |
| **4** | fastembed 嵌入引擎 | 导入页面 |
| **5** | sqlite-vec 存储 + BM25 检索 | 格式转换页面 |
| **6** | CLI 全部命令 + Converter | 质量看板 |
| **7** | MCP Server | 设置页面 |
| **8** | FastAPI (extras) + 调试 | 系统托盘 + 打包 |

---

## 十五、关键风险与规避

| 风险 | 影响 | 规避方案 |
|------|------|---------|
| Java 缺失 → opendataloader 不可用 | PDF 精度下降 | pdfminer.six 回退 + 引导安装 Java |
| PaddlePaddle 包大 (~300MB) | 安装慢 | 放在 extras，用户按需安装 |
| ~~PyMuPDF AGPL 传染~~ | ~~许可证违规~~ | **已解决**（2026-08-12）：`pdf_loader.py` 已从 `import fitz` (PyMuPDF, AGPL-3.0) 替换为 `from pdf2image import convert_from_path`（基于 poppler，BSD 兼容）。扫描型 PDF 回退渲染完全消除 AGPL 依赖。注意：pdf2image 需系统安装 poppler（`pdftoppm`/`pdfinfo`） |
| PaddleOCR 误触 PyMuPDF | 许可证违规 | PaddleOCR 只对图片调用，不触发 PyMuPDF（已验证） |
| sqlite-vec 编译问题 (Windows) | 无法安装 | 提供预编译 wheel 或纯 Python fallback |
| 中文 token 计数 | 分块不准确 | 用 `tiktoken` 或按字符数估算 (~1 token ≈ 2-3 中文字符) |
| 大文档 OOM | 处理中断 | 分页/分 sheet 流式处理，配置内存上限 |

---

## 十六、新对话启动指南

新对话打开时，贴以下指令给 AI：

```
我从上一个对话接手 DocMind 项目。项目目录 E:\DocMind。

技术决策已全部完成：
- Python core + C# WPF 桌面
- fastembed (ONNX) 替代 PyTorch
- sqlite-vec 替代 ChromaDB
- pdfminer.six 替代 Java opendataloader-pdf (core)
- MCP Server 作为 AI 工具接口
- 8 种文档格式加载器
- 见 HANDOVER.md 完整交接报告

切换到 BUILD 模式，按 HANDOVER.md 中第十四节的构建顺序从阶段 1 开始实现。
需要 Python 包名 doc2mind，CLI 命令 doc2mind。
```
