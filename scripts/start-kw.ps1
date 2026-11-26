<#
.SYNOPSIS
  一键启动 KnowledgeWeaver 本地全套服务（统一入口，位于 D:\agent\KnowledgeWeaver\scripts）：
  自动拉起 Docker Desktop -> KW Postgres & Qdrant 容器 -> 加载本地密钥 -> 启动 FastAPI (8000) + Streamlit UI (8501) + RabbitMQ Bridge（若 KnowSphere RabbitMQ 运行中）。
.EXAMPLE
  D:\agent\KnowledgeWeaver\scripts\start-kw.ps1
  D:\agent\KnowledgeWeaver\scripts\start-kw.ps1 -Restart
  D:\agent\KnowledgeWeaver\scripts\start-kw.ps1 -NoUi -WithMqBridge
  D:\agent\KnowledgeWeaver\scripts\start-kw.ps1 -CorpusId "02f86e70-2d2e-4142-9389-3c1ac0b9bd86"
#>
[CmdletBinding()]
param(
    [int]$ApiPort = 8000,
    [int]$UiPort = 8501,
    [string]$CorpusId = "",
    [string]$AllowedRoots = "",
    [string]$PythonExe = "",
    [string]$KwSourceDir = "",
    [string]$KnowSphereDir = "D:\agent\KnowSphere",
    [switch]$NoUi,
    [switch]$NoMqBridge,
    [switch]$WithMqBridge,
    [switch]$Restart,
    [switch]$SkipDockerLaunch,
    [int]$WaitTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"
$KwHome = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RunDir = Join-Path $KwHome ".kw\run"
$PidFile = Join-Path $RunDir "kw-pids.json"

function Read-DotEnvMap {
    param([string]$Path)
    $map = @{}
    if (-not (Test-Path $Path)) { return $map }
    foreach ($line in (Get-Content -Path $Path -Encoding UTF8)) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        $idx = $trimmed.IndexOf("=")
        if ($idx -lt 1) { continue }
        $k = $trimmed.Substring(0, $idx).Trim()
        $v = $trimmed.Substring($idx + 1).Trim()
        if (($v.StartsWith('"') -and $v.EndsWith('"')) -or ($v.StartsWith("'") -and $v.EndsWith("'"))) {
            $v = $v.Substring(1, $v.Length - 2)
        }
        $map[$k] = $v
    }
    return $map
}

function Test-TcpPort {
    param([string]$HostName = "127.0.0.1", [int]$Port, [int]$TimeoutMs = 300)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $ar = $client.BeginConnect($HostName, $Port, $null, $null)
        if ($ar.AsyncWaitHandle.WaitOne($TimeoutMs, $false) -and $client.Connected) {
            $client.EndConnect($ar)
            return $true
        }
        return $false
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Ensure-DockerReady {
    param([int]$TimeoutSec, [bool]$SkipLaunch)
    docker info 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[KW] Docker 引擎已就绪。" -ForegroundColor Green
        return
    }
    if ($SkipLaunch) {
        throw "Docker 引擎未运行，请先启动 Docker Desktop 或移除 -SkipDockerLaunch。"
    }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\DockerDesktop\Docker Desktop.exe"),
        "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    )
    $dockerExe = $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
    if (-not $dockerExe) {
        throw "未找到 Docker Desktop 可执行文件，请手动启动 Docker Desktop 后重试。"
    }
    Write-Host "[KW] 正在自动启动 Docker Desktop ..." -ForegroundColor Yellow
    Start-Process -FilePath $dockerExe
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        docker info 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "[KW] Docker 引擎已就绪。" -ForegroundColor Green
            return
        }
    }
    throw "等待 Docker 引擎就绪超时（${TimeoutSec}s）。"
}

function Find-FirstExisting {
    param([string[]]$Paths)
    foreach ($p in $Paths) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    return $null
}

Write-Host "=== 一键启动 KnowledgeWeaver (KW) ===" -ForegroundColor Cyan

