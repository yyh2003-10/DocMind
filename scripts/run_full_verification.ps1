#Requires -Version 5.1
<#
.SYNOPSIS
    DocMind 全方面检测：分域 pytest + 商用门禁 + 业务矩阵 + 可选 dotnet/smoke。

.DESCRIPTION
    在隔离目录或主仓上跑完整验证，并输出报告到 docs/verification/full-test-report.md。
    退出码：0 = 无 FAIL；1 = 有 FAIL。

.EXAMPLE
    .\scripts\run_full_verification.ps1
    .\scripts\run_full_verification.ps1 -RepoRoot E:\DocMindY-worktrees\agent-p0 -IncludeDotnet
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = '',
    [string]$Python = '',
    [switch]$IncludeDotnet,
    [switch]$IncludeSmoke,
    [string]$SmokeBaseUrl = 'http://127.0.0.1:8765',
    [switch]$SkipFullSuite
)

$ErrorActionPreference = 'Continue'
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    if (Test-Path 'E:\DocMindY-worktrees\agent-p0\src\doc2mind') {
        $RepoRoot = 'E:\DocMindY-worktrees\agent-p0'
    } else {
        $RepoRoot = (Get-Location).Path
    }
}
$RepoRoot = (Resolve-Path $RepoRoot).Path
if ([string]::IsNullOrWhiteSpace($Python)) {
    if (Test-Path 'E:\DocMindY\.venv\Scripts\python.exe') {
        $Python = 'E:\DocMindY\.venv\Scripts\python.exe'
    } else {
        $Python = 'python'
    }
}

$env:PYTHONPATH = Join-Path $RepoRoot 'src'
$env:DOC2MIND_DISABLE_AUTH = '1'
$TestsDir = Join-Path $RepoRoot 'tests'
$ReportDir = Join-Path $RepoRoot 'docs\verification'
New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null
$ReportPath = Join-Path $ReportDir 'full-test-report.md'
$stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$results = New-Object System.Collections.Generic.List[object]

function Invoke-PyTestGroup {
    param([string]$Name, [string[]]$Files, [switch]$Required)
    $existing = @()
    foreach ($f in $Files) {
        $p = Join-Path $TestsDir $f
        if (Test-Path $p) { $existing += $p }
    }
    if ($existing.Count -eq 0) {
        $results.Add([pscustomobject]@{ Group = $Name; Status = 'SKIP'; Detail = 'no test files' })
        Write-Host "[SKIP] $Name (no files)" -ForegroundColor Yellow
        return
    }
    Write-Host "==> $Name ($($existing.Count) files)" -ForegroundColor Cyan
    $out = & $Python -m pytest @existing -q --tb=line 2>&1 | Out-String
    $code = $LASTEXITCODE
    if ($code -eq 0) {
        $summary = ($out -split "`n" | Select-String -Pattern 'passed|no tests' | Select-Object -Last 1)
        $results.Add([pscustomobject]@{ Group = $Name; Status = 'PASS'; Detail = "$summary" })
        Write-Host "[PASS] $Name — $summary" -ForegroundColor Green
    } else {
        $summary = ($out -split "`n" | Select-String -Pattern 'failed|error|passed' | Select-Object -Last 3) -join ' | '
        $results.Add([pscustomobject]@{ Group = $Name; Status = if ($Required) { 'FAIL' } else { 'FAIL' }; Detail = $summary })
        Write-Host "[FAIL] $Name — $summary" -ForegroundColor Red
        Write-Host $out
    }
}

