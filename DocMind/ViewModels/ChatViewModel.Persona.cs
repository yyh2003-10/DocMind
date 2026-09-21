using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Documents;
using System.Windows.Input;
using System.Windows.Media;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;
using Markdig;
using Markdig.Wpf;

namespace DocMind.ViewModels;

/// <summary>办公角色人设选项。</summary>
public sealed record PersonaOption(string Id, string DisplayName, string Icon, string Description, IReadOnlyList<string>? QuickQuestions = null)
{
    public override string ToString() => DisplayName;

    /// <summary>是否为用户自定义角色（ID 以 custom_ 开头）。</summary>
    public bool IsCustom => Id.StartsWith("custom_", StringComparison.OrdinalIgnoreCase);

    /// <summary>是否有专属快捷问题。</summary>
    public bool HasQuickQuestions => QuickQuestions is { Count: > 0 };
}

public partial class ChatViewModel : ViewModelBase
{
/// <summary>可选的办公与创作角色人设列表。</summary>
    public IReadOnlyList<PersonaOption> AvailablePersonas { get; } = new List<PersonaOption>
    {
        // ── 通用办公 ──
        new("office", "💼 知识办公助手", "💼",
            "提炼核心结论、梳理 Action Items 待办清单与标准公文润色",
            new[] {
                "请将上述内容提炼为核心结论与 Action Items 待办清单",
                "请帮我把这份草稿按企业公文规范进行润色重构",
                "请用 3-5 句话概括上述文档的核心要点",
            }),
        new("simplify", "🪶 极简表达翻译官", "🪶",
            "把复杂专业内容转化为通俗易懂、老少皆宜的极简表达与可视化摘要",
            new[] {
                "请用小学生都能听懂的语言解释上述技术概念",
                "请用一张类比图把复杂流程可视化为生活场景",
                "请用 100 字以内精炼概括上述内容",
            }),
        new("brainstorm", "💡 创新方案顾问", "💡",
            "头脑风暴、SWOT 矩阵分析、多方案多维度对比表格与排期落地规划",
            new[] {
                "请对上述主题进行头脑风暴，列出至少 10 个创新方案",
                "请做一份 SWOT 分析并给出战略建议",
                "请对比至少 3 种方案的优缺点并给出推荐",
            }),
        // ── 创作输出 ──
        new("ppt", "📊 PPT 演示架构师", "📊",
            "依托知识库生成 Marp 语法幻灯片、提炼分页要点与演讲备注",
            new[] {
                "请制作一份 10 页的专业汇报 PPT（含封面、目录、核心论点、演讲备注）",
                "请为上述内容设计一份演示大纲，标注每页的视觉风格建议",
                "请把上述方案转化为一份 8 页的客户提案 PPT",
            }),
        new("doc", "📄 资深研报公文专家", "📄",
            "深度技术方案论证、公文撰写、行业研报与规范排版",
            new[] {
                "请撰写一份结构严谨的技术方案论证报告",
                "请按照行业研报规范撰写一份深度分析报告",
                "请帮我润色这份公文，确保格式与用语符合行政规范",
            }),
        new("lesson", "🎓 课程教案设计师", "🎓",
            "教学大纲设计、课时环节编排、重难点剖析与随堂测验",
            new[] {
                "请设计一份 2 课时的教学大纲（含教学目标、重难点、教学过程）",
                "请为上述知识点设计 5 道随堂测验题",
                "请把上述内容拆解为 4 个教学环节并标注时间分配",
            }),
        new("web", "🌐 交互看板工程师", "🌐",
            "生成自包含 HTML5 响应式知识总结看板与卡片",
            new[] {
                "请生成一个自包含的 HTML5 交互式知识总结看板",
                "请用卡片式布局设计一个数据可视化看板",
                "请生成一个带动态图表的项目进度看板页面",
            }),
        new("writer", "✍️ 创意文案大师", "✍️",
            "品牌故事撰写、营销文案创作、社媒内容策划与多平台适配",
            new[] {
                "请为上述产品撰写 3 版不同风格的营销文案",
                "请撰写一个品牌故事脚本（300 字以内）",
                "请为上述内容设计 5 个吸睛标题",
            }),
        new("content", "📱 社媒运营策划师", "📱",
            "小红书/抖音/微信公众号内容策划、爆款标题设计与多平台分发策略",
            new[] {
                "请为上述主题策划 5 条小红书笔记（含标题、正文、标签）",
                "请设计一个抖音短视频脚本（15-60 秒）",
                "请制定一份多平台内容分发策略表",
            }),
        // ── 数据与分析 ──
        new("table", "📑 商业数据分析师", "📑",
            "多维对比矩阵抽取、指标打分表与甘特排期规划",
            new[] {
                "请提取上述方案的关键指标并输出对比矩阵表格",
                "请设计一份多维度评分表并对各方案打分",
                "请用甘特图格式规划项目排期",
            }),
        new("analyst", "📈 数据洞察专家", "📈",
            "数据趋势解读、可视化图表建议、KPI 分析框架与业务洞察提炼",
            new[] {
                "请对上述数据进行趋势分析并给出可视化图表建议",
                "请设计一套 KPI 分析框架并标注关键指标",
                "请从数据中提炼 5 条可执行的业务洞察",
            }),
        new("finance", "💰 财务分析师", "💰",
            "财务报表解读、预算编制建议、投资回报分析与现金流预测",
            new[] {
                "请解读上述财务数据并给出投资建议",
                "请编制一份年度预算建议方案",
                "请做一份投资回报率(ROI)分析",
            }),
        // ── 技术与工程 ──
        new("architect", "🧠 资深系统架构师", "🧠",
            "系统设计模式选型、底层运行机制剖析、性能瓶颈评估与架构演进设计",
            new[] {
                "请对上述系统进行架构评审并给出优化建议",
                "请设计一套微服务架构方案并说明选型理由",
                "请评估上述架构的性能瓶颈并给出改进路线图",
            }),
        new("engineer", "🛠️ 资深研发工匠", "🛠️",
            "工业级代码实现、重构优化、异常边界防御与单元测试建议",
            new[] {
                "请帮我重构这段代码并附上重构理由",
                "请为上述函数编写单元测试用例",
                "请分析这段代码的边界异常并补充防御性代码",
            }),
        new("devops", "🔧 DevOps 运维专家", "🔧",
            "CI/CD 流水线设计、容器化部署方案、监控告警体系与故障应急响应",
            new[] {
                "请设计一套 CI/CD 流水线方案",
                "请制定容器化部署方案（Docker + K8s）",
                "请设计一套监控告警体系并定义告警阈值",
            }),
        new("security", "🔐 网络安全顾问", "🔐",
            "渗透测试报告、安全架构评审、漏洞分析与等保合规方案",
            new[] {
                "请对上述系统进行安全架构评审",
                "请生成一份渗透测试报告模板",
                "请分析潜在安全漏洞并给出修复建议",
            }),
        // ── 行业专家 ──
        new("medical", "🩺 医疗健康顾问", "🩺",
            "临床研究解读、药品/器械合规审查、医学文献综述与健康管理方案",
            new[] {
                "请解读上述临床研究数据并给出医学建议",
                "请撰写一份药品/器械合规审查报告",
                "请综述上述医学文献的核心发现",
            }),
        new("legal", "⚖️ 法务合规顾问", "⚖️",
            "合同条款审阅、合规风险排查、知识产权保护与法律条文解读",
            new[] {
                "请审阅上述合同条款并标注风险点",
                "请对上述业务进行合规风险排查",
                "请解读相关法律条文并给出合规建议",
            }),
        new("hr", "👥 人力资源专家", "👥",
            "招聘 JD 撰写、绩效考核方案设计、员工培训体系规划与劳动法合规",
            new[] {
                "请为上述岗位撰写一份招聘 JD",
                "请设计一套绩效考核方案（KPI + OKR）",
                "请规划一份员工培训体系",
            }),
        // ── 项目与管理 ──
        new("scrum", "🏃 敏捷项目教练", "🏃",
            "Sprint 规划、用户故事拆解、站会纪要生成与迭代复盘报告",
            new[] {
                "请为下一个 Sprint 制定规划并拆解用户故事",
                "请生成一份站会纪要模板",
                "请对本次迭代进行复盘并列出改进项",
            }),
        new("product", "🎯 产品经理", "🎯",
            "需求文档撰写、用户故事拆解、竞品分析报告与产品路线图规划",
            new[] {
                "请撰写一份 PRD 需求文档",
                "请对上述功能进行竞品分析",
                "请制定一份季度产品路线图",
            }),
        new("cs", "🤝 客户成功经理", "🤝",
            "客户健康度评估、续约方案设计、客户案例包装与满意度分析",
            new[] {
                "请评估客户健康度并给出挽留方案",
                "请包装一个客户成功案例",
                "请设计一份客户满意度调研问卷",
            }),
        // ── 研究与访谈 ──
        new("interviewer", "🎙️ 深度访谈策划师", "🎙️",
            "访谈提纲设计、追问链路规划、访谈稿整理与核心观点提炼",
            new[] {
                "请设计一份 30 分钟的深度访谈提纲",
                "请根据上述回答设计追问链路",
                "请从访谈稿中提炼核心观点与洞察",
            }),
        new("researcher", "🔬 学术研究员", "🔬",
            "文献综述撰写、研究方法论设计、实验数据分析与论文结构优化",
            new[] {
                "请撰写一份文献综述",
                "请设计一套研究方法论并论证可行性",
                "请优化论文的结构并给出修改建议",
            }),
        new("ux", "🎨 UX 设计师", "🎨",
            "用户调研报告、交互原型评审、可用性测试分析与设计系统规范",
            new[] {
                "请撰写一份用户调研分析报告",
                "请评审上述交互原型并给出改进建议",
                "请设计一份设计系统规范（色彩、字体、间距）",
            }),
    };

