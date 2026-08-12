# AGENTS.md — DocMindY 项目约定

## DocMind 知识库 MCP 工具

本项目通过 AtomCode 的 MCP 接入 DocMind 本地知识库，工具以 `mcp__doc2mind__*` 形式可用（stdio 传输，与 WPF 客户端/HTTP 服务共用 `%LOCALAPPDATA%\doc2mind\doc2mind.db`）。

### 可用工具

| 工具 | 用途 | 关键参数 |
|---|---|---|
| `mcp__doc2mind__search` | 混合检索（BM25+向量），查询历史经验/文档 | `query`（必填）、`collection`、`top_k` |
| `mcp__doc2mind__ingest_text` | 把一段经验/结论/要点直接写入知识库（不依赖文件） | `text`（必填）、`title`、`collection` |
| `mcp__doc2mind__ingest` | 同步摄入文件或目录（小目录够用） | `path`（必填，绝对路径）、`collection`、`recursive` |
| `mcp__doc2mind__ingest_job` | 异步摄入目录，返回 `job_id` 轮询进度（中大型项目用） | 同 `ingest` |
| `mcp__doc2mind__get_job` | 查询异步任务进度 | `job_id` |
| `mcp__doc2mind__list_docs` | 列出已摄入文档 | `collection`、`limit` |
| `mcp__doc2mind__remove_doc` | 删除文档及其分块/向量 | `target`（文档 ID 或路径） |
| `mcp__doc2mind__quality_check` | 知识库质量报告 | `collection` |
| `mcp__doc2mind__convert_file` | 文档格式互转 | `input_path`、`output_format` |
| `mcp__doc2mind__reindex` | 重建向量索引 | `collection`、`model` |

### 用法约定

- **开工前先查**：接手任务前，先用 `search` 检索相关历史经验和资料，避免重复踩坑。
- **摄入路径必须用绝对路径**（如 `E:/MyProject/src`），且进程有权限访问。
- **按项目分集合**：跨项目内容用不同 `collection`（如 `docmindy`、`prj-x`），避免互相污染。
- 工具调用会触发权限确认，用户按 `A` 可在当前会话放行。

## 主动提入库建议（重要）

在任务过程中遇到**值得沉淀的新知识**时，agent 应主动向用户提出入库建议，而不是默默处理完就结束。典型情形：

- 解决了一个报错/疑难 bug，有修复经验（根因 + 解法）
- 做出了架构决策或关键设计取舍
- 学到了本项目或第三方库的非显而易见用法/坑
- 用户明确给出了结论、规范或偏好

做法：任务收尾时，用一句话向用户提出建议，例如：
「这个问题值得沉淀：我建议用 `ingest_text` 把「xxx 的根因是 yyy，解法是 zzz」写入知识库（collection=docmindy），要写吗？」

等用户确认后再调用 `mcp__doc2mind__ingest_text`（写入前先 `search` 查重，避免重复入库）。如用户多次无需确认可直接入库，可改用直接写入并简短告知。

## Agent skills

### Issue tracker

Issues 与规格存于 GitHub Issues，用 `gh` CLI 操作。详见 `docs/agents/issue-tracker.md`。

### Triage labels

五个标准 triage 角色标签：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。详见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文布局：根目录 `CONTEXT.md` + `docs/adr/`。详见 `docs/agents/domain.md`。
