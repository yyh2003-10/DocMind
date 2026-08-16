using DocMind.Models;
using DocMind.Services;

namespace DocMind.Tests;

/// <summary>
/// Fake IDoc2kbApiService for ViewModel unit tests.
/// Provides controllable responses for each endpoint — no HTTP involved.
/// </summary>
public sealed class FakeDoc2kbApiService : IDoc2kbApiService
{
    // ── Ingest ──
    public Func<IngestRequest, CancellationToken, Task<IngestResponse>>? OnIngest { get; set; }
    public Func<IngestRequest, CancellationToken, Task<JobStatus>>? OnIngestJob { get; set; }

    // ── Search ──
    public Func<SearchRequest, CancellationToken, Task<SearchResponse>>? OnSearch { get; set; }

    // ── Chat ──
    public Func<ChatRequest, CancellationToken, Task<ChatResponse>>? OnChat { get; set; }
    public Func<ChatRequest, Action<string>, Action<ChatStreamResult>, CancellationToken, Task<ChatStreamResult>>? OnChatStream { get; set; }

    // ── Documents ──
    public Func<string?, int, int, string?, string, CancellationToken, Task<DocumentListResponse>>? OnListDocuments { get; set; }
    public Func<string, int, int, string?, CancellationToken, Task<DocumentDetail>>? OnGetDocument { get; set; }
    public Func<string, string?, CancellationToken, Task<DeleteResult>>? OnDeleteDocument { get; set; }

    // ── Stats / Quality / Collections ──
    public Func<string?, CancellationToken, Task<Stats>>? OnGetStats { get; set; }
    public Func<string?, CancellationToken, Task<QualityReport>>? OnGetQuality { get; set; }
    public Func<string, CancellationToken, Task<Stats>>? OnCreateCollection { get; set; }

    // ── Convert ──
    public Func<ConvertRequest, CancellationToken, Task<ConvertResult>>? OnConvert { get; set; }

    // ── Reindex / Job ──
    public Func<ReindexRequest, CancellationToken, Task<JobStatus>>? OnReindex { get; set; }
    public Func<string, CancellationToken, Task<JobStatus>>? OnGetJob { get; set; }

    // ── Health / Config ──
    public Func<CancellationToken, Task<HealthStatus>>? OnGetHealth { get; set; }
    public Func<CancellationToken, Task<BackendConfig>>? OnGetConfig { get; set; }
    public Func<BackendConfigUpdate, CancellationToken, Task<BackendConfig>>? OnUpdateConfig { get; set; }

    // ── LLM 连接测试 ──
    public Func<LlmTestRequest, CancellationToken, Task<LlmTestResult>>? OnLlmTest { get; set; }

    public void UpdateBaseAddress(string baseUrl) { /* no-op */ }

    public Task<HealthStatus> GetHealthAsync(CancellationToken ct = default)
        => OnGetHealth?.Invoke(ct) ?? Task.FromResult(new HealthStatus { Status = "ok" });

    public Task<BackendConfig> GetConfigAsync(CancellationToken ct = default)
        => OnGetConfig?.Invoke(ct) ?? throw new NotImplementedException();

    public Task<BackendConfig> UpdateConfigAsync(BackendConfigUpdate req, CancellationToken ct = default)
        => OnUpdateConfig?.Invoke(req, ct) ?? throw new NotImplementedException();

    public Task<LlmTestResult> LlmTestAsync(LlmTestRequest req, CancellationToken ct = default)
        => OnLlmTest?.Invoke(req, ct) ?? throw new NotImplementedException();

    public Task<IngestResponse> IngestAsync(IngestRequest req, CancellationToken ct = default)
        => OnIngest?.Invoke(req, ct) ?? throw new NotImplementedException();

    public Task<JobStatus> IngestJobAsync(IngestRequest req, CancellationToken ct = default)
        => OnIngestJob?.Invoke(req, ct) ?? throw new NotImplementedException();

    public Task<SearchResponse> SearchAsync(SearchRequest req, CancellationToken ct = default)
        => OnSearch?.Invoke(req, ct) ?? throw new NotImplementedException();

