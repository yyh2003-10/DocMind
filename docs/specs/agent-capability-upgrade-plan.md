# 规格：DocMind 对话 Agent 能力升级完整计划

> 状态：`ready-for-agent`（待评审后排期实施）
> 生成自：现状代码审查（`core/rag.py` / `core/agent/planner.py` / `server/mcp.py` / WPF `ChatViewModel`）+ 开源 agent 架构对照（Grok Build / Open Interpreter / OpenHands / Aider / Anthropic agent loop 形态）
> 约束：**只借鉴能力分层、状态机、权限模型、事件语义；不复制任何第三方源码、提示词全文或目录结构。**
> 关联文档：`docs/specs/功能契约/对话.md`、`docs/specs/功能契约规范.md`、`docs/api.md`、`docs/verification/chat-pipeline-acceptance.md`
> 主实现范围：后端 `src/doc2mind/`（rag / agent / llm / server）+ 前端 `DocMind/`（ChatViewModel / ChatView / 设置）

---

## 0. Problem Statement（问题陈述）

用户侧体感：

1. **对话写不出长文段落**——复杂任务被压成短答，或在 token 上限处静默截断。
2. **不能读写文件**——无法“根据库内资料写一份报告并保存到本地”。
3. **没有 agent 能力**——不能多步执行任务（先查库 → 再联网 → 再写文件 → 再导出 → 再自检）。

技术根因（已核实，非猜测）：

| # | 根因 | 证据位置 |
|---|---|---|
| R1 | 对话主链路是 **预规划 RAG 管线**，不是 tool-loop agent | `rag.py`：`plan_with_llm` 只在生成前跑一次；工具结果由服务端预检索注入，模型生成阶段**不能再调用工具** |
| R2 | 系统提示 **明确禁止** function_call / tool_calls，并要求“基于上下文直接写最终答案” | `rag.py` `_SYSTEM_PROMPT` / `_SYSTEM_PROMPT_SLIM` 第 4、11 条 |
| R3 | 系统提示 **主动压制篇幅**（简单题一段话、中等 300–500 字） | 同上「回答篇幅原则」 |
| R4 | 输出上限默认偏保守，且 registry 对大量模型给 4096–8192 | `AppSettings.LlmMaxTokens=8192`；`model_registry.py` 多数 spec |
| R5 | **对话链路没有文件读写工具**；文件能力只在 MCP 面或前端 artifact 魔法字符串 | `mcp.py` `create_artifact`；WPF 侧 `:::artifact` 正则解析导出 |
| R6 | 弱模型曾幻觉吐出搜索 JSON，后端用 `AnswerGuard` **拦截丢弃**，而不是执行 | `llm/output.py` AnswerGuard 注释；实测记录 `DocMind_Chat_20260912_*.md` |
| R7 | “Agent 规划”工具表（knowledge_base / web_search / create_artifact 等）只影响**回答前编排**，未形成模型可控的执行回路 | `planner.py` TOOLS；`rag_answer_stream` 仅用 `enabled_tools` 决定是否检索/联网 |

**结论**：缺的不是模型，也不是知识库工具本身，而是「**模型决策 → 工具执行 → 结果回注 → 再决策**」的主链路回路，以及长文交付的输出策略与工作区权限模型。

---

## 1. 现状能力地图

### 1.1 已有可复用资产（不要重做）

| 资产 | 位置 | Agent 化用途 |
|---|---|---|
| 知识库混合检索 | Retriever / VectorStore | 工具 `kb_search` |
| 联网搜索（deadline 可控） | `core/search/web_search.py` | 工具 `web_search` |
| 知识图谱查询 | GraphStore | 工具 `graph_query` |
| 科研写作上下文组装 | `core/agent/research.py` | 工具/子流程 `research_pack` |
| 创作导出 | `core/creator` + MCP `create_artifact` | 工具 `export_artifact` |
| 创作体检 | `inspect_artifact` | 工具 `inspect_artifact` |
| 入库/整理 | `ingest_text` / `curate`（MCP） | 工具 `ingest_note`（默认关闭或需确认） |
| 会话持久化 | ChatStore + `/v1/chats*` | Agent 轨迹可扩展落库 |
| 输出守卫 | OutputSanitizer / AnswerGuard | Agent 模式下改为「拦未声明垃圾」 |
| 模型元数据链 | `llm/metadata.py` 四级 max_tokens 推导 | 长文预算依据 |
| SSE 真流式 | `server/http.py` + WPF | 扩展 tool 事件帧 |

### 1.2 现状对话时序（问题形态）

```text
用户消息
  → planner 规划一次（选 tools 列表，仅作编排开关）
  → 服务端检索/图谱/联网（一次性）
  → 系统提示注入资料 + 禁止 tool call + 篇幅压制
  → LLM 单次生成
  → AnswerGuard 过滤
  → done 帧（sources/evidence）
```

模型在第 4 步**无法**发起第二次检索、无法读文件、无法导出。

### 1.3 目标对话时序（Agent 形态）

```text
用户任务（含模式选择：RAG | Agent）
  → [Agent] 粗规划（可选）：任务类型、预算、默认工具白名单
  → Loop Controller 进入循环（max_steps）
      模型输出：最终答案 | 一次或多次 tool_call
      → 权限检查 → 沙箱路径解析 → 执行工具
      → tool_result 摘要回注上下文
      → 下一轮模型调用
  → 最终答案 / 交付物路径
  → 轨迹落库 + done 帧（含 artifacts、trajectory 摘要）
```

---

## 2. 目标与非目标

### 2.1 目标（必须达成）

