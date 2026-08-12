# 规格：DocMindY 架构缺陷修复

> 状态：`ready-for-agent`
> 生成自：架构审查报告（2026-08-12）

## Problem Statement

DocMindY 经过架构审查，发现前后端模型不一致、API 规范与实现漂移、代码质量问题和功能缺失四类系统性缺陷。这些问题导致：
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
- **前端** `WpfApp1/Models/IngestRequest.cs`：添加 `Force` 属性
- **前端** `WpfApp1/Models/HealthStatus.cs`：移除 `Timestamp` 字段
- **前端** `WpfApp1/Models/ConvertRequest.cs`：移除 `Collection` 字段
- **前端** `WpfApp1/Models/QualityReport.cs`：添加 6 个缺失字段
- **前端** `WpfApp1/AppSettings.cs`：`RequestTimeoutSec` 默认值改为 60
- **前端** `WpfApp1/ViewModels/ImportViewModel.cs`：绑定 `Force` 参数到 UI
- **前端** `WpfApp1/ViewModels/QualityViewModel.cs`：展示新增的质量指标
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
- **前端测试**：WPF 无自动化测试框架，通过手动验证：导入页勾选 force → 确认后端收到 force=true → 文件被重新摄入
- **回归验证**：修改后运行 `pytest` 确保现有测试不破坏；手动验证质量页、导入页、设置页功能正常
- **先例**：项目已有 `tests/test_pipeline.py`、`tests/test_http.py` 等测试文件，新测试遵循相同模式

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
