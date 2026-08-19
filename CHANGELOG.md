# Changelog

本文件记录 DocMind 每个版本的主要变更。格式基于 [Keep a Changelog](https://keepachangelog.com/)。

## [v1.0.1] - 2026-08-19

### 🌟 全新特性与架构升级

- **🏥 Doctor 系统全维自愈诊断体系**：
  - 命令行 `doc2mind doctor` 及 WPF 设置页一键执行全维体检；
  - 深度覆盖 Python 运行时、sqlite-vec 扩展库、嵌入模型缓存、GPU CUDA 硬件加速与国内镜像网络连通性；
  - 提供问题根因分析与一键自愈修复指引。
- **🎨 Creative Artifact 多模态创意导出工作台**：
  - 结构化提取对话成果，原生支持一键生成并导出 **PPTX 演示幻灯片**（含封面、目录、卡片、看板、演讲备注）、**Word 文档**（样式化排版与表格）、**Excel 统计报表** 与 **自包含独立 HTML 页面**。
- **⚡ 本地 AI 环境智能秒级感知**：
  - 后端新增 `/v1/system/local-ai-environment` 探测服务；
  - 前端毫秒级感知 LM Studio / Ollama 服务状态及本地 36+ GGUF 大模型，实现 0 门槛一键免配置绑定。
- **🕸️ 知识图谱实体 Copilot 工作台**：
  - AABB 物理胶囊防重叠引擎、双向贝塞尔连线分离，画布清晰无遮挡；
  - 450px 黄金宽度工作台：本地原著切片速览（Ground Truth）、关联网实体拓扑双向下钻；
  - 一键提炼精炼知识卡片沉淀入库。
- **🌐 实时免 Key 联网技术资料融合**：
  - 内置 WebSearchService，实体探讨与智能问答时实时检索业界最新资料，与本地切片双轨溯源。
- **🧹 知识库自主整理引擎 (Curator)**：
  - 支持 `enrich`（智能打标）、`categorize`（自动归类）、`dedup`（语义去重）、`consolidate`（精炼蒸馏），含 `dry_run` 安全预览。

### 🐞 稳定性与兼容性修复

- **🛡️ 弱模型容错与自愈机制**：增强对小参数本地模型与国产大模型输出 JSON 格式不规范、Markdown 截断的自动修复容错。
- **🚀 进程间通信与启动优化**：修复后端子进程管道缓冲区满导致的死锁挂起问题，启动速度压缩至 1 秒内。
- **🌊 流式通信保护**：彻底修复商汤 SenseNova、DeepSeek 等大模型流式调用末尾 chunk 导致的 `list index out of range` 异常。
- **🎨 WPF UI 与体验增强**：
  - 修复 `ChatMessage` 缺少 `IsAssistant` 属性引起的 WPF 绑定报错；
  - 新增 Toast 浮层通知、快捷预设芯片与冷启动体验优化；
  - 强制清除 WebView2 磁盘与内存缓存，保证最新画布与抽屉栏 100% 渲染。

### 📦 安装与交付

- **免安装绿色便携版**：`DocMind-v1.0.1-win-x64.zip`（解压即用，内置 .NET 8 独立运行时）。
- **标准安装包**：`DocMind-Setup-1.0.1.exe`（双击安装到开始菜单和桌面，内置环境自动自愈）。

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
