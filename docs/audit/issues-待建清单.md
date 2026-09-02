# 待建 GitHub Issue 清单

> 来源：`docs/audit/2026-08-29-功能实现审计报告.md`
> 用法：确认后用 `gh issue create` 批量创建。标签取自 `docs/agents/triage-labels.md`
> （`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`）

---

## Issue 1 — BM25 对 <3 字符查询词静默失效

- **标题**：`[P1] 混合检索：BM25 对少于 3 字符的查询词完全失效（trigram 分词限制）`
- **标签**：`ready-for-agent`、`bug`
- **正文**：
  ```
  ## 现象
  `bm25_index` 使用 FTS5 `trigram` 分词器，只能匹配 ≥3 字符的连续子串。
  `_build_fts5_match()`（core/store/sqlite_vec.py:95-129）对 <3 字符的 token 回退为
  前缀查询 `"词*"`，而 trigram 索引不支持前缀匹配，导致该 term 完全不召回。

  实测（12 文档库，文档明确含"气缸缸径 32mm"）：
  - `MATCH "伺服驱动器"` → 命中，1.47387 分 ✅
  - `MATCH "热电偶"`     → 命中，2.12316 分 ✅
  - `MATCH "气缸"`       → **0 行** ❌

  HTTP `/v1/search` 表现为 `bm25_score=0.0000`、`match_type=vector`，
  混合检索静默退化为纯向量检索，用户与 UI 均无感知。

  ## 影响
  中文 2 字词（气缸/电机/电源/温度/报警…）与 2 字符英文缩写（IP / 5A / K型）
  极其常见，直接削弱 README 主推的"向量 + BM25 双引擎混合检索"卖点。

  ## 关键位置
  - core/store/sqlite_vec.py:95-129（`_build_fts5_match`）
  - core/store/sqlite_vec.py:242-247（`_FTS_SQL`，tokenize = 'trigram'）
  - core/store/sqlite_vec.py:1056-1068（`bm25_search`）

  ## 建议方案
  1. 对 <3 字符的中文查询词改用 `content LIKE '%词%'` 补充召回；或
  2. 换用支持 bigram 的分词方案（jieba 预分词 + unicode61）；或
  3. 至少在 `SearchStats.degraded_reason` 中上报，避免静默失效。
  ```
- **验收标准**：
  - 搜索 2 字中文词（如"气缸"）时 `bm25_score > 0` 且 `match_type` 含 `hybrid`
  - 新增单测覆盖"2 字中文词 BM25 召回"与"2 字符英文缩写召回"
  - 现有 `tests/test_bm25_ranking.py` 全绿

---

## Issue 2 — AI 知识库整理（curate）后端已实现但客户端零入口

- **标题**：`[P1] 已实现的 /v1/curate（AI 知识库整理）在 WPF 客户端完全没有入口`
- **标签**：`ready-for-agent`、`enhancement`
- **正文**：
  ```
  ## 现象
  `POST /v1/curate` 后端完整可用（core/curator.py，支持 enrich/categorize/
  dedup/consolidate/extract 五大动作、LLM 真实调用、进度回调与取消、dry_run 预览），
  MCP 也暴露了 `curate` 工具，但：
  - `DocMind/Services/Doc2kbApiService.cs` 无对应方法
  - 全仓 *.cs 检索 `curate` 命中数 0
  - AGENTS.md 将 curate 列为 agents 的常规整理手段，属于对外主推能力

  同类未接通端点还有 6 个（影响较小）：
  - `/v1/graph/stats`（图谱规模统计，GraphStatsDTO 已定义）
  - `/v1/system/dependencies`（依赖状态）
  - `/v1/system/download-model`（模型下载 SSE 进度）
  - `/v1/jobs/{id}/events`（单任务 SSE 进度，客户端被迫轮询）
  - `/v1/graph/entities`、`/v1/graph/entity/{id}`（后者为冗余别名）

  ## 关键位置
  - 后端：server/http.py:2724（curate）、:2396（graph/stats）
  - 客户端：Services/IDoc2kbApiService.cs、Services/Doc2kbApiService.cs
  ```
