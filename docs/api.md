# DocMind HTTP API 契约

> **版本：v1**　**Base URL：`http://127.0.0.1:8765`**　**Content-Type：`application/json; charset=utf-8`**
>
> 本文档是 **契约**，先于实现固定。Python FastAPI 后端（阶段 8）和 C# WPF HttpClient 封装（WPF 任务 #2）都以此为准。

---

## 通用约定

| 项 | 值 |
|---|---|
| 协议 | HTTP/1.1，本地回环，不对外暴露 |
| 编码 | UTF-8 |
| 时间格式 | ISO 8601 带时区，如 `2026-07-28T15:30:00+08:00` |
| ID 类型 | 字符串（ULID 或 UUID），全局唯一 |
| 分页 | `?page=1&page_size=20`，响应含 `total` |
| 错误响应 | 统一 `{"code": "...", "message": "...", "detail": {...}?}`，HTTP 状态码 4xx/5xx |
| 鉴权 | 默认启用 Bearer 令牌：`Authorization: Bearer <token>`；令牌在 `%LOCALAPPDATA%/doc2mind/server.token`（服务启动时自动生成；WPF 客户端自动读取注入）。仅 `/v1/health` 匿名可访问。开发/测试可用 `DOC2MIND_DISABLE_AUTH=1` 显式关闭（不推荐生产）。 |

### 错误码

| code | HTTP | 含义 |
|---|---|---|
| `BAD_REQUEST` | 400 | 参数缺失/格式错误 |
| `NOT_FOUND` | 404 | 资源不存在 |
| `CONFLICT` | 409 | 文件已摄入且未变更（非错误， informational） |
| `UNSUPPORTED_FORMAT` | 415 | 不支持的文档格式 |
| `INTERNAL` | 500 | 服务器内部错误 |
| `BACKEND_BUSY` | 503 | 嵌入/索引任务进行中，请稍后 |

---

## 数据模型

### `Document`（文档级元数据）

```jsonc
{
  "id": "01J9XYZ...",              // ULID
  "source": "report.pdf",          // 原始文件名或路径
  "collection": "papers",          // 集合名
  "format": "pdf",                 // pdf|docx|xlsx|pptx|md|html|image|code
  "file_hash": "a1b2c3...",        // MD5，用于增量去重
  "size_bytes": 1048576,
  "page_count": 12,                // pdf/docx/pptx 有，其余 null
  "chunk_count": 47,               // 该文档切出的分块数
  "created_at": "2026-07-28T...",
  "updated_at": "2026-07-28T..."
}
```

### `Chunk`（分块，检索结果单元）

```jsonc
{
  "id": "01J9CHUNK...",            // ULID
  "document_id": "01J9XYZ...",
  "content": "Transformer 采用多头自注意力...",  // 分块文本
  "metadata": {                    // 加载器/分块器写入的结构信息
    "type": "paragraph",           // heading|paragraph|table|code|list|image
    "heading": "第三节 注意力机制",
    "page": 5,                     // 来源页码，无则 null
    "tokens": 312,                 // 该分块的 token 数
    "source_format": "pdf"
  },
  "score": 0.873,                  // 检索得分（仅搜索结果中出现）
  "source": "report.pdf"           // 便于前端展示
}
```

### `SearchHit`（检索结果项）

```jsonc
{
  "rank": 1,
  "score": 0.873,
  "match_type": "hybrid",          // vector|bm25|hybrid
  "vector_score": 0.91,
  "bm25_score": 0.74,
  "source": "report.pdf",
  "format": "pdf",
  "page": 5,                       // 无则 null
  "heading": "第三节 注意力机制",   // 无则 null
  "content": "Transformer 采用多头自注意力..."
}
```

### `IngestResult`

```jsonc
{
  "source": "report.pdf",          // 文件名或 note:标题
  "collection": "papers",
  "format": "pdf",                 // pdf|docx|xlsx|pptx|md|html|image|code
  "size_bytes": 1048576,
  "chunk_count": 47,
  "elapsed_ms": 1234,
  "status": "ingested",            // ingested|skipped|failed
  "error": null,                   // status=failed 时有值
  "document_id": "a1b2c3..."       // ingested/skipped 时有值
}
```

### `QualityReport`

```jsonc
{
  "collection": "papers",          // null 表示跨所有集合
  "total_documents": 23,
  "total_chunks": 1089,
  "format_distribution": { "pdf": 15, "docx": 8 },
  "warnings": []                   // 质量警告信息
}
```

### `Stats`

```jsonc
{
  "total_documents": 23,
  "total_chunks": 1089,
  "collections": {
    "papers": [23, 1089, 45678901]   // [doc_count, chunk_count, total_bytes]
  }
}
```

