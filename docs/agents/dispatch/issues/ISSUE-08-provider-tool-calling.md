# ISSUE-08 Provider 真 tool-calling 回路

| 字段 | 内容 |
|---|---|
| ID | T8 |
| 优先级 | **P1** |
| 角色 | 后端 Agent |
| 依赖 | 可与 T7 并行；建议有 T6 更佳 |
| 状态 | ready |

## 背景

当前 Agent 为**服务端编排**：planner 选工具 → 执行 → 一次性生成。计划目标是模型自主 tool-loop。

## 目标

在支持的 provider 上实现模型 tool-calling 多轮回路；弱模型自动降级为现有编排。

## 非目标

- 不实现任意 shell/危险工具。  
- 工具集保持白名单：检索/工作区/export 等。

## 验收标准

- [ ] `LLMClient` 支持 tools 请求与 tool_calls 解析（至少 OpenAI 兼容）  
- [ ] Loop：tool_call → execute → 回注 → 直至 final 或预算尽  
- [ ] `agent_mode_enabled=false` 时不可达  
- [ ] 弱模型/不支持 tool-calling → 编排降级，正文仍可用  
- [ ] AnswerGuard：合法 tool 事件不误杀；未声明 JSON 仍拦  
- [ ] 单测：mock provider tool 协议 + 权限拒绝 + 步数上限  
- [ ] 版权：自研协议/命名，不粘贴第三方实现  

## 实现触点

- `core/llm/*`、`core/agent/runtime/loop.py`、`chat_agent.py`、`http.py`  

## 产出

- 时序说明 + 测试 + 降级矩阵  

