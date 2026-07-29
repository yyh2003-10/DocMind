# WPF 任务 #7：设置页面

> **状态：可立即开始**
> **依赖：WPF 任务 #1 完成**
> **后端依赖：无（纯本地配置读写）**

---

## 目标

实现设置页面：后端地址 / 嵌入模型 / 分块参数 / 主题切换，配置持久化到 `appsettings.json`。

---

## UI 布局 (`Views/SettingsView.xaml`)

```
┌─────────────────────────────────────────────────────────┐
│  连接                                                    │
│    后端地址: [http://127.0.0.1:8765]                    │
│    启动超时: [30] 秒                                     │
│    ☑ 启动时自动拉起后端                                  │
│                                                          │
│  嵌入                                                    │
│    嵌入模型: [BAAI/bge-small-zh-v1.5 ▾]                 │
│      选项: small (35MB) / base (220MB) / large (670MB)  │
│    ☐ 使用 GPU (需 doc2mind[gpu])                        │
│                                                          │
│  分块                                                    │
│    最大块大小: [1500] tokens                             │
│    最小块大小: [50] chars                                │
│    重叠: [200] chars                                     │
│                                                          │
│  外观                                                    │
│    主题: ● 浅色  ○ 深色  ○ 跟随系统                     │
│    字体: [微软雅黑 ▾]  字号: [14]                       │
│                                                          │
│  [保存]  [重置为默认]  [测试连接]                        │
└─────────────────────────────────────────────────────────┘
```

---

## ViewModel (`ViewModels/SettingsViewModel.cs`)

```csharp
public partial class SettingsViewModel : ViewModelBase
{
    private readonly AppSettings _settings;

    [ObservableProperty] private string _backendUrl;
    [ObservableProperty] private int _startupTimeoutSec;
    [ObservableProperty] private bool _autoStartBackend;
    [ObservableProperty] private string _embedModel;
    [ObservableProperty] private bool _useGpu;
    [ObservableProperty] private int _maxChunkTokens;
    [ObservableProperty] private int _minChunkChars;
    [ObservableProperty] private int _overlapChars;
    [ObservableProperty] private string _theme;
    [ObservableProperty] private string _fontFamily;
    [ObservableProperty] private int _fontSize;
    [ObservableProperty] private bool _isTesting;
    [ObservableProperty] private string _testResult = "";

    public SettingsViewModel(AppSettings settings) { _settings = settings; Load(); }

    private void Load()
    {
        BackendUrl = _settings.BackendUrl;
        StartupTimeoutSec = _settings.StartupTimeoutSec;
        // ... 其余从 _settings 读
    }

    [RelayCommand]
    private void Save()
    {
        _settings.BackendUrl = BackendUrl;
        _settings.StartupTimeoutSec = StartupTimeoutSec;
        // ... 其余写回
        _settings.Save();   // 序列化回 appsettings.json
    }

    [RelayCommand]
    private void Reset() { _settings.ResetToDefaults(); Load(); }

    [RelayCommand]
    private async Task TestConnectionAsync()
    {
        IsTesting = true;
        try
        {
            using var client = new HttpClient { BaseAddress = new Uri(BackendUrl),
                                                Timeout = TimeSpan.FromSeconds(5) };
            var resp = await client.GetAsync("/v1/health");
            TestResult = resp.IsSuccessStatusCode ? "✓ 连接成功" : $"✗ HTTP {resp.StatusCode}";
        }
        catch { TestResult = "✗ 无法连接"; }
        finally { IsTesting = false; }
    }
}
```

---

## 配置持久化

`AppSettings.Save()` 把当前对象序列化回 `appsettings.json`：

```csharp
public void Save()
{
    var json = JsonSerializer.Serialize(this, new JsonSerializerOptions
    {
        WriteIndented = true, PropertyNamingPolicy = JsonNamingPolicy.CamelCase
    });
    File.WriteAllText("appsettings.json", json);
}
```

主题切换：通过 `App.Current.Resources.MergedDictionaries` 动态切换 `Theme.Light.xaml` / `Theme.Dark.xaml`。

---

## 验收标准

- [ ] 修改后端地址 → "测试连接"验证
- [ ] "保存"写入 `appsettings.json`，重启后保留
- [ ] "重置为默认"恢复出厂值
- [ ] 主题切换立即生效（不需重启）
