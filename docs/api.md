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
| 鉴权 | 本地默认无鉴权；可选 `Authorization: Bearer <token>` |

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
  "status": "ok",
  "version": "0.1.0",
  "uptime_seconds": 3600,
  "gpu_available": true,
  "gpu_provider": "cuda",
  "embed_providers": ["cuda", "cpu"]
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
  "min_score": 0.0,                // 过滤低分结果，默认 0
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
  "elapsed_ms": 47
}
```

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

删除单个文档及其所有分块与向量。

**查询参数：**
- `collection` (string, 可选) — 校验集合归属，不匹配则 404

**响应 200：**
```jsonc
{ "id": "01J9XYZ...", "deleted_chunks": 47, "status": "deleted" }
```

**响应 404 `NOT_FOUND`：** 文档不存在。

---

### `GET /v1/stats`

知识库统计概览。

**查询参数：**
- `collection` (string, 可选) — 限定单个集合，不传则全部

**响应 200：** 见上文 `Stats` 模型。

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

查询异步任务状态（格式转换批量、重建索引等）。

**响应 200：**
```jsonc
{
  "job_id": "01J9JOB...",
  "type": "convert_batch",         // convert_batch|reindex|ingest_dir
  "status": "running",             // pending|running|completed|failed
  "progress": 0.65,                // 0-1
  "processed": 15,
  "total": 23,
  "started_at": "2026-07-28T...",
  "finished_at": null,
  "error": null
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
