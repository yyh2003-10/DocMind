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
    /// <summary>流式对话：消费 SSE 逐 token 输出。onToken 每收到一个 token 触发，onDone 在终帧触发，返回终帧元数据。</summary>
    Task<ChatStreamResult> ChatStreamAsync(ChatRequest req, Action<string> onToken, Action<ChatStreamResult> onDone, CancellationToken ct = default);
    Task<DocumentListResponse> ListDocumentsAsync(string? collection = null, int page = 1, int pageSize = 20, string? format = null, string sort = "created_at_desc", string? q = null, CancellationToken ct = default);
    Task<DocumentDetail> GetDocumentAsync(string id, int chunks = 5, int chunkContentLength = 200, string? collection = null, CancellationToken ct = default);
    Task<DeleteResult> DeleteDocumentAsync(string id, string? collection = null, CancellationToken ct = default);
    Task<Stats> GetStatsAsync(string? collection = null, CancellationToken ct = default);
    /// <summary>创建空知识库集合（POST /v1/collections）。</summary>
    Task<Stats> CreateCollectionAsync(string name, CancellationToken ct = default);
    Task<QualityReport> GetQualityAsync(string? collection = null, CancellationToken ct = default);
    Task<ConvertResult> ConvertAsync(ConvertRequest req, CancellationToken ct = default);
    Task<JobStatus> ReindexAsync(ReindexRequest req, CancellationToken ct = default);
    Task<JobStatus> GetJobAsync(string jobId, CancellationToken ct = default);
    /// <summary>取消异步任务（DELETE /v1/jobs/{jobId}）。</summary>
    Task<JobStatus> CancelJobAsync(string jobId, CancellationToken ct = default);
    /// <summary>更新分块批注（PUT /v1/chunks/{chunkId}/annotation）。</summary>
    Task UpsertChunkAnnotationAsync(int chunkId, string text, CancellationToken ct = default);
    Task<JobStatus> PollJobUntilDoneAsync(string jobId, IProgress<JobStatus>? progress = null, TimeSpan? pollInterval = null, CancellationToken ct = default);

    /// <summary>GPU 加速环境诊断（GET /v1/system/gpu-diagnosis）。</summary>
    Task<GpuDiagnosis> GetGpuDiagnosisAsync(CancellationToken ct = default);

    /// <summary>GPU 加速包一键安装（POST /v1/system/install-gpu，SSE 流式）。
    /// onLog 每收到一行 pip 日志触发，onDone 在安装完成/失败时触发（bool 为成功标志）。</summary>
    Task InstallGpuAsync(string path, Action<string> onLog, Action<bool> onDone, CancellationToken ct = default);

    /// <summary>知识图谱可视化数据（GET /v1/graph/visualize）。</summary>
    Task<GraphResponse> GetGraphAsync(string? collection = null, int limit = 200, CancellationToken ct = default);

    /// <summary>单实体关联关系（GET /v1/graph/relations/{entityId}）。</summary>
    Task<List<GraphEntityRelation>> GetEntityRelationsAsync(string entityId, int limit = 50, CancellationToken ct = default);

    /// <summary>实体完整知识全景与具体内容（GET /v1/graph/entities/{entityId}/details）。</summary>
    Task<GraphEntityDetailResponse> GetEntityDetailAsync(string entityId, int limit = 8, CancellationToken ct = default);

    /// <summary>触发已有文档的知识图谱实体抽取（POST /v1/graph/extract）。</summary>
    Task<GraphExtractResult> ExtractGraphAsync(string? collection = null, int topK = 20, CancellationToken ct = default);

    /// <summary>实体知识卡片智能蒸馏（POST /v1/graph/entities/distill）。</summary>
    Task<EntityDistillResponse> DistillEntityKnowledgeAsync(EntityDistillRequest req, CancellationToken ct = default);

    /// <summary>订阅后端事件流（SSE GET /v1/events）。返回 IDisposable 用于取消订阅。</summary>
    IDisposable SubscribeEvents(Action<EventMessage> onEvent, CancellationToken ct = default);
}
