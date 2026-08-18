# Changelog

本文件记录 DocMind 每个版本的主要变更。格式基于 [Keep a Changelog](https://keepachangelog.com/)。

## [v1.0.1] - 2026-08-17

### 🌟 全新特性

- **知识图谱实体 Copilot 工作台**：AABB 物理胶囊防重叠、贝塞尔曲线连线、知识卡片一键提炼
- **Copilot Agent 深度专家思维准则**：重构系统提示词，具备架构师级深度剖析能力
- **实时免 Key 联网技术资料融合**：内置 WebSearchService，与本地切片双轨溯源
- **知识库自主整理引擎 (Curator)**：支持 `enrich`/`categorize`/`dedup`/`consolidate`，含 `dry_run` 预览

### 🐞 问题修复

- 彻底修复国产大模型（SenseNova、DeepSeek 等）流式调用末尾 chunk `list index out of range` 异常
- 修复后端子进程管道缓冲区满导致的死锁挂起问题，启动速度压缩至 1 秒内
- 修复 `ChatMessage` 缺少 `IsAssistant` 属性引起的 WPF 绑定报错
- 修复设置页 GPU 加速包一键安装参数与 Windows 子进程无窗口调用异常
- 强制清除 WebView2 磁盘与内存缓存，保证最新画布与抽屉栏 100% 渲染

### 📦 安装

- 免安装绿色版：`DocMind-v1.0.1-win-x64.zip`（解压即用，内置 .NET 8 独立运行时）
- 安装包：`DocMind-Setup-1.0.1.exe`（双击安装到开始菜单和桌面）

---

## [v1.0.0] - 2026-08-10

首次正式发布。

### 功能概览

- 8 种文档格式解析（PDF/Word/Excel/PPT/Markdown/HTML/图片/代码）
- 智能语义分块（表格整块保护、代码按函数切分）
- ONNX 本地嵌入（BAAI/bge-small-zh-v1.5，~35MB）
- sqlite-vec 向量存储 + BM25 + 向量混合检索（RRF 融合）
- 格式互转 PDF/DOCX/XLSX/PPTX → MD/JSON/TXT/HTML
- MCP Server 一行接入 Cursor / Claude Desktop / Windsurf
- RAG 对话（OpenAI 兼容 API / Ollama 本地 LLM）
- FastAPI HTTP 服务 + CLI 工具
- WPF 桌面客户端（Windows）
