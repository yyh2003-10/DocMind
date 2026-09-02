# 规格：DocMind 架构缺陷修复

> 状态：`ready-for-agent`
> 生成自：架构审查报告（2026-08-12）

## Problem Statement

DocMind 经过架构审查，发现前后端模型不一致、API 规范与实现漂移、代码质量问题和功能缺失四类系统性缺陷。这些问题导致：
- 前端无法使用后端已支持的功能（如强制重新摄入）
- API 文档误导第三方集成者
- 存在潜在 bug（重复函数定义）
- 配置管理碎片化，调试困难
- 质量报告信息不完整

## Solution

按优先级分批修复所有已识别缺陷，确保前后端模型对齐、API 规范与实现一致、消除代码质量问题、补全缺失功能。

## User Stories

### P0 — 立即修复（Bug / 阻断性问题）

1. 作为开发者，我希望 `pipeline.py` 中不存在重复的 `ingest_text` 函数定义，以避免静默覆盖导致的行为差异
2. 作为用户，我希望在 WPF 前端的导入页面能选择"强制重新摄入"，以便覆盖已存在的文件而无需手动删除再导入
3. 作为第三方集成者，我希望 `docs/api.md` 中的 `IngestResult` 字段名与实际实现一致（`source` 而非 `document`，`chunk_count` 而非 `chunks_added`），以免集成时踩坑

### P1 — 高优先级（前后端对齐）

4. 作为用户，我希望质量报告页面能展示完整的后端数据（`avg_chunk_tokens`、`empty_chunks`、`oversized_chunks`、`duplicate_ratio`、`coverage_by_heading_level`），以便全面评估知识库质量
5. 作为开发者，我希望前端 `HealthStatus` 模型不包含后端未返回的 `Timestamp` 字段，以避免误导
6. 作为开发者，我希望前端 `ConvertRequest` 不包含后端不使用的 `Collection` 字段，以保持模型干净
7. 作为开发者，我希望 `StatsResponse.collections` 的类型链条（tuple → list → int[]）有注释说明约定，以防后续维护者误改长度

### P2 — 中优先级（配置与体验）

8. 作为用户，我希望 `RequestTimeoutSec` 的默认值从 1800 秒降低到 60 秒，以免误操作导致 UI 长时间无响应
9. 作为开发者，我希望前端独有配置（主题、窗口大小）与后端共享配置有清晰的边界说明，以便调试时快速定位配置来源
10. 作为用户，我希望 MCP 工具 `quality_check` 的输出与 HTTP `/v1/quality` 的输出格式对齐，以便 AI 工具和 WPF 前端看到一致的数据

### P3 — 低优先级（健壮性）

11. 作为用户，我希望删除集合中最后一个文档后，空集合记录被自动清理，以免污染统计和质量报告
12. 作为用户，我希望 `list` 命令和 MCP `list_docs` 工具支持分页，以便查看超过 500/10000 个文档时不会截断
13. 作为开发者，我希望 `docs/api.md` 中的 `StatsResponse.collections` 类型描述与实际实现一致（当前文档描述为嵌套对象，实际为 `dict[str, list[int]]`）
14. 作为用户，我希望搜索功能支持 `highlight` 参数，以便在结果中高亮匹配关键词
15. 作为用户，我希望搜索功能支持 `filter` 参数，以便按集合之外的维度过滤结果

## Implementation Decisions

### 模块修改范围

- **后端** `src/doc2mind/core/pipeline.py`：删除重复的 `ingest_text` 函数定义（保留逻辑正确的版本）
- **后端** `src/doc2mind/core/store/sqlite_vec.py`：`delete_document` / `delete_by_source` 后检查集合是否为空，空则级联删除
- **前端** `DocMind/Models/IngestRequest.cs`：添加 `Force` 属性
- **前端** `DocMind/Models/HealthStatus.cs`：移除 `Timestamp` 字段
- **前端** `DocMind/Models/ConvertRequest.cs`：移除 `Collection` 字段
- **前端** `DocMind/Models/QualityReport.cs`：添加 6 个缺失字段
- **前端** `DocMind/AppSettings.cs`：`RequestTimeoutSec` 默认值改为 60
- **前端** `DocMind/ViewModels/ImportViewModel.cs`：绑定 `Force` 参数到 UI
- **前端** `DocMind/ViewModels/QualityViewModel.cs`：展示新增的质量指标
- **文档** `docs/api.md`：对齐所有端点的实际实现字段

