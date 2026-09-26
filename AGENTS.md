# AGENTS.md — DocMind 项目约定

## DocMind 知识库 MCP 工具

本项目通过 AtomCode 的 MCP 接入 DocMind 本地知识库，工具以 `mcp__doc2mind__*` 形式可用（stdio 传输，与 WPF 客户端/HTTP 服务共用 `%LOCALAPPDATA%\doc2mind\doc2mind.db`）。

### 可用工具

| 工具 | 用途 | 关键参数 |
|---|---|---|
| `mcp__doc2mind__search` | 混合检索（BM25+向量），查询历史经验/文档 | `query`（必填）、`collection`、`top_k`、`min_score`；`score` 是 RRF 融合分（仅代表排名），判断相关性看 `vector_score`/`bm25_score` |
| `mcp__doc2mind__chat` | RAG 对话：检索知识库 + LLM 生成回答，带来源引用，支持多轮 | `query`（必填）、`collection`/`collections`、`chat_id`、`model` |
| `mcp__doc2mind__ingest_text` | 把一段经验/结论/要点直接写入知识库（不依赖文件） | `text`（必填）、`title`、`collection`（可不传，AI 自动归类） |
| `mcp__doc2mind__ingest` | 同步摄入文件或目录（小目录够用） | `path`（必填，绝对路径）、`collection`、`recursive` |
| `mcp__doc2mind__ingest_job` | 异步摄入目录，返回 `job_id` 轮询进度（中大型项目用） | 同 `ingest` |
| `mcp__doc2mind__get_job` | 查询异步任务进度 | `job_id` |
| `mcp__doc2mind__list_docs` | 列出已摄入文档 | `collection`、`limit` |
| `mcp__doc2mind__remove_doc` | 软删除文档（chunks/向量物理删，documents 行保留 30 天可恢复） | `target`（文档 ID 或路径） |
| `mcp__doc2mind__quality_check` | 知识库质量报告 | `collection` |
| `mcp__doc2mind__convert_file` | 文档格式互转 | `input_path`、`output_format` |
| `mcp__doc2mind__reindex` | 重建向量索引 | `collection`、`model` |
| `mcp__doc2mind__curate` | AI 整理知识库：打标签/摘要/自动归类/语义去重/归纳合并 | `collection`、`actions`、`dry_run`（默认 true 只读预览）、`top_k`、`note`（触发来源） |
| `mcp__doc2mind__graph_get` | 知识图谱查询：实体与关系（需先经 `extract`/curate 抽取入库） | `collection`、`limit` |
| `mcp__doc2mind__create_artifact` | 创作导出：大纲/内容编译为 PPTX/DOCX/XLSX/HTML 物理文件 | `content`、`format`、`output_path` |
| `mcp__doc2mind__inspect_artifact` | PPT 大纲体检：0-100 评分 + 排版/密度/版式多样性诊断 | `content` |
| `mcp__doc2mind__restore_doc` | 恢复软删除的文档（仅元数据：deleted_at 置 NULL；不重建 chunks/向量，恢复后需重新摄入才能被检索） | `target`（文档 ID 或文件路径）、`collection` |
| `mcp__doc2mind__list_trash` | 列出回收站中的软删除文档（按 deleted_at 倒序） | `limit` |
| `mcp__doc2mind__purge_trash` | 物理清空回收站（破坏性，不可恢复；默认清 30 天前的，需用户明确同意） | `older_than_days` |
| `mcp__doc2mind__list_curate_runs` | 列出近 N 天的 curate 运行记录（让 Agent 看到自己/别人跑了什么整理） | `days`、`limit` |

> 共 **20 个** MCP 工具（与 `docs/mcp.md` 工具清单一致，含 `library_status`）。

### 用法约定

- **开工前先查**：接手任务前，先用 `search` 检索相关历史经验和资料，避免重复踩坑。
- **摄入路径必须用绝对路径**（如 `E:/MyProject/src`），且进程有权限访问。
- **入库可以不指定集合**：`ingest_text` 不传 `collection` 时，AI 会自动打标签、生成摘要并归类到合适的集合（需配置 LLM）；明确知道归属集合时仍可显式指定。
- **按项目分集合**：跨项目内容用不同 `collection`（如 `docmind`、`prj-x`），避免互相污染。
- 工具调用会触发权限确认，用户按 `A` 可在当前会话放行。

