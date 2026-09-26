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

public partial class ChatViewModel : ViewModelBase
{
    private SourceRef? _selectedSource;
    private bool _isSourceDrawerOpen;
    private double _sourceDrawerWidth;
    private ArtifactItem? _selectedArtifact;
    private int _currentSlideIndex;
    private bool _isArtifactMode;

    /// <summary>当前选中的创作物交付物（供右侧创作画布展示）。</summary>
    public ArtifactItem? SelectedArtifact
    {
        get => _selectedArtifact;
        set
        {
            if (SetProperty(ref _selectedArtifact, value))
            {
                OnPropertyChanged(nameof(HasSelectedArtifact));
                OnPropertyChanged(nameof(SelectedArtifactTitle));
                OnPropertyChanged(nameof(SelectedSlide));
                OnPropertyChanged(nameof(HasSelectedSlide));
                OnPropertyChanged(nameof(SlideCountText));
                OnPropertyChanged(nameof(CanPrevSlide));
                OnPropertyChanged(nameof(CanNextSlide));
                OnPropertyChanged(nameof(IsPptArtifact));
                OnPropertyChanged(nameof(IsDocArtifact));
                OnPropertyChanged(nameof(IsExcelArtifact));
                OnPropertyChanged(nameof(IsHtmlArtifact));

                // 统一同步入口：无论从消息卡片还是横排清单切换创作物，
                // 都保证页码归位、配色主题与该 artifact 自带的 theme 对齐。
                CurrentSlideIndex = 0;
                if (value != null && !string.IsNullOrWhiteSpace(value.Theme))
                {
                    var matchedTheme = AvailableThemes.FirstOrDefault(t => string.Equals(t.Id, value.Theme, StringComparison.OrdinalIgnoreCase));
                    if (matchedTheme != null) SelectedTheme = matchedTheme;
                }
            }
        }
    }

    public bool HasSelectedArtifact => SelectedArtifact != null;

    // ==================== 本会话创作物清单（工作台横排管理台） ====================
    // 一次会话常产出多个交付物（PPT + 研报 + 看板），只预览单个 artifact 会让用户在
    // 消息流里来回翻找。这里把本会话全部 artifact 聚合成清单，支持在工作台内直接切换。
    /// <summary>本会话已产出的全部创作物。</summary>
    public ObservableCollection<ArtifactItem> SessionArtifacts { get; } = new();

    /// <summary>本会话是否存在多个创作物（决定横排清单是否显示）。</summary>
    public bool HasMultipleArtifacts => SessionArtifacts.Count > 1;

    /// <summary>扫描全部消息，重建本会话创作物清单。</summary>
    private void RefreshSessionArtifacts()
    {
        SessionArtifacts.Clear();
        foreach (var m in Messages)
        {
            if (m.Artifact != null && !SessionArtifacts.Contains(m.Artifact))
            {
                SessionArtifacts.Add(m.Artifact);
            }
        }
        OnPropertyChanged(nameof(HasMultipleArtifacts));
    }
    public string SelectedArtifactTitle => SelectedArtifact?.Title ?? "创作交付物";
    public bool IsPptArtifact => SelectedArtifact?.IsPpt == true;
    public bool IsDocArtifact => SelectedArtifact?.IsDoc == true;
    public bool IsExcelArtifact => SelectedArtifact?.IsExcel == true;
    public bool IsHtmlArtifact => SelectedArtifact?.IsHtml == true;

    /// <summary>当前正在预览的幻灯片页索引（从 0 开始）。</summary>
    public int CurrentSlideIndex
    {
        get => _currentSlideIndex;
        set
        {
            if (SetProperty(ref _currentSlideIndex, value))
            {
                OnPropertyChanged(nameof(SelectedSlide));
                OnPropertyChanged(nameof(HasSelectedSlide));
                OnPropertyChanged(nameof(SlideCountText));
                OnPropertyChanged(nameof(CanPrevSlide));
                OnPropertyChanged(nameof(CanNextSlide));
            }
        }
    }

    /// <summary>当前选中的幻灯片页。</summary>
    public SlideItem? SelectedSlide =>
        SelectedArtifact?.Slides is { Count: > 0 } slides && CurrentSlideIndex >= 0 && CurrentSlideIndex < slides.Count
            ? slides[CurrentSlideIndex]
            : null;

    /// <summary>是否存在可预览的当前幻灯片页（无则预览卡显示空状态提示）。</summary>
    public bool HasSelectedSlide => SelectedSlide != null;

            /// <summary>幻灯片页码文案（如 "1 / 8"）。</summary>
    public string SlideCountText => SelectedArtifact?.Slides is { Count: > 0 } slides
        ? $"{CurrentSlideIndex + 1} / {slides.Count}"
        : "0 / 0";

    public bool CanPrevSlide => CurrentSlideIndex > 0;
    public bool CanNextSlide => SelectedArtifact?.Slides is { Count: > 0 } slides && CurrentSlideIndex < slides.Count - 1;

    /// <summary>抽屉是否处于创作物工作台模式（false 为原著切片模式）。</summary>
    public bool IsArtifactMode
    {
        get => _isArtifactMode;
        set => SetProperty(ref _isArtifactMode, value);
    }

