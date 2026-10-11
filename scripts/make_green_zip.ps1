# ============================================================
# scripts/make_green_zip.ps1
# Green portable ZIP packaging (called by publish_release.bat)
#
# Why not Compress-Archive:
#   1. Windows PowerShell 5.1 Compress-Archive writes entry names with
#      backslashes (python\python.exe instead of python/python.exe),
#      a non-standard ZIP form that some extractors mishandle;
#   2. the `staging\*` wildcard risks flattening directory contents to root.
# This script uses .NET ZipArchive with explicit forward-slash entry
# names, so the archive layout mirrors the directory layout exactly.
# ============================================================
param(
    [Parameter(Mandatory = $true)][string]$StagingDir,
    [Parameter(Mandatory = $true)][string]$OutputZip
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

if (-not (Test-Path -LiteralPath $StagingDir)) {
    Write-Host "[x] staging dir not found: $StagingDir"
    exit 1
}
if (-not (Test-Path -LiteralPath "$StagingDir\python\python.exe")) {
    Write-Host "[x] staging missing python\python.exe (portable runtime not copied correctly), abort"
    exit 1
}

if (Test-Path -LiteralPath $OutputZip) { Remove-Item -LiteralPath $OutputZip -Force }

$src = (Resolve-Path -LiteralPath $StagingDir).Path
$zip = [System.IO.Compression.ZipFile]::Open($OutputZip, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    $count = 0
    Get-ChildItem -LiteralPath $src -Recurse -File | ForEach-Object {
        $rel = $_.FullName.Substring($src.Length + 1).Replace('\', '/')
        [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
            $zip, $_.FullName, $rel, [System.IO.Compression.CompressionLevel]::Optimal)
        $count++
    }
    Write-Host "[OK] ZIP built: $OutputZip ($count file entries)"
} finally {
    $zip.Dispose()
}