### API 合约变更

`IngestRequest` 新增可选字段 `force: bool = False`（已有后端支持，仅前端补齐）。

`QualityReport` 响应新增 6 个字段（后端已返回，仅前端补齐映射）。

### 配置管理

建议在 `AppSettings.cs` 中添加注释分隔线，区分"仅前端配置"和"推送到后端的共享配置"。不做大规模重构，仅增加文档化注释。

### 集合清理策略

在 `VectorStore` 的删除方法中，删除文档后查询该集合的剩余文档数。若为 0，执行 `DELETE FROM collections WHERE name = ?`。此逻辑在事务内执行，保证原子性。

## Testing Decisions

- **测试原则**：只测试外部行为（API 响应字段、CLI 输出），不测试实现细节
- **后端测试**：在现有 pytest 框架下，为 `ingest_text` 去重、集合级联删除添加用例
- **前端测试**：⚠️ 原文「WPF 无自动化测试框架」已过时 —— 现有 `DocMind.Tests` 共 **200 项** xUnit 测试（`FakeDoc2kbApiService` 为 Fake 模式），前端模型/DTO 变更应补单测，不再只靠手动验证
- **回归验证**：修改后运行 `pytest` 确保现有测试不破坏；手动验证质量页、导入页、设置页功能正常
- **先例**：项目已有 `tests/test_pipeline.py`、`tests/test_http.py` 等测试文件，新测试遵循相同模式

## Success Metrics

> 本节补于 2026-08-31。原 Testing Decisions 只有「回归门」，缺少可判定的验收标准，故补本节。
> 基线数字以实测为准（原文 129 / 47 已过期，见 AUD-020 / D-03）。
> 可执行验证：`scripts/smoke.ps1`；完整卡点矩阵：`docs/verification/core-chain-and-smoke.md`。

### 回归门（每一批改动都必须满足，缺一不可）

| 层 | 命令 | 通过判据 |
|---|---|---|
| 后端 | `python -m pytest tests/ -q` | **442 通过 / 0 失败 / 1 跳过**；跳过项须写明依赖原因（SSE 无限流 TestClient 挂起） |
| 前端 | `dotnet test DocMind.Tests -v q` | **250 通过 / 0 失败** |
| 接口 | `scripts/smoke.ps1` | **FAIL = 0**（WARN / SKIP / KNOWN 不阻塞） |

> 回归门只证明「没改坏」。每条 story 还须满足下表专属验收条件，才算「做对了」。

### 🔴 实施前必须先核实的阻塞前提

**P1-4（质量报告补齐 6 字段）前提不成立**：`avg_chunk_tokens`、`empty_chunks`、`oversized_chunks`、`duplicate_ratio`、`coverage_by_heading_level` 在 `src/` 全库检索 **0 命中**；`QualityResponse`（`server/http.py:797-802`）实际只有 `collection` / `total_documents` / `total_chunks` / `format_distribution` / `warnings` 五个字段。

因此该 story **不能按「仅前端补齐映射」实施**，须调整为先后端后端补计算，再对齐两端：

| 步骤 | 完成判据 |
|---|---|
| ① 后端补字段 | `GET /v1/quality?collection=X` 响应含这 5 个统计字段，数值由真实库内数据算出（非硬编码） |
| ② MCP 对齐 | MCP `quality_check` 与 HTTP `/v1/quality` 的**字段集合完全一致**（含「无告警」语义：MCP 会追加「未发现质量问题」，HTTP 不追加 —— 二者取一，`mcp.py:357-358`） |
| ③ 前端补映射 | `DocMind/Models/QualityReport.cs` 补齐字段，质量看板能展示 |

### 各 story 验收条件（逐条可判定）

**P0 — 立即修复**