    /// <summary>当前选中的引用来源（供右侧协同抽屉预览）。</summary>
    public SourceRef? SelectedSource
    {
        get => _selectedSource;
        set
        {
            if (SetProperty(ref _selectedSource, value))
            {
                OnPropertyChanged(nameof(HasSelectedSource));
                OnPropertyChanged(nameof(SelectedSourceTitle));
                OnPropertyChanged(nameof(SelectedSourceSnippet));
                OnPropertyChanged(nameof(SelectedSourceAnswerSupport));
                OnPropertyChanged(nameof(HasSelectedSourceAnswerSupport));
            }
        }
    }

    public bool HasSelectedSource => SelectedSource != null;
    public string SelectedSourceTitle => SelectedSource?.DisplayTitle ?? "(未命名来源)";
    public string SelectedSourceSnippet
    {
        get
        {
            var src = SelectedSource;
            if (src is null)
            {
                return "";
            }
            // 网页来源：正文没抓到且搜索摘要为空（占位/纯链接摘要已在后端过滤）时，
            // 给出诚实提示并引导打开原文，而不是展示一段像链接一样的垃圾文本。
            if (src.IsWebSource && !src.ContentFetched && string.IsNullOrWhiteSpace(src.Snippet))
            {
                return "⚠️ 未抓到该网页正文（页面可能需要 JS 渲染、需登录或禁止爬取）。搜索摘要不可用，请点击「🌐 在浏览器中打开」查看原文。";
            }
            return !string.IsNullOrWhiteSpace(src.Snippet)
                ? src.Snippet
                : "（该切片暂无全文预览或来自早期版本会话，可通过下方动作查看原文）";
        }
    }

    /// <summary>答案侧与该引用对应的表述：从助手消息中抽取含 [n] 角标的句子，供原文件对照。</summary>
    public string SelectedSourceAnswerSupport
    {
        get
        {
            var src = SelectedSource;
            if (src is null)
            {
                return string.Empty;
            }

            var marker = $"[{src.Index}]";
            for (var i = Messages.Count - 1; i >= 0; i--)
            {
                var msg = Messages[i];
                if (!string.Equals(msg.Role, "assistant", StringComparison.OrdinalIgnoreCase))
                {
                    continue;
                }
                if (msg.Sources is not { Count: > 0 })
                {
                    continue;
                }

                var content = msg.Content ?? string.Empty;
                var idx = content.IndexOf(marker, StringComparison.Ordinal);
                if (idx < 0)
                {
                    continue;
                }

                var start = idx;
                while (start > 0)
                {
                    var c = content[start - 1];
                    if (c is '。' or '！' or '？' or '\n' or '；')
                    {
                        break;
                    }
                    start--;
                }

                var end = idx + marker.Length;
                while (end < content.Length)
                {
                    var c = content[end];
                    if (c is '。' or '！' or '？' or '\n' or '；')
                    {
                        end++;
                        break;
                    }
                    end++;
                }

                var sentence = content[start..end].Trim();
                if (sentence.Length > 0)
                {
                    return sentence;
                }
            }

            return string.Empty;
        }
    }

    public bool HasSelectedSourceAnswerSupport => !string.IsNullOrWhiteSpace(SelectedSourceAnswerSupport);

    /// <summary>协同来源预览抽屉是否展开。</summary>
    public bool IsSourceDrawerOpen
    {
        get => _isSourceDrawerOpen;
        set => SetProperty(ref _isSourceDrawerOpen, value);
    }

    /// <summary>右侧协同抽屉允许的最小/最大宽度（像素），与 ChatView 抽屉 Border 的
    /// MinWidth/MaxWidth 约束保持一致（双重保险，避免两处区间漂移）。</summary>
    public const double MinSourceDrawerWidth = 240;
    public const double MaxSourceDrawerWidth = 1100;
    public const double DefaultSourceDrawerWidth = 380;

    /// <summary>右侧协同抽屉的宽度（像素）。用户拖动左边缘把手后由 ChatView 回写此处，
    /// 立即钳制到 240~720 并落盘到 AppSettings.ChatDrawerWidth，下次启动自动还原。</summary>
    public double SourceDrawerWidth
    {
        get => _sourceDrawerWidth;
        set
        {
            var clamped = System.Math.Clamp(value, MinSourceDrawerWidth, MaxSourceDrawerWidth);
            if (SetProperty(ref _sourceDrawerWidth, clamped))
            {
                // 仅更新内存快照：拖动过程中每帧都会走到这里，而 Save() 内含
                // DPAPI 加密（SecretProtector.Protect），逐帧调用会造成明显卡顿，
                // 故落盘交由 PersistSourceDrawerWidth 在拖动结束时执行一次。
                _appSettings.ChatDrawerWidth = clamped;
            }
        }
    }

    /// <summary>把当前抽屉宽度落盘到 appsettings.json。
    /// 由 ChatView 在拖动结束（Thumb.DragCompleted）时调用一次，避免拖动过程逐帧写盘。</summary>
    public void PersistSourceDrawerWidth()
    {
        _appSettings.ChatDrawerWidth = _sourceDrawerWidth;
        try
        {
            _appSettings.Save();
        }
        catch
        {
            // 落盘失败不阻断对话（与联网搜索开关一致：UI 状态优先）
        }
    }

