# WPF 任务 #3：搜索页面

> **状态：可立即开始**
> **依赖：WPF 任务 #1 + #2 完成**
> **后端依赖：`POST /v1/search`（Python 阶段 6-8 实现，可先用 mock 数据）**

---

## 目标

实现知识库语义搜索页面：搜索框 + 结果列表 + 命中详情，支持分页、集合切换、Top-K 调节。

---

## UI 布局 (`Views/SearchView.xaml`)

```
┌─────────────────────────────────────────────────────────┐
│ [搜索框................] [搜索按钮]   集合▾ Top-K: 10   │
├─────────────────────────────────────────────────────────┤
│  结果 47 条，耗时 47ms                                 │
├─────────────────────────────────────────────────────────┤
│  ▸ 0.873  report.pdf 第5页                             │
│    Transformer 采用多头自注意力机制...                 │
│    [vector 0.91] [bm25 0.74] [hybrid]                  │
│  ──────────────────────────────────────────────────     │
│  ▸ 0.812  notes.md 第3节                              │
│    ...                                                  │
├─────────────────────────────────────────────────────────┤
│  ‹ 1 2 3 ›                    每页 20 ▾                │
└─────────────────────────────────────────────────────────┘
```

- 搜索框回车即触发搜索
- 结果项可点击，点击后右侧详情面板显示完整 Chunk + 高亮命中片段
- 顶栏"集合下拉"和"Top-K 数字框"是搜索参数

---

## ViewModel (`ViewModels/SearchViewModel.cs`)

继承 `ViewModelBase`，用 CommunityToolkit.Mvvm 源生成器：

```csharp
public partial class SearchViewModel : ViewModelBase
{
    private readonly IDoc2kbApiService _api;

    [ObservableProperty] private string _query = string.Empty;
    [ObservableProperty] private string _collection = "default";
    [ObservableProperty] private int _topK = 10;
    [ObservableProperty] private bool _isSearching;
    [ObservableProperty] private ObservableCollection<SearchHit> _hits = new();
    [ObservableProperty] private SearchHit? _selectedHit;
    [ObservableProperty] private string _elapsedText = "";

    public SearchViewModel(IDoc2kbApiService api) { _api = api; }

    [RelayCommand(CanExecute = nameof(CanSearch))]
    private async Task SearchAsync()
    {
        IsSearching = true;
        try
        {
            var resp = await _api.SearchAsync(new SearchRequest
            {
                Query = Query, Collection = Collection, TopK = TopK
            });
            Hits.Clear();
            foreach (var h in resp.Hits) Hits.Add(h);
            ElapsedText = $"结果 {resp.Total} 条，耗时 {resp.ElapsedMs}ms";
        }
        catch (BackendConnectionException) { /* 顶栏状态灯处理 */ }
        catch (ApiException ex) { /* 错误条提示 */ }
        finally { IsSearching = false; }
    }

    private bool CanSearch => !IsSearching && !string.IsNullOrWhiteSpace(Query);
}
```

分页说明：第一版**不做真分页**，`TopK` 上限设 50，结果全部载入内存列表；
真分页留到 WPF 任务 #8。

---

## 关键点

1. **`SelectedHit` 双向绑定**到 `MainViewModel.SelectedHit`，右侧详情面板订阅它。
2. **命令可用性**：搜索中禁用按钮和回车（`CanExecute`）。
3. **取消支持**：`SearchAsync` 接受 `CancellationToken`，用户重新搜索时取消上一次。
4. **空状态**：未搜索时显示"输入关键词开始搜索"；无结果显示"未找到相关内容"。
5. **后端未就绪**：搜索按钮禁用 + tooltip "等待后端启动"。

---

## 验收标准

- [ ] `dotnet build` 通过
- [ ] 输入查询回车 → 调 `SearchAsync` → 结果列表渲染
- [ ] 点击结果项 → 右侧详情面板更新
- [ ] 切换集合 / 调节 Top-K 后重新搜索生效
- [ ] 后端未启动时 UI 友好降级（不崩、有提示）
