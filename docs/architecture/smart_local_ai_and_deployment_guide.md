# 📖 DocMind 算力协同架构与纯净环境交付指南

> 版本：v1.0  
> 日期：2026-08-19  
> 适用：DocMind 架构设计、本地大模型协同与全平台打包交付

---

## 一、硬件算力调度与显存协同架构

### 1. 核心设计原则：显存独占 + CPU 并发
在消费级显卡（如 NVIDIA RTX 2060 6GB 显存）环境下，**大语言模型（LLM 对话/RAG）**与**向量嵌入模型（Embedding 知识库构建）**存在显存争抢的天然冲突。

- **显存调度策略**：
  - **知识库向量处理（Embedding）**：固定采用内置轻量 CPU 引擎（`bge-small-zh-v1.5` ONNX 格式）。仅占用 30MB 内存，0 显存需求，多核 CPU 每秒可处理 200+ 篇文档切片。
  - **大模型对话推理（LLM）**：全部 6GB 显存 100% 独占给本地大语言模型（通过 LM Studio / Ollama 加载 `Qwen3.5-4B` / `DeepSeek-R1-8B` 等量化 GGUF 模型）。
- **协同优势**：
  - 彻底杜绝因向量模型和对话模型并发占用显存导致的 CUDA Out-of-Memory (OOM) 崩溃；
  - 后台大批量导入文档切片时，前台与大模型聊天问答零卡顿、零延迟。

---

## 二、本地 AI 环境智能感知与一键免配置绑定

### 1. 工作原理
- **后端探测器（`src/doc2mind/core/local_ai_detect.py`）**：
  - 并发探测 `http://127.0.0.1:11434`（Ollama）与 `http://127.0.0.1:1234`（LM Studio）运行状态及可用模型；
  - 极速扫描 `F:\models`、`F:\llama`、`%USERPROFILE%\.ollama\models` 等目录下的所有 `.gguf` 模型资产；
  - 暴露 `GET /v1/system/local-ai-environment` 端点。
- **前端装配卡片（`SettingsView.xaml` & `SettingsViewModel.cs`）**：
  - 启动/进入设置页时自动检测；
  - 实时显示 LM Studio / Ollama 的就绪徽标及本地已扫描模型数；
  - 提供 **【⚡ 一键套用 LM Studio 极速本地方案】** 和 **【🟢 一键套用 Ollama 一体化本地方案】** 按钮，点击后 Base URL、Model、API Key 一键对齐并自动完成连通性测试。

---

## 三、面向普通用户（全新纯净环境）的打包与交付建议

为了让完全没有技术背景、未安装 Python 的小白用户能够双击即用，正式发布时推荐采用以下交付标准：

### 1. 内嵌 Python 运行时（Zero-Dependency Runtime）
- **方案 A（PyInstaller 单文件服务）**：
  - 将 Python 后端打包为单个独立可执行文件 `doc2mind_server.exe`，放置在安装目录的 `backend/` 文件夹下；
  - `BackendProcessService.cs` 自动检测并优先拉起安装包自带的 `doc2mind_server.exe`。
- **方案 B（嵌入式 Python / Embedded venv）**：
  - 将精简版 `.venv` 打包在安装包根目录下，无需系统环境变量配置。

### 2. 预置离线嵌入模型（Offline Embedding Pack）
- 将 35MB 的 `bge-small-zh-v1.5` ONNX 权重文件（`model.onnx` + `tokenizer.json`）随安装包一并分发并预置在本地模型目录；
- 实现纯离线内网环境下首次启动“秒级可用”，零网络请求依赖。

### 3. 国内网络镜像加速兜底
- `BackendProcessService.cs` 已默认注入 `HF_ENDPOINT=https://hf-mirror.com`，即使在线拉取模型也能秒级完成，杜绝超时。
