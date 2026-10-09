param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskUrl = 'http://127.0.0.1:8910'
$taskRuntime = Join-Path $taskRoot 'runtime'
$taskPython = Join-Path (Split-Path $taskRoot -Parent) '.venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $taskPython)) { throw 'Project Python environment is missing.' }
if (!(Test-Path -LiteralPath (Join-Path $taskRoot 'dist\index.html'))) { throw 'Built UI is missing. See README.md.' }
New-Item -ItemType Directory -Path $taskRuntime -Force | Out-Null
$taskReady = $false
try {
    $taskHealth = Invoke-RestMethod -Uri "$taskUrl/api/health" -TimeoutSec 2
    if ($taskHealth.app -eq 'wuyu-model-chat') { $taskReady = $true }
} catch { }
if (!$taskReady) {
    $taskListener = Get-NetTCPConnection -LocalPort 8910 -State Listen -ErrorAction SilentlyContinue
    if ($taskListener) { throw 'Port 8910 is occupied by another application. No process was changed.' }
    $taskArguments = @('-m', 'uvicorn', 'server:app', '--host', '127.0.0.1', '--port', '8910', '--app-dir', ('"' + $taskRoot + '"'), '--no-access-log')
    $taskProcess = Start-Process -FilePath $taskPython -ArgumentList $taskArguments -WorkingDirectory $taskRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $taskRuntime 'server.out.log') -RedirectStandardError (Join-Path $taskRuntime 'server.err.log')
    Set-Content -LiteralPath (Join-Path $taskRuntime 'server.pid') -Value $taskProcess.Id -Encoding ascii
    for ($taskAttempt = 0; $taskAttempt -lt 30; $taskAttempt++) {
        Start-Sleep -Milliseconds 300
        try {
            $taskHealth = Invoke-RestMethod -Uri "$taskUrl/api/health" -TimeoutSec 1
            if ($taskHealth.app -eq 'wuyu-model-chat') { $taskReady = $true; break }
        } catch { }
    }
    if (!$taskReady) { throw 'Startup failed. Inspect model-chat/runtime/server.err.log.' }
}
Write-Host "Wuyu model chat is ready: $taskUrl"
if (!$NoBrowser) { Start-Process $taskUrl }