- **验收标准**：
  - `IDoc2kbApiService` 增加 `CurateAsync`，并在设置页或质量看板提供 UI 入口
  - 至少接通 `graph/stats` 到图谱页
  - 新增客户端单测覆盖新方法（参照 `FakeDoc2kbApiService` 现有模式）

---

## Issue 3 — SSE 流式缺少全局时长上限，后端卡死时前端永久等待

- **标题**：`[P1] 流式对话缺少收敛上限：后端卡死时 SSE 无限流 + UI 永久转圈`
- **标签**：`ready-for-agent`、`bug`
- **正文**：
  ```
  ## 现象（两个缺陷叠加）
  1. 后端（server/http.py:1972-1978）：15s 空闲仅发心跳并 continue，
     **无连续心跳次数上限**，后端卡死则流无限持续。
  2. 客户端（Doc2kbApiService.cs:207）：`reader.ReadLineAsync()` 未传
     CancellationToken；`HttpCompletionOption.ResponseHeadersRead` 使
     `HttpClient.Timeout`（App.xaml.cs:136，默认 1800s）不覆盖 body 读取。
     后端每 15s 心跳续命 → UI `IsLoading` 永久为 true。

  用户点"停止生成"后，取消检查只在收到一行后生效（:209），
  实际最多延迟一个心跳周期（15s）。

  ## 建议
  - 后端：加最大时长或最大心跳次数上限，超限发 error 帧收敛
  - 客户端：`ReadLineAsync` 传入 ct，并加空闲超时兜底
  ```
- **验收标准**：
  - 模拟后端不响应时，SSE 在可配置上限内主动收敛并发出 error 帧
  - 客户端在超时后 `IsLoading=false` 并给出明确错误提示
  - 点"停止生成"后 1s 内 UI 状态收敛

---

## Issue 4 — SSE 队列零背压且 put 无超时，存在线程泄漏

- **标题**：`[P1] SSE Queue 无 maxsize 且 put 无 timeout，event loop 关闭时工作线程永久阻塞`
- **标签**：`ready-for-agent`、`bug`
- **正文**：
  ```
  server/http.py:1915 `asyncio.Queue()` 无 maxsize（零背压）；
  :1947-1949 `asyncio.run_coroutine_threadsafe(queue.put(chunk_json), loop).result()`
  无 timeout —— event loop 停止/关闭时工作线程永久阻塞，形成线程泄漏。
  :1988 `await fut` 位于 finally 内，不再被取消，同样可能挂死。

  建议：Queue 设 maxsize 提供背压，`.result(timeout=...)` 加兜底。
  ```
- **验收标准**：
  - 客户端断连后，工作线程在可预期时间内退出（可用线程数断言验证）
  - 快速连续发起/中断流式请求不导致线程数持续增长

---

## Issue 5 — 多轮历史 user/assistant 分两次事务落库

- **标题**：`[P1] 多轮历史 user/assistant 两次独立事务，异常留孤儿轮、并发下上下文错序`
- **标签**：`ready-for-agent`、`bug`
- **正文**：
  ```
  core/rag.py:260（user）与 :287（assistant）各自独立事务
  （chat_store.py:143 每次 `with self._conn()` 各开一个事务）。
  - assistant 写失败 → 留下孤儿 user 轮
  - 并发下同 chat_id 两请求可交错落库 A_user,B_user,A_asst,B_asst，
    而 `get_history` 按 id 升序取回（chat_store.py:176-181）→ 上下文错序

  建议：合并为单事务；或对同一 chat_id 的写入串行化。
  ```
- **验收标准**：
  - assistant 写入失败时 user 轮一并回滚（无孤儿）
  - 并发压测同 chat_id 后，`get_history` 取回顺序为 user/assistant 成对且时序正确

---

## Issue 6 — 知识图谱数据被双通道重复渲染

