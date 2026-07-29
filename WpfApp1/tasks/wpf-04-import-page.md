# WPF 任务 #4：导入页面

> **状态：可立即开始**
> **依赖：WPF 任务 #1 + #2 完成**
> **后端依赖：`POST /v1/ingest` + `GET /v1/jobs/{id}`（轮询进度）**

---

## 目标

实现文档摄入页面：拖拽 + 文件选择 + 集合指定 + 进度条 + 结果列表。

---

## UI 布局 (`Views/ImportView.xaml`)

```
┌─────────────────────────────────────────────────────────┐
│  目标集合: [default ▾]  □ 递归子目录                    │
├─────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────┐   │
│  │                                                   │   │
│  │     拖拽文件到这里                                │   │
│  │     或点击 [选择文件] [选择目录]                  │   │
│  │                                                   │   │
│  │  已选 3 项：                                      │   │
│  │   • report.pdf  (2.1 MB)                          │   │
│  │   • data.xlsx  (45 KB)                            │   │
│  │   • notes.md  (12 KB)                             │   │
│  └─────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────┤
│  [开始摄入]  [清空]                                     │
├─────────────────────────────────────────────────────────┤
│  进度: ████████░░░░ 65%   处理中 report.pdf...         │
├─────────────────────────────────────────────────────────┤
│  最近摄入:                                              │
│   ✓ report.pdf → papers 集合 (47 块)                  │
│   ⊘ notes.md   (已存在，跳过)                          │
│   ✗ scan.tiff  (OCR 未安装)                           │
└─────────────────────────────────────────────────────────┘
```

---

## ViewModel (`ViewModels/ImportViewModel.cs`)

```csharp
public partial class ImportViewModel : ViewModelBase
{
    [ObservableProperty] private string _collection = "default";
    [ObservableProperty] private bool _recursive;
    [ObservableProperty] private ObservableCollection<SelectedFile> _selectedFiles = new();
    [ObservableProperty] private bool _isImporting;
    [ObservableProperty] private double _progress;
    [ObservableProperty] private string _currentFile = "";
    [ObservableProperty] private ObservableCollection<IngestResultRow> _recent = new();

    [RelayCommand] private void PickFiles() { /* OpenFileDialog, 多选 */ }
    [RelayCommand] private void PickDirectory() { /* FolderBrowserDialog */ }
    [RelayCommand] private void Clear() { SelectedFiles.Clear(); }
    [RelayCommand(CanExecute = nameof(CanImport))]
    private async Task ImportAsync() { /* 见下 */ }
}
```

### 拖拽支持

`SearchView` 的根 `Grid` 设 `AllowDrop=true`，处理 `Drop` 事件：
- 文件 → 加入 `SelectedFiles`
- 目录 → 展开为文件列表（递归按 `Recursive` 复选框）

### 摄入流程

```csharp
private async Task ImportAsync()
{
    IsImporting = true;
    Progress = 0;
    try
    {
        // 逐文件调用 ingest（v1 后端无批量端点；目录场景由后端自己递归）
        for (int i = 0; i < SelectedFiles.Count; i++)
        {
            CurrentFile = SelectedFiles[i].Name;
            var resp = await _api.IngestAsync(new IngestRequest
            {
                Path = SelectedFiles[i].Path,
                Collection = Collection,
                Recursive = Recursive,
                Force = false
            });
            // 每个结果追加到 Recent
            Progress = (i + 1.0) / SelectedFiles.Count * 100;
        }
    }
    finally { IsImporting = false; }
}
```

> 注：若 `Path` 是目录，后端会自己递归并返回 `total_documents`；WPF 这层不重复算文件数，进度按"提交的项数"算即可。

---

## 验收标准

- [ ] 拖拽文件/目录进入选区
- [ ] 点击选择按钮弹原生对话框
- [ ] "开始摄入"调 API，进度条实时更新，完成后结果进 `Recent` 列表
- [ ] 后端返回 `409 CONFLICT`（已存在）时显示"跳过"而非错误
- [ ] 后端返回 `415 UNSUPPORTED_FORMAT` 时该行标红并提示原因