| # | story | 验收条件 | 验证方式 |
|---|---|---|---|
| 1 | 删除 `pipeline.py` 重复 `ingest_text` | 全库 `def ingest_text` 定义数 == 1（`core/pipeline.py:155`）；`POST /v1/ingest/text` 仍返回 `chunk_count>0` | `grep -c "def ingest_text" src/doc2mind/core/pipeline.py` + 冒烟 2.04 |
| 2 | 导入页「强制重新摄入」 | `IngestRequest.cs` 含 `Force`；导入页勾选后抓包/日志确认请求体 `force=true`；对已存在文件触发**真实重摄入**（`ingested` 非空而非 `skipped`） | 手动 + 单测断言请求体 |
| 3 | `docs/api.md` 字段名对齐 | `IngestResult` 用 `source`（非 `document`）、`chunk_count`（非 `chunks_added`）；与 `IngestResultDTO`（`http.py:733`）逐字段比对无差异 | 人工 diff |

**P1 — 前后端对齐**

| # | story | 验收条件 | 验证方式 |
|---|---|---|---|
| 4 | 质量报告完整展示 | 见上文「阻塞前提」三步 | 接口 + 前端走查 |
| 5 | `HealthStatus` 移除 `Timestamp` | 前端 DTO 字段集合 ⊆ `HealthResponse`（`http.py:563`）；反序列化后无未映射残留 | 单测 + 冒烟 1.02 |
| 6 | `ConvertRequest` 移除 `Collection` | `POST /v1/convert` 请求体不再带 `collection`；转换功能不受影响 | 冒烟 6.01 |
| 7 | `StatsResponse.collections` 类型注释 | `DocMind/Models/StatsResponse.cs` 中该属性有注释说明 `dict[str, list[int]]`（`[doc_count, chunk_count, total_bytes]`） | 代码走查 |

**P2 — 配置与体验**

| # | story | 验收条件 | 验证方式 |
|---|---|---|---|
| 8 | `RequestTimeoutSec` 默认 60 | `AppSettings.cs` 默认值 == 60；未手动改过时读回 60 | 单测断言默认值 |
| 9 | 配置边界注释 | `AppSettings.cs` 有「仅前端配置」与「推送后端的共享配置」两段分隔注释 | 代码走查 |
| 10 | MCP `quality_check` 对齐 | 与 HTTP `/v1/quality` 字段集合一致（含 warnings 语义） | 见阻塞前提 ② |

**P3 — 健壮性**

| # | story | 验收条件 | 验证方式 |
|---|---|---|---|
| 11 | 空集合自动清理 | 删除集合内最后一个文档后，`GET /v1/stats` 的 `collections` 不含该集合 | 新增 pytest + `GET /v1/stats` 前后对比 |
| 12 | `list` / `list_docs` 分页 | 支持 `limit` / `offset`，文档数 > 上限时不静默截断（返回 `total` 供前端判断） | CLI + MCP 各验一次 |
| 13 | api.md `Stats.collections` 类型 | 文档描述改为 `dict[str, list[int]]`（当前写成嵌套对象，与 `StatsResponse`（`http.py:791`）不符） | 文档 diff |
| 14/15 | 搜索 `highlight` / `filter` | 后端已支持 `highlight` 与 `filter` 参数；`POST /v1/search` 传 `highlight=true` 时结果含高亮片段 | 新增 pytest（当前属新功能，见 Out of Scope） |

### 完成定义（DoD）

- [ ] 回归门三件套全绿
- [ ] 上表每条 story 的验收条件逐条核对通过
- [ ] 阻塞前提（质量 6 字段）已按三步实施或明确降级为「前端只展示后端现有 5 字段」
- [ ] `docs/api.md` 与实现逐字段比对无差异
- [ ] 按 `AGENTS.md` 约定，把本次踩到的契约漂移沉淀进知识库

## Success Metrics

> 2026-08-31 补。此前本节缺失，导致「测试全绿」被误当成验收标准 —— 它只能证明没改坏，不能证明做对了。
> 回归门数字已按实测更新（原 129 / 47 已过期，见 AUD-020；实际 **442 通过 / 1 跳过** 与 **250 通过**）。

### 回归门（每个优先级批次都必须过，缺一不可）

| 门 | 命令 | 通过判定 |
|---|---|---|
| Python 后端 | `python -m pytest tests/ -q` | 442 通过 / 0 失败 / 1 跳过（跳过项须有依赖缺失原因：SSE 无限流） |
| WPF 客户端 | `dotnet test DocMind.Tests -v q` | 250 通过 / 0 失败 |
| API 冒烟 | `scripts/smoke.ps1` | 0 FAIL（KNOWN 不计，见下） |