| ID | 目标 | 用户可感知结果 |
|---|---|---|
| G1 | 对话支持 **双轨模式**：RAG 问答 / Agent 任务 | 用户可切换；RAG 行为不回归 |
| G2 | Agent 模式具备 **受控工具回路** | 可多步检索/联网/读工作区/导出文件 |
| G3 | 长文能力：大纲先行 + 分块续写 + 截断可观测 | 能稳定产出 ≥2000 字报告或完整 PPT 结构 |
| G4 | 文件读写落在 **工作区沙箱**，权限分级 | 能保存 docx/md/xlsx/pptx 到约定目录并打开 |
| G5 | 工具轨迹 UI 可解释 | 思考区显示 step 时间线，可展开/取消/重跑 |
| G6 | 交付物从「前端魔法字符串」升级为「后端工具产物」 | 有真实 file_path，可 inspect 闭环 |
| G7 | 版权安全 | 无第三方源码复制；接口与命名自研 |

### 2.2 非目标（本轮不做 / 明确排除）

| 非目标 | 理由 |
|---|---|
| 任意路径文件系统读写 / 任意 shell 执行 | 风险过高；与知识库产品定位不符 |
| 完整 IDE 级 coding agent（Cursor 同级） | 产品边界是知识库 + 交付物，不是代码编辑器 |
| 多 agent 协作 / 云端任务编排 | 先做单会话 loop，稳定后再谈后台 job |
| 替换现有 MCP 对外工具面 | MCP 保留；对话主链路与 MCP **共用执行器** |
| 重写检索内核 / 图谱引擎 | 只读复用，不改内核（与 research.py 同等约束） |
| 复制 Grok/Cursor/开源项目代码或提示词 | 用户明确要求 + 合规 |

---

## 3. 目标架构

### 3.1 模块分层（自研命名）

```text
┌─────────────────────────────────────────────────────────┐
│ WPF Chat UI                                              │
│  模式切换 · 轨迹时间线 · 权限确认 · 交付物卡片 · 续写按钮 │
└───────────────────────────┬─────────────────────────────┘
                            │ HTTP + SSE（扩展事件帧）
┌───────────────────────────▼─────────────────────────────┐
│ server/http.py  对话入口                                  │
│  /v1/chat/stream  （RAG，保持兼容）                       │
│  /v1/agent/stream （Agent，新增）或同端点 mode 字段分流   │
└───────────────────────────┬─────────────────────────────┘
┌───────────────────────────▼─────────────────────────────┐
│ Agent Runtime（新建包建议：core/agent/runtime/）          │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────┐  │
│  │ LoopCtrl    │  │ ToolRegistry │  │ PermissionGate │  │
│  │ 步数/预算/取消│  │ 注册与schema │  │ L0-L3 策略     │  │
│  └──────┬──────┘  └──────┬───────┘  └───────┬────────┘  │
│         │                │                  │            │
│  ┌──────▼────────────────▼──────────────────▼────────┐  │
│  │ Workspace（chat 级沙箱根路径 + 路径规范化/越权拒绝）│  │
│  └────────────────────────────────────────────────────┘  │
│  Planner（可选粗分）· PromptPolicy（双轨提示）            │
│  LongformOrchestrator（大纲/分块/续写）                   │
│  TrajectoryStore（轨迹持久化）                            │
└───────────┬─────────────────┬───────────────────┬────────┘
            │                 │                   │
     ┌──────▼──────┐   ┌──────▼──────┐    ┌───────▼───────┐
     │ LLM Client  │   │ 既有工具执行 │    │ Creator/MCP   │
     │ tool-call 适配│  │ kb/web/graph│    │ export/inspect│
     └─────────────┘   └─────────────┘    └───────────────┘
```

### 3.2 设计原则

1. **双轨隔离**：RAG 与 Agent 提示词、工具暴露、输出守卫策略分离，避免互相污染。
2. **执行器复用**：对话工具与 MCP 工具调用同一实现层，禁止两套逻辑漂移。
3. **模型不碰实现**：模型只看到 tool schema（名称/描述/参数/权限级别），不看到代码路径。
4. **失败可见**：任何工具失败/降级必须产生用户可见事件，禁止静默空成功。
5. **取消即真取消**：stop 作用于 Loop 对象与 stop_event，立即停止后续工具与 LLM。
6. **默认最小权限**：Agent 模式默认 L0+L1；写文件/导出 L2；危险能力永不自动开。
7. **弱模型降级**：按模型能力自动减少工具数、禁止多步写文件、或退回 RAG 提示策略。

---

## 4. 核心契约设计

### 4.1 双轨模式

| 维度 | RAG 模式（默认，保持现状语义） | Agent 模式（新增） |
|---|---|---|
| 用户入口 | 现对话发送 | 对话页模式开关 / 任务型指令自动建议切换 |
| 工具暴露 | 无（服务端预检索） | ToolRegistry 白名单内工具 |
| 提示词 | 禁 tool call + 篇幅原则 + 引用约束 | 允许受控 tool 协议 + 任务交付规格 |
| 输出守卫 | AnswerGuard 拦 JSON dump | 只拦**未在 registry 声明**的 JSON 垃圾 |
| 长文 | 可选续写 | 大纲 + 分块 + 交付物 |
| 完成判据 | 正文 + sources | 正文/文件 + trajectory + artifacts |

**模式选择策略：**

- 用户显式选择优先。
- 未选择时：`planner.query_type ∈ {creative, research, task, troubleshoot}` 且任务像“要产出东西/要多步”→ UI 建议切 Agent（一键确认），**不静默强切**（避免普通问答变慢）。
- 纯问候/简单问答 → 始终 RAG/轻量路径。

### 4.2 Tool Registry（工具注册表）

每个工具条目必须包含：

