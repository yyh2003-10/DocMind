# 功能契约目录（6 个主功能）

> 编写：2026-09-09，任务 6（`docs/specs/任务派发-功能契约闭环.md`）
> 规范：`docs/specs/功能契约规范.md`
> 原则：每份契约必须写满 4 个出口（成功/失败/取消/空态）。**当前实现做不到的标 ❌ 并引用 FC 编号**，不为好看而写成已实现。

## 文件

| 功能 | 文件 | 已实现 ✅ / 有缺口 ❌（以 worktree 实现为准，详见下表） |
|---|---|---|
| 导入 | [`导入.md`](导入.md) | 主路径✅；FC-01a/b、FC-08 在 worktree 已接 |
| 搜索 | [`搜索.md`](搜索.md) | FC-07 在 worktree 已接（空库/无命中分流 + 去导入） |
| 对话 | [`对话.md`](对话.md) | FC-02✅；FC-03 在 worktree 已接（全局离线横幅） |
| 设置保存 | [`设置保存.md`](设置保存.md) | FC-05✅；**FC-04 生效视图仍缺口** |
| 知识图谱 | [`知识图谱.md`](知识图谱.md) | 图谱链路✅；FC-06 在 worktree 已接 |
| 质量看板 | [`质量看板.md`](质量看板.md) | 报告/curate✅；LLM 前置在 worktree 已接 |

> 下表状态列以 `E:\DocMindY-worktrees\agent-p0` 实现为准；合回主仓前主表可能仍显示历史 ❌ 文案。

## 汇总：FC 缺口核对

| FC | 功能 | 破的出口 | 契约文件标注点 | 状态（2026-09-20 worktree） |
|---|---|---|---|---|
| FC-01a | 导入 | 取消（粒度） | `导入.md` 取消出口 | **worktree 已接**：解析/分块/嵌入/写库多检查点 |
| FC-01b | 导入 | 取消（残留） | `导入.md` 取消出口 | **worktree 已接**：`on_result` 明细 + `cancel_note` + 前端展示 |
| FC-03 | 对话/全站 | 失败/离线 | `对话.md` 失败出口 | **worktree 已接**：各页 `BackendUnreachable` → Main 全局横幅 |
| FC-04 | 设置 | 成功 | `设置保存.md` 成功出口/状态机 | **worktree 已接**：生效配置明细清单（EffectiveConfigItems） |
| FC-06 | 图谱 | 前置 | `知识图谱.md` 前置条件 | **worktree 已接**：`IsLlmConfigured` + `CanExtractGraph` + 设置跳转 |
| FC-07 | 搜索 | 空态 | `搜索.md` 空态出口 | **worktree 已接**：后端 message + 前端分流 +「去导入」 |
| FC-08 | 导入/全站 | 失败/离线 | `导入.md` 失败出口 | **同 FC-03，worktree 已接横幅联动** |

质量看板「AI 整理」LLM 前置（类比 FC-06）：**worktree 已接** `QualityViewModel.IsLlmConfigured` + 命令禁用 + 设置跳转。

> 实现位置：`E:\DocMindY-worktrees\agent-p0`（`feat/agent-p0-p1`），尚未合回主 worktree；合入后请把上表「worktree 已接」改为 ✅。

> 注：FC-05（设置 API Key 徽章）和 FC-02（对话前置拦截）经实测**已实现**，本文档按真实现状标记 ✅；两者对应的锁定测试保留在 `对话.md` / `设置保存.md` 可执行测试中，暂无契约级破口。
> 补充：质量看板「AI 整理」的 LLM 前置预检查缺口未列入 FC 台账，按前述原则标记 ⚠️ 并在 `质量看板.md` 前置条件中说明（类比 FC-06）。
> FC-01c（取消契约测试）与 FC-05 的锁定测试、FC-02 的锁定测试归属任务 3（`core-chain-and-smoke.md` 卡点列 + `smoke.ps1` 取消断言）。
