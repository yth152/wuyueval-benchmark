$ErrorActionPreference='Stop'
$wuyuPidPath=Join-Path $PSScriptRoot 'runtime\server.pid'
if (!(Test-Path -LiteralPath $wuyuPidPath)) {Write-Host 'No managed process record.';exit 0}
$wuyuProcessId=[int](Get-Content -LiteralPath $wuyuPidPath -Raw).Trim()
$wuyuProcess=Get-CimInstance Win32_Process -Filter "ProcessId=$wuyuProcessId" -ErrorAction SilentlyContinue
if ($wuyuProcess) {
 $wuyuCommand=[string]$wuyuProcess.CommandLine
 if (!$wuyuCommand.Contains($PSScriptRoot) -or !$wuyuCommand.Contains('app_main:app') -or !$wuyuCommand.Contains('8920')) {throw 'Process does not match this application. Nothing was stopped.'}
 Stop-Process -Id $wuyuProcessId
}
Remove-Item -LiteralPath $wuyuPidPath -Force
Write-Host 'Formal application stopped. Interrupted generation will be marked on the next start.'
