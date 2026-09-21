using DocMind.Models;

namespace DocMind.Services;

public interface IDoc2kbApiService
{
    /// <summary>更新后端 BaseAddress（端口被占用顺延时由 BackendProcessService 触发）。</summary>
    void UpdateBaseAddress(string baseUrl);

    Task<HealthStatus> GetHealthAsync(CancellationToken ct = default);
    Task<BackendConfig> GetConfigAsync(CancellationToken ct = default);
    Task<BackendConfig> UpdateConfigAsync(BackendConfigUpdate req, CancellationToken ct = default);
    /// <summary>测试 LLM 连接（POST /v1/llm/test）：验证传入的 provider/key/baseUrl/model 是否可用，不落盘。</summary>
    Task<LlmTestResult> LlmTestAsync(LlmTestRequest req, CancellationToken ct = default);
    /// <summary>列出提供商可用模型（POST /v1/llm/models）：Ollama 本地模型 / 云端 /models 接口，不落盘。</summary>
    Task<LlmModelsResult> LlmModelsAsync(LlmModelsRequest req, CancellationToken ct = default);
    /// <summary>历史会话列表（GET /v1/chats，按更新时间倒序）。</summary>
    Task<ChatSessionListResponse> ListChatsAsync(int limit = 50, CancellationToken ct = default);
    /// <summary>会话全部消息（GET /v1/chats/{id}，回看/续聊）。</summary>
    Task<ChatSessionDetail> GetChatAsync(string chatId, CancellationToken ct = default);
    /// <summary>删除会话（DELETE /v1/chats/{id}，内存 + SQLite）。</summary>
    Task DeleteChatAsync(string chatId, CancellationToken ct = default);
    Task<IngestResponse> IngestAsync(IngestRequest req, CancellationToken ct = default);
    /// <summary>纯文本直入（沉淀经验/笔记/知识卡片，POST /v1/ingest/text）。</summary>
    Task<IngestResponse> IngestTextAsync(IngestTextRequest req, CancellationToken ct = default);
    /// <summary>异步摄入：提交任务并返回 JobStatus，供轮询真实进度（POST /v1/ingest/job）。</summary>
    Task<JobStatus> IngestJobAsync(IngestRequest req, CancellationToken ct = default);
    Task<SearchResponse> SearchAsync(SearchRequest req, CancellationToken ct = default);
    Task<ChatResponse> ChatAsync(ChatRequest req, CancellationToken ct = default);
    /// <summary>流式对话：消费 SSE 逐 token 输出。onToken 每收到一个 token 触发，onDone 在终帧触发，返回终帧元数据。
    /// onStatus 阶段状态；onThinking 推理链增量（DeepSeek-R1/Qwen3 等模型的 reasoning_content）；
    /// onRestart 后端重启生成（上下文溢出精简后重试）时触发，调用方应丢弃已累积的正文重新开始。</summary>
    Task<ChatStreamResult> ChatStreamAsync(ChatRequest req, Action<string> onToken, Action<ChatStreamResult> onDone, Action<string>? onStatus = null, Action<string>? onThinking = null, Action? onRestart = null, CancellationToken ct = default);
    Task<DocumentListResponse> ListDocumentsAsync(string? collection = null, int page = 1, int pageSize = 20, string? format = null, string sort = "created_at_desc", string? q = null, CancellationToken ct = default);
    Task<DocumentDetail> GetDocumentAsync(string id, int chunks = 5, int chunkContentLength = 200, string? collection = null, CancellationToken ct = default);
    Task<DeleteResult> DeleteDocumentAsync(string id, string? collection = null, CancellationToken ct = default);
    Task<Stats> GetStatsAsync(string? collection = null, CancellationToken ct = default);
    /// <summary>创建空知识库集合（POST /v1/collections）。</summary>
    Task<Stats> CreateCollectionAsync(string name, CancellationToken ct = default);
    Task<QualityReport> GetQualityAsync(string? collection = null, CancellationToken ct = default);
    Task<ConvertResult> ConvertAsync(ConvertRequest req, CancellationToken ct = default);
    Task<JobStatus> ReindexAsync(ReindexRequest req, CancellationToken ct = default);
    /// <summary>AI 知识库整理（POST /v1/curate，异步任务）：打标签/摘要/归类/语义去重/归纳合并。
    /// dry_run=true（默认）只读预览零写入；dedup/consolidate 有损，确认预览后用 dry_run=false 执行。</summary>
    Task<JobStatus> CurateAsync(CurateRequest req, CancellationToken ct = default);
    Task<JobStatus> GetJobAsync(string jobId, CancellationToken ct = default);
    /// <summary>取消异步任务（DELETE /v1/jobs/{jobId}）。</summary>
    Task<JobStatus> CancelJobAsync(string jobId, CancellationToken ct = default);
    /// <summary>更新分块批注（PUT /v1/chunks/{chunkId}/annotation）。</summary>
    Task UpsertChunkAnnotationAsync(int chunkId, string text, CancellationToken ct = default);
    Task<JobStatus> PollJobUntilDoneAsync(string jobId, IProgress<JobStatus>? progress = null, TimeSpan? pollInterval = null, CancellationToken ct = default);

