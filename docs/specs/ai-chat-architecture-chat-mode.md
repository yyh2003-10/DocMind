# 规格：AI 对话架构统一 —— ChatMode（rag / agent / auto）可切换与可默认

> 状态：`implementing`（用户已确认 §8：出厂 rag / auto 偏保守 / 总闸与默认分离 / 输入框选择器 / Toast+status / 收口设置推送）  
> 更新：2026-09 — 批次 A/B/C 后端与 D/E 客户端主干已落地  
> 关联：`docs/specs/agent-capability-upgrade-plan.md`、`docs/handoffs/zcode-agent-web-search-upgrade.md`

---

## 0. 问题与目标

### 0.1 用户诉求

1. **想切换就切换**：对话过程中可临时指定 RAG / Agent / 自动，不必进设置页重启认知。  
2. **可设默认**：全局默认可设为 `auto`（推荐）或 `agent` / `rag`。  
3. **整体架构可讨论、可演进**：不是再打补丁，而是明确「模式从哪来、谁决定、如何降级、如何观测」。

### 0.2 现状痛点（已核实）

| # | 痛点 | 证据 |
|---|---|---|
| P1 | 双路径分叉：RAG 与 Agent 各自一套联网/证据/生成逻辑 | `rag.py` vs `chat_agent.py` |
| P2 | 模式选择残缺：仅有全局 Agent 开关，无会话内三态切换 | `ChatView` 只有联网 off/normal/deep |
| P3 | 门禁静默：`agent_mode_enabled=false` 时 agent 请求直接回落，用户难感知 | `http.py` 仅日志 |
| P4 | 默认单一：双方默认 RAG；「默认 Agent / 默认 auto」无配置面 | `config.agent_mode_enabled=false` |
| P5 | auto 无路由：没有规则/策略决定「这题该走哪条」 | 代码中无 `resolve_chat_mode` 接线 |
| P6 | 设置曾只写本地 Agent 开关不推后端（已部分修复，需纳入本规格闭环） | `SettingsViewModel` 已加推送 |

### 0.3 目标（Done 定义）

1. 请求契约支持 `chatMode: rag|agent|auto`，优先级清晰。  
2. 对话页可切换模式，重启后恢复上次选择；设置页可改**全局默认模式**。  
3. `auto` 规则路由可测、可观测（status 帧写清「为何走 RAG/Agent」）。  
4. 门禁关闭时：`agent` / `auto→agent` **可见降级**，不静默装死。  
5. 后端配置可持久化 `chat_mode_default`。  
6. 单测覆盖路由矩阵；手测清单可复现「什么是挠度 + 深度联网」等用例。

### 0.4 非目标（本规格不做）

- 不合并 RAG/Agent 为单一 ChatEngine 大重构（列入后续 P3）。  
- 不引入 LLM 参与的 auto 路由（成本/延迟；规则版先行）。  
- 不默认打开付费 Search Provider。  
- 不改引用门控阈值、证据条视觉、deep_qa 篇幅轨主体。  
- 不把 ZCode 桌面端作为运行时依赖。

---

## 1. 目标架构总览

### 1.1 逻辑架构

```text
                    ┌─────────────────────────────────────┐
                    │  UI 层（WPF Chat / Settings）        │
                    │  ChatMode 选择器 + 默认模式 + 角标    │
                    └─────────────────┬───────────────────┘
                                      │ chatMode + enableWebSearch + webSearchMode
                                      ▼
                    ┌─────────────────────────────────────┐
                    │  API 层（POST /v1/chat/stream）      │
                    │  ChatRequest.chatMode                │
                    │  Config.chat_mode_default            │
                    └─────────────────┬───────────────────┘
                                      ▼
                    ┌─────────────────────────────────────┐
                    │  路由层 chat_mode.resolve_chat_mode  │
                    │  优先级 + auto 规则 + 门禁降级        │
                    │  → ChatModeDecision(mode, reason)    │
                    └───────────┬─────────────┬───────────┘
                                │             │
              mode=rag          │             │          mode=agent
                                ▼             ▼
                    ┌──────────────┐   ┌──────────────────┐
                    │ RAG Execution│   │ Agent Execution  │
                    │ Policy=once  │   │ Policy=loop      │
                    │ +深度弱证据补搜│   │ kb/web 工具循环   │
                    └──────┬───────┘   └────────┬─────────┘
                           │                    │
                           └──────────┬─────────┘
                                      ▼
                    ┌─────────────────────────────────────┐
                    │  统一出口（渐进收敛，本规格不强制）    │
                    │  sources / evidence / done 帧契约    │
                    └─────────────────────────────────────┘
```

