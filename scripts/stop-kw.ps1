<#
.SYNOPSIS
  一键停止 KnowledgeWeaver 本地服务（FastAPI / Streamlit UI / MQ Bridge），可选停止 KW Docker 容器。
.EXAMPLE
  D:\agent\KnowledgeWeaver\scripts\stop-kw.ps1
  D:\agent\KnowledgeWeaver\scripts\stop-kw.ps1 -StopContainers
#>
[CmdletBinding()]
param(
    [string]$KwSourceDir = "",
    [switch]$StopContainers,
    [switch]$ForceAll
)

$ErrorActionPreference = "Stop"
$KwHome = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PidFile = Join-Path $KwHome ".kw\run\kw-pids.json"

Write-Host "=== 停止 KnowledgeWeaver (KW) ===" -ForegroundColor Cyan

function Stop-ProcessTree {
    param([int]$ParentPid)
    $children = Get-CimInstance Win32_Process -Filter "ParentProcessId=$ParentPid" -ErrorAction SilentlyContinue
    foreach ($child in $children) {
        Stop-ProcessTree -ParentPid $child.ProcessId
    }
    $proc = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
    if ($proc) {
        Stop-Process -Id $ParentPid -Force -ErrorAction SilentlyContinue
    }
}

$savedSourceDir = ""
if (Test-Path $PidFile) {
    try {
        $pids = Get-Content $PidFile -Raw | ConvertFrom-Json
        if ($pids.kw_source_dir) { $savedSourceDir = $pids.kw_source_dir }
        foreach ($entry in @(
            @{ Name = "mq-bridge"; Pid = $pids.bridge_pid },
            @{ Name = "ui";        Pid = $pids.ui_pid },
            @{ Name = "api";       Pid = $pids.api_pid }
        )) {
            if ($entry.Pid) {
                $proc = Get-Process -Id $entry.Pid -ErrorAction SilentlyContinue
                if ($proc) {
                    Stop-ProcessTree -ParentPid $entry.Pid
                    Write-Host "[KW] 已停止 $($entry.Name) (PID=$($entry.Pid))。"
                }
            }
        }
    } catch {
        Write-Warning "[KW] 读取 PID 文件失败: $_"
    }
    Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue
}

if ($ForceAll) {
    $strays = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match "knowledge_weaver\.cli" -or $_.CommandLine -match "knowledge_weaver\\ui\\app\.py" }
    foreach ($s in $strays) {
        Stop-ProcessTree -ParentPid $s.ProcessId
        Write-Host "[KW] 已强制清理残留进程 PID=$($s.ProcessId)。"
    }
}

if ($StopContainers) {
    docker info 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[KW] 正在停止 KW Docker 容器 (postgres, qdrant)..." -ForegroundColor Cyan
        $composeFile = @(
            $(if ($KwSourceDir) { Join-Path $KwSourceDir "infra\compose.yaml" }),
            $(if ($savedSourceDir) { Join-Path $savedSourceDir "infra\compose.yaml" }),
            (Join-Path $KwHome "infra\compose.yaml"),
            "D:\codex\1190\KnowledgeWeaver\infra\compose.yaml"
        ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

        $composeEnvFile = @(
            (Join-Path $KwHome ".kw\secrets\compose.env"),
            "D:\codex\1190\KnowledgeWeaver\.kw\secrets\compose.env"
        ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

        if ($composeFile -and $composeEnvFile) {
            & docker compose --env-file $composeEnvFile -f $composeFile stop
        } else {
            & docker stop knowledgeweaver-postgres-1 knowledgeweaver-qdrant-1 2>$null | Out-Null
        }
    } else {
        Write-Host "[KW] Docker 引擎未运行，跳过容器停止。" -ForegroundColor Yellow
    }
}

Write-Host "[KW] KnowledgeWeaver 停止操作完成。" -ForegroundColor Green