    /// <summary>18 套企业级演示文稿主题配色，覆盖主流与细分场景。</summary>
    public IReadOnlyList<PptThemeOption> AvailableThemes { get; } = new List<PptThemeOption>
    {
        // ── 通用商务 ──
        new("tech_blue", "🔷 科技商务蓝", "🔷", "深邃稳健，架构汇报首选", "#0F4C81", "#F6F8FC"),
        new("emerald_green", "🌿 清新自然绿", "🌿", "战略规划、ESG 与教育", "#1B4D3E", "#F4F7F5"),
        new("modern_purple", "🟣 AI 智能紫", "🟣", "前沿创新、未来科技", "#4A148C", "#F7F5FD"),
        new("warm_orange", "🔶 活力暖橙红", "🔶", "商业营销与成果战报", "#B73225", "#FEF8F6"),
        new("dark_elegant", "⬛ 极简暗黑风", "⬛", "沉浸发布会、极客科技", "#60A5FA", "#181A20"),
        // ── 时尚 / 创意 ──
        new("rose_pink", "🩷 浪漫玫瑰粉", "🩷", "时尚品牌、产品发布与活动策划", "#BE185D", "#FDF2F8"),
        new("candy_bright", "🍬 糖果明快", "🍬", "活泼创意、团队协作与内部培训", "#C026D3", "#FDF4FF"),
        // ── 自然 / 环保 ──
        new("ocean_turquoise", "🐬 海洋碧蓝", "🐬", "清新通透、科技产品与海洋生态", "#0E7490", "#F0FDFA"),
        new("forest_deep", "🌲 深林墨绿", "🌲", "自然环保、农业与可持续发展", "#065F46", "#ECFDF5"),
        new("earth_warm", "🪨 大地暖岩", "🪨", "建筑材料、地质勘探与户外运动", "#78350F", "#FFFBEB"),
        // ── 金融 / 奢华 ──
        new("golden_luxury", "✨ 奢华金棕", "✨", "高端金融、奢侈品与年度盛典", "#92400E", "#FFFBEB"),
        new("deep_wine", "🍷 醇酿酒红", "🍷", "高端商务晚宴、品牌联名与尊享活动", "#7F1D1D", "#FEF2F2"),
        // ── 医疗 / 学术 ──
        new("medical_calm", "🏥 医疗清蓝", "🏥", "医疗健康、临床研究与生命科学", "#1E40AF", "#EFF6FF"),
        new("scholar_cream", "📚 学术象牙", "📚", "论文答辩、学术会议与期刊发表", "#78350F", "#FFFBEB"),
        // ── 政府 / 公共 ──
        new("gov_red", "🏛️ 庄重中国红", "🏛️", "政府公文、党建汇报与公共服务", "#991B1B", "#FEF2F2"),
        // ── 科技 / 前沿 ──
        new("cyber_neon", "🤖 赛博霓虹", "🤖", "游戏电竞、元宇宙与前沿科技发布会", "#06B6D4", "#0F172A"),
        new("sunset_gradient", "🌅 日落渐变", "🌅", "温暖叙事、品牌故事与年终总结", "#DC2626", "#FFF7ED"),
        new("nordic_ice", "🧊 北欧冰川", "🧊", "极简冷淡风、学术会议与研究报告", "#334155", "#F8FAFC"),
    };

    private PptThemeOption? _selectedTheme;

    /// <summary>当前选中的 PPT 主题配色。</summary>
    public PptThemeOption SelectedTheme
    {
        get => _selectedTheme ?? AvailableThemes[0];
        set
        {
            if (SetProperty(ref _selectedTheme, value ?? AvailableThemes[0]))
            {
                RefreshSlideThemeBrushes();
            }
        }
    }

    // ---- 前端预览 / 放映专用主题画笔 ----
    // 导出与网页放映由后端按主题 id 渲染配色，而前端此前一直用应用全局主色，
    // 导致切换「配色主题」下拉时预览纹丝不动、只有导出的文件变色（三个出口不同步）。
    private Brush? _slideAccentBrush;
    private Brush? _slideAccentSoftBrush;

    /// <summary>当前 PPT 主题主色画笔（标题、装饰条、卡片边框）。</summary>
    public Brush SlideAccentBrush => _slideAccentBrush ??= ParseThemeBrush(SelectedTheme.PrimaryHex, "#2563EB");

    /// <summary>当前 PPT 主题浅色底画笔（板式徽章等强调块背景）。</summary>
    public Brush SlideAccentSoftBrush => _slideAccentSoftBrush ??= ParseThemeBrush(SelectedTheme.BgHex, "#EFF6FF");

    private static Brush ParseThemeBrush(string? hex, string fallbackHex)
    {
        if (!string.IsNullOrWhiteSpace(hex))
        {
            try
            {
                var c = (Color)ColorConverter.ConvertFromString(hex!);
                return new SolidColorBrush(c);
            }
            catch
            {
                // 主题色非法时落回默认，不让整个预览崩掉
            }
        }
        return new SolidColorBrush((Color)ColorConverter.ConvertFromString(fallbackHex));
    }

    /// <summary>主题变更后重建画笔并广播，令预览 / 放映 / 导出三处配色保持一致。</summary>
    private void RefreshSlideThemeBrushes()
    {
        _slideAccentBrush = ParseThemeBrush(SelectedTheme.PrimaryHex, "#2563EB");
        _slideAccentSoftBrush = ParseThemeBrush(SelectedTheme.BgHex, "#EFF6FF");
        OnPropertyChanged(nameof(SlideAccentBrush));
        OnPropertyChanged(nameof(SlideAccentSoftBrush));
    }

