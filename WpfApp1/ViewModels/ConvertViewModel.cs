using System.IO;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;

namespace DocMind.ViewModels;

public partial class ConvertViewModel : ViewModelBase
{
    private readonly IDoc2kbApiService _apiService;
    private readonly NotificationService _notifications;

    private string _inputPath = string.Empty;
    private string _outputPath = string.Empty;
    private string _format = "md";
    private bool _isBusy;
    private string _statusMessage = "就绪";
    private string _previewContent = string.Empty;
    private ConvertResult? _lastResult;

    /// <summary>支持的目标格式清单（UI 下拉用）。</summary>
    public IReadOnlyList<string> SupportedFormats { get; } = new[]
    {
        "md", "json", "txt", "html",
    };

    public ConvertViewModel(IDoc2kbApiService apiService, NotificationService notifications)
    {
        _apiService = apiService;
        _notifications = notifications;
        Title = "格式转换";
    }

    /// <summary>输入文件路径（本地）。</summary>
    public string InputPath
    {
        get => _inputPath;
        set
        {
            if (SetProperty(ref _inputPath, value))
            {
                ConvertCommand.NotifyCanExecuteChanged();
                OnPropertyChanged(nameof(HasInputPath));
            }
        }
    }

    /// <summary>是否已选择输入文件。</summary>
    public bool HasInputPath => !string.IsNullOrWhiteSpace(InputPath);

    /// <summary>输出文件路径（本地）。空表示仅预览不落盘。</summary>
    public string OutputPath
    {
        get => _outputPath;
        set => SetProperty(ref _outputPath, value);
    }

    /// <summary>目标格式：md / json / txt / html。</summary>
    public string Format
    {
        get => _format;
        set => SetProperty(ref _format, value);
    }

    /// <summary>是否正在转换中。</summary>
    public bool IsBusy
    {
        get => _isBusy;
        set
        {
            if (SetProperty(ref _isBusy, value))
            {
                ConvertCommand.NotifyCanExecuteChanged();
            }
        }
    }

    /// <summary>底部状态栏消息。</summary>
    public string StatusMessage
    {
        get => _statusMessage;
        set => SetProperty(ref _statusMessage, value);
    }

    /// <summary>预览文本（转换结果正文前 ~10KB）。</summary>
    public string PreviewContent
    {
        get => _previewContent;
        set => SetProperty(ref _previewContent, value);
    }

    /// <summary>上次 ConvertResult，落盘成功时显示路径。</summary>
    public ConvertResult? LastResult
    {
        get => _lastResult;
        set => SetProperty(ref _lastResult, value);
    }

    private bool CanConvert => !IsBusy && !string.IsNullOrWhiteSpace(InputPath);

    /// <summary>选择输入文件。</summary>
    [RelayCommand]
    private void PickInput()
    {
        var dlg = new Microsoft.Win32.OpenFileDialog
        {
            Title = "选择要转换的源文件",
            Filter = "所有文件 (*.*)|*.*",
        };
        if (dlg.ShowDialog() == true)
        {
            InputPath = dlg.FileName;
            // 默认输出路径：同目录 + 同名 + .Format
            var dir = System.IO.Path.GetDirectoryName(InputPath) ?? string.Empty;
            var stem = System.IO.Path.GetFileNameWithoutExtension(InputPath);
            OutputPath = System.IO.Path.Combine(dir, $"{stem}.{Format}");
            DebugLog.Info($"已选择输入文件: {InputPath}，默认输出: {OutputPath}", "Convert");
        }
        else
        {
            DebugLog.Info("用户取消选择输入文件", "Convert");
        }
    }

    /// <summary>选择输出文件落盘位置。</summary>
    [RelayCommand]
    private void PickOutput()
    {
        var dlg = new Microsoft.Win32.SaveFileDialog
        {
            Title = "选择输出文件位置",
            FileName = string.IsNullOrWhiteSpace(OutputPath)
                ? $"converted.{Format}"
                : System.IO.Path.GetFileName(OutputPath),
            Filter = $"{Format} 文件 (*.{Format})|*.{Format}|所有文件 (*.*)|*.*",
        };
        if (dlg.ShowDialog() == true)
        {
            OutputPath = dlg.FileName;
        }
    }

