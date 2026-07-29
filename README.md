# DocMind

> 轻量向量知识库工具 — 本地 ONNX 嵌入 + sqlite-vec 混合检索 + MCP 工具接口

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://docs.astral.sh/ruff/)

DocMind 把任意文档（PDF / Word / Excel / PPT / Markdown / HTML / 图片 / 代码）
转成语义分块、向量索引和可检索的知识库。全部计算在本地完成，
默认嵌入模型 ~35MB，向量库 ~5MB，无需 PyTorch / ChromaDB / Java。

## 特性

- **8 种文档格式** 解析，保留标题 / 表格 / 代码块结构
- **智能语义分块**，表格整块保护、代码按函数切分
- **ONNX 本地嵌入**（BAAI/bge-small-zh-v1.5，~35MB，首下自动）
- **sqlite-vec 向量存储**，零依赖嵌入式
- **BM25 + 向量混合检索**，RRF 融合，中英文皆宜
- **格式互转** PDF / DOCX / XLSX / PPTX → MD / JSON / TXT / HTML
- **MCP Server** 一行接入 Cursor / Claude Desktop / Windsurf
- **FastAPI 服务**（extras）HTTP API
- **增量更新** 文件 MD5 去重，未变更跳过

## 安装

```bash
pip install doc2mind                 # core: ~70MB, 全功能
pip install doc2mind[native-pdf]     # + opendataloader-pdf (最高精度 PDF)
pip install doc2mind[ocr]            # + PaddleOCR (图片文字识别)
pip install doc2mind[gpu]            # + GPU 加速嵌入
pip install doc2mind[server]         # + FastAPI RAG 服务
pip install doc2mind[all]            # 全部安装
```

## 快速开始

```bash
# 摄入文档
doc2mind ingest ./docs/
doc2mind ingest ./report.pdf --collection papers

# 搜索
doc2mind search "transformer 注意力机制"
doc2mind search "..." --collection papers --top-k 5

# 列出 / 统计 / 删除
doc2mind list
doc2mind list --collection papers
doc2mind stats
doc2mind remove ./old.pdf

# 格式转换
doc2mind convert input.docx output.md
doc2mind convert ./batch/ --format md --out ./out/

# 启动服务
doc2mind serve                       # 启动 HTTP 服务 (extras)
doc2mind mcp                         # 启动 MCP Server
```

## MCP 接入

把 `doc2mind mcp` 注册到 AI 工具的 MCP 配置即可：

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

暴露 7 个工具：`ingest` / `search` / `list_docs` / `remove_doc` /
`quality_check` / `convert_file` / `reindex`。

## 架构

```
CLI ─┐
MCP  ┼─► Python 后端 (FastAPI on localhost:8765)
WPF  ┘        │
              ├─ Loader (8 种文档格式)
              ├─ Chunker (语义 / 表格 / 代码)
              ├─ Embedder (fastembed ONNX)
              ├─ Store (sqlite-vec)
              ├─ Retriever (BM25 + 向量 RRF 融合)
              └─ Converter (格式互转)
```

WPF 桌面客户端源码在 [`wpf/`](wpf/)，HTTP API 文档在 [`docs/api.md`](docs/api.md)，
MCP 接入文档在 [`docs/mcp.md`](docs/mcp.md)。

## 配置

默认配置文件位置：

| 平台 | 路径 |
|------|------|
| Windows | `%APPDATA%\doc2mind\config.toml` |
| macOS | `~/Library/Application Support/doc2mind/config.toml` |
| Linux | `~/.config/doc2mind/config.toml` |

也可通过环境变量 `DOC2MIND_*` 覆盖，或用 `--config` 指定。

## 许可证

[Apache License 2.0](LICENSE)。
默认嵌入模型 BAAI/bge-small-zh-v1.5 采用 MIT 许可证。
