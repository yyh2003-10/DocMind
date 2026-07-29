# WPF 任务 #8：系统托盘 + 后端进程管理 + 打包

> **状态：阻塞，需 WPF 任务 #1-7 全部完成**
> **依赖：WPF 任务 #1-7**
> **后端依赖：`GET /v1/health` 轮询、`doc2mind serve` 子进程**

---

## 目标

- WPF 最小化到系统托盘，后台常驻
- 启动时自动拉起 Python 后端子进程（`doc2mind serve`），关闭时优雅退出
- 后端状态实时反映到顶栏状态灯
- 最终打包为单文件 EXE + 资源

---

## 实现清单

### 8.1 系统托盘 (`Services/TrayService.cs`)

NuGet：`H.NotifyIcon.Wpf` 2.1.3（Windows 11 原生托盘支持）

```csharp
public partial class TrayService : ObservableObject
{
    [ObservableProperty] private string _status = "离线";   // 在线/离线/启动中
    [ObservableProperty] private string _tooltip = "DocMind - 离线";

    public TaskbarIcon TrayIcon { get; }

    public TrayService()
    {
        TrayIcon = new TaskbarIcon
        {
            IconSource = new BitmapImage(...),  // 嵌入资源 DocMind.ico
            ToolTipText = "DocMind",
            ContextMenuStripItems = ...          // 显示主窗口 / 退出
        };
    }
}
```

行为：
- 窗口"最小化"→ 隐藏到托盘（不显示在任务栏）
- 托盘双击 → 显示主窗口
- 右键菜单 → 显示主窗口 / 设置 / 退出
- 退出前确认有未完成的长任务

### 8.2 后端进程管理 (`Services/BackendProcessService.cs`)

```csharp
public class BackendProcessService : IDisposable
{
    private Process? _python;
    private readonly AppSettings _settings;

    public async Task<bool> StartAsync(IProgress<string>? progress, CancellationToken ct)
    {
        // 找到 doc2mind 命令
        var cmd = Environment.GetEnvironmentVariable("DOC2MIND_CMD") ?? "doc2mind";
        var psi = new ProcessStartInfo
        {
            FileName = cmd,
            Arguments = $"serve --host 127.0.0.1 --port {_settings.Port}",
            UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true
        };
        _python = Process.Start(psi);
        // 轮询 /v1/health 最多 startupTimeoutSec
        return await PollHealthAsync(ct);
    }

    public async Task StopAsync(CancellationToken ct)
    {
        if (_python is null || _python.HasExited) return;
        // 优先发 SIGTERM / Ctrl+C
        _python.StandardInput.Close();   // 让 doc2mind serve 收到 stdin 关闭
        if (!await WaitForExitAsync(5000, ct))
            _python.Kill(entireProcessTree: true);
    }
}
```

集成到 `App.xaml.cs`：
- `OnStartup`：`BackendProcessService.StartAsync` → `MainViewModel` 健康检查
- `OnExit`：`BackendProcessService.StopAsync` → 等待子进程退出

### 8.3 SSE 事件流（可选）

订阅 `GET /v1/events` 实现实时进度（替代轮询）：

```csharp
// 用 HttpClient.GetStreamAsync + 手动解析 SSE
```

时间紧可跳过，留 v0.2。

### 8.4 打包

**方案 A（推荐）：自包含单文件**

`DocMind.csproj` 加：
```xml
<PublishSingleFile>true</PublishSingleFile>
<SelfContained>true</SelfContained>
<RuntimeIdentifier>win-x64</RuntimeIdentifier>
<IncludeNativeLibrariesForSelfExtract>true</IncludeNativeLibrariesForSelfExtract>
<EnableCompressionInSingleFile>true</EnableCompressionInSingleFile>
```

```bash
dotnet publish -c Release
# 输出 bin\Release\net8.0-windows\win-x64\publish\DocMind.exe (~150MB)
```

**方案 B：MSIX 安装包**（Windows 应用商店分发）

需要 Windows Application Packaging Project 模板。可选。

### 8.5 安装器

`Inno Setup` 脚本（可选）：把 `DocMind.exe` + Python 后端（`pyinstaller --onefile doc2mind`）打成单一安装包。

---

## 验收标准

- [ ] 窗口最小化到托盘，双击恢复
- [ ] 启动时自动拉起 `doc2mind serve`，状态灯由"启动中"→"在线"
- [ ] 退出时优雅杀子进程（先 SIGTERM 再 SIGKILL）
- [ ] `dotnet publish -c Release` 产出可运行的单文件 EXE
- [ ] 在干净机器（无 .NET 8）上能启动
