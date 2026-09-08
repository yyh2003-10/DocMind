using System.IO;
using System.Windows;
using CommunityToolkit.Mvvm.ComponentModel;
using CommunityToolkit.Mvvm.Input;
using DocMind.Services;

namespace DocMind.ViewModels;

/// <summary>单个外部资源（poppler / 后端 Python / 离线 wheels / 模型缓存）的路径行。</summary>
public partial class ResourcePathItemViewModel : ObservableObject
{
    public LocatableResource Kind { get; }

    public string DisplayName { get; }
    public string Description { get; }

    [ObservableProperty]
    [NotifyPropertyChangedFor(nameof(StatusIcon))]
    private bool _isFound;

    [ObservableProperty]
    private string? _effectivePath;

    [ObservableProperty]
    private string _sourceLabel = "未检测";

    [ObservableProperty]
    private string _statusText = "";

    [ObservableProperty]
    private bool _isBusy;

    public string StatusIcon => IsFound ? "✅" : "❌";

    /// <summary>当前生效路径 + 来源的一行展示文本。</summary>
    public string PathDisplay => IsFound
        ? $"{EffectivePath}（{SourceLabel}）"
        : "未找到可用配置";

    public ResourcePathItemViewModel(LocatableResource kind, string displayName, string description)
    {
        Kind = kind;
        DisplayName = displayName;
        Description = description;
    }

    public void UpdateFrom(ResourceLocatorResult result)
    {
        IsFound = result.Found;
        EffectivePath = result.Path;
        SourceLabel = result.Source;
        StatusText = result.Detail ?? "";
        OnPropertyChanged(nameof(PathDisplay));
    }
}

/// <summary>
/// 设置页「外部组件路径」面板：支持用户浏览指定位置，以及「自动寻找可用配置」
/// ——三级递进（显式配置 → 高概率区快扫 → 全盘深扫含 U 盘），命中后写配置
/// 并提示重启后端生效。
/// </summary>
public partial class ResourcePathPanelViewModel : ViewModelBase
{
    private readonly ResourceLocatorService _locator;
    private readonly NotificationService _notifications;

    /// <summary>深扫确认钩子（默认 MessageBox；测试可注入）。返回 false 则不深扫。</summary>
    public Func<string, bool>? ConfirmDeepScan { get; set; }

    public System.Collections.ObjectModel.ObservableCollection<ResourcePathItemViewModel> Items { get; }

    public bool IsAnyScanning => Items.Any(i => i.IsBusy);

    public ResourcePathPanelViewModel(
        ResourceLocatorService locator,
        NotificationService notifications)
    {
        _locator = locator;
        _notifications = notifications;
        ConfirmDeepScan = DefaultConfirm;

        Items = new System.Collections.ObjectModel.ObservableCollection<ResourcePathItemViewModel>
        {
            new(LocatableResource.Poppler, "Poppler（扫描 PDF 渲染）",
                "扫描版 PDF 转图片做 OCR 的依赖；可指向解压后含 pdftoppm.exe 的 bin 目录"),
            new(LocatableResource.BackendPython, "后端 Python 解释器",
                "后端 doc2mind 服务的运行环境；建议指向项目 .venv 内的 python.exe"),
            new(LocatableResource.OfflineWheels, "离线安装包目录（wheels）",
                "插件安装优先从此目录取包，无需联网；可放在 U 盘携带"),
            new(LocatableResource.EmbedModelCache, "嵌入模型缓存目录",
                "本地语义向量模型缓存（fastembed_cache）；换位置后需重新下载或复制模型"),
        };

        // 任一行 IsBusy 变化 → 广播 IsAnyScanning 并刷新各命令的 CanExecute。
        // CommunityToolkit RelayCommand 不接 WPF CommandManager，必须显式
        // NotifyCanExecuteChanged，否则深扫期间其他行按钮不会变灰/恢复。
        foreach (var item in Items)
        {
            item.PropertyChanged += (_, e) =>
            {
                if (e.PropertyName == nameof(ResourcePathItemViewModel.IsBusy))
                {
                    OnPropertyChanged(nameof(IsAnyScanning));
                    RefreshCommand.NotifyCanExecuteChanged();
                    AutoLocateCommand.NotifyCanExecuteChanged();
                    BrowseCommand.NotifyCanExecuteChanged();
                    CancelScanCommand.NotifyCanExecuteChanged();
                }
            };
        }
    }

    private bool AnyBusy => Items.Any(i => i.IsBusy);

    private bool CanRefresh => !AnyBusy;
    private bool CanBrowse(ResourcePathItemViewModel? item) =>
        item is not null && !AnyBusy;

    /// <summary>刷新全部资源行（快扫，秒级；显式配置优先）。深扫期间禁止并发。</summary>
    [RelayCommand(CanExecute = nameof(CanRefresh))]
    public async Task RefreshAsync()
    {
        foreach (var item in Items)
        {
            item.IsBusy = true;
            try
            {
                var result = await Task.Run(() => _locator.Locate(item.Kind));
                item.UpdateFrom(result);
            }
            catch (Exception ex)
            {
                item.StatusText = $"检测失败: {ex.Message}";
            }
            finally
            {
                item.IsBusy = false;
            }
        }
    }