    private PersonaOption _selectedPersona;

    /// <summary>创作类人设集合（后端自动路由创作意图时使用）。
    /// 仅当后端回传的实际生效人设属于此集合时，才静默同步前端人设下拉框，
    /// 避免把用户手动选择的 architect/brainstorm/office 覆写为回传值。</summary>
    private static readonly HashSet<string> CreativePersonaIds = new() { "ppt", "doc", "lesson", "table", "web" };

    /// <summary>当前选中的办公角色人设。</summary>
    public PersonaOption SelectedPersona
    {
        get => _selectedPersona;
        set
        {
            if (SetProperty(ref _selectedPersona, value ?? AllPersonas.FirstOrDefault() ?? AvailablePersonas[0]))
            {
                StatusMessage = $"角色: {_selectedPersona.DisplayName}";
                OnPropertyChanged(nameof(PersonaQuickQuestions));
                OnPropertyChanged(nameof(HasPersonaQuickQuestions));
            }
        }
    }

    /// <summary>当前选中角色的专属快捷问题列表。</summary>
    public IReadOnlyList<string> PersonaQuickQuestions =>
        SelectedPersona?.QuickQuestions ?? Array.Empty<string>();

    /// <summary>当前角色是否有专属快捷问题。</summary>
    public bool HasPersonaQuickQuestions =>
        SelectedPersona?.HasQuickQuestions == true && !ShowEmptyGuide;

