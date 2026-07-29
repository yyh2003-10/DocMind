using DocMind.Models;

namespace DocMind.Services;

public interface IDoc2kbApiService
{
    Task<HealthStatus> GetHealthAsync(CancellationToken ct = default);
    Task<IngestResponse> IngestAsync(IngestRequest req, CancellationToken ct = default);
    Task<SearchResponse> SearchAsync(SearchRequest req, CancellationToken ct = default);
    Task<PagedResult<Document>> ListDocumentsAsync(string? collection = null, int page = 1, int pageSize = 20, string? format = null, string sort = "created_at_desc", CancellationToken ct = default);
    Task<DocumentDetail> GetDocumentAsync(string id, int chunks = 5, int chunkContentLength = 200, string? collection = null, CancellationToken ct = default);
    Task<DeleteResult> DeleteDocumentAsync(string id, string? collection = null, CancellationToken ct = default);
    Task<Stats> GetStatsAsync(string? collection = null, CancellationToken ct = default);
    Task<QualityReport> GetQualityAsync(string? collection = null, CancellationToken ct = default);
    Task<ConvertResult> ConvertAsync(ConvertRequest req, CancellationToken ct = default);
    Task<JobStatus> ReindexAsync(ReindexRequest req, CancellationToken ct = default);
    Task<JobStatus> GetJobAsync(string jobId, CancellationToken ct = default);
    Task<JobStatus> PollJobUntilDoneAsync(string jobId, IProgress<JobStatus>? progress = null, TimeSpan? pollInterval = null, CancellationToken ct = default);
}
