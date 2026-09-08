# DocMind UI 升级实施计划

> 基于 `ui-upgrade-evaluation.md` 诊断结果，本文档提供逐文件、逐行的具体修改方案。
> 标记说明：`[修改]` 改动现有代码 | `[新增]` 创建新文件/新代码段 | `[删除]` 移除现有代码

---

## 总览：修改文件清单

| 文件路径 | 操作类型 | Phase |
|----------|---------|-------|
| `Styles/Icons.xaml` | [新增] | P1 |
| `Controls/IconControl.cs` | [新增] | P1 |
| `Styles/Theme.xaml` | [修改] | P2/P4/P5/P7 |
| `Styles/Theme.Dark.xaml` | [修改] | P2/P4/P5/P7 |
| `App.xaml` | [修改] | P1 |
| `ViewModels/NavigationItem.cs` | [修改] | P1 |
| `ViewModels/MainViewModel.cs` | [修改] | P1 |
| `MainWindow.xaml` | [修改] | P1/P3/P8 |
| `Views/SearchView.xaml` | [修改] | P1/P3/P6 |
| `Views/ImportView.xaml` | [修改] | P1/P3/P6 |
| `Views/ConvertView.xaml` | [修改] | P1/P3 |
| `Views/QualityView.xaml` | [修改] | P1/P3 |
| `Views/GraphView.xaml` | [修改] | P1/P3 |
| `Views/DocumentsView.xaml` | [修改] | P1/P3 |
| `Views/SettingsView.xaml` | [修改] | P1 |
| `Views/ChatView.xaml` | [修改] | P1/P3 |
| `Controls/ToastControl.cs` | [修改] | P1/P4 |
| `DocMind.csproj` | [修改] | P1 |

---

## Phase 1：矢量图标系统（P0 - 最高优先级）

### 1.1 [新增] 创建 `Styles/Icons.xaml`

用 WPF `Geometry` 资源定义所有图标路径数据，线宽统一 1.5。

```xml
<ResourceDictionary xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
                    xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml">

    <!-- ========== 图标 Geometry 资源 (Fluent Icons 线条风格, 24x24 viewport) ========== -->

    <!-- 对话 -->
    <Geometry x:Key="IconChat">M12 2C6.48 2 2 5.95 2 10.8c0 2.4 1.1 4.57 2.85 6.1L4 22l4.9-1.95c.97.22 1.99.35 3.1.35 5.52 0 10-3.95 10-8.8S17.52 2 12 2z</Geometry>

    <!-- 搜索 -->
    <Geometry x:Key="IconSearch">M10 2.75A7.25 7.25 0 1 1 2.75 10 7.25 7.25 0 0 1 10 2.75zM16.5 16.5l4.75 4.75M4.5 10a5.5 5.5 0 1 0 11 0 5.5 5.5 0 0 0-11 0z</Geometry>

    <!-- 知识图谱 (节点连线) -->
    <Geometry x:Key="IconGraph">M6 3a3 3 0 1 1 0 6 3 3 0 0 1 0-6zm12 0a3 3 0 1 1 0 6 3 3 0 0 1 0-6zM6 15a3 3 0 1 1 0 6 3 3 0 0 1 0-6zm12 0a3 3 0 1 1 0 6 3 3 0 0 1 0-6zM8.5 6.5l7 2M8.5 17.5l7-2M8.5 8v8</Geometry>

    <!-- 文档库 (文件夹) -->
    <Geometry x:Key="IconFolder">M3 5a2 2 0 0 1 2-2h4.5a2 2 0 0 1 1.6.8l1.2 1.2H19a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5z</Geometry>

    <!-- 导入 (向下箭头入框) -->
    <Geometry x:Key="IconImport">M12 2v10m0 0l-4-4m4 4l4-4M4 14v4a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-4</Geometry>

    <!-- 转换 (双向箭头循环) -->
    <Geometry x:Key="IconConvert">M4 9a5 5 0 0 1 5-5h8l-2-2m2 2l-2 2M20 15a5 5 0 0 1-5 5H7l2-2m-2 2l2-2</Geometry>

    <!-- 质量看板 (仪表盘) -->
    <Geometry x:Key="IconDashboard">M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zM12 2v5l3.5 3.5</Geometry>

    <!-- 设置 (齿轮) -->
    <Geometry x:Key="IconSettings">M12 2l1.5 2.5 2.8-.5 1 2.7 2.5 1.2-.5 2.8L21 12l-2.5 1.5.5 2.8-2.5 1.2-1 2.7-2.8-.5L12 22l-1.5-2.5-2.8.5-1-2.7-2.5-1.2.5-2.8L2 12l2.5-1.5-.5-2.8 2.5-1.2 1-2.7 2.8.5L12 2zM12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8z</Geometry>

    <!-- 调试日志 (列表) -->
    <Geometry x:Key="IconList">M4 5h16M4 12h16M4 19h10</Geometry>

    <!-- 文件 (通用) -->
    <Geometry x:Key="IconFile">M7 2h7l5 5v13a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm7 0v5h5</Geometry>

    <!-- 上传/选择文件 -->
    <Geometry x:Key="IconUpload">M12 22V12m0 0l-4 4m4-4l4 4M4 2h16v6H4z</Geometry>

    <!-- 刷新 -->
    <Geometry x:Key="IconRefresh">M4 12a8 8 0 0 1 14-5.3L20 8M20 4v4h-4M20 12a8 8 0 0 1-14 5.3L4 16M4 20v-4h4</Geometry>

    <!-- 警告 -->
    <Geometry x:Key="IconWarning">M12 2L2 20h20L12 2zM12 9v5M12 17v.5</Geometry>

    <!-- 成功 (勾选) -->
    <Geometry x:Key="IconCheck">M5 12l5 5L20 7</Geometry>

    <!-- 失败 (叉) -->
    <Geometry x:Key="IconCross">M6 6l12 12M18 6L6 18</Geometry>

    <!-- 跳过 -->
    <Geometry x:Key="IconSkip">M6 4v16l10-8zM18 4v16</Geometry>

    <!-- 文档全文 -->
    <Geometry x:Key="IconDocumentFull">M7 2h10a2 2 0 0 1 2 2v16a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zM9 7h6M9 11h6M9 15h4</Geometry>

    <!-- 锁 -->
    <Geometry x:Key="IconLock">M7 11V8a5 5 0 0 1 10 0v3M5 11h14v10H5z</Geometry>

    <!-- 火箭/启动 -->
    <Geometry x:Key="IconRocket">M12 2c3 2 5 5 5 9l-2 2H9l-2-2c0-4 2-7 5-9zM12 8a2 2 0 1 0 0 4 2 2 0 0 0 0-4zM9 15l-2 4M15 15l2 4</Geometry>

    <!-- AI 整理 (星星) -->
    <Geometry x:Key="IconAi">M12 2l2.5 6.5L21 9l-5 5 1 7-5-3-5 3 1-7-5-5 6.5-.5L12 2z</Geometry>

    <!-- 闪电 (速度/进行中) -->
    <Geometry x:Key="IconBolt">M13 2L4 14h7l-1 8 9-12h-7l1-8z</Geometry>

    <!-- 折叠面板/抽屉 -->
    <Geometry x:Key="IconPanelLeft">M4 5h16v14H4zM9 5v14</Geometry>

    <!-- 关闭 (X) -->
    <Geometry x:Key="IconClose">M6 6l12 12M18 6L6 18</Geometry>

    <!-- 最小化 -->
    <Geometry x:Key="IconMinimize">M4 12h16</Geometry>

    <!-- 最大化 -->
    <Geometry x:Key="IconMaximize">M4 4h16v16H4zM8 4v16</Geometry>

    <!-- 大脑/品牌 (自定义 DocMind Logo 抽象) -->
    <Geometry x:Key="IconDocMindLogo">M12 3a4 4 0 0 0-4 4v.5A3.5 3.5 0 0 0 5 14c0 1 .4 1.9 1 2.5V19a3 3 0 0 0 3 3h6a3 3 0 0 0 3-3v-2.5c.6-.6 1-1.5 1-2.5a3.5 3.5 0 0 0-3-6.5V7a4 4 0 0 0-4-4zM9 9h6M9 13h6M9 17h4</Geometry>

    <!-- ========== 图标样式 (Path 绑定 Geometry) ========== -->

    <Style x:Key="IconPathStyle" TargetType="Path">
        <Setter Property="StrokeThickness" Value="1.5"/>
        <Setter Property="Stretch" Value="Uniform"/>
        <Setter Property="StrokeLineJoin" Value="Round"/>
        <Setter Property="StrokeStartLineCap" Value="Round"/>
        <Setter Property="StrokeEndLineCap" Value="Round"/>
        <Setter Property="Fill" Value="Transparent"/>
        <Setter Property="Stroke" Value="{DynamicResource TextSecondaryBrush}"/>
    </Style>

    <!-- 导航轨图标专用：24x24 -->
    <Style x:Key="NavIconStyle" TargetType="Path" BasedOn="{StaticResource IconPathStyle}">
        <Setter Property="Width" Value="22"/>
        <Setter Property="Height" Value="22"/>
    </Style>

    <!-- 小图标：16x16 -->
    <Style x:Key="IconSmStyle" TargetType="Path" BasedOn="{StaticResource IconPathStyle}">
        <Setter Property="Width" Value="16"/>
        <Setter Property="Height" Value="16"/>
        <Setter Property="StrokeThickness" Value="1.5"/>
    </Style>

    <!-- 大图标/空态：48x48 -->
    <Style x:Key="IconLgStyle" TargetType="Path" BasedOn="{StaticResource IconPathStyle}">
        <Setter Property="Width" Value="48"/>
        <Setter Property="Height" Value="48"/>
        <Setter Property="StrokeThickness" Value="1.2"/>
    </Style>

</ResourceDictionary>
```