    // ==================== 自定义角色与主题管理 ====================

    private bool _isCustomManagerOpen;

    /// <summary>自定义角色/主题管理面板是否展开。</summary>
    public bool IsCustomManagerOpen
    {
        get => _isCustomManagerOpen;
        set => SetProperty(ref _isCustomManagerOpen, value);
    }

    private string _newPersonaName = string.Empty;
    public string NewPersonaName { get => _newPersonaName; set => SetProperty(ref _newPersonaName, value); }

    private string _newPersonaIcon = "🤖";
    public string NewPersonaIcon { get => _newPersonaIcon; set => SetProperty(ref _newPersonaIcon, value); }

    private string _newPersonaDescription = string.Empty;
    public string NewPersonaDescription { get => _newPersonaDescription; set => SetProperty(ref _newPersonaDescription, value); }

    private string _newThemeDisplayName = string.Empty;
    public string NewThemeDisplayName { get => _newThemeDisplayName; set => SetProperty(ref _newThemeDisplayName, value); }

    private string _newThemeIcon = "🎨";
    public string NewThemeIcon { get => _newThemeIcon; set => SetProperty(ref _newThemeIcon, value); }

    private string _newThemeDescription = string.Empty;
    public string NewThemeDescription { get => _newThemeDescription; set => SetProperty(ref _newThemeDescription, value); }

