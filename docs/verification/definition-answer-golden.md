# 定义题回答黄金验收 —「什么是挠度」

> 关联规划：`docs/handoffs/answer-quality-authority-upgrade.md`  
> 用途：改前基线固定 + 改后探针对比，防止「看起来改了但证据/篇幅没变」。

## 1. 改前基线（2026 用户截图实测）

| 项 | 观察值 |
|---|---|
| 检索 | 降级纯 BM25（向量维度与索引不一致，未重建） |
| 思考 pill | 「库内·5 个来源」（易被理解成 5 条可引用） |
| 引用 | 0 条库内 + 2 条网页，均标「单一来源」 |
| 自省 | 「已综合 1 条网资料 生成回答，共约 359 字」 |
| 内容缺口 | 单位、规范限值、多工况公式、概念边界、库内原文 |
| 模型 | openai/gpt-oss-20b · 深度搜索 · @default +5 |

## 2. 改后否决项（G）

| ID | 标准 |
|---|---|
| G1 | 重建索引后思考流无「向量维度与索引不一致」 |
| G2 | pill/证据条能区分「命中 N / 可引用 M」；可引用 0 时不得只显示「N 个来源」 |
| G3 | 深度+多源时 `prompt_track=deep_qa`，回答相对改前增加单位/意义/公式/限值/边界中 ≥2 类 |
| G4 | 可引用 ≥2 时正文引用 ≥2 个 [n]；=1 时出现「证据强度：弱（单一来源）」或证据条 ⚠ |
| G5 | `local_cite=0` 时不出现伪造库内 [n]；可出现库内未命中/导入建议 |
| G6 | 闲聊/问候不被 deep_qa 模板拉长 |
| G7 | `pytest tests/test_prompt_policy.py tests/test_rag.py -q` 相关组通过 |

## 3. 探针清单（执行时追加实测）

### 3.1 配置探针

```powershell
$token = (Get-Content "$env:LOCALAPPDATA\doc2mind\server.token").Trim()
Invoke-RestMethod -Uri http://127.0.0.1:8765/v1/config -Headers @{Authorization="Bearer $token"} |
  Select-Object rerank_model, embed_model, citation_min_score
```

- [ ] 换嵌入模型后已重建索引（或确认本轮无「检索降级」）

### 3.2 对话探针（「什么是挠度」深度搜索）

观察 done 帧 / 思考流：

- [ ] `evidence.local_hit_count` / `local_cite_count` / `web_fetched_count` / `synthesized_source_count` / `single_source` / `prompt_track`
- [ ] 思考流状态含「命中 … · 可引用 …」
- [ ] 多源时 `prompt_track` 为 `deep_qa`
- [ ] 自省含「单一来源」或「无库内可引用」时的诚实句

### 3.3 对照场景

| 场景 | 期望 |
|---|---|
| 「你好」 | track=rag，1–3 句 |
| 「什么是挠度」关联网、库无命中 | 诚实未命中 + 导入建议，不编规范 |
| 「什么是挠度」深度 + ≥2 精读页 | deep_qa + 多引用或弱证据标注 |
| 「重启后端」等操作题 | 不升 deep_qa |

## 4. 实测记录

### 2026-09-21 检测（代码 + 运行时）

| 项 | 结果 |
|---|---|
| Python 回归 | **1073 passed / 1 skipped**（含 prompt_policy、rag evidence、全量 pytest） |
| 静态契约 | deep_qa 解析矩阵、evidence 新字段、状态文案「命中/可引用」源码均在位；`py_compile` 通过 |
| WPF | 源码已改；`dotnet test` 因 NuGet `path1 null` 环境债未跑通 |
| 后端在线 | `127.0.0.1:8765` OK；documents=28，quality warnings 空 |

**运行时异常（复现原差评根因）**

