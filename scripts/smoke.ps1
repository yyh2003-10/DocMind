#Requires -Version 5.1
<#
.SYNOPSIS
    DocMind 核心链路 API 冒烟验证脚本（Windows / PowerShell 5.1+）。

.DESCRIPTION
    按 docs/verification/core-chain-and-smoke.md 的「卡点验收矩阵」逐项验证。
    主链（摄入 → 分块嵌入 → 混合检索 → RAG 问答 → 流式 → 会话持久化）默认全跑；
    支线（转换 / 交付物导出 / 重建索引 / AI 整理 / 知识图谱 / 系统依赖）用 -Deep 开启。

    设计约束：
    - 数据隔离：默认写入独立集合 smoke-<timestamp>；-TempDb 时另起临时数据库文件。
    - 自动清理：默认删除本次摄入的文档与临时目录（-KeepTemp 保留现场）。
    - LLM 自适应：探测不到可用 LLM 时，相关项记 SKIP 而非 FAIL。
    - 退出码：0 = 无 FAIL；1 = 存在 FAIL。KNOWN 表示「已登记缺陷复现」，不计入退出码。

.EXAMPLE
    .\scripts\smoke.ps1
        连接已在运行的后端（默认 http://127.0.0.1:8765）。

    .\scripts\smoke.ps1 -StartBackend -TempDb -DisableAuth
        自起后端 + 临时库 + 关闭鉴权，最干净的隔离跑法（推荐给 CI）。

    .\scripts\smoke.ps1 -Deep -Verbose
        追加支线验证，并打印每个请求的细节。

.NOTES
    契约依据：src/doc2mind/server/http.py（45 个路由）与 docs/api.md。
    本脚本故意对「摄入幂等」等文档/实现有漂移的卡点做宽容判定，详见文档注释。
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = 'http://127.0.0.1:8765',
    [switch]$StartBackend,
    [switch]$TempDb,
    [switch]$DisableAuth,
    [string]$Collection = '',
    [switch]$Deep,
    [switch]$SkipLlm,
    [switch]$KeepTemp,
    [int]$HealthWaitSec = 90,
    [int]$JobWaitSec = 300
)

$ErrorActionPreference = 'Stop'

# 控制台按 UTF-8 输出：保证中文在被重定向/管道捕获（CI、IDE 终端）时不乱码
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { $OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }

# ---------------------------------------------------------------- 全局状态
$script:Results = @()
$script:Token = $null
$script:AuthEnabled = $false
$script:HasLlm = $false
$script:BackendProc = $null
$script:TempRoot = $null
$script:Collection = ''
$script:DocIds = @()
$script:ChatId = $null

# ---------------------------------------------------------------- 输出与记分
function Write-Section {
    param([string]$Text)
    Write-Host ''
    Write-Host ('--- ' + $Text + ' ' + ('-' * [Math]::Max(0, 66 - $Text.Length))) -ForegroundColor Cyan
}

function Add-Check {
    param(
        [Parameter(Mandatory)][string]$Id,
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][ValidateSet('PASS', 'FAIL', 'WARN', 'SKIP', 'KNOWN')][string]$Status,
        [string]$Evidence = ''
    )
    $script:Results += [pscustomobject]@{ Id = $Id; Name = $Name; Status = $Status; Evidence = $Evidence }
    $color = switch ($Status) {
        'PASS'  { 'Green' }
        'FAIL'  { 'Red' }
        'WARN'  { 'Yellow' }
        'SKIP'  { 'DarkGray' }
        'KNOWN' { 'Magenta' }
    }
    $tag = '[' + $Status.PadRight(5) + ']'
    $line = ($tag + ' ' + $Id.PadRight(4) + ' ' + $Name)
    if ($line.Length -lt 62) { $line = $line.PadRight(62) }
    Write-Host ($line + ' ' + $Evidence) -ForegroundColor $color
}

# ---------------------------------------------------------------- 文本解码
function Convert-DmText {
    <#
    修正 PowerShell 5.1 的响应体解码：Invoke-WebRequest 对无 charset 的 JSON 响应
    默认按 Latin1 解码，导致后端返回的中文变成 "æ··å"。这里把 Latin1 码点还原成
    UTF-8 字节再解码。若文本已含中日韩字符（PS7 或响应带 charset=utf-8）则原样返回。
    #>
    param([string]$Text)
    if (-not $Text) { return '' }
    if ($Text -match '[\u4e00-\u9fff]') { return $Text }
    if ($PSVersionTable.PSVersion.Major -gt 5) { return $Text }
    try {
        $bytes = [System.Text.Encoding]::GetEncoding(28591).GetBytes($Text)
        return [System.Text.Encoding]::UTF8.GetString($bytes)
    }
    catch { return $Text }
}

