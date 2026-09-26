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

    /// <summary>带 onStatus/onThinking 的流式替身：测试「思考过程」步骤与推理链收集用（事件会透传）。</summary>
    public Func<ChatRequest, Action<string>, Action<ChatStreamResult>, Action<string>?, Action<string>?, CancellationToken, Task<ChatStreamResult>>? OnChatStreamWithStatus { get; set; }

    // ── Documents ──
    public Func<string?, int, int, string?, string, string?, CancellationToken, Task<DocumentListResponse>>? OnListDocuments { get; set; }
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

    // ── AI 整理（curate）──
    public Func<CurateRequest, CancellationToken, Task<JobStatus>>? OnCurate { get; set; }
    public Func<string, CancellationToken, Task<JobStatus>>? OnGetJob { get; set; }

    // ── Chunk Annotation ──
    public Func<int, string, CancellationToken, Task>? OnUpsertChunkAnnotation { get; set; }

    // ── Health / Config ──
    public Func<CancellationToken, Task<HealthStatus>>? OnGetHealth { get; set; }
    public Func<CancellationToken, Task<BackendConfig>>? OnGetConfig { get; set; }
    public Func<BackendConfigUpdate, CancellationToken, Task<BackendConfig>>? OnUpdateConfig { get; set; }

    // ── LLM 连接测试 / 模型列表 ──
    public Func<LlmTestRequest, CancellationToken, Task<LlmTestResult>>? OnLlmTest { get; set; }
    public Func<LlmModelsRequest, CancellationToken, Task<LlmModelsResult>>? OnLlmModels { get; set; }

    // ── 会话历史 ──
    public Func<int, string?, CancellationToken, Task<ChatSessionListResponse>>? OnListChats { get; set; }
    public Func<string, CancellationToken, Task<ChatSessionDetail>>? OnGetChat { get; set; }
    public Func<string, CancellationToken, Task>? OnDeleteChat { get; set; }

    public void UpdateBaseAddress(string baseUrl) { /* no-op */ }

    public Task InstallOcrAsync(string path, Action<string> onLog, Action<bool> onDone, CancellationToken ct = default)
        => OnInstallOcr?.Invoke(path, onLog, onDone, ct) ?? Task.CompletedTask;

    public Func<string, Action<string>, Action<bool>, CancellationToken, Task>? OnInstallOcr { get; set; }

    public Task<HealthStatus> GetHealthAsync(CancellationToken ct = default)
        => OnGetHealth?.Invoke(ct) ?? Task.FromResult(new HealthStatus { Status = "ok" });

    public Task<BackendConfig> GetConfigAsync(CancellationToken ct = default)
        => OnGetConfig?.Invoke(ct) ?? throw new NotImplementedException();

    public Task<BackendConfig> UpdateConfigAsync(BackendConfigUpdate req, CancellationToken ct = default)
        => OnUpdateConfig?.Invoke(req, ct) ?? throw new NotImplementedException();

    // ── 本地 AI 环境感知：测试默认返回 null（VM 判空静默跳过，不触发探测 UI）──
    public Task<LocalAiEnvironment> GetLocalAiEnvironmentAsync(CancellationToken ct = default)
        => Task.FromResult<LocalAiEnvironment>(null!);

    // ── 一键拉取：测试注入点（实现在文件尾部「库状态」区，OnStartOllamaPull 可自定义行为）──
    public Func<string, OllamaPullStatus>? OnStartOllamaPull;

    public Task<LlmTestResult> LlmTestAsync(LlmTestRequest req, CancellationToken ct = default)
        => OnLlmTest?.Invoke(req, ct) ?? throw new NotImplementedException();

    public Task<LlmModelsResult> LlmModelsAsync(LlmModelsRequest req, CancellationToken ct = default)
        => OnLlmModels?.Invoke(req, ct) ?? throw new NotImplementedException();

    public Task<ChatSessionListResponse> ListChatsAsync(int limit = 50, string? q = null, CancellationToken ct = default)
        => OnListChats?.Invoke(limit, q, ct) ?? Task.FromResult(new ChatSessionListResponse());

    public Task<ChatSessionDetail> GetChatAsync(string chatId, CancellationToken ct = default)
        => OnGetChat?.Invoke(chatId, ct) ?? Task.FromResult(new ChatSessionDetail { ChatId = chatId });

    public Task DeleteChatAsync(string chatId, CancellationToken ct = default)
        => OnDeleteChat?.Invoke(chatId, ct) ?? Task.CompletedTask;

    public Task<IngestResponse> IngestAsync(IngestRequest req, CancellationToken ct = default)
        => OnIngest?.Invoke(req, ct) ?? throw new NotImplementedException();

    public Func<IngestTextRequest, CancellationToken, Task<IngestResponse>>? OnIngestText { get; set; }

    public Task<IngestResponse> IngestTextAsync(IngestTextRequest req, CancellationToken ct = default)
        => OnIngestText is not null
            ? OnIngestText(req, ct)
            : Task.FromResult(new IngestResponse { TotalDocuments = 1, TotalChunks = 100 });

    public Task<JobStatus> IngestJobAsync(IngestRequest req, CancellationToken ct = default)
        => OnIngestJob?.Invoke(req, ct) ?? throw new NotImplementedException();

    public Task<SearchResponse> SearchAsync(SearchRequest req, CancellationToken ct = default)
        => OnSearch?.Invoke(req, ct) ?? throw new NotImplementedException();

    public Task<ChatResponse> ChatAsync(ChatRequest req, CancellationToken ct = default)
        => OnChat?.Invoke(req, ct) ?? throw new NotImplementedException();

    public async Task<ChatStreamResult> ChatStreamAsync(ChatRequest req, Action<string> onToken, Action<ChatStreamResult> onDone, Action<string>? onStatus = null, Action<string>? onThinking = null, Action? onRestart = null, Action<string, string>? onAgentEvent = null, Action<string, string>? onPermissionRequest = null, CancellationToken ct = default)
    {
        if (OnChatStreamWithStatus is not null)
        {
            return await OnChatStreamWithStatus(req, onToken, onDone, onStatus, onThinking, ct);
        }
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
            Evidence = resp.Evidence ?? EvidenceSummary.FromSources(resp.Sources),
        };
        onDone?.Invoke(result);
        return result;
    }

    public Task<DocumentListResponse> ListDocumentsAsync(string? collection = null, int page = 1, int pageSize = 20, string? format = null, string sort = "created_at_desc", string? q = null, CancellationToken ct = default)
        => OnListDocuments?.Invoke(collection, page, pageSize, format, sort, q, ct) ?? throw new NotImplementedException();

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

    public Task<JobStatus> CurateAsync(CurateRequest req, CancellationToken ct = default)
        => OnCurate?.Invoke(req, ct) ?? throw new NotImplementedException();
    public Task<bool> ResolveAgentPermissionAsync(string requestId, bool allow, CancellationToken ct = default)
        => Task.FromResult(true);

    public Task<CurateRunsResponse> ListCurateRunsAsync(int days = 7, int limit = 50, CancellationToken ct = default)
        => Task.FromResult(new CurateRunsResponse());

    public Task<LibraryEvalResult> EvalLibraryAsync(string? collection = null, int sample = 30, CancellationToken ct = default)
        => Task.FromResult(new LibraryEvalResult { Sample = sample, Summary = "ok" });

    public Task<RetrievalRecommendedResult> GetRetrievalRecommendedAsync(CancellationToken ct = default)
        => Task.FromResult(new RetrievalRecommendedResult { Description = "builtin" });

    public Task<RetrievalRecommendedResult> ApplyRetrievalRecommendedAsync(CancellationToken ct = default)
        => Task.FromResult(new RetrievalRecommendedResult { Applied = true, Description = "applied" });

    public Task<OllamaPullStatus> StartOllamaPullAsync(string model, CancellationToken ct = default)
        => Task.FromResult(OnStartOllamaPull?.Invoke(model) ?? new OllamaPullStatus { Model = model, State = "done" });

    public Task<OllamaPullStatus> GetOllamaPullStatusAsync(CancellationToken ct = default)
        => Task.FromResult(new OllamaPullStatus { State = "idle" });


    public Func<string, CancellationToken, Task<JobStatus>>? OnCancelJob { get; set; }

    public Task<JobStatus> GetJobAsync(string jobId, CancellationToken ct = default)
        => OnGetJob?.Invoke(jobId, ct) ?? throw new NotImplementedException();

    public Task<JobStatus> CancelJobAsync(string jobId, CancellationToken ct = default)
        => OnCancelJob?.Invoke(jobId, ct) ?? Task.FromResult(new JobStatus { JobId = jobId, Status = "cancelled" });

    public Task UpsertChunkAnnotationAsync(int chunkId, string text, CancellationToken ct = default)
        => OnUpsertChunkAnnotation?.Invoke(chunkId, text, ct) ?? Task.CompletedTask;

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

    // ── Job SSE 实时进度：Fake 中与轮询同义（OnGetJob 驱动），VM 侧行为一致 ──
    public Func<string, IProgress<JobStatus>?, CancellationToken, Task<JobStatus>>? OnWatchJobUntilDone { get; set; }

    public async Task<JobStatus> WatchJobUntilDoneAsync(string jobId, IProgress<JobStatus>? progress = null, CancellationToken ct = default)
    {
        if (OnWatchJobUntilDone is not null)
        {
            return await OnWatchJobUntilDone(jobId, progress, ct);
        }
        // 默认与轮询路径一致（OnGetJob 驱动），保证既有测试不感知切换
        return await PollJobUntilDoneAsync(jobId, progress, ct: ct);
    }

    private static bool IsTerminal(string? status)
        => status is "completed" or "done" or "failed" or "succeeded" or "canceled" or "cancelled";

    // ── 系统环境依赖 / 模型下载 ──
    public Func<CancellationToken, Task<DependenciesStatus>>? OnGetDependencies { get; set; }
    public Func<string?, IProgress<DownloadProgressFrame>?, CancellationToken, Task<string?>>? OnDownloadModel { get; set; }

    public Task<DependenciesStatus> GetDependenciesAsync(CancellationToken ct = default)
        => OnGetDependencies is not null
            ? OnGetDependencies(ct)
            : Task.FromResult(new DependenciesStatus());

    public Task<string?> DownloadModelAsync(string? modelName = null, IProgress<DownloadProgressFrame>? progress = null, CancellationToken ct = default)
        => OnDownloadModel is not null
            ? OnDownloadModel(modelName, progress, ct)
            : Task.FromResult<string?>(null);

    // ── GPU 加速 ──
    public Func<CancellationToken, Task<GpuDiagnosis>>? OnGetGpuDiagnosis { get; set; }
    public Func<string, Action<string>, Action<bool>, CancellationToken, Task>? OnInstallGpu { get; set; }

    public Task<GpuDiagnosis> GetGpuDiagnosisAsync(CancellationToken ct = default)
        => OnGetGpuDiagnosis is not null
            ? OnGetGpuDiagnosis(ct)
            : Task.FromResult(new GpuDiagnosis { RecommendedPath = "cpu" });

    public Task InstallGpuAsync(string path, Action<string> onLog, Action<bool> onDone, CancellationToken ct = default)
    {
        if (OnInstallGpu is not null)
            return OnInstallGpu(path, onLog, onDone, ct);
        onLog($"[模拟] 安装 {path}");
        onDone(true);
        return Task.CompletedTask;
    }

    // ── 知识图谱 ──
    public Func<string?, int, CancellationToken, Task<GraphResponse>>? OnGetGraph { get; set; }

    public Func<string?, CancellationToken, Task<GraphStats>>? OnGetGraphStats { get; set; }
    public Func<string?, int, CancellationToken, Task<List<GraphNode>>>? OnGetGraphEntities { get; set; }

    // 默认返回 null：VM 走「统计不可用→回退可视化计数」路径，与旧测试行为一致
    public Task<GraphStats> GetGraphStatsAsync(string? collection = null, CancellationToken ct = default)
        => OnGetGraphStats is not null
            ? OnGetGraphStats(collection, ct)
            : Task.FromResult<GraphStats>(null!);

    public Task<List<GraphNode>> GetGraphEntitiesAsync(string? collection = null, int limit = 200, CancellationToken ct = default)
        => OnGetGraphEntities is not null
            ? OnGetGraphEntities(collection, limit, ct)
            : Task.FromResult(new List<GraphNode>());
    public Func<string, int, CancellationToken, Task<List<GraphEntityRelation>>>? OnGetEntityRelations { get; set; }

    public Task<GraphResponse> GetGraphAsync(string? collection = null, int limit = 200, CancellationToken ct = default)
        => OnGetGraph is not null
            ? OnGetGraph(collection, limit, ct)
            : Task.FromResult(new GraphResponse(new List<GraphNode>(), new List<GraphEdge>(), 0));

    public Task<List<GraphEntityRelation>> GetEntityRelationsAsync(string entityId, int limit = 50, CancellationToken ct = default)
        => OnGetEntityRelations is not null
            ? OnGetEntityRelations(entityId, limit, ct)
            : Task.FromResult(new List<GraphEntityRelation>());

    public Func<string, int, CancellationToken, Task<GraphEntityDetailResponse>>? OnGetEntityDetail { get; set; }

    public async Task<GraphEntityDetailResponse> GetEntityDetailAsync(string entityId, int limit = 8, CancellationToken ct = default)
    {
        if (OnGetEntityDetail is not null)
        {
            return await OnGetEntityDetail(entityId, limit, ct);
        }

        var relations = OnGetEntityRelations is not null
            ? await OnGetEntityRelations(entityId, limit, ct)
            : new List<GraphEntityRelation>();

        return new GraphEntityDetailResponse
        {
            Entity = new GraphNode(entityId, entityId, "concept", "concept", 1, "default"),
            Relations = relations,
            Snippets = new List<GraphContextSnippet>(),
            SourceDocuments = new List<GraphSourceDocument>()
        };
    }

    public Func<string?, int, CancellationToken, Task<GraphExtractResult>>? OnExtractGraph { get; set; }

    public Task<GraphExtractResult> ExtractGraphAsync(string? collection = null, int topK = 20, CancellationToken ct = default)
        => OnExtractGraph is not null
            ? OnExtractGraph(collection, topK, ct)
            : Task.FromResult(new GraphExtractResult(true, 5, 0, new List<string>(), 100));

    public Func<EntityDistillRequest, CancellationToken, Task<EntityDistillResponse>>? OnDistillEntityKnowledge { get; set; }

    public Task<EntityDistillResponse> DistillEntityKnowledgeAsync(EntityDistillRequest req, CancellationToken ct = default)
        => OnDistillEntityKnowledge is not null
            ? OnDistillEntityKnowledge(req, ct)
            : Task.FromResult(new EntityDistillResponse
            {
                EntityId = req.EntityId,
                EntityName = req.EntityName,
                MarkdownCard = $"# 📚【知识档案】{req.EntityName}\n## 📌 核心定义与定位\n自动生成的精炼卡片",
                SuggestedTags = new List<string> { req.EntityType, req.EntityName },
                Model = "fake-llm"
            });

    // ── 系统体检 / 示例文档 ──
    public Func<bool, CancellationToken, Task<DoctorReportResult>>? OnGetDoctorReport { get; set; }
    public Func<string, CancellationToken, Task<SampleIngestResult>>? OnIngestSample { get; set; }

    public Task<DoctorReportResult> GetDoctorReportAsync(bool network = true, CancellationToken ct = default)
        => OnGetDoctorReport?.Invoke(network, ct) ?? Task.FromResult(new DoctorReportResult
        {
            OverallStatus = "ok",
            Score = 100,
            Summary = "系统就绪 (Fake)",
            Checks = new List<DiagnosticCheckItem>
            {
                new() { Name = "Python 运行环境", Category = "python", Status = "ok", Message = "Python 3.11.0" },
                new() { Name = "本地存储读写", Category = "storage", Status = "ok", Message = "正常" }
            }
        });

    public Task<SampleIngestResult> IngestSampleAsync(string collection = "default", CancellationToken ct = default)
        => OnIngestSample?.Invoke(collection, ct) ?? Task.FromResult(new SampleIngestResult
        {
            Ok = true,
            Status = "ingested",
            Title = "DocMind 快速上手与全景操作指南",
            Collection = collection,
            ChunkCount = 1,
            ElapsedMs = 50
        });

    public Func<CreativeExportRequest, CancellationToken, Task<CreativeExportResponse>>? OnExportCreativeArtifact { get; set; }
    public Func<string, CancellationToken, Task<PptInspectionReportDto>>? OnInspectCreativeArtifact { get; set; }

    public Task<CreativeExportResponse> ExportCreativeArtifactAsync(CreativeExportRequest req, CancellationToken ct = default)
        => OnExportCreativeArtifact?.Invoke(req, ct) ?? Task.FromResult(new CreativeExportResponse
        {
            Ok = true,
            Format = req.Format ?? "docx",
            FilePath = $"C:\\fake\\exported_artifact.{req.Format ?? "docx"}",
            FileName = $"exported_artifact.{req.Format ?? "docx"}",
            FileSizeBytes = 1024,
        });

    public Task<PptInspectionReportDto> InspectCreativeArtifactAsync(string content, CancellationToken ct = default)
        => OnInspectCreativeArtifact?.Invoke(content, ct) ?? Task.FromResult(new PptInspectionReportDto
        {
            Score = 95,
            Grade = "S",
            Summary = "卓越演示文稿 (Fake)",
            SlideCount = 5,
            NotesCoveragePct = 100,
            ArchetypeDiversity = 4,
        });

    public Func<string, CreativeExportRequest, CancellationToken, Task<ArtifactVersionResponse>>? OnSaveArtifactVersion { get; set; }
    public Func<string, CancellationToken, Task<List<ArtifactVersionSummary>>>? OnListArtifactVersions { get; set; }
    public Func<string, string, CancellationToken, Task<ArtifactVersionResponse>>? OnGetArtifactVersion { get; set; }

    public Task<ArtifactVersionResponse> SaveArtifactVersionAsync(string artifactId, CreativeExportRequest draft, CancellationToken ct = default)
        => OnSaveArtifactVersion?.Invoke(artifactId, draft, ct) ?? Task.FromResult(new ArtifactVersionResponse());

    public Task<List<ArtifactVersionSummary>> ListArtifactVersionsAsync(string artifactId, CancellationToken ct = default)
        => OnListArtifactVersions?.Invoke(artifactId, ct) ?? Task.FromResult(new List<ArtifactVersionSummary>());

    public Task<ArtifactVersionResponse> GetArtifactVersionAsync(string artifactId, string versionId, CancellationToken ct = default)
        => OnGetArtifactVersion?.Invoke(artifactId, versionId, ct) ?? Task.FromResult(new ArtifactVersionResponse());

    // ── 事件流订阅 ──
    public Func<Action<EventMessage>, CancellationToken, IDisposable>? OnSubscribeEvents { get; set; }

    public IDisposable SubscribeEvents(Action<EventMessage> onEvent, CancellationToken ct = default)
        => OnSubscribeEvents?.Invoke(onEvent, ct) ?? new DummyDisposable();

    // ── 库状态 / 使用档案 ──
    public LibraryStatus LibraryStatus { get; set; } = new() { Status = "ok", Summary = "最新可用" };

    public Task<LibraryStatus> GetLibraryStatusAsync(CancellationToken ct = default)
        => Task.FromResult(LibraryStatus);

    // ── Curate runs / 检索自评估 / 推荐参数 ──

    public string? LastProfile { get; private set; }

    public Task<ProfileSwitchResult> SetUsageProfileAsync(string profile, CancellationToken ct = default)
    {
        LastProfile = profile;
        return Task.FromResult(new ProfileSwitchResult
        {
            Profile = profile,
            Preset = new ProfilePreset { Label = profile, Description = "test" },
            Applied = new ProfileApplied { RagTopK = 4, RagMinScore = 0.35, RagMode = "strict", QueryExpansion = "off" },
        });
    }

    private sealed class DummyDisposable : IDisposable
    {
        public void Dispose() { }
    }

    // ── 回收站（基础能力）──
    public Func<int, CancellationToken, Task<TrashListResponse>>? OnListTrash { get; set; }
    public Task<TrashListResponse> ListTrashAsync(int limit = 100, CancellationToken ct = default)
        => OnListTrash?.Invoke(limit, ct) ?? Task.FromResult(new TrashListResponse());

    public Func<string, CancellationToken, Task<TrashRestoreResponse>>? OnRestoreTrash { get; set; }
    public Task<TrashRestoreResponse> RestoreTrashedDocumentAsync(string documentId, CancellationToken ct = default)
        => OnRestoreTrash?.Invoke(documentId, ct)
           ?? Task.FromResult(new TrashRestoreResponse { Id = documentId, Status = "restored", Note = "恢复后需重新摄入或 reindex 才能被检索" });

    public Func<int, CancellationToken, Task<TrashPurgeResponse>>? OnPurgeTrash { get; set; }
    public Task<TrashPurgeResponse> PurgeTrashAsync(int olderThanDays = 30, CancellationToken ct = default)
        => OnPurgeTrash?.Invoke(olderThanDays, ct) ?? Task.FromResult(new TrashPurgeResponse { Purged = 0 });

    public Task<BackupResponse> CreateBackupAsync(string? outputPath = null, CancellationToken ct = default)
        => Task.FromResult(new BackupResponse(true, outputPath ?? "C:\\fake\\docmind.docmind.zip"));

    public Task<BackupResponse> RestoreBackupAsync(string backupPath, CancellationToken ct = default)
        => Task.FromResult(new BackupResponse(true, "C:\\fake\\docmind.db", PreviousBackup: "C:\\fake\\docmind.pre-restore.db"));

    public Task<BackupResponse> CreateDiagnosticBundleAsync(string? outputPath = null, bool includeLogs = false, CancellationToken ct = default)
        => Task.FromResult(new BackupResponse(true, outputPath ?? "C:\\fake\\diagnostics.zip"));
}