# 0. 自动解析 KW 代码目录（优先使用 D:\agent\KnowledgeWeaver；若主目录尚未合并 B08 代码，自动回退到 B08 worktree）
if (-not $KwSourceDir) {
    $sourceCandidates = @(
        $KwHome,
        "D:\codex\1190\KnowledgeWeaver"
    )
    foreach ($cand in $sourceCandidates) {
        if ((Test-Path (Join-Path $cand "src\knowledge_weaver\cli.py")) -and (Test-Path (Join-Path $cand "infra\compose.yaml"))) {
            $KwSourceDir = (Resolve-Path $cand).Path
            break
        }
    }
}
if (-not $KwSourceDir -or -not (Test-Path (Join-Path $KwSourceDir "src\knowledge_weaver\cli.py"))) {
    throw "未找到包含 src\knowledge_weaver\cli.py 的 KnowledgeWeaver 代码目录，请通过 -KwSourceDir 指定。"
}
$ComposeFile = Join-Path $KwSourceDir "infra\compose.yaml"
if (-not (Test-Path $ComposeFile)) {
    throw "未找到 KW compose 文件: $ComposeFile"
}
Write-Host "[KW] 运行主目录: $KwHome"
if ($KwSourceDir -ne $KwHome) {
    Write-Host "[KW] 代码工作区: $KwSourceDir（主目录合并 B08 分支后将自动切回主目录）"
}

if ($Restart) {
    $stopScript = Join-Path $PSScriptRoot "stop-kw.ps1"
    if (Test-Path $stopScript) {
        & $stopScript -KwSourceDir $KwSourceDir
    }
}

# 1. 自动定位 Python 虚拟环境
if (-not $PythonExe) {
    $PythonExe = Find-FirstExisting @(
        (Join-Path $KwHome ".venv\Scripts\python.exe"),
        (Join-Path $KwSourceDir ".venv\Scripts\python.exe")
    )
    if (-not $PythonExe) {
        $pyCmd = Get-Command python -ErrorAction SilentlyContinue
        if ($pyCmd) { $PythonExe = $pyCmd.Source }
    }
}
if (-not $PythonExe) {
    throw "未找到可用的 Python 环境，请通过 -PythonExe 指定或先创建 .venv。"
}
Write-Host "[KW] Python: $PythonExe"

# 2. 加载 compose.env 与可选 local.env（绝不打印敏感值）
$composeEnvFile = Find-FirstExisting @(
    (Join-Path $KwHome ".kw\secrets\compose.env"),
    (Join-Path $KwSourceDir ".kw\secrets\compose.env")
)
if (-not $composeEnvFile) {
    throw "未找到 .kw\secrets\compose.env（需包含 KW_POSTGRES_PASSWORD 与 KW_QDRANT_API_KEY）。"
}
$composeEnv = Read-DotEnvMap -Path $composeEnvFile
if (-not $composeEnv["KW_POSTGRES_PASSWORD"] -or -not $composeEnv["KW_QDRANT_API_KEY"]) {
    throw "$composeEnvFile 缺少 KW_POSTGRES_PASSWORD 或 KW_QDRANT_API_KEY。"
}

$localEnvFile = Find-FirstExisting @(
    (Join-Path $KwHome ".kw\secrets\local.env"),
    (Join-Path $KwSourceDir ".kw\secrets\local.env")
)
$localEnv = if ($localEnvFile) { Read-DotEnvMap -Path $localEnvFile } else { @{} }
if ($localEnvFile) {
    Write-Host "[KW] 已加载本地扩展配置: $localEnvFile"
}

# 3. 启动 Docker Desktop 与 KW 数据容器 (Postgres 55432 + Qdrant 56333)
Ensure-DockerReady -TimeoutSec $WaitTimeoutSeconds -SkipLaunch:$SkipDockerLaunch

Write-Host "[KW] 正在启动 KW 数据容器 (postgres:55432, qdrant:56333)..." -ForegroundColor Cyan
& docker compose --env-file $composeEnvFile -f $ComposeFile up -d
if ($LASTEXITCODE -ne 0) {
    throw "启动 KW Docker 容器失败，退出码: $LASTEXITCODE"
}

