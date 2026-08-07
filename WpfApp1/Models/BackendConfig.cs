namespace DocMind.Models;

/// <summary>对应后端 ConfigResponse：设置页可调的后端运行参数（嵌入 + 分块 + 检索）。</summary>
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

    /// <summary>后端可选提示（如切换模型后需重建索引）；null/空表示无提示。</summary>
    public string? Notice { get; init; }
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
}