    /// <summary>订阅 job 进度 SSE（GET /v1/jobs/{id}/events，实时进度），返回最终 JobStatus。
    /// SSE 不可用/中断时自动回退到轮询，保证任务仍能收敛。</summary>
    Task<JobStatus> WatchJobUntilDoneAsync(string jobId, IProgress<JobStatus>? progress = null, CancellationToken ct = default);

    /// <summary>GPU 加速环境诊断（GET /v1/system/gpu-diagnosis）。</summary>
    Task<GpuDiagnosis> GetGpuDiagnosisAsync(CancellationToken ct = default);

    /// <summary>本地 AI 环境与模型资产智能探测（GET /v1/system/local-ai-environment）。</summary>
    Task<LocalAiEnvironment> GetLocalAiEnvironmentAsync(CancellationToken ct = default);

    /// <summary>GPU 加速包一键安装（POST /v1/system/install-gpu，SSE 流式）。
    /// onLog 每收到一行 pip 日志触发，onDone 在安装完成/失败时触发（bool 为成功标志）。</summary>
    Task InstallGpuAsync(string path, Action<string> onLog, Action<bool> onDone, CancellationToken ct = default);

    /// <summary>OCR 扩展一键安装（POST /v1/system/install-ocr，SSE 流式）。
    /// path 取 "cpu"（CPU 版，所有设备）或 "paddle-ocr-gpu"（NVIDIA GPU 加速版）；
    /// 回调语义与 InstallGpuAsync 相同。</summary>
    Task InstallOcrAsync(string path, Action<string> onLog, Action<bool> onDone, CancellationToken ct = default);

    /// <summary>运行依赖就绪状态聚合（GET /v1/system/dependencies）。</summary>
    Task<DependenciesStatus> GetDependenciesAsync(CancellationToken ct = default);

    /// <summary>下载嵌入模型（POST /v1/system/download-model，SSE 流式进度）。
    /// progress 每收到一帧进度触发；成功返回模型快照目录路径，失败抛 ApiException。</summary>
    Task<string?> DownloadModelAsync(string? modelName = null, IProgress<DownloadProgressFrame>? progress = null, CancellationToken ct = default);

    /// <summary>知识图谱可视化数据（GET /v1/graph/visualize）。</summary>
    Task<GraphResponse> GetGraphAsync(string? collection = null, int limit = 200, CancellationToken ct = default);

    /// <summary>单实体关联关系（GET /v1/graph/relations/{entityId}）。</summary>
    Task<List<GraphEntityRelation>> GetEntityRelationsAsync(string entityId, int limit = 50, CancellationToken ct = default);

    /// <summary>实体完整知识全景与具体内容（GET /v1/graph/entities/{entityId}/details）。</summary>
    Task<GraphEntityDetailResponse> GetEntityDetailAsync(string entityId, int limit = 8, CancellationToken ct = default);

    /// <summary>图谱规模统计（GET /v1/graph/stats，权威实体/关系总数）。</summary>
    Task<GraphStats> GetGraphStatsAsync(string? collection = null, CancellationToken ct = default);

    /// <summary>知识图谱实体列表（GET /v1/graph/entities，可分页取前 N 个）。</summary>
    Task<List<GraphNode>> GetGraphEntitiesAsync(string? collection = null, int limit = 200, CancellationToken ct = default);

    /// <summary>触发已有文档的知识图谱实体抽取（POST /v1/graph/extract）。</summary>
    Task<GraphExtractResult> ExtractGraphAsync(string? collection = null, int topK = 20, CancellationToken ct = default);

    /// <summary>实体知识卡片智能蒸馏（POST /v1/graph/entities/distill）。</summary>
    Task<EntityDistillResponse> DistillEntityKnowledgeAsync(EntityDistillRequest req, CancellationToken ct = default);

    /// <summary>系统全面环境诊断（GET /v1/doctor）。</summary>
    Task<DoctorReportResult> GetDoctorReportAsync(bool network = true, CancellationToken ct = default);

    /// <summary>一键导入内置新手示例文档库（POST /v1/sample/ingest）。</summary>
    Task<SampleIngestResult> IngestSampleAsync(string collection = "default", CancellationToken ct = default);

    /// <summary>多格式创作交付物导出为物理文件（POST /v1/creative/export）。</summary>
    Task<CreativeExportResponse> ExportCreativeArtifactAsync(CreativeExportRequest req, CancellationToken ct = default);

    /// <summary>PPT 效果自检与质量体检评分（POST /v1/creative/inspect）。</summary>
    Task<PptInspectionReportDto> InspectCreativeArtifactAsync(string content, CancellationToken ct = default);

    /// <summary>订阅后端事件流（SSE GET /v1/events）。返回 IDisposable 用于取消订阅。</summary>
    IDisposable SubscribeEvents(Action<EventMessage> onEvent, CancellationToken ct = default);

    /// <summary>库健康状态（GET /v1/library/status）：ok/empty/warn/reindex_needed。</summary>
    Task<LibraryStatus> GetLibraryStatusAsync(CancellationToken ct = default);

    /// <summary>切换使用档案（POST /v1/profile）：notes/docs/agent/library。</summary>
    Task<ProfileSwitchResult> SetUsageProfileAsync(string profile, CancellationToken ct = default);
}
