[CmdletBinding()]
param(
    [ValidateSet('g620', 'g720')]
    [string]$Profile,
    [Parameter(Mandatory)]
    [string]$Scenario,
    [int]$G620ControlPort = 18620,
    [int]$G720ControlPort = 18720
)

$ErrorActionPreference = 'Stop'
$port = if ($Profile -eq 'g620') { $G620ControlPort } else { $G720ControlPort }
$headers = @{}
if ($env:BROCADE_EMULATOR_CONTROL_TOKEN) {
    $headers['X-Emulator-Control-Token'] = $env:BROCADE_EMULATOR_CONTROL_TOKEN
}
$body = @{ name = $Scenario } | ConvertTo-Json -Compress
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:${port}/scenario" -Headers $headers -ContentType 'application/json' -Body $body
