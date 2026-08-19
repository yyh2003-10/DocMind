namespace DocMind.Models;

/// <summary>
/// 主流大模型服务商预设模版。
/// </summary>
public sealed record LlmPreset(
    string Id,
    string DisplayName,
    string Provider,
    string? BaseUrl,
    string DefaultModel,
    IReadOnlyList<string> RecommendedModels,
    string Description,
    bool RequiresApiKey = true,
    string? ConsoleUrl = null
)
{
    public override string ToString() => DisplayName;
}

public static class LlmPresetCatalog
{
    public static readonly IReadOnlyList<LlmPreset> All = new List<LlmPreset>
    {
        new(
            "custom",
            "🛠️ 自定义服务商 (OpenAI 兼容)",
            "openai",
            null,
            "",
            new[] { "gpt-4o-mini", "deepseek-chat", "qwen-plus" },
            "自行填写 API Key、Base URL 与 Model 名称"
        ),
        new(
            "deepseek",
            "🚀 DeepSeek 官方 API",
            "openai",
            "https://api.deepseek.com/v1",
            "deepseek-chat",
            new[] { "deepseek-chat", "deepseek-reasoner" },
            "国内领先的大语言模型，性价比与代码/推理能力极强",
            ConsoleUrl: "https://platform.deepseek.com/api_keys"
        ),
        new(
            "siliconflow",
            "⚡ 硅基流动 SiliconFlow",
            "openai",
            "https://api.siliconflow.cn/v1",
            "deepseek-ai/DeepSeek-V3",
            new[] { "deepseek-ai/DeepSeek-V3", "deepseek-ai/DeepSeek-R1", "Qwen/Qwen2.5-72B-Instruct", "meta-llama/Meta-Llama-3.1-8B-Instruct" },
            "多模型高速推理聚合平台，支持 DeepSeek-V3/R1 满血版",
            ConsoleUrl: "https://cloud.siliconflow.cn/account/ak"
        ),
        new(
            "qwen",
            "🟣 阿里云百炼 通义千问 (DashScope)",
            "openai",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen-plus",
            new[] { "qwen-plus", "qwen-turbo", "qwen-max", "qwen-long" },
            "阿里通义千问系列，稳定性与中文综合理解优异",
            ConsoleUrl: "https://bailian.console.aliyun.com/?apiKey=1"
        ),
        new(
            "zhipu",
            "🧠 智谱清言 BigModel (GLM-4)",
            "openai",
            "https://open.bigmodel.cn/api/paas/v4/",
            "glm-4-flash",
            new[] { "glm-4-flash", "glm-4-plus", "glm-4-long", "glm-4-air" },
            "清华智谱 GLM-4 认知大模型，glm-4-flash 免费极速调用",
            ConsoleUrl: "https://open.bigmodel.cn/usercenter/apikeys"
        ),
        new(
            "moonshot",
            "🌙 月之暗面 Kimi (Moonshot)",
            "openai",
            "https://api.moonshot.cn/v1",
            "moonshot-v1-8k",
            new[] { "moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k" },
            "长文本对话与文档研报理解专家",
            ConsoleUrl: "https://platform.moonshot.cn/console/api-keys"
        ),
        new(
            "ollama",
            "🦙 本地 Ollama (完全离线)",
            "ollama",
            "http://localhost:11434",
            "qwen2.5:7b",
            new[] { "qwen2.5:7b", "deepseek-r1:8b", "llama3.2:3b", "gemma2:9b" },
            "本地离线大模型引擎，完全不依赖外网与 API Key",
            RequiresApiKey: false,
            ConsoleUrl: "https://ollama.com/library"
        ),
        new(
            "openai",
            "🌐 OpenAI 官方",
            "openai",
            "https://api.openai.com/v1",
            "gpt-4o-mini",
            new[] { "gpt-4o-mini", "gpt-4o", "o3-mini" },
            "OpenAI 原生接口",
            ConsoleUrl: "https://platform.openai.com/api-keys"
        ),
    };
}

