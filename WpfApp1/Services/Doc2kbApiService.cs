using System.Net;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using DocMind.Models;
using Microsoft.Extensions.Logging;

namespace DocMind.Services;

public class Doc2kbApiService : IDoc2kbApiService
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase
    };

    private readonly HttpClient _httpClient;
    private readonly ILogger<Doc2kbApiService> _logger;

    public Doc2kbApiService(HttpClient httpClient, ILogger<Doc2kbApiService> logger)
    {
        _httpClient = httpClient;
        _logger = logger;
    }

    public Task<HealthStatus> GetHealthAsync(CancellationToken ct = default)
        => SendAsync<HealthStatus>(HttpMethod.Get, "v1/health", null, ct);

    public Task<IngestResponse> IngestAsync(IngestRequest req, CancellationToken ct = default)
        => SendAsync<IngestResponse>(HttpMethod.Post, "v1/ingest", req, ct);

    public Task<SearchResponse> SearchAsync(SearchRequest req, CancellationToken ct = default)
        => SendAsync<SearchResponse>(HttpMethod.Post, "v1/search", req, ct);

    public Task<PagedResult<Document>> ListDocumentsAsync(string? collection = null, int page = 1, int pageSize = 20, string? format = null, string sort = "created_at_desc", CancellationToken ct = default)
        => SendAsync<PagedResult<Document>>(HttpMethod.Get, BuildUri("v1/documents", new Dictionary<string, string?>
        {
            ["collection"] = collection,
            ["page"] = page.ToString(),
            ["pageSize"] = pageSize.ToString(),
            ["format"] = format,
            ["sort"] = sort
        }), null, ct);

    public Task<DocumentDetail> GetDocumentAsync(string id, int chunks = 5, int chunkContentLength = 200, string? collection = null, CancellationToken ct = default)
        => SendAsync<DocumentDetail>(HttpMethod.Get, BuildUri($"v1/documents/{Uri.EscapeDataString(id)}", new Dictionary<string, string?>
        {
            ["chunks"] = chunks.ToString(),
            ["chunkContentLength"] = chunkContentLength.ToString(),
            ["collection"] = collection
        }), null, ct);

    public Task<DeleteResult> DeleteDocumentAsync(string id, string? collection = null, CancellationToken ct = default)
        => SendAsync<DeleteResult>(HttpMethod.Delete, BuildUri($"v1/documents/{Uri.EscapeDataString(id)}", new Dictionary<string, string?>
        {
            ["collection"] = collection
        }), null, ct);

    public Task<Stats> GetStatsAsync(string? collection = null, CancellationToken ct = default)
        => SendAsync<Stats>(HttpMethod.Get, BuildUri("v1/stats", new Dictionary<string, string?>
        {
            ["collection"] = collection
        }), null, ct);

    public Task<QualityReport> GetQualityAsync(string? collection = null, CancellationToken ct = default)
        => SendAsync<QualityReport>(HttpMethod.Get, BuildUri("v1/quality", new Dictionary<string, string?>
        {
            ["collection"] = collection
        }), null, ct);

    public Task<ConvertResult> ConvertAsync(ConvertRequest req, CancellationToken ct = default)
        => SendAsync<ConvertResult>(HttpMethod.Post, "v1/convert", req, ct);

    public Task<JobStatus> ReindexAsync(ReindexRequest req, CancellationToken ct = default)
        => SendAsync<JobStatus>(HttpMethod.Post, "v1/reindex", req, ct);

    public Task<JobStatus> GetJobAsync(string jobId, CancellationToken ct = default)
        => SendAsync<JobStatus>(HttpMethod.Get, $"v1/jobs/{Uri.EscapeDataString(jobId)}", null, ct);

    public async Task<JobStatus> PollJobUntilDoneAsync(string jobId, IProgress<JobStatus>? progress = null, TimeSpan? pollInterval = null, CancellationToken ct = default)
    {
        var delay = pollInterval ?? TimeSpan.FromSeconds(1);

        while (true)
        {
            var job = await GetJobAsync(jobId, ct).ConfigureAwait(false);
            progress?.Report(job);

            if (IsTerminal(job.Status))
            {
                return job;
            }

            await Task.Delay(delay, ct).ConfigureAwait(false);
        }
    }

    private async Task<T> SendAsync<T>(HttpMethod method, string uri, object? payload, CancellationToken ct)
    {
        using var request = new HttpRequestMessage(method, uri);
        if (payload is not null)
        {
            request.Content = System.Net.Http.Json.JsonContent.Create(payload, options: JsonOptions);
        }

        HttpResponseMessage response;
        try
        {
            response = await _httpClient.SendAsync(request, ct).ConfigureAwait(false);
        }
        catch (TaskCanceledException ex) when (!ct.IsCancellationRequested)
        {
            throw new ApiException("TIMEOUT", "Request timed out.", innerException: ex);
        }
        catch (HttpRequestException ex)
        {
            _logger.LogWarning(ex, "Backend connection failed for {Uri}", uri);
            throw new BackendConnectionException("Backend is unreachable.", ex);
        }

        using (response)
        {
            if (!response.IsSuccessStatusCode)
            {
                throw await CreateApiExceptionAsync(response).ConfigureAwait(false);
            }

            try
            {
                var result = await response.Content.ReadFromJsonAsync<T>(JsonOptions, ct).ConfigureAwait(false);
                return result ?? throw new ApiException("PARSE_ERROR", "Response body was empty.");
            }
            catch (JsonException ex)
            {
                throw new ApiException("PARSE_ERROR", "Failed to parse response body.", innerException: ex);
            }
        }
    }

    private static async Task<ApiException> CreateApiExceptionAsync(HttpResponseMessage response)
    {
        var body = await response.Content.ReadAsStringAsync().ConfigureAwait(false);

        if (!string.IsNullOrWhiteSpace(body))
        {
            try
            {
                var error = JsonSerializer.Deserialize<ApiError>(body, JsonOptions);
                if (error is not null)
                {
                    return new ApiException(error);
                }
            }
            catch (JsonException)
            {
            }
        }

        var code = response.StatusCode.ToString().ToUpperInvariant();
        return new ApiException(code, $"Request failed with status code {(int)response.StatusCode} ({response.ReasonPhrase}).", body);
    }

    private static bool IsTerminal(string? status)
    {
        if (string.IsNullOrWhiteSpace(status))
        {
            return false;
        }

        return status.Equals("completed", StringComparison.OrdinalIgnoreCase)
            || status.Equals("done", StringComparison.OrdinalIgnoreCase)
            || status.Equals("failed", StringComparison.OrdinalIgnoreCase)
            || status.Equals("succeeded", StringComparison.OrdinalIgnoreCase)
            || status.Equals("canceled", StringComparison.OrdinalIgnoreCase)
            || status.Equals("cancelled", StringComparison.OrdinalIgnoreCase);
    }

    private static string BuildUri(string path, IReadOnlyDictionary<string, string?> query)
    {
        var parts = query
            .Where(pair => !string.IsNullOrWhiteSpace(pair.Value))
            .Select(pair => $"{Uri.EscapeDataString(pair.Key)}={Uri.EscapeDataString(pair.Value!)}")
            .ToArray();

        return parts.Length == 0 ? path : $"{path}?{string.Join("&", parts)}";
    }
}
