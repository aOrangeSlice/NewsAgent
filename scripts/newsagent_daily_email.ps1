$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogDir = Join-Path $RepoRoot "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogPath = Join-Path $LogDir "daily_email_$Timestamp.log"

Set-Location $RepoRoot

if (-not $env:NEWSAGENT_SMTP_PASSWORD) {
    throw "NEWSAGENT_SMTP_PASSWORD is not set. Use setx NEWSAGENT_SMTP_PASSWORD `"your-gmail-app-password`" for scheduled tasks."
}

python -m newsagent daily --language zh --email *>&1 | Tee-Object -FilePath $LogPath
