using System.Text.Json.Serialization;

namespace DocMind.Models;

/// <summary>对应后端 ConfigResponse：设置页可调的后端运行参数（嵌入 + 分块 + 检索 + LLM）。</summary>
public sealed record BackendConfig
{
    public string EmbedModel { get; init; } = "BAAI/bge-small-zh-v1.5";
    public int EmbedBatchSize { get; init; } = 32;
    public int ChunkMaxTokens { get; init; } = 1500;
    public int ChunkMinChars { get; init; } = 50;
    public int ChunkOverlapChars { get; init; } = 200;
    public int ChunkMaxChars { get; init; } = 4000;
    public int SearchTopK { get; init; } = 10;
    public int RrfK { get; init; } = 60;

    // --- LLM / RAG 对话 ---
    public string LlmProvider { get; init; } = "none";
    public string? LlmBaseUrl { get; init; }
    public string LlmModel { get; init; } = "";
    public double LlmTemperature { get; init; } = 0.7;
    public int LlmMaxTokens { get; init; } = 2048;
    public int RagTopK { get; init; } = 5;
    public double RagMinScore { get; init; } = 0.0;

    /// <summary>自定义 RAG 系统提示词；null = 未配置（后端用内置默认提示词）。</summary>
    public string? RagSystemPrompt { get; init; }

    /// <summary>多轮对话历史 token 预算（0 = 不按 token 截断）。</summary>
    public int RagMaxHistoryTokens { get; init; } = 4096;

    /// <summary>API key 是否已配置（后端不回传明文，仅布尔标记）。</summary>
    public bool LlmApiKeyConfigured { get; init; }

    /// <summary>后端可选提示（如切换模型后需重建索引）；null/空表示无提示。</summary>
    public string? Notice { get; init; }

    /// <summary>后端启动时 config.toml 解析失败的告警（用户应修复/删除配置文件）；null = 正常。</summary>
    public string? ConfigError { get; init; }

    /// <summary>文件监控目录列表。</summary>
    public List<string> WatchPaths { get; init; } = new();

    /// <summary>文件监控去抖秒数。</summary>
    public double WatchDebounceSeconds { get; init; } = 5.0;
}

/// <summary>对应后端 ConfigUpdate：只提交有值的字段（null 表示不修改）。</summary>
public sealed record BackendConfigUpdate
{
    public string? EmbedModel { get; init; }
    /// <summary>本地模型目录（后端 DOC2MIND_EMBED_MODEL_PATH）；空字符串=清除本地模型。</summary>
    public string? EmbedModelPath { get; init; }
    public int? EmbedBatchSize { get; init; }
    public int? ChunkMaxTokens { get; init; }
    public int? ChunkMinChars { get; init; }
    public int? ChunkOverlapChars { get; init; }
    public int? ChunkMaxChars { get; init; }
    public int? SearchTopK { get; init; }
    public int? RrfK { get; init; }

    // --- LLM / RAG 对话 ---
    public string? LlmProvider { get; init; }
    /// <summary>API key（写后端不回显；null 表示不修改）。</summary>
    public string? LlmApiKey { get; init; }
    public string? LlmBaseUrl { get; init; }
    public string? LlmModel { get; init; }
    public double? LlmTemperature { get; init; }
    public int? LlmMaxTokens { get; init; }
    public int? RagTopK { get; init; }
    public double? RagMinScore { get; init; }

    /// <summary>自定义 RAG 系统提示词；空字符串=显式清除（回到内置默认），null=不修改。</summary>
    public string? RagSystemPrompt { get; init; }

    /// <summary>多轮对话历史 token 预算；null = 不修改。</summary>
    public int? RagMaxHistoryTokens { get; init; }

    public float? LlmTimeout { get; init; }

    // --- 文件监控 ---
    public List<string>? WatchPaths { get; init; }
    public double? WatchDebounceSeconds { get; init; }
}