    public Task<ChatResponse> ChatAsync(ChatRequest req, CancellationToken ct = default)
        => OnChat?.Invoke(req, ct) ?? throw new NotImplementedException();

    public async Task<ChatStreamResult> ChatStreamAsync(ChatRequest req, Action<string> onToken, Action<ChatStreamResult> onDone, CancellationToken ct = default)
    {
        if (OnChatStream is not null)
        {
            return await OnChatStream(req, onToken, onDone, ct);
        }
        // 兼容旧测试写法：未配置 OnChatStream 时回退到 OnChat（非流式），
        // 把完整回答作为单 token 输出，终帧元数据从 ChatResponse 映射
        var resp = await (OnChat?.Invoke(req, ct) ?? throw new NotImplementedException());
        if (!string.IsNullOrEmpty(resp.Answer))
        {
            onToken?.Invoke(resp.Answer);
        }
        var result = new ChatStreamResult
        {
            ChatId = resp.ChatId,
            Model = resp.Model,
            Provider = resp.Provider,
            TotalChunks = resp.TotalChunks,
            ElapsedMs = resp.ElapsedMs,
            Sources = resp.Sources,
        };
        onDone?.Invoke(result);
        return result;
    }

    public Task<DocumentListResponse> ListDocumentsAsync(string? collection = null, int page = 1, int pageSize = 20, string? format = null, string sort = "created_at_desc", CancellationToken ct = default)
        => OnListDocuments?.Invoke(collection, page, pageSize, format, sort, ct) ?? throw new NotImplementedException();

    public Task<DocumentDetail> GetDocumentAsync(string id, int chunks = 5, int chunkContentLength = 200, string? collection = null, CancellationToken ct = default)
        => OnGetDocument?.Invoke(id, chunks, chunkContentLength, collection, ct) ?? throw new NotImplementedException();

    public Task<DeleteResult> DeleteDocumentAsync(string id, string? collection = null, CancellationToken ct = default)
        => OnDeleteDocument?.Invoke(id, collection, ct) ?? throw new NotImplementedException();

    public Task<Stats> GetStatsAsync(string? collection = null, CancellationToken ct = default)
        => OnGetStats?.Invoke(collection, ct) ?? throw new NotImplementedException();

    public Task<QualityReport> GetQualityAsync(string? collection = null, CancellationToken ct = default)
        => OnGetQuality?.Invoke(collection, ct) ?? throw new NotImplementedException();

    public Task<Stats> CreateCollectionAsync(string name, CancellationToken ct = default)
        => OnCreateCollection?.Invoke(name, ct) ?? throw new NotImplementedException();

    public Task<ConvertResult> ConvertAsync(ConvertRequest req, CancellationToken ct = default)
        => OnConvert?.Invoke(req, ct) ?? throw new NotImplementedException();

    public Task<JobStatus> ReindexAsync(ReindexRequest req, CancellationToken ct = default)
        => OnReindex?.Invoke(req, ct) ?? throw new NotImplementedException();

    public Task<JobStatus> GetJobAsync(string jobId, CancellationToken ct = default)
        => OnGetJob?.Invoke(jobId, ct) ?? throw new NotImplementedException();

    public async Task<JobStatus> PollJobUntilDoneAsync(string jobId, IProgress<JobStatus>? progress = null, TimeSpan? pollInterval = null, CancellationToken ct = default)
    {
        // 与真实 Doc2kbApiService 行为一致：循环轮询直到任务进入终态。
        // 测试中始终用 10ms 间隔，避免 VM 传递的 1s 间隔拖慢测试。
        while (true)
        {
            var job = await GetJobAsync(jobId, ct);
            progress?.Report(job);

            if (IsTerminal(job.Status))
            {
                return job;
            }

            await Task.Delay(10, ct);
        }
    }

    private static bool IsTerminal(string? status)
        => status is "completed" or "done" or "failed" or "succeeded" or "canceled" or "cancelled";
}