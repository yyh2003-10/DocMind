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
}
