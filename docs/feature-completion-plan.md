# DocMind — 功能完善计划

> 生成日期：2026-08-16  
> 面向：下一会话的接续开发  
> 前置：本文档需配合 `HANDOVER.md` 与本会话提交（RAG + Chat LLM 集成 + WPF 聊天界面 + 设置页大模型配置）阅读

> **⚡ 状态刷新（2026-08-29）**：第二节 P1 全部 5 项与 P2 两项在 08-19~08-29 间已实现（SSE 真流式 + WPF/CLI 接入、会话持久化、引用来源可点击、Markdown 渲染、上下文长度管理）；2026-08-29 本轮又补齐：知识库勾选持久化（`AppSettings.LastChatCollections`）、会话消息数展示、LLM 未配置事前引导（动态空态文案 + 发送前拦截）、历史上限条数可配（`DOC2MIND_RAG_MAX_HISTORY_MESSAGES`，旧名 TURNS 兼容）。下文勾选框已按此更新，仅保留未完成项。

---

## 一、当前已交付能力（本会话完成）

### 1. RAG + Chat LLM 后端（Python 侧）
| 模块 | 内容 | 入口 |
|---|---|---|
| `core/llm/` | OpenAI 兼容客户端（DeepSeek/Qwen/OpenAI）+ Ollama 本地客户端 + 工厂 | `get_llm_client()` |
| `core/rag.py` | 检索→来源标注上下文→多轮历史→LLM→带引用回答 **+ 流式 rag_answer_stream** | `rag_answer()` / `rag_answer_stream()` |
| `core/llm/base.py` | `stream_chat()` 方法 + 默认回退到非流式 | `LLMClient.stream_chat()` |
| `core/llm/openai_impl.py` | 流式实现（`stream=True`） | `OpenAIClient._do_stream_chat()` |
| `core/llm/ollama_impl.py` | 流式实现（`httpx.stream` + SSE 行解析） | `OllamaClient._do_stream_chat()` |
| `server/http.py` | `POST /v1/chat` + `POST /v1/chat/stream`（SSE） | HTTP 调用 |
| `server/mcp.py` | `chat` MCP 工具 | AI 编辑器调用 |
| `cli.py` | `doc2mind chat` 命令（单次 + 交互式 REPL） | 终端 |
| `config.py` | 8 个 LLM/RAG 配置字段 + `llm_timeout` 字段 | 配置 |

### 2. 多集合知识库（本会话完成）
- `VectorStore.vector_search` / `bm25_search` 支持 `str | Sequence[str] | None` 集合参数
- `rag_answer` 新增 `collections: list[str] | None` 参数，优先于单 `collection`
- HTTP `/v1/chat` 接受 `collections` 数组；MCP `chat` 工具同上
- WPF 对话页「知识库选择」复选框，默认勾选 default

### 3. 鲁棒性加固（本会话完成）
- OpenAI `choices` 空数组防护
- LLM 超时配置（`DOC2MIND_LLM_TIMEOUT`，默认 120s）
- 会话历史 LRU 上限（`_MAX_SESSIONS=100`）
- 噪声过滤：`rag_min_score` 按组件分 `max(vector, bm25)` 过滤
- 修复：`ensure_collection()` 中 `_new_id()` 未定义的真实 NameError 运行时 bug
- 修复：`_build_fts5_match` 设置 `re` flag 避免多线程 `_sre.compile` 竞争

### 4. WPF 客户端（本会话完成）
- 「对话」页（`ChatView`）：消息气泡、来源引用、多轮会话、思考中动画、错误内联展示、多集合复选框
- 「设置」页「大模型对话」卡片：provider / API Key（PasswordBox 脱敏） / base_url / model / temperature / max_tokens / top_k，保存即推送后端 `/v1/config` 实时生效
- 「测试连接」按钮：先测后端健康，再测 LLM 对话
- `PasswordBoxHelper` 附加属性支持双向绑定

### 5. 测试覆盖
- Python 测试：**125/125 通过**（含单元 + 集成 + 多集合 + 流式）
- WPF 测试：**47/47 通过**（ChatViewModel 10 个 + SettingsViewModel 18 个 + 原有 19 个）
- ruff 自动修复 60 个机械问题（import 排序、换行、已废弃导入等）

---

## 二、待完善功能（下一会话候选）

### P0 — 提交与收尾
- [x] 本会话全部改动**尚未提交**。审查 diff → 分逻辑提交（已于 08-19 完成）

### P1 — RAG 体验完善
- [x] **SSE 流式 WPF 对接**：`Doc2kbApiService.ChatStreamAsync` 已接 `/v1/chat/stream`，逐帧增量渲染（08-19）
- [x] **SSE 流式 CLI 对接**：`doc2mind chat --stream` 逐 token 输出（08-19）
- [x] **会话持久化**：`core/store/chat_store.py` SQLite + `/v1/chats*` 端点 + WPF 历史会话列表（08-19）
- [x] **引用来源可点击**：来源角标/卡片可交互（08-24）
- [x] **渲染 markdown**：Markdig → FlowDocument（08-24）