| 字段 | 说明 |
|---|---|
| `tool_id` | 稳定英文 id（自研命名） |
| `display_name` | 中文展示名 |
| `description` | 给模型看的用途说明（自写，勿抄第三方） |
| `parameters_schema` | 参数类型/必填/枚举/路径约束 |
| `permission_level` | L0–L3 |
| `executor` | 后端执行函数（与 MCP 复用） |
| `result_budget` | 回注上下文的最大字符/块数 |
| `timeout_ms` | 单次执行超时 |
| `enabled_by_default` | 默认是否对 Agent 模式开启 |
| `weak_model_allowed` | 弱模型是否可用 |

**首批工具清单（MVP）：**

| tool_id | 权限 | 语义 | 数据来源 |
|---|---|---|---|
| `kb_search` | L0 | 知识库检索，返回 top 片段+来源 | 现有 Retriever |
| `web_search` | L0 | 联网搜索（deadline 可控） | 现有 WebSearchService |
| `graph_query` | L0 | 实体/topic 关联查询 | GraphStore |
| `research_pack` | L0 | 科研文献集合上下文组装 | research.py |
| `list_workspace` | L1 | 列工作区文件 | Workspace |
| `read_workspace_file` | L1 | 读工作区文件（类型/大小限制） | Workspace |
| `write_workspace_file` | L2 | 写工作区文本文件 | Workspace |
| `export_artifact` | L2 | 编译导出 PPTX/DOCX/XLSX/HTML | creator / MCP create_artifact |
| `inspect_artifact` | L1 | 交付物体检评分 | MCP inspect |
| `ingest_note` | L2 | 将结论写入知识库（默认建议确认） | ingest_text |

**明确不进 MVP 的工具：** 任意 shell、任意盘符读写、删除知识库文档、网络爬虫无限制抓取、修改系统配置。

### 4.3 Agent Loop 状态机

```text
idle
  → planning（可选）
  → running_model
      → awaiting_permission（L2+ 需确认时）
      → running_tool
      → running_model
      → ...
  → composing_final
  → succeeded
分支：
  cancelled | failed | budget_exhausted（转 partial 成功或失败见契约）
```

**Loop Controller 规则（语义级）：**

| 规则 | 默认值（可配置） | 说明 |
|---|---|---|
| `max_steps` | 8 | 一次任务最多模型/工具交替轮次 |
| `max_tool_calls_total` | 16 | 防刷 |
| `max_wall_ms` | 180000 | 总墙钟超时 |
| `max_llm_tokens_est` | 按模型 metadata | 超预算进入收尾模式 |
| 同工具连续失败 | 2 次 | 换策略或收尾，不死循环 |
| 取消 | 立即 | 后续工具不再执行；已写出文件保留并标注 |

**收尾模式（budget_exhausted）：**

- 不再开放新工具；
- 要求模型基于已有 trajectory 产出「当前最佳交付」；
- done 帧 `partial=true` + `warning` 说明原因。

### 4.4 模型工具协议（自研，不抄第三方）

**原则：**

1. 优先使用 provider **原生 tool/function calling**（OpenAI/Anthropic/Gemini/Ollama 视支持情况）。
2. 无原生支持的模型：使用 **受控结构化通道**（约定 fenced 协议块，独立解析器解析；解析失败不进入正文）。
3. **禁止**把未解析的工具 JSON 当作用户可见正文流出（现状 AnswerGuard 的教训）。
4. 合法 tool 事件走 SSE 专用帧，不走 `{"token":...}`。

**适配层接口语义（概念）：**

```text
LLMClient.chat_with_tools(messages, tools_schema, max_tokens, ...)
  -> ModelTurn {
       text_parts,
       tool_calls: [ { call_id, tool_id, arguments } ],
       finish_reason,
       usage
     }
```

- 流式场景：正文可 token 流；tool_calls 在 provider 返回完整调用对象时再进入执行（执行本身不 token 流）。
- `finish_reason ∈ {stop, tool_calls, length, content_filter, error}` 必须透传。

### 4.5 SSE 事件扩展（向后兼容）

在现有帧（`status` / `thinking` / `token` / `error` / `restart` / `done`）基础上新增。旧客户端遇到未知帧应忽略。

| 帧类型 | 语义 | 关键字段（概念） |
|---|---|---|
| `agent_plan` | Agent 粗规划结果 | `query_type`, `planned_tools`, `budget`, `mode` |
| `tool_call` | 发起工具调用 | `step`, `call_id`, `tool_id`, `arguments_summary`, `permission_level` |
| `permission_request` | 等待用户授权 | `call_id`, `tool_id`, `risk_text`, `target_path` |
| `tool_result` | 工具执行结果摘要 | `call_id`, `status(ok/error/denied)`, `summary`, `artifact_ref?` |
| `tool_error` | 工具失败 | `call_id`, `code`, `message`, `next_hint` |
| `longform_outline` | 大纲产出/更新 | `sections[]` |
| `longform_chunk` | 分块进度 | `section_id`, `status` |
| `artifact_ready` | 交付物就绪 | `artifact_id`, `format`, `file_path`, `file_name` |
| `trajectory_done` | 轨迹摘要（可并入 done） | `steps`, `tools_used[]`, `denied[]` |

**`done` 帧扩展字段：**

```jsonc
{
  "done": true,
  "mode": "agent",              // rag | agent
  "steps": 4,
  "tools_used": ["kb_search", "export_artifact"],
  "artifacts": [
    { "artifact_id": "art_1", "format": "docx", "file_path": "…", "title": "…" }
  ],
  "partial": false,
  "warning": null,
  "workspace_root": "…",        // 仅本机路径，按需返回
  "model_spec": { /* 现有 */ },
  "sources": [ /* 检索来源，Agent 下仍保留 */ ],
  "evidence": { /* 现有 */ }
}
```

**HTTP 入口建议：**

- 方案 A（推荐）：沿用 `POST /v1/chat/stream`，请求体新增 `mode: "rag"|"agent"`、`agent_options`；默认 `rag`，完全向后兼容。
- 方案 B：新增 `POST /v1/agent/stream`，旧端点不动。
- 评审时二选一；文档 `docs/api.md` 同步。

