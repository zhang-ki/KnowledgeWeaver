$ErrorActionPreference = 'Stop'
$observedLocal = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz')
$observedUtc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ssZ')
Write-Output ('OBSERVED_LOCAL=' + $observedLocal)
Write-Output ('OBSERVED_UTC=' + $observedUtc)

$apiKey = [Environment]::GetEnvironmentVariable('KW_QDRANT_API_KEY')
if ([string]::IsNullOrWhiteSpace($apiKey)) {
    Write-Output 'NOT_RUN: KW_QDRANT_API_KEY is missing; no key value was read or printed.'
    exit 2
}

$portText = [Environment]::GetEnvironmentVariable('KW_QDRANT_PORT')
$port = 56333
if (-not [string]::IsNullOrWhiteSpace($portText)) {
    $parsedPort = 0
    if (-not [int]::TryParse($portText, [ref]$parsedPort) -or $parsedPort -lt 1 -or $parsedPort -gt 65535) {
        Write-Output 'FAIL: KW_QDRANT_PORT must be a TCP port number.'
        exit 2
    }
    $port = $parsedPort
}

$base = 'http://127.0.0.1:' + $port
$headers = @{ 'api-key' = $apiKey }
$runId = [guid]::NewGuid().ToString('N')
$collection = 'test_' + $runId
$pointId = [guid]::NewGuid().ToString()
$created = $false
$success = $false
$cleanupFailed = $false
$deadline = [DateTimeOffset]::UtcNow.AddSeconds(60)

try {
    $ready = $false
    do {
        try {
            $null = Invoke-RestMethod -Method Get -Uri ($base + '/readyz') -Headers $headers -TimeoutSec 3
            $ready = $true
        }
        catch {
            Start-Sleep -Milliseconds 1000
        }
    } while (-not $ready -and [DateTimeOffset]::UtcNow -lt $deadline)

    if (-not $ready) {
        throw 'Qdrant did not become ready within 60 seconds.'
    }

    $createBody = @{ vectors = @{ size = 3; distance = 'Cosine' } } | ConvertTo-Json -Depth 4
    $null = Invoke-RestMethod -Method Put -Uri ($base + '/collections/' + $collection) -Headers $headers -ContentType 'application/json' -Body $createBody -TimeoutSec 10
    $created = $true

    $upsertBody = @{
        points = @(
            @{
                id = $pointId
                vector = @(1.0, 0.0, 0.0)
                payload = @{ run_id = $runId }
            }
        )
    } | ConvertTo-Json -Depth 6
    $null = Invoke-RestMethod -Method Put -Uri ($base + '/collections/' + $collection + '/points?wait=true') -Headers $headers -ContentType 'application/json' -Body $upsertBody -TimeoutSec 10

    $scrollBody = @{
        limit = 10
        with_payload = $true
        with_vector = $false
        filter = @{ must = @(@{ key = 'run_id'; match = @{ value = $runId } }) }
    } | ConvertTo-Json -Depth 6
    $scroll = Invoke-RestMethod -Method Post -Uri ($base + '/collections/' + $collection + '/points/scroll') -Headers $headers -ContentType 'application/json' -Body $scrollBody -TimeoutSec 10
    $found = @($scroll.result.points | Where-Object { $_.id -eq $pointId -and $_.payload.run_id -eq $runId }).Count -gt 0
    if (-not $found) {
        throw 'The isolated point was not returned by the query.'
    }
    $success = $true
}
catch {
    Write-Output 'FAIL: Qdrant readiness or isolated collection write/query failed; response details suppressed.'
}
finally {
    if ($created) {
        try {
            $null = Invoke-RestMethod -Method Delete -Uri ($base + '/collections/' + $collection) -Headers $headers -TimeoutSec 10
        }
        catch {
            $cleanupFailed = $true
            Write-Output 'FAIL: isolated test collection cleanup failed.'
        }
    }
}

if ($success -and -not $cleanupFailed) {
    Write-Output 'PASS: /readyz, isolated collection create, point write, filtered scroll query, and collection delete.'
    exit 0
}

exit 1
