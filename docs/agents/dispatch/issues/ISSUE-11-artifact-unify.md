# ISSUE-11 创作 artifact 双轨统一

| 字段 | 内容 |
|---|---|
| ID | T11 |
| 优先级 | **P2** |
| 角色 | 全栈 |
| 依赖 | T0 |
| 状态 | ready |

## 背景

前端解析 `:::artifact` 魔法字符串导出；后端已有 `export_artifact` / Agent notes。双轨易不一致。

## 验收标准

- [ ] 主路径：后端导出返回 file_path，前端展示/打开  
- [ ] 旧 `:::artifact` 作 fallback，文档标明兼容  
- [ ] PPT 体检与导出结果一致  
- [ ] 手动表 A-01/A-02  
- [ ] 测试：creative API + 前端解析兼容  

## 产出

- 迁移说明 + 测试结果  