    private string _newThemePrimaryHex = "#3B82F6";
    public string NewThemePrimaryHex { get => _newThemePrimaryHex; set => SetProperty(ref _newThemePrimaryHex, value); }

    private string _newThemeBgHex = "#F8FAFC";
    public string NewThemeBgHex { get => _newThemeBgHex; set => SetProperty(ref _newThemeBgHex, value); }

    /// <summary>当前展示的角色列表（内置 + 自定义）。</summary>
    public ObservableCollection<PersonaOption> AllPersonas { get; } = new();

    /// <summary>当前展示的主题列表（内置 + 自定义）。</summary>
    public ObservableCollection<PptThemeOption> AllThemes { get; } = new();

    [RelayCommand]
    private void OpenCustomManager() => IsCustomManagerOpen = true;

    [RelayCommand]
    private void CloseCustomManager() => IsCustomManagerOpen = false;

    /// <summary>添加自定义角色。</summary>
    [RelayCommand]
    private void AddCustomPersona()
    {
        if (string.IsNullOrWhiteSpace(NewPersonaName)) return;
        var entry = new CustomPersonaEntry
        {
            Name = NewPersonaName.Trim(),
            Icon = string.IsNullOrWhiteSpace(NewPersonaIcon) ? "🤖" : NewPersonaIcon.Trim(),
            Description = NewPersonaDescription.Trim(),
        };
        _appSettings.CustomPersonas.Add(entry);
        try { _appSettings.Save(); } catch { }
        AllPersonas.Add(new PersonaOption($"custom_{entry.Id}", $"{entry.Icon} {entry.Name}", entry.Icon, entry.Description));
        NewPersonaName = string.Empty;
        NewPersonaIcon = "🤖";
        NewPersonaDescription = string.Empty;
        StatusMessage = $"已添加自定义角色: {entry.Name}（描述将作为系统提示词生效）";
    }

    /// <summary>删除自定义角色。</summary>
    [RelayCommand]
    private void RemoveCustomPersona(string? id)
    {
        if (string.IsNullOrWhiteSpace(id)) return;
        var entry = _appSettings.CustomPersonas.FirstOrDefault(p => $"custom_{p.Id}" == id);
        if (entry == null) return;
        _appSettings.CustomPersonas.Remove(entry);
        try { _appSettings.Save(); } catch { }
        var item = AllPersonas.FirstOrDefault(p => p.Id == id);
        if (item != null) AllPersonas.Remove(item);
        StatusMessage = $"已删除自定义角色: {entry.Name}";
    }