---

## 端点

### `GET /v1/health`

健康检查。WPF 启动时轮询此端点判断后端是否就绪。

**响应 200：**
```json
{
  "status": "ok",                 // ok | degraded（数据库/sqlite-vec 不可用时 degraded）
  "version": "0.1.0",
  "uptime_seconds": 3600,
  "gpu_available": true,
  "gpu_provider": "cuda",
  "embed_providers": ["cuda", "cpu"],
  "store_ok": true,               // 真实健康探测：数据库连接 + sqlite-vec 扩展
  "store_error": null             // store_ok=false 时的原因描述
}
```

---

### `POST /v1/ingest`

摄入一个文件或目录（递归）。同步返回摄入摘要；大文件分块/嵌入可能耗时数秒到数分钟。

**请求体：**
```jsonc
{
  "path": "E:/docs/report.pdf",    // 绝对或相对路径
  "collection": "papers",          // 默认 "default"
  "recursive": false,              // path 为目录时是否递归，默认 false
  "force": false                   // 即使 file_hash 已存在也重新摄入，默认 false
}
```

**响应 200：**
```jsonc
{
  "ingested": [ { /* IngestResult */ } ],
  "skipped": 0,
  "failed": 0,
  "failed_details": [],           // status=failed 的 IngestResult 明细
  "total_documents": 1,
  "total_chunks": 47
}
```

**响应 409 `CONFLICT`：** 文件已存在且 `force=false`，返回现有 `Document`。

**响应 415 `UNSUPPORTED_FORMAT`：** 扩展名不在支持列表。

---

### `POST /v1/search`

混合检索（BM25 + 向量余弦，RRF 融合）。

**请求体：**
```jsonc
{
  "query": "transformer 注意力机制",
  "collection": "papers",          // 默认 "default"，"*" 表示跨所有集合
  "top_k": 10,                     // 默认 10
  "min_score": 0.0,                // 过滤低分结果，默认 0；注意过滤的是 RRF
                                   // 融合分（量纲约 0~0.033），超范围会被忽略并提示
  "filter": {                      // 可选元数据过滤
    "format": ["pdf", "docx"],
    "heading_level": [1, 2]
  },
  "highlight": true                // 是否返回高亮片段，默认 false
}
```

**响应 200：**
```jsonc
{
  "query": "transformer 注意力机制",
  "hits": [ { /* SearchHit */ } ],
  "total": 10,
  "elapsed_ms": 47,
  "degraded": false,              // true = 嵌入/向量检索不可用，本次为纯 BM25 降级
  "message": null                 // 供前端展示的提示：空库/集合无文档/降级原因/min_score 误用
}
```

---

### `POST /v1/chat`

RAG 对话问答：从知识库检索相关文档，调用 LLM 生成回答并标注引用来源。支持多轮对话（传 `chatId` 延续会话）。

需要先配置 LLM：`DOC2MIND_LLM_PROVIDER`（`openai` / `anthropic` / `gemini` / `ollama`）+ 对应密钥与模型名，详见 [docs/mcp.md](mcp.md) 的「RAG 对话配置」。

**请求体：**
```jsonc
{
  "query": "项目架构是什么？",
  "collection": "papers",          // 默认 "default"
  "topK": 5,                       // 引用 chunk 数，默认 5，上限 20
  "chatId": "chat-abc123",         // 多轮对话时传同一值，不传则新建会话
  "collections": ["docs-a", "docs-b"],  // 多选知识库（优先于 collection）
  "model": "qwen2.5:7b"            // 按请求覆盖模型名（对话页快速切换），省略用后端配置
}
```

**响应 200：**
```jsonc
// 注意：响应字段为 snake_case（与请求体的 camelCase 别名不同）
{
  "answer": "根据资料，DocMind 采用分层架构...",
  "chat_id": "chat-abc123",        // 首次自动生成，后续追问传同一值
  "model": "deepseek-chat",
  "provider": "openai",
  "total_chunks": 5,
  "elapsed_ms": 2340,
  "timing": {
    "retrieval_ms": 180,
    "web_ms": 0,
    "llm_first_token_ms": 2000,
    "generation_ms": 2000,
    "total_ms": 2340,
    "citation_gate": { "citation_min_score": 0.45, "cite_count": 3, "bg_count": 1, "discarded_count": 1 }
  },
  "sources": [                     // 引用来源列表
    {
      "index": 1,
      "source": "report.pdf",
      "format": "pdf",
      "page": 3,
      "heading": "架构概述",
      "score": 0.8723,
      "score_type": "rerank"   // score 的量纲：rerank(重排相关度)/vector(向量相似度)/
                               // bm25(关键词匹配)/rrf(RRF 融合分，仅代表排名、量纲
                               // ~0.016-0.033)/web_relevance(网页相关度)/
                               // attachment(附件全文)；空 = 旧数据
    }
  ],
  "evidence": {
    "local_count": 5,
    "citation_gate": { "cite_count": 3, "bg_count": 1, "discarded_count": 1 },
    "timing": { "retrieval_ms": 180, "generation_ms": 2000, "total_ms": 2340 }
  }
}
```

