# ISSUE-00 worktree → 主仓合并与回归基线

| 字段 | 内容 |
|---|---|
| ID | T0 |
| 优先级 | **P0** |
| 角色 | 集成/发布 |
| 依赖 | — |
| 状态 | ready |

## 背景

P0/P1 基础能力与商用门禁已在 `E:\DocMindY-worktrees\agent-p0`（`feat/agent-p0-p1`）实现；主仓 `E:\DocMindY`（`debug`）有大量未提交改动，不能盲目覆盖。

## 目标

1. 将 worktree 中**本能力相关**改动安全合入主开发流（分支策略由集成 Agent 拍板）。  
2. 主仓可独立跑通：后端 pytest + `scripts/run_full_verification.py` 核心分组。  
3. 更新契约台账：worktree「已接」→ 正式 ✅。

## 非目标

- 不在本任务实现新功能。  
- 不强制一次清空主仓所有历史未提交文件。

## 范围建议

- 后端：`src/doc2mind/core/agent/**`、`rag.py`、`pipeline.py`、`chat_store.py`、`config.py`、`llm/output.py`、`server/http.py`、`mcp.py` 等  
- 前端：Models/ViewModels/Views/Services 中 P0+回收站+设置生效相关文件  
- 文档：`docs/api.md`、`docs/mcp.md`、`docs/specs/功能契约/**`、`docs/agents/dispatch/**`、`docs/verification/**`  
- 测试：`tests/test_*` 本轮新增与修改项  

## 验收标准

- [ ] 主仓（或约定分支）包含上述改动，且 `PYTHONPATH=src` 下 pytest 相关组通过  
- [ ] `scripts/run_full_verification.py` 在合并后路径可跑，报告更新  
- [ ] 契约 README 中 FC 状态与代码一致  
- [ ] 主仓 `debug` 上原有未提交改动未被静默丢弃（合并策略有记录）  
- [ ] 无第三方源码粘贴  

## 完成后交付

- 合并说明（从哪到哪、冲突如何解）  
- 回归结果摘要（PASS/FAIL 数字）  