# ---------- 1) 分域测试 ----------
Invoke-PyTestGroup -Name '商业门禁与基础能力' -Required -Files @(
    'test_commercial_gates.py', 'test_foundations_upgrade.py',
    'test_contract_p0_gates.py', 'test_business_matrix.py',
    'test_http_foundation_api.py', 'test_http_dto_contract.py',
    'test_auth_middleware.py'
)
Invoke-PyTestGroup -Name '对话/续写/双轨' -Required -Files @(
    'test_rag.py', 'test_prompt_policy.py', 'test_continue_merge.py',
    'test_wpf_p0_contract.py', 'test_stream_resilience.py',
    'test_rag_agent_planning.py', 'test_chat_store.py'
)
Invoke-PyTestGroup -Name 'Agent 骨架与执行器' -Required -Files @(
    'test_agent_chat_stream.py', 'test_agent_runtime.py',
    'test_agent_executors.py'
)
Invoke-PyTestGroup -Name '导入/取消/软删' -Required -Files @(
    'test_fc01_import_cancel.py', 'test_cancel_contracts.py',
    'test_soft_delete.py', 'test_ingest_stage_progress.py',
    'test_p0_foundation.py', 'test_extractor.py'
)
Invoke-PyTestGroup -Name '搜索/检索/重排' -Files @(
    'test_fc07_search_empty.py', 'test_bm25_ranking.py',
    'test_rerank.py', 'test_confidence_citation.py',
    'test_contextual_retrieval.py'
)
Invoke-PyTestGroup -Name '图谱/科研/意图' -Files @(
    'test_arbiter.py', 'test_intent_classifier.py',
    'test_research_context.py', 'test_research_citation_support.py',
    'test_research_graph_topic.py'
)
Invoke-PyTestGroup -Name '创作/导出/质检' -Files @(
    'test_creative_api.py', 'test_creator_exporters.py',
    'test_pptx_p0_quality.py', 'test_pptx_inspector.py',
    'test_curator.py', 'test_config.py'
)
Invoke-PyTestGroup -Name 'LLM/元数据/健壮性' -Files @(
    'test_llm_providers.py', 'test_metadata.py', 'test_model_registry.py',
    'test_llm_output.py', 'test_robustness_fixes.py',
    'test_weak_model_resilience.py', 'test_web_search_deadline.py'
)

# ---------- 2) 全量 pytest（排除已知坏文件） ----------
if (-not $SkipFullSuite) {
    Write-Host '==> 全量 pytest（排除 test_retrieval_eval）' -ForegroundColor Cyan
    $ignore = Join-Path $TestsDir 'test_retrieval_eval.py'
    $out = & $Python -m pytest $TestsDir -q --tb=line --ignore=$ignore 2>&1 | Out-String
    $code = $LASTEXITCODE
    $summary = ($out -split "`n" | Select-String -Pattern 'passed|failed|skipped' | Select-Object -Last 1)
    $status = if ($code -eq 0) { 'PASS' } else { 'FAIL' }
    $results.Add([pscustomobject]@{ Group = '全量 pytest'; Status = $status; Detail = "$summary" })
    Write-Host "[$status] 全量 pytest — $summary"
    if ($code -ne 0) { Write-Host $out }
}

# ---------- 3) 业务结构矩阵（Python 一键） ----------
Write-Host '==> 业务矩阵 import 自检' -ForegroundColor Cyan
$pyCheck = @"
import sys
sys.path.insert(0, r'$RepoRoot\src')
sys.path.insert(0, r'$RepoRoot')
from pathlib import Path
import tests.test_business_matrix as m
# execute assertions by calling test functions
m.test_all_required_endpoints_exist()
m.test_all_required_viewmodels_exist()
m.test_all_required_tests_exist()
m.test_feature_markers_present()
m.test_mcp_handler_count_at_least_20()
print('business matrix OK')
"@
$pyFile = Join-Path $env:TEMP 'docmind_matrix_check.py'
Set-Content -Path $pyFile -Value $pyCheck -Encoding UTF8
$mOut = & $Python $pyFile 2>&1 | Out-String
if ($LASTEXITCODE -eq 0 -and $mOut -match 'OK') {
    $results.Add([pscustomobject]@{ Group = '业务矩阵'; Status = 'PASS'; Detail = 'endpoints/vms/tests/markers OK' })
    Write-Host '[PASS] 业务矩阵'
} else {
    $results.Add([pscustomobject]@{ Group = '业务矩阵'; Status = 'FAIL'; Detail = $mOut })
    Write-Host "[FAIL] 业务矩阵`n$mOut" -ForegroundColor Red
}