**响应 400 `RAG_ERROR`：** LLM 未配置或配置错误。

---

### `POST /v1/chat/stream`

RAG **流式**对话（SSE）：WPF 对话页实际使用的端点。请求体与 `POST /v1/chat` 完全一致（含 `topK`/`chatId`/`providerConfig`/`enableWebSearch`/`attachments`/`ragMode` 等字段），并新增 P0 字段：`responseMode`（`rag`|`delivery`，可省略自动推断）、`continueWriting`（bool，续写：不重复检索，基于会话历史补全并合并进上一条 assistant），以及 P1：`agentMode`（bool）或 `mode="agent"` — **仅当后端 `agent_mode_enabled=true` 时生效**；为 false 时服务端忽略并回落 RAG（商用默认关闭进阶能力）。启用后 SSE 追加 `agent_plan` / `tool_call` / `tool_result` / `artifact_ready` 帧，done 帧含 `mode:"agent"`、`tools_used`、`artifacts`、`workspace_root`。响应为 `text/event-stream`，携带 `Cache-Control: no-cache` / `X-Accel-Buffering: no` 头；空闲超 15s 发送心跳注释帧 `: heartbeat` 防代理掐断。

**ChatMode（2026-09）**：请求可带 `chatMode`：`rag` | `agent` | `auto`（优先级高于 `agentMode`）。`auto` 为规则路由（深度联网+定义/任务题→Agent，其余→RAG）。配置项：`chat_mode_default`（出厂 `rag`）、`chat_mode_auto_enabled`。路由后 SSE 先发 `status`「回答模式：…」；done 帧附加 `chat_mode` / `chat_mode_requested` / `chat_mode_reason` / `chat_mode_degraded`。门禁 `agent_mode_enabled=false` 时 agent/auto 升级可见降级为 RAG。

**SSE 帧类型（`data: ` 前缀 JSON 行，`data: [DONE]` 结束）：**

**阶段耗时与慢模型提示（兼容：旧客户端忽略未知字段）：**

- done 帧顶层 `stage_*`：`stage_retrieval_ms` / `stage_web_ms` / `stage_tool_ms` / `stage_first_token_ms` / `stage_generation_ms` / `stage_total_ms` + `stage_first_token_mode`（`stream`|`n/a`，非流式 TTFT=-1）
- `timing.citation_gate`：`dropped_by_score` / `dropped_by_topic` / `cross_check` / `bg_injected` / `bg_overflow`
- 首 token 等待 ≥ `llm_first_token_slow_ms`（默认 30s）后，status 周期出现「正在生成回答…（已等待 Ns）」
- 配置：`search_provider=builtin|tavily|bocha|serpapi`（无 key 回落 builtin，密钥不回显）；`background_hit_limit`（0=关背景）；`stage_elapsed_enabled`；`agent_native_tool_calling`

| 帧类型 | 形状 | 说明 |
|---|---|---|
| 状态 | `{"type":"status","message":"正在检索知识库..."}` | 检索/联网等阶段进度 |
| 推理链 | `{"type":"thinking","text":"..."}` | DeepSeek-R1/Qwen3 等模型的思考过程，独立于正文；可含 `prompt_track` |
| 正文 | `{"token":"..."}` | 回答正文增量 |
| Agent 轨迹 | `{"type":"tool_call"|"tool_result"|"agent_plan", ...}` | 仅 agent_mode 开启时；默认忽略安全 |
| 错误 | `{"error":"..."}` | 后端 RAG/LLM 出错，随后紧跟 `data: [DONE]` |
| 终帧 | `{"done":true, ...}` | 见下方字段说明 |