# ---------------------------------------------------------------- HTTP 封装
function Invoke-Dm {
    param(
        [Parameter(Mandatory)][ValidateSet('GET', 'POST', 'PUT', 'DELETE')][string]$Method,
        [Parameter(Mandatory)][string]$Path,
        [hashtable]$Body,
        [hashtable]$Query,
        [switch]$NoAuth,
        [int]$TimeoutSec = 120
    )

    $uri = $script:BaseUrl + $Path
    if ($Query -and $Query.Count -gt 0) {
        $pairs = foreach ($k in $Query.Keys) { '{0}={1}' -f $k, [uri]::EscapeDataString([string]$Query[$k]) }
        $uri = $uri + '?' + ($pairs -join '&')
    }

    $headers = @{ 'Accept' = 'application/json' }
    if (-not $NoAuth -and $script:Token) { $headers['X-DocMind-Token'] = $script:Token }

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $ps = @{
            Uri              = $uri
            Method           = $Method
            Headers          = $headers
            TimeoutSec       = $TimeoutSec
            UseBasicParsing  = $true
            ErrorAction      = 'Stop'
        }
        if ($Method -in @('POST', 'PUT')) {
            $ps['ContentType'] = 'application/json; charset=utf-8'
            $ps['Body'] = if ($Body) { $Body | ConvertTo-Json -Depth 12 -Compress } else { '{}' }
        }
        $resp = Invoke-WebRequest @ps
        $sw.Stop()
        $parsed = $null
        $content = Convert-DmText $resp.Content
        if ($content) { try { $parsed = $content | ConvertFrom-Json } catch { $parsed = $null } }
        Write-Verbose ("{0} {1} -> {2} ({3}ms)" -f $Method, $uri, $resp.StatusCode, $sw.ElapsedMilliseconds)
        return [pscustomobject]@{
            Ok = $true; Status = [int]$resp.StatusCode; Json = $parsed
            Raw = $content; Ms = [int]$sw.ElapsedMilliseconds; Message = ''
        }
    }
    catch {
        $sw.Stop()
        $status = 0; $raw = ''; $parsed = $null
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        # Invoke-WebRequest 会把 4xx/5xx 的响应体放进 ErrorDetails，优先取它
        if ($_.ErrorDetails -and $_.ErrorDetails.Message) { $raw = Convert-DmText $_.ErrorDetails.Message }
        elseif ($_.Exception.Response) {
            $stream = $_.Exception.Response.GetResponseStream()
            if ($stream) {
                $reader = New-Object System.IO.StreamReader($stream, [System.Text.Encoding]::UTF8)
                $raw = Convert-DmText ($reader.ReadToEnd()); $reader.Close()
            }
        }
        if ($raw) { try { $parsed = $raw | ConvertFrom-Json } catch { $parsed = $null } }
        Write-Verbose ("{0} {1} -> {2} ({3}ms) {4}" -f $Method, $uri, $status, $sw.ElapsedMilliseconds, $_.Exception.Message)
        return [pscustomobject]@{
            Ok = $false; Status = $status; Json = $parsed
            Raw = $raw; Ms = [int]$sw.ElapsedMilliseconds; Message = $_.Exception.Message
        }
    }
}

function Get-ErrMessage {
    param($Result)
    if ($Result.Json -and $Result.Json.PSObject.Properties['detail'] -and $Result.Json.detail.message) {
        return [string]$Result.Json.detail.message
    }
    if ($Result.Json -and $Result.Json.PSObject.Properties['message']) { return [string]$Result.Json.message }
    if ($Result.Json -and $Result.Json.PSObject.Properties['error']) { return [string]$Result.Json.error }
    return ([string]$Result.Message)
}

function Wait-Job {
    param([string]$JobId, [int]$TimeoutSec = 300)
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $r = Invoke-Dm -Method GET -Path ("/v1/jobs/{0}" -f $JobId)
        if ($r.Ok -and $r.Json) {
            if ($r.Json.status -in @('completed', 'failed')) { return $r }
        }
        Start-Sleep -Seconds 2
    }
    return $null
}

# ---------------------------------------------------------------- SSE 检查
function Test-SseStream {
    param([string]$Query, [string]$OutFile)

    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if (-not $curl) {
        return [pscustomobject]@{ Supported = $false; Reason = '未找到 curl.exe（Windows 10 1803+ 自带），SSE 检查跳过' }
    }

    $payloadPath = Join-Path $script:TempRoot 'sse-payload.json'
    $payload = @{ query = $Query; collections = @($script:Collection); topK = 5 } | ConvertTo-Json -Depth 6 -Compress
    [System.IO.File]::WriteAllText($payloadPath, $payload, (New-Object System.Text.UTF8Encoding($false)))

    $curlArgs = @('-N', '-s', '-X', 'POST', ($script:BaseUrl + '/v1/chat/stream'), '-H', 'Content-Type: application/json')
    if ($script:Token) { $curlArgs += @('-H', ('X-DocMind-Token: ' + $script:Token)) }
    $curlArgs += @('--data-binary', ("@" + $payloadPath), '-o', $OutFile, '-w', '%{time_starttransfer}')

    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $ttfb = & curl.exe @curlArgs 2>$null
    $sw.Stop()

    $raw = ''
    if (Test-Path $OutFile) { $raw = [System.IO.File]::ReadAllText($OutFile, [System.Text.Encoding]::UTF8) }
    $dataLines = @()
    if ($raw) { $dataLines = @($raw -split "`n" | Where-Object { $_.TrimStart().StartsWith('data: ') }) }

    $tokenCount = @($dataLines | Where-Object { $_ -match '"token"' }).Count
    $statusCnt  = @($dataLines | Where-Object { $_ -match '"type"\s*:\s*"status"' }).Count
    $doneLine   = @($dataLines | Where-Object { $_ -match '"done"\s*:\s*true' }) | Select-Object -First 1
    $errLine    = @($dataLines | Where-Object { $_ -match '"error"' }) | Select-Object -First 1

    return [pscustomobject]@{
        Supported = $true
        TtfbSec   = $ttfb
        TotalMs   = [int]$sw.ElapsedMilliseconds
        Frames    = $dataLines.Count
        Status    = $statusCnt
        Tokens    = $tokenCount
        Done      = [bool]$doneLine
        DoneJson  = $doneLine
        Error     = $errLine
    }
}

# ---------------------------------------------------------------- 语料准备
function New-SmokeCorpus {
    param([string]$Dir)

    New-Item -ItemType Directory -Path $Dir -Force | Out-Null
    $path = Join-Path $Dir 'smoke-corpus.md'
    $text = @'
# 冒烟语料（自动生成，可安全删除）

## 混合检索原理

DocMind 采用向量检索与 BM25 关键词检索的双引擎混合方案，两路结果通过 RRF 融合排序后返回。
向量检索负责语义相近，BM25 负责关键词精确命中，二者互补。

## 设备参数速查表

气缸缸径为 32mm，电机额定功率为 750W，电源适配 220V 交流输入。
控制器默认 IP 地址为 192.168.1.10，额定电流 5A，防护等级 IP54。
伺服驱动器的响应频率为 2.0kHz，重复定位精度正负 0.01mm。

## 维护要点

每半年检查一次气缸密封件，电机轴承补充润滑脂，电源接线端子紧固扭矩 1.2Nm。
'@
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding($false)))
    return $path
}