    /// <summary>浏览手动指定位置（显式配置最高优先级）。深扫期间禁止并发。</summary>
    [RelayCommand(CanExecute = nameof(CanBrowse))]
    private void Browse(ResourcePathItemViewModel? item)
    {
        if (item is null || AnyBusy) return;
        var dialog = new Microsoft.Win32.OpenFolderDialog
        {
            Title = $"选择 {item.DisplayName} 位置",
            Multiselect = false,
        };
        if (dialog.ShowDialog() != true) return;

        if (!_locator.Validate(item.Kind, dialog.FolderName))
        {
            item.StatusText = "所选目录未通过校验（缺少必需文件），未写入配置。";
            _notifications.Warning($"{item.DisplayName}：所选目录缺少必需文件，未生效", "外部组件");
            return;
        }
        ApplyAndNotify(item, dialog.FolderName, "手动配置");
    }

    /// <summary>自动寻找可用配置：快扫未命中时征询用户后转入全盘深扫（含 U 盘）。
    /// 深扫命中多个候选时自动应用第一个，其余候选写入状态行供「浏览选择」改选。</summary>
    [RelayCommand(CanExecute = nameof(CanAutoLocate))]
    private async Task AutoLocateAsync(ResourcePathItemViewModel? item)
    {
        if (item is null || item.IsBusy || AnyBusy) return;
        item.IsBusy = true;
        try
        {
            item.StatusText = "正在快扫高概率位置…";
            var quick = await Task.Run(() => _locator.Locate(item.Kind));
            if (quick.Found)
            {
                if (quick.Source == "手动配置")
                {
                    item.UpdateFrom(quick);
                    item.StatusText = "当前配置可用，无需更改。";
                    return;
                }
                _locator.Apply(item.Kind, quick.Path!);
                item.UpdateFrom(quick with { Source = quick.Source + "·已应用" });
                _notifications.Success(
                    $"{item.DisplayName}已自动配置：{quick.Path}（重启后端生效）", "自动寻找");
                return;
            }

            if (!(ConfirmDeepScan?.Invoke(item.DisplayName) ?? false))
            {
                item.StatusText = "已取消，可稍后再试或手动浏览指定位置。";
                return;
            }

            using var cts = new CancellationTokenSource();
            _scanCts = cts;
            var progress = new Progress<string>(dir => item.StatusText = $"正在搜索… {dir}");
            try
            {
                var deep = await _locator.DeepScanAsync(item.Kind, progress, cts.Token);
                if (deep.Found)
                {
                    _locator.Apply(item.Kind, deep.Path!);
                    item.UpdateFrom(deep with { Source = deep.Source + "·已应用" });
                    // 多候选（如盘上有几套 poppler/python）：自动应用的是第一个，
                    // 其余候选明确列出让用户可用「浏览选择」改选，避免静默选错
                    item.StatusText = deep.Candidates.Count > 1
                        ? $"已应用第 1/{deep.Candidates.Count} 个候选。其余候选："
                          + string.Join("；", deep.Candidates.Skip(1))
                          + "（如需改选，请用「浏览选择…」指定）"
                        : "";
                    _notifications.Success(
                        $"{item.DisplayName}已自动配置：{deep.Path}（重启后端生效）", "自动寻找");
                }
                else
                {
                    item.UpdateFrom(deep);
                }
            }
            catch (OperationCanceledException)
            {
                item.StatusText = "搜索已取消（已扫描部分未写入配置）。";
            }
        }
        catch (Exception ex)
        {
            item.StatusText = $"自动寻找失败: {ex.Message}";
            _notifications.Error($"{item.DisplayName} 自动寻找失败：{ex.Message}", "外部组件");
        }
        finally
        {
            item.IsBusy = false;
            _scanCts = null;
        }
    }

    private bool CanAutoLocate(ResourcePathItemViewModel? item) =>
        item is not null && !item.IsBusy && !AnyBusy;

    private CancellationTokenSource? _scanCts;

    /// <summary>取消正在进行的全盘深扫（仅扫描期间可用）。</summary>
    [RelayCommand(CanExecute = nameof(CanCancelScan))]
    private void CancelScan()
    {
        _scanCts?.Cancel();
    }

    private bool CanCancelScan => IsAnyScanning;

    private void ApplyAndNotify(ResourcePathItemViewModel item, string path, string source)
    {
        _locator.Apply(item.Kind, path);
        item.UpdateFrom(new ResourceLocatorResult
        {
            Found = true,
            Path = path,
            Source = source,
            Candidates = new[] { path },
        });
        _notifications.Success($"{item.DisplayName}已配置：{path}（重启后端生效）", "外部组件");
    }

    private bool DefaultConfirm(string displayName)
    {
        return MessageBox.Show(
            $"在高概率位置未找到 {displayName} 的可用配置。\n\n"
            + "是否全盘深搜（含 U 盘，跳过系统目录，SSD 约 1-3 分钟，可中途取消）？",
            "自动寻找可用配置",
            MessageBoxButton.YesNo,
            MessageBoxImage.Question) == MessageBoxResult.Yes;
    }
}