- **标题**：`[P2] 图谱数据双通道注入导致每次渲染两遍`
- **标签**：`ready-for-agent`、`bug`
- **正文**：
  ```
  GraphView.xaml.cs:314 走 `PostWebMessageAsJson`，:319 又执行 `window.renderGraph(...)`；
  而 GraphTemplate.html:585-589 的 message 监听器收到通道1 消息后同样调用
  `window.renderGraph(e.data)` → `initFluidWaterSphereGraph` 每次执行两遍，
  造成重复初始化、动画状态重置与性能浪费。

  建议：二选一保留（推荐保留 PostWebMessageAsJson，注释中标为"安全高效"），删除冗余通道。
  ```
- **验收标准**：
  - 单次图谱数据注入只触发一次 `initFluidWaterSphereGraph`
  - 图谱页渲染结果与交互（点击节点、聚焦、抽取）保持不变

---

## Issue 7 — 文档口径与实现严重滞后

- **标题**：`[P2] 文档与实际实现脱节：MCP 工具数三方不一、缺对话页/图谱页章节、测试基线过期`
- **标签**：`ready-for-human`、`documentation`
- **正文**：
  ```
  | 项 | 文档声称 | 实际 |
  |---|---|---|
  | MCP 工具数 | README 7 / AGENTS.md 11 / mcp.md 12 | **15** |
  | dotnet test | HANDOVER 与 feature-plan 写 47 | **200** |
  | pytest | 写 129 | **306** |
  | 使用说明页面 | 覆盖 7 类页面 | 客户端有 9 个页面，**缺对话页、知识图谱页** |
  | feature-plan P3 | 知识图谱/文件监控标为"未实现" | **均已实现** |
  | README CLI 速查 | 列 9 条 | 实际 14 顶层命令 + 4 个 model 子命令 |

  api.md 已有"已实现未收录端点"附录，质量良好，可作为其他文档的参照。
  ```
- **验收标准**：
  - README / AGENTS.md / mcp.md 的 MCP 工具数统一为 15 并补齐缺失工具说明
  - 使用说明补齐「对话页」「知识图谱页」章节
  - HANDOVER.md 与 feature-completion-plan.md 回填真实测试基线与 P1/P2/P3 完成度

---

## Issue 8 — watchdog 未安装导致文件监控功能既不可验证也不可用

- **标题**：`[P2] watchdog 未安装：test_file_watcher.py 整文件 skip，文件监控功能在本环境不可用`
- **标签**：`needs-info`、`dependencies`
- **正文**：
  ```
  `requirements-server.txt:6` 声明 `watchdog==6.0.0`，但当前环境未安装，
  导致 `tests/test_file_watcher.py:37` `pytest.importorskip("watchdog")` 整文件跳过。
  `core/file_watcher.py` 由 `server/http.py:2870` 在服务启动时调用，
  依赖缺失时该能力的降级行为需确认（是静默跳过还是抛错）。

  需确认：watchdog 是必需依赖还是可选依赖？若可选，缺失时应给出明确提示。
  ```
- **验收标准**：
  - 明确 watchdog 的依赖级别并在 requirements 中标注
  - 缺失时有明确日志/UI 提示，而非静默失效
  - CI 中安装 watchdog 或显式标注 skip 原因

---

## Issue 9 — 流式端点缺集成级测试覆盖

- **标题**：`[P2] test_integration.py:832 被 skip，SSE 流式端点无集成级覆盖`
- **标签**：`ready-for-agent`、`test`
- **正文**：
  ```
  `tests/test_integration.py:832` 因 "Starlette TestClient hangs on infinite async
  SSE generator stream" 被 skip，导致 `/v1/chat/stream` 缺少端到端集成覆盖，
  目前只有单元测试（test_rag.py / test_stream_resilience.py）。

  建议改造为有界流，或用真实端口 + httpx streaming client 测试。
  ```
- **验收标准**：
  - `/v1/chat/stream` 有至少 1 个集成级用例（真实 HTTP 分帧读取）
  - 覆盖 token 帧增量到达、done 帧、心跳帧、异常 error 帧四种情形

---

## Issue 10 — 配置文件敏感字段变更缺迁移提示