# ---------------------------------------------------------------- 主流程
try {
    Write-Host ''
    Write-Host 'DocMind 核心链路冒烟验证' -ForegroundColor Cyan
    Write-Host ('目标后端: ' + $BaseUrl) -ForegroundColor DarkGray

    # 临时工作目录
    $ts = Get-Date -Format 'yyyyMMdd-HHmmss'
    $script:TempRoot = Join-Path $env:TEMP ('docmind-smoke-' + $ts)
    New-Item -ItemType Directory -Path $script:TempRoot -Force | Out-Null

    if (-not $Collection) { $Collection = 'smoke-' + ($ts -replace '-', '') }
    $script:Collection = $Collection
    Write-Host ('集合: ' + $script:Collection + '    临时目录: ' + $script:TempRoot) -ForegroundColor DarkGray

    # ---------------------------------------------------------- 0. 后端启动
    if ($StartBackend) {
        Write-Section '0. 后端启动'
        if ($TempDb) {
            $dbDir = Join-Path $script:TempRoot 'db'
            New-Item -ItemType Directory -Path $dbDir -Force | Out-Null
            $env:DOC2MIND_DB_PATH = Join-Path $dbDir 'smoke.db'
        }
        if ($DisableAuth) { $env:DOC2MIND_DISABLE_AUTH = '1' }

        $port = ([uri]$BaseUrl).Port
        Write-Host ('拉起后端: doc2mind serve --port ' + $port) -ForegroundColor DarkGray
        try {
            $script:BackendProc = Start-Process -FilePath 'doc2mind' -ArgumentList @('serve', '--port', $port) -PassThru -WindowStyle Hidden
        }
        catch {
            Add-Check '0.01' '后端进程可启动' 'FAIL' ('无法执行 doc2mind: ' + $_.Exception.Message)
            throw
        }

        # 等待健康
        $ok = $false
        $deadline = (Get-Date).AddSeconds($HealthWaitSec)
        while ((Get-Date) -lt $deadline) {
            $r = Invoke-Dm -Method GET -Path '/v1/health' -NoAuth -TimeoutSec 10
            if ($r.Ok) { $ok = $true; break }
            Start-Sleep -Seconds 2
        }
        if (-not $ok) {
            Add-Check '0.02' (('后端在 {0}s 内就绪' -f $HealthWaitSec)) 'FAIL' '健康检查始终不通（首次启动需下载嵌入模型，可增大 -HealthWaitSec）'
            throw '后端未就绪'
        }
        Add-Check '0.02' (('后端在 {0}s 内就绪' -f $HealthWaitSec)) 'PASS' ('等待已通过，耗时见后续 health 项')
    }
    elseif ($TempDb -or $DisableAuth) {
        Write-Host '提示: -TempDb / -DisableAuth 仅在 -StartBackend 时生效（对已运行的后端无效）。' -ForegroundColor Yellow
    }

    # ---------------------------------------------------------- 1. 鉴权探测
    Write-Section '1. 服务与鉴权'

    $probe = Invoke-Dm -Method GET -Path '/v1/stats' -NoAuth -TimeoutSec 15
    if ($probe.Status -eq 401) {
        $script:AuthEnabled = $true
        # 读令牌文件（http.py: _user_data_dir()/server.token）
        $tokenFile = Join-Path $env:LOCALAPPDATA 'doc2mind\server.token'
        if (Test-Path $tokenFile) {
            $script:Token = (Get-Content $tokenFile -Raw).Trim()
            Add-Check '1.01' '鉴权生效（无令牌请求被拒）' 'PASS' ('401 UNAUTHORIZED，已从 ' + $tokenFile + ' 读取令牌')
        }
        else {
            Add-Check '1.01' '鉴权生效但令牌文件缺失' 'FAIL' ('401，但找不到 ' + $tokenFile)
        }
    }
    elseif ($probe.Ok) {
        Add-Check '1.01' '鉴权已关闭（DOC2MIND_DISABLE_AUTH）' 'WARN' ('/v1/stats 匿名可达，仅开发环境允许；生产须启用 Bearer 令牌')
    }
    else {
        Add-Check '1.01' '后端可达性' 'FAIL' ('/v1/stats 返回 ' + $probe.Status + ' ' + (Get-ErrMessage $probe))
        throw '后端不可用'
    }

    $h = Invoke-Dm -Method GET -Path '/v1/health' -NoAuth -TimeoutSec 30
    if ($h.Ok -and $h.Json.status -eq 'ok' -and $h.Json.store_ok) {
        Add-Check '1.02' '健康检查（唯一匿名端点）' 'PASS' ('status=ok store_ok=True gpu=' + $h.Json.gpu_available + ' v' + $h.Json.version + ' ' + $h.Ms + 'ms')
    }
    elseif ($h.Ok) {
        Add-Check '1.02' '健康检查' 'WARN' ('status=' + $h.Json.status + ' store_ok=' + $h.Json.store_ok + ' store_error=' + $h.Json.store_error)
    }
    else {
        Add-Check '1.02' '健康检查' 'FAIL' (Get-ErrMessage $h)
    }

    if ($h.Ok) {
        $providers = @($h.Json.embed_providers)
        if ($providers.Count -gt 0) {
            Add-Check '1.03' '嵌入引擎可用（核心卖点前置）' 'PASS' ('providers=' + ($providers -join ',') + ' gpu=' + $h.Json.gpu_available)
        }
        else {
            Add-Check '1.03' '嵌入引擎可用（核心卖点前置）' 'WARN' ('health 未上报 embed_providers，向量路可能不可用（fastembed==0.8.0 属 core 依赖）')
        }
    }
    else {
        Add-Check '1.03' '嵌入引擎可用（核心卖点前置）' 'SKIP' 'health 未通过，无法判定'
    }

    $cfg = Invoke-Dm -Method GET -Path '/v1/config' -TimeoutSec 20
    if ($cfg.Ok) {
        Add-Check '1.04' '配置可读 GET /v1/config' 'PASS' ('embed=' + $cfg.Json.embed_model + ' llm_provider=' + $cfg.Json.llm_provider + ' rag_top_k=' + $cfg.Json.rag_top_k)
    }
    else {
        Add-Check '1.04' '配置可读 GET /v1/config' 'FAIL' (Get-ErrMessage $cfg)
    }

    # ---------------------------------------------------------- 2. 写入链
    Write-Section '2. 写入链（摄入 → 分块 → 嵌入 → 落库）'

    $cc = Invoke-Dm -Method POST -Path '/v1/collections' -Body @{ name = $script:Collection } -TimeoutSec 30
    if ($cc.Ok) {
        Add-Check '2.01' '创建集合 POST /v1/collections' 'PASS' ('name=' + $script:Collection)
    }
    else {
        Add-Check '2.01' '创建集合 POST /v1/collections' 'FAIL' (Get-ErrMessage $cc)
    }

    $corpus = New-SmokeCorpus -Dir (Join-Path $script:TempRoot 'corpus')
    $ing = Invoke-Dm -Method POST -Path '/v1/ingest' -Body @{ path = $corpus; collection = $script:Collection; force = $true } -TimeoutSec 300
    $first = $null
    if ($ing.Ok -and $ing.Json.ingested -and $ing.Json.ingested.Count -gt 0) {
        $first = $ing.Json.ingested[0]
        $script:DocIds += $first.document_id
        Add-Check '2.02' '文档摄入 POST /v1/ingest' 'PASS' ('chunk_count=' + $first.chunk_count + ' format=' + $first.format + ' ' + $ing.Ms + 'ms')
    }
    else {
        Add-Check '2.02' '文档摄入 POST /v1/ingest' 'FAIL' ('status=' + $ing.Status + ' ' + (Get-ErrMessage $ing))
    }

    # 幂等：api.md 记为 409 CONFLICT，实现为 200 + skipped 计数 —— 两种都判通过
    $ing2 = Invoke-Dm -Method POST -Path '/v1/ingest' -Body @{ path = $corpus; collection = $script:Collection; force = $false } -TimeoutSec 120
    if (($ing2.Status -eq 409) -or ($ing2.Ok -and $ing2.Json.skipped -ge 1)) {
        Add-Check '2.03' '重复摄入幂等（force=false）' 'PASS' ('HTTP ' + $ing2.Status + ' skipped=' + $ing2.Json.skipped)
    }
    else {
        Add-Check '2.03' '重复摄入幂等（force=false）' 'FAIL' ('HTTP ' + $ing2.Status + ' skipped=' + $ing2.Json.skipped + '（应拒绝或跳过）')
    }

    $txt = Invoke-Dm -Method POST -Path '/v1/ingest/text' -Body @{
        text       = '冒烟验证结论：混合检索由向量检索与 BM25 双路结果经 RRF 融合后返回。'
        title      = 'smoke-note-' + $ts
        collection = $script:Collection
    } -TimeoutSec 120
    if ($txt.Ok -and $txt.Json.ingested -and $txt.Json.ingested.Count -gt 0) {
        $script:DocIds += $txt.Json.ingested[0].document_id
        Add-Check '2.04' '文本直入 POST /v1/ingest/text' 'PASS' ('chunk_count=' + $txt.Json.ingested[0].chunk_count)
    }
    else {
        Add-Check '2.04' '文本直入 POST /v1/ingest/text' 'FAIL' (Get-ErrMessage $txt)
    }

    $st = Invoke-Dm -Method GET -Path '/v1/stats' -Query @{ collection = $script:Collection } -TimeoutSec 30
    if ($st.Ok -and $st.Json.total_documents -ge 2 -and $st.Json.total_chunks -gt 0) {
        Add-Check '2.05' '统计反映落库 GET /v1/stats' 'PASS' ('docs=' + $st.Json.total_documents + ' chunks=' + $st.Json.total_chunks)
    }
    else {
        Add-Check '2.05' '统计反映落库 GET /v1/stats' 'FAIL' ('docs=' + $st.Json.total_documents + ' chunks=' + $st.Json.total_chunks)
    }

    # ---------------------------------------------------------- 3. 检索链
    Write-Section '3. 检索链（向量 + BM25 → RRF 融合）'

    $sLong = Invoke-Dm -Method POST -Path '/v1/search' -Body @{ query = '混合检索 RRF 融合原理'; collection = $script:Collection; top_k = 5 } -TimeoutSec 60
    $hit = $null
    if ($sLong.Ok -and $sLong.Json.hits) { $hit = $sLong.Json.hits | Select-Object -First 1 }
    if ($hit -and -not $sLong.Json.degraded) {
        Add-Check '3.01' '混合检索召回（长查询）' 'PASS' ('total=' + $sLong.Json.total + ' match=' + $hit.match_type + ' vec=' + [Math]::Round($hit.vector_score, 4) + ' bm25=' + [Math]::Round($hit.bm25_score, 4) + ' ' + $sLong.Ms + 'ms')
    }
    elseif ($hit) {
        # degraded 语义比 api.md 更宽：嵌入/向量/重排任一不可用都会置位
        # 本环境实测为「重排模型不可用（fastembed 未安装）」，向量路仍可用
        Add-Check '3.01' '混合检索召回（长查询，但 degraded=True）' 'WARN' ('原因: ' + $sLong.Json.message + '；修复: pip install fastembed==0.8.0')
    }
    else {
        Add-Check '3.01' '混合检索召回（长查询）' 'FAIL' ('无命中 ' + (Get-ErrMessage $sLong))
    }

    if ($hit -and $hit.bm25_score -gt 0) {
        Add-Check '3.02' 'BM25 分量可用（对照组长词）' 'PASS' ('bm25_score=' + [Math]::Round($hit.bm25_score, 4))
    }
    elseif ($hit) {
        Add-Check '3.02' 'BM25 分量可用（对照组长词）' 'WARN' ('bm25_score=0，长词亦未命中关键词索引')
    }
    else {
        Add-Check '3.02' 'BM25 分量可用（对照组长词）' 'SKIP' '前一项无命中，无法判定'
    }

    # AUD-003 探针：<3 字符查询词在 trigram 分词器下 BM25 完全不召回
    $sShort = Invoke-Dm -Method POST -Path '/v1/search' -Body @{ query = '气缸'; collection = $script:Collection; top_k = 5 } -TimeoutSec 60
    $shortHit = $null
    if ($sShort.Ok -and $sShort.Json.hits) {
        $shortHit = $sShort.Json.hits | Where-Object { $_.source -like '*smoke-corpus*' } | Select-Object -First 1
        if (-not $shortHit) { $shortHit = $sShort.Json.hits | Select-Object -First 1 }
    }
    if ($shortHit -and $shortHit.bm25_score -gt 0) {
        Add-Check '3.03' 'AUD-003 短词 BM25 召回（2 字中文）' 'PASS' ('bm25_score=' + [Math]::Round($shortHit.bm25_score, 4) + '，AUD-003 已修复')
    }
    elseif ($shortHit) {
        Add-Check '3.03' 'AUD-003 短词 BM25 召回（2 字中文）' 'KNOWN' ('语料含「气缸缸径」但结果 bm25_score=0，关键词路零命中 → AUD-003 复现（trigram 分词器不支持 <3 字符）')
    }
    else {
        Add-Check '3.03' 'AUD-003 短词 BM25 召回（2 字中文）' 'FAIL' ('短词完全无召回，比 AUD-003 更严重：向量路也未命中')
    }

    $sEn = Invoke-Dm -Method POST -Path '/v1/search' -Body @{ query = 'IP'; collection = $script:Collection; top_k = 5 } -TimeoutSec 60
    $enHit = $null
    if ($sEn.Ok -and $sEn.Json.hits) { $enHit = $sEn.Json.hits | Select-Object -First 1 }
    if ($enHit -and $enHit.bm25_score -gt 0) {
        Add-Check '3.04' 'AUD-003 短词 BM25 召回（2 字符英文）' 'PASS' ('bm25_score=' + [Math]::Round($enHit.bm25_score, 4))
    }
    elseif ($enHit) {
        Add-Check '3.04' 'AUD-003 短词 BM25 召回（2 字符英文）' 'KNOWN' 'bm25_score=0，同 AUD-003'
    }
    else {
        Add-Check '3.04' 'AUD-003 短词 BM25 召回（2 字符英文）' 'FAIL' '短词完全无召回'
    }

    # min_score 误用提示（RRF 融合分约 0.016-0.033，传 0.9 应被识别为误用）
    $sMin = Invoke-Dm -Method POST -Path '/v1/search' -Body @{ query = '混合检索'; collection = $script:Collection; top_k = 5; min_score = 0.9 } -TimeoutSec 60
    if ($sMin.Ok) {
        if ($sMin.Json.total -eq 0 -or $sMin.Json.message) {
            Add-Check '3.05' 'min_score 越界提示' 'PASS' ('total=' + $sMin.Json.total + ' message=' + $sMin.Json.message)
        }
        else {
            Add-Check '3.05' 'min_score 越界提示' 'WARN' ('min_score=0.9 仍有 ' + $sMin.Json.total + ' 条命中且无提示，量纲提示可能失效')
        }
    }
    else {
        Add-Check '3.05' 'min_score 越界提示' 'FAIL' (Get-ErrMessage $sMin)
    }

    # ---------------------------------------------------------- 4. RAG 主链
    Write-Section '4. RAG 主链（检索 → 上下文 → LLM → 引用 → 会话持久化）'

    if (-not $SkipLlm) {
        $lt = Invoke-Dm -Method POST -Path '/v1/llm/test' -Body @{ timeout = 20 } -TimeoutSec 60
        if ($lt.Ok -and $lt.Json.ok) {
            $script:HasLlm = $true
            Add-Check '4.01' 'LLM 连通性 POST /v1/llm/test' 'PASS' ('provider=' + $lt.Json.provider + ' model=' + $lt.Json.model + ' ' + $lt.Json.elapsed_ms + 'ms')
        }
        elseif ($lt.Ok) {
            Add-Check '4.01' 'LLM 连通性 POST /v1/llm/test' 'WARN' ('已配置但调用失败: ' + $lt.Json.error)
        }
        else {
            Add-Check '4.01' 'LLM 连通性 POST /v1/llm/test' 'SKIP' ('未配置 LLM（HTTP ' + $lt.Status + '），后续 LLM 项跳过')
        }
    }
    else {
        Add-Check '4.01' 'LLM 连通性 POST /v1/llm/test' 'SKIP' '-SkipLlm 已指定'
    }

    if ($script:HasLlm) {
        $chat = Invoke-Dm -Method POST -Path '/v1/chat' -Body @{
            query = '这份语料里混合检索是怎么做的？'
            collections = @($script:Collection)
            topK = 5
        } -TimeoutSec 180
        if ($chat.Ok -and $chat.Json.answer) {
            $src = $chat.Json.sources | Select-Object -First 1
            $stype = if ($src) { $src.score_type } else { '(无来源)' }
            Add-Check '4.02' 'RAG 问答 POST /v1/chat' 'PASS' ('answer ' + $chat.Json.answer.Length + ' 字, sources=' + @($chat.Json.sources).Count + ' score_type=' + $stype + ' ' + $chat.Ms + 'ms')
        }
        else {
            Add-Check '4.02' 'RAG 问答 POST /v1/chat' 'FAIL' (Get-ErrMessage $chat)
        }

        if ($chat.Ok -and $chat.Json.chat_id) {
            $script:ChatId = $chat.Json.chat_id

            $sseFile = Join-Path $script:TempRoot 'sse.txt'
            $sse = Test-SseStream -Query '用一句话总结语料里的维护要点' -OutFile $sseFile
            if (-not $sse.Supported) {
                Add-Check '4.03' 'SSE 流式 POST /v1/chat/stream' 'SKIP' $sse.Reason
            }
            elseif ($sse.Done -and -not $sse.Error -and $sse.Tokens -gt 0) {
                Add-Check '4.03' 'SSE 流式 POST /v1/chat/stream' 'PASS' ('TTFB=' + $sse.TtfbSec + 's tokens=' + $sse.Tokens + ' 总帧=' + $sse.Frames + ' 结束帧 done=true')
            }
            elseif ($sse.Error) {
                Add-Check '4.03' 'SSE 流式 POST /v1/chat/stream' 'FAIL' ('流内错误帧: ' + $sse.Error)
            }
            else {
                Add-Check '4.03' 'SSE 流式 POST /v1/chat/stream' 'FAIL' ('未收到 done 帧（frames=' + $sse.Frames + '，可能命中 AUD-006 无收敛上限）')
            }

            if ($sse.Supported -and $sse.TtfbSec) {
                $ttfb = [double]$sse.TtfbSec
                if ($ttfb -le 8) {
                    Add-Check '4.04' '首 token 延迟（TTFB）' 'PASS' ($ttfb.ToString('0.00') + 's ≤ 8s')
                }
                else {
                    Add-Check '4.04' '首 token 延迟（TTFB）' 'WARN' ($ttfb.ToString('0.00') + 's > 8s（云端模型首次调用偏慢属正常）')
                }
            }
            else {
                Add-Check '4.04' '首 token 延迟（TTFB）' 'SKIP' '无 TTFB 数据'
            }

            $cl = Invoke-Dm -Method GET -Path '/v1/chats' -Query @{ limit = 20 } -TimeoutSec 30
            $mine = $null
            if ($cl.Ok -and $cl.Json.chats) { $mine = $cl.Json.chats | Where-Object { $_.chat_id -eq $script:ChatId } | Select-Object -First 1 }
            if ($mine) {
                Add-Check '4.05' '会话持久化 GET /v1/chats' 'PASS' ('chat_id=' + $mine.chat_id + ' messages=' + $mine.message_count)
            }
            else {
                Add-Check '4.05' '会话持久化 GET /v1/chats' 'FAIL' ('会话列表中找不到本次 chat_id=' + $script:ChatId)
            }

            $cd = Invoke-Dm -Method GET -Path ('/v1/chats/' + $script:ChatId) -TimeoutSec 30
            if ($cd.Ok -and $cd.Json.messages -and @($cd.Json.messages).Count -ge 2) {
                Add-Check '4.06' '会话消息完整 GET /v1/chats/{id}' 'PASS' ('messages=' + @($cd.Json.messages).Count + '（user+assistant 成对）')
            }
            else {
                Add-Check '4.06' '会话消息完整 GET /v1/chats/{id}' 'WARN' ('messages=' + @($cd.Json.messages).Count + '（AUD-008 非原子写入可能导致缺轮）')
            }

            # 多轮续聊：带同一 chat_id 再问一次，验证上下文生效
            $chat2 = Invoke-Dm -Method POST -Path '/v1/chat' -Body @{
                query = '那气缸的维护周期是多久？'
                collections = @($script:Collection)
                chatId = $script:ChatId
                topK = 5
            } -TimeoutSec 180
            if ($chat2.Ok -and $chat2.Json.chat_id -eq $script:ChatId) {
                Add-Check '4.07' '多轮续聊（chatId 复用）' 'PASS' ('answer ' + $chat2.Json.answer.Length + ' 字，沿用同一会话')
            }
            else {
                Add-Check '4.07' '多轮续聊（chatId 复用）' 'FAIL' (Get-ErrMessage $chat2)
            }
        }
        else {
            Add-Check '4.03' 'SSE 流式 POST /v1/chat/stream' 'SKIP' '前一项 /v1/chat 未返回 chat_id'
            Add-Check '4.05' '会话持久化 GET /v1/chats' 'SKIP' '前一项 /v1/chat 未返回 chat_id'
            Add-Check '4.06' '会话消息完整 GET /v1/chats/{id}' 'SKIP' '前一项 /v1/chat 未返回 chat_id'
            Add-Check '4.07' '多轮续聊（chatId 复用）' 'SKIP' '前一项 /v1/chat 未返回 chat_id'
        }
    }
    else {
        # 无 LLM 时的降级契约：chat / curate / graph extract 应统一 400
        $c0 = Invoke-Dm -Method POST -Path '/v1/chat' -Body @{ query = '测试'; collections = @($script:Collection) } -TimeoutSec 60
        if ($c0.Status -eq 400) {
            Add-Check '4.02' '未配 LLM 时 /v1/chat 返回 400' 'PASS' ('400 ' + (Get-ErrMessage $c0))
        }
        else {
            Add-Check '4.02' '未配 LLM 时 /v1/chat 返回 400' 'FAIL' ('期望 400，实际 ' + $c0.Status)
        }
        Add-Check '4.03' 'SSE 流式 POST /v1/chat/stream' 'SKIP' '无 LLM'
        Add-Check '4.04' '首 token 延迟（TTFB）' 'SKIP' '无 LLM'
        Add-Check '4.05' '会话持久化 GET /v1/chats' 'SKIP' '无 LLM'
        Add-Check '4.06' '会话消息完整 GET /v1/chats/{id}' 'SKIP' '无 LLM'
        Add-Check '4.07' '多轮续聊（chatId 复用）' 'SKIP' '无 LLM'
    }

    # ---------------------------------------------------------- 5. 支线（默认）
    Write-Section '5. 支线（默认档：质量 / 文档库 / 图谱规模）'

    $q = Invoke-Dm -Method GET -Path '/v1/quality' -Query @{ collection = $script:Collection } -TimeoutSec 60
    if ($q.Ok -and $q.Json.total_documents -ge 2) {
        Add-Check '5.01' '质量报告 GET /v1/quality' 'PASS' ('docs=' + $q.Json.total_documents + ' chunks=' + $q.Json.total_chunks + ' warnings=' + @($q.Json.warnings).Count)
    }
    else {
        Add-Check '5.01' '质量报告 GET /v1/quality' 'FAIL' (Get-ErrMessage $q)
    }

    $docs = Invoke-Dm -Method GET -Path '/v1/documents' -Query @{ collection = $script:Collection; pageSize = 20 } -TimeoutSec 60
    if ($docs.Ok -and @($docs.Json.documents).Count -ge 2) {
        Add-Check '5.02' '文档库列表 GET /v1/documents' 'PASS' ('total=' + $docs.Json.total + ' 返回=' + @($docs.Json.documents).Count)
    }
    else {
        Add-Check '5.02' '文档库列表 GET /v1/documents' 'FAIL' (Get-ErrMessage $docs)
    }

    $gs = Invoke-Dm -Method GET -Path '/v1/graph/stats' -TimeoutSec 60
    if ($gs.Ok) {
        Add-Check '5.03' '图谱规模 GET /v1/graph/stats' 'PASS' ('响应正常（节点数取决于是否跑过抽取）')
    }
    else {
        Add-Check '5.03' '图谱规模 GET /v1/graph/stats' 'FAIL' (Get-ErrMessage $gs)
    }

    $gv = Invoke-Dm -Method GET -Path '/v1/graph/visualize' -Query @{ collection = $script:Collection; limit = 200 } -TimeoutSec 60
    if ($gv.Ok) {
        Add-Check '5.04' '图谱可视化 GET /v1/graph/visualize' 'PASS' ('nodes=' + @($gv.Json.nodes).Count + ' edges=' + @($gv.Json.edges).Count)
    }
    else {
        Add-Check '5.04' '图谱可视化 GET /v1/graph/visualize' 'FAIL' (Get-ErrMessage $gv)
    }

    # ---------------------------------------------------------- 6. 支线（-Deep）
    if ($Deep) {
        Write-Section '6. 支线（-Deep：转换 / 导出 / 重建索引 / 整理 / 图谱抽取）'

        $cv = Invoke-Dm -Method POST -Path '/v1/convert' -Body @{ input_path = $corpus; output_format = 'md' } -TimeoutSec 120
        if ($cv.Ok -and $cv.Json.content) {
            Add-Check '6.01' '格式转换 POST /v1/convert' 'PASS' ('输出 ' + $cv.Json.content.Length + ' 字符, elements=' + $cv.Json.elements_count)
        }
        else {
            Add-Check '6.01' '格式转换 POST /v1/convert' 'FAIL' (Get-ErrMessage $cv)
        }

        $outPptx = Join-Path $script:TempRoot 'smoke-export.html'
        $ce = Invoke-Dm -Method POST -Path '/v1/creative/export' -Body @{
            content = '# 冒烟导出`n`n这是导出链路验证。`n'
            format = 'html'
            output_path = $outPptx
            title = '冒烟导出'
        } -TimeoutSec 120
        if ($ce.Ok) {
            $exists = Test-Path $outPptx
            Add-Check '6.02' '交付物导出 POST /v1/creative/export' $(if ($exists) { 'PASS' } else { 'WARN' }) ('ok=' + $ce.Json.ok + ' 文件已落盘=' + $exists)
        }
        else {
            Add-Check '6.02' '交付物导出 POST /v1/creative/export' 'FAIL' (Get-ErrMessage $ce)
        }

        $ri = Invoke-Dm -Method POST -Path '/v1/reindex' -Body @{ collection = $script:Collection } -TimeoutSec 60
        if ($ri.Ok -and $ri.Json.job_id) {
            $job = Wait-Job -JobId $ri.Json.job_id -TimeoutSec $JobWaitSec
            if ($job -and $job.Json.status -eq 'completed') {
                Add-Check '6.03' '重建索引 POST /v1/reindex' 'PASS' ('job=' + $ri.Json.job_id + ' status=completed')
            }
            elseif ($job) {
                Add-Check '6.03' '重建索引 POST /v1/reindex' 'FAIL' ('status=' + $job.Json.status + ' error=' + $job.Json.error)
            }
            else {
                Add-Check '6.03' '重建索引 POST /v1/reindex' 'FAIL' ('任务在 ' + $JobWaitSec + 's 内未收敛')
            }
        }
        else {
            Add-Check '6.03' '重建索引 POST /v1/reindex' 'FAIL' (Get-ErrMessage $ri)
        }

        # 重建索引后检索应仍可用
        $sAfter = Invoke-Dm -Method POST -Path '/v1/search' -Body @{ query = '伺服驱动器'; collection = $script:Collection; top_k = 5 } -TimeoutSec 60
        if ($sAfter.Ok -and $sAfter.Json.total -gt 0) {
            Add-Check '6.04' '重建索引后检索可用' 'PASS' ('total=' + $sAfter.Json.total)
        }
        else {
            Add-Check '6.04' '重建索引后检索可用' 'FAIL' ('重建后检索无命中: ' + (Get-ErrMessage $sAfter))
        }

        if ($script:HasLlm) {
            $cu = Invoke-Dm -Method POST -Path '/v1/curate' -Body @{
                collection = $script:Collection
                actions = @('enrich')
                dry_run = $true
                top_k = 5
            } -TimeoutSec 60
            if ($cu.Ok -and $cu.Json.job_id) {
                $cuj = Wait-Job -JobId $cu.Json.job_id -TimeoutSec $JobWaitSec
                if ($cuj -and $cuj.Json.status -eq 'completed' -and $cuj.Json.report) {
                    $dry = $cuj.Json.report.dry_run
                    Add-Check '6.05' 'AI 整理 dry_run 零写入 POST /v1/curate' $(if ($dry) { 'PASS' } else { 'FAIL' }) ('report.dry_run=' + $dry)
                }
                elseif ($cuj) {
                    Add-Check '6.05' 'AI 整理 dry_run 零写入 POST /v1/curate' 'FAIL' ('status=' + $cuj.Json.status + ' error=' + $cuj.Json.error)
                }
                else {
                    Add-Check '6.05' 'AI 整理 dry_run 零写入 POST /v1/curate' 'FAIL' ('任务在 ' + $JobWaitSec + 's 内未收敛')
                }
            }
            else {
                Add-Check '6.05' 'AI 整理 dry_run 零写入 POST /v1/curate' 'FAIL' (Get-ErrMessage $cu)
            }

            # 注意：/v1/graph/extract 用 Query 参数，不是 JSON body
            $ge = Invoke-Dm -Method POST -Path '/v1/graph/extract' -Query @{ collection = $script:Collection; top_k = 5 } -TimeoutSec $JobWaitSec
            if ($ge.Ok) {
                Add-Check '6.06' '图谱抽取 POST /v1/graph/extract' 'PASS' ('extracted=' + $ge.Json.extracted_count + ' skipped=' + $ge.Json.skipped_count + ' ' + $ge.Json.elapsed_ms + 'ms')
            }
            else {
                Add-Check '6.06' '图谱抽取 POST /v1/graph/extract' 'FAIL' (Get-ErrMessage $ge)
            }

            $gv2 = Invoke-Dm -Method GET -Path '/v1/graph/visualize' -Query @{ collection = $script:Collection; limit = 200 } -TimeoutSec 60
            if ($gv2.Ok -and @($gv2.Json.nodes).Count -gt 0) {
                Add-Check '6.07' '抽取后图谱有节点' 'PASS' ('nodes=' + @($gv2.Json.nodes).Count + ' edges=' + @($gv2.Json.edges).Count)
            }
            else {
                Add-Check '6.07' '抽取后图谱有节点' 'WARN' ('抽取完成但 visualize 无节点，检查图谱落库链路')
            }
        }
        else {
            Add-Check '6.05' 'AI 整理 dry_run 零写入 POST /v1/curate' 'SKIP' '无 LLM'
            Add-Check '6.06' '图谱抽取 POST /v1/graph/extract' 'SKIP' '无 LLM'
            Add-Check '6.07' '抽取后图谱有节点' 'SKIP' '无 LLM'
        }

        $dep = Invoke-Dm -Method GET -Path '/v1/system/dependencies' -TimeoutSec 60
        if ($dep.Ok) {
            Add-Check '6.08' '依赖清单 GET /v1/system/dependencies' 'PASS' '响应正常'
        }
        else {
            Add-Check '6.08' '依赖清单 GET /v1/system/dependencies' 'FAIL' (Get-ErrMessage $dep)
        }
    }
}
catch {
    Add-Check 'X.00' '脚本执行异常' 'FAIL' $_.Exception.Message
}
finally {
    # ---------------------------------------------------------- 清理
    Write-Section '清理'
    # 1) 删除本次摄入的文档（后端仍在运行时才能删）
    if (-not $KeepTemp) {
        $deleted = 0
        foreach ($id in ($script:DocIds | Where-Object { $_ })) {
            try {
                Invoke-Dm -Method DELETE -Path ('/v1/documents/' + $id) -TimeoutSec 30 | Out-Null
                $deleted++
            }
            catch { }
        }
        Write-Host ('已删除本次摄入的 ' + $deleted + ' 个文档') -ForegroundColor DarkGray
    }
    else {
        Write-Host ('保留现场: ' + $script:TempRoot) -ForegroundColor DarkGray
    }

    # 2) 先停后端，再删临时目录 —— 否则 sqlite 文件被占用，删除必然失败
    if ($script:BackendProc -and -not $script:BackendProc.HasExited) {
        try {
            Stop-Process -Id $script:BackendProc.Id -Force -ErrorAction Stop
            Start-Sleep -Seconds 2
            Write-Host '已停止本次拉起的后端进程' -ForegroundColor DarkGray
        }
        catch {
            Write-Host ('停止后端进程失败（可手动结束 PID ' + $script:BackendProc.Id + '）: ' + $_.Exception.Message) -ForegroundColor Yellow
        }
    }

    # 3) 清理临时目录（占用未释放时重试一次）
    if (-not $KeepTemp -and $script:TempRoot -and (Test-Path $script:TempRoot)) {
        $removed = $false
        foreach ($attempt in 1..3) {
            try {
                Remove-Item -Path $script:TempRoot -Recurse -Force -ErrorAction Stop
                $removed = $true
                break
            }
            catch {
                Start-Sleep -Seconds 2
            }
        }
        if (-not $removed) {
            Write-Host ('临时目录未能自动删除（文件仍被占用），请手动删除: ' + $script:TempRoot) -ForegroundColor Yellow
        }
    }
}

