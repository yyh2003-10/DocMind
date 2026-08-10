using System.IO;
using System.Text.Json;

namespace DocMind;

public class AppSettings
{
    public string BackendUrl { get; set; } = "http://127.0.0.1:8765";
    public int PollIntervalMs { get; set; } = 1000;
    public int StartupTimeoutSec { get; set; } = 30;
    /// <summary>后端请求超时（秒）。OCR/嵌入是耗时操作（扫描型 PDF 可达数分钟），默认 30 分钟。</summary>
    public int RequestTimeoutSec { get; set; } = 1800;
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

    // ===== 配置文件路径 =====

    /// <summary>用户级配置目录（%LOCALAPPDATA%\DocMind\）。</summary>
    public static string ConfigDir => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "DocMind");

    /// <summary>用户级配置文件路径。</summary>
    public static string ConfigPath => Path.Combine(ConfigDir, "appsettings.json");

    /// <summary>确保配置目录存在。</summary>
    public static void EnsureConfigDir()
    {
        Directory.CreateDirectory(ConfigDir);
    }

    /// <summary>持久化当前设置到用户级目录（%LOCALAPPDATA%\DocMind\appsettings.json）。</summary>
    public void Save()
    {
        EnsureConfigDir();
        var json = JsonSerializer.Serialize(this, new JsonSerializerOptions
        {
            WriteIndented = true,
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        });
        File.WriteAllText(ConfigPath, json);
    }
}