### P2 — 工程完善
- [x] **CLI chat 的流式输出**：同 P1 第 2 项（08-19）
- [x] **WPF 聊天页上下文窗口管理**：token 预算（`RagMaxHistoryTokens`，默认 4096）+ 历史条数上限（`DOC2MIND_RAG_MAX_HISTORY_MESSAGES`，默认 20，08-29 可配）+ 会话消息数展示
- [ ] **引用来源下钻原文**：点击来源显示分块全文预览（批次 2，待设计确认）
- [ ] **会话管理增强**：重命名 / 搜索过滤历史会话（批次 2，待设计确认）

### P3 — 竞品差异化方向
- [x] **知识图谱**（chunk → 实体抽取 → 关系可视化）：**已实现**（08-29 审查确认）—— `GraphView` / `GraphViewModel` / `Resources/GraphTemplate.html`（WebGL 水球体渲染）+ 后端 8 个 `/v1/graph/*` 端点 + `core/extractor.py` LLM 实体关系抽取并落库 + MCP `graph_get`
- [x] **文件系统监控自动摄入**（watchdog）：**代码已实现**（08-29 审查确认）—— `core/file_watcher.py`，由 `server/http.py:2870` 服务启动时拉起，配置项 `WatchPaths` / `WatchDebounceSeconds`；⚠️ **依赖 `watchdog` 未安装于当前环境，功能实际不可用且测试整文件 skip**（见审查报告 AUD-021）
- [ ] 多用户/多集合隔离的 Web 管理端
- [ ] 性能：嵌入缓存、GPU int8 量化嵌入

> **⚠️ 2026-08-29 审查回填**：本节 P3 前两项此前标记为"未实现"，实际代码已落地，本次按实况更新勾选状态。
> 完整的实现度审查见 [`docs/audit/2026-08-29-功能实现审计报告.md`](audit/2026-08-29-功能实现审计报告.md)。

---

## 三、已知技术债 / 风险

| 项 | 说明 | 状态 |
|---|---|---|
| `rag_min_score` 默认 0.0 | 默认不过滤低分噪声 | 已修复：组件分 [0,1] 量纲阈值 |
| 会话历史内存无上限 | 单会话 20 条截断，会话数无上限 | 已修复：`_MAX_SESSIONS=100` LRU |
| ruff 遗留 46 个 lint | 多为 N806/SIM105/F821 假阳性（`from __future__ import annotations` 下的引用名） | 建议：不阻塞，可后续统一清理 |
| ~~SSE 流式暂为全量收集~~ | `/v1/chat/stream` 已改 asyncio.Queue 桥接真流式（08-19） | 已闭环 |
| `openai` SDK 走 `llm` extras | 依赖体积 +~10MB | 文档已说明，保持 |
| **BM25 短词失效** | `bm25_index` 用 trigram 分词器，<3 字符查询词（"气缸"、"IP"）**完全不召回**，混合检索静默退化为纯向量 | **已修复（2026-09-02）**：短词走 `LIKE '%词%'` 兜底与 FTS5 合并计分，新增 4 个回归测试 |
| **SSE 缺少收敛上限** | 无全局时长/心跳次数上限；客户端 `ReadLineAsync` 未传 CancellationToken → 后端卡死时 UI 永久转圈 | **已修复（2026-09-02）**：队列背压 + put 超时 + 600s/20 心跳上限；WPF 取消令牌 + 30s 空闲超时 |
| **多轮历史非原子写入** | user/assistant 两次独立事务，异常留孤儿轮、并发下上下文错序 | **已修复（2026-09-02）**：`ChatStore.append_turn` 单事务落库 + 并发配对测试 |
| **curate 未接通 UI** | `/v1/curate` 后端完整可用 + MCP 已暴露，但客户端零入口 | **已修复（2026-09-02）**：质量看板新增「AI 知识库整理」：只读预览 → 确认执行，6 个单测 |
| **watchdog 未安装** | `requirements-server.txt` 声明但环境缺失，文件监控不可用 | **已解决（2026-09-02）**：已安装，`test_file_watcher.py` 3 项通过，功能可用 |

---

## 四、接手建议

1. 先 `git status` + `git diff HEAD` 熟悉本会话改动范围
2. 重跑验证（**基线已更新，2026-09-02 实测**）：`python -m pytest tests/ -q`（**442 通过 / 1 跳过**；唯一跳过为 SSE 无限流集成测试）+ `dotnet test DocMind.Tests -v q`（**250 通过**）
3. 冒烟 RAG：配置 LLM 后 `doc2mind chat "问题"` 或 WPF「对话」页
4. 按 P0 → P3 顺序推进；每项完成后按 AGENTS.md 约定用 `doc2mind ingest_text` 沉淀经验