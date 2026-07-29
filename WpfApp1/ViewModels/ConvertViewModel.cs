using System.IO;
using CommunityToolkit.Mvvm.Input;
using DocMind.Models;
using DocMind.Services;

namespace DocMind.ViewModels;

public partial class ConvertViewModel : ViewModelBase
{
    private readonly IDoc2kbApiService _apiService;

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

    public ConvertViewModel(IDoc2kbApiService apiService)
    {
        _apiService = apiService;
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
            }
        }
    }

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

        try
        {
            var resp = await _apiService.ConvertAsync(
                new ConvertRequest
                {
                    InputPath = InputPath.Trim(),
                    OutputPath = string.IsNullOrWhiteSpace(OutputPath) ? string.Empty : OutputPath.Trim(),
                    Format = Format,
                });

            LastResult = resp;
            if (resp.Success)
            {
                // 后端返回 outputPath 时优先用之
                var actualOut = resp.OutputPath ?? OutputPath;
                if (!string.IsNullOrWhiteSpace(actualOut) && File.Exists(actualOut))
                {
                    var text = await File.ReadAllTextAsync(actualOut);
                    PreviewContent = text.Length > 10_000
                        ? text[..10_000] + "\n\n…（预览截断，共 " + text.Length + " 字符）"
                        : text;
                }
                else
                {
                    PreviewContent = resp.Message ?? "转换成功，但未返回可预览内容。";
                }
                StatusMessage = $"完成 → {actualOut}";
            }
            else
            {
                PreviewContent = resp.Message ?? "转换失败。";
                StatusMessage = $"失败：{resp.Message ?? "未知原因"}";
            }
        }
        catch (ApiException ex)
        {
            StatusMessage = $"API 错误：{ex.Message}";
        }
        catch (BackendConnectionException ex)
        {
            StatusMessage = $"后端不可达：{ex.Message}";
        }
        catch (Exception ex)
        {
            StatusMessage = $"错误：{ex.Message}";
        }
        finally
        {
            IsBusy = false;
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
