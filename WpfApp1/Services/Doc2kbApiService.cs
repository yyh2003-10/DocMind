using System.Net;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using DocMind.Models;
using Microsoft.Extensions.Logging;

namespace DocMind.Services;

public class Doc2kbApiService : IDoc2kbApiService
{
    /// <summary>snake_case 命名策略：发 POST body 时把 CamelCase 字段名转 snake_case，
    /// 与后端 pydantic DTO 字段对齐（避免 422）。</summary>
    private static readonly JsonNamingPolicy SnakeCasePolicy = new SnakeCaseNamingPolicy();

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        // 输出（发往后端）用 snake_case；输入（解析后端响应）仍 case-insensitive
        PropertyNamingPolicy = SnakeCasePolicy,
    };

    private readonly HttpClient _httpClient;
    private readonly ILogger<Doc2kbApiService> _logger;

    public Doc2kbApiService(HttpClient httpClient, ILogger<Doc2kbApiService> logger)
    {
        _httpClient = httpClient;
        _logger = logger;
    }

    /// <summary>更新后端 BaseAddress（端口被占用顺延时跟随实际端口）。</summary>
    public void UpdateBaseAddress(string baseUrl)
    {
        try
        {
            _httpClient.BaseAddress = new Uri(baseUrl.TrimEnd('/') + "/");
            DebugLog.Info($"API 客户端 BaseAddress 已更新: {_httpClient.BaseAddress}", "API");
        }
        catch (UriFormatException ex)
        {
            DebugLog.Error($"无效的后端地址: {baseUrl}", "API", ex);
        }
    }

    public Task<HealthStatus> GetHealthAsync(CancellationToken ct = default)
        => SendAsync<HealthStatus>(HttpMethod.Get, "v1/health", null, ct);

    public Task<BackendConfig> GetConfigAsync(CancellationToken ct = default)
        => SendAsync<BackendConfig>(HttpMethod.Get, "v1/config", null, ct);

    public Task<BackendConfig> UpdateConfigAsync(BackendConfigUpdate req, CancellationToken ct = default)
        => SendAsync<BackendConfig>(HttpMethod.Post, "v1/config", req, ct);

    public Task<IngestResponse> IngestAsync(IngestRequest req, CancellationToken ct = default)
        => SendAsync<IngestResponse>(HttpMethod.Post, "v1/ingest", req, ct);

    public Task<JobStatus> IngestJobAsync(IngestRequest req, CancellationToken ct = default)
        => SendAsync<JobStatus>(HttpMethod.Post, "v1/ingest/job", req, ct);

    public Task<SearchResponse> SearchAsync(SearchRequest req, CancellationToken ct = default)
        => SendAsync<SearchResponse>(HttpMethod.Post, "v1/search", req, ct);

    public Task<ChatResponse> ChatAsync(ChatRequest req, CancellationToken ct = default)
        => SendAsync<ChatResponse>(HttpMethod.Post, "v1/chat", req, ct);

    public Task<DocumentListResponse> ListDocumentsAsync(string? collection = null, int page = 1, int pageSize = 20, string? format = null, string sort = "created_at_desc", CancellationToken ct = default)
        => SendAsync<DocumentListResponse>(HttpMethod.Get, BuildUri("v1/documents", new Dictionary<string, string?>
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

    public Task<Stats> CreateCollectionAsync(string name, CancellationToken ct = default)
        => SendAsync<Stats>(HttpMethod.Post, "v1/collections", new { name }, ct);

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
        // 调试日志：请求出参
        string? reqBody = null;
        if (payload is not null)
        {
            reqBody = JsonSerializer.Serialize(payload, JsonOptions);
        }
        DebugLog.Info($"→ {method} {uri}" + (reqBody is null ? "" : "\n  req: " + Truncate(reqBody, 800)), "API");

        using var request = new HttpRequestMessage(method, uri);
        if (payload is not null)
        {
            request.Content = System.Net.Http.Json.JsonContent.Create(payload, options: JsonOptions);
        }

        var sw = System.Diagnostics.Stopwatch.StartNew();
        HttpResponseMessage response;
        try
        {
            response = await _httpClient.SendAsync(request, ct).ConfigureAwait(false);
        }
        catch (TaskCanceledException ex) when (!ct.IsCancellationRequested)
        {
            sw.Stop();
            DebugLog.Error($"✗ {method} {uri} TIMEOUT after {sw.ElapsedMilliseconds}ms", "API", ex);
            throw new ApiException("TIMEOUT", "Request timed out.", innerException: ex);
        }
        catch (HttpRequestException ex)
        {
            sw.Stop();
            DebugLog.Error($"✗ {method} {uri} unreachable after {sw.ElapsedMilliseconds}ms", "API", ex);
            _logger.LogWarning(ex, "Backend connection failed for {Uri}", uri);
            throw new BackendConnectionException("Backend is unreachable.", ex);
        }
        catch (Exception ex)
        {
            sw.Stop();
            DebugLog.Error($"✗ {method} {uri} unexpected error after {sw.ElapsedMilliseconds}ms", "API", ex);
            throw;
        }

        sw.Stop();
        var status = (int)response.StatusCode;

        using (response)
        {
            if (!response.IsSuccessStatusCode)
            {
                var errBody = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                DebugLog.Error(
                    $"✗ {method} {uri} -> {status} ({response.ReasonPhrase}) in {sw.ElapsedMilliseconds}ms"
                    + (string.IsNullOrWhiteSpace(errBody) ? "" : "\n  resp: " + Truncate(errBody, 800)),
                    "API");
                var ex = await CreateApiExceptionAsync(response).ConfigureAwait(false);
                throw ex;
            }

            // 成功路径：读取 raw body 用于日志，再反序列化
            string rawBody;
            try
            {
                rawBody = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
            }
            catch
            {
                rawBody = "<unreadable>";
            }

            DebugLog.Info(
                $"✓ {method} {uri} -> {status} in {sw.ElapsedMilliseconds}ms"
                + (string.IsNullOrWhiteSpace(rawBody) ? "" : "\n  resp: " + Truncate(rawBody, 800)),
                "API");

            try
            {
                var result = JsonSerializer.Deserialize<T>(rawBody, JsonOptions);
                return result ?? throw new ApiException("PARSE_ERROR", "Response body was empty.");
            }
            catch (JsonException ex)
            {
                DebugLog.Error($"JSON parse failed for {method} {uri}: {ex.Message}\n  raw: {Truncate(rawBody, 800)}", "API", ex);
                throw new ApiException("PARSE_ERROR", "Failed to parse response body.", innerException: ex);
            }
        }
    }

    private static string Truncate(string s, int max)
        => s.Length > max ? s[..max] + "…(truncated)" : s;

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
