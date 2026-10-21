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
    if (-not $page.Contains('budgetFields') -or -not $page.Contains('storageSetup') -or -not $page.Contains('exportDraft') -or -not $page.Contains('request_id')) { throw 'EXE lacks budget/storage/receipt UI' }
    if (-not $page.Contains('historyQuery') -or -not $page.Contains('historyStatus') -or -not $page.Contains('changeHistoryPage')) { throw 'EXE lacks paged history UI' }
    if (-not $page.Contains('candidateHistory') -or -not $page.Contains('value="ollama"')) { throw 'EXE lacks candidate history/native Ollama UI' }
    if (-not $page.Contains('savingsDashboard') -or -not $page.Contains('taskProgress') -or -not $page.Contains('progress-spinner')) { throw 'EXE lacks savings/progress UI' }
    if (-not $page.Contains('collaborationMode') -or -not $page.Contains('stepBoard') -or -not $page.Contains('quickComplex')) { throw 'EXE lacks collaboration UI' }
    $tfPreviewBody = [Text.Encoding]::UTF8.GetBytes((@{task='统计字符数'; content='abc'; collaboration_mode='local_only'} | ConvertTo-Json))
    $tfDispatch = Invoke-RestMethod "$tfBase/api/tasks/preview" -Method Post -Headers $tfHeaders -ContentType 'application/json; charset=utf-8' -Body $tfPreviewBody -TimeoutSec 5
    if (-not $tfDispatch.ready -or -not $tfDispatch.deterministic -or $tfDispatch.routes.Count -ne 1 -or $tfDispatch.routes[0].location -ne 'local') { throw 'Packaged deterministic dispatch failed' }
    $tfMissingLocalBody = [Text.Encoding]::UTF8.GetBytes((@{task='分析资料并比较方案'; content='test'; collaboration_mode='local_only'} | ConvertTo-Json))
    $tfMissingLocal = Invoke-RestMethod "$tfBase/api/tasks/preview" -Method Post -Headers $tfHeaders -ContentType 'application/json; charset=utf-8' -Body $tfMissingLocalBody -TimeoutSec 5
    if ($tfMissingLocal.ready -or $tfMissingLocal.issues.Count -eq 0) { throw 'Packaged local-only preview failed to reject missing connection' }
    $tfDependencySource = "REFUND_DAYS = 14`n`n" + ("# unrelated`n" * 300) + "def calculate_refund():`n    return REFUND_DAYS`n"
    $tfDependencyBody = @{task='calculate_refund 的返回值是多少？只回答数字。'; content=$tfDependencySource} | ConvertTo-Json
    $tfDependency = Invoke-RestMethod "$tfBase/api/run" -Method Post -Headers $tfHeaders -ContentType 'application/json; charset=utf-8' -Body $tfDependencyBody -TimeoutSec 5
    if (-not $tfDependency.compression_applied -or -not $tfDependency.result.Contains('REFUND_DAYS = 14') -or -not $tfDependency.result.Contains('return REFUND_DAYS')) { throw 'Packaged Python extraction lost its global dependency' }
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
    $tfReused = Invoke-RestMethod "$tfBase/api/parse" -Method Post -Headers $tfHeaders -Form @{file=(Get-Item -LiteralPath $tfImagePath)} -TimeoutSec 10
    if (-not $tfReused.cache_reused -or $tfReused.text -ne $tfRecognized.text) { throw 'Packaged OCR cache failed' }
    $savedConnection = @{id='smoke-profile'; name='Smoke fixture'; base_url='http://127.0.0.1:9/v1'; protocol='responses'; api_key='smoke-connection-key'; models=@('smoke-model')} | ConvertTo-Json
    $saved = Invoke-RestMethod "$tfBase/api/config/connection" -Method Post -Headers $tfHeaders -ContentType 'application/json' -Body $savedConnection -TimeoutSec 5
    if (($saved | ConvertTo-Json).Contains('smoke-connection-key') -or -not $saved.key_configured) { throw 'Saved connection exposed or lost its key' }
    $tfTaskBody = [Text.Encoding]::UTF8.GetBytes((@{request_id='exe-smoke-request-0001'; task='统计CSV的行数和平均值'; content="name,value`na,2`nb,4`n"} | ConvertTo-Json))
    $run = Invoke-RestMethod "$tfBase/api/tasks/start" -Method Post -Headers $tfHeaders -ContentType 'application/json; charset=utf-8' -Body $tfTaskBody -TimeoutSec 5
    for ($i = 0; $i -lt 50; $i++) {
        $result = Invoke-RestMethod "$tfBase/api/tasks/$($run.id)" -Headers $tfHeaders -TimeoutSec 5
        if ($result.status -ne 'running') { break }
        Start-Sleep -Milliseconds 100
    }
    if ($result.status -ne 'completed' -or $result.metrics.model_calls -ne 0 -or $result.acceptance.status -ne 'passed') { throw ('Deterministic task failed: ' + ($result | Select-Object status,error,acceptance,metrics | ConvertTo-Json -Depth 4 -Compress)) }
    $tfPartialBody = [Text.Encoding]::UTF8.GetBytes((@{task='统计表格总和'; content="id,value`nA,10`nB,=5*4 [无缓存值]`n"} | ConvertTo-Json))
    $tfPartial = Invoke-RestMethod "$tfBase/api/tasks/start" -Method Post -Headers $tfHeaders -ContentType 'application/json; charset=utf-8' -Body $tfPartialBody -TimeoutSec 5
    for ($i = 0; $i -lt 50; $i++) {
        $tfPartialResult = Invoke-RestMethod "$tfBase/api/tasks/$($tfPartial.id)" -Headers $tfHeaders -TimeoutSec 5
        if ($tfPartialResult.status -ne 'running') { break }
        Start-Sleep -Milliseconds 100
    }
    if ($tfPartialResult.status -ne 'needs_review' -or $tfPartialResult.acceptance.status -ne 'failed' -or $tfPartialResult.metrics.model_calls -ne 0) { throw 'Packaged partial statistics incorrectly accepted' }
    $tfLookupBody = [Text.Encoding]::UTF8.GetBytes((@{task='CSV表格中 "K 17" 的 free_stock 值是多少？只回答数字。'; content="code,free_stock`nK170,80`n`"K 17`",43`n"} | ConvertTo-Json))
    $tfLookup = Invoke-RestMethod "$tfBase/api/tasks/start" -Method Post -Headers $tfHeaders -ContentType 'application/json; charset=utf-8' -Body $tfLookupBody -TimeoutSec 5
    for ($i = 0; $i -lt 50; $i++) {
        $tfLookupResult = Invoke-RestMethod "$tfBase/api/tasks/$($tfLookup.id)" -Headers $tfHeaders -TimeoutSec 5
        if ($tfLookupResult.status -ne 'running') { break }
        Start-Sleep -Milliseconds 100
    }
    if ($tfLookupResult.status -ne 'completed' -or $tfLookupResult.result -ne '43' -or $tfLookupResult.metrics.model_calls -ne 0) { throw 'Packaged exact lookup failed' }
    $tfNativeBody = @{id='smoke-native'; name='Native fixture'; base_url='http://127.0.0.1:9/v1'; protocol='ollama'; models=@('smoke-model')} | ConvertTo-Json
    $tfNative = Invoke-RestMethod "$tfBase/api/config/connection" -Method Post -Headers $tfHeaders -ContentType 'application/json' -Body $tfNativeBody -TimeoutSec 5
    if ($tfNative.protocol -ne 'ollama' -or -not $tfNative.local) { throw 'Packaged native Ollama protocol failed' }
    $tfHistory = Invoke-RestMethod "$tfBase/api/tasks?limit=1&status=needs_review" -Headers $tfHeaders -TimeoutSec 5
    if ($tfHistory.total -ne 1 -or $tfHistory.runs[0].id -ne $tfPartial.id -or $tfHistory.has_more) { throw 'Packaged history pagination/filter failed' }
    $tfDuplicate = Invoke-RestMethod "$tfBase/api/tasks/start" -Method Post -Headers $tfHeaders -ContentType 'application/json; charset=utf-8' -Body $tfTaskBody -TimeoutSec 5
    if ($tfDuplicate.id -ne $run.id -or -not $tfDuplicate.reused) { throw 'Packaged task receipt failed' }
    $tfSavings = Invoke-RestMethod "$tfBase/api/config/savings" -Headers $tfHeaders -TimeoutSec 5
    if ($tfSavings.records -ne 3 -or $tfSavings.counted_records -ne 3 -or $tfSavings.saved_input_tokens -le 0 -or $tfSavings.price.model -ne 'gpt-6.1-sol') { throw 'Packaged savings or receipt deduplication failed' }
    $tfDraft = Invoke-RestMethod "$tfBase/api/tasks/eval-draft" -Method Post -Headers $tfHeaders -ContentType 'application/json' -Body (@{id=$run.id} | ConvertTo-Json) -TimeoutSec 5
    if ($tfDraft.label_status -ne 'draft' -or $tfDraft.expected_answer -or -not $tfDraft.content.Contains('a,2')) { throw 'Packaged evaluation draft failed' }
    Invoke-RestMethod "$tfBase/api/config/settings" -Method Post -Headers $tfHeaders -ContentType 'application/json' -Body '{"max_task_tokens":48000,"output_limits":{"planner":1024},"collaboration_mode":"local_only"}' -TimeoutSec 5 | Out-Null
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
    $tfRestoredSavings = Invoke-RestMethod "$tfBase/api/config/savings" -Headers $tfHeaders -TimeoutSec 5
    if ($tfRestoredSavings.records -ne $tfSavings.records -or $tfRestoredSavings.saved_input_usd -ne $tfSavings.saved_input_usd) { throw 'Savings lost or duplicated after restart' }
    if ($config.max_task_tokens -ne 48000 -or $config.output_limits.planner -ne 1024) { throw 'Budget settings lost after restart' }
    if ($config.collaboration_mode -ne 'local_only') { throw 'Collaboration policy lost after restart' }
    $tfReceipt = Invoke-RestMethod "$tfBase/api/tasks/request/exe-smoke-request-0001" -Headers $tfHeaders -TimeoutSec 5
    if ($tfReceipt.id -ne $run.id) { throw 'Receipt lost after restart' }
    $tfStorage = Invoke-RestMethod "$tfBase/api/config/storage" -Headers $tfHeaders -TimeoutSec 5
    if ($tfStorage.cache.parsed_files -lt 1 -or $tfStorage.cache.content_bytes -le 0) { throw 'Cache capacity unavailable' }
    Invoke-RestMethod "$tfBase/api/tasks/delete" -Method Post -Headers $tfHeaders -ContentType 'application/json' -Body (@{id=$run.id} | ConvertTo-Json) -TimeoutSec 5 | Out-Null
    $tfRetainedSavings = Invoke-RestMethod "$tfBase/api/config/savings" -Headers $tfHeaders -TimeoutSec 5
    if ($tfRetainedSavings.saved_input_usd -ne $tfSavings.saved_input_usd -or $tfRetainedSavings.records -ne 3) { throw 'History cleanup erased savings' }
    Invoke-RestMethod "$tfBase/api/config/storage/clear" -Method Post -Headers $tfHeaders -ContentType 'application/json' -Body '{}' -TimeoutSec 5 | Out-Null
    Invoke-RestMethod "$tfBase/api/shutdown" -Method Post -Headers $tfHeaders -ContentType 'application/json' -Body '{}' -TimeoutSec 5 | Out-Null
    if (-not $tfProcess.WaitForExit(10000)) { throw 'Restarted EXE did not exit' }
    Write-Output 'EXE smoke: PASS (startup, Python dependencies, protected keys, OCR cache, partial statistics, history filters, exact lookup, native Ollama, candidate UI, savings/progress UI, durable savings, zero-call task, receipts, budgets, draft export, storage, restart, shutdown)'
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
