# ISSUE-04 长文截断 / 继续写 真机验收

| 字段 | 内容 |
|---|---|
| ID | T4 |
| 优先级 | **P0** |
| 角色 | QA / 前端 |
| 依赖 | T0 + T1（WPF 编译） |
| 状态 | ready |

## 背景

P0：双轨提示、交付轨 token 抬升、截断提示、继续写合并。需真机。

## 验收标准

- [ ] 调小 `LlmMaxTokens` 后长文出现截断提示 +「继续写」  
- [ ] 继续写：正文**追加**在同一气泡，不新开用户回合  
- [ ] 继续写后来源列表不被清空；状态为「已续写」  
- [ ] 重启后历史无「原答 + 合并全文」双份重复  
- [ ] delivery 轨（创作/科研/续写）长文明显长于 RAG 短答策略  
- [ ] 手动表 C-03/C-04/C-05 勾选  

## 实现触点

- `prompt_policy.py`、`rag.py`、`ChatViewModel` 继续写命令  
- `docs/verification/manual-test-checklist.html`  

## 产出

- 截断前后截图/日志、`prompt_track`、`truncated` 字段记录  

