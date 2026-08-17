using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;
using DocMind.Services;

namespace DocMind;

public class AppSettings
{
    public string BackendUrl { get; set; } = "http://127.0.0.1:8765";
    public int PollIntervalMs { get; set; } = 1000;
    public int StartupTimeoutSec { get; set; } = 30;
    /// <summary>后端请求超时（秒）。大文件 OCR/嵌入可能较慢，建议 60-300 秒。</summary>
    public int RequestTimeoutSec { get; set; } = 60;
    public string Theme { get; set; } = "Light";

    /// <summary>拉起后端用的命令（绝对路径优先；空表示自动探测 doc2mind / python -m doc2mind）。</summary>
    public string? BackendCommand { get; set; }

    // ===== 后端模型/分块参数（注入 DOC2MIND_* 环境变量，重启后端生效） =====
    /// <summary>嵌入模型名（对应后端 DOC2MIND_EMBED_MODEL）。</summary>
    public string EmbedModel { get; set; } = "BAAI/bge-small-zh-v1.5";
    /// <summary>本地模型目录（对应后端 DOC2MIND_EMBED_MODEL_PATH；空 = 用 EmbedModel 联网下载）。</summary>
    public string? EmbedModelPath { get; set; }
    /// <summary>分块最大 token 数（DOC2MIND_CHUNK_MAX_TOKENS）。</summary>
    public int? ChunkMaxTokens { get; set; }
    /// <summary>分块最小字符数（DOC2MIND_CHUNK_MIN_CHARS）。</summary>
    public int? ChunkMinChars { get; set; }
    /// <summary>分块重叠字符数（DOC2MIND_CHUNK_OVERLAP_CHARS）。</summary>
    public int? ChunkOverlapChars { get; set; }
    /// <summary>分块最大字符数（DOC2MIND_CHUNK_MAX_CHARS）。</summary>
    public int? ChunkMaxChars { get; set; }

    /// <summary>HuggingFace 镜像端点（注入 HF_ENDPOINT 环境变量；空 = 用内置默认值 hf-mirror.com）。</summary>
    public string? HfEndpoint { get; set; }

    // ===== LLM / RAG 对话（启动时注入 DOC2MIND_* 环境变量） =====
    /// <summary>LLM 提供商标识（none | openai | ollama）。</summary>
    public string LlmProvider { get; set; } = "none";
    /// <summary>OpenAI 兼容 API Key（对应 DOC2MIND_LLM_API_KEY）。</summary>
    public string? LlmApiKey { get; set; }
    /// <summary>API 基础地址（对应 DOC2MIND_LLM_BASE_URL）。</summary>
    public string? LlmBaseUrl { get; set; }
    /// <summary>模型名（对应 DOC2MIND_LLM_MODEL）。</summary>
    public string LlmModel { get; set; } = "";
    /// <summary>温度参数（对应 DOC2MIND_LLM_TEMPERATURE）。</summary>
    public double LlmTemperature { get; set; } = 0.7;
    /// <summary>最大 token 数（对应 DOC2MIND_LLM_MAX_TOKENS）。</summary>
    public int LlmMaxTokens { get; set; } = 2048;
    /// <summary>检索引用 chunk 数（对应 DOC2MIND_RAG_TOP_K）。</summary>
    public int RagTopK { get; set; } = 5;
    /// <summary>自定义 RAG 系统提示词（对应 DOC2MIND_RAG_SYSTEM_PROMPT；空 = 用后端内置默认提示词）。</summary>
    public string? RagSystemPrompt { get; set; }
    /// <summary>多轮对话历史 token 预算（对应 DOC2MIND_RAG_MAX_HISTORY_TOKENS；0 = 不按 token 截断）。</summary>
    public int RagMaxHistoryTokens { get; set; } = 4096;

    // ===== 文件系统监控 =====
    /// <summary>监控目录列表（对应 DOC2MIND_WATCH_PATHS，逗号分隔注入）。</summary>
    public List<string> WatchPaths { get; set; } = new();
    /// <summary>监控防抖秒数（对应 DOC2MIND_WATCH_DEBOUNCE_SECONDS）。</summary>
    public double WatchDebounceSeconds { get; set; } = 5.0;

    // ===== 启动选项 =====
    /// <summary>启动 WPF 时自动拉起后端子进程（false = 仅轮询外部已运行的后端）。</summary>
    public bool AutoStartBackend { get; set; } = true;
    /// <summary>WPF 退出时联动终止后端子进程（false = 退出后保留后端继续运行）。</summary>
    public bool StopBackendOnExit { get; set; } = true;
    /// <summary>启动时自动 ingest 的目录路径（空表示不自动导入）。</summary>
    public string? AutoIngestPath { get; set; }
    /// <summary>自动 ingest 用的集合名（默认 default）。</summary>
    public string AutoIngestCollection { get; set; } = "default";
    /// <summary>自动 ingest 目录时是否递归子目录。</summary>
    public bool AutoIngestRecursive { get; set; } = false;

    /// <summary>用户是否已选择"不再提示 GPU 加速"（持久化，避免每次启动都弹）。</summary>
    public bool DismissGpuWarning { get; set; } = false;

    /// <summary>启动时 LlmApiKey 密文解密失败（换 Windows 用户/文件损坏）。
    /// 仅运行时标志：提醒用户重新输入，不落盘。加载入口（App.LoadSettings）负责置位。</summary>
    [JsonIgnore]
    public bool LlmKeyDecryptFailed { get; set; }

    // ===== 配置文件路径 =====

    /// <summary>测试覆写的配置目录；null = 用真实 %LOCALAPPDATA%\DocMind。
    /// 单元测试必须指向 temp 目录，避免 SaveAsync 落盘覆盖用户真实配置（含 API Key）。</summary>
    internal static string? ConfigDirOverrideForTests { get; set; }

    /// <summary>用户级配置目录（%LOCALAPPDATA%\DocMind\）。</summary>
    public static string ConfigDir => ConfigDirOverrideForTests ?? Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DocMind");

    /// <summary>用户级配置文件路径。</summary>
    public static string ConfigPath => Path.Combine(ConfigDir, "appsettings.json");

    /// <summary>确保配置目录存在。</summary>
    public static void EnsureConfigDir()
    {
        Directory.CreateDirectory(ConfigDir);
    }

    /// <summary>持久化当前设置到用户级目录（%LOCALAPPDATA%\DocMind\appsettings.json）。
    /// 唯一的落盘出口：LlmApiKey 统一经 DPAPI 加密（幂等：明文迁移为密文、已密文原样、空值原样），
    /// 内存单例仍持明文供运行时使用。序列化副本，不改动 this 的字段值。</summary>
    public void Save()
    {
        EnsureConfigDir();
        var snapshot = (AppSettings)MemberwiseClone();
        snapshot.LlmApiKey = SecretProtector.Protect(LlmApiKey);
        var json = JsonSerializer.Serialize(snapshot, new JsonSerializerOptions
        {
            WriteIndented = true,
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        });
        File.WriteAllText(ConfigPath, json);
    }
}