$deadline = (Get-Date).AddSeconds($WaitTimeoutSeconds)
Write-Host "[KW] 等待 KW PostgreSQL (127.0.0.1:55432) 与 Qdrant (127.0.0.1:56333) 就绪..."
$dbReady = $false
$qdrantReady = $false
while ((Get-Date) -lt $deadline) {
    if (-not $dbReady) {
        & docker compose --env-file $composeEnvFile -f $ComposeFile exec -T postgres pg_isready -U kw -d knowledgeweaver 2>$null | Out-Null
        if ($LASTEXITCODE -eq 0) { $dbReady = $true }
    }
    if (-not $qdrantReady) {
        if (Test-TcpPort -Port 56333) { $qdrantReady = $true }
    }
    if ($dbReady -and $qdrantReady) { break }
    Start-Sleep -Seconds 2
}
if (-not $dbReady -or -not $qdrantReady) {
    throw "KW 数据容器在 ${WaitTimeoutSeconds}s 内未就绪（dbReady=$dbReady, qdrantReady=$qdrantReady）。"
}
Write-Host "[KW] KW PostgreSQL 与 Qdrant 已就绪。" -ForegroundColor Green

# 4. 组装 KW 运行环境变量（自动建目录，杜绝 PathSafetyError）
foreach ($kv in $localEnv.GetEnumerator()) {
    if ($kv.Value -ne "") {
        Set-Item -Path ("Env:" + $kv.Key) -Value $kv.Value
    }
}

$encodedPgPass = [System.Uri]::EscapeDataString($composeEnv["KW_POSTGRES_PASSWORD"])
$env:PYTHONPATH = Join-Path $KwSourceDir "src"
$env:KW_DATABASE_URL = "postgresql://kw:$encodedPgPass@127.0.0.1:55432/knowledgeweaver"
$env:KW_QDRANT_URL = "http://127.0.0.1:56333"
$env:KW_QDRANT_API_KEY = $composeEnv["KW_QDRANT_API_KEY"]
$env:KW_API_URL = "http://127.0.0.1:$ApiPort"

if (-not $env:KW_DATA_DIR) {
    $env:KW_DATA_DIR = Join-Path $KwHome ".kw\product_data"
}
New-Item -ItemType Directory -Path $env:KW_DATA_DIR -Force | Out-Null

if ($AllowedRoots) {
    $env:KW_ALLOWED_ROOTS = $AllowedRoots
} elseif (-not $env:KW_ALLOWED_ROOTS) {
    $env:KW_ALLOWED_ROOTS = Join-Path $KwHome "my_docs"
}
foreach ($rootItem in ($env:KW_ALLOWED_ROOTS -split [System.IO.Path]::PathSeparator)) {
    $trimmedRoot = $rootItem.Trim()
    if ($trimmedRoot) {
        New-Item -ItemType Directory -Path $trimmedRoot -Force | Out-Null
    }
}

if ($CorpusId) {
    $env:KW_CORPUS_ID = $CorpusId.Trim()
}

New-Item -ItemType Directory -Path $RunDir -Force | Out-Null

$pids = [ordered]@{
    started_at    = (Get-Date).ToString("o")
    kw_home       = $KwHome
    kw_source_dir = $KwSourceDir
    api_pid       = $null
    ui_pid        = $null
    bridge_pid    = $null
}
if (Test-Path $PidFile) {
    try {
        $prev = Get-Content $PidFile -Raw | ConvertFrom-Json
        if ($prev.api_pid -and (Get-Process -Id $prev.api_pid -ErrorAction SilentlyContinue)) { $pids.api_pid = $prev.api_pid }
        if ($prev.ui_pid -and (Get-Process -Id $prev.ui_pid -ErrorAction SilentlyContinue)) { $pids.ui_pid = $prev.ui_pid }
        if ($prev.bridge_pid -and (Get-Process -Id $prev.bridge_pid -ErrorAction SilentlyContinue)) { $pids.bridge_pid = $prev.bridge_pid }
    } catch { }
}