### 1.2 [修改] `App.xaml` — 注册 Icons.xaml

```xml
<!-- 修改前 -->
<ResourceDictionary.MergedDictionaries>
    <ResourceDictionary Source="Styles/Theme.xaml"/>
</ResourceDictionary.MergedDictionaries>

<!-- 修改后 -->
<ResourceDictionary.MergedDictionaries>
    <ResourceDictionary Source="Styles/Theme.xaml"/>
    <ResourceDictionary Source="Styles/Icons.xaml"/>
</ResourceDictionary.MergedDictionaries>
```

### 1.3 [修改] `ViewModels/NavigationItem.cs` — 增加 IconKey

```csharp
// 修改前
public string Icon { get; set; } = string.Empty;
public string? IconPath { get; set; }

// 修改后
public string Icon { get; set; } = string.Empty;        // 保留兼容回退
public string? IconPath { get; set; }                     // 保留兼容回退
public string? IconKey { get; set; }                      // 新增：Geometry 资源 key
```

### 1.4 [修改] `ViewModels/MainViewModel.cs` — 导航项使用 IconKey

```csharp
// 修改前 (第 222-234 行)
NavigationItems.Add(new NavigationItem { Title = "对话", Icon = "💬", Category = "工作台", ViewModelType = typeof(ChatViewModel) });
NavigationItems.Add(new NavigationItem { Title = "搜索", Icon = "🔍", IconPath = "Assets/nav-search.png", Category = "工作台", ViewModelType = typeof(SearchViewModel) });
NavigationItems.Add(new NavigationItem { Title = "知识图谱", Icon = "🕸️", Category = "工作台", ViewModelType = typeof(GraphViewModel) });
NavigationItems.Add(new NavigationItem { Title = "文档库", Icon = "🗂️", Category = "知识资产", ViewModelType = typeof(DocumentsViewModel) });
NavigationItems.Add(new NavigationItem { Title = "导入", Icon = "📥", IconPath = "Assets/nav-import.png", Category = "知识资产", ViewModelType = typeof(ImportViewModel) });
NavigationItems.Add(new NavigationItem { Title = "转换", Icon = "🔄", IconPath = "Assets/nav-convert.png", Category = "知识资产", ViewModelType = typeof(ConvertViewModel) });
NavigationItems.Add(new NavigationItem { Title = "质量看板", Icon = "📊", IconPath = "Assets/nav-quality.png", Category = "知识资产", ViewModelType = typeof(QualityViewModel) });
NavigationItems.Add(new NavigationItem { Title = "设置", Icon = "⚙️", IconPath = "Assets/nav-settings.png", Category = "系统与支持", ViewModelType = typeof(SettingsViewModel) });
NavigationItems.Add(new NavigationItem { Title = "调试日志", Icon = "📋", Category = "系统与支持", ViewModelType = typeof(DebugLogViewModel) });

// 修改后
NavigationItems.Add(new NavigationItem { Title = "对话", IconKey = "IconChat", Category = "工作台", ViewModelType = typeof(ChatViewModel) });
NavigationItems.Add(new NavigationItem { Title = "搜索", IconKey = "IconSearch", Category = "工作台", ViewModelType = typeof(SearchViewModel) });
NavigationItems.Add(new NavigationItem { Title = "知识图谱", IconKey = "IconGraph", Category = "工作台", ViewModelType = typeof(GraphViewModel) });
NavigationItems.Add(new NavigationItem { Title = "文档库", IconKey = "IconFolder", Category = "知识资产", ViewModelType = typeof(DocumentsViewModel) });
NavigationItems.Add(new NavigationItem { Title = "导入", IconKey = "IconImport", Category = "知识资产", ViewModelType = typeof(ImportViewModel) });
NavigationItems.Add(new NavigationItem { Title = "转换", IconKey = "IconConvert", Category = "知识资产", ViewModelType = typeof(ConvertViewModel) });
NavigationItems.Add(new NavigationItem { Title = "质量看板", IconKey = "IconDashboard", Category = "知识资产", ViewModelType = typeof(QualityViewModel) });
NavigationItems.Add(new NavigationItem { Title = "设置", IconKey = "IconSettings", Category = "系统与支持", ViewModelType = typeof(SettingsViewModel) });
NavigationItems.Add(new NavigationItem { Title = "调试日志", IconKey = "IconList", Category = "系统与支持", ViewModelType = typeof(DebugLogViewModel) });
```

### 1.5 [修改] `MainWindow.xaml` — 导航 ItemTemplate 用 Path 替换 emoji

**品牌图标** (第 281-285 行)：

