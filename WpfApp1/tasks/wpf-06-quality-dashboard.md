# WPF 任务 #6：质量看板

> **状态：可立即开始**
> **依赖：WPF 任务 #1 + #2 完成**
> **后端依赖：`GET /v1/quality` + `GET /v1/stats`（Python 阶段 5-8 实现）**

---

## 目标

实现质量看板：概览卡片 + 图表（格式分布饼图、分块 token 分布直方图、标题层级覆盖柱状图）。

---

## UI 布局 (`Views/QualityView.xaml`)

```
┌─────────────────────────────────────────────────────────┐
│  集合: [全部 ▾]                          [刷新]          │
├──────────────┬──────────────┬──────────────┬───────────┤
│  文档数       │  分块数       │  平均 tokens  │ 空分块    │
│    23         │   1089        │    287.4      │   0      │
├──────────────┴──────────────┴──────────────┴───────────┤
│  ┌─ 格式分布 ─┐  ┌─ 分块 token 分布 ──────────┐         │
│  │  饼图       │  │  直方图                    │         │
│  │ pdf 65%    │  │  0-100 █████████           │         │
│  │ docx 35%   │  │  100-300 ████████████████  │         │
│  │            │  │  300+ ███████              │         │
│  └────────────┘  └────────────────────────────┘         │
├─────────────────────────────────────────────────────────┤
│  ┌─ 标题层级覆盖 ──────────────────────────┐            │
│  │ H1 ████████████ 23                       │            │
│  │ H2 ████████████████████ 56               │            │
│  │ H3 ░ 0                                   │            │
│  └──────────────────────────────────────────┘            │
│                                                          │
│  ⚠ 告警: 3 个超大分块 (>4000 chars)                     │
│  ⚠ 告警: 重复率 2% (正常 <1%)                           │
└─────────────────────────────────────────────────────────┘
```

---

## 图表库

`LiveChartsCore.SkiaSharpView.WPF`（v2.0.0-rc4.5 稳定可用）

`DocMind.csproj` 补充：
```xml
<PackageReference Include="LiveChartsCore.SkiaSharpView.WPF" Version="2.0.0-rc4.5" />
<PackageReference Include="SkiaSharp.Views.WPF" Version="2.88.7" />
```

> LiveChartsCore v2 需要 SkiaSharp。WPF 包含 SkiaSharp native deps，开箱即用。

---

## ViewModel (`ViewModels/QualityViewModel.cs`)

```csharp
public partial class QualityViewModel : ViewModelBase
{
    [ObservableProperty] private string? _collection;
    [ObservableProperty] private bool _isLoading;
    [ObservableProperty] private QualityReport? _report;
    [ObservableProperty] private Stats? _stats;

    // LiveCharts 系列
    public ISeries[] FormatSeries { get; set; } = Array.Empty<ISeries>();
    public ISeries[] TokenHistogramSeries { get; set; } = Array.Empty<ISeries>();
    public ISeries[] HeadingLevelSeries { get; set; } = Array.Empty<ISeries>();

    [RelayCommand]
    private async Task LoadAsync()
    {
        IsLoading = true;
        try
        {
            Stats = await _api.GetStatsAsync(Collection);
            Report = await _api.GetQualityAsync(Collection);
            BuildCharts();
        }
        finally { IsLoading = false; }
    }

    private void BuildCharts()
    {
        if (Report == null) return;
        // 饼图
        FormatSeries = Report.FormatDistribution
            .Select(kv => new PieSeries<double> { Values = new[] { kv.Value },
                Name = kv.Key })
            .Cast<ISeries>().ToArray();
        // 直方图、柱状图类似
        OnPropertyChanged(nameof(FormatSeries));
        OnPropertyChanged(nameof(TokenHistogramSeries));
        OnPropertyChanged(nameof(HeadingLevelSeries));
    }
}
```

---

## 告警逻辑

在 ViewModel 里基于 `QualityReport` 计算告警列表：

| 条件 | 告警 |
|---|---|
| `oversized_chunks > 0` | ⚠ N 个超大分块 |
| `empty_chunks > 0` | ⚠ N 个空分块 |
| `duplicate_ratio > 0.05` | ⚠ 重复率高 (X%) |
| `coverage_by_heading_level` 全为 0 | ⚠ 文档无结构化标题 |

---

## 验收标准

- [ ] 切换集合后图表刷新
- [ ] 三种图表正确渲染（饼/直方/柱）
- [ ] 告警列表根据数据动态生成
- [ ] 空集合状态友好（不崩、有占位）
