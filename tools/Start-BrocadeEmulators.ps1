[CmdletBinding()]
param(
    [string]$BindAddress = '127.0.0.1',
    [int]$G620Port = 8620,
    [int]$G720Port = 8720,
    [int]$G620ControlPort = 18620,
    [int]$G720ControlPort = 18720,
    [switch]$Wait
)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$python = (Get-Command python -ErrorAction Stop).Source
$runtimePath = Join-Path $repositoryRoot 'emulator\.runtime'
$null = New-Item -ItemType Directory -Force -Path $runtimePath
$statePath = Join-Path $runtimePath 'processes.json'

if (Test-Path -LiteralPath $statePath) {
    $existingProcesses = @(Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json)
    $runningProcesses = foreach ($existingProcess in $existingProcesses) {
        $process = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $($existingProcess.ProcessId)" -ErrorAction SilentlyContinue
        if ($process -and $process.CommandLine -match 'emulator\.brocade_fos_emulator') {
            $existingProcess
        }
    }

    if ($runningProcesses) {
        $processIds = ($runningProcesses.ProcessId -join ', ')
        throw "Brocade emulator processes are already running (PID: $processIds). Run tools\Stop-BrocadeEmulators.ps1 first."
    }

    Remove-Item -LiteralPath $statePath -Force
}

$profiles = @(
    @{
        Name = 'g620'
        Profile = Join-Path $repositoryRoot 'emulator\profiles\ds6620b-g620\profile.json'
        Port = $G620Port
        ControlPort = $G620ControlPort
    },
    @{
        Name = 'g720'
        Profile = Join-Path $repositoryRoot 'emulator\profiles\ds7720b-g720\profile.json'
        Port = $G720Port
        ControlPort = $G720ControlPort
    }
)

$processes = foreach ($profile in $profiles) {
    $logPath = Join-Path $runtimePath "$($profile.Name).log"
    $errorPath = Join-Path $runtimePath "$($profile.Name).error.log"
    $arguments = @(
        '-m', 'emulator.brocade_fos_emulator',
        '--profile', ('"{0}"' -f $profile.Profile),
        '--host', $BindAddress,
        '--port', $profile.Port,
        '--control-port', $profile.ControlPort
    )
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $repositoryRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput $logPath -RedirectStandardError $errorPath
    [pscustomobject]@{
        Profile = $profile.Name
        ProcessId = $process.Id
        DataUrl = "http://${BindAddress}:$($profile.Port)"
        ControlUrl = "http://127.0.0.1:$($profile.ControlPort)"
        Log = $logPath
    }
}

$null = Start-Sleep -Milliseconds 750
$failedProcesses = foreach ($process in $processes) {
    if (-not (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue)) {
        $process
    }
}

if ($failedProcesses) {
    foreach ($process in $processes) {
        Stop-Process -Id $process.ProcessId -ErrorAction SilentlyContinue
    }

    $failedProfiles = ($failedProcesses.Profile -join ', ')
    throw "One or more emulator processes exited during startup ($failedProfiles). Review emulator\.runtime\*.error.log."
}

$processes | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $statePath -Encoding utf8
$processes | Format-Table -AutoSize
if ($Wait) {
    Wait-Process -Id $processes.ProcessId
}