```xml
<!-- 修改前 -->
<Border Grid.Row="0" Width="36" Height="36" CornerRadius="10"
        Background="{DynamicResource PrimaryLightBrush}"
        HorizontalAlignment="Center" Margin="0,0,0,12"
        ToolTip="DocMind 本地向量知识库">
    <TextBlock Text="🧠" FontSize="18" HorizontalAlignment="Center" VerticalAlignment="Center"/>
</Border>

<!-- 修改后 -->
<Border Grid.Row="0" Width="36" Height="36" CornerRadius="10"
        Background="{DynamicResource PrimaryLightBrush}"
        HorizontalAlignment="Center" Margin="0,0,0,12"
        ToolTip="DocMind 本地向量知识库">
    <Path Data="{StaticResource IconDocMindLogo}"
          Style="{StaticResource NavIconStyle}"
          Stroke="{DynamicResource PrimaryBrush}"
          Width="20" Height="20"/>
</Border>
```

**导航项 ItemTemplate** (第 294-333 行)：

```xml
<!-- 修改后：替换整个 DataTemplate -->
<DataTemplate DataType="{x:Type vm:NavigationItem}">
    <Grid ToolTip="{Binding Title}" Width="40" Height="40">
        <!-- 矢量图标 (优先) -->
        <Path Data="{Binding IconKey, Converter={StaticResource IconKeyToGeometryConverter}}"
              Style="{StaticResource NavIconStyle}"
              HorizontalAlignment="Center" VerticalAlignment="Center">
            <Path.Style>
                <Style TargetType="Path" BasedOn="{StaticResource NavIconStyle}">
                    <Setter Property="Visibility" Value="Visible"/>
                    <Style.Triggers>
                        <DataTrigger Binding="{Binding IconKey}" Value="{x:Null}">
                            <Setter Property="Visibility" Value="Collapsed"/>
                        </DataTrigger>
                    </Style.Triggers>
                </Style>
            </Path.Style>
        </Path>
        <!-- emoji 回退 (仅 IconKey 为空时) -->
        <TextBlock Text="{Binding Icon}"
                   FontSize="18"
                   VerticalAlignment="Center"
                   HorizontalAlignment="Center"
                   TextAlignment="Center">
            <TextBlock.Style>
                <Style TargetType="TextBlock">
                    <Setter Property="Visibility" Value="Collapsed"/>
                    <Style.Triggers>
                        <DataTrigger Binding="{Binding IconKey}" Value="{x:Null}">
                            <Setter Property="Visibility" Value="Visible"/>
                        </DataTrigger>
                    </Style.Triggers>
                </Style>
            </TextBlock.Style>
        </TextBlock>
    </Grid>
</DataTemplate>
```

> 需要新增 `IconKeyToGeometryConverter`：将 string key 转为 `Geometry` 资源查找。或直接用 `DynamicResource` 绑定（见 1.6 替代方案）。

**替代方案（更简洁，不需要 Converter）**：

```xml
<DataTemplate DataType="{x:Type vm:NavigationItem}">
    <Grid ToolTip="{Binding Title}" Width="40" Height="40">
        <Path Style="{StaticResource NavIconStyle}"
              HorizontalAlignment="Center" VerticalAlignment="Center">
            <Path.Data>
                <DynamicResource ResourceKey="IconChat"/>
                <!-- 注：WPF 不支持直接 DataBinding 到 Data 属性，需用 Converter 或下方方案 -->
            </Path.Data>
        </Path>
    </Grid>
</DataTemplate>
```

**最终推荐方案：用 ContentControl + DataTrigger 逐项硬编码**（避免 Converter，最稳妥）：

```xml
<DataTemplate DataType="{x:Type vm:NavigationItem}">
    <Grid ToolTip="{Binding Title}" Width="40" Height="40">
        <Path x:Name="NavIcon" Style="{StaticResource NavIconStyle}"
              Data="{StaticResource IconChat}"
              HorizontalAlignment="Center" VerticalAlignment="Center"/>
    </Grid>
    <DataTemplate.Triggers>
        <DataTrigger Binding="{Binding Title}" Value="搜索">
            <Setter TargetName="NavIcon" Property="Data" Value="{StaticResource IconSearch}"/>
        </DataTrigger>
        <DataTrigger Binding="{Binding Title}" Value="知识图谱">
            <Setter TargetName="NavIcon" Property="Data" Value="{StaticResource IconGraph}"/>
        </DataTrigger>
        <DataTrigger Binding="{Binding Title}" Value="文档库">
            <Setter TargetName="NavIcon" Property="Data" Value="{StaticResource IconFolder}"/>
        </DataTrigger>
        <DataTrigger Binding="{Binding Title}" Value="导入">
            <Setter TargetName="NavIcon" Property="Data" Value="{StaticResource IconImport}"/>
        </DataTrigger>
        <DataTrigger Binding="{Binding Title}" Value="转换">
            <Setter TargetName="NavIcon" Property="Data" Value="{StaticResource IconConvert}"/>
        </DataTrigger>
        <DataTrigger Binding="{Binding Title}" Value="质量看板">
            <Setter TargetName="NavIcon" Property="Data" Value="{StaticResource IconDashboard}"/>
        </DataTrigger>
        <DataTrigger Binding="{Binding Title}" Value="设置">
            <Setter TargetName="NavIcon" Property="Data" Value="{StaticResource IconSettings}"/>
        </DataTrigger>
        <DataTrigger Binding="{Binding Title}" Value="调试日志">
            <Setter TargetName="NavIcon" Property="Data" Value="{StaticResource IconList}"/>
        </DataTrigger>
    </DataTemplate.Triggers>
</DataTemplate>
```

**离线横幅** (第 232-243 行)：

```xml
<!-- 修改后 -->
<Button Command="{Binding RefreshBackendCommand}" Style="{StaticResource SecondaryButtonStyle}" Padding="10,4" Margin="0,0,8,0">
    <StackPanel Orientation="Horizontal">
        <Path Data="{StaticResource IconRefresh}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>
        <TextBlock Text="重新检测"/>
    </StackPanel>
</Button>
<Button Command="{Binding StartBackendCommand}" Style="{StaticResource PrimaryButtonStyle}" Padding="10,4" Margin="0,0,8,0">
    <StackPanel Orientation="Horizontal">
        <Path Data="{StaticResource IconRocket}" Style="{StaticResource IconSmStyle}" Stroke="White" Margin="0,0,6,0"/>
        <TextBlock Text="一键重启后端"/>
    </StackPanel>
</Button>
<Button Command="{Binding OpenDebugLogCommand}" Style="{StaticResource SecondaryButtonStyle}" Padding="10,4">
    <StackPanel Orientation="Horizontal">
        <Path Data="{StaticResource IconList}" Style="{StaticResource IconSmStyle}" Margin="0,0,6,0"/>
        <TextBlock Text="查看日志"/>
    </StackPanel>
</Button>
```

**横幅标题** (第 246-247 行)：

```xml
<!-- 修改前 -->
<TextBlock Text="⚠️ 后端服务离线" FontWeight="Bold" FontSize="13" Foreground="{DynamicResource DangerBrush}"/>

<!-- 修改后 -->
<StackPanel Orientation="Horizontal">
    <Path Data="{StaticResource IconWarning}" Style="{StaticResource IconSmStyle}" Stroke="{DynamicResource DangerBrush}" Margin="0,0,6,0"/>
    <TextBlock Text="后端服务离线" FontWeight="Bold" FontSize="13" Foreground="{DynamicResource DangerBrush}"/>
</StackPanel>
```

**窗口控制按钮** (第 158-216 行)：

