# ISSUE-09 对话消费 MCP / 外部插件（只读搜索）

| 字段 | 内容 |
|---|---|
| ID | T9 |
| 优先级 | **P2** |
| 角色 | 后端 Agent |
| 依赖 | T6 + T8 |
| 状态 | ready |

## 背景

DocMind 自带 MCP Server（被外部调用）；对话侧**未作为 MCP client** 调用外部搜索插件。用户明确问过「不是有 MCP 或插件吗」。

## 目标

对话/Agent 可挂载**只读**外部 MCP 工具（先 search/fetch 类），经 ToolRegistry + 权限执行。

## 非目标

- 不加载任意未审计 MCP。  
- 不开放写文件类外部工具（默认）。

## 验收标准

- [ ] 配置：MCP server 列表（stdio/http）+ 启用开关 + 工具白名单  
- [ ] Agent 模式可调用外部 search MCP，轨迹可见 tool 名来源（builtin/mcp）  
- [ ] `agent_mode_enabled=false` 时不加载/不调用  
- [ ] 密钥与命令不在日志明文  
- [ ] 失败降级：MCP 挂了仍能 RAG/内置搜索  
- [ ] 文档：如何配置一个示例 search MCP  

## 实现触点

- `core/agent/runtime/**`、`config.py`、设置页  

## 产出

- 架构说明 + 配置样例 + 测试记录  

