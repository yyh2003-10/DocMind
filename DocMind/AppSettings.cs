using System.IO;
using System.Text.Json;
using System.Text.Json.Serialization;
using DocMind.Services;

namespace DocMind;

/// <summary>用户自定义角色条目。</summary>
public sealed class CustomPersonaEntry
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N")[..8];
    public string Name { get; set; } = string.Empty;
    public string Icon { get; set; } = "🤖";
    public string Description { get; set; } = string.Empty;
}

/// <summary>用户自定义主题条目。</summary>
public sealed class CustomThemeEntry
{
    public string Id { get; set; } = Guid.NewGuid().ToString("N")[..8];
    public string DisplayName { get; set; } = string.Empty;
    public string Icon { get; set; } = "🎨";
    public string Description { get; set; } = string.Empty;
    public string PrimaryHex { get; set; } = "#3B82F6";
    public string BgHex { get; set; } = "#F8FAFC";

    /// <summary>设置页列表色块预览（PrimaryHex）。</summary>
    [System.Text.Json.Serialization.JsonIgnore]
    public System.Windows.Media.Brush PrimaryBrush
    {
        get
        {
            try
            {
                var c = (System.Windows.Media.Color)System.Windows.Media.ColorConverter.ConvertFromString(PrimaryHex);
                return new System.Windows.Media.SolidColorBrush(c);
            }
            catch
            {
                return System.Windows.Media.Brushes.SteelBlue;
            }
        }
    }
}

public class AppSettings
{
    public string BackendUrl { get; set; } = "http://127.0.0.1:8765";
    public int PollIntervalMs { get; set; } = 1000;
    public int StartupTimeoutSec { get; set; } = 30;
    /// <summary>后端请求超时（秒）。大文件 OCR/嵌入可能耗时数分钟，默认 1800s
    /// （与 App.xaml.cs 的 Math.Max(120, RequestTimeoutSec) 下限配合）。</summary>
    public int RequestTimeoutSec { get; set; } = 1800;
    public string Theme { get; set; } = "Light";

    /// <summary>拉起后端用的命令（绝对路径优先；空表示自动探测 doc2mind / python -m doc2mind）。</summary>
    public string? BackendCommand { get; set; }

    // ===== 推送到后端的共享配置（注入 DOC2MIND_* 环境变量，重启后端生效） =====
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

    /// <summary>OCR 识别语言（注入 DOC2MIND_OCR_LANG；ch=中英混合，en=纯英文，japan 等）。</summary>
    public string OcrLanguage { get; set; } = "ch";

    // ===== 外部资源路径（可由用户指定或「自动寻找可用配置」写入；注入 DOC2MIND_* 环境变量） =====

    /// <summary>poppler bin 目录（含 pdftoppm.exe；扫描 PDF OCR 渲染依赖；
    /// 注入 DOC2MIND_POPPLER_PATH。空 = 后端按内置顺序自动探测）。</summary>
    public string? PopplerPath { get; set; }

    /// <summary>离线安装包 wheels 目录（注入 DOCMIND_WHEELS_DIR，插件安装离线优先）。</summary>
    public string? WheelsDir { get; set; }

    /// <summary>嵌入模型缓存目录（注入 DOC2MIND_EMBED_CACHE_DIR；
    /// 空 = 默认 %LOCALAPPDATA%\doc2mind\fastembed_cache）。</summary>
    public string? EmbedCacheDir { get; set; }

    // ===== 推送到后端的共享配置 — LLM / RAG 对话（启动时注入 DOC2MIND_* 环境变量） =====
    /// <summary>LLM 提供商标识（none | openai | ollama）。</summary>
    public string LlmProvider { get; set; } = "none";
    /// <summary>OpenAI 兼容 API Key（对应 DOC2MIND_LLM_API_KEY）。</summary>
    public string? LlmApiKey { get; set; }