**终帧字段：**
```jsonc
{
  "done": true,
  "chat_id": "chat-abc123",
  "model": "deepseek-chat",
  "provider": "openai",
  "model_spec": {                  // 后端确认的模型规格
    "display_name": "DeepSeek Chat",
    "context_window": 65536,
    "max_output_tokens": 8192,
    "is_reasoning_model": false,
    "summary_text": "上下文 64K · 最大输出 8K"
  },
  "total_chunks": 5,               // 引用来源数
  "elapsed_ms": 2340,
  "timing": {                      // 分阶段耗时（ms，T3/T5 可观测）
    "retrieval_ms": 180,
    "web_ms": 0,
    "context_ms": 220,
    "llm_first_token_ms": 420,    // 首 token；非流式≈整段生成
    "generation_ms": 2000,
    "total_ms": 2340,
    "citation_gate": {             // 弱相关引用门控统计
      "citation_min_score": 0.45,
      "bg_floor": 0.27,
      "reranked_usable": false,
      "hit_count": 5,
      "cite_count": 3,
      "bg_count": 1,
      "discarded_count": 1,
      "topic_demoted_count": 0
    }
  },
  "partial": false,                // true = 部分回答（中断/停止/截断）
  "warning": "回答因网络连接中断…",  // partial=true 时附带的人类可读警示
  "prompt_track": "rag",           // P0：rag | delivery
  "truncated": false,              // P0：true = 输出 token 上限截断
  "continue_supported": true,      // P0：客户端可展示「继续写」
  "response_mode": "normal",       // normal | continue
  "evidence": {
    "local_count": 3,
    "citation_audit": { "ok": true, "evidence_support": [1,2] },
    "citation_gate": { /* 同 timing.citation_gate */ },
    "timing": { /* 同上 timing */ }
  },
  "sources": [ /* 与 /v1/chat 响应的 sources 字段一致（snake_case，18 字段） */ ]
}
```

> 被中断/停止的部分回答不会作为完整 assistant 消息写入会话历史，避免污染后续多轮上下文。截断时 `truncated=true` 且 `partial=true`，并提示「继续写」。

**引用门控与耗时默认值（推荐）：**

| 配置 | 默认 | 说明 |
|---|---|---|
| `citation_min_score` | `0.45` | 重排分引用线；低于此值不占 `[n]`。>0.60 在无重排库上易误杀 |
| `citation_bg_score_ratio` | `0.6` | 背景线 = 引用线 × ratio；更低丢弃不注入（提速且防无效长上下文） |
| `web_search_timeout` | `36.0` | 联网预算；超时带部分结果返回，不拖死生成 |
| `llm_first_token_slow_ms` | `30000` | 首 token 超过则 status 提示关联网/换模型 |
| `llm_timeout` | `0`（不限） | 建议按模型设置；超时文案含检查模型/关联网/换模型 |

**门控误杀风险：** 主题门基于查询与切片词汇重叠；同义改写可能无重叠，此时高分命中降为背景而非丢弃。空命中时状态流明确「无库内引用依据」，不伪造 `[n]`。

---

### `GET/POST /v1/config`（节选新增字段）

```jsonc
// GET 响应新增（密钥只回显 configured，不回显明文）
{
  "search_provider": "builtin",
  "search_provider_api_key_configured": false,
  "search_provider_endpoint": null,
  "citation_min_score": 0.45,
  "citation_bg_score_ratio": 0.6,
  "background_hit_limit": 5,
  "stage_elapsed_enabled": true,
  "llm_first_token_slow_ms": 30000,
  "agent_mode_enabled": false,
  "chat_mode_default": "rag",
  "chat_mode_auto_enabled": true,
  "agent_native_tool_calling": true,
  "agent_file_write_policy": "session_allow"
}

// POST 可写字段（None=不改；persist=false 仅运行时生效）
{
  "search_provider": "tavily",
  "search_provider_api_key": "tvly-***",
  "citation_min_score": 0.45,
  "background_hit_limit": 5,
  "stage_elapsed_enabled": true,
  "agent_mode_enabled": false,
  "chat_mode_default": "rag",
  "chat_mode_auto_enabled": true,
  "agent_native_tool_calling": true,
  "persist": true
}
```

> 商用红线：`agent_mode_enabled=false` 时，请求中的 `agentMode=true` 会被服务端忽略并回落 RAG。`search_provider_api_key` 永不回显明文。

---

### `POST /v1/llm/test`

LLM 连接测试：用传入参数构造**临时**客户端发一条极小消息（`max_tokens=16`），验证提供商/API Key/地址/模型名是否可用。不落盘、不修改运行时配置、不做 RAG 检索。设置页「测试连接」按钮的后端。

