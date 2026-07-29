# WPF 任务 #1：项目重构 + MVVM 骨架 + 主窗口布局

> **状态：可立即开始**（无依赖）
> **负责：Visual Studio Copilot**
> **Python 后端依赖：无**

---

## 目标

把当前 VS 默认生成的空 WPF 骨架，重构为符合 DocMind 架构的 MVVM 项目骨架，
搭好主窗口的三栏布局（左侧导航 / 中间内容区 / 右侧详情面板），
并建立后续 7 个任务共同依赖的目录结构与基类。

---

## 当前项目状态

- 项目路径：`E:\DocMind\WpfApp1\`
- 解决方案：`WpfApp1.sln`
- 项目文件：`DocMind.csproj`（已配置 `RootNamespace=DocMind`、`AssemblyName=DocMind`、`TargetFramework=net8.0-windows`、`UseWPF=true`、`Nullable=enable`、`ImplicitUsings=enable`）
- 已添加 NuGet 包：
  - `CommunityToolkit.Mvvm` 8.2.2 —— MVVM 框架（`ObservableObject` / `[ObservableProperty]` / `[RelayCommand]` / `IMessenger`）
  - `System.Text.Json` 8.0.4 —— HTTP JSON 序列化
- 现有文件（VS 默认生成，namespace 仍是 `WpfApp1`，需要全部改为 `DocMind`）：
  - `App.xaml` / `App.xaml.cs`
  - `MainWindow.xaml` / `MainWindow.xaml.cs`
  - `AssemblyInfo.cs`

---

## 任务清单

### 1.1 全局命名空间统一

把所有 `.cs` / `.xaml` 文件的 namespace 从 `WpfApp1` 统一改为 `DocMind`。
包括 `x:Class`、`xmlns:local`、`StartupUri` 等。

### 1.2 建立目录结构

按 HANDOVER §5 的 WPF 结构创建目录：

```
WpfApp1/
├── DocMind.csproj
├── WpfApp1.sln
├── App.xaml / App.xaml.cs          # 已有，改 namespace
├── MainWindow.xaml / .cs           # 已有，重写为三栏布局
├── Models/                         # 数据模型（DTO）
├── ViewModels/                     # ViewModel（每个页面一个）
├── Views/                          # 页面 UserControl
├── Services/                       # HttpClient 封装、配置、进程管理
├── Converters/                     # 值转换器
└── Styles/                         # 全局样式 / 主题
```

每个目录放一个占位 `.gitkeep` 或一个简单的类文件，确保目录被 git 跟踪。

### 1.3 MVVM 基类与基础设施

在 `ViewModels/` 下创建：

- `ViewModelBase.cs` —— 继承 `ObservableObject`，提供 `SetTitle`、`IsActive` 等公共属性。
- `MainViewModel.cs` —— 主窗口 ViewModel，管理当前激活的页面 ViewModel（`CurrentPage`），以及左侧导航命令。
- `NavigationItem.cs` —— 导航项模型（图标 + 标题 + 目标 ViewModel 类型）。

使用 CommunityToolkit.Mvvm 的源生成器：
```csharp
public partial class ViewModelBase : ObservableObject
{
    [ObservableProperty]
    private string _title = string.Empty;
}
```

### 1.4 主窗口三栏布局

重写 `MainWindow.xaml` 为：

```
┌──────────────────────────────────────────────────────────────┐
│  顶栏：DocMind logo + 后端状态指示灯 + 设置按钮              │
├────────────┬─────────────────────────────────┬───────────────┤
│  左侧导航   │  中间内容区                      │  右侧详情面板  │
│  (160px)   │  (ContentControl, 弹性宽度)      │  (300px, 可折叠)│
│            │                                 │               │
│  ▸ 搜索     │  当前页面的 View 在这里渲染      │  选中项详情    │
│  ▸ 导入     │                                 │               │
│  ▸ 转换     │                                 │               │
│  ▸ 质量看板 │                                 │               │
│  ▸ 设置     │                                 │               │
├────────────┴─────────────────────────────────┴───────────────┤
│  底栏：后端地址 localhost:8765 + 文档数 + 分块数              │
└──────────────────────────────────────────────────────────────┘
```

技术要点：
- 用 `Grid` 三列布局：`160 / * / 300`
- 左侧导航用 `ListBox` + 自定义 `ItemTemplate`（图标 + 文字）
- 中间用 `ContentControl Content="{Binding CurrentPage.View}"`
- 右侧用 `Expander` 或可折叠 `GridSplitter`
- 顶栏 / 底栏用固定高度的 `RowDefinition`

### 1.5 导航占位页面

在 `Views/` 下创建 5 个占位 `UserControl`，内容只需一个居中的 `TextBlock` 显示页面名：

- `SearchView.xaml` —— "搜索页面"
- `ImportView.xaml` —— "导入页面"
- `ConvertView.xaml` —— "格式转换页面"
- `QualityView.xaml` —— "质量看板"
- `SettingsView.xaml` —— "设置页面"

对应的 ViewModel 放在 `ViewModels/` 下（`SearchViewModel.cs` 等），都继承 `ViewModelBase`。
`MainViewModel` 初始化时把 5 个导航项注册进去，默认激活"搜索"页。

### 1.6 全局样式与主题

在 `Styles/` 下创建：

- `Theme.xaml` —— 定义主色调（建议深色友好：主色 `#2D7FF9`，背景 `#1E1E1E`/`#FFFFFF`，文字 `#333`/`#EEE`）、字体（微软雅黑 14px 默认）、圆角按钮样式
- 在 `App.xaml` 的 `MergedDictionaries` 里引用 `Theme.xaml`

