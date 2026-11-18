$ErrorActionPreference = 'Stop'
$observedLocal = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz')
$observedUtc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ssZ')
Write-Output ('OBSERVED_LOCAL=' + $observedLocal)
Write-Output ('OBSERVED_UTC=' + $observedUtc)

$dsn = [Environment]::GetEnvironmentVariable('KW_POSTGRES_DSN')
if ([string]::IsNullOrWhiteSpace($dsn)) {
    Write-Output 'NOT_RUN: KW_POSTGRES_DSN is missing; no connection details were read or printed.'
    exit 2
}

$python = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Output 'NOT_RUN: python.exe is unavailable.'
    exit 2
}

$code = @'
import os
import psycopg

with psycopg.connect(os.environ["KW_POSTGRES_DSN"]) as connection:
    value = connection.execute("SELECT 1").fetchone()[0]
    if value != 1:
        raise SystemExit(3)
print("SQL_CHECK=SELECT 1 -> 1")
'@

$output = & $python.Source -c $code 2>$null
$exitCode = $LASTEXITCODE
if ($exitCode -eq 0 -and $output -contains 'SQL_CHECK=SELECT 1 -> 1') {
    Write-Output 'PASS: Windows psycopg connection returned 1 for SELECT 1.'
    exit 0
}

Write-Output ('FAIL: Windows psycopg SELECT 1 check did not pass; exit code ' + $exitCode + '; driver output suppressed.')
exit 1