# ---------- 4) 可选 dotnet ----------
if ($IncludeDotnet) {
    Write-Host '==> dotnet build / test' -ForegroundColor Cyan
    $csproj = Join-Path $RepoRoot 'DocMind\DocMind.csproj'
    $testProj = Join-Path $RepoRoot 'DocMind.Tests\DocMind.Tests.csproj'
    $bOut = & dotnet build $csproj -v q --nologo 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0) {
        $results.Add([pscustomobject]@{ Group = 'dotnet build'; Status = 'PASS'; Detail = 'ok' })
        if (Test-Path $testProj) {
            $tOut = & dotnet test $testProj -v q --nologo 2>&1 | Out-String
            $ts = if ($LASTEXITCODE -eq 0) { 'PASS' } else { 'FAIL' }
            $results.Add([pscustomobject]@{ Group = 'dotnet test'; Status = $ts; Detail = ($tOut -split "`n" | Select-Object -Last 1) })
        }
    } else {
        $results.Add([pscustomobject]@{ Group = 'dotnet build'; Status = 'FAIL'; Detail = ($bOut -split "`n" | Select-Object -Last 2) -join ' ' })
        Write-Host $bOut
    }
} else {
    $results.Add([pscustomobject]@{ Group = 'dotnet'; Status = 'SKIP'; Detail = '未启用 -IncludeDotnet（或环境 NuGet 不可用）' })
}

# ---------- 5) 可选 API smoke ----------
if ($IncludeSmoke) {
    $smoke = Join-Path $RepoRoot 'scripts\smoke.ps1'
    if (Test-Path $smoke) {
        Write-Host "==> smoke.ps1 → $SmokeBaseUrl" -ForegroundColor Cyan
        $sOut = & powershell -NoProfile -File $smoke -BaseUrl $SmokeBaseUrl 2>&1 | Out-String
        $ss = if ($LASTEXITCODE -eq 0) { 'PASS' } else { 'FAIL' }
        $results.Add([pscustomobject]@{ Group = 'API smoke'; Status = $ss; Detail = ($sOut -split "`n" | Select-Object -Last 3) -join ' | ' })
        if ($ss -eq 'FAIL') { Write-Host $sOut }
    }
} else {
    $results.Add([pscustomobject]@{ Group = 'API smoke'; Status = 'SKIP'; Detail = '未启用 -IncludeSmoke / 后端可能未运行' })
}

# ---------- 报告 ----------
$fail = @($results | Where-Object { $_.Status -eq 'FAIL' }).Count
$pass = @($results | Where-Object { $_.Status -eq 'PASS' }).Count
$skip = @($results | Where-Object { $_.Status -eq 'SKIP' }).Count

$md = @()
$md += "# DocMind 全方面检测报告"
$md += ""
$md += "- 时间：$stamp"
$md += "- 仓库：``$RepoRoot``"
$md += "- Python：``$Python``"
$md += "- 结果：**PASS=$pass / FAIL=$fail / SKIP=$skip**"
$md += ""
$md += "## 分组结果"
$md += ""
$md += "| 分组 | 状态 | 摘要 |"
$md += "|---|---|---|"
foreach ($r in $results) {
    $detail = "$($r.Detail)" -replace '\|', '/' -replace "`n", ' '
    if ($detail.Length -gt 160) { $detail = $detail.Substring(0, 160) + '…' }
    $md += "| $($r.Group) | **$($r.Status)** | $detail |"
}
$md += ""
$md += "## 说明"
$md += ""
$md += "- `test_retrieval_eval.py` 默认排除（缺 `eval_retrieval` 模块，环境债）。"
$md += "- `test_rag.py::test_source_log_recorded` 在全量跑时可能 caplog 串扰失败，单测可通过。"
$md += "- WPF `dotnet` 在 NuGet path1 环境债下可能失败，需在可用开发机上补跑。"
$md += "- API smoke 需要后端已启动；未启动时记 SKIP。"
$md += ""
$md += "## 商用门禁已覆盖"
$md += ""
$md += "- Agent 未开启时服务端回落 RAG"
$md += "- 回收站清理二次确认（前端）+ 恢复需重摄入提示"
$md += "- 路径穿越 / 超大写入拒绝"
$md += "- API 鉴权 401 / Bearer 放行"
$md += "- 密钥不进生效配置明文列表"
$md += "- 导入取消残留明细 / 搜索空库分流 / LLM 前置禁用 / 离线横幅接线"
$md += ""

$md | Set-Content -Path $ReportPath -Encoding UTF8
Write-Host ""
Write-Host "报告已写入: $ReportPath" -ForegroundColor Cyan
Write-Host "PASS=$pass FAIL=$fail SKIP=$skip"
if ($fail -gt 0) { exit 1 } else { exit 0 }