    /// <summary>执行转换并刷新预览。</summary>
    [RelayCommand(CanExecute = nameof(CanConvert))]
    private async Task ConvertAsync()
    {
        if (!CanConvert)
        {
            return;
        }

        IsBusy = true;
        StatusMessage = "转换中…";
        PreviewContent = string.Empty;
        LastResult = null;

        DebugLog.Info($"开始转换: InputPath='{InputPath.Trim()}' OutputPath='{(string.IsNullOrWhiteSpace(OutputPath) ? "(空,仅预览)" : OutputPath.Trim())}' Format='{Format}'", "Convert");
        var sw = System.Diagnostics.Stopwatch.StartNew();

        try
        {
            var resp = await _apiService.ConvertAsync(
                new ConvertRequest
                {
                    InputPath = InputPath.Trim(),
                    // 后端 output_path: str | None；空时发 null 避免后端误判落盘
                    OutputPath = string.IsNullOrWhiteSpace(OutputPath) ? null : OutputPath.Trim(),
                    Format = Format,
                });

            sw.Stop();
            LastResult = resp;
            if (resp.Success)
            {
                // 后端 convert 行为：指定 output_path 时会落盘写文件（见 http.py）；
                // 未指定时仅返回 Content 供预览。此处两种情况分别提示。
                var content = resp.Content;
                PreviewContent = content.Length > 10_000
                    ? content[..10_000] + "\n\n…（预览截断，共 " + content.Length + " 字符）"
                    : content;

                var wroteFile = !string.IsNullOrWhiteSpace(OutputPath);
                StatusMessage = wroteFile
                    ? $"完成 → 已写入：{OutputPath.Trim()}"
                    : $"完成 → 格式 {resp.OutputFormat}（未指定输出位置，仅预览）";
                _notifications.Success(wroteFile
                    ? $"转换完成，已写入：{OutputPath.Trim()}"
                    : $"转换完成：{resp.OutputFormat}（仅预览）");
                DebugLog.Info(
                    $"转换成功: input='{resp.Input}' format='{resp.OutputFormat}' elements={resp.ElementsCount} " +
                    $"contentLen={content.Length} 耗时{sw.ElapsedMilliseconds}ms",
                    "Convert");
            }
            else
            {
                sw.Stop();
                PreviewContent = resp.Message ?? "转换失败。";
                StatusMessage = $"失败：{resp.Message ?? "未知原因"}";
                _notifications.Error(resp.Message ?? "转换失败");
                DebugLog.Error($"转换失败(Success=false): message='{resp.Message}' 耗时{sw.ElapsedMilliseconds}ms", "Convert");
            }
        }
        catch (ApiException ex)
        {
            sw.Stop();
            StatusMessage = ex.Code == "TIMEOUT"
                ? "转换超时：后端处理时间过长（OCR/嵌入耗时任务），已自动取消本次请求，可稍后重试或到「日志」页查看后端进度"
                : $"API 错误：{ex.Message}";
            DebugLog.Error($"转换 API 错误: code={ex.Code} message={ex.Message} 耗时{sw.ElapsedMilliseconds}ms", "Convert", ex);
        }
        catch (BackendConnectionException ex)
        {
            sw.Stop();
            StatusMessage = $"后端不可达：{ex.Message}";
            DebugLog.Error($"转换后端不可达: {ex.Message} 耗时{sw.ElapsedMilliseconds}ms", "Convert", ex);
        }
        catch (Exception ex)
        {
            sw.Stop();
            StatusMessage = $"错误：{ex.Message}";
            DebugLog.Error($"转换未知异常 耗时{sw.ElapsedMilliseconds}ms", "Convert", ex);
        }
        finally
        {
            IsBusy = false;
            DebugLog.Info($"转换流程结束，总耗时{sw.ElapsedMilliseconds}ms", "Convert");
        }
    }

    /// <summary>清空输入/输出/预览。</summary>
    [RelayCommand]
    private void Reset()
    {
        InputPath = string.Empty;
        OutputPath = string.Empty;
        PreviewContent = string.Empty;
        LastResult = null;
        StatusMessage = "就绪";
    }
}
