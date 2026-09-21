# DocMind 任务派工表（多 Agent）

> 更新：2026-09-21（全量检查）  
> 工作副本：`E:\DocMindY-worktrees\agent-p0`（`feat/t3-citation-gate-timing`）  
> **全量检查**：pytest `tests`（排除 retrieval_eval）**1028 passed / 1 skipped**；分组验证 **PASS=9 FAIL=0**；WPF **335/335**  
> WPF 构建：用 VS 2022 MSBuild（见 `T1-nuget-env-fix.md`）  
> 手动验收记录：`docs/verification/manual-checklist-results.json`（16 pass / 3 manual）

**派工约定（请转给各 Agent）**

1. 优先在 **独立分支/worktree** 改代码，禁止直接覆盖主仓脏工作区。  
2. 每个任务以 issue 文件的 **验收标准** 为准；完成后跑相关 pytest。  
3. **禁止**粘贴第三方源码/提示词（版权约束）。  
4. 进阶能力默认 **关闭**；不得打开任意文件系统/危险工具。  
5. 完成后在本表「状态」列更新为 `done`，并附验证命令结果。

---

## 总览状态

| 状态 | 含义 |
|---|---|
| `done-worktree` | 已在 agent-p0 实现，待合并/真机验收 |
| `ready` | 可直接派工 |
| `blocked` | 依赖其它任务 |
| `wontfix` | 本轮不做 |

---

## 任务总表（派工用）

| ID | 标题 | 优先级 | 建议角色 | 依赖 | 预估 | 状态 | Issue |
|---|---|---|---|---|---|---|---|
| **T0** | worktree → 主仓合并与回归基线 | P0 | 集成/发布 | — | 0.5–1d | done-worktree | [ISSUE-00](issues/ISSUE-00-merge-worktree.md) |
| **T1** | NuGet/`dotnet test` 环境修复与 WPF 编译验收 | P0 | 构建/DevOps | T0 可并行 | 0.5d | done-worktree（构建+测试 335/335） | [ISSUE-01](issues/ISSUE-01-nuget-wpf-build.md) |
| **T2** | 多轮上下文 E2E 验收（含失败轮记忆） | P0 | QA/后端 | T0 | 0.5d | done-worktree（自动化用例） | [ISSUE-02](issues/ISSUE-02-multiturn-e2e.md) |
| **T3** | 弱相关引用门控调参与回归 | P0 | 检索/后端 | T0 | 1d | done-worktree | [ISSUE-03](issues/ISSUE-03-citation-gate-tune.md) |
| **T4** | 长文截断/继续写 真机验收 | P0 | QA/前端 | T0+T1 | 0.5d | done-worktree（契约自动化；真机 UI 待 T1） | [ISSUE-04](issues/ISSUE-04-longform-continue-e2e.md) |
| **T5** | 模型延迟诊断与推荐策略 | P1 | 后端/产品 | T0 | 1d | done-worktree | [ISSUE-05](issues/ISSUE-05-model-latency.md) |
| **T6** | 稳定联网：搜索 API 插件化 | P1 | 后端 | T3 | 2–3d | done-worktree | [ISSUE-06](issues/ISSUE-06-search-api-plugin.md) |
| **T7** | Agent 模式 WPF UI + 轨迹 | P1 | 前端 | T0+T1 | 2–3d | done-worktree（设置开关+SSE 轨迹解析） | [ISSUE-07](issues/ISSUE-07-agent-wpf-ui.md) |
| **T8** | Provider 真 tool-calling 回路 | P1 | 后端 Agent | T7 可并行 | 3–5d | done-worktree | [ISSUE-08](issues/ISSUE-08-provider-tool-calling.md) |
| **T9** | 对话消费 MCP/插件（只读搜索） | P2 | 后端 Agent | T6+T8 | 3d | done-worktree | [ISSUE-09](issues/ISSUE-09-mcp-client-readonly.md) |
| **T10** | Longform 大纲/分块编排（P2） | P2 | 后端 | T4 | 3–5d | done-worktree | [ISSUE-10](issues/ISSUE-10-longform-orchestrator.md) |
| **T11** | 创作 artifact 双轨统一 | P2 | 全栈 | T0 | 2d | done-worktree | [ISSUE-11](issues/ISSUE-11-artifact-unify.md) |
| **T12** | 思考区 meta 泄漏清理 | P2 | 后端 NLP/提示 | T0 | 1d | done-worktree | [ISSUE-12](issues/ISSUE-12-thinking-meta-leak.md) |
| **T13** | BM25 短词与检索边角 | P2 | 检索 | T3 | 1d | done-worktree | [ISSUE-13](issues/ISSUE-13-bm25-short-token.md) |
| **T14** | MCP 文档口径与工具数对齐 | P3 | 文档 | T0 | 0.5d | done-worktree | [ISSUE-14](issues/ISSUE-14-mcp-docs-count.md) |
| **T15** | 手动验收 HTML 执行与缺陷回写 | P0 | QA | T0+T1 | 1–2d | partial（自动化 16 pass，3 项待真机） | [ISSUE-15](issues/ISSUE-15-manual-checklist-exec.md) |
| **T16** | 设置「生效配置」真机核对 | P1 | QA | T0+T1 | 0.5d | done-worktree | [ISSUE-16](issues/ISSUE-16-settings-effective-e2e.md) |
| **T17** | 回收站/导入取消 真机验收 | P1 | QA | T0+T1 | 0.5d | done-worktree | [ISSUE-17](issues/ISSUE-17-trash-import-cancel-e2e.md) |

---

## 按 Agent 类型分派建议

| Agent 角色 | 建议领走 |
|---|---|
| **集成/发布** | T0, T1 |
| **QA** | T2, T4, T15, T16, T17 |
| **后端检索** | T3, T13 |
| **后端 Agent/工具** | T5, T6, T8, T9, T10 |
| **前端 WPF** | T7, T11（前端侧） |
| **提示/体验** | T12 |
| **文档** | T14（可复核） |

---

## 推荐执行顺序

```text
第 1 批（并行）
  T0 合并框架
  T1 NuGet/WPF 构建
  T2/T15 手动+多轮验收（可先用 worktree）

第 2 批（并行）
  T3 引用门控
  T4 继续写 E2E
  T5 模型延迟策略

第 3 批（进阶）
  T6 搜索插件 → T9 MCP
  T7 Agent UI ∥ T8 tool-calling
  T10 Longform ∥ T11 artifact ∥ T12 meta 泄漏
```

---

## 统一验收门（每个 PR/任务合并前）

| 门 | 命令/动作 |
|---|---|
| 后端单测 | `python -m pytest tests/ -q`（排除 retrieval_eval 环境债） |
| 全方面 | `scripts/run_full_verification.py` |
| 商用门禁 | `tests/test_commercial_gates.py` 等 |
| WPF | `dotnet test DocMind.Tests`（T1 修复后） |
| 契约 | 四出口 + FC 编号是否更新 |
| 版权 | 无第三方源码/提示词粘贴 |

---

## Issue 文件目录

```text
docs/agents/dispatch/
  README.md          ← 本文件（任务总表）
  issues/
    ISSUE-00-*.md … ISSUE-17-*.md
```

每个 issue 含：背景、范围、非目标、验收标准、建议实现点、测试、文件触点、备注。