```xml
<!-- 设置按钮 -->
<Button Command="{Binding NavigateToSettingsCommand}" ToolTip="设置" Style="{StaticResource IconButtonStyle}" Width="32" Height="32">
    <Path Data="{StaticResource IconSettings}" Style="{StaticResource IconSmStyle}"/>
</Button>

<!-- 最小化 -->
<Button ToolTip="最小化" Style="{StaticResource IconButtonStyle}" Width="36" Height="32" Click="MinimizeButton_Click">
    <Path Data="{StaticResource IconMinimize}" Style="{StaticResource IconSmStyle}"/>
</Button>

<!-- 最大化 -->
<Button ToolTip="最大化" Style="{StaticResource IconButtonStyle}" Width="36" Height="32" Click="MaximizeButton_Click">
    <Path Data="{StaticResource IconMaximize}" Style="{StaticResource IconSmStyle}"/>
</Button>

<!-- 关闭 -->
<Button ToolTip="关闭" Width="36" Height="32" Cursor="Hand" Click="CloseButton_Click" ...>
    <Path Data="{StaticResource IconClose}" Style="{StaticResource IconSmStyle}"/>
</Button>
```

**抽屉底部安全提示** (第 508 行)：

```xml
<!-- 修改前 -->
<TextBlock Text="🔒 本地私有存储 · 永久留存" .../>

<!-- 修改后 -->
<StackPanel Orientation="Horizontal" HorizontalAlignment="Center">
    <Path Data="{StaticResource IconLock}" Style="{StaticResource IconSmStyle}" Width="12" Height="12" Margin="0,0,4,0"/>
    <TextBlock Text="本地私有存储 · 永久留存" FontSize="10.5" Foreground="{DynamicResource TextMutedBrush}"/>
</StackPanel>
```

### 1.6 [修改] 各 View 中的 emoji → Path 替换清单

**ImportView.xaml**：

| 行号 | 修改前 | 修改后 |
|------|--------|-------|
| 58 | `Content="📁 选择…"` | `<Path Data="{StaticResource IconUpload}" .../>` + `Text="选择…"` |
| 86 | `Content="✓"` | `<Path Data="{StaticResource IconCheck}" .../>` |
| 95 | `Content="✕"` | `<Path Data="{StaticResource IconClose}" .../>` |
| 222 | `Text="📂" FontSize="48"` | `<Path Data="{StaticResource IconFolder}" Style="{StaticResource IconLgStyle}" Stroke="{StaticResource TextMutedBrush}"/>` |
| 354 | `Text="✓"` | `<Path Data="{StaticResource IconCheck}" Stroke="{StaticResource SuccessBrush}" .../>` |
| 391 | `Text="🤖 AI 整理"` | `<Path Data="{StaticResource IconAi}" Stroke="{StaticResource PrimaryBrush}" .../>` + `Text="AI 整理"` |
| 436 | `Text="⏭"` | `<Path Data="{StaticResource IconSkip}" .../>` |
| 465 | `Text="✗"` | `<Path Data="{StaticResource IconCross}" Stroke="{StaticResource DangerBrush}" .../>` |

**SearchView.xaml**：

| 行号 | 修改前 | 修改后 |
|------|--------|-------|
| 276 | `Text="🔍" FontSize="30"` | `<Path Data="{StaticResource IconSearch}" Width="32" Height="32" Stroke="{StaticResource PrimaryBrush}"/>` |
| 388 | `Content="📄 查看全文"` | `<Path Data="{StaticResource IconDocumentFull}" .../>` + `Text="查看全文"` |
| 395 | `Content="💬 发起对话"` | `<Path Data="{StaticResource IconChat}" .../>` + `Text="发起对话"` |

**ConvertView.xaml**：

| 行号 | 修改前 | 修改后 |
|------|--------|-------|
| 47 | `Content="📁 选择文件…"` | `<Path Data="{StaticResource IconUpload}" .../>` + `Text="选择文件…"` |

**QualityView.xaml**：

| 行号 | 修改前 | 修改后 |
|------|--------|-------|
| 52 | `Content="🔄 刷新看板"` | `<Path Data="{StaticResource IconRefresh}" .../>` + `Text="刷新看板"` |
| 72 | `Text="📄"` | `<Path Data="{StaticResource IconFile}" Style="{StaticResource IconSmStyle}"/>` |
| 90 | `Text="🧩"` | `<Path Data="{StaticResource IconDashboard}" Style="{StaticResource IconSmStyle}"/>` |
| 108 | `Text="🔁"` | `<Path Data="{StaticResource IconRefresh}" Style="{StaticResource IconSmStyle}"/>` |
| 127 | `Text="⚠"` | `<Path Data="{StaticResource IconWarning}" Style="{StaticResource IconSmStyle}" Stroke="{StaticResource WarningBrush}"/>` |

**GraphView.xaml**：

| 行号 | 修改前 | 修改后 |
|------|--------|-------|
| 41 | `Text="🕸️ 知识图谱"` | `<Path Data="{StaticResource IconGraph}" .../>` + `Text="知识图谱"` |
| 76 | `Content="🔄 刷新"` | `<Path Data="{StaticResource IconRefresh}" .../>` + `Text="刷新"` |

**SettingsView.xaml**：

| 行号 | 修改前 | 修改后 |
|------|--------|-------|
| 81 | `Text="⚙️ 偏好设置"` | `<Path Data="{StaticResource IconSettings}" .../>` + `Text="偏好设置"` |
| 93 | `Text="🤖"` | `<Path Data="{StaticResource IconAi}" .../>` |
| 107 | `Text="📚"` | `<Path Data="{StaticResource IconFolder}" .../>` |
| 120+ | `Text="⚙️"` | `<Path Data="{StaticResource IconSettings}" .../>` |

**ImportView.xaml 空态** (第 221-231 行)：

```xml
<!-- 修改后 -->
<StackPanel VerticalAlignment="Center" HorizontalAlignment="Center">
    <Path Data="{StaticResource IconFolder}"
          Style="{StaticResource IconLgStyle}"
          Stroke="{StaticResource TextMutedBrush}"
          HorizontalAlignment="Center"
          Margin="0,0,0,16"/>
    <TextBlock Text="拖拽文件或文件夹到此处"
               Style="{StaticResource SubtitleText}"
               Foreground="{StaticResource TextMutedBrush}"
               HorizontalAlignment="Center"
               Margin="0,0,0,4"/>
    <TextBlock Text="或点击「选择」按钮浏览"
               Style="{StaticResource CaptionText}"
               HorizontalAlignment="Center"/>
</StackPanel>
```

### 1.7 [修改] `Controls/ToastControl.cs` — 替换 emoji 图标

```csharp
// 修改前 (第 49-55 行)
var (bg, icon) = notification.Type switch
{
    ToastType.Success => (new SolidColorBrush(Color.FromRgb(240, 255, 244)), "✓"),
    ToastType.Warning => (new SolidColorBrush(Color.FromRgb(255, 251, 235)), "⚠"),
    ToastType.Error => (new SolidColorBrush(Color.FromRgb(255, 245, 245)), "✗"),
    _ => (new SolidColorBrush(Color.FromRgb(235, 248, 255)), "ℹ"),
};

// 修改后：移除 emoji，用 Path 替代
var (bg, iconGeometry) = notification.Type switch
{
    ToastType.Success => (new SolidColorBrush(Color.FromRgb(240, 255, 244)),
        (Geometry)Application.Current.FindResource("IconCheck")),
    ToastType.Warning => (new SolidColorBrush(Color.FromRgb(255, 251, 235)),
        (Geometry)Application.Current.FindResource("IconWarning")),
    ToastType.Error => (new SolidColorBrush(Color.FromRgb(255, 245, 245)),
        (Geometry)Application.Current.FindResource("IconCross")),
    _ => (new SolidColorBrush(Color.FromRgb(235, 248, 255)),
        (Geometry)Application.Current.FindResource("IconList")),
};
```

