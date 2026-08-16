# ============================================================
# DocMind 一键部署脚本（Windows / PowerShell）
#
# 用法（普通用户即可，无需管理员）：
#   powershell -ExecutionPolicy Bypass -File scripts/setup.ps1             # 仅 core
#   powershell -ExecutionPolicy Bypass -File scripts/setup.ps1 -All        # 全功能（推荐）
#   powershell -ExecutionPolicy Bypass -File scripts/setup.ps1 -Gpu -Ocr   # GPU + OCR
#
# 参数：
#   -All        全功能：core + server + ocr + gpu
#   -Gpu        GPU 加速嵌入（onnxruntime-gpu，需 NVIDIA 显卡）
#   -Ocr        PaddleOCR 图片文字识别（GPU 版 paddlepaddle-gpu；无显卡请改 CPU 版）
#   -SkipTests  跳过安装后的 pytest 验证
#   -Python     Python 解释器命令（默认 "python"；建议 Python 3.11）
#
# 设计要点（保证"换设备可复现"）：
#   - 依赖全部锁定在 requirements-*.txt（本机已验证组合），不跑 pip 自由解析
#   - 项目本身以 editable 方式注册（--no-deps，避免二次解析覆盖锁定版本）
#   - 国内网络：pip 走清华镜像；HF 模型下载内置 hf-mirror.com 镜像
#   - 大 whl（paddlepaddle-gpu ~300MB 等）pip 若停滞，用 curl -C - 断点续传
#     下载后 pip install <本地whl>，详见 docs/部署指南.md
# ============================================================

[CmdletBinding()]
param(
    [switch]$All,
    [switch]$Gpu,
    [switch]$Ocr,
    [switch]$SkipTests,
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

# --- 国内 pip 镜像（默认清华；可用 -PipMirror 自定义） ---
$PipMirror = "https://pypi.tuna.tsinghua.edu.cn/simple"

function Write-Step([string]$Msg) {
    Write-Host ""
    Write-Host "==> $Msg" -ForegroundColor Cyan
}

function Test-Command([string]$Cmd) {
    return [bool](Get-Command $Cmd -ErrorAction SilentlyContinue)
}

# ------------------------------------------------------------
# 1. 检查 Python 版本（>= 3.10）
# ------------------------------------------------------------
Write-Step "检查 Python 环境"
if (-not (Test-Command $Python)) {
    throw "未找到 Python 命令：'$Python'。请先安装 Python 3.11（勾选 Add to PATH）后重试。"
}
$pyVer = (& $Python --version 2>&1) -replace "Python ", ""
Write-Host "Python: $pyVer"
$verParts = $pyVer.Split(".")
$major = [int]$verParts[0]
$minor = [int]$verParts[1]
if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 10)) {
    throw "DocMind 需要 Python >= 3.10，当前为 $pyVer。请升级 Python 后重试。"
}
if ($minor -lt 11) {
    Write-Warning "检测到 Python 3.$minor：config.toml 解析将使用 tomli 回退（功能不变）。推荐 Python 3.11+。"
}

# ------------------------------------------------------------
# 2. 创建 .venv
# ------------------------------------------------------------
$VenvDir = Join-Path $RepoRoot ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Step "创建虚拟环境 .venv"
    & $Python -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) { throw "创建 venv 失败" }
} else {
    Write-Host "虚拟环境已存在，跳过创建: $VenvDir"
}

# ------------------------------------------------------------
# 3. 安装锁定依赖
# ------------------------------------------------------------
Write-Step "安装锁定依赖（清华镜像）"
$reqs = @("requirements-core.txt")
if ($All -or $Ocr)   { $reqs += "requirements-ocr.txt" }
if ($All -or $Gpu)   { $reqs += "requirements-gpu.txt" }
if ($All)            { $reqs += "requirements-server.txt" }

foreach ($req in $reqs) {
    $reqPath = Join-Path $RepoRoot $req
    Write-Host "--- $req ---"
    & $VenvPython -m pip install -r $reqPath -i $PipMirror
    if ($LASTEXITCODE -ne 0) { throw "依赖安装失败: $req（大包可尝试 curl -C - 断点续传，见 docs/部署指南.md）" }
}

# ------------------------------------------------------------
# 4. 以 editable 注册项目（不重新解析依赖，保住锁定版本）
# ------------------------------------------------------------
Write-Step "注册 doc2mind 命令（editable，--no-deps）"
& $VenvPython -m pip install -e . --no-deps
if ($LASTEXITCODE -ne 0) { throw "editable 安装失败" }

# ------------------------------------------------------------
# 5. 冒烟验证：CLI 可用 + 测试通过
# ------------------------------------------------------------
Write-Step "冒烟验证"
& $VenvPython -c "import doc2mind; print('doc2mind import OK')"
if ($LASTEXITCODE -ne 0) { throw "doc2mind 导入失败" }

if (-not $SkipTests) {
    Write-Host "运行 pytest（约 1 分钟）..."
    & $VenvPython -m pytest tests -q
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "测试未全部通过（$LASTEXITCODE）。请检查输出后重试，或加 -SkipTests 跳过。"
    } else {
        Write-Host "全部测试通过" -ForegroundColor Green
    }
}

# ------------------------------------------------------------
# 6. 完成提示
# ------------------------------------------------------------
Write-Host ""
Write-Host "================================" -ForegroundColor Green
Write-Host "DocMind 部署完成！" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Green
Write-Host ""
Write-Host "启动后端："
Write-Host "  .\.venv\Scripts\doc2mind.exe serve        # HTTP 服务 (localhost:8765)"
Write-Host "  .\.venv\Scripts\doc2mind.exe mcp          # MCP Server"
Write-Host "  .\.venv\Scripts\doc2mind.exe ingest ./文档目录"
Write-Host ""
Write-Host "WPF 桌面客户端（可选）："
Write-Host "  cd DocMind && dotnet build -c Release    # 需要 .NET 8 SDK"
Write-Host "  运行后会自动从 .venv 拉起后端（BackendProcessService）"
Write-Host ""
if (-not $All) {
    Write-Host "本次未装全功能。需要时补装："
    Write-Host "  powershell -ExecutionPolicy Bypass -File scripts/setup.ps1 -All"
    Write-Host ""
}
Write-Host "首次使用嵌入模型需联网下载（约 90MB，自动走 hf-mirror.com 镜像）。"
Write-Host "模型缓存目录：%LOCALAPPDATA%\doc2mind\fastembed_cache"
Write-Host "离线设备：从网络正常的机器把该目录整体拷贝过来即可（保留目录结构）。"
Write-Host ""