    /// <summary>自定义主题随导出携带完整色值，后端据此构建 PPT 配色（内置主题走后端库）。</summary>
    private static Dictionary<string, object>? BuildThemeColorsPayload(PptThemeOption? theme)
    {
        if (theme is null || !theme.IsCustom)
            return null;
        // 键名与后端 theme_colors / get_theme 约定一致（camelCase 由 JsonPropertyName 负责）
        return new Dictionary<string, object>
        {
            ["name"] = theme.DisplayName,
            ["description"] = theme.Description ?? "",
            ["primary"] = theme.PrimaryHex,
            ["bg"] = theme.BgHex,
        };
    }

    /// <summary>打开创作物画布抽屉。</summary>
    [RelayCommand]
    public void OpenArtifact(object? param)
    {
        ArtifactItem? item = null;
        if (param is ArtifactItem ai) item = ai;
        else if (param is ChatMessage msg && msg.Artifact != null) item = msg.Artifact;

        if (item != null)
        {
            // 打开工作台前先重建清单，确保本会话此前产出的其它交付物也能一并切换
            RefreshSessionArtifacts();

            SelectedArtifact = item;
            CurrentSlideIndex = 0;
            IsArtifactMode = true;
            IsSourceDrawerOpen = true;

            // 匹配并同步主题
            if (!string.IsNullOrWhiteSpace(item.Theme))
            {
                var matchedTheme = AvailableThemes.FirstOrDefault(t => string.Equals(t.Id, item.Theme, StringComparison.OrdinalIgnoreCase));
                if (matchedTheme != null) SelectedTheme = matchedTheme;
            }

            StatusMessage = $"展开创作物画布：{item.Title} ({item.Type.ToUpperInvariant()})";
            DebugLog.Info($"展开创作物画布: title={item.Title} type={item.Type} slides={item.SlideCount}", "Chat");
        }
    }

    /// <summary>幻灯片上一页。</summary>
    [RelayCommand]
    private void PrevSlide()
    {
        if (CanPrevSlide)
        {
            CurrentSlideIndex--;
        }
    }

    /// <summary>幻灯片下一页。</summary>
    [RelayCommand]
    private void NextSlide()
    {
        if (CanNextSlide)
        {
            CurrentSlideIndex++;
        }
    }

    /// <summary>一键将创作物导出为本地物理文件（PPTX/DOCX/XLSX/HTML）。</summary>
    [RelayCommand]
    public async Task ExportArtifactFileAsync(string? targetFormat = null)
    {
        var artifact = SelectedArtifact;
        if (artifact == null || string.IsNullOrWhiteSpace(artifact.RawContent))
        {
            _notifications?.Warning("当前没有可导出的创作物内容");
            return;
        }

        var fmt = targetFormat ?? artifact.Type;
        if (IsBlockedByPptGate(artifact, fmt, out var gateError))
        {
            StatusMessage = gateError;
            _notifications?.Warning(gateError, "导出已拦截");
            DebugLog.Warn($"PPT 导出门禁拦截: {gateError}", "Chat");
            return;
        }

        StatusMessage = $"正在编译导出 {fmt.ToUpperInvariant()} 物理文件（主题: {SelectedTheme.DisplayName}）…";

        try
        {
            var req = new CreativeExportRequest
            {
                Content = artifact.RawContent,
                Format = fmt,
                Title = artifact.Title,
                Theme = SelectedTheme.Id,
                ThemeColors = BuildThemeColorsPayload(SelectedTheme),
            };

            var res = await _apiService.ExportCreativeArtifactAsync(req);
            if (res.Ok && !string.IsNullOrWhiteSpace(res.FilePath))
            {
                StatusMessage = $"已导出文件：{res.FileName}";
                _notifications?.Success($"已成功导出至：{res.FileName}\n路径：{res.FilePath}", "创作导出成功");
                DebugLog.Info($"导出创作物物理文件成功: path={res.FilePath} size={res.FileSizeBytes}", "Chat");

                // 尝试在 Windows 资源管理器中高亮选中生成的文件
                try
                {
                    if (System.IO.File.Exists(res.FilePath))
                    {
                        Process.Start(new ProcessStartInfo("explorer.exe", $"/select,\"{res.FilePath}\"") { UseShellExecute = true });
                    }
                }
                catch (Exception ex)
                {
                    DebugLog.Warn($"在资源管理器中定位导出文件异常: {ex.Message}", "Chat");
                }
            }
            else
            {
                StatusMessage = $"导出失败：{res.Error ?? "未知错误"}";
                _notifications?.Error($"导出失败: {res.Error}");
                DebugLog.Error($"导出交付物失败: {res.Error}", "Chat");
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"导出异常：{ex.Message}";
            DebugLog.Error($"导出交付物异常: {ex.Message}", "Chat", ex);
            _notifications?.Error($"导出异常: {ex.Message}");
        }
    }

    /// <summary>自动导出创作物为物理文件（后台静默执行，不阻塞 UI）。</summary>
    private async Task AutoExportArtifactAsync(ArtifactItem artifact)
    {
        try
        {
            // 延迟 500ms 等待流式完成渲染
            await Task.Delay(500);

            var fmt = artifact.Type;
            if (IsBlockedByPptGate(artifact, fmt, out var gateError))
            {
                StatusMessage = gateError;
                DebugLog.Warn($"自动导出门禁拦截: {gateError}", "Chat");
                return;
            }

            var exportTheme = ReferenceEquals(artifact, SelectedArtifact)
                ? SelectedTheme
                : AllThemes.FirstOrDefault(t => t.Id == (artifact.Theme ?? "tech_blue")) ?? SelectedTheme;
            var req = new CreativeExportRequest
            {
                Content = artifact.RawContent,
                Format = fmt,
                Title = artifact.Title,
                // 若该创作物正在工作台中预览，则跟随用户当前选定的主题，
                // 否则自动导出会停留在 artifact 自带的原始 theme，与随后手动导出/网页放映的配色对不上。
                Theme = exportTheme.Id,
                ThemeColors = BuildThemeColorsPayload(exportTheme),
            };

            var res = await _apiService.ExportCreativeArtifactAsync(req);
            if (res.Ok && !string.IsNullOrWhiteSpace(res.FilePath))
            {
                StatusMessage = $"✅ 已自动导出 {fmt.ToUpperInvariant()}：{res.FileName}";
                DebugLog.Info($"自动导出创作物成功: path={res.FilePath} size={res.FileSizeBytes}", "Chat");

                // 尝试在 Windows 资源管理器中高亮选中生成的文件
                try
                {
                    if (System.IO.File.Exists(res.FilePath))
                    {
                        Process.Start(new ProcessStartInfo("explorer.exe", $"/select,\"{res.FilePath}\"") { UseShellExecute = true });
                    }
                }
                catch (Exception ex)
                {
                    DebugLog.Warn($"在资源管理器中定位自动导出文件异常: {ex.Message}", "Chat");
                }
            }
            else
            {
                // 后端硬门禁也可能拒绝：把「结构不合格」直接透出到状态栏
                var failMsg = string.IsNullOrWhiteSpace(res.Error) ? "自动导出创作物失败" : res.Error;
                if (failMsg.Contains("结构不合格", StringComparison.Ordinal))
                {
                    StatusMessage = failMsg;
                }
                DebugLog.Warn($"自动导出创作物失败: {res.Error}", "Chat");
            }
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"自动导出创作物异常（不影响对话）: {ex.Message}", "Chat");
        }
    }

