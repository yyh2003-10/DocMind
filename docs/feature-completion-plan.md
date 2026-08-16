# DocMind — 功能完善计划

> 生成日期：2026-08-16  
> 面向：下一会话的接续开发  
> 前置：本文档需配合 `HANDOVER.md` 与本会话提交（RAG + Chat LLM 集成 + WPF 聊天界面 + 设置页大模型配置）阅读

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
- [ ] 本会话全部改动**尚未提交**。审查 diff → 分逻辑提交

### P1 — RAG 体验完善
- [ ] **SSE 流式 WPF 对接**：`ChatViewModel` 尚未接入 `/v1/chat/stream`，目前仍是全量请求
- [ ] **SSE 流式 CLI 对接**：`doc2mind chat` 当前等完整回答，未用 `rag_answer_stream`
- [ ] **会话持久化**：`_CHAT_SESSIONS` 是进程内 dict，HTTP/MCP 服务重启后多轮上下文丢失。预估：SQLite 会话表 ~150 行实现，兼容现有 LRU 逻辑
- [ ] **引用来源可点击**：WPF 对话页的 `[1] 文件.pdf` 目前纯文本
- [ ] **渲染 markdown**：对话回答目前是纯文本 TextBlock

### P2 — 工程完善
- [ ] **CLI chat 的流式输出**：`doc2mind chat` 目前等全部生成完才打印
- [ ] **WPF 聊天页上下文窗口管理**：多轮对话历史长度不受控

### P3 — 竞品差异化方向
- [ ] 知识图谱（chunk → 实体抽取 → 关系可视化）
- [ ] 文件系统监控自动摄入（watchdog）
- [ ] 多用户/多集合隔离的 Web 管理端
- [ ] 性能：嵌入缓存、GPU int8 量化嵌入

---

## 三、已知技术债 / 风险

| 项 | 说明 | 状态 |
|---|---|---|
| `rag_min_score` 默认 0.0 | 默认不过滤低分噪声 | 已修复：组件分 [0,1] 量纲阈值 |
| 会话历史内存无上限 | 单会话 20 条截断，会话数无上限 | 已修复：`_MAX_SESSIONS=100` LRU |
| ruff 遗留 46 个 lint | 多为 N806/SIM105/F821 假阳性（`from __future__ import annotations` 下的引用名） | 建议：不阻塞，可后续统一清理 |
| SSE 流式暂为全量收集 | `/v1/chat/stream` 用 `run_in_executor` 收集全部 token 后一次 SSE 输出，非逐 token 推送 | 下一步：`asyncio.Queue` 桥接实现真流式 |
| `openai` SDK 走 `llm` extras | 依赖体积 +~10MB | 文档已说明，保持 |

---

## 四、接手建议

1. 先 `git status` + `git diff HEAD` 熟悉本会话改动范围
2. 重跑验证：`python -m pytest`（125 测试）+ `dotnet build WpfApp1.Tests && dotnet test WpfApp1.Tests --no-build`（47 测试）
3. 冒烟 RAG：配置 LLM 后 `doc2mind chat "问题"` 或 WPF「对话」页
4. 按 P0 → P3 顺序推进；每项完成后按 AGENTS.md 约定用 `doc2mind ingest_text` 沉淀经验