### 1.2 模式语义

| 模式 | 语义 | 延迟 | 典型场景 |
|---|---|---|---|
| `rag` | 一次检索（库+可选联网）→ 单次生成；禁止工具调用 | 低 | 库内有货、闲聊、简单事实 |
| `agent` | 规划 + 工具循环（kb_search/web_search 可多轮）→ 生成 | 中高 | 研究、弱证据交叉印证、任务型 |
| `auto` | **规则路由**到 rag 或 agent；每轮 status 可解释 | 视路由 | 默认推荐；用户不想管模式时 |

### 1.3 决策优先级（契约）

```text
1. continueWriting = true          → rag（续写不进工具链）
2. 消息级 chatMode ∈ {rag,agent,auto}
   （旧字段 agentMode=true / mode=agent ≡ chatMode=agent）
3. 否则全局默认 chat_mode_default（服务端）/ 客户端 LastChatMode（UI 回显）
4. 门禁 agent_mode_enabled=false
   → 请求 agent 或 auto 路由到 agent 时：执行 rag，degraded=true，status 可见
5. auto 规则（门禁开启时）
```

**注意**：客户端「会话内选择」覆盖「设置默认」；设置默认影响「未显式选择/新建会话」的初始值。服务端最终裁决始终以请求字段 + 后端配置 + 门禁为准。

---

## 2. auto 规则路由（规则版 v1）

### 2.1 设计原则

- **零额外 LLM 调用**；纯规则，毫秒级。  
- **保守升级**：默认偏 RAG（快、省）；仅明确信号升级 Agent。  
- **可解释**：每个 decision 必须有中文 reason。  
- **可关**：`chat_mode_default=rag` 时 auto 仍可用（消息级），但默认体验仍是 RAG。

### 2.2 规则表 v1（按序短路）

| 序 | 条件 | 结果 | reason 模板 |
|---|---|---|---|
| R0 | continueWriting | rag | 续写优先走 RAG |
| R1 | 显式 rag | rag | 用户指定 RAG |
| R2 | 显式 agent 且门禁开 | agent | 用户指定 Agent |
| R3 | 显式 agent 且门禁关 | rag + degraded | 后端未开启 Agent，回落 RAG |
| R4 | auto 且门禁关 | rag | 自动模式：后端未开启 Agent |
| R5 | auto 且 query 空 | rag | 空查询 |
| R6 | auto 且 deep 联网 且（定义/任务关键词） | agent | 深度联网+研究/定义型 → Agent |
| R7 | auto 且 普通联网 且 定义/原理题 | agent | 定义题+联网 → 多源比对 |
| R8 | auto 且 任务/排错/对比关键词 且 len≥8 | agent | 任务型 → Agent |
| R9 | 其余 auto | rag | 常规问答 → RAG |

### 2.3 关键词（v1，可配置预留）

- 定义：`是什么|什么是|啥是|定义|含义|概念|原理|指的是`  
- 任务/Agent 倾向：`对比|选型|架构|故障|报错|排查|排错|优化方案|调研|综述|复现|根因|怎么做|如何实现|设计一|评估|benchmark|压测`  
- 问候排除：`你好|您好|hi|hello|在吗|谢谢…`

### 2.4 与现有链路的关系

| 路径 | auto 选中后行为 |
|---|---|
| rag | 现有 `rag_answer_stream`；深度弱证据仍走**已实现的补搜** |
| agent | 现有 `agent_answer_stream`；web_search 已可迭代 |

auto **不替代** RAG 内补搜，也不替代 Agent 工具循环——它只决定**入口**。

### 2.5 后续演进（不在 v1）

- P1.5：auto 在 RAG 结束后若 evidence.single_source 且用户开了深度联网 → **会话内提示**「可切换 Agent 重试」（不自动二次生成，控成本）。  
- P2：接入意图分类 `query_type`（planner 已有）作为 auto 特征。  
- P3：统一 ExecutionPolicy，路由层只换 policy。

---

## 3. 分模块详细计划

### 模块 A — 后端路由核心 `core/agent/chat_mode.py`