    /// <summary>联网搜索 GitHub 通道的个人令牌（可选）。每个用户填自己的 Token：
    /// 随对话请求按请求携带（ChatRequest.GithubToken），后端不注入环境变量、
    /// 不写全局配置，避免多用户共享同一 GitHub 账户；留空 = 用匿名公开额度。
    /// 落盘时经 DPAPI 加密（与 LlmApiKey 同机制，内存单例持明文）。</summary>
    public string? GithubToken { get; set; }
    /// <summary>API 基础地址（对应 DOC2MIND_LLM_BASE_URL）。</summary>
    public string? LlmBaseUrl { get; set; }
    /// <summary>模型名（对应 DOC2MIND_LLM_MODEL）。</summary>
    public string LlmModel { get; set; } = "";
    /// <summary>温度参数（对应 DOC2MIND_LLM_TEMPERATURE）。</summary>
    public double LlmTemperature { get; set; } = 0.7;
    /// <summary>最大 token 数（对应 DOC2MIND_LLM_MAX_TOKENS）。</summary>
    public int LlmMaxTokens { get; set; } = 8192;
    /// <summary>LLM 调用/流式空闲超时（秒，对应 DOC2MIND_LLM_TIMEOUT）。
    /// 0 = 用后端默认 180s。慢网/大模型（NIM 等）建议 300+。</summary>
    public double LlmTimeoutSec { get; set; } = 300;
    /// <summary>联网搜索总预算（秒，对应 DOC2MIND_WEB_SEARCH_TIMEOUT）。
    /// 多引擎聚合 + 正文精读硬上限；过短会在慢网/反爬下拿不到正文。默认 36。</summary>
    public double WebSearchTimeoutSec { get; set; } = 36;
    /// <summary>自建/自选 SearXNG 实例地址（对应 DOC2MIND_WEB_SEARCH_SEARXNG_URL）。
    /// 空 = 用内置公有实例；可填单个 URL 或逗号分隔多个，如 http://127.0.0.1:8888。</summary>
    public string? WebSearchSearxngUrl { get; set; }
    /// <summary>检索引用 chunk 数（对应 DOC2MIND_RAG_TOP_K）。</summary>
    public int RagTopK { get; set; } = 5;
    /// <summary>自定义 RAG 系统提示词（对应 DOC2MIND_RAG_SYSTEM_PROMPT；空 = 用后端内置默认提示词）。</summary>
    public string? RagSystemPrompt { get; set; }
    /// <summary>多轮对话历史 token 预算（对应 DOC2MIND_RAG_MAX_HISTORY_TOKENS；0 = 不按 token 截断）。</summary>
    public int RagMaxHistoryTokens { get; set; } = 4096;
    /// <summary>RAG 问答模式（"strict" = 严格知识库模式；"hybrid" = 混合常识增强模式，未命中本地文档时使用大模型常识解答）。</summary>
    public string RagMode { get; set; } = "hybrid";

    /// <summary>使用档案：docs | notes | agent | library。决定回答风格与检索默认预设。</summary>
    public string UsageProfile { get; set; } = "docs";

    /// <summary>是否启用检索后重排（Reranker / cross-encoder 精排），对应 DOC2MIND_RERANK_ENABLED。
    /// 开启后用重排模型对召回候选逐对打分重排，显著提升知识检索相关性；模型不可用时自动降级为原始 RRF 排序。</summary>
    public bool RerankEnabled { get; set; } = true;
    /// <summary>重排模型名（fastembed TextCrossEncoder 支持列表中的模型），对应 DOC2MIND_RERANK_MODEL。
    /// 默认 BAAI/bge-reranker-base（中英可用，首次使用需联网下载约 280MB）。
    /// 注意：Xenova/bge-reranker-v2-m3 不在支持列表，会被自动纠正。</summary>
    public string RerankModel { get; set; } = "BAAI/bge-reranker-base";
    /// <summary>送入重排器的候选数上限（对应 DOC2MIND_RERANK_RECALL），默认 20。</summary>
    public int RerankRecall { get; set; } = 20;

    /// <summary>AI 提供商档案列表：可复用命名配置（提供商/BaseURL/Key/模型/温度等），
    /// 设置页与对话页一键应用与切换。ApiKey 落盘时经 DPAPI 加密（与 LlmApiKey 同机制）。</summary>
    public List<Models.LlmProfile> LlmProfiles { get; set; } = new();

    /// <summary>最后应用的档案 Id（仅用于 UI 高亮/默认选中，不自动改配置）。</summary>
    public string? ActiveProfileId { get; set; }

    /// <summary>对话页最后选择的模型名（持久化，重启/新建对话后还原）。
    /// null = 选中「默认」项（用设置页配置的默认模型）；非空 = 对话页点选的模型名
    /// （配合 LastChatProfileId 定位到具体服务商条目）。</summary>
    public string? LastChatModel { get; set; }

    /// <summary>对话页最后选择模型所属的服务商档案 Id；null = 默认提供商分组
    /// （裸模型名，用设置页全局配置的 provider/key/地址）。</summary>
    public string? LastChatProfileId { get; set; }

    /// <summary>设置页「获取模型列表」拉到的可用模型名（持久化，由设置页保存时镜像 LlmModels 写入）。
    /// 对话页默认提供商分组的种子来源之一：让用户不必每次都到对话页点刷新，
    /// 重启后也能直接看到上次拉取的全部模型。始终镜像设置页当前的模型候选，
    /// 切换服务商时由设置页先 Clear 再重填，故无需额外的来源指纹。</summary>
    public List<string> LlmAvailableModels { get; set; } = new();

    /// <summary>对话页「🌐 联网搜索」开关是否开启（持久化，重启后保持勾选状态）。
    /// 兼容旧配置；新字段以 WebSearchMode 为准，两者写入时同步。</summary>
    public bool EnableWebSearch { get; set; } = false;

    /// <summary>联网搜索模式：off / normal / deep（持久化）。
    /// 空 = 旧配置未写过此字段，启动时按 EnableWebSearch 迁移为 normal/off。</summary>
    public string WebSearchMode { get; set; } = "";

    /// <summary>解析有效联网模式（兼容旧 EnableWebSearch 布尔字段）。</summary>
    public string ResolveWebSearchMode()
    {
        var m = (WebSearchMode ?? "").Trim().ToLowerInvariant();
        return m switch
        {
            "deep" => "deep",
            "normal" => "normal",
            "off" => "off",
            _ => EnableWebSearch ? "normal" : "off",
        };
    }