```csharp
// 修改前 (第 68-76 行) — TextBlock 显示 emoji
stack.Children.Add(new TextBlock
{
    Text = icon,
    FontSize = 15,
    FontWeight = FontWeights.Bold,
    Foreground = accent,
    ...
});

// 修改后 — Path 显示矢量图标
stack.Children.Add(new System.Windows.Shapes.Path
{
    Data = iconGeometry,
    Width = 16,
    Height = 16,
    Stroke = accent,
    StrokeThickness = 1.5,
    Stretch = Stretch.Uniform,
    StrokeLineJoin = PenLineJoin.Round,
    StrokeStartLineCap = PenLineCap.Round,
    StrokeEndLineCap = PenLineCap.Round,
    Fill = Brushes.Transparent,
    VerticalAlignment = VerticalAlignment.Center,
    Margin = new Thickness(0, 0, 8, 0),
});
```

```csharp
// 修改前 (第 100-108 行) — 关闭按钮
var closeBtn = new TextBlock
{
    Text = "✕",
    ...
};

// 修改后
var closeBtn = new System.Windows.Shapes.Path
{
    Data = (Geometry)Application.Current.FindResource("IconClose"),
    Width = 12,
    Height = 12,
    Stroke = new SolidColorBrush(Color.FromRgb(160, 174, 192)),
    StrokeThickness = 1.5,
    Stretch = Stretch.Uniform,
    StrokeLineJoin = PenLineJoin.Round,
    VerticalAlignment = VerticalAlignment.Top,
    Cursor = System.Windows.Input.Cursors.Hand,
    Margin = new Thickness(8, 2, 0, 0),
    Fill = Brushes.Transparent,
};
```

---

## Phase 2：品牌色彩体系重建（P0）

### 2.1 [修改] `Styles/Theme.xaml` — Light 主题配色

**Primary 系列改为深靛蓝**：

```xml
<!-- 修改前 -->
<Color x:Key="PrimaryColor">#007AFF</Color>
<Color x:Key="PrimaryLight">#E8F2FF</Color>
<Color x:Key="PrimaryHover">#0064D6</Color>
<Color x:Key="PrimaryActive">#004FAD</Color>
<Color x:Key="PrimarySubtle">#CFE5FF</Color>

<!-- 修改后 -->
<Color x:Key="PrimaryColor">#4F46E5</Color>
<Color x:Key="PrimaryLight">#EEF2FF</Color>
<Color x:Key="PrimaryHover">#4338CA</Color>
<Color x:Key="PrimaryActive">#3730A3</Color>
<Color x:Key="PrimarySubtle">#C7D2FE</Color>
```

**Link/Accent 同步**：

```xml
<!-- 修改前 -->
<Color x:Key="LinkColor">#007AFF</Color>
<Color x:Key="AccentColor">#007AFF</Color>

<!-- 修改后 -->
<Color x:Key="LinkColor">#4F46E5</Color>
<Color x:Key="AccentColor">#4F46E5</Color>
```

**SelectedColor**：

```xml
<!-- 修改前 -->
<Color x:Key="SelectedColor">#E8F2FF</Color>

<!-- 修改后 -->
<Color x:Key="SelectedColor">#EEF2FF</Color>
```

**品牌专属色**：

```xml
<!-- 修改前 -->
<SolidColorBrush x:Key="DocMindEmeraldBrush" Color="#10B981"/>
<SolidColorBrush x:Key="DocMindEmeraldLightBrush" Color="#ECFDF5"/>

<!-- 修改后 -->
<SolidColorBrush x:Key="DocMindEmeraldBrush" Color="#059669"/>
<SolidColorBrush x:Key="DocMindEmeraldLightBrush" Color="#ECFDF5"/>
```

**主题文件头注释更新**：

```xml
<!-- 修改前 -->
<!-- ===================================================================
     Apple Design System · Light Theme
     Anchored on Apple HIG system colors
     Pure white surfaces · clear gray hierarchy · minimal shadows
     =================================================================== -->

<!-- 修改后 -->
<!-- ===================================================================
     DocMind Design System · Light Theme
     Deep Indigo primary · warm neutrals · layered elevation
     =================================================================== -->
```

### 2.2 [修改] `Styles/Theme.Dark.xaml` — Dark 主题配色

```xml
<!-- 修改前 -->
<Color x:Key="PrimaryColor">#0A84FF</Color>
<Color x:Key="PrimaryLight">#0D2547</Color>
<Color x:Key="PrimaryHover">#3D9BFF</Color>
<Color x:Key="PrimaryActive">#5BAFFF</Color>
<Color x:Key="PrimarySubtle">#1A2D42</Color>

<!-- 修改后 -->
<Color x:Key="PrimaryColor">#818CF8</Color>
<Color x:Key="PrimaryLight">#1E1B4B</Color>
<Color x:Key="PrimaryHover">#6366F1</Color>
<Color x:Key="PrimaryActive">#4F46E5</Color>
<Color x:Key="PrimarySubtle">#312E81</Color>
```

```xml
<!-- 修改前 -->
<Color x:Key="LinkColor">#0A84FF</Color>
<Color x:Key="AccentColor">#0A84FF</Color>
<Color x:Key="SelectedColor">#0D2547</Color>

<!-- 修改后 -->
<Color x:Key="LinkColor">#818CF8</Color>
<Color x:Key="AccentColor">#818CF8</Color>
<Color x:Key="SelectedColor">#1E1B4B</Color>
```

```xml
<!-- 修改前 -->
<SolidColorBrush x:Key="DocMindEmeraldBrush" Color="#10B981"/>
<SolidColorBrush x:Key="DocMindEmeraldLightBrush" Color="#064E3B"/>

<!-- 修改后 -->
<SolidColorBrush x:Key="DocMindEmeraldBrush" Color="#34D399"/>
<SolidColorBrush x:Key="DocMindEmeraldLightBrush" Color="#064E3B"/>
```

```xml
<!-- 修改前 -->
<SolidColorBrush x:Key="DocMindCyanBrush" Color="#0EA5E9"/>
<SolidColorBrush x:Key="DocMindCyanLightBrush" Color="#0C4A6E"/>

<!-- 修改后 -->
<SolidColorBrush x:Key="DocMindCyanBrush" Color="#38BDF8"/>
<SolidColorBrush x:Key="DocMindCyanLightBrush" Color="#0C4A6E"/>
```

---

## Phase 3：卡片节奏与视觉层次重构（P1）

### 3.1 [修改] `Styles/Theme.xaml` — 圆角收紧

```xml
<!-- 修改前 -->
<CornerRadius x:Key="CornerSm">6</CornerRadius>
<CornerRadius x:Key="CornerMd">10</CornerRadius>
<CornerRadius x:Key="CornerLg">14</CornerRadius>
<CornerRadius x:Key="CornerXl">20</CornerRadius>

<!-- 修改后 -->
<CornerRadius x:Key="CornerSm">4</CornerRadius>
<CornerRadius x:Key="CornerMd">8</CornerRadius>
<CornerRadius x:Key="CornerLg">12</CornerRadius>
<CornerRadius x:Key="CornerXl">16</CornerRadius>
```