- **标题**：`[P2] 1.1 起 config.toml 的 llm_api_key 被静默忽略，历史用户升级后密钥失效无提示`
- **标签**：`ready-for-human`、`ux`
- **正文**：
  ```
  `core/config.py:255-260` 明确：1.1 起不再从 config.toml 读取任何敏感字段
  （消除 API Key 明文落盘的泄漏面），密钥仅由 `DOC2MIND_LLM_API_KEY`
  或 `POST /v1/config` 运行时注入。

  该设计正确，但 `load_config_file()`（:299-301）直接过滤掉敏感字段，
  **无任何告警**。历史用户手写 config.toml 的 API Key 会在升级后静默失效，
  表现为"之前能用，升级后突然报未配置 LLM"，排查成本高。

  建议：检测到 config.toml 中存在被忽略的敏感字段时记 warning，
  并在 `/v1/config` 响应中返回迁移提示。
  ```
- **验收标准**：
  - `load_config_file()` 命中敏感字段时输出明确 warning
  - `/v1/config` 返回可机读的迁移提示字段
  - 使用说明/README 补充该变更说明

---

## Issue 11 — fastembed 未安装导致重排能力静默降级

- **标题**：`[P1] fastembed 未安装：重排与向量能力静默降级，检索 degraded=true 且无环境级告警`
- **已创建**：#17
- **标签**：`ready-for-agent`、`bug`、`area:infra`
- **正文**：
  ```
  ## 现象
  `fastembed==0.8.0` 是 core 依赖（requirements-core.txt:18），但当前开发环境未安装。

  实测（2026-08-31，`scripts/smoke.ps1`）：
  - `POST /v1/search` 返回 `degraded=true`，message 为
    「重排模型不可用（fastembed 未安装，无法使用重排模型。请运行：pip install fastembed），
    本次为原始 RRF 排序」
  - 检索退化为「向量 + BM25 的 RRF 融合」，**跳过重排环节**；向量路本身仍可用
  - 与 AUD-021（watchdog 未安装）同类：声明了依赖但环境缺失，
    功能既不可验证也不可用，且只有在真正调用时才暴露

  ## 关键位置
  - requirements-core.txt:18（依赖声明）
  - src/doc2mind/core/reranker/（重排实现）
  - server/http.py:773-781（SearchResponse.degraded / message）

  ## 建议方案
  1. 当前环境执行 `pip install fastembed==0.8.0` 恢复完整能力
  2. 补环境自检：`doc2mind doctor` 与 `GET /v1/system/dependencies` 应把
     fastembed 缺失标为 error 级，而不是等到检索时才在 message 里说
  3. README「极速上手」补一句依赖自检命令

  ## 附带发现（D-13，语义漂移）
  `degraded` 的实际语义比 api.md:212 记载的更宽：重排不可用也会置位，
  而文档称其含义为「嵌入/向量检索不可用，本次为纯 BM25 降级」。
  按文档理解会把「重排不可用」误判为「向量挂了」，排查方向被带偏。
  ```
- **验收标准**：
  - 安装 fastembed 后 `POST /v1/search` 的 `degraded=false`
  - `doc2mind doctor` / `/v1/system/dependencies` 在 fastembed 缺失时报 error 级并给出安装命令
  - api.md 修正 `degraded` 的语义描述（D-13）

---

## Issue 12 — /v1/quality 缺 spec 承诺的 6 个字段，且 MCP 与 HTTP 输出不一致

