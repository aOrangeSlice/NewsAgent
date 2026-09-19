[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [Parameter(Mandatory = $true)]
    [string]$ConfirmProjectId,
    [string]$Region = "asia-northeast1",
    [string]$BillingAccountId = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    throw "Google Cloud CLI is not installed."
}
if ($ConfirmProjectId -cne $ProjectId) {
    throw "ConfirmProjectId must exactly match ProjectId."
}
if (-not $Force) {
    throw "This deletes NewsAgent cloud resources and versioned state. Re-run with -Force."
}

$BucketName = "$ProjectId-newsagent-state"
& gcloud config set project $ProjectId | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Unable to select project $ProjectId" }

& gcloud scheduler jobs delete newsagent-daily-0800 --location=$Region --quiet
& gcloud run jobs delete newsagent-daily --region=$Region --quiet
& gcloud logging metrics delete newsagent_job_failures --quiet
$alertPolicies = & gcloud alpha monitoring policies list `
    --filter='displayName="NewsAgent Cloud Run job failures"' `
    --format="value(name)"
if ($LASTEXITCODE -eq 0) {
    foreach ($policy in $alertPolicies) {
        if ($policy) { & gcloud alpha monitoring policies delete $policy --quiet }
    }
}
& gcloud storage rm --recursive --all-versions "gs://$BucketName/**"
& gcloud storage buckets delete "gs://$BucketName" --quiet
& gcloud secrets delete newsagent-smtp-password --quiet
& gcloud artifacts repositories delete newsagent --location=$Region --quiet
& gcloud iam service-accounts delete "newsagent-job@$ProjectId.iam.gserviceaccount.com" --quiet
& gcloud iam service-accounts delete "newsagent-scheduler@$ProjectId.iam.gserviceaccount.com" --quiet

if ($BillingAccountId) {
    $budgets = & gcloud billing budgets list `
        "--billing-account=$BillingAccountId" `
        --filter='displayName="NewsAgent monthly budget"' `
        --format="value(name)"
    if ($LASTEXITCODE -eq 0) {
        foreach ($budget in $budgets) {
            if ($budget) { & gcloud billing budgets delete $budget --quiet }
        }
    }
}

Write-Host "Requested deletion of the named NewsAgent resources. The GCP project itself was not deleted."
