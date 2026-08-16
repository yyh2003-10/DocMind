using DocMind.Models;

namespace DocMind.Services;

public interface IDoc2kbApiService
{
    /// <summary>更新后端 BaseAddress（端口被占用顺延时由 BackendProcessService 触发）。</summary>
    void UpdateBaseAddress(string baseUrl);

    Task<HealthStatus> GetHealthAsync(CancellationToken ct = default);
    Task<BackendConfig> GetConfigAsync(CancellationToken ct = default);
    Task<BackendConfig> UpdateConfigAsync(BackendConfigUpdate req, CancellationToken ct = default);
    Task<IngestResponse> IngestAsync(IngestRequest req, CancellationToken ct = default);
    /// <summary>异步摄入：提交任务并返回 JobStatus，供轮询真实进度（POST /v1/ingest/job）。</summary>
    Task<JobStatus> IngestJobAsync(IngestRequest req, CancellationToken ct = default);
    Task<SearchResponse> SearchAsync(SearchRequest req, CancellationToken ct = default);
    Task<ChatResponse> ChatAsync(ChatRequest req, CancellationToken ct = default);
    Task<DocumentListResponse> ListDocumentsAsync(string? collection = null, int page = 1, int pageSize = 20, string? format = null, string sort = "created_at_desc", CancellationToken ct = default);
    Task<DocumentDetail> GetDocumentAsync(string id, int chunks = 5, int chunkContentLength = 200, string? collection = null, CancellationToken ct = default);
    Task<DeleteResult> DeleteDocumentAsync(string id, string? collection = null, CancellationToken ct = default);
    Task<Stats> GetStatsAsync(string? collection = null, CancellationToken ct = default);
    /// <summary>创建空知识库集合（POST /v1/collections）。</summary>
    Task<Stats> CreateCollectionAsync(string name, CancellationToken ct = default);
    Task<QualityReport> GetQualityAsync(string? collection = null, CancellationToken ct = default);
    Task<ConvertResult> ConvertAsync(ConvertRequest req, CancellationToken ct = default);
    Task<JobStatus> ReindexAsync(ReindexRequest req, CancellationToken ct = default);
    Task<JobStatus> GetJobAsync(string jobId, CancellationToken ct = default);
    Task<JobStatus> PollJobUntilDoneAsync(string jobId, IProgress<JobStatus>? progress = null, TimeSpan? pollInterval = null, CancellationToken ct = default);
}
