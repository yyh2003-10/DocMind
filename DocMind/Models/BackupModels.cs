namespace DocMind.Models;

public sealed record BackupCreateRequest(string? OutputPath = null, bool IncludeLogs = false);

public sealed record BackupRestoreRequest(string BackupPath);

public sealed record DiagnosticBundleRequest(string? OutputPath = null, bool IncludeLogs = false);

public sealed record BackupResponse(
    bool Ok,
    string Path,
    string? CreatedAt = null,
    long DatabaseSize = 0,
    List<string>? Tables = null,
    string? PreviousBackup = null,
    string? Error = null);