**请求体：**
```jsonc
{
  "provider": "openai",        // 必填之一：none/openai/anthropic/gemini/ollama；
                               // 省略时用后端当前运行时配置
  "api_key": "sk-xxx",         // 省略/空 = 沿用后端当前配置的 key（验证已保存配置）
  "base_url": "https://api.deepseek.com/v1",  // 省略 = 沿用当前配置或提供商官方地址
  "model": "deepseek-chat",    // 省略 = 沿用当前配置或提供商默认模型
  "timeout": 15.0,             // 测试超时秒数，默认 15，上限 120
  "stream": false              // true = 走流式接口（stream_chat）测试，
                               // 可暴露流式特有问题（SSE 解析、chunk 格式）
}
```

**响应 200：**
```jsonc
// 成功
{ "ok": true, "provider": "openai", "model": "deepseek-chat",
  "reply_preview": "你好", "elapsed_ms": 1234, "error": null }

// 失败（错误已分类：key 无效 / 地址错误 / 网络不通 / 运行库缺失 / 超时）
{ "ok": false, "provider": "openai", "model": "",
  "reply_preview": null, "elapsed_ms": 362,
  "error": "OpenAI API 调用失败: 401 invalid api key" }
```

---

### `POST /v1/llm/models`

列出提供商当前可用的模型 ID：Ollama 调 `/api/tags`（本地已装模型），云端调各家模型列表接口（OpenAI 兼容 `/models`、Anthropic `/v1/models`、Gemini `/v1beta/models` 过滤 `generateContent`）。与 `/v1/llm/test` 同模式：临时客户端、不落盘、不动运行时配置。设置页「获取模型列表」与对话页模型下拉的后端。

**请求体：**
```jsonc
{
  "provider": "ollama",        // 省略 = 用后端当前运行时配置
  "api_key": "sk-xxx",         // 省略/空 = 沿用后端当前配置
  "base_url": "http://localhost:11434",  // 省略 = 沿用当前配置或提供商官方地址
  "timeout": 10.0              // 拉取超时秒数，默认 10，上限 60
}
```

**响应 200：**
```jsonc
// 成功
{ "ok": true, "provider": "ollama", "models": ["llama3.2:latest", "qwen2.5:7b"], "error": null }

// 失败（404 时 error 附「手动输入」引导：部分 OpenAI 兼容服务未实现列表接口）
{ "ok": false, "provider": "openai", "models": [],
  "error": "OpenAI API 列出模型失败（模型名或 API 地址不存在）: ... (HTTP 404)（该服务可能未实现列出模型接口，请手动输入模型名）" }
```

---

### `GET /v1/chats`

历史会话列表（持久化在 SQLite `chat_sessions` 表，重启不丢失）。按更新时间倒序。

**查询参数：** `limit`（默认 50，上限 200）、`offset`（默认 0）、`q`（可选，按标题/消息内容模糊搜索；设置后 `total` 为匹配会话数）

**响应 200：**
```jsonc
{
  "chats": [
    {
      "chat_id": "chat-abc123",
      "title": "项目架构是什么？",      // 首条用户问题前 50 字
      "message_count": 6,
      "created_at": "2026-08-16T10:00:00+08:00",
      "updated_at": "2026-08-16T10:05:00+08:00"
    }
  ],
  "total": 1
}
```

### `GET /v1/chats/{chat_id}`

会话全部消息（时间正序），供前端回看完整历史。响应 404 `NOT_FOUND` 表示会话不存在。

**响应 200：**
```jsonc
{
  "chat_id": "chat-abc123",
  "title": "项目架构是什么？",
  "messages": [
    { "role": "user", "content": "项目架构是什么？", "created_at": "..." },
    { "role": "assistant", "content": "根据资料，DocMind 采用分层架构...", "created_at": "..." }
  ]
}
```

### `DELETE /v1/chats/{chat_id}`

删除会话及其全部消息（内存缓存 + SQLite 级联删除）。响应 `{"chat_id": "...", "deleted": true}`；不存在时 404。

> 会话上下文持久化：`POST /v1/chat` 的每一轮问答都会写入 SQLite，后端重启后传同一 `chatId` 续聊仍能恢复最近 20 条上下文。

---

### `GET /v1/documents`

列出文档。支持分页与按集合过滤。

**查询参数：**
- `collection` (string, 可选) — 不传则跨所有集合
- `page` (int, 默认 1)
- `page_size` (int, 默认 20, 上限 100)
- `format` (string, 可选) — 按格式过滤
- `sort` (string, 默认 `created_at_desc`) — `created_at_asc|created_at_desc|size_desc|chunk_count_desc`

**响应 200：**
```jsonc
{
  "documents": [ { /* Document */ } ],
  "total": 23,
  "page": 1,
  "page_size": 20
}
```

---

### `GET /v1/documents/{id}`

取单个文档详情，含分块摘要（前 N 个分块的 content 截断）。

