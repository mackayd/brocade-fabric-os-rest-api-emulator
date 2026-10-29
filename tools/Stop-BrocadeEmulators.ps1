[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$runtimePath = Join-Path $repositoryRoot 'emulator\.runtime'
$statePath = Join-Path $runtimePath 'processes.json'

if (-not (Test-Path -LiteralPath $statePath)) {
    Write-Output 'No emulator process state was found. Nothing to stop.'
    return
}

$records = @(Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json)
$stopped = foreach ($record in $records) {
    $process = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $($record.ProcessId)" -ErrorAction SilentlyContinue
    if (-not $process) {
        continue
    }

    if ($process.CommandLine -notmatch 'emulator\.brocade_fos_emulator') {
        Write-Warning "PID $($record.ProcessId) no longer belongs to a Brocade emulator process and was not stopped."
        continue
    }

    Stop-Process -Id $record.ProcessId
    [pscustomobject]@{
        Profile = $record.Profile
        ProcessId = $record.ProcessId
    }
}

Remove-Item -LiteralPath $statePath -Force

if ($stopped) {
    $stopped | Format-Table -AutoSize
} else {
    Write-Output 'No running emulator processes were found. Stale process state was removed.'
}