    /// <summary>
    /// PPT 导出硬门禁（客户端预检，与 Python validate_pptx_export 对齐）。
    /// 拒绝：页码占位标题 / 空正文页 / 残留 Markdown 超阈值。
    /// </summary>
    internal static bool IsBlockedByPptGate(ArtifactItem artifact, string fmt, out string error)
    {
        error = string.Empty;
        if (!string.Equals(fmt, "pptx", StringComparison.OrdinalIgnoreCase)
            && !string.Equals(fmt, "ppt", StringComparison.OrdinalIgnoreCase))
        {
            return false;
        }

        // 仅当预览侧已解析出幻灯片时才做客户端门禁；
        // Slides 为空时交给 Python 后端用 raw_content 重新解析并做权威门禁。
        if (artifact.Slides.Count == 0)
        {
            return false;
        }

        var placeholderPages = new List<int>();
        var emptyPages = new List<int>();
        var residuePages = new List<int>();
        var pageTitleRx = new System.Text.RegularExpressions.Regex(@"^第\s*\d+\s*页$");
        var mathRx = new System.Text.RegularExpressions.Regex(@"\$[^$\n]{1,40}\$");

        foreach (var s in artifact.Slides)
        {
            var title = s.Title?.Trim() ?? string.Empty;
            if (pageTitleRx.IsMatch(title))
            {
                placeholderPages.Add(s.Index);
            }

            if (!HasRealPptBody(s))
            {
                var isCoverish = string.Equals(s.Layout, "cover", StringComparison.OrdinalIgnoreCase) || s.IsCover;
                if (!(isCoverish && (!string.IsNullOrWhiteSpace(s.Title) || !string.IsNullOrWhiteSpace(s.Subtitle))))
                {
                    emptyPages.Add(s.Index);
                }
            }

            if (CountMarkdownResidue(s, mathRx) >= 3)
            {
                residuePages.Add(s.Index);
            }
        }

        var errors = new List<string>();
        if (placeholderPages.Count > 0)
        {
            errors.Add($"存在页码占位标题页（{string.Join("、", placeholderPages)}），请为每页提供 `# 标题`");
        }
        if (emptyPages.Count > 0)
        {
            errors.Add($"存在空正文页（{string.Join("、", emptyPages)}），请补充要点/表格/金句内容");
        }
        if (residuePages.Count > 0)
        {
            errors.Add($"正文/表格残留 Markdown 语法（{string.Join("、", residuePages)}），请清理 `**加粗**` 与 `$公式$`");
        }

        if (errors.Count > 0)
        {
            error = "结构不合格，请重生成：" + string.Join("；", errors);
            return true;
        }

        return false;
    }

    private static bool HasRealPptBody(SlideItem s)
    {
        if (s.BulletPoints.Any(b => !string.IsNullOrWhiteSpace(b))) return true;
        if (s.TableData != null && s.TableData.Any(row => row.Any(c => !string.IsNullOrWhiteSpace(c)))) return true;
        if (s.Cards.Any(c => !string.IsNullOrWhiteSpace(c.Title)
            || !string.IsNullOrWhiteSpace(c.Content)
            || c.Bullets.Any(b => !string.IsNullOrWhiteSpace(b)))) return true;
        if (s.Metrics.Any(m => !string.IsNullOrWhiteSpace(m.Value) || !string.IsNullOrWhiteSpace(m.Label))) return true;
        if (s.TimelineNodes.Any(t => !string.IsNullOrWhiteSpace(t.Stage) || !string.IsNullOrWhiteSpace(t.Title))) return true;
        if (!string.IsNullOrWhiteSpace(s.QuoteText)) return true;
        return false;
    }