**查询参数：**
- `chunks` (int, 默认 5) — 返回的分块数
- `chunk_content_length` (int, 默认 200) — 每个分块 content 截断长度

**响应 200：**
```jsonc
{
  "document": { /* Document */ },
  "chunks_preview": [ { /* Chunk, 无 score */ } ]
}
```

---

### `DELETE /v1/documents/{id}`

软删除单个文档：chunks/向量/FTS/稀疏索引物理删除，`documents` 行保留 30 天（`deleted_at` 置时间戳），期间可 `POST /v1/trash/{id}/restore` 恢复元数据。

**查询参数：**
- `collection` (string, 可选) — 校验集合归属，不匹配则 404

**响应 200：**
```jsonc
{ "id": "01J9XYZ...", "deleted_chunks": 47, "status": "deleted" }
```
重复删除（已处于软删除态）返回 `status: "already_deleted"`、`deleted_chunks: 0`（幂等成功）。

**响应 404 `NOT_FOUND`：** 文档不存在（从未摄入过）。

---

### `POST /v1/trash/{id}/restore`

恢复软删除的文档（仅元数据：`deleted_at` 置 NULL）。**不重建** chunks/向量——恢复后文档出现在列表/统计，但检索不到，需重新摄入或 `POST /v1/reindex` 才能恢复全文检索。

**路径参数：**
- `id` (string) — 文档 ID（ULID）

**响应 200：**
```jsonc
{
  "id": "01J9XYZ...",
  "status": "restored",          // 或 "not_found"
  "note": "恢复成功；chunks/向量已物理删，需要重新摄入或重跑 reindex 才能被检索命中"
}
```
`status: "not_found"` 表示文档不存在、未处于软删除态、或同 source 已被重新摄入的活跃文档占用（无法恢复）。

---

### `GET /v1/trash`

列出回收站中的软删除文档（按 `deleted_at` 倒序）。供"误删后悔期"查看可恢复项。

**查询参数：**
- `limit` (int, 可选, 默认 100, 上限 500)

**响应 200：**
```jsonc
{
  "items": [
    { "document_id": "01J9XYZ...", "source": "E:/notes/a.md",
      "collection": "default", "deleted_at": "2026-09-01T10:00:00+08:00", "purged_at": null }
  ],
  "total": 1
}
```

---

### `POST /v1/trash/purge`

物理清空超过 N 天的回收站（**破坏性操作，不可恢复**）。联动删除 `documents` 行 + 清理空集合。

**请求体：**
```jsonc
{ "older_than_days": 30 }   // 默认 30，范围 1~365
```

**响应 200：** `{ "purged": 3 }`（物理清除的 trash 行数）。

---

### `GET /v1/curate-runs`

列出近 N 天的 AI 整理（curate）运行记录，用于质量看板展示「过去 N 天跑过几次整理、动了哪些文档」。

**查询参数：**
- `days` (int, 可选, 默认 7, 上限 90)
- `limit` (int, 可选, 默认 50, 上限 500)

**响应 200：**
```jsonc
{
  "items": [
    { "id": 1, "started_at": "...", "finished_at": "...", "dry_run": true,
      "collection": "default", "actions": ["enrich", "categorize"],
      "changed_doc_ids": ["doc1", "doc2"], "skipped_count": 0,
      "error_count": 0, "elapsed_ms": 5000, "note": "agent_settle" }
  ],
  "total": 1
}
```

---

### `GET /v1/stats`

知识库统计概览。

**查询参数：**
- `collection` (string, 可选) — 限定单个集合，不传则全部

**响应 200：** 见上文 `Stats` 模型。

---

### `POST /v1/collections`

创建空知识库集合（占位登记，使集合出现在列表并可被检索/对话勾选）。幂等：集合已存在则跳过。

**请求体：**
```json
{ "name": "my_kb" }
```

**约束：** `name` 仅允许中英文、数字、空格、下划线、连字符（空格会被替换为下划线），为空或含其他字符返回 400。

**响应 200：** 见上文 `Stats` 模型（返回创建后的最新集合概览）。

---

### `GET /v1/quality`

质量报告，供"质量看板"页面渲染图表。

**查询参数：**
- `collection` (string, 可选)

**响应 200：** 见上文 `QualityReport` 模型。

---

### `POST /v1/convert`

格式互转。单个文件返回转换结果；目录返回批量任务 ID（异步）。

**请求体（单文件）：**
```jsonc
{
  "input_path": "E:/docs/report.pdf",
  "output_format": "md",           // md|json|txt|html
  "output_path": "E:/out/report.md" // 可选，省略则返回内容
}
```

