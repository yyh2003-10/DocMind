using System.Net;
using System.Net.Http;
using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using DocMind.Models;
using Microsoft.Extensions.Logging;

namespace DocMind.Services;

public class Doc2kbApiService : IDoc2kbApiService
{
    /// <summary>后端服务访问令牌（服务端生成于 %LOCALAPPDATA%/doc2mind/server.token）。
    /// 所有请求经 Bearer 注入；令牌文件不存在/读取失败则置空（后端可能未启用鉴权）。</summary>
    private static string? _authToken;

    /// <summary>从后端数据目录读取服务令牌（server.token）。每次启动/后端重启后调用一次。</summary>
    public static string? LoadAuthToken()
    {
        try
        {
            var dataDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "doc2mind");
            var tokenFile = Path.Combine(dataDir, "server.token");
            if (!File.Exists(tokenFile))
            {
                return null;
            }
            var token = File.ReadAllText(tokenFile).Trim();
            _authToken = string.IsNullOrWhiteSpace(token) ? null : token;
            return _authToken;
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"读取后端服务令牌失败: {ex.Message}", "API");
            return null;
        }
    }

    /// <summary>把令牌注入请求头（Bearer 优先，兼容 X-DocMind-Token）。
    /// 注意：HttpRequestMessage.Headers.Authorization 对非标准 scheme 会抛异常，
    /// 这里直接写原始头，避免 "Bearer" + 空格带来的拼写问题。</summary>
    private static void AttachAuthHeader(HttpRequestMessage request)
    {
        if (!string.IsNullOrEmpty(_authToken))
        {
            request.Headers.TryAddWithoutValidation("Authorization", $"Bearer {_authToken}");
        }
    }

    /// <summary>snake_case 命名规范：发 POST body 时把 CamelCase 字段名转 snake_case，
    /// 与后端 pydantic DTO 字段对齐（避免 422）。</summary>
    private static readonly JsonNamingPolicy SnakeCasePolicy = new SnakeCaseNamingPolicy();

    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNameCaseInsensitive = true,
        // 输出（发往后端）用 snake_case；输入（解析后端响应）仍 case-insensitive
        PropertyNamingPolicy = SnakeCasePolicy,
        // null 字段不发送（缺省 = 后端字段默认值，语义等价）。此前 ChatRequest 无附件时
        // 显式发 "attachments":null，遇到把 attachments 声明为不可空 list[str] 的后端版本
        // 会直接 422（"Input should be a valid list"），省略后旧/新后端均可正常接受。
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
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

    public Task<ChatSessionListResponse> ListChatsAsync(int limit = 50, string? q = null, CancellationToken ct = default)
    {
        var query = new Dictionary<string, string?>
        {
            ["limit"] = limit.ToString(),
        };
        if (!string.IsNullOrWhiteSpace(q))
            query["q"] = q.Trim();
        return SendAsync<ChatSessionListResponse>(HttpMethod.Get, BuildUri("v1/chats", query), null, ct);
    }

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

    public Task<DoctorReportResult> GetDoctorReportAsync(bool network = true, CancellationToken ct = default)
        => SendAsync<DoctorReportResult>(HttpMethod.Get, BuildUri("v1/doctor", new Dictionary<string, string?>
        {
            ["network"] = network ? "true" : "false",
        }), null, ct);

    public Task<SampleIngestResult> IngestSampleAsync(string collection = "default", CancellationToken ct = default)
        => SendAsync<SampleIngestResult>(HttpMethod.Post, "v1/sample/ingest", new { Collection = collection }, ct);

    public Task<BackupResponse> CreateBackupAsync(string? outputPath = null, CancellationToken ct = default)
        => SendAsync<BackupResponse>(HttpMethod.Post, "v1/backup", new BackupCreateRequest(outputPath), ct);

    public Task<BackupResponse> RestoreBackupAsync(string backupPath, CancellationToken ct = default)
        => SendAsync<BackupResponse>(HttpMethod.Post, "v1/backup/restore", new BackupRestoreRequest(backupPath), ct);

    public Task<BackupResponse> CreateDiagnosticBundleAsync(string? outputPath = null, bool includeLogs = false, CancellationToken ct = default)
        => SendAsync<BackupResponse>(HttpMethod.Post, "v1/diagnostics/bundle", new DiagnosticBundleRequest(outputPath, includeLogs), ct);

    public Task<ChatResponse> ChatAsync(ChatRequest req, CancellationToken ct = default)
        => SendAsync<ChatResponse>(HttpMethod.Post, "v1/chat", req, ct);

    public async Task<ChatStreamResult> ChatStreamAsync(
        ChatRequest req, Action<string> onToken, Action<ChatStreamResult> onDone,
        Action<string>? onStatus = null, Action<string>? onThinking = null,
        Action? onRestart = null, Action<string, string>? onAgentEvent = null, Action<string, string>? onPermissionRequest = null,
        CancellationToken ct = default)
    {
        var reqBody = JsonSerializer.Serialize(req, JsonOptions);
        DebugLog.Info($"→ POST v1/chat/stream\n  req: {Truncate(RedactSecrets(reqBody), 800)}", "API");

        using var request = new HttpRequestMessage(HttpMethod.Post, "v1/chat/stream")
        {
            Content = new StringContent(reqBody, System.Text.Encoding.UTF8, "application/json"),
        };
        AttachAuthHeader(request);

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
            ChatStreamResult? finalResult = null;
            try
            {
                // 空闲超时兜底（AUD-007）：ReadLineAsync 阻塞期间不响应父 ct，且
                // ResponseHeadersRead 使 HttpClient.Timeout 不覆盖 body 读取。后端每
                // 15s 心跳一次，若 30s 无任何行到达则判定后端卡死，主动报错收敛，
                // 不再让 UI 永久转圈。
                var idleTimeout = TimeSpan.FromSeconds(30);
                while (true)
                {
                    string? nextLine;
                    using (var readCts = CancellationTokenSource.CreateLinkedTokenSource(ct))
                    {
                        readCts.CancelAfter(idleTimeout);
                        try
                        {
                            nextLine = await reader.ReadLineAsync(readCts.Token).ConfigureAwait(false);
                        }
                        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
                        {
                            // 空闲超时（非用户取消）→ 明确的流式超时错误
                            throw new ApiException("STREAM_TIMEOUT",
                                "后端长时间无响应（30s 无数据），流式对话已自动终止。");
                        }
                    }

                    if (nextLine is null)
                    {
                        break;
                    }

                    line = nextLine;
                    ct.ThrowIfCancellationRequested();

                    if (string.IsNullOrWhiteSpace(line))
                    {
                        continue; // SSE 帧间空行
                    }

                    // 容错前缀解析（AUD-016）：标准为 "data: "，代理可能改写为无空格 "data:"，
                    // 两种都接受，避免静默丢帧；重复拼接的多层前缀也一并剥掉
                    if (!line.StartsWith("data:", StringComparison.Ordinal))
                    {
                        continue;
                    }

                    var payload = StripSsePrefix(line);
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

                    if (root.TryGetProperty("type", out var typeElem)
                        && typeElem.GetString() == "status"
                        && root.TryGetProperty("message", out var msgElem))
                    {
                        onStatus?.Invoke(msgElem.GetString() ?? string.Empty);
                        continue;
                    }

                    if (root.TryGetProperty("type", out var typeElem2)
                        && typeElem2.GetString() == "thinking"
                        && root.TryGetProperty("text", out var thElem))
                    {
                        onThinking?.Invoke(thElem.GetString() ?? string.Empty);
                        continue;
                    }

                    // 后端重启生成（上下文过大精简后重试）：此前收到的正文是废弃的半成品，
                    // 必须由调用方显式丢弃，否则两次尝试的内容会首尾相接变成重复文本
                    if (root.TryGetProperty("type", out var typeElem3)
                        && typeElem3.GetString() == "restart")
                    {
                        onRestart?.Invoke();
                        continue;
                    }

                    // Agent 轨迹帧（T7）：默认安全忽略；有回调时上报 tool 名与摘要
                    if (root.TryGetProperty("type", out var typeAgent)
                        && onAgentEvent is not null)
                    {
                        var agentType = typeAgent.GetString() ?? "";
                        if (agentType == "permission_request")
                        {
                            var reqId = root.TryGetProperty("request_id", out var rid) ? (rid.GetString() ?? "") : "";
                            var pTool = root.TryGetProperty("tool_id", out var pt) ? (pt.GetString() ?? "") : "";
                            onPermissionRequest?.Invoke(reqId, pTool);
                            onAgentEvent?.Invoke("permission_request", string.IsNullOrWhiteSpace(pTool) ? reqId : $"{pTool} ({reqId})");
                            continue;
                        }
                        if (agentType is "agent_plan" or "tool_call" or "tool_result" or "artifact_ready")
                        {
                            var toolId = root.TryGetProperty("tool_id", out var tid) ? (tid.GetString() ?? "")
                                : root.TryGetProperty("tool", out var t2) ? (t2.GetString() ?? "") : "";
                            var summary = root.TryGetProperty("summary", out var sum)
                                ? (sum.GetString() ?? "")
                                : root.TryGetProperty("status", out var st) ? (st.GetString() ?? "") : agentType;
                            onAgentEvent(agentType, string.IsNullOrWhiteSpace(toolId) ? summary : $"{toolId}: {summary}");
                            continue;
                        }
                    }

                    if (root.TryGetProperty("done", out var doneElem) && doneElem.ValueKind == JsonValueKind.True)
                    {
                        doneReceived = true;
                        finalResult = ParseDoneFrame(root);
                        onDone(finalResult);
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

            // 没收到 done 帧时给出空结果（下限保护）；收到则返回解析出的终帧数据
            //（调用方既可用返回值也可用 onDone 回调，二者一致）
            return finalResult ?? new ChatStreamResult();
        }
    }

    private static ChatStreamResult ParseDoneFrame(JsonElement root)
    {
        string ChatId() => root.TryGetProperty("chat_id", out var v) ? (v.GetString() ?? string.Empty) : string.Empty;
        string Model() => root.TryGetProperty("model", out var v) ? (v.GetString() ?? string.Empty) : string.Empty;
        string Provider() => root.TryGetProperty("provider", out var v) ? (v.GetString() ?? string.Empty) : string.Empty;
        int TotalChunks() => root.TryGetProperty("total_chunks", out var v) && v.TryGetInt32(out var n) ? n : 0;
        int ElapsedMs() => root.TryGetProperty("elapsed_ms", out var v) && v.TryGetInt32(out var n) ? n : 0;
        bool Partial() => root.TryGetProperty("partial", out var v) && v.ValueKind == JsonValueKind.True;
        string? Warning() => root.TryGetProperty("warning", out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
        // P0 双轨 / 续写（旧后端缺字段时为 null/false）
        string? PromptTrack() => root.TryGetProperty("prompt_track", out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
        bool Truncated() => root.TryGetProperty("truncated", out var v) && v.ValueKind == JsonValueKind.True;
        bool ContinueSupported() => !root.TryGetProperty("continue_supported", out var v)
            || v.ValueKind is not JsonValueKind.False;
        string? ResponseMode() => root.TryGetProperty("response_mode", out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
        string? ContinueHint() => root.TryGetProperty("continue_hint", out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
        // 后端实际生效人设（创作意图自动路由时可能与请求不同）
        string? Persona() => root.TryGetProperty("persona", out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;

        // model_spec：后端确认的模型规格（display_name 等字段可能缺省，需逐个守卫）
        string? modelDisplayName = null;
        int? contextWindow = null;
        int? maxOutputTokens = null;
        bool? isReasoningModel = null;
        string? modelSpecSummary = null;
        if (root.TryGetProperty("model_spec", out var spec) && spec.ValueKind == JsonValueKind.Object)
        {
            if (spec.TryGetProperty("display_name", out var dn) && dn.ValueKind == JsonValueKind.String)
            {
                modelDisplayName = dn.GetString();
            }
            if (spec.TryGetProperty("context_window", out var cw) && cw.TryGetInt32(out var cwVal))
            {
                contextWindow = cwVal;
            }
            if (spec.TryGetProperty("max_output_tokens", out var mot) && mot.TryGetInt32(out var motVal))
            {
                maxOutputTokens = motVal;
            }
            if (spec.TryGetProperty("is_reasoning_model", out var rm) && rm.ValueKind is JsonValueKind.True or JsonValueKind.False)
            {
                isReasoningModel = rm.GetBoolean();
            }
            if (spec.TryGetProperty("summary_text", out var st) && st.ValueKind == JsonValueKind.String)
            {
                modelSpecSummary = st.GetString();
            }
        }

        var sources = new List<SourceRef>();
        if (root.TryGetProperty("sources", out var sArr) && sArr.ValueKind == JsonValueKind.Array)
        {
            foreach (var s in sArr.EnumerateArray())
            {
                sources.Add(new SourceRef
                {
                    Index = s.TryGetProperty("index", out var i) && i.TryGetInt32(out var iv) ? iv : 0,
                    Source = s.TryGetProperty("source", out var src) ? (src.GetString() ?? string.Empty) : string.Empty,
                    ChunkId = s.TryGetProperty("chunk_id", out var cid) && cid.ValueKind == JsonValueKind.Number ? cid.GetInt32() : null,
                    Format = s.TryGetProperty("format", out var f) ? (f.GetString() ?? string.Empty) : string.Empty,
                    Page = s.TryGetProperty("page", out var p) && p.ValueKind == JsonValueKind.Number ? p.GetInt32() : null,
                    Heading = s.TryGetProperty("heading", out var h) ? h.GetString() : null,
                    Score = s.TryGetProperty("score", out var sc) && sc.ValueKind == JsonValueKind.Number ? sc.GetDouble() : 0,
                    ScoreType = s.TryGetProperty("score_type", out var sct) ? (sct.GetString() ?? string.Empty) : string.Empty,
                    ConfidenceLabel = s.TryGetProperty("confidence_label", out var cl) ? (cl.GetString() ?? string.Empty) : string.Empty,
                    SourceType = s.TryGetProperty("source_type", out var st) ? (st.GetString() ?? "local") : "local",
                    Url = s.TryGetProperty("url", out var u) ? u.GetString() : null,
                    Title = s.TryGetProperty("title", out var t) ? t.GetString() : null,
                    Snippet = s.TryGetProperty("snippet", out var snip) ? snip.GetString() : null,
                    SourceName = s.TryGetProperty("source_name", out var sn) ? sn.GetString() : null,
                    Domain = s.TryGetProperty("domain", out var dom) ? dom.GetString() : null,
                    PublishedAt = s.TryGetProperty("published_at", out var pa) ? pa.GetString() : null,
                    ContentFetched = s.TryGetProperty("content_fetched", out var cf)
                        && cf.ValueKind is JsonValueKind.True or JsonValueKind.False
                        && cf.GetBoolean(),
                    CorroboratedBy = s.TryGetProperty("corroborated_by", out var cb) && cb.TryGetInt32(out var cbv) ? cbv : 0,
                    EvidenceLevel = s.TryGetProperty("evidence_level", out var el) ? (el.GetString() ?? "单一来源") : "单一来源",
                });
            }
        }

        EvidenceSummary? evidence = null;
        if (root.TryGetProperty("evidence", out var ev) && ev.ValueKind == JsonValueKind.Object)
        {
            CitationAudit? citationAudit = null;
            if (ev.TryGetProperty("citation_audit", out var ca) && ca.ValueKind == JsonValueKind.Object)
            {
                static List<int> ReadIntList(JsonElement el)
                {
                    var list = new List<int>();
                    if (el.ValueKind == JsonValueKind.Array)
                    {
                        foreach (var item in el.EnumerateArray())
                        {
                            if (item.TryGetInt32(out var v)) list.Add(v);
                        }
                    }
                    return list;
                }
                static List<int> ReadIntListProp(JsonElement parent, string name)
                    => parent.TryGetProperty(name, out var el) ? ReadIntList(el) : [];
                citationAudit = new CitationAudit
                {
                    Cited = ReadIntListProp(ca, "cited"),
                    Valid = ReadIntListProp(ca, "valid"),
                    Invalid = ReadIntListProp(ca, "invalid"),
                    EvidenceSupport = ReadIntListProp(ca, "evidence_support"),
                    DisclaimerOnly = ReadIntListProp(ca, "disclaimer_only"),
                    ValidRatio = ca.TryGetProperty("valid_ratio", out var vr) && vr.ValueKind == JsonValueKind.Number
                        ? vr.GetDouble() : 1.0,
                    Ok = !ca.TryGetProperty("ok", out var ok)
                        || (ok.ValueKind is JsonValueKind.True or JsonValueKind.False && ok.GetBoolean()),
                };
            }
            evidence = new EvidenceSummary
            {
                LocalCount = ev.TryGetProperty("local_count", out var lc) && lc.TryGetInt32(out var lcv) ? lcv : 0,
                LocalCiteCount = ev.TryGetProperty("local_cite_count", out var lcc) && lcc.TryGetInt32(out var lccv) ? lccv
                    : (ev.TryGetProperty("local_count", out lc) && lc.TryGetInt32(out lcv) ? lcv : 0),
                LocalHitCount = ev.TryGetProperty("local_hit_count", out var lhc) && lhc.TryGetInt32(out var lhcv) ? lhcv
                    : (ev.TryGetProperty("local_count", out lc) && lc.TryGetInt32(out lcv) ? lcv : 0),
                WebFetchedCount = ev.TryGetProperty("web_fetched_count", out var wf) && wf.TryGetInt32(out var wfv) ? wfv : 0,
                WebUnfetchedCount = ev.TryGetProperty("web_unfetched_count", out var wu) && wu.TryGetInt32(out var wuv) ? wuv : 0,
                GraphInjected = ev.TryGetProperty("graph_injected", out var gi)
                    && gi.ValueKind is JsonValueKind.True or JsonValueKind.False
                    && gi.GetBoolean(),
                FallbackGeneralKnowledge = ev.TryGetProperty("fallback_general_knowledge", out var fg)
                    && fg.ValueKind is JsonValueKind.True or JsonValueKind.False
                    && fg.GetBoolean(),
                CitableTotal = ev.TryGetProperty("citable_total", out var ct) && ct.TryGetInt32(out var ctv) ? ctv : 0,
                SynthesizedSourceCount = ev.TryGetProperty("synthesized_source_count", out var ssc) && ssc.TryGetInt32(out var sscv) ? sscv : 0,
                SingleSource = ev.TryGetProperty("single_source", out var ss)
                    && ss.ValueKind is JsonValueKind.True or JsonValueKind.False
                    && ss.GetBoolean(),
                WebOnly = ev.TryGetProperty("web_only", out var wo)
                    && wo.ValueKind is JsonValueKind.True or JsonValueKind.False
                    && wo.GetBoolean(),
                DegradedRetrieval = ev.TryGetProperty("degraded_retrieval", out var dr)
                    && dr.ValueKind is JsonValueKind.True or JsonValueKind.False
                    && dr.GetBoolean(),
                PromptTrack = ev.TryGetProperty("prompt_track", out var pt) && pt.ValueKind == JsonValueKind.String
                    ? pt.GetString() : null,
                CitationAudit = citationAudit,
            };
        }

        return new ChatStreamResult
        {
            ChatId = ChatId(),
            Model = Model(),
            Provider = Provider(),
            Persona = Persona(),
            TotalChunks = TotalChunks(),
            ElapsedMs = ElapsedMs(),
            Sources = sources,
            Partial = Partial(),
            Warning = Warning(),
            PromptTrack = PromptTrack(),
            Truncated = Truncated(),
            ContinueSupported = ContinueSupported(),
            ResponseMode = ResponseMode(),
            ContinueHint = ContinueHint(),
            ModelDisplayName = modelDisplayName,
            ContextWindow = contextWindow,
            MaxOutputTokens = maxOutputTokens,
            IsReasoningModel = isReasoningModel,
            ModelSpecSummary = modelSpecSummary,
            Evidence = evidence ?? EvidenceSummary.FromSources(sources),
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

    public Task<JobStatus> CurateAsync(CurateRequest req, CancellationToken ct = default)
        => SendAsync<JobStatus>(HttpMethod.Post, "v1/curate", req, ct);
    public async Task<bool> ResolveAgentPermissionAsync(string requestId, bool allow, CancellationToken ct = default)
    {
        var body = new Dictionary<string, object?> { ["decision"] = allow ? "allow" : "deny" };
        var json = await SendRawAsync(HttpMethod.Post, $"v1/agent/permission/{Uri.EscapeDataString(requestId)}", body, ct);
        return json.Contains("\"resolved\":true") || json.Contains("\"resolved\": true");
    }

    public Task<CurateRunsResponse> ListCurateRunsAsync(int days = 7, int limit = 50, CancellationToken ct = default)
        => SendAsync<CurateRunsResponse>(HttpMethod.Get, $"v1/curate-runs?days={days}&limit={limit}", null, ct);

    public async Task<LibraryEvalResult> EvalLibraryAsync(string? collection = null, int sample = 30, CancellationToken ct = default)
    {
        var body = new Dictionary<string, object?> { ["sample"] = sample };
        if (!string.IsNullOrWhiteSpace(collection)) body["collection"] = collection;
        var json = await SendRawAsync(HttpMethod.Post, "v1/eval/library", body, ct);
        return ParseEvalLibrary(json);
    }

    public Task<RetrievalRecommendedResult> GetRetrievalRecommendedAsync(CancellationToken ct = default)
        => SendRecommendedAsync(HttpMethod.Get, "v1/config/retrieval-recommended", null, ct);

    public Task<RetrievalRecommendedResult> ApplyRetrievalRecommendedAsync(CancellationToken ct = default)
        => SendRecommendedAsync(HttpMethod.Post, "v1/config/retrieval-recommended", new Dictionary<string, object?>(), ct);

    private async Task<RetrievalRecommendedResult> SendRecommendedAsync(HttpMethod method, string path, object? body, CancellationToken ct)
    {
        var json = await SendRawAsync(method, path, body, ct);
        return ParseRecommended(json);
    }

    private static LibraryEvalResult ParseEvalLibrary(string json)
    {
        try
        {
            using var doc = System.Text.Json.JsonDocument.Parse(json);
            var root = doc.RootElement;
            static double? Num(System.Text.Json.JsonElement e, params string[] names)
            {
                foreach (var n in names)
                {
                    if (e.TryGetProperty(n, out var v) && v.ValueKind == System.Text.Json.JsonValueKind.Number)
                        return v.GetDouble();
                }
                return null;
            }
            var suggestions = new List<string>();
            if (root.TryGetProperty("suggestions", out var sug) && sug.ValueKind == System.Text.Json.JsonValueKind.Array)
            {
                foreach (var s in sug.EnumerateArray())
                {
                    var t = s.ToString();
                    if (!string.IsNullOrWhiteSpace(t)) suggestions.Add(t);
                }
            }
            return new LibraryEvalResult
            {
                SelfRecallAtK = Num(root, "self_recall_at_k", "self_recall", "recall"),
                Mrr = Num(root, "mrr"),
                Sample = root.TryGetProperty("sample", out var sp) && sp.TryGetInt32(out var spi) ? spi : 0,
                Suggestions = suggestions,
                Summary = root.TryGetProperty("summary", out var sum) ? sum.ToString() : null,
                RawJson = json,
            };
        }
        catch
        {
            return new LibraryEvalResult { RawJson = json };
        }
    }

    private static RetrievalRecommendedResult ParseRecommended(string json)
    {
        try
        {
            using var doc = System.Text.Json.JsonDocument.Parse(json);
            var root = doc.RootElement;
            var changes = new Dictionary<string, string>();
            if (root.TryGetProperty("changes", out var ch) && ch.ValueKind == System.Text.Json.JsonValueKind.Object)
            {
                foreach (var p in ch.EnumerateObject())
                    changes[p.Name] = p.Value.ToString();
            }
            return new RetrievalRecommendedResult
            {
                AlignedBefore = root.TryGetProperty("aligned_before", out var ab) && ab.ValueKind == System.Text.Json.JsonValueKind.True,
                Applied = root.TryGetProperty("applied", out var ap) && ap.ValueKind == System.Text.Json.JsonValueKind.True,
                Description = root.TryGetProperty("description", out var d) ? d.ToString() : "",
                Changes = changes,
                RawJson = json,
            };
        }
        catch
        {
            return new RetrievalRecommendedResult { RawJson = json };
        }
    }

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

    public Task<LocalAiEnvironment> GetLocalAiEnvironmentAsync(CancellationToken ct = default)
        => SendAsync<LocalAiEnvironment>(HttpMethod.Get, "v1/system/local-ai-environment", null, ct);

    public Task<OllamaPullStatus> StartOllamaPullAsync(string model, CancellationToken ct = default)
        => SendAsync<OllamaPullStatus>(HttpMethod.Post, "v1/system/ollama/pull", new { model_name = model }, ct);

    public Task<OllamaPullStatus> GetOllamaPullStatusAsync(CancellationToken ct = default)
        => SendAsync<OllamaPullStatus>(HttpMethod.Get, "v1/system/ollama/pull/status", null, ct);

    public Task InstallGpuAsync(string path, Action<string> onLog, Action<bool> onDone, CancellationToken ct = default)
        => InstallViaSseAsync("v1/system/install-gpu", path, "GPU install", onLog, onDone, ct);

    public Task InstallOcrAsync(string path, Action<string> onLog, Action<bool> onDone, CancellationToken ct = default)
        => InstallViaSseAsync("v1/system/install-ocr", path, "OCR install", onLog, onDone, ct);

    /// <summary>下载嵌入模型（POST /v1/system/download-model，SSE 流式进度）。
    /// progress 每收到一帧进度（downloaded/total 文件与字节）触发；成功返回模型快照目录路径。</summary>
    public async Task<string?> DownloadModelAsync(string? modelName = null, IProgress<DownloadProgressFrame>? progress = null, CancellationToken ct = default)
    {
        var reqBody = JsonSerializer.Serialize(new { model_name = modelName }, JsonOptions);
        DebugLog.Info($"→ POST v1/system/download-model  model={modelName ?? "(default)"}", "API");

        using var request = new HttpRequestMessage(HttpMethod.Post, "v1/system/download-model")
        {
            Content = new StringContent(reqBody, System.Text.Encoding.UTF8, "application/json"),
        };
        AttachAuthHeader(request);

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
            _logger.LogWarning(ex, "Backend connection failed for download-model");
            throw new BackendConnectionException("Backend is unreachable.", ex);
        }

        using (response)
        {
            if (!response.IsSuccessStatusCode)
            {
                var errBody = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                DebugLog.Error(
                    $"✗ POST v1/system/download-model -> {(int)response.StatusCode} ({response.ReasonPhrase})\n  resp: {Truncate(errBody, 800)}",
                    "API");
                throw await CreateApiExceptionAsync(response).ConfigureAwait(false);
            }

            using var stream = await response.Content.ReadAsStreamAsync().ConfigureAwait(false);
            using var reader = new StreamReader(stream);

            string? snapshotPath = null;
            TimeSpan idleTimeout = TimeSpan.FromSeconds(60);
            try
            {
                while (true)
                {
                    string? nextLine;
                    using (var readCts = CancellationTokenSource.CreateLinkedTokenSource(ct))
                    {
                        try
                        {
                            readCts.CancelAfter(idleTimeout);
                            nextLine = await reader.ReadLineAsync(readCts.Token).ConfigureAwait(false);
                        }
                        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
                        {
                            throw new ApiException("STREAM_TIMEOUT", "模型下载后端长时间无响应，已自动终止。");
                        }
                    }

                    if (nextLine is null)
                        break;

                    ct.ThrowIfCancellationRequested();

                    if (string.IsNullOrWhiteSpace(nextLine))
                        continue;

                    if (!nextLine.StartsWith("data:", StringComparison.Ordinal))
                        continue;

                    var payload = StripSsePrefix(nextLine);
                    if (payload == "[DONE]")
                        break;

                    JsonDocument doc;
                    try
                    {
                        doc = JsonDocument.Parse(payload);
                    }
                    catch (JsonException ex)
                    {
                        DebugLog.Error($"download-model SSE JSON 解析失败: {ex.Message}\n  raw: {Truncate(payload, 300)}", "API", ex);
                        throw new ApiException("PARSE_ERROR", $"Invalid SSE frame: {ex.Message}", innerException: ex);
                    }

                    using var d = doc;
                    var root = d.RootElement;
                    if (!root.TryGetProperty("type", out var typeElem))
                        continue;

                    var eventType = typeElem.GetString() ?? "";
                    if (eventType == "progress")
                    {
                        try
                        {
                            var frame = JsonSerializer.Deserialize<DownloadProgressFrame>(root.GetRawText(), JsonOptions);
                            progress?.Report(frame ?? new DownloadProgressFrame());
                        }
                        catch (JsonException)
                        {
                            // 单帧解析失败不中断下载，等待终帧
                        }
                    }
                    else if (eventType == "done" && root.TryGetProperty("path", out var pathElem))
                    {
                        snapshotPath = pathElem.GetString();
                        break;
                    }
                    else if (eventType == "error" && root.TryGetProperty("message", out var msgElem))
                    {
                        DebugLog.Error($"download-model 报错: {msgElem.GetString()}", "API");
                        throw new ApiException("DOWNLOAD_ERROR", msgElem.GetString() ?? "模型下载失败");
                    }
                }
            }
            catch (Exception ex) when (ex is not (OperationCanceledException or ApiException))
            {
                DebugLog.Error($"download-model SSE 流读取中断: {ex.GetType().Name}: {ex.Message}", "API", ex);
                throw new ApiException("STREAM_INTERRUPTED", $"模型下载流中断: {ex.Message}", innerException: ex);
            }

            DebugLog.Info($"✓ POST v1/system/download-model done. snapshot={snapshotPath}", "API");
            return snapshotPath;
        }
    }

    /// <summary>订阅 job 进度 SSE（GET /v1/jobs/{id}/events，实时进度），返回最终 JobStatus。
    /// SSE 不可用/中断时自动回退到轮询（PollJobUntilDoneAsync），保证任务仍能收敛。</summary>
    public async Task<JobStatus> WatchJobUntilDoneAsync(string jobId, IProgress<JobStatus>? progress = null, CancellationToken ct = default)
    {
        var eventsUri = $"v1/jobs/{Uri.EscapeDataString(jobId)}/events";
        try
        {
            using var request = new HttpRequestMessage(HttpMethod.Get, eventsUri);
            AttachAuthHeader(request);

            HttpResponseMessage response;
            try
            {
                response = await _httpClient.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct)
                    .ConfigureAwait(false);
            }
            catch (HttpRequestException ex)
            {
                _logger.LogWarning(ex, "Backend connection failed for job events {JobId}", jobId);
                throw new BackendConnectionException("Backend is unreachable.", ex);
            }

            using (response)
            {
                if (!response.IsSuccessStatusCode)
                {
                    DebugLog.Warn($"✗ GET {eventsUri} -> {(int)response.StatusCode}，回退轮询", "API");
                    return await PollJobUntilDoneAsync(jobId, progress, ct: ct).ConfigureAwait(false);
                }

                using var stream = await response.Content.ReadAsStreamAsync().ConfigureAwait(false);
                using var reader = new StreamReader(stream);

                // 心跳约 15s 一帧；60s 无任何帧视为连接失效 → 回退轮询
                var idleTimeout = TimeSpan.FromSeconds(60);
                while (true)
                {
                    string? nextLine;
                    using (var readCts = CancellationTokenSource.CreateLinkedTokenSource(ct))
                    {
                        try
                        {
                            readCts.CancelAfter(idleTimeout);
                            nextLine = await reader.ReadLineAsync(readCts.Token).ConfigureAwait(false);
                        }
                        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
                        {
                            DebugLog.Warn($"job events SSE 空闲超时，回退轮询 {jobId}", "API");
                            return await PollJobUntilDoneAsync(jobId, progress, ct: ct).ConfigureAwait(false);
                        }
                    }

                    if (nextLine is null)
                    {
                        // 流意外结束：取一次终态，若未收敛则轮询兜底
                        var job = await GetJobAsync(jobId, ct).ConfigureAwait(false);
                        progress?.Report(job);
                        return IsTerminal(job.Status)
                            ? job
                            : await PollJobUntilDoneAsync(jobId, progress, ct: ct).ConfigureAwait(false);
                    }

                    ct.ThrowIfCancellationRequested();

                    if (string.IsNullOrWhiteSpace(nextLine))
                        continue;

                    if (!nextLine.StartsWith("data:", StringComparison.Ordinal))
                        continue;

                    var payload = StripSsePrefix(nextLine);
                    if (payload == "[DONE]")
                        break;

                    try
                    {
                        using var d = JsonDocument.Parse(payload);
                        var root = d.RootElement;
                        var type = root.TryGetProperty("type", out var t) ? t.GetString() ?? "" : "";
                        if (type == "progress")
                        {
                            var job = JsonSerializer.Deserialize<JobStatus>(root.GetRawText(), JsonOptions)
                                      ?? new JobStatus { JobId = jobId };
                            job = job with { JobId = jobId };
                            progress?.Report(job);
                        }
                        else if (type is "done" or "succeeded" or "completed")
                        {
                            var final = await GetJobAsync(jobId, ct).ConfigureAwait(false);
                            progress?.Report(final);
                            return final;
                        }
                        else if (type == "failed")
                        {
                            var msg = root.TryGetProperty("error", out var e) ? e.GetString() : null;
                            var final = await GetJobAsync(jobId, ct).ConfigureAwait(false);
                            if (string.IsNullOrEmpty(final.Error) && !string.IsNullOrEmpty(msg))
                            {
                                final = final with { Error = msg };
                            }
                            progress?.Report(final);
                            return final;
                        }
                        else if (type is "cancelled" or "canceled")
                        {
                            var final = await GetJobAsync(jobId, ct).ConfigureAwait(false);
                            progress?.Report(final);
                            return final;
                        }
                        // heartbeat / 其它帧：忽略，继续读
                    }
                    catch (JsonException ex)
                    {
                        DebugLog.Warn($"job events SSE JSON 解析失败（忽略）: {ex.Message}", "API");
                    }
                }

                // [DONE] 后取一次终态（可能已完成但浏览器/代理截断）
                var done = await GetJobAsync(jobId, ct).ConfigureAwait(false);
                progress?.Report(done);
                return IsTerminal(done.Status)
                    ? done
                    : await PollJobUntilDoneAsync(jobId, progress, ct: ct).ConfigureAwait(false);
            }
        }
        catch (ApiException)
        {
            throw;
        }
        catch (OperationCanceledException)
        {
            throw;
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"job events SSE 异常（{ex.GetType().Name}），回退轮询 {jobId}", "API");
            return await PollJobUntilDoneAsync(jobId, progress, ct: ct).ConfigureAwait(false);
        }
    }

    /// <summary>一键安装通用实现：POST 安装端点并逐行消费 SSE 事件流（log/done/error）。</summary>
    private async Task InstallViaSseAsync(
        string endpoint, string path, string label,
        Action<string> onLog, Action<bool> onDone, CancellationToken ct)
    {
        var reqBody = JsonSerializer.Serialize(new { path }, JsonOptions);
        DebugLog.Info($"→ POST {endpoint}  path={path}", "API");

        using var request = new HttpRequestMessage(HttpMethod.Post, endpoint)
        {
            Content = new StringContent(reqBody, System.Text.Encoding.UTF8, "application/json"),
        };
        AttachAuthHeader(request);

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
            _logger.LogWarning(ex, "Backend connection failed for {Endpoint}", endpoint);
            throw new BackendConnectionException("Backend is unreachable.", ex);
        }

        using (response)
        {
            if (!response.IsSuccessStatusCode)
            {
                var errBody = await response.Content.ReadAsStringAsync().ConfigureAwait(false);
                DebugLog.Error(
                    $"✗ POST {endpoint} -> {(int)response.StatusCode} ({response.ReasonPhrase})\n  resp: {Truncate(errBody, 800)}",
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
                while (true)
                {
                    // 与 ChatStreamAsync 一致：ReadLineAsync 阻塞期间不响应父 ct，
                    // 用联动令牌使取消能中断阻塞读（安装可长时间静默，不做空闲超时）
                    string? nextLine;
                    using (var readCts = CancellationTokenSource.CreateLinkedTokenSource(ct))
                    {
                        try
                        {
                            nextLine = await reader.ReadLineAsync(readCts.Token).ConfigureAwait(false);
                        }
                        catch (OperationCanceledException) when (!ct.IsCancellationRequested)
                        {
                            throw new ApiException("STREAM_TIMEOUT", $"{label} 后端长时间无响应，已自动终止。");
                        }
                    }

                    if (nextLine is null)
                        break;

                    line = nextLine;
                    ct.ThrowIfCancellationRequested();

                    if (string.IsNullOrWhiteSpace(line))
                        continue;

                    // 容错前缀解析（AUD-016）：标准为 "data: "，代理可能改写为无空格 "data:"，
                    // 重复拼接的多层前缀也一并剥掉
                    if (!line.StartsWith("data:", StringComparison.Ordinal))
                        continue;

                    var payload = StripSsePrefix(line);
                    if (payload == "[DONE]")
                        break;

                    JsonDocument doc;
                    try
                    {
                        doc = JsonDocument.Parse(payload);
                    }
                    catch (JsonException ex)
                    {
                        DebugLog.Error($"{label} SSE JSON 解析失败: {ex.Message}\n  raw: {Truncate(payload, 300)}", "API", ex);
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
                            DebugLog.Error($"{label} 报错: {msgElem.GetString()}", "API");
                            onLog($"[错误] {msgElem.GetString()}");
                            onDone(false);
                        }
                    }
                }
            }
            catch (Exception ex) when (ex is not (OperationCanceledException or ApiException))
            {
                DebugLog.Error($"{label} SSE 流读取中断: {ex.GetType().Name}: {ex.Message}", "API", ex);
                throw new ApiException("STREAM_INTERRUPTED", $"{label} stream interrupted: {ex.Message}", innerException: ex);
            }

            sw.Stop();
            DebugLog.Info(
                $"✓ POST {endpoint} completed in {sw.ElapsedMilliseconds}ms "
                + $"(logLines={logLines} done={doneReceived})",
                "API");

            if (!doneReceived)
            {
                DebugLog.Warn($"{label} SSE 流结束但未收到 done 终帧", "API");
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

    public async Task<GraphStats> GetGraphStatsAsync(string? collection = null, CancellationToken ct = default)
    {
        var uri = "v1/graph/stats";
        if (!string.IsNullOrWhiteSpace(collection))
        {
            uri += $"?collection={Uri.EscapeDataString(collection)}";
        }
        return await SendAsync<GraphStats>(HttpMethod.Get, uri, null, ct);
    }

    public async Task<List<GraphNode>> GetGraphEntitiesAsync(string? collection = null, int limit = 200, CancellationToken ct = default)
    {
        var uri = $"v1/graph/entities?limit={limit}";
        if (!string.IsNullOrWhiteSpace(collection))
        {
            uri += $"&collection={Uri.EscapeDataString(collection)}";
        }
        return await SendAsync<List<GraphNode>>(HttpMethod.Get, uri, null, ct);
    }

    public async Task<DependenciesStatus> GetDependenciesAsync(CancellationToken ct = default)
        => await SendAsync<DependenciesStatus>(HttpMethod.Get, "v1/system/dependencies", null, ct);

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

    public async Task<CreativeExportResponse> ExportCreativeArtifactAsync(CreativeExportRequest req, CancellationToken ct = default)
    {
        return await SendAsync<CreativeExportResponse>(HttpMethod.Post, "v1/creative/export", req, ct);
    }

    public async Task<PptInspectionReportDto> InspectCreativeArtifactAsync(string content, CancellationToken ct = default)
    {
        var body = new { content };
        return await SendAsync<PptInspectionReportDto>(HttpMethod.Post, "v1/creative/inspect", body, ct);
    }

    public Task<ArtifactVersionResponse> SaveArtifactVersionAsync(string artifactId, CreativeExportRequest draft, CancellationToken ct = default)
        => SendAsync<ArtifactVersionResponse>(HttpMethod.Post, $"v1/creative/artifacts/{Uri.EscapeDataString(artifactId)}/versions", draft, ct);

    public Task<List<ArtifactVersionSummary>> ListArtifactVersionsAsync(string artifactId, CancellationToken ct = default)
        => SendAsync<List<ArtifactVersionSummary>>(HttpMethod.Get, $"v1/creative/artifacts/{Uri.EscapeDataString(artifactId)}/versions", null, ct);

    public Task<ArtifactVersionResponse> GetArtifactVersionAsync(string artifactId, string versionId, CancellationToken ct = default)
        => SendAsync<ArtifactVersionResponse>(HttpMethod.Get, $"v1/creative/artifacts/{Uri.EscapeDataString(artifactId)}/versions/{Uri.EscapeDataString(versionId)}", null, ct);

    public Task<LibraryStatus> GetLibraryStatusAsync(CancellationToken ct = default)
        => SendAsync<LibraryStatus>(HttpMethod.Get, "v1/library/status", null, ct);

    public Task<TrashListResponse> ListTrashAsync(int limit = 100, CancellationToken ct = default)
        => SendAsync<TrashListResponse>(HttpMethod.Get, $"v1/trash?limit={limit}", null, ct);

    public Task<TrashRestoreResponse> RestoreTrashedDocumentAsync(string documentId, CancellationToken ct = default)
        => SendAsync<TrashRestoreResponse>(HttpMethod.Post, $"v1/trash/{Uri.EscapeDataString(documentId)}/restore", null, ct);

    public Task<TrashPurgeResponse> PurgeTrashAsync(int olderThanDays = 30, CancellationToken ct = default)
        => SendAsync<TrashPurgeResponse>(HttpMethod.Post, "v1/trash/purge", new TrashPurgeRequest { OlderThanDays = olderThanDays }, ct);

    public Task<ProfileSwitchResult> SetUsageProfileAsync(string profile, CancellationToken ct = default)
        => SendAsync<ProfileSwitchResult>(HttpMethod.Post, "v1/profile", new { profile }, ct);

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
                    AttachAuthHeader(req);
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

                        // 容错前缀解析（AUD-016）：标准为 "data: "，代理可能改写为无空格 "data:"
                        if (line.StartsWith("data:", StringComparison.Ordinal))
                        {
                            var json = StripSsePrefix(line);
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

    /// <summary>请求并返回原始 JSON 字符串（宽松解析用）。</summary>
    private async Task<string> SendRawAsync(HttpMethod method, string uri, object? payload, CancellationToken ct)
    {
        using var request = new HttpRequestMessage(method, uri);
        AttachAuthHeader(request);
        if (payload is not null)
        {
            request.Content = System.Net.Http.Json.JsonContent.Create(payload, options: JsonOptions);
        }
        using var response = await _httpClient.SendAsync(request, ct).ConfigureAwait(false);
        var body = await response.Content.ReadAsStringAsync(ct).ConfigureAwait(false);
        if (!response.IsSuccessStatusCode)
        {
            throw new ApiException("HTTP_" + (int)response.StatusCode, Truncate(body, 400));
        }
        return body;
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
        AttachAuthHeader(request);
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

            // 成功路径：流式反序列化 + 日志只留截断预览，
            // 避免整包 ReadAsStringAsync（UTF-16 全量副本）+ 再反序列化的 2~3 份大响应体副本。
            T result;
            using (var stream = await response.Content.ReadAsStreamAsync().ConfigureAwait(false))
            {
                string preview = string.Empty;
                if (stream.CanSeek)
                {
                    preview = await ReadBodyPreviewAsync(stream).ConfigureAwait(false);
                    stream.Position = 0;
                }

                DebugLog.Info(
                    $"✓ {method} {uri} -> {status} in {sw.ElapsedMilliseconds}ms"
                    + (string.IsNullOrWhiteSpace(preview) ? "" : "\n  resp: " + Truncate(preview, 800)),
                    "API");

                try
                {
                    result = await JsonSerializer.DeserializeAsync<T>(stream, JsonOptions, ct).ConfigureAwait(false)
                        ?? throw new ApiException("PARSE_ERROR", "Response body was empty.");
                }
                catch (JsonException ex)
                {
                    DebugLog.Error($"JSON parse failed for {method} {uri}: {ex.Message}\n  raw: {Truncate(preview, 800)}", "API", ex);
                    throw new ApiException("PARSE_ERROR", "Failed to parse response body.", innerException: ex);
                }
            }
            return result;
        }
    }

    /// <summary>只读响应体开头 ≤2KB 原始字节用于日志预览，不为打日志整读大响应。</summary>
    private static async Task<string> ReadBodyPreviewAsync(Stream stream)
    {
        var buf = new byte[2048];
        var n = await stream.ReadAsync(buf.AsMemory(0, buf.Length)).ConfigureAwait(false);
        return n == 0 ? string.Empty : Encoding.UTF8.GetString(buf, 0, n);
    }

    /// <summary>剥掉 SSE "data:" 前缀并去前导空白。
    /// 容忍代理/后端重复拼接的多层前缀——曾出现 "data: data: {...}"，
    /// 端上只剥一次后 payload 仍以 'd' 开头，JsonDocument.Parse 直接抛 PARSE_ERROR，
    /// 导致整轮对话判定失败（HTML 气泡随之空白）。</summary>
    private static string StripSsePrefix(string line)
    {
        var s = line.TrimStart();
        while (s.StartsWith("data:", StringComparison.Ordinal))
        {
            // 每剥一层都要再去一次空白，否则 "data: data: {...}" 剥一次后
            // 残留的前导空格会让下一轮 StartsWith 判定失败，只剥一层就退出
            s = s["data:".Length..].TrimStart();
        }
        return s;
    }

    private static string Truncate(string s, int max)
        => s.Length > max ? s[..max] + "…(truncated)" : s;

    /// <summary>日志脱敏：掩盖请求体中的 *api_key 字段值（如 /v1/config 推送的 llm_api_key），避免明文密钥落入日志文件。</summary>
    private static string RedactSecrets(string body)
        // 覆盖 snake_case (api_key) 与 camelCase (apiKey, githubToken) 两种序列化风格，
        // 避免 API Key / GitHub Token 明文泄露到调试日志。
        // 正则: "(api_key|apiKey|githubToken)"\s*:\s*"[^"]*"  匹配 JSON 中的敏感字段
        // 替换: "$1":"***"  保留字段名，值替换为 ***
        => Regex.Replace(body,
            "\"(api_key|apiKey|githubToken|github_token|access_token|authorization|token|llm_api_key)\"\\s*:\\s*\"(?:\\\\\"|[^\"])*\"",
            "\"$1\":\"***\"",
            RegexOptions.IgnoreCase);

    private static async Task<ApiException> CreateApiExceptionAsync(HttpResponseMessage response)
    {
        var body = await response.Content.ReadAsStringAsync().ConfigureAwait(false);

        if (!string.IsNullOrWhiteSpace(body))
        {
            try
            {
                using var doc = JsonDocument.Parse(body);
                var root = doc.RootElement;

                // 非对象根（数组/字符串/数字）无从解析字段：TryGetProperty 对非对象会抛
                // InvalidOperationException，必须先按 ValueKind 守卫，避免把错误变成未处理异常
                if (root.ValueKind == JsonValueKind.Object)
                {
                    // 优先解析 FastAPI 异常统一格式：{"detail": {"code": "...", "message": "...", ...}}
                    if (root.TryGetProperty("detail", out var detailElem))
                    {
                        if (detailElem.ValueKind == JsonValueKind.Object)
                        {
                            var code = detailElem.TryGetProperty("code", out var c) ? c.GetString() : null;
                            var msg = detailElem.TryGetProperty("message", out var m) ? m.GetString() : null;
                            var subDetail = detailElem.TryGetProperty("detail", out var d) ? d.ToString() : null;
                            if (!string.IsNullOrWhiteSpace(msg))
                            {
                                return new ApiException(code ?? response.StatusCode.ToString().ToUpperInvariant(), msg, subDetail);
                            }
                        }
                        else if (detailElem.ValueKind == JsonValueKind.String)
                        {
                            var msg = detailElem.GetString();
                            if (!string.IsNullOrWhiteSpace(msg))
                            {
                                return new ApiException(response.StatusCode.ToString().ToUpperInvariant(), msg);
                            }
                        }
                        else if (detailElem.ValueKind == JsonValueKind.Array)
                        {
                            // 兼容 FastAPI 422 参数验证失败数组（逐项过滤非对象，避免 TryGetProperty 抛异常）
                            var errors = detailElem.EnumerateArray()
                                .Where(e => e.ValueKind == JsonValueKind.Object)
                                .Select(e => e.TryGetProperty("msg", out var m) ? m.GetString() : null)
                                .Where(m => !string.IsNullOrWhiteSpace(m));
                            var combined = string.Join("; ", errors);
                            if (!string.IsNullOrWhiteSpace(combined))
                            {
                                return new ApiException("VALIDATION_ERROR", combined, body);
                            }
                        }
                    }

                    // 兼容顶层直接包含 code / message 的错误体
                    if (root.TryGetProperty("message", out var msgElem) && msgElem.ValueKind == JsonValueKind.String)
                    {
                        var msg = msgElem.GetString();
                        var code = root.TryGetProperty("code", out var c) ? c.GetString() : null;
                        var detail = root.TryGetProperty("detail", out var d) ? d.ToString() : null;
                        if (!string.IsNullOrWhiteSpace(msg))
                        {
                            return new ApiException(code ?? response.StatusCode.ToString().ToUpperInvariant(), msg, detail);
                        }
                    }

                    // 兼容 {"error": "..."} 结构
                    if (root.TryGetProperty("error", out var errElem) && errElem.ValueKind == JsonValueKind.String)
                    {
                        var err = errElem.GetString();
                        if (!string.IsNullOrWhiteSpace(err))
                        {
                            return new ApiException(response.StatusCode.ToString().ToUpperInvariant(), err);
                        }
                    }
                }
            }
            catch (JsonException)
            {
                // body 非 JSON 时走下方通用状态码异常
            }
            catch (InvalidOperationException)
            {
                // 兜底：JSON 结构异常（对象/数组嵌套等）不阻断，走通用状态码异常
            }
        }

        var fallbackCode = response.StatusCode.ToString().ToUpperInvariant();
        return new ApiException(fallbackCode, $"Request failed with status code {(int)response.StatusCode} ({response.ReasonPhrase}).", body);
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