### 4.6 权限与工作区

#### 4.6.1 工作区（Workspace）

```text
<doc2mind_data>/workspaces/<chat_id>/
  ├─ inputs/          # 会话附件/用户放入（只读给模型）
  ├─ drafts/          # 草稿、分块中间产物
  ├─ artifacts/       # 导出的 PPTX/DOCX/XLSX/HTML
  └─ notes/           # 模型写的 md 笔记
```

规则：

1. 所有路径必须落在 `workspace_root` 内；规范化后越权 → 拒绝 + `tool_error(PATH_DENIED)`。
2. 禁止 `..`、绝对盘符、UNC、符号链接逃逸（Windows 路径规范化后校验前缀）。
3. 单文件写入大小上限（建议默认 2MB 文本 / 导出按 creator 已有限制）。
4. 读文件类型白名单：`md/txt/csv/json/html/py/cs/...` + 可选 pdf/docx 抽取（复用摄入抽取，非任意二进制）。
5. 会话删除/清理策略与 ChatStore 一致或可配置保留。

#### 4.6.2 权限级别

| 级别 | 范围 | 默认策略 | UI |
|---|---|---|---|
| L0 | 知识库检索、联网、图谱 | 自动允许 | 轨迹显示即可 |
| L1 | 读/列工作区、inspect | 自动允许（白名单目录） | 轨迹显示 |
| L2 | 写工作区、导出文件、ingest 入库 | **会话内确认一次** 或设置项「信任本会话写入」 | 权限卡片：允许一次 / 本会话允许 / 拒绝 |
| L3 | 工作区外、系统路径、危险操作 | **拒绝** | 不提供入口 |

设置项建议：

- `agent_file_write_policy`: `ask`（默认）| `session_allow` | `always_allow_workspace`
- `agent_allowed_tools`: 工具开关列表
- `agent_max_steps` / `agent_max_wall_ms`
- `llm_max_tokens` 提高时提示「长文任务」

### 4.7 长文输出策略（Longform）

**问题**：单次 `max_tokens` 不足以稳定产出长报告/PPT。

**策略（三件套）：**

1. **大纲先行**  
   - 任务字数目标 ≥ 约 1200–1500 或交付物为 PPT/研报 → 先生成结构大纲（章节标题 + 每节要点 + 预估字数）。  
   - SSE `longform_outline`；UI 可「继续生成 / 调整大纲后继续」。

2. **分块填充**  
   - 按大纲节逐块生成；每块使用独立输出预算；块间把「已完成节摘要 + 总大纲」注入上下文，避免重复。  
   - 中间稿写入 `drafts/`。

3. **续写与截断恢复**  
   - 检测 `finish_reason=length` → done 标记截断，UI 提供「继续写」。  
   - 续写请求携带：任务目标、大纲、已完成正文尾部、续写指令（自研文案）。  
   - 禁止静默把截断稿当完整答案。

**篇幅策略（提示词双轨）：**

| 模式 | 篇幅原则 |
|---|---|
| RAG | 保留现有原则（精准、按问题复杂度） |
| Agent 问答型 | 允许按任务展开；仍禁止无意义注水 |
| Agent 交付型 | 以交付规格为准（字数/页数/结构），不受「简单题一段话」约束 |

### 4.8 Artifact 交付闭环

**现状：** 模型输出 `:::artifact ... :::` → 前端 Regex 解析 → 导出。脆弱，且不是 agent 语义。

**目标路径：**

```text
模型调用 export_artifact(content|draft_path, format, title, theme)
  → creator 编译
  → 写入 workspace/artifacts/
  → SSE artifact_ready
  → （可选自动）inspect_artifact
      → 分数低 / issues → 回注模型请求修订 → 再导出
```

兼容：保留前端对旧 `:::artifact` 的解析作为 fallback；新对话主路径走工具产物。

**交付物元数据：**

- `artifact_id`, `format`, `title`, `theme`, `file_path`, `file_name`, `file_size`, `inspect_score?`, `source_draft?`

---

## 5. 分阶段实施计划

### 总览

| 阶段 | 名称 | 周期建议 | 出口体感 | 风险 |
|---|---|---|---|---|
| **P0** | 长文止血 + 双轨提示雏形 | 2–4 天 | 能写更长、截断可续 | 低 |
| **P1** | Agent Loop MVP + 工作区读写 | 1.5–2.5 周 | 能多步任务、能写文件 | 中 |
| **P2** | 长文编排 + Artifact 后端化 + 轨迹 UI 完善 | 1.5–2 周 | 专业交付物、可解释 | 中 |
| **P3** | 工具插件化 + 项目规则 + 可选后台任务 | 按需迭代 | 接近成熟 agent 产品 | 中高 |

各阶段必须过回归门：`python -m pytest tests/ -q`、`dotnet test DocMind.Tests -v q`、`scripts/smoke.ps1`。

---

### P0 — 长文止血（不引入完整 tool loop）

**目标：** 快速改善「写不长」；为 Agent 模式预留提示与预算开关。

#### 工作项

