$ErrorActionPreference = 'Stop'
$taskRoot = $PSScriptRoot
$taskPidFile = Join-Path $taskRoot 'runtime\server.pid'
$taskIds = @()
if (Test-Path -LiteralPath $taskPidFile) { $taskIds += [int](Get-Content -LiteralPath $taskPidFile) }
$taskListener = Get-NetTCPConnection -LocalPort 8910 -State Listen -ErrorAction SilentlyContinue
if ($taskListener) { $taskIds += $taskListener.OwningProcess }
foreach ($taskProcessId in ($taskIds | Select-Object -Unique)) {
    $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$taskProcessId" -ErrorAction SilentlyContinue
    if (!$taskProcess) { continue }
    if ($taskProcess.Name -notmatch '^python(w)?\.exe$' -or !$taskProcess.CommandLine.Contains($taskRoot) -or $taskProcess.CommandLine -notmatch 'uvicorn\s+server:app' -or $taskProcess.CommandLine -notmatch '--port\s+8910') {
        Write-Warning "Process $taskProcessId was not recognized as this chat server; left unchanged."
        continue
    }
    Stop-Process -Id $taskProcessId -ErrorAction SilentlyContinue
}
if (Test-Path -LiteralPath $taskPidFile) { Remove-Item -LiteralPath $taskPidFile }
Write-Host 'Wuyu model chat stopped. Browser conversation history is retained.'