- **标题**：`[P2] 质量报告契约不成立：后端未返回 spec 承诺的 6 个字段，且 MCP 与 HTTP 行为不一致`
- **已创建**：#18
- **标签**：`status:needs-triage`、`documentation`、`area:api`
- **正文**：
  ```
  ## 现象（两个问题叠加）

  1）spec 前提不成立
  docs/specs/fix-architecture-deficiencies.md 的 User Story 4（P1）称：
  「后端已返回 avg_chunk_tokens / empty_chunks / oversized_chunks /
  duplicate_ratio / coverage_by_heading_level，仅前端补齐映射」。

  实测（2026-08-31）：对 src/ 全库检索这 6 个字段名，**命中数为 0**。
  QualityResponse（server/http.py:797-802）只有 5 个字段：
  collection / total_documents / total_chunks / format_distribution / warnings。
  → 该 story 无法按「仅前端补齐映射」实施，需先决策。

  2）MCP 与 HTTP 行为不一致
  MCP `quality_check`（server/mcp.py:357-358）在无告警且文档非空时会追加
  「未发现质量问题」；HTTP `/v1/quality`（http.py:2266-2280）返回空列表。
  同一份数据两个出口表现不同 —— 这正是 spec P2-10 要对齐的点。

  ## 关键位置
  - server/http.py:797-802（QualityResponse）、:2256-2288（/v1/quality 实现）
  - server/mcp.py:333-360（_tool_quality_check）
  - docs/specs/fix-architecture-deficiencies.md：User Story 4 与 P2-10

  ## 建议方案
  **先决策再实施**，两条路二选一：
  - A（推荐）：后端补齐这 6 个字段的真实计算，前端再补映射 —— 信息量最大
  - B：认定当前 5 字段已够用，从 spec 移除 Story 4，避免持续误导后续 agent

  无论选哪条，都同步修掉 MCP/HTTP 的 warnings 行为差异：建议统一为 HTTP 的空列表语义
  「无问题即空列表」比塞一条伪告警更利于前端判断。
  ```
- **验收标准**：
  - 决策结论写回 spec（A/B 二选一），不留「前提错误」的 story
  - 若选 A：`GET /v1/quality` 返回全部 6 字段，且数值与库内真实统计一致，前端映射同步
  - 无论 A/B：`mcp.py:quality_check` 与 `GET /v1/quality` 对同一份数据返回相同的 `warnings`

---

## Issue 13 — api.md 与实现契约漂移 4 处

- **标题**：`[P2] api.md 契约漂移：摄入幂等返回值、rerank_score 未记载、curate 动作数缺失、graph/extract 参数形式`
- **已创建**：#16
- **标签**：`ready-for-agent`、`documentation`、`type:docs`
- **正文**：
  ```
  ## 现象（2026-08-31 逐行核对 server/http.py 新发现，编号接续审计报告 D-01~D-07）

  D-09 摄入幂等返回值：api.md:179 记「文件已存在且 force=false → 409 CONFLICT」。
  实测 POST /v1/ingest（http.py:1622-1662）无论文件是否已存在都返回 200，
  用 IngestResponse.skipped 计数表达跳过，永远不会抛 409。

  D-10 检索命中缺字段：SearchHitDTO（http.py:758-770）含 `rerank_score`
  （重排分，已 sigmoid 归一化 0-1，None = 未启用重排），
  api.md 的 SearchHit 模型未记载，前端无法消费重排分。

  D-11 curate 动作数：VALID_ACTIONS（core/curator.py:37）为 **5 个**
  （enrich / categorize / dedup / consolidate / extract），
  api.md:602-606 只写 4 个，漏了 extract —— 而 extract 正是知识图谱抽取的入口。

  D-12 图谱抽取参数形式：POST /v1/graph/extract（http.py:2439-2443）用
  **Query 参数** collection / top_k，**不是 JSON body**；
  api.md 附录只写了端点名，极易误用。

  ## 建议方案
  在 api.md 逐一订正，并把 curl 示例补进「已实现未收录端点」附录。
  ```
- **验收标准**：
  - api.md 四处全部订正，且与 http.py 逐字段核对一致
  - 补 `/v1/graph/extract` 的 Query 参数示例
  - 回归：`scripts/smoke.ps1` 的项 2.03（幂等，已做宽容判定）、3.x（检索）、6.06（图谱抽取）保持 0 FAIL

---

## 创建命令示例

```powershell
# 单条创建（以 Issue 1 为例）
gh issue create `
  --title "[P1] 混合检索：BM25 对少于 3 字符的查询词完全失效（trigram 分词限制）" `
  --label "ready-for-agent" --label "bug" `
  --body-file ./.audit-smoke/issue-01.md
```

> 建议：Issue 1~5 优先；Issue 7、10 涉及对外承诺，建议同步安排。