### 可判定验收条件（逐条对应 User Stories）

| Story | 验收条件（写成断言） | 验证方式 |
|---|---|---|
| 1 · 移除重复 `ingest_text` | `grep -c "^def ingest_text" src/doc2mind/core/pipeline.py` == **1**；`ingest_text` 行为用例全绿 | 命令 + pytest |
| 2 · 导入页「强制重新摄入」 | 勾选 force 后，后端 `POST /v1/ingest` 收到 `force=true` 且同一文件被重新摄入（`ingested[0].status="ingested"`）；不勾选时重复摄入被跳过（`skipped>=1` 或 409） | 手动 + `smoke.ps1` 项 2.02 / 2.03 |
| 3 · api.md 字段对齐 | api.md 的 `IngestResult` 字段名与 `IngestResultDTO`（`http.py:733`）逐字段一致；**并修掉 D-09~D-13 共 5 处新发现漂移** | 逐字段核对 |
| 4 · 质量报告 6 字段 | ⚠️ **前置门**：先确认后端是否返回这 6 个字段。**2026-08-31 实测后端不存在**（见 `docs/verification/core-chain-and-smoke.md` D-08）。若确认要补，验收 = `GET /v1/quality` 返回全部 6 字段且数值与库内真实统计一致；若决定不改后端，则本 story 应从 spec 移除或改写 | 先决策，再实施 |
| 5 · 移除 `Timestamp` | `DocMind/Models/HealthStatus.cs` 中 `Timestamp` 命中数 == **0** | grep |
| 6 · 移除 `Collection` | `DocMind/Models/ConvertRequest.cs` 中 `Collection` 命中数 == **0** | grep |
| 7 · 类型链条注释 | `StatsResponse.collections` 的 `dict[str, list[int]]` 语义（tuple→list→int[]）在前端模型处有注释 | 人工核对 |
| 8 · 超时默认 60 | `AppSettings.RequestTimeoutSec` 默认值 == **60** | 单测或 grep |
| 9 · 配置边界说明 | `AppSettings.cs` 中存在「仅前端配置 / 推送到后端的共享配置」分隔注释 | 人工核对 |
| 10 · MCP 与 HTTP 对齐 | `mcp.py:quality_check` 与 `GET /v1/quality` 对**同一份数据**返回相同的 `warnings`（当前 MCP 会额外追加「未发现质量问题」，见 D-14） | 对比两个出口的输出 |
| 11 · 空集合清理 | 删除集合内最后一个文档后，`GET /v1/stats` 的 `collections` 中不再出现该集合 | pytest |
| 12 / 13 · 分页与类型描述 | 见「已知限制」：仅记录，不在本 spec 内验收 | — |

### 状态语义（与 `scripts/smoke.ps1` 一致）

`PASS` 通过 ｜ `FAIL` 回归（阻塞）｜ `WARN` 需人工确认 ｜ `SKIP` 前置缺失 ｜ `KNOWN` 已登记缺陷复现（不阻塞退出码）

### Definition of Done

1. 上表每条 story 的验收条件均可被复现（命令或断言，不是主观描述）
2. 三条回归门全绿
3. `scripts/smoke.ps1` 无新增 FAIL
4. 文档与实现同步更新（api.md 的漂移一并修掉，避免二次返工）

## Out of Scope

- 搜索 `highlight` 和 `filter` 功能的后端实现（当前后端也未实现，属于新功能开发）
- 配置管理系统的重构（仅增加注释，不做架构变更）
- MCP 工具与 HTTP API 的完全对齐（仅对齐 `quality_check`）
- 分页功能的完整实现（仅记录为已知限制）
- 前端自动化测试框架的引入

## Further Notes

- 修复顺序建议：P0 → P1 → P2 → P3，每个优先级内按改动量从小到大
- P0 的三个修复都是低成本高收益，建议在同一个 commit 中完成
- `pipeline.py` 重复函数是最高优先级 bug，需先确认哪个版本是正确的（对比两个函数的逻辑差异）
- 本规格基于 2026-08-12 的代码状态，具体行号可能随开发变化