# ---------------------------------------------------------------- 汇总
Write-Section '汇总'
$pass  = @($script:Results | Where-Object { $_.Status -eq 'PASS' }).Count
$fail  = @($script:Results | Where-Object { $_.Status -eq 'FAIL' }).Count
$warn  = @($script:Results | Where-Object { $_.Status -eq 'WARN' }).Count
$skip  = @($script:Results | Where-Object { $_.Status -eq 'SKIP' }).Count
$known = @($script:Results | Where-Object { $_.Status -eq 'KNOWN' }).Count

Write-Host ('PASS {0}   FAIL {1}   WARN {2}   SKIP {3}   KNOWN {4}   TOTAL {5}' -f $pass, $fail, $warn, $skip, $known, $script:Results.Count) -ForegroundColor $(if ($fail -gt 0) { 'Red' } else { 'Green' })
Write-Host ''
Write-Host '状态说明: PASS=通过  FAIL=回归(需修)  WARN=需人工确认  SKIP=前置缺失  KNOWN=已登记缺陷复现(不计入退出码)' -ForegroundColor DarkGray

if ($fail -gt 0) {
    Write-Host ''
    Write-Host '失败项:' -ForegroundColor Red
    $script:Results | Where-Object { $_.Status -eq 'FAIL' } | ForEach-Object {
        Write-Host ('  ' + $_.Id + ' ' + $_.Name + ' -> ' + $_.Evidence) -ForegroundColor Red
    }
}
if ($known -gt 0) {
    Write-Host ''
    Write-Host '已登记缺陷（复现，非本次引入）:' -ForegroundColor Magenta
    $script:Results | Where-Object { $_.Status -eq 'KNOWN' } | ForEach-Object {
        Write-Host ('  ' + $_.Id + ' ' + $_.Name + ' -> ' + $_.Evidence) -ForegroundColor Magenta
    }
}

Write-Host ''
Write-Host ('详细文档: docs/verification/core-chain-and-smoke.md') -ForegroundColor DarkGray
Write-Host ''

if ($fail -gt 0) { exit 1 } else { exit 0 }
