---
name: ppt-auto-create
overview: 让 DocMind 聊天助手自动识别「做 PPT/研报/表格/看板」等创作意图，后端自动切换创作人设并注入 :::artifact 规范，使 LLM 输出结构化创作物而非纯文字，前端自动解析并导出可下载的 PPTX/DOCX/XLSX/HTML。
todos:
  - id: planner-creative
    content: planner 增加 creative_mode 与兜底映射，TOOLS 加 create_artifact
    status: completed
  - id: rag-auto-persona
    content: rag_answer_stream 自动覆盖 persona 并回传 thinking/done 帧
    status: completed
    dependencies:
      - planner-creative
  - id: frontend-sync
    content: ChatViewModel 解析帧同步 SelectedPersona 创作模式
    status: completed
    dependencies:
      - rag-auto-persona
  - id: backend-tests
    content: 新增 planner 创作识别与 mode 映射单测
    status: completed
    dependencies:
      - planner-creative
  - id: frontend-tests
    content: 补充自然语言自动路由到 ppt persona 的前端测试
    status: completed
    dependencies:
      - frontend-sync
  - id: review
    content: 用 [skill:code-review] 审查改动，回归既有分支
    status: completed
    dependencies:
      - backend-tests
      - frontend-tests
---

## 用户需求

用户在使用 DocMind 桌面端聊天助手时，直接说「帮我做个 PPT」之类的自然语言，助手只返回一段纯文字大纲，并没有真正调用 PPT 创作功能生成可下载的 PPTX 文件。

## 产品概述

让聊天助手具备自动意图识别能力：当用户用自然语言表达创作诉求（做 PPT、写研报、做课件、出对比表、生成看板等）时，后端自动切到对应的创作人设并输出 `:::artifact` 结构化块，前端自动解析创作物并导出为物理文件，无需用户手动切人设或点按钮。同时完整保留既有手动「📊 制作PPT」按钮与人设下拉能力。

## 核心功能

- 后端智能规划器（planner）识别创作类意图，并判定具体创作模式（ppt/doc/lesson/table/web）
- `rag_answer_stream` 在用户未显式选创作人设时，按规划结果自动覆盖本次请求的 persona 为人设创作模式（仅本次生效，不入全局、不落盘）
- 创作请求仍保留知识库检索与附件透传，作为生成素材
- 后端把实际生效的 persona 回传给前端，前端同步切换人设下拉框，保证下一句仍走创作模式、UI 状态一致
- 前端已有的 Artifact 解析与自动导出逻辑复用，创作物自动生成 PPTX/DOCX/XLSX/HTML 并可在右侧工作台预览、放映、导出
- 新增后端 planner 单测与前端路由测试，覆盖「自然语言自动路由到创作人设且产出 artifact」的场景

## 技术栈