# 5. 启动 FastAPI (kw serve)
$apiHealthUrl = "http://127.0.0.1:$ApiPort/healthz"
$apiRunning = $false
try {
    $h = Invoke-WebRequest -Uri $apiHealthUrl -UseBasicParsing -TimeoutSec 2
    if ($h.StatusCode -eq 200) { $apiRunning = $true }
} catch { }

if ($apiRunning) {
    Write-Host "[KW] FastAPI 已在 http://127.0.0.1:$ApiPort 运行（如需重启请加 -Restart）。" -ForegroundColor Yellow
} else {
    Write-Host "[KW] 正在后台启动 FastAPI (http://127.0.0.1:$ApiPort)..." -ForegroundColor Cyan
    $apiProc = Start-Process -FilePath $PythonExe `
        -ArgumentList @("-m", "knowledge_weaver.cli", "serve", "--port", "$ApiPort") `
        -WorkingDirectory $KwSourceDir `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $RunDir "api.out.log") `
        -RedirectStandardError (Join-Path $RunDir "api.err.log") `
        -PassThru
    $pids.api_pid = $apiProc.Id

    $apiDeadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $apiDeadline) {
        if ($apiProc.HasExited) {
            $errTail = Get-Content (Join-Path $RunDir "api.err.log") -Tail 25 -ErrorAction SilentlyContinue
            throw "KW FastAPI 启动失败（进程已退出，exit=$($apiProc.ExitCode)）：`n$($errTail -join "`n")"
        }
        try {
            $h = Invoke-WebRequest -Uri $apiHealthUrl -UseBasicParsing -TimeoutSec 2
            if ($h.StatusCode -eq 200) {
                $apiRunning = $true
                break
            }
        } catch { }
        Start-Sleep -Seconds 1
    }
    if (-not $apiRunning) {
        throw "KW FastAPI 在 45s 内未响应 /healthz，请查看日志: $(Join-Path $RunDir 'api.err.log')"
    }
    Write-Host "[KW] FastAPI 已就绪 (PID=$($apiProc.Id))。" -ForegroundColor Green
}

# 6. 启动 Streamlit UI (kw ui)
if (-not $NoUi) {
    if (Test-TcpPort -Port $UiPort) {
        Write-Host "[KW] Streamlit UI 已在 http://127.0.0.1:$UiPort 运行。" -ForegroundColor Yellow
    } else {
        Write-Host "[KW] 正在后台启动 Streamlit UI (http://127.0.0.1:$UiPort)..." -ForegroundColor Cyan
        $uiProc = Start-Process -FilePath $PythonExe `
            -ArgumentList @("-m", "knowledge_weaver.cli", "ui", "--port", "$UiPort", "--api-url", "http://127.0.0.1:$ApiPort") `
            -WorkingDirectory $KwSourceDir `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $RunDir "ui.out.log") `
            -RedirectStandardError (Join-Path $RunDir "ui.err.log") `
            -PassThru
        $pids.ui_pid = $uiProc.Id

        $uiDeadline = (Get-Date).AddSeconds(30)
        $uiUp = $false
        while ((Get-Date) -lt $uiDeadline) {
            if ($uiProc.HasExited) {
                $errTail = Get-Content (Join-Path $RunDir "ui.err.log") -Tail 20 -ErrorAction SilentlyContinue
                throw "KW Streamlit UI 启动失败：`n$($errTail -join "`n")"
            }
            if (Test-TcpPort -Port $UiPort) {
                $uiUp = $true
                break
            }
            Start-Sleep -Seconds 1
        }
        if (-not $uiUp) {
            Write-Warning "[KW] Streamlit UI 端口 $UiPort 暂未就绪，请检查日志 $(Join-Path $RunDir 'ui.err.log')"
        } else {
            Write-Host "[KW] Streamlit UI 已就绪 (PID=$($uiProc.Id))。" -ForegroundColor Green
        }
    }
}

# 7. 自动连接 KnowSphere RabbitMQ Bridge（通过环境变量传入连接串，不暴露密码到命令行）
$ksEnvFile = Join-Path $KnowSphereDir ".env"
$ksEnv = Read-DotEnvMap -Path $ksEnvFile
$rmqPort = if ($ksEnv["KNOWSPHERE_RABBITMQ_PORT"]) { [int]$ksEnv["KNOWSPHERE_RABBITMQ_PORT"] } else { 5672 }
$rmqOpen = Test-TcpPort -Port $rmqPort