| ID | 任务 | 涉及 | 完成判据 |
|---|---|---|---|
| P0-1 | 提示词双轨：拆分 `RAG` / `Agent` / `交付` 策略配置（配置化，不散落字符串） | `rag.py` 或新建 `core/agent/prompt_policy.py` | RAG 默认行为与现网兼容；Agent/交付策略可单独改；单测锁关键句存在/不存在 |
| P0-2 | Agent/交付策略**移除**篇幅压制与「禁止 tool call」对后续阶段的阻塞（P0 可先仅移除篇幅压制，tool 协议仍关） | 同上 | 交付型任务不再被「300–500 字」约束 |
| P0-3 | 长输出预算：对交付型请求抬高 `effective_max_tokens`（仍走 metadata 夹取） | `rag.py` + 设置 | done 帧 `model_spec` 可观测；日志含来源标签 |
| P0-4 | 截断可观测：`finish_reason=length` → `done.partial` / `warning` / UI 文案 | llm impl + WPF | 截断不再伪装成完整回答 |
| P0-5 | 「继续写」命令（非 loop 级续写）：携带上下文续写正文 | HTTP + ChatViewModel | 续写成功拼接到同一条 assistant 消息或子消息 |
| P0-6 | 设置页说明：`LlmMaxTokens` vs 模型真实输出上限 | SettingsView | 用户能理解为何有时仍截断 |
| P0-7 | 文档：`docs/specs/功能契约/对话.md` 增补 RAG 篇幅/截断出口 | docs | 契约含新出口 |

#### P0 验收（可执行）

- [ ] 同一复杂问题，Agent/交付策略下正文中位长度显著高于 RAG 策略（用固定测试集对比）。
- [ ] 人为小 `max_tokens` 触发截断 → UI 出现截断提示 + 可点「继续写」。
- [ ] `pytest tests/test_rag.py` 及相关不回归。
- [ ] RAG 模式默认提示仍含引用约束；**不**因 P0 导致 JSON dump 回升。

#### P0 明确不做

- 文件读写、tool loop、权限 UI。

---

### P1 — Agent Loop MVP + 工作区

**目标：** 用户在 Agent 模式下完成「检索 → 写笔记/导出文档到工作区」的多步任务。

#### 1) 后端运行时

| ID | 任务 | 完成判据 |
|---|---|---|
| P1-1 | 新建 `core/agent/runtime/`：`LoopController`, `ToolRegistry`, `PermissionGate`, `Workspace` | 可单测：无 LLM 时 mock 模型驱动完整 loop |
| P1-2 | 工具执行器接入首批 L0/L1/L2（复用现有检索/creator） | MCP 与 Agent 调用同一 executor 单测锁定 |
| P1-3 | LLM `chat_with_tools` 适配：OpenAI 优先，其次 Ollama/其他；不支持则受控结构化协议 | 至少 1 个 provider 端到端 tool_call 真实可跑 |
| P1-4 | Agent 提示策略：允许声明协议；交付规格注入 | 弱模型探测：工具调用失败率可接受或自动降级 |
| P1-5 | AnswerGuard Agent 模式策略：合法 tool 帧不误杀；未声明垃圾仍拦 | 回归 09-12 场景：不出现整页 JSON 正文 |
| P1-6 | HTTP：`mode=agent` 分流 + SSE 新帧 + done 扩展 | `docs/api.md` 更新；旧客户端忽略未知帧不崩 |
| P1-7 | 取消/超时/步数预算 | stop 后无新 tool 执行；done 含 partial 原因 |
| P1-8 | 轨迹落库（可先 JSON 文件或 chat_messages 扩展） | 会话回看可见 steps |

#### 2) 工作区与权限

| ID | 任务 | 完成判据 |
|---|---|---|
| P1-9 | Workspace 路径规范化 + 越权拒绝 | 单测覆盖 `..`、绝对路径、超长路径 |
| P1-10 | `write_workspace_file` / `read_workspace_file` / `list_workspace` | 能在 workspace 读写 md/txt |
| P1-11 | `export_artifact` 接入对话 Agent 回路 | Agent 任务产出真实 pptx/docx 文件路径 |
| P1-12 | L2 权限确认帧 + 设置策略 | 默认 ask；拒绝后模型收到 denied 并改道 |

#### 3) WPF

| ID | 任务 | 完成判据 |
|---|---|---|
| P1-13 | 对话页模式开关（RAG / Agent）+ 空态说明 | 用户可切换；未配置/无权限时有动作引导 |
| P1-14 | 轨迹时间线控件（step、工具名、状态、摘要） | 至少可滚动查看；可停止 |
| P1-15 | 权限确认卡片 | 允许一次 / 拒绝可用 |
| P1-16 | 交付物卡片（打开文件位置） | 点击可定位 artifacts 目录 |

#### P1 功能契约（对话 · Agent 模式）

**能完成的任务（一句话）：**  
在授权工作区内，让助手多步检索知识库/联网，并写出笔记或导出文档。

**不能完成的事：**  
任意路径读写、执行系统命令、自动删除知识库文档、保证无检索命中时的“编造引用”。

**前置条件：** LLM 已配置；后端在线；Agent 模式已开关。不满足 → 引导设置/启动后端（复用现有导航范式）。

**输入契约：** 文本任务描述；可选附件（进入 `inputs/`）；模式=agent。

**状态机：** `idle → running(tool/model 交替，可能 awaiting_permission) → succeeded | partial | failed | cancelled`

**4 个出口：**

- ✅ 成功：正文 + 轨迹 +（可选）artifacts；完成判据：`done=true` 且无 `partial`，用户可见文件路径（若要求产出文件）。
- ❌ 失败：页内错误 +「重试 / 改为 RAG / 打开设置」；失败工具在轨迹中标红。
- ⏹ 取消：停止后续 loop；已写文件保留并在 UI 标注「取消时已生成」；会话历史策略与 RAG partial 一致或显式落盘「agent cancelled」。
- ∅ 空态：知识库无命中时 Agent 仍可用 web（若开）或明确说明依据不足；与 RAG strict 拒答语义对齐并可区分。

**可执行测试（P1）：**

1. Agent 模式：提问需检索的知识点 → 轨迹出现 `kb_search` → 回答含来源。  
2. 要求「写一份大纲到 notes」→ L2 确认 → 文件存在且内容非空。  
3. 要求「导出一份 docx」→ `export_artifact` → 文件可打开。  
4. 路径参数写 `C:\Windows\...` → denied，不落盘。  
5. 生成中点停止 → 无后续 tool_call 帧。  
6. RAG 模式回归：原对话契约测试不失败。

