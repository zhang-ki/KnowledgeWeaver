<#
.SYNOPSIS
Start KnowledgeWeaver local FastAPI service and Streamlit UI on Windows loopback.
#>
[CmdletBinding()]
param(
    [ValidateSet("all", "api", "ui", "doctor")]
    [string]$Mode = "all",
    [int]$ApiPort = 8000,
    [int]$UiPort = 8501,
    [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $PythonExe) {
    $Candidate = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $Candidate) {
        $PythonExe = $Candidate
    } else {
        $PythonExe = "python"
    }
}

$env:KW_API_URL = "http://127.0.0.1:$ApiPort"

if ($Mode -eq "doctor") {
    & $PythonExe -m knowledge_weaver.cli doctor
    exit $LASTEXITCODE
}

if ($Mode -eq "api") {
    Write-Host "Starting KnowledgeWeaver FastAPI on http://127.0.0.1:$ApiPort ..."
    & $PythonExe -m knowledge_weaver.cli serve --port $ApiPort
    exit $LASTEXITCODE
}

if ($Mode -eq "ui") {
    Write-Host "Starting KnowledgeWeaver Streamlit UI on http://127.0.0.1:$UiPort (API: $env:KW_API_URL) ..."
    & $PythonExe -m knowledge_weaver.cli ui --port $UiPort --api-url $env:KW_API_URL
    exit $LASTEXITCODE
}

Write-Host "Starting KnowledgeWeaver API (127.0.0.1:$ApiPort) and UI (127.0.0.1:$UiPort)..."
$ApiProc = Start-Process -FilePath $PythonExe -ArgumentList @("-m", "knowledge_weaver.cli", "serve", "--port", "$ApiPort") -PassThru -NoNewWindow
try {
    Start-Sleep -Seconds 2
    & $PythonExe -m knowledge_weaver.cli ui --port $UiPort --api-url $env:KW_API_URL
} finally {
    if ($ApiProc -and -not $ApiProc.HasExited) {
        Stop-Process -Id $ApiProc.Id -Force
    }
}