    /// <summary>添加自定义 PPT 主题。</summary>
    [RelayCommand]
    private void AddCustomTheme()
    {
        if (string.IsNullOrWhiteSpace(NewThemeDisplayName)) return;
        var entry = new CustomThemeEntry
        {
            DisplayName = NewThemeDisplayName.Trim(),
            Icon = string.IsNullOrWhiteSpace(NewThemeIcon) ? "🎨" : NewThemeIcon.Trim(),
            Description = NewThemeDescription.Trim(),
            PrimaryHex = string.IsNullOrWhiteSpace(NewThemePrimaryHex) ? "#3B82F6" : NewThemePrimaryHex.Trim(),
            BgHex = string.IsNullOrWhiteSpace(NewThemeBgHex) ? "#F8FAFC" : NewThemeBgHex.Trim(),
        };
        _appSettings.CustomThemes.Add(entry);
        try { _appSettings.Save(); } catch { }
        AllThemes.Add(new PptThemeOption($"custom_{entry.Id}", $"{entry.Icon} {entry.DisplayName}", entry.Icon, entry.Description, entry.PrimaryHex, entry.BgHex));
        NewThemeDisplayName = string.Empty;
        NewThemeIcon = "🎨";
        NewThemeDescription = string.Empty;
        NewThemePrimaryHex = "#3B82F6";
        NewThemeBgHex = "#F8FAFC";
        StatusMessage = $"已添加自定义主题: {entry.DisplayName}";
    }

    /// <summary>删除自定义 PPT 主题。</summary>
    [RelayCommand]
    private void RemoveCustomTheme(string? id)
    {
        if (string.IsNullOrWhiteSpace(id)) return;
        var entry = _appSettings.CustomThemes.FirstOrDefault(t => $"custom_{t.Id}" == id);
        if (entry == null) return;
        _appSettings.CustomThemes.Remove(entry);
        try { _appSettings.Save(); } catch { }
        var item = AllThemes.FirstOrDefault(t => t.Id == id);
        if (item != null) AllThemes.Remove(item);
        StatusMessage = $"已删除自定义主题: {entry.DisplayName}";
    }

    /// <summary>合并内置 + 自定义角色/主题到展示列表，并确保下拉框数据同步。</summary>
    private void MergeCustomItems()
    {
        AllPersonas.Clear();
        foreach (var p in AvailablePersonas) AllPersonas.Add(p);
        foreach (var c in _appSettings.CustomPersonas)
            AllPersonas.Add(new PersonaOption($"custom_{c.Id}", $"{c.Icon} {c.Name}", c.Icon, c.Description));

        AllThemes.Clear();
        foreach (var t in AvailableThemes) AllThemes.Add(t);
        foreach (var c in _appSettings.CustomThemes)
            AllThemes.Add(new PptThemeOption($"custom_{c.Id}", $"{c.Icon} {c.DisplayName}", c.Icon, c.Description, c.PrimaryHex, c.BgHex));
    }

    // ==================== 智能推荐：根据对话内容匹配最佳角色与主题 ====================

    private PersonaOption? _recommendedPersona;
    private PptThemeOption? _recommendedTheme;
    private string _recommendationReason = string.Empty;
    private bool _showRecommendation;
    private bool _isAnalyzingRecommendation;

    /// <summary>推荐的最佳角色。</summary>
    public PersonaOption? RecommendedPersona
    {
        get => _recommendedPersona;
        set => SetProperty(ref _recommendedPersona, value);
    }

    /// <summary>推荐的最佳主题。</summary>
    public PptThemeOption? RecommendedTheme
    {
        get => _recommendedTheme;
        set => SetProperty(ref _recommendedTheme, value);
    }

    /// <summary>推荐理由（用于提示文案）。</summary>
    public string RecommendationReason
    {
        get => _recommendationReason;
        set => SetProperty(ref _recommendationReason, value);
    }

    /// <summary>是否显示推荐提示条。</summary>
    public bool ShowRecommendation
    {
        get => _showRecommendation;
        set => SetProperty(ref _showRecommendation, value);
    }

    /// <summary>是否正在分析推荐中。</summary>
    public bool IsAnalyzingRecommendation
    {
        get => _isAnalyzingRecommendation;
        set => SetProperty(ref _isAnalyzingRecommendation, value);
    }

    /// <summary>应用推荐的角色。</summary>
    [RelayCommand]
    private void ApplyRecommendedPersona()
    {
        if (RecommendedPersona != null)
        {
            SelectedPersona = RecommendedPersona;
            StatusMessage = $"✅ 已切换到推荐角色: {RecommendedPersona.DisplayName}";
        }
    }

