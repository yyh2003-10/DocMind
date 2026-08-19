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

## 🚀 极速上手（双击即用）

1. **一键极速启动**：下载解压源码后，直接双击项目根目录下的 **`start.bat`**：
   - ⚡ 自动检查 Python 环境与依赖（清华镜像极速加速）；
   - ⚡ 自动启动本地智能后端与 WPF 桌面客户端；
   - ⚡ 自动完成向量数据库与模型缓存初始化。
2. **新手 0 门槛体验**：
   - 打开客户端进入「对话」页，点击 **「🚀 一键导入官方示例知识库体验」**；
   - 点击推荐快捷问题芯片（如 *“DocMind 支持哪些格式？”*），立刻开始智能问答！
3. **主流大模型一键连接**：
   - 进入「设置」页，选择 **快捷服务商预设**（如 **DeepSeek 官方 API**、**硅基流动 SiliconFlow**、**通义千问 DashScope**、**月之暗面 Kimi**、**智谱清言 GLM-4** 或 **本地离线 Ollama**）；
   - 自动填入官方 API 端点与推荐模型，只需输入 API Key 即可使用！
4. **一键系统全面体检与自愈诊断**：
   - 进入「设置」页点击 **「立即执行全面体检」**，或终端运行 `doc2mind doctor`，全维检查 Python、向量数据库、模型缓存、GPU 硬件加速与镜像网络，一键获取修复指南！

---

## 💻 CLI 常用命令速查

```bash
# 1. 环境诊断与自愈
doc2mind doctor                        # 系统全维体检（Python/存储/模型缓存/GPU/网络）

# 2. 新手示例文档体验
doc2mind sample                        # 一键注入内置官方示例文档库

# 3. 摄入文档与目录
doc2mind ingest ./docs/
doc2mind ingest ./report.pdf --collection papers

# 4. 混合检索（向量 + BM25 双引擎）
doc2mind search "双引擎混合检索原理" --top-k 5

# 5. RAG 智能对话（支持终端真流式打字机打印）
doc2mind chat "请总结 DocMind 的核心特性"

# 6. 索引重建与跨模型维度迁移
doc2mind reindex                       # 重新计算向量索引

# 8. 列出 / 统计 / 删除文档
doc2mind list
doc2mind list --collection papers
doc2mind stats
doc2mind remove ./old.pdf

# 9. 格式转换（PDF/Word/Excel/PPT -> MD/JSON/TXT/HTML）
doc2mind convert input.docx output.md
doc2mind convert ./batch/ --format md --out ./out/
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
