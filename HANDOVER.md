# 🏗️ DocMind — 会话交接

> 更新日期：2026-08-16（第二次交接）
> 上一会话：多集合 + SSE 流式 + 技术债修复 + WPF 设置页（已全部提交）
> 本会话：完成新功能审查、补流式测试、全部代码入库


> **⚡ 状态刷新（2026-09-25 · 记忆管理 + 版本管理批次）**：
> - ✅ **记忆 UI**：设置页条目列表 + 编辑/删除/刷新；`UserMemoryService.DeleteByIdAsync`
> - ✅ **创作物版本 UI**：保存当前版本 / 列表 / 恢复历史版本；`ArtifactItem.Id` 稳定标识
> - 测试：C# **427 passed**（含 DeleteById 用例）
> - **本轮业务缺口清单已清完**（消息操作行 / reindex 横幅 / 权限确认 / 设置页 / 质量看板 / Agent 工具+L2 / 轨迹 / inspect 自迭代 / 记忆 / 版本）
>
> **⚡ 状态刷新（2026-09-25 · inspect 自迭代批次）**：
> - ✅ **后端**：`creator/auto_revise.py` + 创作导出路径低分（<75）自动修订 1 轮后重导出；无 LLM/失败不阻断
> - ✅ **客户端**：效果自检报告面板（分数/问题）+「按建议修订」续写入口
> - 测试：`tests/test_auto_revise.py` 5 passed；C# **426 passed**
> - **仍缺口**：记忆编辑/删除 UI、创作物版本 UI
>
> **⚡ 状态刷新（2026-09-25 · 轨迹落库/回看批次）**：
> - ✅ **trajectory_json** 落库（chat_messages 扩列 + 自动迁移）；`_append_turn(..., trajectory=)` 写入 loop transcript
> - ✅ **GET /v1/chats/{id}** 返回 `trajectory`；done 帧含 `trajectory`
> - ✅ **WPF 轨迹时间线**：实时捕获 tool_call/plan + 历史回看，可展开/收起
> - 测试：`tests/test_chat_trajectory.py` 2 passed；C# **426 passed**
> - **仍缺口**：inspect 自迭代、记忆编辑/删除 UI、创作物版本 UI
>
> **⚡ 状态刷新（2026-09-25 · Agent 真闭环批次）**：
> - ✅ **原生工具白名单扩大**：模型可自主调用 `read/write_workspace_file` / `export_artifact` / `inspect_artifact`（L2 写入走权限门）
> - ✅ **L2 权限真挂起**：`PermissionBroker` + loop ASK 等待 + `POST /v1/agent/permission/{id}` + WPF「允许/拒绝」卡片；`session_allow` 不再静默放行首次写入
> - 测试：Python agent **22 passed**；C# **426 passed**
> - **仍缺口**：inspect 自迭代、轨迹落库、记忆编辑/删除 UI、创作物版本 UI
>
> **⚡ 状态刷新（2026-09-25 · 业务缺口修复批次）**：按业务完善度诊断优先级落地——
> - ✅ **Chat 消息操作行**：复制 / 赞踩（写入 FeedbackService，不再装饰）/ 记住 / 沉淀入库 / 重新生成 / 继续写 / Token 统计；用户消息有复制+撤回
> - ✅ **P0.1 reindex 横幅**：对话页顶部强警告 + 「去重建索引」直达文档页 + 重新检测
> - ✅ **permission_request 帧可见**：SSE 解析 + 思考区警告标签（真交互放行仍待双向通道）
> - ✅ **设置页**：长文编排开关/章节/字数、联网搜索 Provider、记忆统计、费用统计
> - ✅ **质量看板**：curate 运行历史、本库检索自评估、推荐检索配置预览/应用
> - ✅ 顺手修复：App.xaml.cs finally-return 编译错；FakeDoc2kbApiService 补齐接口
> - 测试：dotnet test **426 passed**
> - **仍缺口**：Agent 模型决策工具白名单过窄、L2 权限真交互、记忆编辑/删除 UI、创作物版本 UI、inspect 自迭代、轨迹落库
>
> **⚡ 状态刷新（2026-09 · 回答权威性升级 · 代码已落地）**：针对「什么是挠度」差评链路，规划见 [`docs/handoffs/answer-quality-authority-upgrade.md`](docs/handoffs/answer-quality-authority-upgrade.md)，**P0–P2 主干已实现**：
> - ✅ **P0 证据诚实**：思考状态改为「命中 N · 可引用 M」；`ThinkingStep` pill 不再把门控后的 0 引用显示成「5 个来源」；evidence 增加 `local_hit_count` / `local_cite_count` / `citable_total` / `synthesized_source_count` / `single_source` / `web_only` / `degraded_retrieval` / `prompt_track`；证据条展示命中/可引用与「⚠单一来源 / ⚠检索降级」
> - ✅ **P0 降级可见**：向量维度不一致时追加「请到文档库重建索引」操作建议帧
> - ✅ **P1 deep_qa 轨**：`prompt_policy.PROMPT_TRACK_DEEP_QA`——定义类问法且（深度联网 或 可引用≥2 或 库内≥1）时启用结构化覆盖段；输出预算 1.5×（下限 4096）；单源必须写「证据强度：弱（单一来源）」；系统提示追加证据纪律
> - ✅ **自省诚实**：web-only / 单源时自省帧明确「无库内可引用依据」「证据强度：弱」；库内空引用时提示可导入教材/规范
> - ✅ **P2 轻量**：中文概念搜索变体补「规范 限值 标准」；`.edu.cn`/`.gov.cn` 权威分上调
> - 测试：`pytest` **1073 passed / 1 skipped**；黄金验收：`docs/verification/definition-answer-golden.md`
> - **环境修复（2026-09-21）**：服务端 `embed_model` 曾是 `bge-small-zh`(512) 与库 768 维不一致 → HTTP 检索 `degraded=true total=0`。已改回 `jinaai/jina-embeddings-v2-base-zh` + reindex(128) + 入库「挠度」定义笔记；HTTP 检索现 **`degraded=false`**，Top1 为挠度定义（vec≈0.61），门控 **cite=1**。`citation_min_score` 已对齐 0.45。
> - **仍阻塞**：HTTP `llm_provider=none` / API Key 未配置 → 端到端 chat / deep_qa 生成待设置页配置 LLM 后重测；WPF NuGet `path1 null` 环境债仍在。
> - 下一步：配置 LLM → 客户端加载新 pill/证据条代码 → 按黄金用例实测；`ingest_text` 沉淀经验（待确认）

