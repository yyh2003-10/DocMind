# Changelog

本文件记录 DocMind 每个版本的主要变更。格式基于 [Keep a Changelog](https://keepachangelog.com/)。

## [v1.0.3] - 2026-10-11

### 🗄️ 存储后端中立化（PostgreSQL/pgvector 支持）

- **后端无关存储契约**：新增 `doc2mind.core.store.contracts`（`KnowledgeStore` 协议 + `StoreCapabilities`/`StoreError`），SQLite 与 PostgreSQL 实现同一套接口，上层业务零改动切换后端。
- **PostgreSQL 存储适配**：新增 `PostgresKnowledgeStore`（pgvector 向量检索）+ `PostgresBatchWriter`（写入不变量收敛到单一深处模块）+ 快照导入导出；配置 `postgres_dsn` / `postgres_tenant_id` / `postgres_index_generation` 即可启用（默认仍走 SQLite，零迁移成本）。
- **配套部署资产**：`deploy/postgres/`（docker-compose + init.sql + schema.sql）一键起库。
- **跨后端快照格式**：新增 `doc2mind.core.snapshot`——ZIP 容器（manifest + 确定性 UTF-8 JSONL），与备份（SQLite 恢复件）职责分离，不同存储适配器可互相导入；向量数据视为可选缓存，无法消费时可重索引。
- 测试：`tests/test_store_contracts.py`、`tests/test_postgres_schema.py`、`tests/test_postgres_batch_writer.py`、`tests/test_postgres_batch_writer_integration.py`、`tests/test_postgres_snapshot.py` 新增入库。

### 🤖 Agent 任务化：后台任务与崩溃续跑

- **任务状态存储**：新增 `doc2mind.core.job_store`（后端无关的 job 状态存储 + 进程内适配器）与 `task_context`（研究任务规格与用户问题分离，不拼入检索 query）。
- **WPF 后台 Agent 任务**：新增 `ChatViewModel.AgentJobs` / `ChatViewModel.TaskProgress`——Agent 长任务改为可持久化的后台任务，客户端崩溃/重启后自动重连并恢复进度展示，不再因窗口关闭丢失任务。
- **服务端 Agent 流式运行时（P1）**：新增 `doc2mind.core.agent.runtime` 的 `chat_agent` / `loop` / `permissions`——按 planner 选定工具 → LoopController 执行 → 轨迹 SSE → 流式生成最终回答；**不依赖 provider 原生 tool-calling**，弱模型/无 tool-calling 能力的模型也能跑完整 Agent 链路。

### 💬 对话体验：记忆隔离与可信证据

- **用户记忆隔离注入**：`memory_context` 独立注入生成上下文，明确框定为「用户偏好/背景，不是本轮问题主题」——记忆不再污染检索 query，通识题不再被库内噪声带偏主题。
- **任务规格（TaskContext）**：研究任务的目标 / 受众 / 交付物 / 引用风格 / 约束与用户问题分离传递（`ChatRequest.TaskContext`），弱模型下主题不漂移。
- **HTML 答案气泡**：新增 `HtmlAnswerBubble` 控件，答案 HTML 渲染、暗色主题跟随、溢出裁剪一体化。

### ⚡ 解析与模型管理

- **PDF 快检快抽**：引入 `pypdfium2`（BSD-3）文本层探测 + 快速文本抽取，秒级区分文字版/扫描版 PDF。
- **OCR 模型统一管理**：新增 `doc2mind.core.loader.ocr_models`——统一模型目录（默认 `%LOCALAPPDATA%/doc2mind/paddle_models`，经 `PADDLE_PDX_CACHE_HOME` 注入 PaddleX）、国内源优先（ModelScope，可切 BOS，关闭默认 HF 连通探测）、缓存检测与显式下载，首次推理前完成模型准备。
- **实用工具类**：`LlmUrlNormalizer`（LLM 端点 URL 规范化）、`NumericRangeRule`（数值范围校验）、`PasswordHelper`、`ThemeMessageBox`（主题化消息弹窗）。

### 📦 安装与交付

- **修复绿色版缺 Python 运行时**：v1.0.2 绿色 ZIP 因打包脚本对缺失的 `python-runtime` 目录静默跳过，解压后后端无 Python 可启动；本版绿色版 ZIP 内含完整 `python\` 便携运行时（embeddable，无任何绝对路径绑定，普通用户开箱即用）。
- **标准安装包**：`DocMind-Setup-1.0.3.exe`（Inno Setup，PerUser 安装无需管理员，含 GPU/OCR 可选扩展安装任务）。
- **升级注意**：直接覆盖安装即可，用户数据（知识库/设置/聊天记录）位于 `%LOCALAPPDATA%` 与 `%APPDATA%`，不受影响。

## [v1.0.2] - 2026-09-26

### 🖥️ 机型档位整套搭配（模型配置一键成套）

- **5 档机型 bundle**：新增 `doc2mind.core.model_bundles`，按硬件自动匹配「嵌入 + 重排 + 本地 LLM」整套搭配（极轻量纯 CPU / 轻量 / 主流 / 进阶 / 旗舰），选型来自魔搭社区 GGUF 仓库与低配实测数据（Qwen3-1.7B → 30B-A3B MoE），拉取命令走魔搭加速源。
- **干净电脑开箱即用**：档位推荐永不依赖本机环境——无 Ollama / 无 GGUF / 无 GPU 也返回完整 5 档（`runtime_missing` 态），配合三态按钮（应用整套 / 复制拉取命令 / 打开 Ollama 下载页）从零安装。
- **硬件探测多级降级**：`nvidia-smi`（独立显存）→ `wmic`（内存 + GPU 名）→ 最保守极轻量档，推荐文案展示实际探测到的硬件（不再硬编码型号）。
- **极轻量档自动关重排**：cross-encoder 在无 GPU 机器上得不偿失，应用该档时 `rerank_enabled=false` 随整套一并下发。
- **GGUF 扫描去盘符化**：不再写死 F:/E:/D:，改为枚举所有存在盘符的 `<盘>:\models`、`<盘>:\llama` + LM Studio 缓存目录 + `DOC2MIND_GGUF_DIRS` 环境变量追加。
- **HTTP 端点**：`GET /v1/system/model-bundles`（档位判定 + 5 档 bundle + 三态 + pull 命令）；`/v1/system/local-ai-environment` 响应新增 `tier` / `bundle_version` 字段。
- **WPF 设置页**：「本地 AI 环境智能感知」区新增档位卡片（当前档位高亮），「应用整套」一次下发嵌入 / 重排 / LLM 三配置。
- 测试：`tests/test_model_bundles.py` 21 项（档位边界 / 干净机用例 / 嵌入模型清单防漂移）+ C# DTO 反序列化与 ApplyBundle 用例 6 项。

### 🧭 基础能力产品化（F0/F1）与评估契约

- **推荐检索配置（F0）**：新增 `RECOMMENDED_RETRIEVAL_PRESET`（bge 中文查询指令、`semantic_floor=0.15` 等已验证参数）。出厂默认仍向后兼容；仅显式应用才写回。
  - CLI：`doc2mind config --recommended-retrieval [--apply]`
  - HTTP：`GET/POST /v1/config/retrieval-recommended`（预览 / 应用，幂等）
- **本库评估（F1）**：`doc2mind.core.eval_library.evaluate_library` + `tools/eval_library.py` + `POST /v1/eval/library`。真实库抽样自检索（SelfRecall@k / MRR）、健康快照、可操作建议；CLI 退出码 0/1/2 可接脚本。
- **评估契约**：新增 `docs/testing/feature-eval-contract.md`（三层评估体系 + 功能 DoD + CI 卡点）。
- 测试：`tests/test_recommended_retrieval_and_eval.py` 9 项全绿（配合既有 `eval_retrieval.py --zh` 门槛）。

### 🎯 向量知识库检索根基升级（召回质量核心）

- **🐛 修复相关度虚高（根因）**：`distance→score` 由失真公式 `1/(1+d)` 改为忠实余弦相似度 `1 - distance`。修复前「正交内容（真实相似度 0）」显示 0.50、「真实余弦 0.33」显示 0.60（界面「相关度 60+」）；修复后如实反映真实余弦，杜绝数据污染。
  - 可用 `python tools/eval_retrieval.py` 复核：受控负样本（真实余弦 0.33）现显示 `vector_score=0.33` 且 `==1-distance`。
- **🇨🇳 中文 BM25 升级（jieba + unicode61）**：开启 `bm25_jieba_enabled=true` 后 FTS5 改用 jieba 分词（unicode61 tokenizer），2 字中文词（气缸/IP/5A 等）可精确 BM25 召回，不再依赖 trigram 的 LIKE 兜底；切换 tokenizer 时自动一次性重建 FTS5 索引。jieba 为 MIT 许可，商用安全。
- **🔤 BGE 查询指令前缀**：新增 `query_instruction` 配置，检索时先拼前缀再 `embed_query`（不动文档嵌入，curator 去重/聚类保持纯净），对 bge 系列可显著提升检索。
- **📉 语义下限过滤**：新增 `semantic_floor` 配置，命中的相关度代理（重排启用用 `rerank_score`，否则 `max(vector,bm25)`）低于下限则丢弃——`top_k` 是上限不硬填，诚实过滤而非虚高硬凑。
- **⚖️ 加权 RRF 融合**：融合支持 `rrf_weights="vec,bm25"` 权重（默认 `1,1` 中性），为后续稀疏召回路铺路。
- **🎯 阈值重标**：专家把关/避坑检索硬编码 `0.4` 改为配置化 `pitfall_min_score=0.30`（忠实余弦标尺）。
- **🧪 检索评估件**：新增 `tools/eval_retrieval.py`（确定性假嵌入器，免联网），输出 MRR / Recall@k(1,3,5) / 标定(负样本显示均分) / 建议 `semantic_floor`，并做「60+」虚高回归；新增 `tests/test_retrieval_eval.py` 锁定回归。
- **🧩 邻块上下文（父子检索，B2）**：新增 `neighbor_context_window=1`（0 关闭）与 `store.get_neighbor_chunks`，RAG 组装上下文时并入命中 chunk 同源相邻分块，提升跨块回答完整性；检索排序仍以命中为准、引用仍指向命中块，邻块缺失绝不阻塞组装（已加异常兜底与单测）。

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