#### P1 验收门

- [ ] 后端 pytest：runtime/workspace/permission 新用例全绿 + 原回归绿。
- [ ] 前端 dotnet test：ChatViewModel 模式/轨迹/权限相关新增用例绿。
- [ ] smoke：对话 RAG 路径 FAIL=0。
- [ ] 至少 1 次真实模型端到端（OpenAI 兼容或本机 Ollama）录屏/日志存 `docs/verification/`。

---

### P2 — 长文编排 + Artifact 闭环 + 体验完善

**目标：** 稳定产出长报告/PPT，交付物可体检迭代，体验接近专业 agent。

| ID | 任务 | 完成判据 |
|---|---|---|
| P2-1 | `LongformOrchestrator`：大纲 → 分块 → 汇总 | 2000+ 字或完整 PPT 结构稳定产出 |
| P2-2 | `longform_*` SSE + UI 大纲编辑/继续 | 用户可改大纲后继续 |
| P2-3 | Artifact 主路径工具化 + 旧 `:::artifact` fallback | 新会话以 file_path 为准 |
| P2-4 | inspect 自迭代（默认最多 1–2 轮） | 低分 PPT 自动修订一次并提示 |
| P2-5 | 轨迹 UI：展开 tool_result、重跑单步（可选，重跑需谨慎） | 至少展开查看 |
| P2-6 | 来源与引用在 Agent 正文中保持诚实 | 无依据不编造 [n]（继承现有约束） |
| P2-7 | 弱模型策略：自动关多步写文件/关 inspect 循环 | `is_weak_model` 下不进入高危 loop |
| P2-8 | 性能：loop 心跳、工具超时、日志 | 单步超时不拖死 SSE |
| P2-9 | 功能契约与 API 文档完整化 | 新人可按文档验收 |

#### P2 验收

- [ ] 指定样例任务库（科研综述、技术对比、PPT、对比表）各 1 条，Agent 模式成功率达标（建议 ≥4/5 可完成主路径）。
- [ ] 截断率较 P0 前显著下降；长文用户可感知。
- [ ] inspect 分数与 UI 展示一致。

---

### P3 — 生态与规模化（可选后续）

| ID | 任务 | 说明 |
|---|---|---|
| P3-1 | 工具插件开关 UI + 最近调用日志 | 设置页 Agent 能力面板 |
| P3-2 | 工作区 `AGENT.md` 项目规则（文风/导出目录/引用格式） | 类似业界 AGENTS 约定，自研内容 |
| P3-3 | 会话收尾知识沉淀建议 → ingest_text | 与项目 AGENTS 契约一致，需确认 |
| P3-4 | 后台 Agent Job（job_id 轮询） | 长研究/批量任务不绑死 SSE |
| P3-5 | MCP 外部工具挂载进对话 Registry（受权限策略约束） | 扩展能力面 |
| P3-6 | 评测集：intent/agent 任务回归基线 | 对齐 `eval/` 既有实践 |

P3 不阻塞 P0–P2 发布。

---

## 6. 模块改动清单（实施索引）

> 仅列改造点与职责，不写具体第三方代码。

### 6.1 后端新建

| 路径（建议） | 职责 |
|---|---|
| `src/doc2mind/core/agent/runtime/loop.py` | LoopController 状态机、预算、取消 |
| `src/doc2mind/core/agent/runtime/registry.py` | ToolRegistry 与 schema |
| `src/doc2mind/core/agent/runtime/permissions.py` | L0–L3 策略 |
| `src/doc2mind/core/agent/runtime/workspace.py` | 工作区路径与 IO |
| `src/doc2mind/core/agent/runtime/policy_prompt.py` | 双轨提示策略组装 |
| `src/doc2mind/core/agent/runtime/longform.py` | 大纲/分块/续写 |
| `src/doc2mind/core/agent/runtime/trajectory.py` | 轨迹模型与持久化 |
| `src/doc2mind/core/llm/tools.py` | chat_with_tools 适配与协议解析 |

### 6.2 后端修改

| 路径 | 改动 |
|---|---|
| `core/rag.py` | 提示词策略抽离；Agent 模式分流或委托 runtime |
| `core/llm/base.py` + impls | tool 调用请求/流式 finish_reason |
| `core/llm/output.py` | AnswerGuard 双策略 |
| `core/llm/metadata.py` / `model_registry.py` | 长文预算辅助信息 |
| `server/http.py` | mode 分流、SSE 新帧、权限确认通道（若需二次请求） |
| `server/mcp.py` | 与 Agent executor 共用实现（抽取 core 层） |
| `docs/api.md` | 契约更新 |

### 6.3 前端修改

| 路径 | 改动 |
|---|---|
| `DocMind/Models/ChatRequest.cs` | `Mode`, `AgentOptions` |
| `DocMind/Models/ChatResponse.cs` 等 | 轨迹/artifacts/done 扩展 |
| `DocMind/ViewModels/ChatViewModel*.cs` | 模式、轨迹、权限、续写、交付物 |
| `DocMind/Views/ChatView.xaml` | UI 控件 |
| `DocMind/AppSettings.cs` / SettingsView | Agent 策略项 |
| `DocMind.Tests/*` | Fake API 与 VM 测试 |

### 6.4 权限确认通道（实现选型）

| 方案 | 描述 | 优缺点 |
|---|---|---|
| A. SSE 内请求 + 独立 POST 批准 | `permission_request` 后，前端 `POST /v1/agent/permission` | 需 runtime 挂起等待；体验好 |
| B. 默认策略直接执行 | 仅设置 ask 时改为「先拒绝并提示用户改策略/重试」 | 实现快，体验差 |
| C. P1 先 B，P2 再 A | 渐进 | **推荐** |

---

## 7. 测试与验证计划

### 7.1 单元测试（后端）