    private static int CountMarkdownResidue(SlideItem s, System.Text.RegularExpressions.Regex mathRx)
    {
        static int Hits(string? text, System.Text.RegularExpressions.Regex mathRx)
        {
            if (string.IsNullOrEmpty(text)) return 0;
            var boldPairs = System.Text.RegularExpressions.Regex.Matches(text, @"\*\*").Count / 2;
            return boldPairs + mathRx.Matches(text).Count;
        }

        var total = 0;
        foreach (var b in s.BulletPoints) total += Hits(b, mathRx);
        total += Hits(s.QuoteText, mathRx);
        foreach (var c in s.Cards)
        {
            total += Hits(c.Title, mathRx);
            total += Hits(c.Content, mathRx);
            foreach (var b in c.Bullets) total += Hits(b, mathRx);
        }
        foreach (var m in s.Metrics)
        {
            total += Hits(m.Value, mathRx);
            total += Hits(m.Label, mathRx);
        }
        foreach (var t in s.TimelineNodes)
        {
            total += Hits(t.Stage, mathRx);
            total += Hits(t.Title, mathRx);
        }
        if (s.TableData != null)
        {
            foreach (var row in s.TableData)
            {
                foreach (var cell in row) total += Hits(cell, mathRx);
            }
        }
        return total;
    }

    private PptInspectionReportDto? _inspectionReport;
    private bool _isInspectionReportOpen;

    /// <summary>当前 PPT 效果自检与质量诊断报告。</summary>
    public PptInspectionReportDto? InspectionReport
    {
        get => _inspectionReport;
        set
        {
            if (SetProperty(ref _inspectionReport, value))
            {
                OnPropertyChanged(nameof(HasInspectionReport));
            }
        }
    }

    public bool HasInspectionReport => InspectionReport != null;

    /// <summary>自检报告抽屉是否展开。</summary>
    public bool IsInspectionReportOpen
    {
        get => _isInspectionReportOpen;
        set => SetProperty(ref _isInspectionReportOpen, value);
    }

    /// <summary>对当前创作物进行效果自检体检诊断。</summary>
    [RelayCommand]
    public async Task InspectPptAsync()
    {
        var artifact = SelectedArtifact;
        if (artifact == null || string.IsNullOrWhiteSpace(artifact.RawContent))
        {
            _notifications?.Warning("当前没有可自检的 PPT 内容");
            return;
        }

        StatusMessage = "正在对演示文稿进行全方位效果自检与体检评分…";

        try
        {
            var report = await _apiService.InspectCreativeArtifactAsync(artifact.RawContent);
            InspectionReport = report;
            IsInspectionReportOpen = true;
            StatusMessage = $"PPT 自检完成：健康度得分 {report.Score} 分 ({report.Grade})";
            _notifications?.Info($"PPT 效果自检完成：健康得分 {report.Score} 分 ({report.Grade})\n{report.Summary}", "效果自检报告");
            DebugLog.Info($"PPT 效果自检完成: score={report.Score} grade={report.Grade} issues={report.Issues.Count}", "Chat");
        }
        catch (Exception ex)
        {
            StatusMessage = $"自检异常：{ex.Message}";
            _notifications?.Error($"效果自检失败: {ex.Message}");
            DebugLog.Error($"PPT 效果自检异常: {ex.Message}", "Chat", ex);
        }
    }

    /// <summary>关闭自检报告抽屉。</summary>
    [RelayCommand]
    public void CloseInspectionReport()
    {
        IsInspectionReportOpen = false;
    }

    // ===================== 创作物版本管理 =====================

    private System.Collections.ObjectModel.ObservableCollection<DocMind.Models.ArtifactVersionSummary> _artifactVersions = new();
    private DocMind.Models.ArtifactVersionSummary? _selectedArtifactVersion;
    private bool _isVersionPanelOpen;

    /// <summary>当前创作物的版本列表。</summary>
    public System.Collections.ObjectModel.ObservableCollection<DocMind.Models.ArtifactVersionSummary> ArtifactVersions
    {
        get => _artifactVersions;
        private set => SetProperty(ref _artifactVersions, value);
    }

    /// <summary>选中的历史版本。</summary>
    public DocMind.Models.ArtifactVersionSummary? SelectedArtifactVersion
    {
        get => _selectedArtifactVersion;
        set
        {
            if (SetProperty(ref _selectedArtifactVersion, value))
                RestoreArtifactVersionCommand.NotifyCanExecuteChanged();
        }
    }

    /// <summary>版本面板是否展开。</summary>
    public bool IsVersionPanelOpen
    {
        get => _isVersionPanelOpen;
        set => SetProperty(ref _isVersionPanelOpen, value);
    }

    /// <summary>保存当前创作物为一个新版本。</summary>
    [RelayCommand]
    private async Task SaveArtifactVersionAsync()
    {
        var artifact = SelectedArtifact;
        if (artifact == null || string.IsNullOrWhiteSpace(artifact.RawContent))
        {
            _notifications?.Warning("没有可存版本的创作物");
            return;
        }
        try
        {
            var draft = new DocMind.Models.CreativeExportRequest
            {
                Content = artifact.RawContent,
                Format = artifact.Type,
                Title = artifact.Title,
            };
            await _apiService.SaveArtifactVersionAsync(artifact.Id, draft);
            await LoadArtifactVersionsAsync();
            IsVersionPanelOpen = true;
            _notifications?.Success("已保存版本", "版本");
        }
        catch (Exception ex)
        {
            _notifications?.Error($"保存版本失败：{ex.Message}");
            DebugLog.Warn($"保存创作物版本失败: {ex.Message}", "Chat");
        }
    }

