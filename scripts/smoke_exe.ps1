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
foreach ($name in @('TOKENFLOW_PORT','TOKENFLOW_DB_PATH','TOKENFLOW_CONFIG_DB','TOKENFLOW_OPEN_BROWSER')) {
    $tfOriginal[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$env:TOKENFLOW_PORT = [string]$tfPort
$env:TOKENFLOW_DB_PATH = $tfDb
$env:TOKENFLOW_CONFIG_DB = Join-Path $tfTemporary 'connections.db'
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
    if (-not $page.Contains('orchestrateTask') -or -not $page.Contains('connectionSetup')) { throw 'EXE lacks orchestration UI' }
    if (-not $page.Contains('cancelRunningTask') -or -not $page.Contains('viewedRun')) { throw 'EXE lacks cancellation/history recovery UI' }
    $ocr = Invoke-RestMethod "$tfBase/api/ocr" -TimeoutSec 5
    if (-not $ocr.available -or -not $ocr.offline -or $ocr.device -ne 'CPU') { throw 'Packaged OCR unavailable' }
    Add-Type -AssemblyName System.Drawing
    $tfImagePath = Join-Path $tfTemporary 'ocr.png'
    $tfBitmap = [Drawing.Bitmap]::new(800,180)
    $tfGraphics = [Drawing.Graphics]::FromImage($tfBitmap)
    $tfFont = [Drawing.Font]::new('Arial',42)
    try {
        $tfGraphics.Clear([Drawing.Color]::White)
        $tfGraphics.DrawString('LOCAL OCR 42',$tfFont,[Drawing.Brushes]::Black,20,40)
        $tfBitmap.Save($tfImagePath,[Drawing.Imaging.ImageFormat]::Png)
    } finally { $tfFont.Dispose(); $tfGraphics.Dispose(); $tfBitmap.Dispose() }
    $tfRecognized = Invoke-RestMethod "$tfBase/api/parse" -Method Post -Headers $tfHeaders -Form @{file=(Get-Item -LiteralPath $tfImagePath)} -TimeoutSec 40
    if ($tfRecognized.method -ne 'local_ocr' -or -not $tfRecognized.text.Contains('42')) { throw 'Packaged OCR recognition failed' }
    $savedConnection = @{id='smoke-profile'; name='Smoke fixture'; base_url='http://127.0.0.1:9/v1'; protocol='responses'; api_key='smoke-connection-key'; models=@('smoke-model')} | ConvertTo-Json
    $saved = Invoke-RestMethod "$tfBase/api/config/connection" -Method Post -Headers $tfHeaders -ContentType 'application/json' -Body $savedConnection -TimeoutSec 5
    if (($saved | ConvertTo-Json).Contains('smoke-connection-key') -or -not $saved.key_configured) { throw 'Saved connection exposed or lost its key' }
    $tfTaskBody = [Text.Encoding]::UTF8.GetBytes((@{task='统计CSV的行数和平均值'; content="name,value`na,2`nb,4`n"} | ConvertTo-Json))
    $run = Invoke-RestMethod "$tfBase/api/tasks/start" -Method Post -Headers $tfHeaders -ContentType 'application/json; charset=utf-8' -Body $tfTaskBody -TimeoutSec 5
    for ($i = 0; $i -lt 50; $i++) {
        $result = Invoke-RestMethod "$tfBase/api/tasks/$($run.id)" -Headers $tfHeaders -TimeoutSec 5
        if ($result.status -ne 'running') { break }
        Start-Sleep -Milliseconds 100
    }
    if ($result.status -ne 'completed' -or $result.metrics.model_calls -ne 0 -or $result.acceptance.status -ne 'passed') { throw ('Deterministic task failed: ' + ($result | Select-Object status,error,acceptance,metrics | ConvertTo-Json -Depth 4 -Compress)) }
    Invoke-RestMethod "$tfBase/api/shutdown" -Method Post -Headers $tfHeaders -ContentType 'application/json' -Body '{}' -TimeoutSec 5 | Out-Null
    if (-not $tfProcess.WaitForExit(10000)) { throw 'EXE did not exit after shutdown' }
    $tfProcess = Start-Process -FilePath $tfPath -WindowStyle Hidden -PassThru
    $tfReady = $false
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Milliseconds 500
        try { if ((Invoke-RestMethod "$tfBase/health" -TimeoutSec 1).status -eq 'ok') { $tfReady = $true; break } } catch {}
    }
    if (-not $tfReady) { throw 'EXE failed to restart' }
    $session = Invoke-RestMethod "$tfBase/api/session" -TimeoutSec 5
    $tfHeaders = @{'X-TokenFlow-Token'=$session.token}
    $config = Invoke-RestMethod "$tfBase/api/config" -Headers $tfHeaders -TimeoutSec 5
    $reopened = @($config.connections | Where-Object id -eq 'smoke-profile')
    if ($reopened.Count -ne 1 -or -not $reopened[0].key_configured -or ($config | ConvertTo-Json -Depth 8).Contains('smoke-connection-key')) { throw 'Restart persistence or key protection failed' }
    $restoredRun = Invoke-RestMethod "$tfBase/api/tasks/$($run.id)" -Headers $tfHeaders -TimeoutSec 5
    if ($restoredRun.status -ne 'completed') { throw 'Task record lost after restart' }
    Invoke-RestMethod "$tfBase/api/shutdown" -Method Post -Headers $tfHeaders -ContentType 'application/json' -Body '{}' -TimeoutSec 5 | Out-Null
    if (-not $tfProcess.WaitForExit(10000)) { throw 'Restarted EXE did not exit' }
    Write-Output 'EXE smoke: PASS (startup, policy, protected keys, OCR, models, zero-call task, restart persistence, UI, shutdown)'
} finally {
    if ($tfProcess -and -not $tfProcess.HasExited) {
        try { Invoke-RestMethod "$tfBase/api/shutdown" -Method Post -Headers $tfHeaders -ContentType 'application/json' -Body '{}' -TimeoutSec 2 | Out-Null } catch {}
        if (-not $tfProcess.WaitForExit(3000)) { Stop-Process -Id $tfProcess.Id -Force }
    }
    foreach ($name in $tfOriginal.Keys) { [Environment]::SetEnvironmentVariable($name, $tfOriginal[$name], 'Process') }
    $tfResolvedTemporary = [IO.Path]::GetFullPath($tfTemporary)
    $tfTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\','/')
    if ([IO.Path]::GetDirectoryName($tfResolvedTemporary) -ne $tfTempRoot -or -not [IO.Path]::GetFileName($tfResolvedTemporary).StartsWith('tokenflow-smoke-')) { throw 'Unsafe smoke cleanup path' }
    Remove-Item -LiteralPath $tfResolvedTemporary -Recurse -Force
}
