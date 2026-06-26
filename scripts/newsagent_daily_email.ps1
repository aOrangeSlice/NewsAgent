$ErrorActionPreference = "Stop"

$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$TokyoTimeZone = [System.TimeZoneInfo]::FindSystemTimeZoneById("Tokyo Standard Time")

function Get-TokyoNow {
    return [System.TimeZoneInfo]::ConvertTime([System.DateTimeOffset]::UtcNow, $TokyoTimeZone)
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$LogDir = Join-Path $RepoRoot "data\logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$Timestamp = (Get-TokyoNow).ToString("yyyyMMdd_HHmmss")
$LogPath = Join-Path $LogDir "daily_email_$Timestamp.log"

function Write-Utf8LogLine {
    param([string]$Line)

    Write-Output $Line
    [System.IO.File]::AppendAllText($LogPath, $Line + [Environment]::NewLine, $Utf8NoBom)
}

function Write-JsonLog {
    param(
        [string]$Level,
        [string]$Event,
        [hashtable]$Fields = @{}
    )

    $Record = [ordered]@{
        ts = (Get-TokyoNow).ToString("o")
        level = $Level
        event = $Event
    }
    foreach ($Key in $Fields.Keys) {
        $Record[$Key] = $Fields[$Key]
    }
    Write-Utf8LogLine (($Record | ConvertTo-Json -Compress -Depth 6))
}

Set-Location $RepoRoot

Write-JsonLog "INFO" "powershell_started" @{
    repo_root = [string]$RepoRoot
    log_path = [string]$LogPath
}

if (-not $env:NEWSAGENT_SMTP_PASSWORD) {
    Write-JsonLog "ERROR" "missing_environment_variable" @{
        name = "NEWSAGENT_SMTP_PASSWORD"
    }
    throw "NEWSAGENT_SMTP_PASSWORD is not set. Use setx NEWSAGENT_SMTP_PASSWORD `"your-gmail-app-password`" for scheduled tasks."
}

python -m newsagent daily --language original --email *>&1 | ForEach-Object {
    Write-Utf8LogLine ([string]$_)
}

if ($LASTEXITCODE -ne 0) {
    Write-JsonLog "ERROR" "powershell_finished" @{
        exit_code = $LASTEXITCODE
    }
    exit $LASTEXITCODE
}

Write-JsonLog "INFO" "powershell_finished" @{
    exit_code = 0
}
