using System.Collections.ObjectModel;
using System.Diagnostics;
using System.Windows;
using CommunityToolkit.Mvvm.Input;
using DocMind.Services;

namespace DocMind.ViewModels;

/// <summary>
/// 调试日志页：展示 DebugLog 内存缓冲的实时内容，
/// 提供"清空缓冲"和"用系统默认应用打开日志文件"两个操作。
/// </summary>
public partial class DebugLogViewModel : ViewModelBase
{
    public ObservableCollection<string> Lines { get; } = new();

    private readonly System.Windows.Threading.Dispatcher _dispatcher;

    public DebugLogViewModel()
    {
        Title = "调试日志";
        _dispatcher = Application.Current.Dispatcher;

        // 载入已有缓冲
        foreach (var line in DebugLog.Snapshot())
        {
            Lines.Add(line);
        }

        // 订阅实时日志（可能来自后台线程，切到 UI 线程）
        DebugLog.LineAppended += OnLineAppended;
    }

    /// <summary>当前日志行数（用于标题栏显示）。</summary>
    public int LineCount => Lines.Count;

    /// <summary>日志文件完整路径（方便用户直接打开）。</summary>
    public string LogFilePath => DebugLog.LogFilePath;

    private void OnLineAppended(string line)
    {
        _dispatcher.BeginInvoke(() =>
        {
            Lines.Add(line);
            // 内存上限保护：缓冲只保留最近 2000 行
            while (Lines.Count > 2000)
            {
                Lines.RemoveAt(0);
            }
            OnPropertyChanged(nameof(LineCount));
        });
    }

    /// <summary>清空内存缓冲（磁盘日志文件保留）。</summary>
    [RelayCommand]
    private void Clear()
    {
        DebugLog.ClearBuffer();
        Lines.Clear();
        OnPropertyChanged(nameof(LineCount));
    }

    /// <summary>用系统默认文本编辑器打开日志文件。</summary>
    [RelayCommand]
    private void OpenLogFile()
    {
        try
        {
            Process.Start(new ProcessStartInfo(DebugLog.LogFilePath) { UseShellExecute = true });
        }
        catch (Exception ex)
        {
            DebugLog.Error($"打开日志文件失败: {DebugLog.LogFilePath}", "DebugLog", ex);
        }
    }

    /// <summary>在资源管理器中打开日志目录并选中当前日志文件。</summary>
    [RelayCommand]
    private void OpenLogFolder()
    {
        DebugLog.OpenLogFolder();
    }
}