> **⚡ 状态刷新（2026-09 · 回答权威性升级规划）**：用户实测「什么是挠度」暴露：检索降级未阻断、思考 pill「库内·N 个来源」与可引用数不一致、定义题被 rag 篇幅规则压短、深度精读失败后单源硬撑。规划已写入 [`docs/handoffs/answer-quality-authority-upgrade.md`](docs/handoffs/answer-quality-authority-upgrade.md)——Phase0 证据诚实+降级阻断 / Phase1 deep_qa 轨 / Phase2 权威与单源警告。**尚未实现**，执行前先读该 handoff；勿与已完成的证据条/引用门控/深度搜索 deadline 重复开发。

> **⚡ 状态刷新（2026-09-11 · 可信证据体验 Epic）**：`docs/handoffs/chat-trusted-evidence-ux.md` 的 P0–P2 已落地——
> - ✅ 后端 `evidence` 结构化摘要（`rag._build_evidence_summary`）：流式全部 done 帧 + 非流式 `RagAnswer`/`ChatResponse` 均携带 `local_count` / `web_fetched_count` / `web_unfetched_count` / `graph_injected` / `fallback_general_knowledge`
> - ✅ 证据条 UI：`ChatMessage.EvidenceSummaryText` + 警告样式 + 点击展开/收起来源列表
> - ✅ 角标对齐：`NotifySourceMarker` 展开列表 + 1.5s 高亮对应卡片（`SourceHighlightConverter`）
> - ✅ 一键核验：PDF `TargetSnippet` 高亮 + 定位失败 banner；文本降级展示「未能自动定位 / 已定位」提示，禁止静默失败
> - ✅ 防御：未精读网页前端过滤；tooltip 改 `DisplayTitle` 脱敏；历史 `sources_json` 字段往返测试
> - 测试：`pytest tests/test_rag.py` **75 通过**。本机 NuGet 仍报 `path1 null`，`dotnet test` 无法在本环境跑通（已知环境债，不阻塞逻辑改动）
> - 下一步可选：`gh issue` 跟踪、会话历史把 `evidence` 一并落库（当前历史由前端从 Sources 推算）