- 后端：Python（DocMind 知识库后端，FastAPI + 同步生成器 `rag_answer_stream`）
- 规划器：现有 `src/doc2mind/core/agent/planner.py`（LLM 规划 + 正则兜底）
- 创作人设：现有 `src/doc2mind/core/creator/prompts.py` 的 `CREATIVE_PERSONA_PROMPTS`（已含 `:::artifact` 规范）
- 前端：WPF (C#) `DocMind/ViewModels/ChatViewModel.cs`，已具备 `ArtifactRegex` 解析与 `AutoExportArtifactAsync` 自动导出
- API 契约：`src/doc2mind/server/http.py` 的 `ChatRequest.persona` 与 SSE 流式帧

## 实现方案

### 总体策略

在「意图规划 → 人设注入 → 前端回传同步」三个环节补齐创作路由，复用已有的 `:::artifact` 机制与前端 Artifact 工作台，做到最小改动、零新架构。

### 关键技术决策

1. **planner 增加 `creative_mode` 输出**

- `AgentPlan` 新增可选字段 `creative_mode: str | None`（取值 ppt/doc/lesson/table/web）。
- `plan_with_llm` 的规划提示词增加 `creative_mode` 字段说明与取值映射；解析 JSON 时填入。当 `query_type=="creative"` 时，按关键词在 prompt 里引导 LLM 给出 mode；同时做本地兜底映射（避免 LLM 乱填）。
- `_fallback_regex_plan` 增加 creative 分支（复用 `intent.py` 的 `_CREATIVE_PATTERNS` 思路），把正则命中的创作意图映射为 mode：ppt|幻灯片|演示 → ppt；研报|报告|公文|方案|汇报 → doc；课件|教案|课程 → lesson；对比|矩阵|排期|表格 → table；看板|网页|html → web；其余创作 → ppt。
- `TOOLS`（AGENT_TOOLS）增加 `create_artifact` 条目，用于 thinking 帧向用户展示「正在创作 PPT…」，保持现有展示逻辑不变。

2. **`rag_answer_stream` 自动切 persona（仅本次请求）**

- 在 `agent_plan = plan_with_llm(...)` 之后、组装 messages 之前，判断：
`if agent_plan.query_type == "creative" and (persona not in CREATIVE_MODES): persona = agent_plan.creative_mode`
- `CREATIVE_MODES = {"ppt","doc","lesson","table","web"}` 常量复用自 `_PERSONA_PROMPTS`。
- 仅覆盖函数内的局部 `persona` 变量，不修改全局 Settings、不落盘（保持幂等、不污染其他会话）。
- 创作仍需素材：`_build_context_and_messages` 中 `attachments if "knowledge_base" in enabled_tools else None` 对 creative 分支已 enable_knowledge_base，保持透传；检索状态帧照常展示。

3. **回传生效 persona，前端同步 UI**

- 在现有 thinking 规划帧（json `{"type":"thinking",...}`）和最终 done 帧中附加 `persona` 字段（= 实际生效 persona）。
- 前端 `ChatViewModel` 解析帧时，若收到 `persona` 且属于 `CREATIVE_MODES` 且不同于当前 `SelectedPersona.Id`，则静默 `SelectedPersona` 同步（不打断流式、不重发请求），保证下拉框与下一句发送一致。

### 性能与可靠性

- planner 仅在非问候时多一次低温度短响应 LLM 调用（已有），新增字段为纯字符串解析，零额外开销。
- 创作模式仍走知识库检索（可能多一次向量检索），但属预期素材获取；可在 planning 阶段对 creative 复用既有 `knowledge_base` 工具，不新增检索通路。
- 回传字段向后兼容：旧版前端忽略未知 `persona` 字段安全无碍；旧版后端对 creative 仅缺自动路由，不影响已有手动模式。

### 实现注意

- 严格限制在「创作路由」范围，不改动 greeting / troubleshoot / code 等既有分支与前端 Artifact 解析/导出主链路，避免回归。
- planner 的 `creative_mode` 兜底映射需与 `intent.py` 关键词保持一致，避免两处语义漂移；建议在 planner 内复用 `intent.py` 的 `_CREATIVE_PATTERNS` 或导出统一映射函数。
- 后端单测用 fake LLM client 验证 `plan_with_llm` 返回 `creative_mode`，以及 `rag_answer_stream` 在 creative 意图下透传的 `persona`（可通过 spy `_build_context_and_messages` 或直接断言 thinking 帧 persona）。
- 前端测试沿用 `FakeDoc2kbApiService` 注入 SSE 流，断言收到 thinking 帧后 `SelectedPersona.Id == "ppt"`。

## 架构设计

```mermaid
flowchart TD
    A[用户: 帮我做个PPT] --> B[ChatView 发送 persona=office]
    B --> C[/v1/chat/stream]
    C --> D[plan_with_llm 意图规划]
    D --> E{query_type==creative?}
    E -- 是 --> F[取 creative_mode=ppt]
    F --> G[rag_answer_stream 局部覆盖 persona=ppt]
    E -- 否 --> H[走原检索/对话链路]
    G --> I[_build_context_and_messages 注入 CREATIVE_PERSONA_PROMPTS.ppt]
    I --> J[LLM 输出含 :::artifact type=pptx]
    J --> K[SSE thinking/done 帧回传 persona=ppt]
    K --> L[ChatViewModel 同步 SelectedPersona=ppt]
    J --> M[前端 ArtifactRegex 解析 ArtifactItem]
    M --> N[流式完成 AutoExportArtifactAsync 生成 PPTX]
    N --> O[右侧创作工作台预览/放映/导出]
```

## 目录结构与修改点

```
src/doc2mind/core/agent/
├── planner.py            # [MODIFY] AgentPlan 增加 creative_mode; plan_with_llm 解析并兜底; _fallback_regex_plan 增 creative 分支; TOOLS 增 create_artifact
├── intent.py            # [MODIFY] 导出统一的创作意图→mode 映射函数供 planner 复用(避免关键词漂移)
src/doc2mind/core/rag.py  # [MODIFY] plan_with_llm 后自动覆盖局部 persona; thinking/done 帧附加 persona
src/doc2mind/server/http.py  # [MODIFY] ChatRequest 可选回传无需改契约, 仅确保转发(prompt 已转发, 无需大改)
src/doc2mind/core/creator/prompts.py  # [REFERENCE] 复用 CREATIVE_PERSONA_PROMPTS, 不修改
DocMind/ViewModels/ChatViewModel.cs   # [MODIFY] 解析 SSE 帧 persona 字段, 同步 SelectedPersona(创作类)
DocMind.Tests/
├── ChatViewModelTests.cs        # [MODIFY] 补充自然语言路由到 ppt persona 的测试
├── FakeDoc2kbApiService.cs      # [REFERENCE/可能微调] 支持注入 thinking/done 帧 persona
└── CreativeArtifactTests.cs     # [MODIFY] 补充「自动路由+artifact」用例
src/doc2mind/core/agent/
└── test_planner.py (或并入现有 tests)  # [NEW] planner 识别 creative+mode 单测
```

## 关键代码结构（示意）

```python
@dataclass
class AgentPlan:
    analysis: str
    query_type: str
    tools: list[ToolPlan]
    expected_output: str
    is_greeting: bool = False
    creative_mode: str | None = None   # 新增: ppt|doc|lesson|table|web

# 创作关键词 -> mode 统一映射（planner/intent 共用）
def map_creative_mode(text: str) -> str | None: ...
```

## Agent Extensions

### Skill

- **code-review**
- 用途：方案落地后对已修改的 planner / rag / ChatViewModel 代码做质量与回归审查
- 预期结果：确认新增创作路由逻辑无 bug、无破坏既有分支、符合项目 SOLID/DRY 约定

### SubAgent

- **code-explorer**
- 用途：在编写详细 plan/tasks 前，跨文件确认 `_build_context_and_messages`、`AutoExportArtifactAsync`、SSE 帧解析等调用链细节，避免遗漏同步点
- 预期结果：给出精确的字段名、方法签名与帧结构，保证 plan 可直接执行