# ISSUE-16 设置「生效配置」真机核对（FC-04）

| 字段 | 内容 |
|---|---|
| ID | T16 |
| 优先级 | **P1** |
| 角色 | QA |
| 依赖 | T0 + T1 |
| 状态 | done-worktree（待真机） |

## 验收标准

- [x] 生效明细 UI 与 `EffectiveConfigItems`（worktree）  
- [ ] 与 `GET /v1/config` 字段一致  
- [ ] API Key 仅「已配置/未配置」，无明文  
- [ ] 标注实时/重启语义正确  
- [ ] 手动表 ST-02  

## 产出

- 核对表（UI 值 vs API 值）  

