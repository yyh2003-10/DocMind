# T9–T15 实施说明

## T9 对话侧只读 MCP（ISSUE-09）

| 项 | 实现 |
|---|---|
| 配置 | `mcp_client_enabled`（默认 false）、`mcp_servers_json`、白名单 `search/fetch/query/lookup`，黑名单 write/delete/shell/exec |
| 总开关 | `agent_mode_enabled=false` 时 **不加载** MCP client |
| 轨迹 | `McpToolResult.source="mcp"`；工具名可带 server 来源 |
| 密钥 | `mask_cmd` / `_scrub`；命令与 key 不进日志明文 |
| 降级 | server 挂了 call 返回 ok=false，RAG/内置搜索继续 |

### 配置样例（config.toml / env）

```toml
agent_mode_enabled = true
mcp_client_enabled = true
# JSON 数组，stdio 示例（示例 search server，勿提交真实 key）
mcp_servers_json = '''[{"name":"demo-search","transport":"stdio","command":"python","args":["-m","demo_mcp_search"],"enabled":true,"tool_whitelist":["search"]}]'''
```

```text
DOC2MIND_MCP_CLIENT_ENABLED=1
DOC2MIND_AGENT_MODE_ENABLED=1
```

## T10 Longform 大纲/分块（ISSUE-10）

- 模块：`src/doc2mind/core/agent/longform.py`
- SSE：`longform_outline` / `longform_section_start|done|skipped` / `longform_done`
- 配置：`longform_enabled=false`（不改 RAG 短答）；`longform_max_sections=8`；`longform_max_chars=12000`
- 预算耗尽：节级 skip + `status=budget_exhausted` + 可继续写
- 流式入口：`rag_answer_stream` 在 delivery 轨 + 长 query + 开关打开时先跑大纲

## T11 Artifact 双轨统一（ISSUE-11）

| 路径 | 行为 |
|---|---|
| **主路径** | `POST /v1/creative/export` → `file_path`/`file_name`；Agent `artifact_ready` 帧含 `file_path` |
| **Fallback** | 前端继续解析 `:::artifact`（`ChatMessage.ArtifactRegex` + `InferArtifactType`），文档标明兼容 |
| **PPT 体检** | `POST /v1/creative/inspect` 与导出共用 content/主题，结果字段一致可对照 |

迁移说明：新 UI 优先消费后端 `file_path`；旧内容无后端导出时走 `:::artifact` 本地解析，不删除该路径。

## T12 思考区 meta 泄漏（ISSUE-12）

- `ThinkingMetaFilter` 扩展：`The user wants…`、`Provide [ACTIONS]`、`Output [ACTIONS]` 等
- 配置：`thinking_meta_filter_enabled=true`（验收 C2 可关 → 原文透出）
- 误杀保护：句中中文实质内容 ≥8 字则整句保留
- 回归：`tests/test_llm_output.py` + `test_dispatch_t9_t15.py`

## T13 BM25 短词（ISSUE-13）

| 场景 | 路径 |
|---|---|
| `bm25_jieba_enabled=true`（默认） | unicode61 FTS5，2 字中文词可命中 |
| jieba 关 + trigram | ≥3 字 FTS5；&lt;3 字 **LIKE 子串** 兜底 |
| 日志 | `BM25 短词路径: tokenizer=trigram short_tokens=...` |

## T15 手动验收

- 结果文件：`docs/verification/manual-checklist-results.json`
- 汇总：pass 16 / manual 3 / fail 0
- 待真机：A-02 artifact UI、B-03 Agent 轨迹展开、C-01 截断「继续写」按钮

## 测试命令

```powershell
$env:PYTHONPATH="E:\DocMindY-worktrees\agent-p0\src"
python -m pytest tests/test_dispatch_t9_t15.py tests/test_llm_output.py tests/test_bm25_ranking.py tests/test_creative_api.py -q
```
