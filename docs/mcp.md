# DocMind MCP Server 接入指南

> 给 AI 编码工具（Cursor / Windsurf / Claude Desktop / Claude Code 等）当**外置记忆库**。
> 让 agent 把项目文件批量摄入向量库、沉淀经验笔记，之后随时检索。
> 全部数据存在本地，不上传。

---

## 一、它是干嘛的（一句话）

DocMind 的 MCP Server 把**知识库读写**暴露成 AI 工具。配置好后，AI 可以直接调用：

- `ingest` / `ingest_job`：把项目文件夹里的文档全部读入知识库
- `ingest_text`：把对话中得到的经验 / 结论 / 要点**直接写入**知识库（不依赖文件）
- `search`：之后随时检索"以前记过的东西"
- 文档管理、质量检查、格式转换、重建索引等

典型用法（配 agent 做中大型项目）：

```
① 项目开始时  → agent 调用 ingest_job 把 ./src、./docs 批量摄入知识库
② 开发过程中  → agent 每解决一个坑，调用 ingest_text 沉淀一条经验
③ 下次开工    → agent 调用 search "之前遇到过 xxx 吗？" 直接找到答案
```

## 二、启动方式

MCP Server 走 **stdio** 传输，需要命令行里有 `doc2mind` 可执行文件：

```bash
# 安装（core 自带 MCP 依赖）
pip install doc2mind

# 手动测试启动
doc2mind mcp
```

> 数据与 WPF 客户端 / HTTP 服务共用同一个本地库（默认 `%LOCALAPPDATA%\doc2mind\doc2mind.db`），
> 你在 MCP 里导入的内容，WPF 搜索页、HTTP API 都能搜到，反之亦然。

## 三、各工具配置

### Claude Desktop / Claude Code

编辑 `claude_desktop_config.json`（或项目 `.mcp.json`）：

```json
{
  "mcpServers": {
    "doc2mind": {
      "command": "doc2mind",
      "args": ["mcp"]
    }
  }
}
```

### Cursor

`Settings → MCP → Add new MCP server`，类型选 `command`：

```
command: doc2mind
args: mcp
```

### Windsurf

`Settings → MCP Servers` 添加：

```
doc2mind  →  command: doc2mind mcp
```

### 如果 doc2mind 不在 PATH

填绝对路径，例如 Windows：

```json
{
  "mcpServers": {
    "doc2mind": {
      "command": "C:\\Users\\you\\.venv\\Scripts\\doc2mind.exe",
      "args": ["mcp"]
    }
  }
}
```

## 四、工具清单（12 个）

| 工具 | 说明 | 关键参数 |
|---|---|---|
| `ingest` | 同步摄入文件或目录（小目录够用） | `path`（必填）、`collection`、`recursive`、`force` |
| `ingest_job` | **异步摄入目录**：立即返回 `job_id`，后台逐文件处理，用 `get_job` 轮询进度。**中大型项目用这个** | 同 `ingest` |
| `ingest_text` | **文本直入**：把一段经验/笔记/结论直接写入知识库，不依赖文件。`collection` 不传时 AI 自动打标签/摘要/归类 | `text`（必填）、`title`、`collection`（可选）、`force` |
| `get_job` | 查询异步任务（ingest_job / reindex）进度 | `job_id` |
| `search` | 混合检索（BM25 + 向量 RRF 融合），返回 Top-K 命中分块 | `query`（必填）、`collection`、`top_k` |
| `chat` | **RAG 对话**：检索知识库 + 调用 LLM 生成回答，带来源引用，支持多轮对话 | `query`（必填）、`collection`、`top_k`、`chat_id` |
| `list_docs` | 列出已摄入文档及元数据（分块数、大小、格式） | `collection`、`limit` |
| `remove_doc` | 删除单个文档及其全部分块与向量 | `target`（文档 ID 或路径） |
| `quality_check` | 知识库质量报告（集合分布、分块统计 + `warnings` 质量告警，如 0 分块 / >50MB 大文档） | `collection` |
| `convert_file` | 单个文档转 Markdown / JSON / TXT / HTML，返回内容 | `input_path`、`output_format` |
| `reindex` | 重建指定集合的向量索引（可换嵌入模型），返回 `job_id` | `collection`、`model` |
| `curate` | **AI 整理知识库**：enrich（打标签/摘要）、categorize（自动归类/建集合）、dedup（语义去重）、consolidate（归纳合并蒸馏笔记）。`dry_run=true` 只读预览零写入 | `collection`、`actions`、`dry_run`、`top_k` |

> `chat` 和 `curate` 需要先配置 LLM（`DOC2MIND_LLM_PROVIDER` + 相关密钥），详见下方「RAG 对话配置」。

## 五、给 agent 的提示词模板

