param([Parameter(Mandatory = $true)][string]$ProjectRoot)

$pidFile = Join-Path $ProjectRoot "data\nclt_uat.pid"
if (-not (Test-Path -LiteralPath $pidFile)) {
    Write-Host "The NCLT UAT server is not running (no UAT process file was found)."
    exit 0
}

$pidText = (Get-Content -LiteralPath $pidFile -Raw).Trim()
if ($pidText -notmatch '^\d+$') {
    Write-Host "ERROR: The UAT process file is invalid. No process was stopped."
    exit 1
}

$processId = [int]$pidText
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
if ($null -eq $process) {
    $basicProcess = Get-Process -Id $processId -ErrorAction SilentlyContinue
    if ($null -ne $basicProcess) {
        Write-Host "ERROR: The recorded process exists but its command line could not be verified."
        Write-Host "No process was stopped and the UAT process file was preserved."
        exit 1
    }
    Remove-Item -LiteralPath $pidFile -Force
    Write-Host "The recorded UAT server process is no longer running. The stale process file was removed."
    exit 0
}

$expectedLauncher = [IO.Path]::GetFullPath((Join-Path $ProjectRoot "backend\uat_launcher.py"))
if ([string]::IsNullOrWhiteSpace($process.CommandLine) -or $process.CommandLine.IndexOf($expectedLauncher, [StringComparison]::OrdinalIgnoreCase) -lt 0) {
    Write-Host "ERROR: Process $processId is not the NCLT UAT launcher. No process was stopped."
    exit 1
}

Stop-Process -Id $processId -ErrorAction Stop
Remove-Item -LiteralPath $pidFile -Force -ErrorAction SilentlyContinue
Write-Host "NCLT UAT server stopped safely (process $processId)."
