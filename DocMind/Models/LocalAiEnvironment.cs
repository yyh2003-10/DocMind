using System.Collections.Generic;
using System.Text.Json.Serialization;

namespace DocMind.Models
{
    /// <summary>
    /// 本地 AI 环境智能探测报告。
    /// </summary>
    public sealed record LocalAiEnvironment
    {
        [JsonPropertyName("ollama")]
        public ServiceStatusInfo Ollama { get; init; } = new();

        [JsonPropertyName("lm_studio")]
        public ServiceStatusInfo LmStudio { get; init; } = new();

        [JsonPropertyName("local_gguf_models")]
        public List<LocalGgufModelInfo> LocalGgufModels { get; init; } = new();

        [JsonPropertyName("local_gguf_count")]
        public int LocalGgufCount { get; init; }

        /// <summary>档位判定结果：{tier, tier_name, vram_gb, ram_gb, gpu_name, source}。</summary>
        [JsonPropertyName("tier")]
        public Dictionary<string, object?>? Tier { get; init; }

        /// <summary>model_bundles.BUNDLE_VERSION，前端据此兼容。</summary>
        [JsonPropertyName("bundle_version")]
        public int BundleVersion { get; init; }

        [JsonPropertyName("recommendations")]
        public List<AutoSetupRecommendation> Recommendations { get; init; } = new();
    }

    /// <summary>
    /// 服务运行状态及模型。
    /// </summary>
    public sealed record ServiceStatusInfo
    {
        [JsonPropertyName("running")]
        public bool Running { get; init; }

        [JsonPropertyName("base_url")]
        public string BaseUrl { get; init; } = "";

        [JsonPropertyName("models")]
        public List<ModelItemInfo> Models { get; init; } = new();

        [JsonPropertyName("chat_models")]
        public List<string> ChatModels { get; init; } = new();

        [JsonPropertyName("embed_models")]
        public List<string> EmbedModels { get; init; } = new();

        [JsonPropertyName("default_chat_model")]
        public string? DefaultChatModel { get; init; }

        [JsonPropertyName("default_embed_model")]
        public string? DefaultEmbedModel { get; init; }

        /// <summary>运行时是否已安装（不要求服务在跑）。</summary>
        [JsonPropertyName("installed")]
        public bool Installed { get; init; }

        /// <summary>安装位置（主 exe）；已装但路径未知时为 null。</summary>
        [JsonPropertyName("install_path")]
        public string? InstallPath { get; init; }

        /// <summary>CLI 版本（可探测到时有值）。</summary>
        [JsonPropertyName("version")]
        public string? Version { get; init; }
    }

    public sealed record ModelItemInfo
    {
        [JsonPropertyName("name")]
        public string Name { get; init; } = "";

        [JsonPropertyName("size_gb")]
        public double SizeGb { get; init; }
    }

    public sealed record LocalGgufModelInfo
    {
        [JsonPropertyName("name")]
        public string Name { get; init; } = "";

        [JsonPropertyName("filename")]
        public string Filename { get; init; } = "";

        [JsonPropertyName("path")]
        public string Path { get; init; } = "";

        [JsonPropertyName("size_gb")]
        public double SizeGb { get; init; }

        [JsonPropertyName("dir")]
        public string Dir { get; init; } = "";
    }

    public sealed record AutoSetupRecommendation
    {
        [JsonPropertyName("id")]
        public string Id { get; init; } = "";

        /// <summary>条目类型：bundle（档位整套搭配）/ service（已运行服务直连）。</summary>
        [JsonPropertyName("kind")]
        public string Kind { get; init; } = "";

        /// <summary>档位 id（bundle 条目）：minimal / light / mainstream / advanced / flagship。</summary>
        [JsonPropertyName("tier")]
        public string Tier { get; init; } = "";

        /// <summary>是否为本机硬件判定的当前档位。</summary>
        [JsonPropertyName("is_current_tier")]
        public bool IsCurrentTier { get; init; }

        /// <summary>三态：ready（模型已装）/ model_missing（缺模型）/ runtime_missing（缺运行时）。</summary>
        [JsonPropertyName("state")]
        public string State { get; init; } = "";

        /// <summary>整套搭配的嵌入模型（bundle 条目）。</summary>
        [JsonPropertyName("embed_model")]
        public string EmbedModel { get; init; } = "";

        /// <summary>整套搭配的重排模型（bundle 条目；空 = 不启用）。</summary>
        [JsonPropertyName("rerank_model")]
        public string RerankModel { get; init; } = "";

        /// <summary>是否启用重排（极轻量档 false）。</summary>
        [JsonPropertyName("rerank_enabled")]
        public bool RerankEnabled { get; init; } = true;

        /// <summary>首选对话模型的 Ollama 拉取命令（魔搭加速源）。</summary>
        [JsonPropertyName("pull_command")]
        public string PullCommand { get; init; } = "";

        /// <summary>缺运行时（Ollama）时的下载页地址。</summary>
        [JsonPropertyName("installer_url")]
        public string InstallerUrl { get; init; } = "";

        /// <summary>已装未启动时的启动指引文本（installed_stopped 态）。</summary>
        [JsonPropertyName("install_hint")]
        public string InstallHint { get; init; } = "";

        /// <summary>档位内可选对话模型清单（bundle 条目）。</summary>
        [JsonPropertyName("chat_options")]
        public List<ChatModelOptionDto> ChatOptions { get; init; } = new();

        [JsonPropertyName("title")]
        public string Title { get; init; } = "";

        [JsonPropertyName("provider")]
        public string Provider { get; init; } = "";

        [JsonPropertyName("base_url")]
        public string BaseUrl { get; init; } = "";

        [JsonPropertyName("api_key")]
        public string ApiKey { get; init; } = "";

        [JsonPropertyName("model")]
        public string Model { get; init; } = "";

        [JsonPropertyName("description")]
        public string Description { get; init; } = "";

        [JsonPropertyName("badge")]
        public string Badge { get; init; } = "";
    }

    /// <summary>档位内一个可选的本地对话模型。</summary>
    public sealed record ChatModelOptionDto
    {
        [JsonPropertyName("model_id")]
        public string ModelId { get; init; } = "";

        [JsonPropertyName("display_name")]
        public string DisplayName { get; init; } = "";

        [JsonPropertyName("size_gb")]
        public double SizeGb { get; init; }

        [JsonPropertyName("pull_command")]
        public string PullCommand { get; init; } = "";

        [JsonPropertyName("recommended")]
        public bool Recommended { get; init; }

        [JsonPropertyName("note")]
        public string Note { get; init; } = "";
    }

    /// <summary>ollama pull 任务状态快照（GET /v1/system/ollama/pull/status 轮询）。</summary>
    public sealed record OllamaPullStatus
    {
        /// <summary>idle / pulling / done / error。</summary>
        [JsonPropertyName("state")]
        public string State { get; init; } = "idle";

        [JsonPropertyName("model")]
        public string Model { get; init; } = "";

        [JsonPropertyName("percent")]
        public double Percent { get; init; }

        [JsonPropertyName("message")]
        public string Message { get; init; } = "";

        [JsonPropertyName("error")]
        public string Error { get; init; } = "";
    }
}
