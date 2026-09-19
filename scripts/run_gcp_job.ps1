[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [string]$Region = "asia-northeast1",
    [string]$JobName = "newsagent-daily",
    [switch]$Async
)

$ErrorActionPreference = "Stop"
& gcloud config set project $ProjectId | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Unable to select project $ProjectId" }

$arguments = @("run", "jobs", "execute", $JobName, "--region=$Region")
if ($Async) { $arguments += "--async" } else { $arguments += "--wait" }
& gcloud @arguments
if ($LASTEXITCODE -ne 0) { throw "Cloud Run job execution failed" }
