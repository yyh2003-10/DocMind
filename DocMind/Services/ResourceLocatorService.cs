using System.Diagnostics;
using System.IO;
using System.Text.Json;
using Microsoft.Extensions.Logging;

namespace DocMind.Services;

/// <summary>可定位的外部资源种类。</summary>
public enum LocatableResource
{
    /// <summary>poppler bin 目录（扫描 PDF OCR 渲染依赖，锚点 pdftoppm.exe）。</summary>
    Poppler,

    /// <summary>后端 Python 解释器（锚点 python.exe，可运行 --version）。</summary>
    BackendPython,

    /// <summary>离线安装包目录（锚点 *.whl）。</summary>
    OfflineWheels,

    /// <summary>嵌入模型缓存目录（锚点 fastembed_cache）。</summary>
    EmbedModelCache,
}

/// <summary>定位结果：路径、来源与全部候选（多命中时供用户挑选）。</summary>
public sealed record ResourceLocatorResult
{
    public bool Found { get; init; }
    public string? Path { get; init; }
    /// <summary>来源：手动配置 / 缓存 / 候选快扫 / 全盘搜索 / 默认。</summary>
    public string Source { get; init; } = "";
    /// <summary>全部通过校验的候选（含选中项）。</summary>
    public IReadOnlyList<string> Candidates { get; init; } = Array.Empty<string>();
    public string? Detail { get; init; }

    public static ResourceLocatorResult NotFound(string? detail = null) =>
        new() { Found = false, Source = "", Detail = detail };
}

/// <summary>
/// 外部资源统一定位服务：显式配置 → 高概率区快扫 → 全盘深扫（U 盘纳入、
/// 网络盘/光驱排除），三级递进。全盘深扫带目录剪枝、深度限制、early-exit、
/// 结果缓存与 7 天冷却期（避免反复空扫）。搜索只读；校验仅执行受限参数。
/// </summary>
public sealed class ResourceLocatorService
{
    private readonly AppSettings _settings;
    private readonly Microsoft.Extensions.Logging.ILogger<ResourceLocatorService>? _logger;

    public ResourceLocatorService(
        AppSettings settings,
        Microsoft.Extensions.Logging.ILogger<ResourceLocatorService>? logger = null)
    {
        _settings = settings;
        _logger = logger;
    }

    // ===== 剪枝与扫描参数 =====
    private static readonly HashSet<string> PrunedDirNames = new(StringComparer.OrdinalIgnoreCase)
    {
        "windows", "programdata", "$recycle.bin", "system volume information",
        "winsxs", "driverstore", "node_modules", ".git", ".svn", "__pycache__",
        "site-packages", "miniconda3", "anaconda3", // conda 内部极深且非安装目标
    };

    private const int MaxScanDepth = 6;
    private const int MaxValidatedCandidates = 3; // early-exit：攒够即停
    private static readonly TimeSpan NoResultCooldown = TimeSpan.FromDays(7);

    /// <summary>锚点文件名 / 目录名（小写）。null = 目录型锚点见 AnchorDirName。</summary>
    private static string? AnchorFile(LocatableResource kind) => kind switch
    {
        LocatableResource.Poppler => "pdftoppm.exe",
        LocatableResource.BackendPython => "python.exe",
        LocatableResource.OfflineWheels => null, // 目录含 ≥1 个 *.whl 即命中
        LocatableResource.EmbedModelCache => null,
        _ => null,
    };

    private static string? AnchorDirName(LocatableResource kind) => kind switch
    {
        LocatableResource.EmbedModelCache => "fastembed_cache",
        _ => null,
    };

    private static bool IsDirectoryAnchor(LocatableResource kind) =>
        kind == LocatableResource.EmbedModelCache;

    // ===== 高概率区快扫根目录（秒级，优先于全盘）=====
    private static List<string> QuickScanRoots(LocatableResource kind)
    {
        var roots = new List<string>();
        var baseDir = AppContext.BaseDirectory;
        var pf = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
        var pf86 = Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);
        var localAppData = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        var userProfile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var downloads = Path.Combine(userProfile, "Downloads");

