param([switch]$NoBrowser)
$ErrorActionPreference='Stop'
$wuyuRoot=$PSScriptRoot
$wuyuPython=Join-Path (Split-Path $wuyuRoot -Parent) '.venv\Scripts\python.exe'
$wuyuRuntime=Join-Path $wuyuRoot 'runtime'
New-Item -ItemType Directory -Force -Path $wuyuRuntime | Out-Null
$wuyuReady=$false
try {$wuyuReady=(Invoke-RestMethod 'http://127.0.0.1:8920/api/health' -TimeoutSec 2).app -eq 'wuyu-official'} catch {}
if (!$wuyuReady) {
 if (Get-NetTCPConnection -LocalPort 8920 -State Listen -ErrorAction SilentlyContinue) {throw 'Port 8920 is occupied. No process was changed.'}
 $wuyuArgs=@('-m','uvicorn','app_main:app','--host','127.0.0.1','--port','8920','--app-dir',('"'+$wuyuRoot+'"'),'--no-access-log')
 $wuyuProcess=Start-Process -FilePath $wuyuPython -ArgumentList $wuyuArgs -WorkingDirectory $wuyuRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $wuyuRuntime 'server.out.log') -RedirectStandardError (Join-Path $wuyuRuntime 'server.err.log')
 Set-Content -LiteralPath (Join-Path $wuyuRuntime 'server.pid') -Value $wuyuProcess.Id -Encoding ascii
 for ($wuyuAttempt=0;$wuyuAttempt -lt 30;$wuyuAttempt++) {Start-Sleep -Milliseconds 300;try {if ((Invoke-RestMethod 'http://127.0.0.1:8920/api/health' -TimeoutSec 1).app -eq 'wuyu-official') {$wuyuReady=$true;break}} catch {}}
 if (!$wuyuReady) {throw 'Startup failed. Inspect runtime/server.err.log.'}
}
Write-Host 'Wuyu AI is ready: http://127.0.0.1:8920'
if (!$NoBrowser) {Start-Process 'http://127.0.0.1:8920'}