### 1.7 应用启动与依赖注入

在 `App.xaml.cs` 里：

- 用 `Microsoft.Extensions.DependencyInjection` 建立简易 DI 容器（如果不想加额外包，可以用静态 `ServiceProvider`）
- 注册：`MainViewModel`、5 个页面 ViewModel、`HttpClient`（命名客户端）、后续要加的 `Services/*`
- `OnStartup` 里创建 `MainWindow`，设置 `DataContext = _services.GetRequiredService<MainViewModel>()`，`Show()`
- 移除 `App.xaml` 里的 `StartupUri="MainWindow.xaml"`，改用代码启动（便于注入 ViewModel）

> 是否引入 `Microsoft.Extensions.DependencyInjection`：**引入**，后续 HttpClient/Services 都依赖它。
> NuGet 包：`Microsoft.Extensions.DependencyInjection` 8.0.0 + `Microsoft.Extensions.Http` 8.0.0

请相应更新 `DocMind.csproj` 增加：
```xml
<PackageReference Include="Microsoft.Extensions.DependencyInjection" Version="8.0.0" />
<PackageReference Include="Microsoft.Extensions.Http" Version="8.0.0" />
```

---

## 验收标准

- [ ] 全项目 namespace 统一为 `DocMind`，无残留 `WpfApp1`
- [ ] `dotnet build` 在 `WpfApp1/` 下成功通过（0 error，0 warning 级别 ≤ CS1591 缺 XML 注释）
- [ ] F5 运行弹出主窗口：左侧 5 项导航、中间内容区、右侧详情面板、顶栏底栏齐全
- [ ] 点击左侧导航项，中间内容区切换到对应占位页面
- [ ] MVVM 基础设施就位：`ViewModelBase`、`MainViewModel`、DI 容器、`Theme.xaml`
- [ ] 目录结构完整：`Models/ ViewModels/ Views/ Services/ Converters/ Styles/` 都存在

---

## 完成后

把本文件顶部状态改为 `已完成`，并在文件末尾追加：

```
## 完成记录
- 完成日期：YYYY-MM-DD
- 实际改动：...
- 遗留问题：...
```

然后通知主控 AI（AtomCode）可以发布任务 #2。
