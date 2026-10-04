param([string]$Executable = (Join-Path $PSScriptRoot '../dist/TokenFlow.exe'))
$ErrorActionPreference = 'Stop'
$tfPath = (Resolve-Path -LiteralPath $Executable).Path
$tfTemporary = Join-Path ([IO.Path]::GetTempPath()) ('tokenflow-smoke-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tfTemporary | Out-Null
$tfDb = Join-Path $tfTemporary 'unused.db'
$tfListener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$tfListener.Start()
$tfPort = ([Net.IPEndPoint]$tfListener.LocalEndpoint).Port
$tfListener.Stop()
$tfOriginal = @{}
foreach ($name in @('TOKENFLOW_PORT','TOKENFLOW_DB_PATH','TOKENFLOW_OPEN_BROWSER')) {
    $tfOriginal[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$env:TOKENFLOW_PORT = [string]$tfPort
$env:TOKENFLOW_DB_PATH = $tfDb
$env:TOKENFLOW_OPEN_BROWSER = '0'
$tfProcess = $null
$tfHeaders = $null
$tfBase = "http://127.0.0.1:$tfPort"
try {
    $tfProcess = Start-Process -FilePath $tfPath -WindowStyle Hidden -PassThru
    $tfReady = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        try {
            $health = Invoke-RestMethod "$tfBase/health" -TimeoutSec 1
            if ($health.status -eq 'ok') { $tfReady = $true; break }
        } catch {}
    }
    if (-not $tfReady) { throw 'EXE did not start the service' }
    $session = Invoke-RestMethod "$tfBase/api/session" -TimeoutSec 5
    $tfHeaders = @{'X-TokenFlow-Token'=$session.token}
    $body = @{url='http://127.0.0.1:20128/v1'; api_key='smoke-dummy-key'; allowed_models=@('smoke-model')} | ConvertTo-Json
    $status = Invoke-RestMethod "$tfBase/api/omniroute/config" -Method Post -Headers $tfHeaders -ContentType 'application/json' -Body $body -TimeoutSec 5
    if (($status | ConvertTo-Json).Contains('smoke-dummy-key')) { throw 'Key appeared in a response' }
    $preview = Invoke-RestMethod "$tfBase/api/omniroute/preview" -Method Post -Headers $tfHeaders -ContentType 'application/json' -Body '{"model":"smoke-model"}' -TimeoutSec 5
    if ($preview.model -ne 'smoke-model' -or $preview.cost -ne 'unknown') { throw 'Unexpected model preview' }
    foreach ($model in @('auto','blocked-model')) {
        $rejected = $false
        try {
            Invoke-RestMethod "$tfBase/api/omniroute/preview" -Method Post -Headers $tfHeaders -ContentType 'application/json' -Body (@{model=$model} | ConvertTo-Json) -TimeoutSec 5 | Out-Null
        } catch {
            if ([int]$_.Exception.Response.StatusCode -eq 503) { $rejected = $true } else { throw }
        }
        if (-not $rejected) { throw 'Unexpected model acceptance' }
    }
    Invoke-RestMethod "$tfBase/api/store" -TimeoutSec 5 | Out-Null
    if (Test-Path -LiteralPath $tfDb) { throw 'Status read created a database' }
    $page = (Invoke-WebRequest "$tfBase/" -TimeoutSec 5 -UseBasicParsing).Content
    if (-not $page.Contains('omAllow') -or -not $page.Contains('/api/omniroute/preview')) { throw 'EXE contains stale UI' }
    Invoke-RestMethod "$tfBase/api/shutdown" -Method Post -Headers $tfHeaders -ContentType 'application/json' -Body '{}' -TimeoutSec 5 | Out-Null
    if (-not $tfProcess.WaitForExit(10000)) { throw 'EXE did not exit after shutdown' }
    Write-Output 'EXE smoke: PASS (startup, policy, preview, Key protection, read-only status, UI, shutdown)'
} finally {
    if ($tfProcess -and -not $tfProcess.HasExited) {
        try { Invoke-RestMethod "$tfBase/api/shutdown" -Method Post -Headers $tfHeaders -ContentType 'application/json' -Body '{}' -TimeoutSec 2 | Out-Null } catch {}
        if (-not $tfProcess.WaitForExit(3000)) { Stop-Process -Id $tfProcess.Id -Force }
    }
    foreach ($name in $tfOriginal.Keys) { [Environment]::SetEnvironmentVariable($name, $tfOriginal[$name], 'Process') }
    if (-not (Test-Path -LiteralPath $tfDb)) { Remove-Item -LiteralPath $tfTemporary }
}