把下面这段放进你的 agent 系统提示或项目说明，它就知道怎么用了：

```text
你有一个外置知识库工具 DocMind，通过 MCP 提供 12 个工具。

用法约定：
- 摄入项目代码/文档：优先用 ingest_job（异步、有进度），路径给绝对路径，
  例如 ingest_job(path="E:/MyProject/src", recursive=true, collection="myproject")。
- 沉淀经验：每当解决一个值得记住的问题（报错修复、架构决策、踩坑经验），
  用 ingest_text(text="...", title="简短标题") 写入——不传 collection，
  AI 会自动打标签、生成摘要并归类到合适的集合。
- 检索：开工前先 search("与当前任务相关的关键词")，看有没有历史经验可用。
- 整理：发现知识库杂乱（重复经验多、集合混乱）时，先 curate(dry_run=true)
  出只读预览；删除/合并类动作把预览结论告诉用户，确认后再 dry_run=false 执行。
```

## 六、数据与配置

| 项 | 位置 |
|---|---|
| 知识库文件 | `%LOCALAPPDATA%\doc2mind\doc2mind.db`（Windows） |
| 配置文件 | `%APPDATA%\doc2mind\config.toml` |
| 嵌入模型 | 默认 `BAAI/bge-small-zh-v1.5`，可用 `DOC2MIND_EMBED_MODEL` 环境变量覆盖 |

换嵌入模型后，旧向量与新模型维度不一致，需用 `reindex` 重建索引。

### RAG 对话配置

`chat` 工具需要配置 LLM 才能使用。支持 4 种提供商（`llm_provider`）：
`openai`（OpenAI 兼容：DeepSeek/Qwen/Kimi/OpenAI 等）、`anthropic`、`gemini`、`ollama`（本地离线）。

**方式一：OpenAI 兼容 API（DeepSeek / Qwen / OpenAI 通用）**

```bash
# 环境变量（临时）
set DOC2MIND_LLM_PROVIDER=openai
set DOC2MIND_LLM_API_KEY=sk-your-api-key
set DOC2MIND_LLM_MODEL=deepseek-chat
set DOC2MIND_LLM_BASE_URL=https://api.deepseek.com/v1
```

**方式二：Anthropic / Gemini**

```bash
set DOC2MIND_LLM_PROVIDER=anthropic
set DOC2MIND_LLM_API_KEY=sk-ant-your-key
set DOC2MIND_LLM_MODEL=claude-sonnet-4-5

set DOC2MIND_LLM_PROVIDER=gemini
set DOC2MIND_LLM_API_KEY=your-key
set DOC2MIND_LLM_MODEL=gemini-2.5-flash
```

**方式三：本地 Ollama（完全离线）**

```bash
# 1. 安装 Ollama 并拉取模型：ollama pull llama3.2
# 2. 设置环境变量
set DOC2MIND_LLM_PROVIDER=ollama
set DOC2MIND_LLM_MODEL=llama3.2
```

**持久化**：手动编辑 `config.toml`（`%APPDATA%\doc2mind\config.toml`）写入
`llm_provider` / `llm_base_url` / `llm_model` 等字段（注意：`llm_api_key` 出于
安全考虑只从环境变量或 WPF 设置页注入，`save_settings` 不会把它写回文件）。
WPF 用户推荐直接在「设置 → 大模型对话」配置并「测试连接」验证。

支持的 `llm_model` 示例：`deepseek-chat`、`gpt-4o-mini`、`qwen-plus`、
`claude-sonnet-4-5`、`gemini-2.5-flash`、`llama3.2` 等。

验证配置是否可用（不落盘、不发真实对话）：

```bash
curl -X POST http://127.0.0.1:8765/v1/llm/test -H "Content-Type: application/json" ^
  -d "{\"provider\":\"openai\",\"api_key\":\"sk-xxx\",\"model\":\"deepseek-chat\"}"
```

## 七、常见问题

**Q：MCP 连接成功但工具调用报"路径不存在"？**
A：ingest / ingest_job 的 `path` 必须是**绝对路径**，且 agent 进程有权限访问。

**Q：导入大项目会不会卡住？**
A：不会。用 `ingest_job`（异步）代替 `ingest`，它会立即返回 `job_id`，你轮询 `get_job` 看进度。

**Q：agent 想记一段经验但没有文件？**
A：用 `ingest_text(text="...", title="...")`，直接写进知识库，无需建文件。

**Q：WPF 客户端和 MCP 会不会互相冲突？**
A：共用同一个 sqlite 库（WAL 模式），可以同时运行。同一时间只有一个写入进程即可，检索随时可用。

**Q：MCP 里删除了文档，WPF 里能看到吗？**
A：能。两边读同一个库，任何一方的变更（导入/删除/重建索引）即时对另一方可见。