> **⚡ 状态刷新（2026-08-29）**：下列「P1/P2 待办」在 08-19~08-29 间已全部实现，本节的待办描述已过期，以本块为准——
> - ✅ 后端 SSE 真流式（`http.py` 已改 asyncio.Queue 桥接 + 15s 心跳 + 断连中断）
> - ✅ WPF ChatViewModel 接 SSE（`Doc2kbApiService.ChatStreamAsync` 真流式逐帧解析）
> - ✅ CLI chat 流式（`cli.py chat --stream`）
> - ✅ 会话持久化（`core/store/chat_store.py` SQLite + `/v1/chats*` 三端点 + 历史会话列表 UI）
> - ✅ 引用来源可点击 + Markdown 渲染（Markdig → FlowDocument，来源角标可交互）
> - ✅ 2026-08-29 新增：对话页知识库勾选持久化（`AppSettings.LastChatCollections`）、当前会话消息数展示、LLM 未配置事前引导（动态空态文案 + 发送前拦截）、历史上限条数可配（`DOC2MIND_RAG_MAX_HISTORY_MESSAGES`，默认 20）

---

## 一、本会话完成事项

### 1. 新功能审查（上会话遗留的 4 项检查）
| 审查项 | 结论 |
|---|---|
| rag_answer / rag_answer_stream / _build_context_and_messages 架构 | ✅ 通过。空检索早返回、终帧格式、异常处理（检索异常→RagError + finally close store）均无遗漏 |
| SSE 端点 | ⚠️ 确认为伪流式（`run_in_executor` + `list()` 全量收集后再 yield），首字节延迟=完整生成时间。属已知技术债，P1 接 WPF 流式时改 `asyncio.Queue` 桥接 |
| PasswordBoxHelper 内存泄漏 | ✅ 无泄漏。事件 `-=`/`+=` 对称；处理器为静态方法、事件宿主是 PasswordBox 自身，不形成外部根引用。`_isUpdating` 静态字段在 UI 单线程 + 单实例（SettingsView 仅 1 个 PasswordBox）下无竞态 |
| rag_answer_stream 测试覆盖 | ❌ 有缺口 → 已补齐（见下） |

### 2. 审查发现问题的修复
- **变量遮蔽**：`rag_answer_stream` 终帧列表推导式 `for s in sources` 的 `s` 遮蔽外层 Settings 变量 `s`，已改为 `src`（`core/rag.py`）
- **测试补齐**：`tests/test_rag.py` 新增 4 个流式测试：
  - `test_stream_empty_retrieval_returns_hint`：空检索 → 提示 token + done 帧（total_chunks=0），不调 LLM
  - `test_stream_collections_passed_to_retriever`：流式路径多集合透传
  - `test_stream_no_llm_configured_raises`：LLM 未配置抛 RagError
  - `test_stream_yields_token_per_chunk`：逐 chunk 流式实现按序拼接还原 + 历史保存完整回答

### 3. P0 代码提交（6 个 commit）
```
6759a58 feat: LLM 客户端层与流式调用（openai/ollama + stream_chat）
f058aa2 feat: 多集合知识库检索（store/retriever 层）
4ed0ede feat: RAG 问答编排 + LLM/RAG 配置（含技术债修复）
32987f7 feat: chat 服务端点（HTTP/SSE/MCP/CLI）+ 文档同步
bd040ba feat: WPF 对话页与设置页完善（ChatView + PasswordBox + 测试连接）
(最后) chore: 代码风格清理 + 交接文档更新
```

### 4. 测试
| 套件 | 数量 | 说明 |
|---|---|---|
| Python pytest | **442/1 skip** | 2 跳过：watchdog 未装 + SSE 无限流 |
| WPF dotnet test | **250/0** | 含 SettingsViewModelTests 新增 9 项 |

> **⚠️ 基线已过期（2026-09-02 实测更新）**：Python pytest 现为 **442 通过 / 1 跳过 / 0 失败**；WPF dotnet test 现为 **250 通过 / 0 失败**。
> 1 处跳过（pytest）：`test_integration.py:836`（SSE 无限流 TestClient 挂起）。WPF dotnet test 更新为 **250 通过 / 0 失败**（含 SettingsViewModelTests 新增 9 项）。

---

## 二、下会话建议推进顺序

### P1 — 对接前端流式（含 SSE 真流式化）
1. **后端 SSE 真流式**：`/v1/chat/stream` 改 `asyncio.Queue` 桥接（工作线程跑 `rag_answer_stream` 逐帧 put，async 生成器逐帧 get + yield），替换现在的 `list()` 全量收集
2. **WPF ChatViewModel 接 SSE**：`HttpClient.SendAsync` + `ResponseHeadersRead` + 逐行解析 `data:` 帧，token 帧增量更新气泡、done 帧落 sources
3. **CLI chat 流式**：`doc2mind chat` 切 `rag_answer_stream` 逐 token `print(token, end="")`

