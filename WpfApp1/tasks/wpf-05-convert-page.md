# WPF 任务 #5：格式转换页面

> **状态：可立即开始**
> **依赖：WPF 任务 #1 + #2 完成**
> **后端依赖：`POST /v1/convert`（Python 阶段 6 实现）**

---

## 目标

实现格式互转页面：文件选择 + 目标格式 + 输出方式 + 预览。

---

## UI 布局 (`Views/ConvertView.xaml`)

```
┌─────────────────────────────────────────────────────────┐
│  源文件: [report.pdf ...............] [选择]            │
│  目标格式: ● MD  ○ JSON  ○ TXT  ○ HTML                 │
│  输出:    ● 预览   ○ 保存到文件 [.................]    │
│            ○ 批量输出到目录 [.................]        │
├─────────────────────────────────────────────────────────┤
│  [转换]   元素数: 47    耗时: 120ms                     │
├─────────────────────────────────────────────────────────┤
│  ┌─ 预览 ──────────────────────────────────────────┐   │
│  │ # Report                                          │   │
│  │                                                   │   │
│  │ ## 第三节 注意力机制                              │   │
│  │ Transformer 采用多头自注意力机制...              │   │
│  │                                                   │   │
│  │ | Q | K | V |                                     │   │
│  │ |---|---|---|                                     │   │
│  │ | 64 | 64 | 64 |                                  │   │
│  └─────────────────────────────────────────────────┘   │
│  [复制]  [另存为]                                        │
└─────────────────────────────────────────────────────────┘
```

---

## ViewModel (`ViewModels/ConvertViewModel.cs`)

```csharp
public partial class ConvertViewModel : ViewModelBase
{
    [ObservableProperty] private string _inputPath = "";
    [ObservableProperty] private string _outputFormat = "md";
    [ObservableProperty] private OutputMode _outputMode = OutputMode.Preview;
    [ObservableProperty] private string _outputPath = "";
    [ObservableProperty] private string _outputDir = "";
    [ObservableProperty] private string _previewContent = "";
    [ObservableProperty] private int _elementsCount;
    [ObservableProperty] private int _elapsedMs;
    [ObservableProperty] private bool _isConverting;

    [RelayCommand] private void PickFile() { /* OpenFileDialog */ }
    [RelayCommand] private void PickOutputFile() { /* SaveFileDialog */ }
    [RelayCommand] private void PickOutputDir() { /* FolderBrowserDialog */ }

    [RelayCommand(CanExecute = nameof(CanConvert))]
    private async Task ConvertAsync()
    {
        IsConverting = true;
        try
        {
            var req = new ConvertRequest
            {
                InputPath = InputPath,
                OutputFormat = OutputFormat,
                OutputPath = OutputMode == OutputMode.SaveFile ? OutputPath : null
            };
            var result = await _api.ConvertAsync(req);
            PreviewContent = result.Content ?? "";
            ElementsCount = result.ElementsCount;
        }
        finally { IsConverting = false; }
    }

    private bool CanConvert => !IsConverting && !string.IsNullOrWhiteSpace(InputPath);
}

public enum OutputMode { Preview, SaveFile, SaveDir }
```

---

## 预览渲染

- `MD` 格式：用 `Markdig` (NuGet: `Markdig` 0.37) 转 FlowDocument，在 `RichTextBox` 显示。
- `JSON` 格式：`JsonSerializer.Serialize` 美化后纯文本显示。
- `TXT` / `HTML`：纯文本显示。

NuGet 补充：
```xml
<PackageReference Include="Markdig" Version="0.37.1" />
```

---

## 验收标准

- [ ] 选 PDF → 转 MD → 预览区显示 Markdown
- [ ] 切换"保存到文件"模式后，转换写盘
- [ ] 不支持的源格式（如 `.exe`）后端返回 415，UI 红字提示