| 主题 | 用例要点 |
|---|---|
| Workspace | 路径穿越拒绝；正常读写；类型白名单 |
| Permission | L0/L1 放行；L2 ask/session_allow；L3 拒绝 |
| Loop | max_steps；取消；工具失败重试上限；budget_exhausted 收尾 |
| Registry | schema 完整性；禁用工具不可调用 |
| Protocol | 原生 tool_call 解析；受控协议解析；垃圾 JSON 不进正文 |
| Longform | 大纲结构；分块拼接；截断续写上下文组装 |
| Export 共用 | Agent 调用与 MCP 调用结果字段一致 |
| RAG 回归 | 现有 test_rag/test_http 全绿 |

### 7.2 单元/集成（前端）

- ChatViewModel：模式切换、未知帧忽略、工具时间线绑定、权限命令、续写命令、交付物打开。
- FakeDoc2kbApiService 扩展 agent 帧序列。

### 7.3 契约/冒烟

- 扩展 `scripts/smoke.ps1`：RAG chat 原项 + Agent 最小链路（可 mock 或标记 SKIP-if-no-LLM）。
- `docs/verification/agent-pipeline-acceptance.md`（新建）记录真实模型验收表。

### 7.4 真实验收场景（建议入库 eval）

| ID | 任务 | 期望 |
|---|---|---|
| A-1 | 知识库已有主题：总结关键结论并写 `notes/结论.md` | kb_search + write；文件存在 |
| A-2 | 通识题 + 联网：交叉验证并标注不确定性 | web_search；无伪造引用 |
| A-3 | 生成「目录对比表」xlsx | export_artifact xlsx |
| A-4 | 生成短 PPT 并 inspect | artifact + score |
| A-5 | 要求写到工作区外路径 | denied 且不落盘 |
| A-6 | 超长报告任务 | 大纲 + 分块，无静默截断 |
| A-7 | RAG 模式复杂问答 | 行为与升级前基线一致（引用/篇幅原则） |
| A-8 | 弱模型 + Agent | 自动降级，不崩、不 JSON dump |

### 7.5 回归门（每阶段合并前）

| 层 | 命令 | 通过判据 |
|---|---|---|
| 后端 | `python -m pytest tests/ -q` | 0 fail（允许既有 skip 说明） |
| 前端 | `dotnet test DocMind.Tests -v q` | 0 fail |
| 接口 | `scripts/smoke.ps1` | FAIL=0 |
| 文档 | api/契约/验收 | 与实现字段一致 |

> 基线数字会随代码演进变化；以合并当日实测写入 verification 文档为准（HANDOVER 中历史基线仅作参考）。

---

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| 弱模型幻觉 tool JSON | 正文垃圾、用户投诉 | 双轨提示 + 协议解析失败丢弃 + Agent Guard + 弱模型降级 |
| 工具 loop 慢/贵 | 体验差、token 爆炸 | max_steps/预算/工具 result 截断/心跳 |
| 写文件误覆盖 | 用户数据损失 | 工作区隔离 + 同名策略（自动后缀或确认）+ 可撤销记录 |
| Prompt injection（网页/文档） | 模型被诱导写危险路径 | 权限层硬校验（不信模型路径）+ L3 拒绝 + 联网内容标记不可执行指令 |
| Provider tool 支持不一致 | 某些模型不能 Agent | 适配层 + 受控协议 + 设置页能力探测提示 |
| RAG 用户被 Agent 拖慢 | 默认体验回退 | 默认 RAG；显式开启 Agent；任务型仅建议不强切 |
| SSE 旧客户端兼容 | 前端解析错误 | 未知帧忽略；done 旧字段保持 |
| MCP 与 Agent 双实现漂移 | 行为不一致 | 执行器下沉 core，单测锁一致性 |
| 版权争议 | 法务风险 | 本计划仅架构概念；实现全部自研；审查 PR 禁止粘贴第三方源码/大段提示词 |
| 测试环境无 LLM | 无法 E2E | mock loop 单测为主；真实模型验收单独文档记录 |

---

## 9. 成功指标

| 指标 | 基线（现状认知） | P0 目标 | P1 目标 | P2 目标 |
|---|---|---|---|---|
| 复杂任务正文中位长度 | 偏短/易截断 | 明显提升 + 截断可见 | — | 长文任务完成率可接受 |
| 可完成「写文件」任务比例 | ≈0（对话链路） | 0 | ≥ 样例集主路径可完成 | ≥4/5 样例 |
| 可完成「导出交付物」比例 | 依赖前端 artifact 运气 | 不变 | Agent 主路径可导出 | +inspect 迭代 |
| 工具轨迹可见率 | 仅规划文案 | — | 100% Agent 会话 | 可展开详情 |
| RAG 回归失败 | — | 0 | 0 | 0 |
| JSON dump 进正文 | 曾发生 | 不升高 | Agent/RAG 均受控 | 受控 |
| 危险路径写入成功次数 | 无此能力 | 0 | **必须为 0** | 0 |

---

## 10. 排期与任务分解（建议）

> 工期按 1 名熟悉本仓的工程师全职估算；可前后端并行压缩日历时间。

### Sprint A（P0）

- A1 提示词策略抽离 + 单测  
- A2 预算/截断/续写  
- A3 设置与契约文档  
- A4 回归 + 简短 verification 记录  

### Sprint B（P1 后端）

- B1 runtime 骨架 + mock loop 单测  
- B2 工具执行器与 workspace/permission  
- B3 LLM tools 适配（OpenAI 兼容优先）  
- B4 HTTP/SSE 扩展 + api.md  

### Sprint C（P1 前端）

- C1 模式开关与请求模型  
- C2 轨迹时间线  
- C3 权限策略 v1（先 session/设置，后确认卡）  
- C4 交付物卡片 + 测试  

