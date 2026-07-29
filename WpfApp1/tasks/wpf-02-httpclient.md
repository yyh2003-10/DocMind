# WPF 任务 #2：HttpClient 封装 + DI 注册

> **状态：可立即开始**
> **依赖：WPF 任务 #1 完成、`docs/api.md` 契约已就位**
> **Python 后端依赖：无（用 mock 或真实后端都行）**

---

## 目标

实现 `IDoc2kbApiService`，封装 DocMind 后端全部 HTTP 端点，供后续所有页面 ViewModel 调用。

API 契约见 [`../docs/api.md`](../docs/api.md)，本任务严格按契约实现，不要臆造端点。

---

## 实现清单

### 2.1 数据模型 (`Models/`)

为每个 API 响应创建 record（C# 9+ record，不可变，便于数据绑定）：

- `Document.cs` — 对应 `Document` 模型
- `Chunk.cs` — 对应 `Chunk`（`Score` 可空，仅在搜索结果中有值）
- `SearchHit.cs` — 对应 `SearchHit`，内嵌 `Chunk`
- `SearchResponse.cs` — `{ Query, Hits, Total, ElapsedMs }`
- `IngestResult.cs` — `{ Document, ChunksAdded, DuplicatesSkipped, Status }`
- `IngestResponse.cs` — `{ Ingested[], Skipped, Failed[], TotalDocuments }`
- `Stats.cs` + `CollectionStats.cs`
- `QualityReport.cs`
- `ConvertResult.cs`
- `JobStatus.cs` — 异步任务状态
- `HealthStatus.cs`
- `ApiError.cs` — `{ Code, Message, Detail? }`

所有时间字段用 `DateTimeOffset`，JSON 用 `System.Text.Json` 的 `JsonSerializerOptions` 配 `PropertyNameCaseInsensitive=true` 和 `JsonNamingPolicy.CamelCase`。

### 2.2 服务接口 (`Services/`)

- `IDoc2kbApiService.cs` — 所有 HTTP 调用的抽象接口
- `Doc2kbApiService.cs` — 实现，注入 `HttpClient` + `ILogger<Doc2kbApiService>`
- `ApiException.cs` — 统一异常，带 `ApiError Code/Message`
- `BackendConnectionException.cs` — 后端不可达时抛

### 2.3 `IDoc2kbApiService` 方法签名

按 `docs/api.md` 端点一对一映射，方法名用动词开头：

```csharp
public interface IDoc2kbApiService
{
    // GET /v1/health
    Task<HealthStatus> GetHealthAsync(CancellationToken ct = default);

    // POST /v1/ingest
    Task<IngestResponse> IngestAsync(IngestRequest req, CancellationToken ct = default);

    // POST /v1/search
    Task<SearchResponse> SearchAsync(SearchRequest req, CancellationToken ct = default);

    // GET /v1/documents
    Task<PagedResult<Document>> ListDocumentsAsync(
        string? collection = null, int page = 1, int pageSize = 20,
        string? format = null, string sort = "created_at_desc",
        CancellationToken ct = default);

    // GET /v1/documents/{id}
    Task<DocumentDetail> GetDocumentAsync(string id, int chunks = 5,
        int chunkContentLength = 200, string? collection = null,
        CancellationToken ct = default);

    // DELETE /v1/documents/{id}
    Task<DeleteResult> DeleteDocumentAsync(string id, string? collection = null,
        CancellationToken ct = default);

    // GET /v1/stats
    Task<Stats> GetStatsAsync(string? collection = null, CancellationToken ct = default);

    // GET /v1/quality
    Task<QualityReport> GetQualityAsync(string? collection = null,
        CancellationToken ct = default);

    // POST /v1/convert
    Task<ConvertResult> ConvertAsync(ConvertRequest req, CancellationToken ct = default);

    // POST /v1/reindex
    Task<JobStatus> ReindexAsync(ReindexRequest req, CancellationToken ct = default);

    // GET /v1/jobs/{id}
    Task<JobStatus> GetJobAsync(string jobId, CancellationToken ct = default);

    // 轮询直到任务完成（内部工具方法，搜索/导入页进度条用）
    Task<JobStatus> PollJobUntilDoneAsync(string jobId,
        IProgress<JobStatus>? progress = null,
        TimeSpan? pollInterval = null,
        CancellationToken ct = default);
}
```