**CardStyle**：

```xml
<!-- 修改前 -->
<Style x:Key="CardStyle" TargetType="Border">
    <Setter Property="CornerRadius" Value="14"/>
    <Setter Property="Padding" Value="20"/>
</Style>

<!-- 修改后 -->
<Style x:Key="CardStyle" TargetType="Border">
    <Setter Property="CornerRadius" Value="12"/>
    <Setter Property="Padding" Value="16"/>
</Style>
```

### 3.2 [修改] 新增工具条样式 `ToolbarBorderStyle`

在 `Theme.xaml` 中新增（两个主题文件同步）：

```xml
<!-- 新增：工具条/控制区样式 (无边框, 底部分隔线) -->
<Style x:Key="ToolbarBorderStyle" TargetType="Border">
    <Setter Property="Background" Value="{StaticResource CardBrush}"/>
    <Setter Property="BorderBrush" Value="{StaticResource BorderBrush}"/>
    <Setter Property="BorderThickness" Value="0,0,0,1"/>
    <Setter Property="CornerRadius" Value="0"/>
    <Setter Property="Padding" Value="16,12"/>
    <Setter Property="SnapsToDevicePixels" Value="True"/>
</Style>
```

### 3.3 [修改] 各 View 工具条从卡片改为工具条样式

**SearchView.xaml** (第 136-142 行)：

```xml
<!-- 修改前 -->
<Border Grid.Row="0"
        Background="{StaticResource CardBrush}"
        BorderBrush="{StaticResource BorderBrush}"
        BorderThickness="1"
        CornerRadius="14"
        Padding="14,10"
        Margin="0,0,0,12">

<!-- 修改后 -->
<Border Grid.Row="0"
        Style="{StaticResource ToolbarBorderStyle}"
        Margin="0,0,0,12">
```

**ImportView.xaml** (第 27-33 行)：

```xml
<!-- 修改前 -->
<Border Grid.Row="0"
        Background="{StaticResource CardBrush}"
        BorderBrush="{StaticResource BorderBrush}"
        BorderThickness="1"
        CornerRadius="14"
        Padding="16,14"
        Margin="0,0,0,12">

<!-- 修改后 -->
<Border Grid.Row="0"
        Style="{StaticResource ToolbarBorderStyle}"
        Margin="0,0,0,12">
```

**ConvertView.xaml** (第 19-25 行, 第 57-63 行)：同样改为 `ToolbarBorderStyle`。

**DocumentsView.xaml** (第 19-25 行)：同样改为 `ToolbarBorderStyle`。

**QualityView.xaml** (第 25-31 行)：同样改为 `ToolbarBorderStyle`。

**GraphView.xaml**：已经是底部 1px 分隔线样式，保持不变。

### 3.4 [修改] 搜索结果卡片增加 hover 强调条

**SearchView.xaml** `HitCardTemplate` (第 27-50 行)：

```xml
<!-- 在 DataTemplate 内 Border 的 Style Triggers 中新增 hover 左侧强调条 -->
<Style.Triggers>
    <Trigger Property="IsMouseOver" Value="True">
        <Setter Property="BorderBrush" Value="{StaticResource PrimaryBrush}"/>
        <!-- 新增：hover 时显示左侧强调条 -->
        <Setter Property="BorderThickness" Value="3,1,1,1"/>
    </Trigger>
    <DataTrigger Binding="{Binding IsSelected, RelativeSource={RelativeSource AncestorType=ListBoxItem}}" Value="True">
        <Setter Property="BorderBrush" Value="{StaticResource PrimaryBrush}"/>
        <Setter Property="BorderThickness" Value="3,1,1,1"/>
    </DataTrigger>
</Style.Triggers>
```

---

## Phase 4：按钮交互升级（P1）

### 4.1 [修改] `Styles/Theme.xaml` — PrimaryButton 用色阶替代透明度

```xml
<!-- 修改前 (PrimaryButtonStyle Triggers) -->
<ControlTemplate.Triggers>
    <Trigger Property="IsMouseOver" Value="True">
        <Setter TargetName="Root" Property="Opacity" Value="0.88"/>
    </Trigger>
    <Trigger Property="IsPressed" Value="True">
        <Setter TargetName="Root" Property="Opacity" Value="0.76"/>
    </Trigger>
    <Trigger Property="IsEnabled" Value="False">
        <Setter TargetName="Root" Property="Opacity" Value="0.42"/>
    </Trigger>
</ControlTemplate.Triggers>

<!-- 修改后 -->
<ControlTemplate.Triggers>
    <Trigger Property="IsMouseOver" Value="True">
        <Setter TargetName="Root" Property="Background" Value="{StaticResource PrimaryHoverBrush}"/>
        <Setter TargetName="Root" Property="Effect" Value="{StaticResource ShadowButtonHover}"/>
    </Trigger>
    <Trigger Property="IsPressed" Value="True">
        <Setter TargetName="Root" Property="Background" Value="{StaticResource PrimaryActiveBrush}"/>
        <Setter TargetName="Root" Property="Effect" Value="{x:Null}"/>
    </Trigger>
    <Trigger Property="IsEnabled" Value="False">
        <Setter TargetName="Root" Property="Background" Value="{StaticResource PrimarySubtleBrush}"/>
        <Setter TargetName="Root" Property="Opacity" Value="0.5"/>
    </Trigger>
</ControlTemplate.Triggers>
```

**DangerButton 同样处理**：hover → `DangerBrush` 改用加深色 `#DC2626`，press → `#B91C1C`。

### 4.2 [新增] ShadowButtonHover 定义

在 `Theme.xaml` 和 `Theme.Dark.xaml` 的阴影区新增：

```xml
<!-- 新增：按钮 hover 阴影 (带品牌色染色) -->
<DropShadowEffect x:Key="ShadowButtonHover" BlurRadius="12" ShadowDepth="2"
                  Color="#4F46E5" Opacity="0.20" Direction="270" RenderingBias="Performance"/>
```

Dark 主题：
```xml
<DropShadowEffect x:Key="ShadowButtonHover" BlurRadius="12" ShadowDepth="2"
                  Color="#818CF8" Opacity="0.30" Direction="270" RenderingBias="Performance"/>
```

---

## Phase 5：阴影层级强化（P1）

### 5.1 [修改] `Styles/Theme.xaml` — 提升阴影可见度

```xml
<!-- 修改前 -->
<DropShadowEffect x:Key="ShadowSm" BlurRadius="2" ShadowDepth="1" Color="#0A000000" Opacity="0.04"/>
<DropShadowEffect x:Key="ShadowMd" BlurRadius="8" ShadowDepth="2" Color="#10000000" Opacity="0.06"/>
<DropShadowEffect x:Key="ShadowLg" BlurRadius="24" ShadowDepth="8" Color="#14000000" Opacity="0.08"/>
<DropShadowEffect x:Key="ShadowCard" BlurRadius="4" ShadowDepth="1" Color="#0A000000" Opacity="0.04"/>
<DropShadowEffect x:Key="ShadowFloating" BlurRadius="20" ShadowDepth="3" Color="#0F172A" Opacity="0.08"/>

<!-- 修改后 -->
<DropShadowEffect x:Key="ShadowSm" BlurRadius="3" ShadowDepth="1" Color="#000000" Opacity="0.08" RenderingBias="Performance"/>
<DropShadowEffect x:Key="ShadowMd" BlurRadius="12" ShadowDepth="2" Color="#000000" Opacity="0.12" RenderingBias="Performance"/>
<DropShadowEffect x:Key="ShadowLg" BlurRadius="28" ShadowDepth="8" Color="#000000" Opacity="0.16" RenderingBias="Performance"/>
<DropShadowEffect x:Key="ShadowCard" BlurRadius="6" ShadowDepth="1" Color="#000000" Opacity="0.06" RenderingBias="Performance"/>
<DropShadowEffect x:Key="ShadowFloating" BlurRadius="24" ShadowDepth="3" Color="#1E1B4B" Opacity="0.20" RenderingBias="Performance"/>
```

