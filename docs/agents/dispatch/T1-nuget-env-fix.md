# T1 NuGet/WPF 环境修复记录（已解决）

## 根因

1. CLI `dotnet restore` 在 NuGet.targets `_GetRestoreSettings` 报
   `Value cannot be null. (Parameter 'path1')`。
   根因线索：`ConfigurationDefaults` 类型初始化失败；机器存在异常环境变量
   `NuGet=E:\Program Files\...\NuGet`，且用户级 NuGet 配置与 `<clear/>` 源映射叠加。
2. **有效解法**：改用 Visual Studio 2022 自带 MSBuild（非 `C:\Program Files\dotnet`），
   并显式指定 `NUGET_PACKAGES` / `NuGetPackageRoot`。

## 可复现命令（本机已通过）

```powershell
Remove-Item Env:NuGet -ErrorAction SilentlyContinue
$msb = "E:\Program Files\Microsoft Visual Studio\2022\MSBuild\Current\Bin\MSBuild.exe"
$env:APPDATA = "C:\Users\Administrator\AppData\Roaming"
$env:UserProfile = "C:\Users\Administrator"
$env:NUGET_PACKAGES = "C:\Users\Administrator\.nuget\packages"

# Restore + Build
& $msb E:\DocMindY-worktrees\agent-p0\DocMind\DocMind.csproj -t:Restore `
  -p:NuGetPackageRoot=C:\Users\Administrator\.nuget\packages\ `
  -p:RestorePackagesPath=C:\Users\Administrator\.nuget\packages\
& $msb E:\DocMindY-worktrees\agent-p0\DocMind\DocMind.csproj -t:Build `
  -p:Configuration=Debug -p:NuGetPackageRoot=C:\Users\Administrator\.nuget\packages\

# 测试项目同理
& $msb E:\DocMindY-worktrees\agent-p0\DocMind.Tests\DocMind.Tests.csproj -t:Restore `
  -p:NuGetPackageRoot=C:\Users\Administrator\.nuget\packages\
```

## 本回合验证结果

| 项 | 结果 |
|---|---|
| `DocMind.csproj` Restore | **成功**（NU1701 仅为 OpenTK/Skia 兼容性警告） |
| `DocMind.csproj` Build | **成功** → `DocMind\bin\Debug\net8.0-windows\DocMind.dll` |
| CS0246/CS0102/CS0111/CS1061 | **无**（编译错误清零） |
| 编译期修复 | `ChatViewModel.cs` onAgentEvent 多余 `},` 已删 |

## 备注

- CLI `dotnet restore` 在本机仍可能 path1：**请统一用 VS MSBuild**。
- 用户 NuGet 配置已指向 `C:\Users\Administrator\.nuget\packages`，避免污染。

## 状态

**done**（构建门已通；真机 UI 验收可继续）