## 主动提入库建议（重要）

在任务过程中遇到**值得沉淀的新知识**时，agent 应主动向用户提出入库建议，而不是默默处理完就结束。典型情形：

- 解决了一个报错/疑难 bug，有修复经验（根因 + 解法）
- 做出了架构决策或关键设计取舍
- 学到了本项目或第三方库的非显而易见用法/坑
- 用户明确给出了结论、规范或偏好

做法：任务收尾时，用一句话向用户提出建议，例如：
「这个问题值得沉淀：我建议用 `ingest_text` 把「xxx 的根因是 yyy，解法是 zzz」写入知识库（collection=docmind），要写吗？」

等用户确认后再调用 `mcp__doc2mind__ingest_text`（写入前先 `search` 查重，避免重复入库）。如用户多次无需确认可直接入库，可改用直接写入并简短告知。

## AI 自动整理（curate）— 必读契约

知识库的自动整理是软件自身的能力，agent 应在合适时机主动调用，而不是建议用户"自己去点"。

### 已自动运行的部分（agent 无需动手）

- **入库自动 enrich + 可选 categorize + extract**：每次 `ingest_text` 入库后，`auto_curate_on_ingest` 默认开启时会自动跑这三个动作，agent 看不到也无须干预。
- **每次 curate() 跑完自动写留痕**：自动在 `curate_runs` 表记一行（started_at / actions / changed_doc_ids / dry_run / note），质量看板能查。`note` 字段建议传入"触发来源"（如 `"agent_settle"`、`"user_manual"`、`"ingest_auto"`），方便回溯。

### Agent 必须主动调用的时机（强制契约）

1. **会话收尾、≥3 次 `ingest_text` 后，或完成一个明确专题时**：必须跑一次 `curate(actions=["enrich","categorize"], dry_run=true)` 预览，把"将要改动的文档数 + 新建集合名"告诉用户；用户确认后再用 `dry_run=false` 落盘。
2. **检测到集合明显脏乱时**（例如同主题重复 ≥3 份、集合内有大量无 title/summary 的文档）：必须跑 `curate(actions=["dedup","consolidate"], dry_run=true)` 预览。
3. **协助用户恢复误删文档时**：先用 `list_trash` 查回收站，再用 `restore_doc(target=...)` 恢复元数据（恢复后需重新摄入或重跑 reindex 才能检索命中）。
4. **回答"刚才做了什么整理"类问题前**：先用 `list_curate_runs(days=7)` 查最近记录，**不靠记忆**。

### 风险分级（不可破坏）

- **可静默自动跑**：`enrich`（幂等，失败仅 skipped）、`categorize`（会建/移集合但能恢复）、`extract`（图谱实体增量追加）。
- **永不静默跑**：`dedup`（删除文档）、`consolidate`（删除原笔记 + 写蒸馏笔记）。这两类**必须** `dry_run=true` 预览 + 用户在干流对话里明确同意才能落盘。
- LLM 未配置时一切功能照旧，`curate` 会返回明确的配置提示，`curate_runs` 仍会写一行（status=skipped）。

### 软删除（trash）契约

- `remove_doc` 现在是**软删除**：documents 行保留 `deleted_at`，chunks/向量物理删，30 天内可 `restore_doc` 撤销。30 天后下次 curate 入口会自动 GC（`purge_trash`，保留由系统管理，agent 无须手动调）。
- agent 删除文档前应**先确认用户意图**，因为撤回要靠用户记得 ID 或文件路径。

### 完整工具清单

| 工具 | 用途 |
|---|---|
| `curate` | 整理（enrich/categorize/dedup/consolidate/extract 五选多） |
| `list_curate_runs` | 查最近 N 天的整理记录 |
| `list_trash` | 查回收站中的软删除文档 |
| `restore_doc` | 恢复软删除文档（仅元数据） |

## Agent skills

### Issue tracker

Issues 与规格存于 GitHub Issues，用 `gh` CLI 操作。详见 `docs/agents/issue-tracker.md`。

### Triage labels

五个标准 triage 角色标签：`needs-triage`、`needs-info`、`ready-for-agent`、`ready-for-human`、`wontfix`。详见 `docs/agents/triage-labels.md`。

### Domain docs

单上下文布局：根目录 `CONTEXT.md` + `docs/adr/`。详见 `docs/agents/domain.md`。