### 5.2 [修改] `Styles/Theme.Dark.xaml` — 深色阴影

```xml
<!-- 修改前 -->
<DropShadowEffect x:Key="ShadowSm" ... Opacity="0.36"/>
<DropShadowEffect x:Key="ShadowMd" ... Opacity="0.44"/>
<DropShadowEffect x:Key="ShadowLg" ... Opacity="0.50"/>
<DropShadowEffect x:Key="ShadowCard" ... Opacity="0.36"/>
<DropShadowEffect x:Key="ShadowFloating" ... Opacity="0.50"/>

<!-- 修改后 -->
<DropShadowEffect x:Key="ShadowSm" BlurRadius="3" ShadowDepth="1" Color="#000000" Opacity="0.40" RenderingBias="Performance"/>
<DropShadowEffect x:Key="ShadowMd" BlurRadius="12" ShadowDepth="2" Color="#000000" Opacity="0.48" RenderingBias="Performance"/>
<DropShadowEffect x:Key="ShadowLg" BlurRadius="28" ShadowDepth="8" Color="#000000" Opacity="0.55" RenderingBias="Performance"/>
<DropShadowEffect x:Key="ShadowCard" BlurRadius="6" ShadowDepth="1" Color="#000000" Opacity="0.40" RenderingBias="Performance"/>
<DropShadowEffect x:Key="ShadowFloating" BlurRadius="24" ShadowDepth="3" Color="#000000" Opacity="0.55" RenderingBias="Performance"/>
```

---

## Phase 6：空态设计重构（P2）

### 6.1 [修改] `Views/SearchView.xaml` — 搜索空态

```xml
<!-- 修改前 (第 260-290 行) -->
<Border Background="{StaticResource SurfaceBrush}" BorderBrush="{StaticResource BorderBrush}"
        BorderThickness="1" CornerRadius="14" Padding="36,48" MaxWidth="580"
        Visibility="{Binding ShowEmptyGuide, Converter={StaticResource BoolToVis}}">
    <StackPanel HorizontalAlignment="Center">
        <Border Width="64" Height="64" Background="{StaticResource PrimaryLightBrush}" CornerRadius="32" ...>
            <TextBlock Text="🔍" FontSize="30" .../>
        </Border>
        <TextBlock Text="知识库检索" Style="{StaticResource TitleText}" .../>
        <TextBlock Text="{Binding EmptyGuideText}" Style="{StaticResource BodyText}" .../>
    </StackPanel>
</Border>

<!-- 修改后：去圆形容器, 大图标, + 3 个可点击示例 -->
<Border Background="{StaticResource SurfaceBrush}"
        CornerRadius="12"
        Padding="40,48"
        HorizontalAlignment="Center" VerticalAlignment="Center"
        MaxWidth="620"
        Visibility="{Binding ShowEmptyGuide, Converter={StaticResource BoolToVis}}">
    <StackPanel HorizontalAlignment="Center">
        <!-- 矢量搜索图标 -->
        <Path Data="{StaticResource IconSearch}"
              Width="40" Height="40"
              Stroke="{StaticResource TextMutedBrush}"
              StrokeThickness="1.2" Stretch="Uniform"
              StrokeLineJoin="Round"
              Fill="Transparent"
              HorizontalAlignment="Center"
              Margin="0,0,0,16"/>

        <TextBlock Text="知识库检索" Style="{StaticResource SubtitleText}"
                   HorizontalAlignment="Center" Margin="0,0,0,8"/>
        <TextBlock Text="{Binding EmptyGuideText}"
                   Style="{StaticResource CaptionText}"
                   Foreground="{StaticResource TextSecondaryBrush}"
                   TextWrapping="Wrap" TextAlignment="Center"
                   MaxWidth="400" Margin="0,0,0,24"/>

        <!-- 示例搜索建议 (可点击 prompt chips) -->
        <StackPanel Orientation="Horizontal" HorizontalAlignment="Center">
            <Button Content="这个文档讲了什么？" Style="{StaticResource TextButtonStyle}" Margin="0,0,8,0" FontSize="12.5"/>
            <Button Content="总结关键结论" Style="{StaticResource TextButtonStyle}" Margin="0,0,8,0" FontSize="12.5"/>
            <Button Content="对比两种方案" Style="{StaticResource TextButtonStyle}" FontSize="12.5"/>
        </StackPanel>
    </StackPanel>
</Border>
```

### 6.2 [修改] `Views/ImportView.xaml` — 导入空态

```xml
<!-- 修改后：虚线边框拖拽区 + 格式标签 -->
<Border Grid.Row="3"
        BorderBrush="{StaticResource BorderBrush}"
        BorderThickness="2"
        CornerRadius="12"
        Background="{StaticResource SurfaceBrush}">
    <!-- 新增：虚线效果通过 DrawingBrush 实现 -->
    <Border.Style>
        <Style TargetType="Border">
            <Setter Property="Visibility" Value="Collapsed"/>
            <Style.Triggers>
                <MultiDataTrigger>
                    <MultiDataTrigger.Conditions>
                        <Condition Binding="{Binding IsBusy}" Value="False"/>
                        <Condition Binding="{Binding HasResults}" Value="False"/>
                    </MultiDataTrigger>
                    <Setter Property="Visibility" Value="Visible"/>
                </MultiDataTrigger>
            </Style.Triggers>
        </Style>
    </Border.Style>

    <!-- 虚线边框层 -->
    <Border BorderBrush="{StaticResource TextMutedBrush}" BorderThickness="2" CornerRadius="10" Opacity="0.4" Margin="-2"/>

    <StackPanel VerticalAlignment="Center" HorizontalAlignment="Center">
        <Path Data="{StaticResource IconFolder}" Style="{StaticResource IconLgStyle}"
              Stroke="{StaticResource TextMutedBrush}" HorizontalAlignment="Center" Margin="0,0,0,16"/>
        <TextBlock Text="拖拽文件或文件夹到此处"
                   Style="{StaticResource SubtitleText}"
                   Foreground="{StaticResource TextMutedBrush}"
                   HorizontalAlignment="Center" Margin="0,0,0,4"/>
        <TextBlock Text="或点击「选择」按钮浏览"
                   Style="{StaticResource CaptionText}"
                   HorizontalAlignment="Center" Margin="0,0,0,20"/>
        <!-- 支持格式标签行 -->
        <ItemsControl HorizontalAlignment="Center">
            <ItemsControl.ItemsPanel>
                <ItemsPanelTemplate>
                    <WrapPanel ItemWidth="Auto"/>
                </ItemsPanelTemplate>
            </ItemsControl.ItemsPanel>
            <ItemsControl.ItemTemplate>
                <DataTemplate>
                    <Border Background="{StaticResource SurfaceBrush}" CornerRadius="4" Padding="8,3" Margin="2">
                        <TextBlock Text="{Binding}" Style="{StaticResource SmallText}" Foreground="{StaticResource TextTertiaryBrush}"/>
                    </Border>
                </DataTemplate>
            </ItemsControl.ItemTemplate>
            <!-- 静态格式列表 -->
            <ItemsControl.ItemsSource>
                <x:Array Type="{x:Type sys:String}">
                    <sys:String>PDF</sys:String>
                    <sys:String>DOCX</sys:String>
                    <sys:String>XLSX</sys:String>
                    <sys:String>PPTX</sys:String>
                    <sys:String>MD</sys:String>
                    <sys:String>HTML</sys:String>
                    <sys:String>CODE</sys:String>
                </x:Array>
            </ItemsControl.ItemsSource>
        </ItemsControl>
    </StackPanel>
</Border>
```