        void AddIfExists(string p)
        {
            if (Directory.Exists(p)) roots.Add(p);
        }

        switch (kind)
        {
            case LocatableResource.Poppler:
                AddIfExists(Path.Combine(pf, "poppler"));
                AddIfExists(Path.Combine(pf86, "poppler"));
                AddIfExists(Path.Combine(localAppData, "poppler"));
                AddIfExists(Path.Combine(userProfile, "poppler"));
                AddIfExists(downloads);
                AddIfExists(@"C:\poppler");
                AddIfExists(@"D:\poppler");
                // 项目自带 tools/poppler（开发形态）：从应用目录向上找
                var dir = new DirectoryInfo(baseDir);
                for (var i = 0; i < 6 && dir is not null; i++, dir = dir.Parent)
                {
                    AddIfExists(Path.Combine(dir.FullName, "tools", "poppler"));
                }
                break;
            case LocatableResource.BackendPython:
                // 项目 .venv 优先
                dir = new DirectoryInfo(baseDir);
                for (var i = 0; i < 6 && dir is not null; i++, dir = dir.Parent)
                {
                    AddIfExists(Path.Combine(dir.FullName, ".venv", "Scripts"));
                }
                AddIfExists(Path.Combine(localAppData, "Programs", "Python"));
                AddIfExists(pf);
                AddIfExists(@"C:\Python311");
                AddIfExists(@"C:\Python312");
                AddIfExists(@"C:\Python313");
                break;
            case LocatableResource.OfflineWheels:
                AddIfExists(downloads);
                AddIfExists(@"C:\wheels");
                AddIfExists(@"D:\wheels");
                AddIfExists(@"E:\wheels");
                dir = new DirectoryInfo(baseDir);
                for (var i = 0; i < 6 && dir is not null; i++, dir = dir.Parent)
                {
                    AddIfExists(Path.Combine(dir.FullName, "wheels"));
                }
                break;
            case LocatableResource.EmbedModelCache:
                AddIfExists(Path.Combine(localAppData, "doc2mind"));
                AddIfExists(userProfile);
                AddIfExists(downloads);
                break;
        }
        return roots;
    }

    /// <summary>盘符根列表：固定磁盘 + 可移动磁盘（U 盘，用户明确要求扫描）；
    /// 排除网络盘 / 光驱 / 未就绪。</summary>
    private static List<string> DeepScanRoots()
    {
        var roots = new List<string>();
        try
        {
            foreach (var d in System.IO.DriveInfo.GetDrives())
            {
                try
                {
                    if (d.DriveType is DriveType.Fixed or DriveType.Removable && d.IsReady)
                    {
                        roots.Add(d.RootDirectory.FullName);
                    }
                }
                catch { /* 单个盘符异常不影响其余 */ }
            }
        }
        catch { /* ignore */ }
        return roots;
    }

    // ===== 三级解析 =====

    /// <summary>当前生效路径：显式配置校验通过 → 返回（来源=手动配置）；
    /// 否则高概率区快扫 → 返回（来源=候选快扫）；都未命中返回 NotFound。
    /// 已配置但失效的路径不会阻断快扫（换机迁移带了旧路径也能自动兜底），
    /// 并在 Detail 中说明，供 UI 显示"配置已失效"。 </summary>
    public ResourceLocatorResult Locate(LocatableResource kind)
    {
        var configured = ConfiguredPath(kind);
        if (!string.IsNullOrEmpty(configured) && Validate(kind, configured!))
        {
            return new ResourceLocatorResult
            {
                Found = true, Path = configured, Source = "手动配置",
                Candidates = new[] { configured! },
            };
        }

        var configuredInvalid = configured is not null;
        var quick = ScanRootsAsync(kind, QuickScanRoots(kind), progress: null, CancellationToken.None)
            .GetAwaiter().GetResult();
        if (quick.Candidates.Count > 0)
        {
            return quick with
            {
                Source = "候选快扫",
                Detail = configuredInvalid
                    ? "已配置的路径在本机不存在或已失效，当前展示的是自动探测结果"
                    : null,
            };
        }
        return ResourceLocatorResult.NotFound(
            configuredInvalid ? "手动配置的路径已失效，且快扫未找到可用配置" : null);
    }

    /// <summary>全盘深扫（含缓存优先）：先校验上次缓存候选（毫秒级），
    /// 未命中再按盘符顺序剪枝遍历。可取消、带进度、early-exit。</summary>
    public Task<ResourceLocatorResult> DeepScanAsync(
        LocatableResource kind, IProgress<string>? progress, CancellationToken ct)
    {
        return Task.Run(() => DeepScanCore(kind, progress, ct), ct);
    }

    private ResourceLocatorResult DeepScanCore(
        LocatableResource kind, IProgress<string>? progress, CancellationToken ct)
    {
        // 1. 缓存候选先验（旧命中若仍有效，毫秒级返回，不付扫描成本）
        var cached = LoadCache(kind);
        if (cached is { Count: > 0 })
        {
            var stillValid = cached.Where(p => Validate(kind, p)).ToList();
            if (stillValid.Count > 0)
            {
                return new ResourceLocatorResult
                {
                    Found = true, Path = stillValid[0], Source = "缓存",
                    Candidates = stillValid,
                };
            }
        }

        // 2. 冷却期内的空结果盘不再重复扫
        var roots = DeepScanRoots();
        var cooldownRoots = GetCooldownRoots(kind);
        var scanRoots = roots.Where(r => !cooldownRoots.Contains(r)).ToList();

        progress?.Report($"准备扫描 {scanRoots.Count} 个磁盘分区（含 U 盘）…");
        var result = ScanRootsAsync(kind, scanRoots, progress, ct).GetAwaiter().GetResult();

        if (result.Candidates.Count > 0)
        {
            SaveCache(kind, result.Candidates);
            ClearNoResultRoots(kind);
            return result with { Source = "全盘搜索" };
        }

        // 空结果：记录各盘冷却时间戳
        SetNoResultRoots(kind, scanRoots);
        return ResourceLocatorResult.NotFound("所有分区（含 U 盘）均未找到可用配置，冷却期内不再重复深扫");
    }

    /// <summary>在指定根目录列表内做剪枝遍历（深度 ≤6、跳过黑名单与重解析点、
    /// early-exit），返回全部通过校验的候选。</summary>
    private Task<ResourceLocatorResult> ScanRootsAsync(
        LocatableResource kind, List<string> roots, IProgress<string>? progress, CancellationToken ct)
    {
        return Task.Run(() =>
        {
            var candidates = new List<string>();
            var anchorFile = AnchorFile(kind);
            var anchorDir = AnchorDirName(kind);
            var isDirAnchor = IsDirectoryAnchor(kind);

            foreach (var root in roots)
            {
                ct.ThrowIfCancellationRequested();
                if (candidates.Count >= MaxValidatedCandidates) break;

                var queue = new Queue<(string Dir, int Depth)>();
                queue.Enqueue((root, 0));
                while (queue.Count > 0 && candidates.Count < MaxValidatedCandidates)
                {
                    ct.ThrowIfCancellationRequested();
                    var (dir, depth) = queue.Dequeue();
                    progress?.Report(dir);

                    IEnumerable<string> entries;
                    try
                    {
                        entries = Directory.EnumerateFileSystemEntries(dir);
                    }
                    catch (Exception ex) when (ex is UnauthorizedAccessException
                        or DirectoryNotFoundException or IOException)
                    {
                        continue; // 无权限/消失的目录直接跳过
                    }

                    foreach (var entry in entries)
                    {
                        if (candidates.Count >= MaxValidatedCandidates) break;
                        try
                        {
                            var attrs = File.GetAttributes(entry);
                            if (attrs.HasFlag(FileAttributes.ReparsePoint)) continue;

                            var name = Path.GetFileName(entry);
                            if (attrs.HasFlag(FileAttributes.Directory))
                            {
                                if (isDirAnchor && name.Equals(anchorDir, StringComparison.OrdinalIgnoreCase)
                                    && Validate(kind, entry))
                                {
                                    candidates.Add(entry);
                                    continue;
                                }
                                if (depth < MaxScanDepth && !PrunedDirNames.Contains(name))
                                {
                                    queue.Enqueue((entry, depth + 1));
                                }
                            }
                            else if (anchorFile is not null
                                && name.Equals(anchorFile, StringComparison.OrdinalIgnoreCase))
                            {
                                // 锚点是文件：Poppler 需要其所在目录（含 bin 归一层），
                                // Python 直接取文件本身
                                var target = kind == LocatableResource.Poppler
                                    ? Path.GetDirectoryName(entry)!
                                    : entry;
                                if (Validate(kind, target) && !candidates.Contains(target))
                                {
                                    candidates.Add(target);
                                }
                            }
                            else if (kind == LocatableResource.OfflineWheels
                                && name.EndsWith(".whl", StringComparison.OrdinalIgnoreCase))
                            {
                                var dirOf = Path.GetDirectoryName(entry)!;
                                if (Validate(kind, dirOf) && !candidates.Contains(dirOf))
                                {
                                    candidates.Add(dirOf);
                                }
                                break; // 该目录已命中，不用再翻其余 whl
                            }
                        }
                        catch (Exception ex) when (ex is UnauthorizedAccessException
                            or FileNotFoundException or IOException)
                        {
                            continue;
                        }
                    }
                }
            }

            return new ResourceLocatorResult
            {
                Found = candidates.Count > 0,
                Path = candidates.Count > 0 ? candidates[0] : null,
                Candidates = candidates,
            };
        }, ct);
    }

    // ===== 校验器 =====

    /// <summary>校验候选是否真的可用（不只是名字匹配）。</summary>
    public bool Validate(LocatableResource kind, string path)
    {
        try
        {
            switch (kind)
            {
                case LocatableResource.Poppler:
                    return File.Exists(Path.Combine(path, "pdftoppm.exe"))
                        || File.Exists(Path.Combine(path, "pdftoppm"));
                case LocatableResource.BackendPython:
                    if (!File.Exists(path)) return false;
                    return RunProbe(path, "--version", 5_000); // 校验可执行且非残缺
                case LocatableResource.OfflineWheels:
                    return Directory.Exists(path)
                        && Directory.EnumerateFiles(path, "*.whl").Any();
                case LocatableResource.EmbedModelCache:
                    return Directory.Exists(path)
                        && Path.GetFileName(path.TrimEnd(Path.DirectorySeparatorChar))
                            .Equals("fastembed_cache", StringComparison.OrdinalIgnoreCase);
                default:
                    return false;
            }
        }
        catch
        {
            return false;
        }
    }

    /// <summary>执行受限参数探测命令（仅 --version 类，不执行任意内容）。</summary>
    private static bool RunProbe(string exe, string args, int timeoutMs)
    {
        try
        {
            using var p = Process.Start(new ProcessStartInfo
            {
                FileName = exe,
                Arguments = args,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            });
            if (p is null) return false;
            using var cts = new CancellationTokenSource(timeoutMs);
            p.WaitForExitAsync(cts.Token).Wait(cts.Token);
            return p.HasExited && p.ExitCode == 0;
        }
        catch
        {
            return false;
        }
    }

    // ===== 配置读写 =====

    /// <summary>当前显式配置的路径（未配置返回 null）。</summary>
    public string? ConfiguredPath(LocatableResource kind) => kind switch
    {
        LocatableResource.Poppler => NullIfEmpty(_settings.PopplerPath),
        LocatableResource.BackendPython => NullIfEmpty(_settings.BackendCommand),
        LocatableResource.OfflineWheels => NullIfEmpty(_settings.WheelsDir),
        LocatableResource.EmbedModelCache => NullIfEmpty(_settings.EmbedCacheDir),
        _ => null,
    };

    /// <summary>把选中的路径写入 AppSettings 并持久化。返回 true 表示写入成功。</summary>
    public bool Apply(LocatableResource kind, string path)
    {
        switch (kind)
        {
            case LocatableResource.Poppler:
                _settings.PopplerPath = path;
                break;
            case LocatableResource.BackendPython:
                _settings.BackendCommand = path;
                break;
            case LocatableResource.OfflineWheels:
                _settings.WheelsDir = path;
                break;
            case LocatableResource.EmbedModelCache:
                _settings.EmbedCacheDir = path;
                break;
            default:
                return false;
        }
        _settings.Save();
        _logger?.LogInformation("资源 {Kind} 配置为 {Path}", kind, path);
        return true;
    }

    // ===== 缓存（%LOCALAPPDATA%\DocMind\resource_scan_cache.json）=====

    private sealed class ScanCache
    {
        public Dictionary<string, List<string>> Candidates { get; set; } = new();
        public Dictionary<string, string> NoResultRoots { get; set; } = new(); // root → ISO 时间
    }

    private static string CacheFilePath()
    {
        var baseDir = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        return Path.Combine(baseDir, "DocMind", "resource_scan_cache.json");
    }

    private List<string>? LoadCache(LocatableResource kind)
    {
        try
        {
            var file = CacheFilePath();
            if (!File.Exists(file)) return null;
            var cache = JsonSerializer.Deserialize<ScanCache>(File.ReadAllText(file));
            return cache?.Candidates.TryGetValue(kind.ToString(), out var list) == true ? list : null;
        }
        catch
        {
            return null;
        }
    }

    private void SaveCache(LocatableResource kind, IReadOnlyList<string> candidates)
    {
        try
        {
            var file = CacheFilePath();
            Directory.CreateDirectory(Path.GetDirectoryName(file)!);
            var cache = File.Exists(file)
                ? JsonSerializer.Deserialize<ScanCache>(File.ReadAllText(file)) ?? new ScanCache()
                : new ScanCache();
            cache.Candidates[kind.ToString()] = candidates.ToList();
            File.WriteAllText(file, JsonSerializer.Serialize(cache));
        }
        catch { /* 缓存写失败不影响主流程 */ }
    }

    /// <summary>冷却期内（近期深扫空结果）的盘根集合：这些盘不再重复扫描。</summary>
    private HashSet<string> GetCooldownRoots(LocatableResource kind)
    {
        try
        {
            var file = CacheFilePath();
            if (!File.Exists(file)) return new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var cache = JsonSerializer.Deserialize<ScanCache>(File.ReadAllText(file));
            if (cache is null) return new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var key = kind.ToString();
            if (!cache.NoResultRoots.TryGetValue(key, out var rootsJoined)
                || !cache.NoResultRoots.TryGetValue(key + "_ts", out var iso)
                || !DateTime.TryParse(iso, out var ts))
            {
                return new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            }
            if (DateTime.UtcNow - ts.ToUniversalTime() >= NoResultCooldown)
            {
                return new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            }
            return rootsJoined
                .Split('|', StringSplitOptions.RemoveEmptyEntries)
                .ToHashSet(StringComparer.OrdinalIgnoreCase);
        }
        catch
        {
            return new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        }
    }

    private void SetNoResultRoots(LocatableResource kind, List<string> roots)
    {
        try
        {
            var file = CacheFilePath();
            Directory.CreateDirectory(Path.GetDirectoryName(file)!);
            var cache = File.Exists(file)
                ? JsonSerializer.Deserialize<ScanCache>(File.ReadAllText(file)) ?? new ScanCache()
                : new ScanCache();
            cache.NoResultRoots[kind.ToString()] = string.Join("|", roots);
            cache.NoResultRoots[kind.ToString() + "_ts"] = DateTime.UtcNow.ToString("O");
            File.WriteAllText(file, JsonSerializer.Serialize(cache));
        }
        catch { /* ignore */ }
    }

    private void ClearNoResultRoots(LocatableResource kind)
    {
        try
        {
            var file = CacheFilePath();
            if (!File.Exists(file)) return;
            var cache = JsonSerializer.Deserialize<ScanCache>(File.ReadAllText(file));
            if (cache is null) return;
            cache.NoResultRoots.Remove(kind.ToString());
            cache.NoResultRoots.Remove(kind.ToString() + "_ts");
            File.WriteAllText(file, JsonSerializer.Serialize(cache));
        }
        catch { /* ignore */ }
    }

    private static string? NullIfEmpty(string? s) =>
        string.IsNullOrWhiteSpace(s) ? null : s!.Trim();
}
