param(
    [string]$TaskName = "NewsAgent Daily Email",
    [string]$Time = "08:00"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$DailyScript = Join-Path $RepoRoot "scripts\newsagent_daily_email.ps1"

if (-not (Test-Path $DailyScript)) {
    throw "Daily script not found: $DailyScript"
}

$Action = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$DailyScript`""

schtasks /Create /TN $TaskName /SC DAILY /ST $Time /TR $Action /F

Write-Host "Installed scheduled task '$TaskName' at $Time."
Write-Host "Make sure NEWSAGENT_SMTP_PASSWORD is set with setx before the task runs."