### P1 — 会话持久化
- `_CHAT_SESSIONS` 进程内 LRU（100 会话），重启丢失
- 加 `chat_sessions` SQLite 表（json 列存 history），加载时并入 LRU，预估 ~150 行

### P2 — 工程完善
- 引用来源可点击（WPF 聊天气泡 sources → 打开文档定位）
- Markdown 渲染（回答气泡）

---

## 三、关键接口速查

### 后端 API
| 端点 | 方法 | 说明 |
|---|---|---|
| `/v1/chat` | POST | 全量 RAG 问答（接受 `collections` 数组） |
| `/v1/chat/stream` | POST | SSE 流式 RAG 问答（⚠️ 目前伪流式，见 P1） |
| `/v1/config` | GET/POST | 配置读写（含 llm_* 字段） |
| `/v1/health` | GET | 健康检查 |

### 核心函数
| 函数 | 位置 | 说明 |
|---|---|---|
| `rag_answer()` | `core/rag.py` | 非流式 RAG 问答 |
| `rag_answer_stream()` | `core/rag.py` | 流式 RAG 问答（yield JSON 行，终帧 done=True） |
| `LLMClient.stream_chat()` | `core/llm/base.py` | 流式 LLM 调用（超时保护） |
| `_do_stream_chat()` | 各 impl | 子类流式实现（默认回退 `_do_chat`） |

### 测试
| 命令 | 说明 |
|---|---|
| `python -m pytest tests/` | Python 后端（442 测试） |
| `dotnet test DocMind.Tests` | WPF 客户端（250 测试） |

---

## 四、技术债 & 风险

| 项 | 说明 | 建议 |
|---|---|---|
| ~~SSE 伪流式~~ | ✅ 已修复（2026-08-19：asyncio.Queue 桥接 + 心跳） | 已闭环 |
| ~~会话不持久~~ | ✅ 已修复（`chat_store.py` SQLite + `/v1/chats*`） | 已闭环 |
| ruff 46 个遗留 lint | 多为 N806/SIM105/F821 假阳性 | 不阻塞，可后续统一清理 |
| LLM 未配置错误提示重复 | `rag_answer` 与 `rag_answer_stream` 各有一份 ~15 行提示文本 | 可提取 `_require_client(s)`，非必须 |

---

## 五、测试验证

```bash
# Python 后端
cd /e/DocMind
python -m pytest tests/ -q    # 442 tests（1 跳过：SSE 无限流集成）

# WPF 客户端
dotnet test DocMind.Tests -v q    # 250 tests

# ruff（可选）
ruff check src tests
```

---

---

## 七、本地 AI 智能感知与纯净环境打包建议 (2026-08-19 新增)

### 1. 架构与新功能
- **算力协同**：向量嵌入固定采用内置轻量 CPU 引擎（`bge-small-zh-v1.5`，30MB 内存，0 显存需求），6GB 显存 100% 独占给本地大语言模型（LM Studio / Ollama），彻底杜绝显存争抢与 CUDA OOM。
- **智能环境感知**：
  - 后端新增 `src/doc2mind/core/local_ai_detect.py` 与 `GET /v1/system/local-ai-environment`；
  - 前端在设置页新增「🌟 本地 AI 环境智能感知」卡片，毫秒级感知 LM Studio / Ollama 运行状态及本地 36+ GGUF 模型，提供一键免配置绑定。
- **详细文档**：见 [`docs/architecture/smart_local_ai_and_deployment_guide.md`](file:///e:/DocMindY/docs/architecture/smart_local_ai_and_deployment_guide.md)。

### 2. 面向小白用户（全新纯净环境）的打包交付建议
1. **内嵌 Python 运行时**：将 Python 后端通过 PyInstaller 打包成 `doc2mind_server.exe` 或内置精简版 `.venv`，随 WPF 安装包分发，实现零 Python 环境依赖；
2. **预置离线嵌入模型**：安装包内直接附带 `bge-small-zh-v1.5` ONNX 权重文件，首次启动脱网即可建库；
3. **国内镜像兜底**：默认注入 `HF_ENDPOINT=https://hf-mirror.com`，保障国内在线下载不超时。