    /// <summary>应用推荐的主题。</summary>
    [RelayCommand]
    private void ApplyRecommendedTheme()
    {
        if (RecommendedTheme != null)
        {
            SelectedTheme = RecommendedTheme;
            StatusMessage = $"✅ 已切换到推荐主题: {RecommendedTheme.DisplayName}";
        }
    }

    /// <summary>一键应用全部推荐（角色 + 主题）。</summary>
    [RelayCommand]
    private void ApplyAllRecommendations()
    {
        ApplyRecommendedPersona();
        ApplyRecommendedTheme();
        ShowRecommendation = false;
    }

    /// <summary>忽略推荐。</summary>
    [RelayCommand]
    private void DismissRecommendation() => ShowRecommendation = false;

    /// <summary>根据最近的用户消息内容，智能匹配最佳 Persona 和主题。</summary>
    private void AnalyzeAndRecommend()
    {
        // 只在有消息且不在忙碌时分析
        if (IsBusy || Messages.Count == 0) return;

        // 收集最近 3 条用户消息的文本
        var recentUserText = string.Join(" ", Messages
            .Where(m => m.Role == "user")
            .TakeLast(3)
            .Select(m => m.Content));

        if (string.IsNullOrWhiteSpace(recentUserText)) return;

        IsAnalyzingRecommendation = true;

        try
        {
            var lowerText = recentUserText.ToLowerInvariant();

            // ── Persona 关键词匹配 ──
            var personaMatches = new List<(PersonaOption persona, int score, string reason)>();

            foreach (var p in AllPersonas)
            {
                var score = 0;
                var reason = "";
                var desc = (p.Description ?? "").ToLowerInvariant();
                var name = (p.DisplayName ?? "").ToLowerInvariant();

                // PPT / 演示文稿
                if (MatchesAny(lowerText, "ppt", "演示文稿", "幻灯片", "slide", "放映", "演示", "演讲"))
                {
                    if (p.Id == "ppt") { score += 10; reason = "检测到 PPT 演示文稿需求"; }
                }
                // 研报 / 公文
                else if (MatchesAny(lowerText, "研报", "报告", "公文", "方案", "论文", "论证", "分析报告", "调研报告"))
                {
                    if (p.Id == "doc") { score += 10; reason = "检测到研报/公文撰写需求"; }
                }
                // 教案 / 教学
                else if (MatchesAny(lowerText, "教案", "教学", "课程", "课时", "教育", "培训课", "备课", "教学大纲"))
                {
                    if (p.Id == "lesson") { score += 10; reason = "检测到教学/教案设计需求"; }
                }
                // 数据 / 表格
                else if (MatchesAny(lowerText, "表格", "数据", "对比", "指标", "矩阵", "排序", "对比表", "评分"))
                {
                    if (p.Id == "table") { score += 10; reason = "检测到数据对比分析需求"; }
                }
                // 看板 / HTML
                else if (MatchesAny(lowerText, "看板", "html", "网页", "交互", "dashboard", "可视化看板"))
                {
                    if (p.Id == "web") { score += 10; reason = "检测到交互式看板生成需求"; }
                }
                // 架构 / 系统设计
                else if (MatchesAny(lowerText, "架构", "系统设计", "设计模式", "微服务", "分布式", "性能优化", "高并发"))
                {
                    if (p.Id == "architect") { score += 10; reason = "检测到系统架构设计需求"; }
                }
                // 代码 / 编程
                else if (MatchesAny(lowerText, "代码", "编程", "bug", "重构", "单元测试", "调试", "实现", "函数", "api"))
                {
                    if (p.Id == "engineer") { score += 10; reason = "检测到代码开发/调试需求"; }
                }
                // 头脑风暴
                else if (MatchesAny(lowerText, "头脑风暴", "创意", "swot", "方案对比", "brainstorm"))
                {
                    if (p.Id == "brainstorm") { score += 10; reason = "检测到头脑风暴/方案对比需求"; }
                }
                // 文案 / 营销
                else if (MatchesAny(lowerText, "文案", "营销", "推广", "广告", "品牌", "社交媒体", "公众号"))
                {
                    if (p.Id == "writer" || p.Id == "content") { score += 10; reason = "检测到营销文案/内容创作需求"; }
                }
                // 数据分析
                else if (MatchesAny(lowerText, "数据分析", "趋势", "kpi", "图表", "可视化", "统计"))
                {
                    if (p.Id == "analyst") { score += 10; reason = "检测到数据分析/洞察需求"; }
                }
                // 财务
                else if (MatchesAny(lowerText, "财务", "预算", "投资", "回报率", "现金流", "报表分析"))
                {
                    if (p.Id == "finance") { score += 10; reason = "检测到财务分析需求"; }
                }
                // DevOps / 运维
                else if (MatchesAny(lowerText, "运维", "部署", "ci/cd", "docker", "kubernetes", "监控", "告警", "devops"))
                {
                    if (p.Id == "devops") { score += 10; reason = "检测到 DevOps/运维需求"; }
                }
                // 安全
                else if (MatchesAny(lowerText, "安全", "渗透", "漏洞", "等保", "合规审查", "网络安全"))
                {
                    if (p.Id == "security") { score += 10; reason = "检测到网络安全需求"; }
                }
                // 医疗
                else if (MatchesAny(lowerText, "医疗", "临床", "药品", "医学", "患者", "诊断", "治疗方案"))
                {
                    if (p.Id == "medical") { score += 10; reason = "检测到医疗健康需求"; }
                }
                // 法务
                else if (MatchesAny(lowerText, "合同", "法律", "法务", "知识产权", "专利", "侵权", "合规"))
                {
                    if (p.Id == "legal") { score += 10; reason = "检测到法务合规需求"; }
                }
                // HR
                else if (MatchesAny(lowerText, "招聘", "绩效", "培训体系", "人力资源", "员工", "岗位"))
                {
                    if (p.Id == "hr") { score += 10; reason = "检测到人力资源需求"; }
                }
                // 产品经理
                else if (MatchesAny(lowerText, "需求文档", "用户故事", "竞品", "产品路线", "prd", "产品设计"))
                {
                    if (p.Id == "product") { score += 10; reason = "检测到产品管理需求"; }
                }
                // 客户成功
                else if (MatchesAny(lowerText, "客户", "续约", "满意度", "nps", "客户成功"))
                {
                    if (p.Id == "cs") { score += 10; reason = "检测到客户成功管理需求"; }
                }
                // 访谈
                else if (MatchesAny(lowerText, "访谈", "调研", "采访", "问答", "访谈提纲"))
                {
                    if (p.Id == "interviewer") { score += 10; reason = "检测到访谈策划需求"; }
                }
                // 学术
                else if (MatchesAny(lowerText, "文献综述", "研究方法", "实验", "论文", "学术", "期刊"))
                {
                    if (p.Id == "researcher") { score += 10; reason = "检测到学术研究需求"; }
                }
                // UX
                else if (MatchesAny(lowerText, "ux", "ui", "交互设计", "可用性", "用户体验", "原型", "设计系统"))
                {
                    if (p.Id == "ux") { score += 10; reason = "检测到 UX 设计需求"; }
                }
                // 极简
                else if (MatchesAny(lowerText, "简单", "通俗", "简洁", "简述", "概括", "精简", "通俗易懂"))
                {
                    if (p.Id == "simplify") { score += 10; reason = "检测到简洁表达需求"; }
                }
                // 敏捷
                else if (MatchesAny(lowerText, "sprint", "站会", "迭代", "敏捷", "用户故事", "回顾"))
                {
                    if (p.Id == "scrum") { score += 10; reason = "检测到敏捷项目管理需求"; }
                }

                if (score > 0)
                    personaMatches.Add((p, score, reason));
            }

            var bestPersona = personaMatches.OrderByDescending(x => x.score).FirstOrDefault();
            RecommendedPersona = bestPersona.score > 0 ? bestPersona.persona : null;

            // ── 主题关键词匹配 ──
            var themeMatches = new List<(PptThemeOption theme, int score, string reason)>();

            foreach (var t in AllThemes)
            {
                var score = 0;
                var reason = "";
                var desc = (t.Description ?? "").ToLowerInvariant();
                var name = (t.DisplayName ?? "").ToLowerInvariant();

                if (MatchesAny(lowerText, "科技", "技术", "架构", "系统", "ai", "人工智能", "数字化"))
                {
                    if (t.Id == "tech_blue" || t.Id == "modern_purple" || t.Id == "cyber_neon")
                    { score += 8; reason = "科技/技术主题匹配"; }
                }
                else if (MatchesAny(lowerText, "教育", "课程", "教学", "培训", "学习"))
                {
                    if (t.Id == "emerald_green" || t.Id == "scholar_cream")
                    { score += 8; reason = "教育/学术主题匹配"; }
                }
                else if (MatchesAny(lowerText, "金融", "投资", "财务", "银行", "证券"))
                {
                    if (t.Id == "golden_luxury" || t.Id == "deep_wine")
                    { score += 8; reason = "金融/高端主题匹配"; }
                }
                else if (MatchesAny(lowerText, "医疗", "健康", "临床", "医学", "生命科学"))
                {
                    if (t.Id == "medical_calm")
                    { score += 8; reason = "医疗健康主题匹配"; }
                }
                else if (MatchesAny(lowerText, "政府", "党建", "公文", "公共服务", "政策"))
                {
                    if (t.Id == "gov_red")
                    { score += 8; reason = "政府/公共主题匹配"; }
                }
                else if (MatchesAny(lowerText, "营销", "品牌", "推广", "活动", "产品发布"))
                {
                    if (t.Id == "warm_orange" || t.Id == "rose_pink")
                    { score += 8; reason = "营销/品牌主题匹配"; }
                }
                else if (MatchesAny(lowerText, "环保", "自然", "可持续", "农业", "生态"))
                {
                    if (t.Id == "forest_deep" || t.Id == "ocean_turquoise")
                    { score += 8; reason = "自然/环保主题匹配"; }
                }
                else if (MatchesAny(lowerText, "极简", "简洁", "简约", "冷淡风", "学术"))
                {
                    if (t.Id == "nordic_ice" || t.Id == "dark_elegant")
                    { score += 8; reason = "极简/冷淡主题匹配"; }
                }
                else if (MatchesAny(lowerText, "游戏", "电竞", "元宇宙", "赛博", "科幻"))
                {
                    if (t.Id == "cyber_neon")
                    { score += 8; reason = "科技前沿主题匹配"; }
                }
                else if (MatchesAny(lowerText, "汇报", "总结", "年终", "述职", "评审"))
                {
                    if (t.Id == "tech_blue" || t.Id == "sunset_gradient")
                    { score += 8; reason = "商务汇报主题匹配"; }
                }

                if (score > 0)
                    themeMatches.Add((t, score, reason));
            }

            var bestTheme = themeMatches.OrderByDescending(x => x.score).FirstOrDefault();
            RecommendedTheme = bestTheme.score > 0 ? bestTheme.theme : null;

            // ── 生成推荐理由 ──
            if (RecommendedPersona != null || RecommendedTheme != null)
            {
                var reasons = new List<string>();
                if (bestPersona.score > 0) reasons.Add(bestPersona.reason);
                if (bestTheme.score > 0) reasons.Add(bestTheme.reason);
                RecommendationReason = string.Join("，", reasons);
                ShowRecommendation = true;
            }
            else
            {
                ShowRecommendation = false;
            }
        }
        finally
        {
            IsAnalyzingRecommendation = false;
        }
    }

}
