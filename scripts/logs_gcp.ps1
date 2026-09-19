[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [string]$JobName = "newsagent-daily",
    [int]$Limit = 100
)

$ErrorActionPreference = "Stop"
& gcloud config set project $ProjectId | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Unable to select project $ProjectId" }
& gcloud logging read `
    "resource.type=cloud_run_job AND resource.labels.job_name=$JobName" `
    "--limit=$Limit" `
    --order=desc `
    --format="table(timestamp,severity,jsonPayload.event,jsonPayload.run_id,textPayload)"
if ($LASTEXITCODE -ne 0) { throw "Unable to read Cloud Run job logs" }
