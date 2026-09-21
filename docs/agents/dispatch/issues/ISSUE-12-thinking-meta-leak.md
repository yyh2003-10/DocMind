# ISSUE-12 思考区 meta 泄漏清理

| 字段 | 内容 |
|---|---|
| ID | T12 |
| 优先级 | **P2** |
| 角色 | 后端提示/输出 |
| 依赖 | T0 |
| 状态 | ready |

## 背景

用户截图思考区出现英文规划/meta：`The user wants definition... Provide [ACTIONS]...`。正文可用，但体验差。

## 验收标准

- [ ] 思考流过滤英文 meta/工具协议句，保留用户可见推理摘要  
- [ ] 不误杀正常中文思考  
- [ ] 回归：`test_llm_output.py` 相关  
- [ ] 与验收文档 C2 对齐可关闭  

## 触点

- `llm/output.py` ThinkingMetaFilter、rag thinking 帧  

## 产出

- 过滤规则说明 + 前后对比  