$shouldStartBridge = (-not $NoMqBridge) -and ($WithMqBridge -or $rmqOpen)
if ($shouldStartBridge) {
    if (-not $rmqOpen) {
        throw "指定了 -WithMqBridge，但 RabbitMQ (127.0.0.1:$rmqPort) 未监听。请先运行 KnowSphere 的 start-ks.ps1。"
    }
    $existingBridge = $pids.bridge_pid -and (Get-Process -Id $pids.bridge_pid -ErrorAction SilentlyContinue)
    if ($existingBridge) {
        Write-Host "[KW] MQ Bridge 已在运行 (PID=$($pids.bridge_pid))。" -ForegroundColor Yellow
    } else {
        $rmqUser = if ($ksEnv["KNOWSPHERE_RABBITMQ_USER"]) { $ksEnv["KNOWSPHERE_RABBITMQ_USER"] } else { "knowsphere" }
        $rmqPass = if ($ksEnv["KNOWSPHERE_RABBITMQ_PASSWORD"]) { $ksEnv["KNOWSPHERE_RABBITMQ_PASSWORD"] } else { "knowsphere_local_dev" }
        $encodedRmqUser = [System.Uri]::EscapeDataString($rmqUser)
        $encodedRmqPass = [System.Uri]::EscapeDataString($rmqPass)
        $env:KW_RABBITMQ_URL = "amqp://${encodedRmqUser}:${encodedRmqPass}@127.0.0.1:${rmqPort}/%2F"

        Write-Host "[KW] 检测到 KnowSphere RabbitMQ (127.0.0.1:$rmqPort)，正在后台启动 kw mq-bridge..." -ForegroundColor Cyan
        $bridgeProc = Start-Process -FilePath $PythonExe `
            -ArgumentList @("-m", "knowledge_weaver.cli", "mq-bridge", "--api-url", "http://127.0.0.1:$ApiPort") `
            -WorkingDirectory $KwSourceDir `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $RunDir "mq-bridge.out.log") `
            -RedirectStandardError (Join-Path $RunDir "mq-bridge.err.log") `
            -PassThru
        Start-Sleep -Seconds 2
        if ($bridgeProc.HasExited) {
            $errTail = Get-Content (Join-Path $RunDir "mq-bridge.err.log") -Tail 20 -ErrorAction SilentlyContinue
            throw "kw mq-bridge 启动失败（exit=$($bridgeProc.ExitCode)）：`n$($errTail -join "`n")"
        }
        $pids.bridge_pid = $bridgeProc.Id
        Write-Host "[KW] kw mq-bridge 已连接 RabbitMQ (PID=$($bridgeProc.Id))。" -ForegroundColor Green
    }
} elseif (-not $NoMqBridge) {
    Write-Host "[KW] 未检测到 KnowSphere RabbitMQ (127.0.0.1:$rmqPort)，已跳过 mq-bridge（启动 KS 后可再次执行本脚本自动挂载）。" -ForegroundColor DarkYellow
}

$pids | ConvertTo-Json | Set-Content -Path $PidFile -Encoding UTF8

# 8. 输出就绪摘要
Write-Host ""
Write-Host "[KW] KnowledgeWeaver 启动完成！" -ForegroundColor Green
Write-Host "  FastAPI 服务:  http://127.0.0.1:$ApiPort  (/healthz, /readyz)"
if (-not $NoUi) {
    Write-Host "  Streamlit UI:  http://127.0.0.1:$UiPort"
}
Write-Host "  数据目录:      $env:KW_DATA_DIR"
Write-Host "  允许文档目录:  $env:KW_ALLOWED_ROOTS"
Write-Host "  日志与 PID:    $RunDir"
Write-Host ""
& docker compose --env-file $composeEnvFile -f $ComposeFile ps --format "table {{.Name}}`t{{.Status}}`t{{.Ports}}"
