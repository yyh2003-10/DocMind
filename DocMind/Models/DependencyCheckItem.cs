namespace DocMind.Models;

/// <summary>设置页「运行依赖就绪状态」单条检查项（UI 展示用）。</summary>
public sealed record DependencyCheckItem(string Name, bool Ok, string Detail);