**职责**：纯函数路由，无 I/O。  
**已 WIP**：文件已存在（`resolve_chat_mode` / `ChatModeDecision` / `decision_status_message`），需评审对齐本规格后锁定。

| 项 | 内容 |
|---|---|
| 输入 | requested, agent_flag, legacy_mode, agent_allowed, default_mode, query, enable_web_search, web_search_mode, continue_writing |
| 输出 | `ChatModeDecision(mode, requested, reason, degraded)` |
| 验收 | 单测覆盖 R0–R9 矩阵 + 旧字段兼容 |
| 风险 | 规则过激导致简单题误入 Agent → 用 R9 兜底 + 词表保守 |

**任务**  
- A1 锁定规则表与 reason 文案  
- A2 单测 `tests/test_chat_mode_route.py`  
- A3 导出到 `core/agent/__init__.py`（可选）

---

### 模块 B — HTTP 契约与路由接线 `server/http.py`

**职责**：解析请求 → 调用 resolve → 选 generator → status 可见。

| 字段 | 类型 | 说明 |
|---|---|---|
| `chatMode` | `rag\|agent\|auto\|null` | 新增；null = 用服务端默认 |
| `agentMode` | bool | 兼容保留 |
| `mode` | string | 兼容 `"agent"` |
| Config | `chat_mode_default` | GET/POST `/v1/config` |
| Config | `agent_mode_enabled` | 门禁，已有 |

**路由伪代码**

```text
decision = resolve_chat_mode(
  req.requested_chat_mode(),
  agent_flag=req.agent_mode,
  legacy_mode=req.mode,
  agent_allowed=settings.agent_mode_enabled,
  default_mode=settings.chat_mode_default,
  query=req.query,
  enable_web_search=req.enable_web_search,
  web_search_mode=req.resolved_web_search_mode(),
  continue_writing=req.continue_writing,
)
yield status(decision_status_message(decision))
if decision.mode == agent: agent_answer_stream(...)
else: rag_answer_stream(...)
done 帧附加: chat_mode=decision.mode, chat_mode_reason, chat_mode_degraded
```

**兼容**  
- 旧客户端只传 `agentMode=true` → 显式 agent，门禁逻辑不变。  
- 旧客户端不传 chatMode → `default_mode`（配置默认建议 `auto`，**但分发版配置默认保持 `rag`/现状，避免行为突变**；个人可在设置改 auto）。

**默认策略（产品决策，请确认）**

| 场景 | 建议默认 |
|---|---|
| 本机开发/个人 | `chat_mode_default=auto` |
| 分发安装包 | `chat_mode_default=rag` + 设置可改；`agent_mode_enabled=false` |
| 你当前诉求 | 倾向 **auto 可设默认**；是否「出厂即 auto」需你拍板 |

**任务**  
- B1 ConfigUpdate/ConfigResponse 增加 `chat_mode_default`  
- B2 Settings 持久化（config.toml 字段）  
- B3 chat stream 路由改用 `resolve_chat_mode` + status/done 附加  
- B4 降级日志与 status 双写  
- B5 测试：`tests/test_chat_mode_http.py`（mock LLM/store）

---

### 模块 C — 后端配置 `core/config.py`

| 配置项 | 默认建议 | 说明 |
|---|---|---|
| `agent_mode_enabled` | `false`（分发）/ 可改 | 商用门禁 |
| `chat_mode_default` | **待确认** `rag` 或 `auto` | 全局默认回答模式 |
| `chat_mode_auto_enabled` | `true` | auto 规则总开关；false 时 auto≡rag |

**任务**  
- C1 增加字段与文档注释  
- C2 config.toml 读写兼容（缺省回落）

---

### 模块 D — 客户端数据与请求 `DocMind/`

| 文件 | 改动 |
|---|---|
| `Models/ChatRequest.cs` | 增加 `ChatMode`（JsonPropertyName `chatMode`） |
| `AppSettings.cs` | `LastChatMode`（会话页恢复）、`DefaultChatMode`（设置默认） |
| `Models/BackendConfig.cs` | Config 读写 `ChatModeDefault` |
| `ChatViewModel` | `ChatModeChoices` / `SelectedChatMode`；发送时填 `ChatMode`；`AgentMode` 可废弃为兼容镜像 |
| `SettingsViewModel` / `SettingsView` | 默认模式 ComboBox；保存推后端 |
| `ChatView.xaml` | 联网 ComboBox **旁**增加「回答模式」ComboBox |

**UI 文案（建议）**