### Sprint D（P2）

- D1 Longform  
- D2 Artifact 工具化 + inspect 闭环  
- D3 弱模型策略与性能  
- D4 样例集验收 + 文档  

P3 进 backlog，按产品优先级拆 issue。

### Issue 打标建议

沿用项目 triage：`needs-triage` / `ready-for-agent` / `ready-for-human`。  
标签建议增加：`area:agent`, `area:chat`, `area:longform`, `risk:fs-write`。

---

## 11. 开源借鉴清单（仅概念，禁止照抄实现）

| 来源 | 借鉴概念 | 本计划中的落点 |
|---|---|---|
| Grok Build CLI（能力文档） | 权限规则与沙箱分离；会话/轨迹；inspect 诊断；MCP | PermissionGate / Workspace / Trajectory / 工具开关 |
| Open Interpreter / Codex 系开源形态 | harness 可切换；ACP；AGENTS 指令目录；沙箱执行 | 弱模型策略分层；工作区规则文件（P3） |
| OpenHands Agent Canvas | UI 与 Agent Server 分离；事件化会话；可换后端 | WPF 只做控制面；Runtime 可独立演进 |
| Aider | 代码/资料地图；变更可回滚思维 | 知识库/交付物可追溯；轨迹可回看 |
| Anthropic agent loop demo（公开文档） | 最小 loop：tools → call → result → final；安全告警 | LoopController 语义；prompt injection 防护要求 |
| Cursor（产品形态，闭源） | Agent 模式、工具面板、可解释执行、应用变更 | 对话页 Agent 模式与轨迹 UI 的产品层 |

**版权与实现纪律：**

1. 不复制上述项目的源文件、测试、提示词原文。  
2. 接口字段名、模块名、UI 文案使用 DocMind 自有命名。  
3. PR 描述中引用开源时只写“受 X 架构启发”，不附大段代码 diff。  
4. 若引入第三方库（非抄实现），走现有依赖与许可证流程（`THIRD_PARTY_LICENSES.md`）。

---

## 12. 评审检查表（开工前 / 合并前）

### 开工前

- [ ] 产品确认：默认 RAG，Agent 显式开启（或确认自动建议策略）
- [ ] 产品确认：L2 写入默认 `ask` 还是 `session_allow`
- [ ] 确认 HTTP 入口方案 A/B
- [ ] 确认工作区根目录位置与清理策略
- [ ] 确认 P1 必达 provider（例如 OpenAI 兼容 + 本机 Ollama）

### 合并前（每个 PR）

- [ ] 无第三方源码/提示词复制
- [ ] 回归门全绿
- [ ] 新字段 `docs/api.md` / 契约已更新
- [ ] 危险路径测试存在且通过
- [ ] RAG 默认体验无回归说明

---

## 13. 建议的第一批 GitHub Issues（拆单）

| 标题 | 阶段 | 标签 |
|---|---|---|
| 提示词双轨与篇幅策略抽离 | P0 | `ready-for-agent`, `area:chat` |
| max_tokens 截断可观测 + 继续写 | P0 | `ready-for-agent`, `area:chat` |
| Agent Runtime 骨架与 mock loop 单测 | P1 | `ready-for-agent`, `area:agent` |
| Workspace 路径沙箱与权限门 | P1 | `ready-for-agent`, `risk:fs-write` |
| LLM tool-calling 适配层 | P1 | `ready-for-agent`, `area:agent` |
| SSE agent 事件帧与 api.md | P1 | `ready-for-agent`, `area:chat` |
| WPF Agent 模式开关与轨迹时间线 | P1 | `ready-for-agent`, `area:chat` |
| export_artifact 接入对话 Agent 回路 | P1 | `ready-for-agent`, `area:agent` |
| Longform 大纲分块续写 | P2 | `ready-for-agent`, `area:longform` |
| Artifact 工具化 + inspect 闭环 | P2 | `ready-for-agent`, `area:agent` |
| 对话 Agent 验收文档与样例集 | P2 | `ready-for-human` |
| 工作区 AGENT.md 用户规则 | P3 | `needs-triage` |

---

## 14. 一句话路线图

> **P0 先让长文“写得出、断得见”；P1 再上工作区内的受控 tool-loop（真 agent）；P2 把长文与导出做成可迭代交付物；P3 再谈插件化与后台任务。全程 RAG 默认不回退，权限硬校验，实现全部自研。**

---

## 附录 A · 与现有功能契约的衔接

| 现有契约 | 升级后 |
|---|---|
| `功能契约/对话.md`（RAG） | 保留为 RAG 模式契约；补截断/续写出口 |
| 新增 `功能契约/对话-Agent.md` | P1 起按第 5 节契约补全并标注 ✅/⚠️/❌ |
| `docs/api.md` `/v1/chat/stream` | 增加 mode 与新帧；旧客户端兼容说明 |
| `docs/verification/chat-pipeline-acceptance.md` | 继续管 RAG 检索链路 |
| 新建 `docs/verification/agent-pipeline-acceptance.md` | 管 Agent 回路与文件权限 |

## 附录 B · 默认配置草案（评审可改）

```text
agent_mode_default = rag
agent_max_steps = 8
agent_max_tool_calls = 16
agent_max_wall_ms = 180000
agent_file_write_policy = ask
agent_enabled_tools_default = kb_search, web_search, graph_query,
                             list_workspace, read_workspace_file,
                             write_workspace_file, export_artifact,
                             inspect_artifact
agent_disabled_for_weak_model = write_workspace_file, export_artifact, inspect_loop
workspace_root = <doc2mind_data>/workspaces/<chat_id>
longform_trigger_chars = 1200
longform_max_chunks = 24
```

---

**文档结束。** 实施时按阶段开 issue，禁止在未过评审检查表的情况下直接改 RAG 默认提示词或打开任意文件系统能力。