**响应 200（单文件，未指定 output_path）：**
```jsonc
{
  "input": "report.pdf",
  "output_format": "md",
  "content": "# Report\n\n...",    // 转换后的文本内容
  "elements_count": 47
}
```

**响应 200（单文件，指定 output_path）：**
```jsonc
{
  "input": "report.pdf",
  "output_format": "md",
  "output_path": "E:/out/report.md",
  "bytes_written": 12345
}
```

**请求体（目录批量）：**
```jsonc
{
  "input_path": "E:/docs/",
  "output_format": "md",
  "output_dir": "E:/out/",
  "recursive": true
}
```

**响应 202（目录批量，异步）：**
```jsonc
{
  "job_id": "01J9JOB...",
  "status": "running",
  "total_files": 23
}
```

---

### `GET /v1/jobs/{id}`

查询异步任务状态（格式转换批量、重建索引、整理等）。

**响应 200：**
```jsonc
{
  "job_id": "01J9JOB...",
  "type": "convert_batch",         // convert_batch|reindex|ingest|curate
  "status": "running",             // pending|running|completed|failed
  "progress": 0.65,                // 0-1
  "processed": 15,
  "total": 23,
  "started_at": "2026-07-28T...",
  "finished_at": null,
  "error": null,
  "report": null                   // curate 任务完成后的整理报告（其它任务为 null）
}
```

---

### `POST /v1/reindex`

重建指定集合的向量索引（删除现有向量，用当前嵌入模型重新嵌入）。

**请求体：**
```jsonc
{
  "collection": "papers",
  "model": null                    // 可选，切换嵌入模型；null 表示用当前模型
}
```

**响应 202：** 返回 `job_id`，同 `/v1/jobs/{id}` 查询。

---

### `POST /v1/curate`

AI 整理知识库（LLM 调用耗时，走异步任务；报告在 `job.report` 里取）。

四个动作：
- `enrich`：给文档生成标题/摘要/标签，写回元数据（全自动，入库时默认已做）
- `categorize`：基于现有集合判断归属，必要时新建集合并移动文档
- `dedup`：向量近邻找语义重复对，LLM 判定后删除冗余篇（同集合内）
- `consolidate`：把小而散的经验笔记（`note:` 开头）聚类，归纳成「蒸馏笔记」替换原条目

**安全约定：** `dry_run=true`（默认）全程只读、零写入，返回完整预览；
`dedup`/`consolidate` 有损失，确认预览后再用 `dry_run=false` 执行。
需要先配置 LLM，否则返回 400。

**请求体：**
```jsonc
{
  "collection": null,              // 可选，null = 整理全部集合
  "actions": ["enrich", "categorize", "dedup", "consolidate"],  // 可选，默认全部
  "dry_run": true,                 // 默认 true = 只读预览
  "top_k": null                    // 可选，enrich/categorize 文档数上限（1-200）
}
```

**响应 202：** 返回 `job_id`，同 `/v1/jobs/{id}` 查询；完成后 `report` 字段为整理报告：

```jsonc
{
  "dry_run": true,
  "actions": ["enrich", "categorize", "dedup", "consolidate"],
  "collection": null,
  "enriched": [ { "doc_id": "...", "source": "note:xxx", "status": "planned|enriched|skipped", "title": "...", "tags": ["..."], "summary": "..." } ],
  "categorized": [ { "doc_id": "...", "from": "default", "to": "auto-col", "new_collection": true, "status": "planned|moved|unchanged|skipped" } ],
  "duplicates": [ { "score": 0.93, "keep": {...}, "remove": {...}, "status": "planned|merged|not_duplicate" } ],
  "consolidated": [ { "cluster_size": 4, "members": ["note:a", "..."], "title": "蒸馏笔记", "preview": "...", "status": "planned|consolidated" } ],
  "skipped": [],
  "errors": [],
  "elapsed_ms": 1234
}
```

---

## 事件流（可选，阶段 8+）

### `GET /v1/events`（SSE）

服务器推送任务进度事件，WPF 客户端订阅以实时更新进度条。

**事件类型：**
- `job.progress` — `{job_id, progress, processed, total}`
- `job.completed` — `{job_id, status, finished_at}`
- `ingest.started` / `ingest.completed`
- `backend.status` — `{status: "ok"|"busy"|"error", message}`

> WPF 任务 #1-7 不依赖 SSE，可先实现轮询；SSE 在 WPF 任务 #8（系统托盘 + 打包）阶段补上。

---

## WPF 客户端约定