    /// <summary>写回联网模式，并同步旧布尔字段。</summary>
    public void SetWebSearchMode(string mode)
    {
        var m = (mode ?? "off").Trim().ToLowerInvariant();
        if (m is not ("off" or "normal" or "deep"))
        {
            m = "off";
        }
        WebSearchMode = m;
        EnableWebSearch = m is "normal" or "deep";
    }

    /// <summary>对话页右侧协同抽屉（出处 / 创作物工作台）的宽度（像素，持久化）。
    /// 用户拖动分隔条改变宽度后落盘，下次启动自动还原；有效区间 240~720，
    /// 越界值（手改配置文件）由 ChatViewModel 读取时钳制。</summary>
    public double ChatDrawerWidth { get; set; } = 380;

    /// <summary>对话页勾选的知识库集合名（持久化，重启后恢复勾选）。
    /// 空 = 未记录过，首次加载维持原行为（默认勾选 default）。</summary>
    public List<string> LastChatCollections { get; set; } = new();

    /// <summary>搜索页历史搜索词列表（持久化，重启后保留；最新在前，上限 20 条）。</summary>
    public List<string> SearchHistory { get; set; } = new();

    /// <summary>联网来源「官方域名」徽章的权威域名表。静态共享：SourceRef 的徽章是
    /// 反序列化后计算的纯展示属性、无 DI 上下文，故挂在 AppSettings 类级别；
    /// 默认仅台达官网，可按需在启动时扩展。</summary>
    public static HashSet<string> AuthoritativeWebDomains { get; } = new(StringComparer.OrdinalIgnoreCase)
    {
        "deltaww.com",
        "delta.com",
    };

    // ===== 仅前端配置（WPF 客户端独有，不推送后端） =====
    /// <summary>监控目录列表（对应 DOC2MIND_WATCH_PATHS，逗号分隔注入）。</summary>
    public List<string> WatchPaths { get; set; } = new();
    /// <summary>监控防抖秒数（对应 DOC2MIND_WATCH_DEBOUNCE_SECONDS）。</summary>
    public double WatchDebounceSeconds { get; set; } = 5.0;

    // ===== 仅前端配置 — 用户记忆 =====
    /// <summary>是否启用跨会话用户记忆（MEMORY.md 模式）。</summary>
    public bool MemoryEnabled { get; set; } = true;
    /// <summary>是否启用自动记忆提取（对话结束后异步提取关键事实）。</summary>
    public bool MemoryAutoExtract { get; set; } = true;
    /// <summary>用户记忆容量上限（字符数），与 Hermes Agent 的 MEMORY.md 对齐。</summary>
    public int MemoryMaxChars { get; set; } = 2200;

    // ===== 仅前端配置 — 知识库自动整理（curate） =====
    /// <summary>是否启用「入库时自动跑 AI 整理」（对应后端 DOC2MIND_AUTO_CURATE_ON_INGEST）。
    /// 启用时：每次 ingest 后自动跑 enrich + 可选 categorize + extract（图谱实体）；
    /// 关闭时：入库后只写元数据，不调 LLM（enrich 不跑），用户可在质量看板或 Agent 主动跑 curate。
    /// 风险：dedup / consolidate 永不自动跑（必须 dry_run=true 预览 + 用户确认）。</summary>
    public bool AutoCurateOnIngest { get; set; } = true;

    // ===== 仅前端配置 — 启动选项 =====
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

    // ===== 用户自定义角色与主题 =====
    /// <summary>用户自定义的 Persona 列表（JSON 序列化字符串，运行时反序列化后合并到 AvailablePersonas）。</summary>
    public List<CustomPersonaEntry> CustomPersonas { get; set; } = new();

    /// <summary>用户自定义的 PPT 主题列表。</summary>
    public List<CustomThemeEntry> CustomThemes { get; set; } = new();

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
    /// 内存单例仍持明文供运行时使用。序列化副本，不改动 this 的字段值。
    /// LlmProfiles 同样加密各档案的 ApiKey（深拷贝后加密，不污染内存单例的明文）。</summary>
    public void Save()
    {
        EnsureConfigDir();
        var snapshot = (AppSettings)MemberwiseClone();
        snapshot.LlmApiKey = SecretProtector.Protect(LlmApiKey);
        snapshot.GithubToken = SecretProtector.Protect(GithubToken);
        // 档案 key 加密：先深拷贝每个档案，只对副本加密，内存单例保持明文供运行时使用
        snapshot.LlmProfiles = LlmProfiles?.Select(p =>
        {
            var clone = p.Clone();
            clone.ApiKey = SecretProtector.Protect(clone.ApiKey);
            return clone;
        }).ToList() ?? new List<Models.LlmProfile>();
        var json = JsonSerializer.Serialize(snapshot, new JsonSerializerOptions
        {
            WriteIndented = true,
            PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        });
        File.WriteAllText(ConfigPath, json);
    }
}
