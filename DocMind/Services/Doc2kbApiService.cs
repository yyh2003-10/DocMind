using System.Net;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.IO;
using System.Text.RegularExpressions;
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

    public Task<LlmTestResult> LlmTestAsync(LlmTestRequest req, CancellationToken ct = default)
        => SendAsync<LlmTestResult>(HttpMethod.Post, "v1/llm/test", req, ct);

    public Task<LlmModelsResult> LlmModelsAsync(LlmModelsRequest req, CancellationToken ct = default)
        => SendAsync<LlmModelsResult>(HttpMethod.Post, "v1/llm/models", req, ct);

    public Task<ChatSessionListResponse> ListChatsAsync(int limit = 50, CancellationToken ct = default)
        => SendAsync<ChatSessionListResponse>(HttpMethod.Get, BuildUri("v1/chats", new Dictionary<string, string?>
        {
            ["limit"] = limit.ToString(),
        }), null, ct);

    public Task<ChatSessionDetail> GetChatAsync(string chatId, CancellationToken ct = default)
        => SendAsync<ChatSessionDetail>(HttpMethod.Get, $"v1/chats/{Uri.EscapeDataString(chatId)}", null, ct);

    public async Task DeleteChatAsync(string chatId, CancellationToken ct = default)
    {
        // 204 风格的 DELETE 统一走 SendAsync<object>；这里响应体无用，仅校验状态码
        await SendAsync<object>(HttpMethod.Delete, $"v1/chats/{Uri.EscapeDataString(chatId)}", null, ct).ConfigureAwait(false);
    }

    public Task<IngestResponse> IngestAsync(IngestRequest req, CancellationToken ct = default)
        => SendAsync<IngestResponse>(HttpMethod.Post, "v1/ingest", req, ct);

    public Task<IngestResponse> IngestTextAsync(IngestTextRequest req, CancellationToken ct = default)
        => SendAsync<IngestResponse>(HttpMethod.Post, "v1/ingest/text", req, ct);

    public Task<JobStatus> IngestJobAsync(IngestRequest req, CancellationToken ct = default)
        => SendAsync<JobStatus>(HttpMethod.Post, "v1/ingest/job", req, ct);

    public Task<SearchResponse> SearchAsync(SearchRequest req, CancellationToken ct = default)
        => SendAsync<SearchResponse>(HttpMethod.Post, "v1/search", req, ct);

    public Task<ChatResponse> ChatAsync(ChatRequest req, CancellationToken ct = default)
        => SendAsync<ChatResponse>(HttpMethod.Post, "v1/chat", req, ct);

    public async Task<ChatStreamResult> ChatStreamAsync(
        ChatRequest req, Action<string> onToken, Action<ChatStreamResult> onDone, CancellationToken ct = default)
    {
        var reqBody = JsonSerializer.Serialize(req, JsonOptions);
        DebugLog.Info($"→ POST v1/chat/stream\n  req: {Truncate(RedactSecrets(reqBody), 800)}", "API");

        using var request = new HttpRequestMessage(HttpMethod.Post, "v1/chat/stream")
        {
            Content = new StringContent(reqBody, System.Text.Encoding.UTF8, "application/json"),
        };

        HttpResponseMessage response;
        try
        {
            // ResponseHeadersRead：一旦响应头就绪即返回，后续逐块读取 body（真流式）
            response = await _httpClient.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct)
                .ConfigureAwait(false);
        }
        catch (TaskCanceledException ex) when (!ct.IsCancellationRequested)
        {
            throw new ApiException("TIMEOUT", "Request timed out.", innerException: ex);
        }
        catch (HttpRequestException ex)
        {
            _logger.LogWarning(ex, "Backend connection failed for v1/chat/stream");
            throw new BackendConnectionException("Backend is unreachable.", ex);
        }

        using (response)
        {
            if (!response.IsSuccessStatusCode)
            {
                var errBody = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                DebugLog.Error($"✗ POST v1/chat/stream -> {(int)response.StatusCode} ({response.ReasonPhrase})\n  resp: {Truncate(errBody, 800)}", "API");
                throw await CreateApiExceptionAsync(response).ConfigureAwait(false);
            }

            // SSE 响应应为 text/event-stream；内容类型异常通常意味着代理/网关返回了非流式 body
            var contentType = response.Content.Headers.ContentType?.MediaType;
            if (!string.IsNullOrEmpty(contentType)
                && contentType.Contains("text", StringComparison.OrdinalIgnoreCase)
                && !contentType.Contains("event-stream", StringComparison.OrdinalIgnoreCase))
            {
                DebugLog.Warn($"v1/chat/stream 响应 Content-Type 异常: {contentType}（预期 text/event-stream）", "API");
            }

            using var stream = await response.Content.ReadAsStreamAsync().ConfigureAwait(false);
            using var reader = new StreamReader(stream);

            string? line;
            var sw = System.Diagnostics.Stopwatch.StartNew();
            // 流式排查统计：token 帧数 / 首 token 延迟 / 是否收到终帧 / 未识别帧数
            var tokenFrames = 0;
            var unknownFrames = 0;
            long firstTokenMs = -1;
            var doneReceived = false;
            try
            {
                while ((line = await reader.ReadLineAsync().ConfigureAwait(false)) is not null)
                {
                    ct.ThrowIfCancellationRequested();

                    if (string.IsNullOrWhiteSpace(line))
                    {
                        continue; // SSE 帧间空行
                    }

                    const string prefix = "data: ";
                    if (!line.StartsWith(prefix, StringComparison.Ordinal))
                    {
                        continue;
                    }

                    var payload = line[prefix.Length..].Trim();
                    if (payload == "[DONE]")
                    {
                        break;
                    }

                    JsonDocument doc;
                    try
                    {
                        doc = JsonDocument.Parse(payload);
                    }
                    catch (JsonException ex)
                    {
                        DebugLog.Error($"SSE 帧 JSON 解析失败: {ex.Message}\n  raw: {Truncate(payload, 300)}", "API", ex);
                        throw new ApiException("PARSE_ERROR", $"Invalid SSE frame: {ex.Message}", innerException: ex);
                    }

                    using var d = doc;
                    var root = d.RootElement;

                    if (root.TryGetProperty("error", out var errElem))
                    {
                        var errMsg = errElem.GetString() ?? "unknown error";
                        DebugLog.Error($"SSE error 帧（后端 RAG/LLM 出错）: {errMsg}\n  raw: {Truncate(payload, 300)}", "API");
                        throw new ApiException("RAG_ERROR", errMsg);
                    }

                    if (root.TryGetProperty("token", out var tokElem))
                    {
                        if (tokenFrames == 0)
                        {
                            firstTokenMs = sw.ElapsedMilliseconds;
                        }
                        tokenFrames++;
                        onToken(tokElem.GetString() ?? string.Empty);
                        continue;
                    }

                    if (root.TryGetProperty("done", out var doneElem) && doneElem.ValueKind == JsonValueKind.True)
                    {
                        doneReceived = true;
                        var result = ParseDoneFrame(root);
                        onDone(result);
                    }
                    else if (root.ValueKind == JsonValueKind.Object)
                    {
                        // 未识别的帧类型：可能是后端新增事件（如心跳/进度），记录以便排查协议不匹配
                        unknownFrames++;
                        if (unknownFrames <= 5)
                        {
                            DebugLog.Debug($"SSE 未识别帧（已跳过）: {Truncate(payload, 300)}", "API");
                        }
                    }
                }
            }
            catch (Exception ex) when (ex is not (OperationCanceledException or ApiException))
            {
                // 流中途断开（后端崩溃/网络中断/代理截断）
                DebugLog.Error(
                    $"SSE 流读取中断 after {sw.ElapsedMilliseconds}ms (tokenFrames={tokenFrames} done={doneReceived}): {ex.GetType().Name}: {ex.Message}",
                    "API", ex);
                throw new ApiException("STREAM_INTERRUPTED", $"Chat stream interrupted: {ex.Message}", innerException: ex);
            }

            sw.Stop();
            DebugLog.Info(
                $"✓ POST v1/chat/stream completed in {sw.ElapsedMilliseconds}ms " +
                $"(tokenFrames={tokenFrames} firstToken={(firstTokenMs >= 0 ? $"{firstTokenMs}ms" : "none")} " +
                $"done={doneReceived} unknownFrames={unknownFrames})",
                "API");

            if (!doneReceived)
            {
                DebugLog.Warn("SSE 流结束但未收到 done 终帧（多轮 chat_id 与引用来源将丢失）", "API");
            }

            // 没收到 done 帧时给出空结果（下限保护）
            return new ChatStreamResult();
        }
    }

    private static ChatStreamResult ParseDoneFrame(JsonElement root)
    {
        string ChatId() => root.TryGetProperty("chat_id", out var v) ? (v.GetString() ?? string.Empty) : string.Empty;
        string Model() => root.TryGetProperty("model", out var v) ? (v.GetString() ?? string.Empty) : string.Empty;
        string Provider() => root.TryGetProperty("provider", out var v) ? (v.GetString() ?? string.Empty) : string.Empty;
        int TotalChunks() => root.TryGetProperty("total_chunks", out var v) && v.TryGetInt32(out var n) ? n : 0;
        int ElapsedMs() => root.TryGetProperty("elapsed_ms", out var v) && v.TryGetInt32(out var n) ? n : 0;

        var sources = new List<SourceRef>();
        if (root.TryGetProperty("sources", out var sArr) && sArr.ValueKind == JsonValueKind.Array)
        {
            foreach (var s in sArr.EnumerateArray())
            {
                sources.Add(new SourceRef
                {
                    Index = s.TryGetProperty("index", out var i) && i.TryGetInt32(out var iv) ? iv : 0,
                    Source = s.TryGetProperty("source", out var src) ? (src.GetString() ?? string.Empty) : string.Empty,
                    Format = s.TryGetProperty("format", out var f) ? (f.GetString() ?? string.Empty) : string.Empty,
                    Page = s.TryGetProperty("page", out var p) && p.ValueKind == JsonValueKind.Number ? p.GetInt32() : null,
                    Heading = s.TryGetProperty("heading", out var h) ? h.GetString() : null,
                    Score = s.TryGetProperty("score", out var sc) && sc.ValueKind == JsonValueKind.Number ? sc.GetDouble() : 0,
                    Snippet = s.TryGetProperty("snippet", out var snip) ? snip.GetString() : null,
                });
            }
        }

        return new ChatStreamResult
        {
            ChatId = ChatId(),
            Model = Model(),
            Provider = Provider(),
            TotalChunks = TotalChunks(),
            ElapsedMs = ElapsedMs(),
            Sources = sources,
        };
    }

    public Task<DocumentListResponse> ListDocumentsAsync(string? collection = null, int page = 1, int pageSize = 20, string? format = null, string sort = "created_at_desc", string? q = null, CancellationToken ct = default)
        => SendAsync<DocumentListResponse>(HttpMethod.Get, BuildUri("v1/documents", new Dictionary<string, string?>
        {
            ["collection"] = collection,
            ["page"] = page.ToString(),
            ["pageSize"] = pageSize.ToString(),
            ["format"] = format,
            ["sort"] = sort,
            ["q"] = q,
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

    public Task<JobStatus> CancelJobAsync(string jobId, CancellationToken ct = default)
        => SendAsync<JobStatus>(HttpMethod.Delete, $"v1/jobs/{Uri.EscapeDataString(jobId)}", null, ct);

    public Task UpsertChunkAnnotationAsync(int chunkId, string text, CancellationToken ct = default)
        => SendAsync<object>(HttpMethod.Put, $"v1/chunks/{chunkId}/annotation", new { text }, ct);

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

    public Task<GpuDiagnosis> GetGpuDiagnosisAsync(CancellationToken ct = default)
        => SendAsync<GpuDiagnosis>(HttpMethod.Get, "v1/system/gpu-diagnosis", null, ct);

    public async Task InstallGpuAsync(
        string path, Action<string> onLog, Action<bool> onDone, CancellationToken ct = default)
    {
        var reqBody = JsonSerializer.Serialize(new { path }, JsonOptions);
        DebugLog.Info($"→ POST v1/system/install-gpu  path={path}", "API");

        using var request = new HttpRequestMessage(HttpMethod.Post, "v1/system/install-gpu")
        {
            Content = new StringContent(reqBody, System.Text.Encoding.UTF8, "application/json"),
        };

        HttpResponseMessage response;
        try
        {
            response = await _httpClient.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct)
                .ConfigureAwait(false);
        }
        catch (TaskCanceledException ex) when (!ct.IsCancellationRequested)
        {
            throw new ApiException("TIMEOUT", "Request timed out.", innerException: ex);
        }
        catch (HttpRequestException ex)
        {
            _logger.LogWarning(ex, "Backend connection failed for v1/system/install-gpu");
            throw new BackendConnectionException("Backend is unreachable.", ex);
        }

        using (response)
        {
            if (!response.IsSuccessStatusCode)
            {
                var errBody = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                DebugLog.Error(
                    $"✗ POST v1/system/install-gpu -> {(int)response.StatusCode} ({response.ReasonPhrase})\n  resp: {Truncate(errBody, 800)}",
                    "API");
                throw await CreateApiExceptionAsync(response).ConfigureAwait(false);
            }

            using var stream = await response.Content.ReadAsStreamAsync().ConfigureAwait(false);
            using var reader = new StreamReader(stream);

            string? line;
            var sw = System.Diagnostics.Stopwatch.StartNew();
            var logLines = 0;
            var doneReceived = false;
            try
            {
                while ((line = await reader.ReadLineAsync().ConfigureAwait(false)) is not null)
                {
                    ct.ThrowIfCancellationRequested();

                    if (string.IsNullOrWhiteSpace(line))
                        continue;

                    const string prefix = "data: ";
                    if (!line.StartsWith(prefix, StringComparison.Ordinal))
                        continue;

                    var payload = line[prefix.Length..].Trim();
                    if (payload == "[DONE]")
                        break;

                    JsonDocument doc;
                    try
                    {
                        doc = JsonDocument.Parse(payload);
                    }
                    catch (JsonException ex)
                    {
                        DebugLog.Error($"GPU install SSE JSON 解析失败: {ex.Message}\n  raw: {Truncate(payload, 300)}", "API", ex);
                        throw new ApiException("PARSE_ERROR", $"Invalid SSE frame: {ex.Message}", innerException: ex);
                    }

                    using var d = doc;
                    var root = d.RootElement;

                    if (root.TryGetProperty("type", out var typeElem))
                    {
                        var eventType = typeElem.GetString() ?? "";
                        if (eventType == "log" && root.TryGetProperty("line", out var lineElem))
                        {
                            logLines++;
                            onLog(lineElem.GetString() ?? string.Empty);
                        }
                        else if (eventType == "done" && root.TryGetProperty("success", out var successElem))
                        {
                            doneReceived = true;
                            onDone(successElem.ValueKind != JsonValueKind.False);
                        }
                        else if (eventType == "error" && root.TryGetProperty("message", out var msgElem))
                        {
                            doneReceived = true;
                            DebugLog.Error($"GPU install 报错: {msgElem.GetString()}", "API");
                            onLog($"[错误] {msgElem.GetString()}");
                            onDone(false);
                        }
                    }
                }
            }
            catch (Exception ex) when (ex is not (OperationCanceledException or ApiException))
            {
                DebugLog.Error($"GPU install SSE 流读取中断: {ex.GetType().Name}: {ex.Message}", "API", ex);
                throw new ApiException("STREAM_INTERRUPTED", $"GPU install stream interrupted: {ex.Message}", innerException: ex);
            }

            sw.Stop();
            DebugLog.Info(
                $"✓ POST v1/system/install-gpu completed in {sw.ElapsedMilliseconds}ms "
                + $"(logLines={logLines} done={doneReceived})",
                "API");

            if (!doneReceived)
            {
                DebugLog.Warn("GPU install SSE 流结束但未收到 done 终帧", "API");
                onDone(false);
            }
        }
    }

    public async Task<GraphResponse> GetGraphAsync(string? collection = null, int limit = 200, CancellationToken ct = default)
    {
        var uri = $"v1/graph/visualize?limit={limit}";
        if (!string.IsNullOrWhiteSpace(collection))
        {
            uri += $"&collection={Uri.EscapeDataString(collection)}";
        }
        return await SendAsync<GraphResponse>(HttpMethod.Get, uri, null, ct);
    }

    public async Task<List<GraphEntityRelation>> GetEntityRelationsAsync(string entityId, int limit = 50, CancellationToken ct = default)
    {
        var uri = $"v1/graph/relations/{Uri.EscapeDataString(entityId)}?limit={limit}";
        return await SendAsync<List<GraphEntityRelation>>(HttpMethod.Get, uri, null, ct);
    }

    public async Task<GraphEntityDetailResponse> GetEntityDetailAsync(string entityId, int limit = 8, CancellationToken ct = default)
    {
        var uri = $"v1/graph/entities/{Uri.EscapeDataString(entityId)}/details?limit={limit}";
        return await SendAsync<GraphEntityDetailResponse>(HttpMethod.Get, uri, null, ct);
    }

    public async Task<GraphExtractResult> ExtractGraphAsync(string? collection = null, int topK = 20, CancellationToken ct = default)
    {
        var uri = $"v1/graph/extract?top_k={topK}";
        if (!string.IsNullOrWhiteSpace(collection))
        {
            uri += $"&collection={Uri.EscapeDataString(collection)}";
        }
        return await SendAsync<GraphExtractResult>(HttpMethod.Post, uri, null, ct);
    }

    public async Task<EntityDistillResponse> DistillEntityKnowledgeAsync(EntityDistillRequest req, CancellationToken ct = default)
    {
        return await SendAsync<EntityDistillResponse>(HttpMethod.Post, "v1/graph/entities/distill", req, ct);
    }

    public IDisposable SubscribeEvents(Action<EventMessage> onEvent, CancellationToken ct = default)
    {
        var cts = CancellationTokenSource.CreateLinkedTokenSource(ct);
        var token = cts.Token;

        _ = Task.Run(async () =>
        {
            var retryDelaySec = 1;
            while (!token.IsCancellationRequested)
            {
                try
                {
                    using var req = new HttpRequestMessage(HttpMethod.Get, "v1/events");
                    using var resp = await _httpClient.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, token);
                    if (!resp.IsSuccessStatusCode)
                    {
                        await Task.Delay(TimeSpan.FromSeconds(Math.Min(retryDelaySec, 10)), token);
                        retryDelaySec = Math.Min(retryDelaySec * 2, 10);
                        continue;
                    }

                    retryDelaySec = 1; // 连接成功重置退避
                    using var stream = await resp.Content.ReadAsStreamAsync(token);
                    using var reader = new StreamReader(stream);

                    while (!reader.EndOfStream && !token.IsCancellationRequested)
                    {
                        var line = await reader.ReadLineAsync(token);
                        if (string.IsNullOrWhiteSpace(line)) continue;

                        if (line.StartsWith("data: "))
                        {
                            var json = line["data: ".Length..].Trim();
                            if (string.IsNullOrWhiteSpace(json) || json == "[DONE]") continue;

                            try
                            {
                                var msg = JsonSerializer.Deserialize<EventMessage>(json, JsonOptions);
                                if (msg != null)
                                {
                                    onEvent(msg);
                                }
                            }
                            catch (Exception ex)
                            {
                                DebugLog.Warn($"解析 SSE 事件 JSON 异常: {ex.Message}", "API");
                            }
                        }
                    }
                }
                catch (OperationCanceledException) when (token.IsCancellationRequested)
                {
                    break;
                }
                catch (Exception ex)
                {
                    DebugLog.Warn($"SSE 事件流断开，准备重连: {ex.Message}", "API");
                    try
                    {
                        await Task.Delay(TimeSpan.FromSeconds(Math.Min(retryDelaySec, 10)), token);
                        retryDelaySec = Math.Min(retryDelaySec * 2, 10);
                    }
                    catch (OperationCanceledException)
                    {
                        break;
                    }
                }
            }
        }, token);

        return cts;
    }

    private async Task<T> SendAsync<T>(HttpMethod method, string uri, object? payload, CancellationToken ct)
    {
        // 调试日志：请求出参
        string? reqBody = null;
        if (payload is not null)
        {
            reqBody = JsonSerializer.Serialize(payload, JsonOptions);
        }
        DebugLog.Info($"→ {method} {uri}" + (reqBody is null ? "" : "\n  req: " + Truncate(RedactSecrets(reqBody), 800)), "API");

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

    /// <summary>日志脱敏：掩盖请求体中的 *api_key 字段值（如 /v1/config 推送的 llm_api_key），避免明文密钥落入日志文件。</summary>
    private static string RedactSecrets(string body)
        => Regex.Replace(body, @"(""[^""]*api_key""\s*:\s*"")[^""]*("")", "$1***$2");

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