- **HttpClient 生命周期：** 单例 `HttpClient`（通过 `Microsoft.Extensions.Http` 的 `AddHttpClient<IDoc2kbApiService>`），`Timeout = TimeSpan.FromSeconds(30)`，长任务用 `/v1/jobs/{id}` 轮询。
- **后端地址：** 默认 `http://127.0.0.1:8765`，存于 `appsettings.json`，设置页面可改。
- **启动握手：** WPF 启动时拉起 Python 子进程（`doc2mind serve`），轮询 `GET /v1/health` 最多 30 秒，超时则提示用户。
- **错误处理：** `ApiException` 统一封装 `{code, message, detail}`，UI 层只关心 `code`。

---

## 附录：已实现未收录端点（2026-08-29 盘点）

以下端点已在 `server/http.py` 实现并被 WPF 客户端/MCP 使用，但正文契约尚未逐个补全。调用约定与正文一致（Bearer 令牌、统一错误结构）。后续按域补文档时可从这里出发：

### 配置与体检
| 端点 | 方法 | 说明 |
|---|---|---|
| `/v1/config` | GET/POST | 后端配置读写（llm_* / rag_* / chunk_* 等字段）；POST 实时生效，API Key 字段写入不回显 |
| `/v1/doctor` | GET/POST | 全维体检报告（Python/存储/模型/LLM 连通性）与自愈 |
| `/v1/diagnostics/bundle` | POST | 导出脱敏诊断包（doctor/runtime，可选日志），不包含知识库原文 |
| `/v1/backup` | POST | 使用 SQLite 在线备份 API 创建知识库一致性备份 |
| `/v1/backup/restore` | POST | 校验并恢复备份，恢复前自动保留当前数据库副本 |
| `/v1/sample/ingest` | POST | 一键导入内置示例知识库（对话页「一键导入官方示例库」） |

### 摄入扩展
| 端点 | 方法 | 说明 |
|---|---|---|
| `/v1/ingest/text` | POST | 直接摄入一段文本（MCP `ingest_text` 同源） |
| `/v1/ingest/job` | POST | 异步摄入目录，返回 `job_id` 轮询 |
| `PUT /v1/chunks/{chunk_id}/annotation` | PUT | 分块人工标注（批注/纠错） |

### 知识图谱（`/v1/graph/*`）
| 端点 | 方法 | 说明 |
|---|---|---|
| `/v1/graph/visualize` | GET | 力导向图数据（nodes/edges，WPF 图谱页） |
| `/v1/graph/entities` | GET | 实体列表（支持 collection/limit） |
| `/v1/graph/entities/{entity_id}/details` | GET | 实体详情 + 关联关系 + 命中切片 |
| `/v1/graph/entity/{entity_id}` | GET | 同上（别名路由，兼容旧客户端） |
| `/v1/graph/relations/{entity_id}` | GET | 指定实体的关系列表 |
| `/v1/graph/stats` | GET | 图谱规模统计 |
| `/v1/graph/entities/distill` | POST | 实体知识卡片蒸馏（LLM 生成 Markdown 档案） |
| `/v1/graph/extract` | POST | 触发图谱抽取（实体/关系入库） |

### 创意交付物
| 端点 | 方法 | 说明 |
|---|---|---|
| `/v1/creative/export` | POST | 把对话/知识内容编译导出为 PPTX/DOCX/XLSX/HTML 物理文件 |
| `/v1/creative/inspect` | POST | 对 PPT 大纲做体检评分（0-100）与排版/密度诊断 |
| `/v1/creative/artifacts/{artifact_id}/versions` | GET/POST | 保存 Artifact 草稿并查看版本历史（含来源元数据） |
| `/v1/creative/artifacts/{artifact_id}/versions/{version_id}` | GET | 读取指定 Artifact 版本，用于重新生成或继续编辑 |

### 系统环境（`/v1/system/*`）
| 端点 | 方法 | 说明 |
|---|---|---|
| `/v1/system/gpu-diagnosis` | GET | GPU/运行时诊断（CPU/CUDA/DirectML 推荐路径） |
| `/v1/system/install-gpu` | POST | 引导安装 GPU 加速依赖 |
| `/v1/system/install-ocr` | POST | 安装 PaddleOCR 可选扩展（流式日志） |
| `/v1/system/local-ai-environment` | GET | 本地 LM Studio/Ollama/GPU 环境感知 |
| `/v1/system/dependencies` | GET | 依赖安装状态清单 |
| `/v1/system/download-model` | POST | 下载嵌入模型（离线包场景） |

### 任务扩展
| 端点 | 方法 | 说明 |
|---|---|---|
| `GET /v1/jobs/{id}/events` | GET(SSE) | 单任务进度事件流（比轮询实时） |
| `DELETE /v1/jobs/{id}` | DELETE | 取消/清理任务 |