| 选项 | Label | Tooltip |
|---|---|---|
| auto | 自动 | 按问题类型自动选择 RAG 或 Agent（推荐） |
| rag | 知识库 RAG | 本地库优先，一次检索作答，更快 |
| agent | Agent 工具 | 多轮检索/联网工具循环，适合研究与弱证据题 |

**交互**  
- 切换模式：写入 `AppSettings.LastChatMode` 并 Save（同联网模式）。  
- 新建会话：用 `DefaultChatMode`，若空则 `LastChatMode`，再空则 `auto`（与后端协商后确定）。  
- 角标：思考区已有 status「回答模式：…」；输入框旁可显示当前模式短标签。

**任务**  
- D1 模型与序列化  
- D2 ChatViewModel 绑定与请求组装  
- D3 ChatView UI  
- D4 Settings 默认模式 + 推送 `/v1/config`  
- D5 兼容：`AgentModeEnabled` 总闸仍保留（设置「允许 Agent」），与「默认模式」分离  
  - 总闸关：选择 agent 时 UI 提示将回落  
  - 总闸开：auto/agent 可用  

---

### 模块 E — 可观测性与前端呈现

| 层 | 内容 |
|---|---|
| SSE status | `✔/⚠ 回答模式：RAG/Agent · <reason>` |
| done 帧 | `chat_mode`, `chat_mode_requested`, `chat_mode_reason`, `chat_mode_degraded` |
| ThinkingStep | 解析「回答模式」「后端未开启 Agent」为 pill |
| 日志 | degraded 时 warning |

**任务**  
- E1 status/done 契约  
- E2 ThinkingStep / ChatViewModel 展示  
- E3 文档 `docs/api.md` 补字段

---

### 模块 F — 测试与验收

| 层级 | 用例 |
|---|---|
| 单元·路由 | R0–R9 矩阵；agentMode 兼容；default_mode |
| 单元·HTTP | chatMode=agent 门禁开走 agent；门禁关 degraded；auto+deep+定义 → agent |
| 回归 | 既有 agent/RAG/web 测试全绿 |
| 手测清单 | 见 §5 |

**任务**  
- F1 `tests/test_chat_mode_route.py`  
- F2 `tests/test_chat_mode_http.py`  
- F3 手测记录写入 `docs/verification/chat-mode-acceptance.md`

---

### 模块 G — 文档与知识沉淀

| 产出 | 说明 |
|---|---|
| 本规格 | `docs/specs/ai-chat-architecture-chat-mode.md`（本文） |
| api.md | 请求/配置字段 |
| handoff | 更新 zcode-agent handoff 或新建 chat-mode handoff |
| 知识库 | 确认实现后 `ingest_text` collection=docmind |

---

## 4. 接口契约汇总

### 4.1 请求 `POST /v1/chat`（增量）

```jsonc
{
  "query": "什么是挠度",
  "enableWebSearch": true,
  "webSearchMode": "deep",
  "chatMode": "auto",          // rag | agent | auto
  "agentMode": false,           // 兼容；chatMode 优先
  "continueWriting": false
}
```

### 4.2 配置 `GET/POST /v1/config`（增量）

```jsonc
{
  "agent_mode_enabled": true,
  "chat_mode_default": "auto",
  "chat_mode_auto_enabled": true
}
```

### 4.3 done 帧（增量）

```jsonc
{
  "done": true,
  "mode": "rag",                 // 实际执行（rag|agent，Agent 路径已有 mode=agent）
  "chat_mode": "rag",
  "chat_mode_requested": "auto",
  "chat_mode_reason": "自动模式：常规问答 → RAG",
  "chat_mode_degraded": false,
  "sources": [],
  "evidence": {}
}
```

> 注意：Agent 路径 done 已有 `"mode":"agent"`。统一字段建议同时写 `chat_mode`，前端只依赖 `chat_mode`。

---

## 5. 手测验收清单（实现后）