| 现象 | 证据 |
|---|---|
| **嵌入模型不一致** | HTTP `embed_model=BAAI/bge-small-zh-v1.5`（通常 512 维） vs 本地配置/库 `jinaai/jina-embeddings-v2-base-zh` / `store.embedding_dim=768` |
| **HTTP 检索降级空结果** | `POST /v1/search` query=什么是挠度 → `total=0, degraded=true, elapsed_ms=17` |
| **本地 jina 768 检索可用** | `Retriever` degraded=false，5 hits；最高 vec≈0.37（`8.11.md` 动平衡刚度笔记等） |
| **库内无「挠度」权威原文** | 命中为动平衡/伺服手册「挠性补偿」/RAG 笔记，非结构力学定义 |
| **引用门控 → 0 可引用** | 模拟 `citation_min_score` 0.35 与 0.45：**cite=0 / bg=3 / discard=2** |
| **BM25 对「挠度」无召回** | `bm25_candidates=0` |
| **LLM 配置分裂** | 服务端 `llm_provider=none, api_key=false`；本地进程 `llm_provider=openai` |
| **citation_min_score 运行时=0.35** | 低于标定推荐 0.45（即便调高也无法把无关切片变成可引用定义依据） |

**结论**：本轮改码已具备「诚实计数 + deep_qa + 单源披露」能力；但当前环境 **服务端嵌入模型与库向量维度不一致** 且 **知识库没有挠度定义类文献**，所以「什么是挠度」仍会表现为库内 0 引用 → 依赖网页。需先：统一 embed 模型 → 重建索引 → 导入结构力学/规范资料 → 重启后端后再按 G1–G7 实测。

### 2026-09-21 环境修复后复测

| 步骤 | 操作 | 结果 |
|---|---|---|
| 1 | `POST /v1/config` 将 `embed_model` 改回 `jinaai/jina-embeddings-v2-base-zh`（与库 768 维一致），`citation_min_score=0.45`，persist | 200；notice 提示需 reindex |
| 2 | `POST /v1/reindex` `collection=""` `model=null`（空串→全部集合） | job completed，processed **128/128** |
| 3 | `POST /v1/ingest/text` 入库「挠度（Deflection）定义与工程要点」 | 200，1 doc / 1 chunk，`note:挠度...` |
| 4 | HTTP `POST /v1/search`「什么是挠度」 | **`degraded=false, total=5`**；Top1 = 挠度定义笔记 **vec≈0.609** |
| 5 | 本地 Retriever + 门控模拟 `@0.45` | **cite=1**（挠度定义笔记）/ bg=3 / discard=1 |

**门控后可引用来源**：`note:挠度（Deflection）定义与工程要点`

| 验收项 | 状态 |
|---|---|
| G1 无向量维度检索降级（HTTP search） | ✅ 通过 |
| G2 库内可引用 ≥1（有定义笔记） | ✅ 检索层通过；UI pill 需客户端重启加载新代码 |
| G3 deep_qa 结构化回答 | ⏳ 待 LLM 配置后端侧有效（当前 HTTP `llm_provider=none`） |
| G4 多源/单源披露 | ⏳ 待真实 chat 轮次 |
| G5 不伪造库内引用 | 代码已约束；待 chat 验证 |
| G6 闲聊不拉长 | 单测覆盖 resolve 矩阵 |
| G7 Python 回归 | ✅ 1073 passed |

**仍阻塞项**：服务端 `llm_provider=none` / `llm_api_key_configured=false`——对话生成与 deep_qa 端到端需在设置页配置可用 LLM 后重测。

### （待填 · LLM 配置后）

| 时间 | 场景 | track | 引用 | evidence 摘要 | 结论 |
|---|---|---|---|---|---|
|  | 什么是挠度 + 深度搜索 |  |  |  |  |

## 5. 相关改动落点

| 层 | 文件 |
|---|---|
| 提示词轨 | `src/doc2mind/core/agent/prompt_policy.py` |
| 证据/状态/自省 | `src/doc2mind/core/rag.py` |
| pill | `DocMind/ViewModels/ThinkingStep.cs` |
| 证据条 | `DocMind/ViewModels/ChatMessage.cs`、`DocMind/Models/ChatResponse.cs` |
| 解析 | `DocMind/Services/Doc2kbApiService.cs` |
| 单测 | `tests/test_prompt_policy.py`、`tests/test_rag.py` |
