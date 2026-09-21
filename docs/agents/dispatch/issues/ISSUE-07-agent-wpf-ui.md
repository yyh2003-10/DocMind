# ISSUE-07 Agent 模式 WPF UI + 工具轨迹

| 字段 | 内容 |
|---|---|
| ID | T7 |
| 优先级 | **P1** |
| 角色 | 前端 WPF |
| 依赖 | T0 + T1 |
| 状态 | ready |

## 背景

后端已有 `agentMode`、`agent_plan/tool_call/tool_result/artifact_ready` 与 `AgentModeEnabled` 预留；**UI 未暴露**，用户无法感知 Agent。

## 目标

1. 设置：Agent 开关 + 写入策略（ask/session_allow/always）说明。  
2. 对话：Agent 模式标识、轨迹时间线、停止。  
3. 默认 **关闭**；开启时请求带 `agentMode:true`。

## 非目标

- 本任务不要求 provider 原生多轮 tool-calling（ISSUE-08）。

## 验收标准

- [ ] 默认 UI 不进 Agent（请求无 agentMode 或 false）  
- [ ] 打开开关后：出现 agent_plan/tool_* 轨迹（后端 enabled=true 时）  
- [ ] 后端 `agent_mode_enabled=false` 时即使开着 UI，服务端回落 RAG（状态可提示）  
- [ ] 轨迹可展开：工具名、状态、摘要  
- [ ] 手动表 B-01/B-02/B-03  
- [ ] `dotnet test` 通过  

## 实现触点

- `AppSettings`、`SettingsView*`、`ChatView*`、`ChatViewModel`  
- `Doc2kbApiService` 解析 tool 帧（未知帧安全忽略）  

## 产出

- UI 说明 + 截图 + 测试结果  