| # | 步骤 | 期望 |
|---|---|---|
| H1 | 设置总闸 Agent=开，默认模式=auto；问「你好」 | status：auto→RAG；答案短 |
| H2 | 同设置；问「什么是挠度」+ 深度联网 | status：auto→Agent（或 RAG 补搜，视词表）；原因可读 |
| H3 | 对话页强制 RAG + 深度联网问挠度 | 不进 Agent loop；深度补搜 status 可出现 |
| H4 | 对话页强制 Agent | 走 agent_plan/tool_call 轨迹 |
| H5 | 总闸关 + 强制 Agent | ⚠ 回落 RAG，degraded=true |
| H6 | 总闸关 + auto | RAG，不报错 |
| H7 | 设置默认=rag，新建会话不改选择 | 请求 chatMode 为 rag 或省略后服务端默认 rag |
| H8 | 续写「继续」 | 强制 RAG，不进 Agent |
| H9 | 重启客户端 | LastChatMode 恢复 |
| H10 | GET /v1/config | chat_mode_default 与设置一致 |

---

## 6. 实现顺序（确认后执行）

| 批次 | 模块 | 验证 |
|---|---|---|
| 1 | A 路由核心 + F1 单测 | pytest 路由矩阵绿 |
| 2 | B+C HTTP/配置接线 + F2 | pytest HTTP mock 绿 |
| 3 | D+E 客户端 UI/请求/展示 | 代码审查 + 有条件则 WPF 构建 |
| 4 | 手测 H1–H10 + api/handoff/知识库 | 验收文档 |

每批完成即跑相关测试；**不跨批大爆炸提交逻辑**。

---

## 7. 风险与缓解

| 风险 | 缓解 |
|---|---|
| auto 误路由导致变慢/变贵 | 规则保守；默认分发仍 rag；status 可解释 |
| 弱模型 Agent 效果差 | 门禁+总闸；用户可强制 rag；native tool 失败已有编排降级 |
| 双路径代码继续分叉 | 本规格只做路由；P3 统一 ChatEngine 单独立项 |
| 旧客户端兼容 | chatMode 缺省时行为 = 现配置默认；agentMode 仍生效 |
| 设置「总闸」与「默认模式」混淆 | UI 分组文案：能力开关 vs 默认策略 |
| WPF NuGet 环境债 | 后端测试优先；C# 改动保持小步可审 |

---

## 8. 待你确认的决策点

请逐项确认（可改）：

1. **全局默认 `chat_mode_default` 出厂值**  
   - [ ] A. `rag`（保守，分发友好）**推荐给出厂**  
   - [ ] B. `auto`（更接近网页版智能路由）  
   - [ ] C. 由设置首次启动向导选择  

2. **auto 规则 v1 是否按 §2.2 锁定**（偏保守）  
   - [ ] 同意  
   - [ ] 要更激进（例如更多题型默认 Agent）  
   - [ ] 要更保守（仅 deep 联网才可能 Agent）  

3. **总闸 `agent_mode_enabled` 与「默认模式」关系**  
   - [ ] 同意分离：总闸管能力，默认模式管策略（推荐）  
   - [ ] 希望总闸开时默认强制可 Agent  

4. **对话页选择器位置**  
   - [ ] 输入框工具条，与「联网」并列（推荐）  
   - [ ] 仅设置页，不进对话页  

5. **门禁关闭时 UI**  
   - [ ] status + Toast 提示回落（推荐）  
   - [ ] 仅 status  

6. **是否允许本规格实现阶段顺带完成**  
   - [ ] 设置推送 `agent_mode_enabled` 的既有 WIP 收尾（推荐，属同一产品面）  
   - [ ] 只做 chatMode，总闸推送另开任务  

---

## 9. 与长期 ChatEngine 的关系（P3 备忘）

```text
ChatEngine
  ├─ RetrievalPlanner   (kb/web/graph → 统一 Evidence 对象)
  ├─ ExecutionPolicy    (once | once_plus_rescue | loop)
  ├─ PromptTrack        (rag | deep_qa | delivery)
  └─ AnswerComposer     (引用/evidence/自省，单出口)

ChatMode 路由 = 选择 ExecutionPolicy + PromptTrack 组合
```

v1 不强制重构；所有新字段应可映射到未来 `ExecutionPolicy`，避免再分叉。

---

## 10. 结论

- **方案成立**：用请求级 `chatMode` + 全局 `chat_mode_default` + 规则版 `auto` + 门禁可见降级，满足「想切就切、可设默认」。  
- **实现前置**：需确认 §8 决策点。  
- **已 WIP**：`chat_mode.py` 草稿、`ChatRequest.chat_mode` 字段、Agent web_search 接线、设置推送 Agent 总闸——实现阶段对齐本规格后收口，不另起炉灶。

**请确认 §8 后，我按 §6 批次逐模块实现并验证。**