---

## Phase 7：字体微调（P2）

### 7.1 [修改] `Styles/Theme.xaml` + `Theme.Dark.xaml`

```xml
<!-- 修改前 -->
<Style x:Key="TitleText" TargetType="TextBlock">
    <Setter Property="FontSize" Value="28"/>
    <Setter Property="FontWeight" Value="Bold"/>
</Style>
<Style x:Key="SubtitleText" TargetType="TextBlock">
    <Setter Property="FontSize" Value="17"/>
    <Setter Property="FontWeight" Value="Semibold"/>
</Style>
<Style x:Key="BodyText" TargetType="TextBlock">
    <Setter Property="FontSize" Value="15"/>
    <Setter Property="FontWeight" Value="Medium"/>
</Style>
<Style x:Key="CaptionText" TargetType="TextBlock">
    <Setter Property="FontSize" Value="13"/>
</Style>
<Style x:Key="SmallText" TargetType="TextBlock">
    <Setter Property="FontSize" Value="12"/>
</Style>

<!-- 修改后 -->
<Style x:Key="TitleText" TargetType="TextBlock">
    <Setter Property="FontSize" Value="26"/>
    <Setter Property="FontWeight" Value="SemiBold"/>
</Style>
<Style x:Key="SubtitleText" TargetType="TextBlock">
    <Setter Property="FontSize" Value="16"/>
    <Setter Property="FontWeight" Value="SemiBold"/>
</Style>
<Style x:Key="BodyText" TargetType="TextBlock">
    <Setter Property="FontSize" Value="14.5"/>
    <Setter Property="FontWeight" Value="Regular"/>
</Style>
<Style x:Key="CaptionText" TargetType="TextBlock">
    <Setter Property="FontSize" Value="12.5"/>
</Style>
<Style x:Key="SmallText" TargetType="TextBlock">
    <Setter Property="FontSize" Value="11.5"/>
</Style>
```

---

## Phase 8：导航轨品牌强化（P2）

### 8.1 [修改] `MainWindow.xaml` — 品牌图标区

```xml
<!-- 修改前 (第 281-285 行) -->
<Border Grid.Row="0" Width="36" Height="36" CornerRadius="10"
        Background="{DynamicResource PrimaryLightBrush}" ...>
    <TextBlock Text="🧠" FontSize="18" .../>
</Border>

<!-- 修改后 -->
<Border Grid.Row="0" Width="36" Height="36" CornerRadius="10"
        Background="{DynamicResource PrimaryLightBrush}"
        HorizontalAlignment="Center" Margin="0,0,0,12"
        ToolTip="DocMind 本地向量知识库">
    <Path Data="{StaticResource IconDocMindLogo}"
          Width="22" Height="22"
          Stretch="Uniform"
          Stroke="{DynamicResource PrimaryBrush}"
          StrokeThickness="1.5"
          StrokeLineJoin="Round"
          Fill="Transparent"
          HorizontalAlignment="Center" VerticalAlignment="Center"/>
</Border>
```

### 8.2 [修改] `Styles/Theme.xaml` — RailItemStyle 选中态增加渐变指示条

```xml
<!-- 在 RailItemStyle 的 ControlTemplate 中修改 ActiveIndicator -->
<!-- 修改前 -->
<Border x:Name="ActiveIndicator"
        Width="3" Height="18"
        CornerRadius="1.5"
        Background="{DynamicResource PrimaryBrush}"
        HorizontalAlignment="Left"
        VerticalAlignment="Center"
        Margin="-6,0,0,0"
        Visibility="Collapsed"/>

<!-- 修改后：使用渐变背景 -->
<Border x:Name="ActiveIndicator"
        Width="3" Height="20"
        CornerRadius="1.5"
        HorizontalAlignment="Left"
        VerticalAlignment="Center"
        Margin="-6,0,0,0"
        Visibility="Collapsed">
    <Border.Background>
        <LinearGradientBrush StartPoint="0,0" EndPoint="0,1">
            <GradientStop Color="{DynamicResource PrimaryColor}" Offset="0"/>
            <GradientStop Color="Transparent" Offset="1"/>
        </LinearGradientBrush>
    </Border.Background>
</Border>
```

---

## 实施顺序建议

### 第一阶段（1-2 天）：立即见效

1. **Phase 2** — 改 `Theme.xaml` + `Theme.Dark.xaml` 的 Color 值（~30 分钟）
2. **Phase 5** — 改阴影 Opacity/BlurRadius（~15 分钟）
3. **Phase 4** — 改 PrimaryButton Triggers + 新增 ShadowButtonHover（~30 分钟）
4. **Phase 3.1** — 改圆角 Token（~10 分钟）
5. **Phase 7** — 改字体 Size/Weight（~15 分钟）

以上 5 项只改 2 个文件，预计 1.5 小时，效果显著。

### 第二阶段（2-3 天）：图标系统

6. **Phase 1.1** — 创建 `Icons.xaml`（~1 小时）
7. **Phase 1.2** — 修改 `App.xaml` 注册（~5 分钟）
8. **Phase 1.3-1.4** — 修改 `NavigationItem.cs` + `MainViewModel.cs`（~20 分钟）
9. **Phase 1.5** — 修改 `MainWindow.xaml` 导航模板 + 窗口按钮（~1 小时）
10. **Phase 1.6** — 逐个 View 替换 emoji（~2 小时）
11. **Phase 1.7** — 修改 `ToastControl.cs`（~30 分钟）

### 第三阶段（1-2 天）：布局与空态

12. **Phase 3.2-3.3** — 新增 ToolbarStyle + 各 View 工具条改造（~1 小时）
13. **Phase 3.4** — 搜索结果卡片 hover 强调条（~15 分钟）
14. **Phase 6** — 搜索/导入空态重构（~1 小时）
15. **Phase 8** — 导航轨品牌强化（~30 分钟）

---

## 验证清单

每个 Phase 完成后需验证：

- [ ] 浅色主题 + 深色主题切换正常
- [ ] 所有页面导航正常（无绑定错误）
- [ ] 按钮交互 hover/press/disabled 状态正确
- [ ] 所有 emoji 已替换（全局搜索 emoji 字符）
- [ ] 阴影在浅色背景上可见
- [ ] 卡片圆角统一
- [ ] 空态在不同分辨率下居中
- [ ] Toast 通知图标正确渲染
- [ ] 编译无 warning/error
