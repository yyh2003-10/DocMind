# DocMind

> 轻量向量知识库工具 — 本地 ONNX 嵌入 + sqlite-vec 混合检索 + MCP 工具接口

> ## ⚠️ 许可证声明
> **AGPL-3.0 开源 + 商业授权** — 本软件开源版本使用 [AGPL-3.0](LICENSE) 许可证。
> 允许个人和商业使用，但须遵守 AGPL-3.0 的源代码公开义务。
> 如需闭源嵌入或获得企业级商业授权，请联系项目维护者。

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Code style: ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://docs.astral.sh/ruff/)
[![CI](https://github.com/yyh2003-10/DocMind/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/yyh2003-10/DocMind/actions/workflows/ci.yml)

DocMind 把任意文档（PDF / Word / Excel / PPT / Markdown / HTML / 图片 / 代码）
转成语义分块、向量索引和可检索的知识库。全部计算在本地完成，
默认嵌入模型 ~35MB，向量库 ~5MB，无需 PyTorch / ChromaDB / Java。

## 🚀 快速开始（推荐：下载安装包）

普通用户最简单的方式是**直接下载安装包**（见 [GitHub Releases](https://github.com/yyh2003-10/DocMind/releases)）：

1. **下载** `DocMind-Setup-<版本>.exe`（约 115MB，含 CPU 核心运行时）
2. **双击安装**，按提示选择是否勾选「GPU 加速」「OCR」扩展（可选，需联网）
3. **启动 DocMind**
4. **导入文档**：到【导入】页选择文件或文件夹，点击导入
5. **搜索**：到【搜索】页输入问题，回车即可检索

> 基础包已内置 Python 环境，**无需**单独安装 Python。
> 首次使用会自动下载嵌入模型（~35MB / 90MB，走国内镜像）。

### 可选扩展（安装时勾选，或以后在设置页操作）

| 扩展 | 作用 | 体积 | 硬件要求 |
|------|------|------|---------|
| **GPU 加速** | 嵌入推理提速（快 5-10 倍） | ~2GB | NVIDIA 显卡 |
| **OCR** | 扫描件 / 图片文字识别 | ~1.5GB | 无特殊要求 |

> 未安装 GPU / OCR 不影响核心功能，软件会自动使用 CPU 模式并在设置页提示。

---

## 🛠️ 安装器无效？手动安装教程

如果安装包无法正常安装或运行，可按下面的步骤手动搭建环境。全程约 10 分钟，只需跟着命令逐条执行。

### 第 1 步：安装 Python 3.11

1. 打开 [Python 官网下载页](https://www.python.org/downloads/release/python-3119/)
2. 下载 **Windows installer (64-bit)**
3. 运行安装时，**务必勾选「Add python.exe to PATH」**，再点「Install Now」
4. 验证：打开命令提示符（`Win+R` 输入 `cmd` 回车），输入 `python --version`，应显示 `Python 3.11.x`

### 第 2 步：下载源码

**无需 git**，直接下载 ZIP：

1. 打开 [DocMind 仓库](https://github.com/yyh2003-10/DocMind)
2. 点绿色按钮 **Code → Download ZIP**，解压到任意目录（如 `D:\DocMind`）

### 第 3 步：创建虚拟环境并安装依赖

在解压目录打开命令提示符，逐条执行：

```bat
cd /d D:\DocMind            :: 换成你的解压路径
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-core.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
.venv\Scripts\python.exe -m pip install -e . --no-deps
```

> 国内网络建议加 `-i https://pypi.tuna.tsinghua.edu.cn/simple`（上例已加）。
> 如需 GPU 加速，追加 `-r requirements-gpu.txt`；如需 OCR，追加 `-r requirements-ocr.txt`。

### 第 4 步：验证安装

```bat
.venv\Scripts\doc2mind.exe --help
```

看到命令帮助即安装成功。首次使用会自动下载嵌入模型（~90MB，走国内镜像）。

### 第 5 步：使用（二选一）

**A. 只用命令行搜索：**
```bat
.venv\Scripts\doc2mind.exe ingest D:\你的文档目录
.venv\Scripts\doc2mind.exe search "你想问的问题"
```

**B. 使用 WPF 桌面客户端：**
```bat
.venv\Scripts\doc2mind.exe serve        :: 先启动后端
```
再构建并启动客户端（需先安装 [.NET 8 SDK](https://dotnet.microsoft.com/download/dotnet/8.0)）：
```bat
dotnet build DocMind\DocMind.csproj -c Release
DocMind\bin\Release\net8.0-windows\win-x64\DocMind.exe
```

### 遇到问题？

| 症状 | 解决 |
|------|------|
| `python` 不是内部命令 | 第 1 步没勾选 Add to PATH，重装 Python |
| pip 下载慢 / 超时 | 加 `-i https://pypi.tuna.tsinghua.edu.cn/simple` |
| 模型下载失败 | 设置环境变量 `HF_ENDPOINT=https://hf-mirror.com` 后重试 |
| 后端启动失败 | 看 [部署指南](docs/部署指南.md) 的排查表 |

> 更详细的部署说明（GPU 选择、离线部署、数据迁移）见 [部署指南](docs/部署指南.md)。

---

## 开发者 / 源码方式安装

如果你需要从源码构建或二次开发：

- **8 种文档格式** 解析，保留标题 / 表格 / 代码块结构
- **智能语义分块**，表格整块保护、代码按函数切分
- **ONNX 本地嵌入**（BAAI/bge-small-zh-v1.5，~35MB，首下自动）
- **sqlite-vec 向量存储**，零依赖嵌入式
- **BM25 + 向量混合检索**，RRF 融合，中英文皆宜
- **格式互转** PDF / DOCX / XLSX / PPTX → MD / JSON / TXT / HTML
- **MCP Server** 一行接入 Cursor / Claude Desktop / Windsurf
- **RAG 对话**（OpenAI 兼容 API / Ollama 本地 LLM），支持多轮追问 + 来源引用
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

# RAG 对话（需配置 LLM，见下方配置说明）
doc2mind chat "项目架构是什么？"                # 单次问答
doc2mind chat                                  # 交互式多轮对话

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

WPF 桌面客户端源码在 [`DocMind/`](DocMind/)，HTTP API 文档在 [`docs/api.md`](docs/api.md)，
使用说明在 [`docs/使用说明-每个页面在干嘛.md`](docs/使用说明-每个页面在干嘛.md)，
MCP 接入文档在 [`docs/mcp.md`](docs/mcp.md)。

## 配置

默认配置文件位置：

| 平台 | 路径 |
|------|------|
| Windows | `%APPDATA%\doc2mind\config.toml` |
| macOS | `~/Library/Application Support/doc2mind/config.toml` |
| Linux | `~/.config/doc2mind/config.toml` |

也可通过环境变量 `DOC2MIND_*` 覆盖，或用 `--config` 指定。

### RAG 对话配置

`doc2mind chat` 需要配置 LLM 才能使用。支持 4 种提供商（`DOC2MIND_LLM_PROVIDER`）：

```bash
# OpenAI 兼容 API（DeepSeek/Qwen/Kimi/OpenAI 通用，可用 DOC2MIND_LLM_BASE_URL 切换服务商）
set DOC2MIND_LLM_PROVIDER=openai
set DOC2MIND_LLM_API_KEY=sk-your-api-key
set DOC2MIND_LLM_MODEL=deepseek-chat

# Anthropic Claude
set DOC2MIND_LLM_PROVIDER=anthropic
set DOC2MIND_LLM_API_KEY=sk-ant-your-key
set DOC2MIND_LLM_MODEL=claude-sonnet-4-5

# Google Gemini
set DOC2MIND_LLM_PROVIDER=gemini
set DOC2MIND_LLM_API_KEY=your-key
set DOC2MIND_LLM_MODEL=gemini-2.5-flash

# 本地 Ollama（完全离线，无需 API Key）
set DOC2MIND_LLM_PROVIDER=ollama
set DOC2MIND_LLM_MODEL=llama3.2
```

WPF 桌面端在「设置 → 大模型对话」图形化配置（API Key 经 DPAPI 加密存储），
支持「测试连接」一键验证。详见 [docs/mcp.md](docs/mcp.md) 的「RAG 对话配置」。

## 许可证

**AGPL-3.0 开源 + 商业授权**

本软件开源版本采用 [GNU Affero General Public License v3.0](LICENSE)（AGPL-3.0）：
- ✅ 允许个人和商业使用
- ✅ 允许修改和再分发
- ⚠️ **必须保持开源**：修改过的版本如通过网络提供服务，必须向用户提供对应源代码
- ℹ️ 衍生作品必须以 AGPL-3.0 发布

如需在**闭源或非 AGPL** 环境下使用本软件，可获取**商业授权**（请通过 GitHub Issues 联系）。

项目依赖的第三方库许可证见 [NOTICE](NOTICE) 和 [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)。
默认嵌入模型 BAAI/bge-small-zh-v1.5 采用 MIT 许可证。