    /// <summary>加载版本列表。</summary>
    [RelayCommand]
    private async Task LoadArtifactVersionsAsync()
    {
        var artifact = SelectedArtifact;
        if (artifact == null) return;
        try
        {
            var list = await _apiService.ListArtifactVersionsAsync(artifact.Id);
            ArtifactVersions = new System.Collections.ObjectModel.ObservableCollection<DocMind.Models.ArtifactVersionSummary>(list);
            IsVersionPanelOpen = true;
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"加载版本列表失败: {ex.Message}", "Chat");
        }
    }

    /// <summary>把选中版本恢复为当前草稿（不自动再导出）。</summary>
    [RelayCommand(CanExecute = nameof(CanRestoreVersion))]
    private async Task RestoreArtifactVersionAsync()
    {
        var artifact = SelectedArtifact;
        var ver = SelectedArtifactVersion;
        if (artifact == null || ver == null) return;
        try
        {
            var full = await _apiService.GetArtifactVersionAsync(artifact.Id, ver.VersionId);
            if (full != null && !string.IsNullOrWhiteSpace(full.Content))
            {
                artifact.RawContent = full.Content;
                if (!string.IsNullOrWhiteSpace(full.Title))
                    artifact.Title = full.Title!;
                // 触发预览刷新
                SelectedArtifact = artifact;
                OnPropertyChanged(nameof(SelectedArtifact));
                _notifications?.Success($"已恢复版本 v{ver.Version}", "版本");
            }
        }
        catch (Exception ex)
        {
            _notifications?.Error($"恢复版本失败：{ex.Message}");
        }
    }

    private bool CanRestoreVersion => SelectedArtifact != null && SelectedArtifactVersion != null;

    /// <summary>按体检报告建议修订当前 PPT（1 轮，续写/重发提示给模型）。</summary>
    [RelayCommand]
    private async Task ReviseFromInspectionAsync()
    {
        var report = InspectionReport;
        var artifact = SelectedArtifact;
        if (report == null || artifact == null || string.IsNullOrWhiteSpace(artifact.RawContent))
            return;

        var sb = new System.Text.StringBuilder();
        sb.AppendLine("请根据以下 PPT 体检结果修订演示文稿，只改结构与表达，不编造无依据事实；");
        sb.AppendLine("保持 Artifact 语法（`---` 分页、`### 卡片`、`<!-- note: -->` 备注），输出完整修订后的全文。");
        sb.AppendLine($"体检得分：{report.Score}（{report.Grade}）");
        if (!string.IsNullOrWhiteSpace(report.Summary))
            sb.AppendLine($"总结：{report.Summary}");
        foreach (var issue in report.Issues.Take(8))
        {
            sb.Append("- ");
            if (!string.IsNullOrWhiteSpace(issue.Category)) sb.Append($"[{issue.Category}] ");
            sb.Append(issue.Message);
            if (!string.IsNullOrWhiteSpace(issue.FixSuggestion)) sb.Append($" → {issue.FixSuggestion}");
            sb.AppendLine();
        }
        foreach (var rec in report.Recommendations.Take(5))
            sb.AppendLine($"- 建议：{rec}");

        CloseInspectionReport();
        // 以续写方式提交：不新开用户气泡，把修订要求并入本轮
        InputText = sb.ToString();
        await ContinueWritingAsync();
    }

    private bool _isSlideShowOpen;
    private bool _isSpeakerNotesVisibleInSlideShow = true;

    /// <summary>是否开启客户端全屏沉浸放映预览。</summary>
    public bool IsSlideShowOpen
    {
        get => _isSlideShowOpen;
        set => SetProperty(ref _isSlideShowOpen, value);
    }

    /// <summary>全屏放映时是否显示演讲提词器抽屉。</summary>
    public bool IsSpeakerNotesVisibleInSlideShow
    {
        get => _isSpeakerNotesVisibleInSlideShow;
        set => SetProperty(ref _isSpeakerNotesVisibleInSlideShow, value);
    }

    /// <summary>开启客户端大屏沉浸放映预览。</summary>
    [RelayCommand]
    public void OpenSlideShow()
    {
        if (SelectedArtifact == null || !IsPptArtifact)
        {
            _notifications?.Warning("当前没有可放映的演示文稿");
            return;
        }
        IsSlideShowOpen = true;
        StatusMessage = "进入 PPT 大屏沉浸放映预览模式（按 Esc 退出，键盘左右键翻页）";
    }

    /// <summary>退出客户端全屏沉浸放映预览。</summary>
    [RelayCommand]
    public void CloseSlideShow()
    {
        IsSlideShowOpen = false;
        StatusMessage = "已退出大屏放映模式";
    }

    /// <summary>切换全屏放映时的提词小抄显示状态。</summary>
    [RelayCommand]
    public void ToggleSlideShowNotes()
    {
        IsSpeakerNotesVisibleInSlideShow = !IsSpeakerNotesVisibleInSlideShow;
    }

    /// <summary>在浏览器中一键秒开 16:9 交互式 SlideShow 网页放映预览。</summary>
    [RelayCommand]
    public async Task OpenWebPreviewAsync()
    {
        var artifact = SelectedArtifact;
        if (artifact == null || string.IsNullOrWhiteSpace(artifact.RawContent))
        {
            _notifications?.Warning("当前没有可预览的创作物内容");
            return;
        }

        StatusMessage = "正在编译 16:9 交互式 HTML5 幻灯片放映页面…";

        try
        {
            var req = new CreativeExportRequest
            {
                Content = artifact.RawContent,
                Format = "html",
                Title = artifact.Title,
                Theme = SelectedTheme.Id,
                ThemeColors = BuildThemeColorsPayload(SelectedTheme),
            };

            var res = await _apiService.ExportCreativeArtifactAsync(req);
            if (res.Ok && !string.IsNullOrWhiteSpace(res.FilePath) && System.IO.File.Exists(res.FilePath))
            {
                StatusMessage = "已在浏览器中启动 16:9 交互式放映预览";
                _notifications?.Success("已在浏览器中打开全屏交互式放映页面（支持键盘 ← → 翻页与 F 键全屏）", "网页放映启动");
                Process.Start(new ProcessStartInfo(res.FilePath) { UseShellExecute = true });
            }
            else
            {
                StatusMessage = $"网页预览生成失败: {res.Error ?? "未知错误"}";
                _notifications?.Error($"网页放映失败: {res.Error}");
            }
        }
        catch (Exception ex)
        {
            StatusMessage = $"网页放映启动异常: {ex.Message}";
            _notifications?.Error($"启动异常: {ex.Message}");
            DebugLog.Error($"启动网页预览异常: {ex.Message}", "Chat", ex);
        }
    }

    /// <summary>点击引用来源：在右侧协同抽屉就地展开原著切片与元数据，不离开对话主界面。</summary>
    [RelayCommand]
    private void OpenSource(SourceRef? src)
    {
        if (src is null)
        {
            return;
        }
        IsArtifactMode = false;
        SelectedSource = src;
        IsSourceDrawerOpen = true;

        // 原文件 × 关键点对照：PDF 默认半屏沉浸阅读（对齐 ima 分屏），
        // 过窄时自动放宽到半屏档；用户仍可拖把手或点展开再放大。
        var isPdf = string.Equals(src.Format, "pdf", StringComparison.OrdinalIgnoreCase)
                    || src.Source.EndsWith(".pdf", StringComparison.OrdinalIgnoreCase);
        if (isPdf && SourceDrawerWidth < 800)
        {
            SourceDrawerWidth = 880;
        }

        StatusMessage = $"原文对照：{src.DisplayTitle} ({src.ScoreBadgeText})";
        DebugLog.Info($"展开原文对照抽屉: index={src.Index} source={src.Source} page={src.Page}", "Chat");
    }

    /// <summary>在浏览器中打开当前选中的网页来源 URL（仅 http/https，防危险协议）。</summary>
    [RelayCommand]
    private void OpenWebSource()
    {
        if (SelectedSource?.Url is not { Length: > 0 } url)
        {
            return;
        }
        if (!TryOpenHttpUrl(url))
        {
            _notifications?.Warning("该来源不是有效的网页地址（仅支持 http/https）", "无法打开");
        }
    }

    /// <summary>一键直达外部网页来源（在默认浏览器中打开）。</summary>
    [RelayCommand]
    private void OpenDirectWeb(string? url)
    {
        if (string.IsNullOrWhiteSpace(url)) return;
        if (!TryOpenHttpUrl(url))
        {
            _notifications?.Warning("该来源不是有效的网页地址（仅支持 http/https）", "无法打开");
        }
    }

    /// <summary>在默认浏览器中打开 http/https 链接；其他协议一律拒绝，返回 false。</summary>
    public static bool TryOpenHttpUrl(string url)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri)
            || (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps))
        {
            return false;
        }
        try
        {
            System.Diagnostics.Process.Start(
                new System.Diagnostics.ProcessStartInfo(uri.AbsoluteUri)
                {
                    UseShellExecute = true,
                });
            return true;
        }
        catch (Exception ex)
        {
            DebugLog.Warn($"打开网页失败: {ex.Message}", "Chat");
            return false;
        }
    }

    /// <summary>关闭协同来源抽屉。</summary>
    [RelayCommand]
    private void CloseSourceDrawer()
    {
        IsSourceDrawerOpen = false;
    }

    /// <summary>复制当前抽屉中切片正文到剪贴板。</summary>
    [RelayCommand]
    private void CopySourceSnippet()
    {
        if (!string.IsNullOrWhiteSpace(SelectedSource?.Snippet))
        {
            try
            {
                Clipboard.SetText(SelectedSource.Snippet);
                _notifications?.Success("已复制切片原文到剪贴板");
                StatusMessage = "已复制切片原文到剪贴板";
            }
            catch (Exception ex)
            {
                DebugLog.Warn($"复制到剪贴板失败: {ex.Message}", "Chat");
            }
        }
    }

    /// <summary>用户主动选择：在知识搜索页全文检索该文档（深钻次级动作）。</summary>
    [RelayCommand]
    private void SearchSourceInSearchPage()
    {
        if (SelectedSource is not null)
        {
            SourceSearchRequested?.Invoke(SelectedSource);
        }
    }
}
