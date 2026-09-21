# ISSUE-01 NuGet / WPF 编译与测试环境

| 字段 | 内容 |
|---|---|
| ID | T1 |
| 优先级 | **P0** |
| 角色 | 构建/DevOps |
| 依赖 | 可与 T0 并行 |
| 状态 | ready |

## 背景

`dotnet build` 在 `E:\DocMindY` 与 worktree 均报：`NuGet.targets ... Value cannot be null. (Parameter 'path1')`，导致无法在本机验证 WPF。

## 目标

修复 NuGet/MSBuild 环境，使 `DocMind.csproj` 与 `DocMind.Tests` 可编译、可测试。

## 验收标准

- [x] `dotnet restore` + `dotnet build DocMind.csproj` 成功  
  证据：VS MSBuild Restore/Build → `DocMind.dll`；CLI path1 用 VS MSBuild 规避（见 T1-nuget-env-fix.md）
- [x] `dotnet test DocMind.Tests` 可跑  
  证据：VSTest `DocMind.Tests.dll` → **失败 0，通过 335，总计 335**
- [x] 编译错误清零：无 CS0246/CS0102/CS0111/CS1061  
  证据：Build 输出仅 NU1701/CS0219/CS8602 警告，无上述错误
- [x] 写下环境修复步骤（供其他 Agent 复现）  
  证据：`docs/agents/dispatch/T1-nuget-env-fix.md`

## 建议排查点

- NuGet 用户配置、`NUGET_PACKAGES`、损坏的 `obj`/`bin`  
- 中文/特殊字符路径、VS 与 CLI 包源不一致  
- 缺文件：`WebSearchModeChoice`、`ThinkingStep`、`LibraryStatus` 等是否齐全  

## 文件触点

- 解决方案/csproj、NuGet.Config  
- `DocMind/Models/**`、`DocMind/Services/Doc2kbApiService.cs`  

## 备注

后端 Python 测试不依赖本任务；WPF 手动验收（T4/T15）依赖本任务。