请求体 record 也一并创建：`IngestRequest` / `SearchRequest` / `ConvertRequest` / `ReindexRequest`，字段严格对齐 `docs/api.md`。

### 2.4 错误处理约定

`Doc2kbApiService` 内部：

1. 后端不可达（`HttpRequestException` 含连接拒绝）→ 抛 `BackendConnectionException`，`MainViewModel` 在顶栏状态灯显示"离线"。
2. HTTP 4xx/5xx → 读响应体 `ApiError`，抛 `ApiException(apiError)`。
3. 反序列化失败 → 抛 `ApiException` 带 `code="PARSE_ERROR"`。
4. 超时（>30s）→ 抛 `ApiException` 带 `code="TIMEOUT"`。

**不要**在每个方法里写 try/catch；让异常向上冒泡到 ViewModel 层统一处理。

### 2.5 DI 注册 (`App.xaml.cs` 的 `ConfigureServices`)

```csharp
services.AddHttpClient<IDoc2kbApiService, Doc2kbApiService>((sp, client) =>
{
    var settings = sp.GetRequiredService<AppSettings>();
    client.BaseAddress = new Uri(settings.BackendUrl);   // http://127.0.0.1:8765
    client.Timeout = TimeSpan.FromSeconds(30);
});
```

同时注册：
- `AppSettings`（从 `appsettings.json` 读取，单例）
- `IDoc2kbApiService`（通过上面的 `AddHttpClient`）
- 所有页面 ViewModel（`SearchViewModel` 等，Transient）
- `MainViewModel`（Singleton）
- `BackendProcessService`（Singleton，负责拉起 `doc2mind serve` 子进程 —— 实现留空，WPF 任务 #8 完成）

### 2.6 配置文件

创建 `WpfApp1/appsettings.json`（设为"复制到输出目录"）：

```json
{
  "BackendUrl": "http://127.0.0.1:8765",
  "PollIntervalMs": 1000,
  "StartupTimeoutSec": 30
}
```

`AppSettings.cs` 用 `Microsoft.Extensions.Configuration` 读取：
```csharp
public class AppSettings
{
    public string BackendUrl { get; set; } = "http://127.0.0.1:8765";
    public int PollIntervalMs { get; set; } = 1000;
    public int StartupTimeoutSec { get; set; } = 30;
}
```

NuGet 包补充：
```xml
<PackageReference Include="Microsoft.Extensions.Configuration" Version="8.0.0" />
<PackageReference Include="Microsoft.Extensions.Configuration.Json" Version="8.0.0" />
<PackageReference Include="Microsoft.Extensions.Logging.Abstractions" Version="8.0.1" />
```

### 2.7 单元测试骨架（可选）

在 `WpfApp1.Tests/` 建一个 xUnit 项目（独立 csproj），写 1-2 个 mock 测试验证 `Doc2kbApiService` 反序列化正确。这一步**可选**，时间紧可跳过。

---

## 验收标准

- [ ] `dotnet build` 通过，0 error，warning ≤ CS1591
- [ ] `IDoc2kbApiService` 覆盖 `docs/api.md` 全部端点，签名一致
- [ ] `ApiException` / `BackendConnectionException` 已定义，错误码透传
- [ ] DI 容器注册齐全，`MainViewModel` 能注入 `IDoc2kbApiService`
- [ ] `appsettings.json` 可正确读取 `BackendUrl`
- [ ] 手动验证：在后端未启动时调用 `GetHealthAsync` 抛 `BackendConnectionException`（用一个临时按钮测一下，测完删掉）

---

## 完成后

文件顶部状态改 `已完成`，追加"完成记录"小节，通知 AtomCode 发布任务 #3。
