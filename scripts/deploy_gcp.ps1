[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,
    [string]$Region = "asia-northeast1",
    [string]$Schedule = "0 8 * * *",
    [string]$TimeZone = "Asia/Tokyo",
    [string]$ImageTag = "",
    [string]$EmailUsername = "",
    [string]$EmailSender = "",
    [string]$EmailRecipients = "",
    [string]$BillingAccountId = "",
    [string]$NotificationChannelId = "",
    [switch]$EnableEmail,
    [switch]$EnableScheduler
)

$ErrorActionPreference = "Stop"
$JobName = "newsagent-daily"
$Repository = "newsagent"
$BucketName = "$ProjectId-newsagent-state"
$JobServiceAccountName = "newsagent-job"
$SchedulerServiceAccountName = "newsagent-scheduler"
$SecretName = "newsagent-smtp-password"
$SchedulerName = "newsagent-daily-0800"
$FailureMetricName = "newsagent_job_failures"

function Invoke-Gcloud {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$CommandArgs)
    $output = & gcloud @CommandArgs 2>&1
    $exitCode = $LASTEXITCODE
    if ($output) { $output | ForEach-Object { Write-Host $_ } }
    if ($exitCode -ne 0) {
        throw "gcloud failed ($exitCode): gcloud $($CommandArgs -join ' ')"
    }
    return $output
}

function Test-GcloudResource {
    param([string[]]$CommandArgs)
    & gcloud @CommandArgs *> $null
    return $LASTEXITCODE -eq 0
}

if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    throw "Google Cloud CLI is not installed. Install it before running this script."
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Warning "Docker is not installed locally. Cloud Build still builds the submitted Dockerfile."
}
if ($EnableEmail -and (-not $EmailUsername -or -not $EmailRecipients)) {
    throw "-EnableEmail requires -EmailUsername and -EmailRecipients."
}
if (-not $EmailSender) { $EmailSender = $EmailUsername }
if (-not $ImageTag) { $ImageTag = "deploy-$(Get-Date -Format 'yyyyMMddHHmmss')" }

$JobServiceAccount = "$JobServiceAccountName@$ProjectId.iam.gserviceaccount.com"
$SchedulerServiceAccount = "$SchedulerServiceAccountName@$ProjectId.iam.gserviceaccount.com"
$Image = "$Region-docker.pkg.dev/$ProjectId/$Repository/newsagent:$ImageTag"

Invoke-Gcloud config set project $ProjectId | Out-Null
Invoke-Gcloud services enable `
    run.googleapis.com `
    artifactregistry.googleapis.com `
    cloudbuild.googleapis.com `
    cloudscheduler.googleapis.com `
    secretmanager.googleapis.com `
    aiplatform.googleapis.com `
    logging.googleapis.com `
    monitoring.googleapis.com `
    billingbudgets.googleapis.com | Out-Null

if (-not (Test-GcloudResource @("artifacts", "repositories", "describe", $Repository, "--location=$Region"))) {
    Invoke-Gcloud artifacts repositories create $Repository `
        --repository-format=docker `
        --location=$Region `
        --description="NewsAgent container images" | Out-Null
}

if (-not (Test-GcloudResource @("storage", "buckets", "describe", "gs://$BucketName"))) {
    Invoke-Gcloud storage buckets create "gs://$BucketName" `
        --location=$Region `
        --uniform-bucket-level-access | Out-Null
}
Invoke-Gcloud storage buckets update "gs://$BucketName" --versioning | Out-Null

$lifecycleFile = New-TemporaryFile
try {
    @'
{
  "rule": [
    {
      "action": {"type": "Delete"},
      "condition": {"daysSinceNoncurrentTime": 14, "isLive": false}
    }
  ]
}
'@ | Set-Content -LiteralPath $lifecycleFile.FullName -Encoding UTF8
    Invoke-Gcloud storage buckets update "gs://$BucketName" `
        "--lifecycle-file=$($lifecycleFile.FullName)" | Out-Null
}
finally {
    Remove-Item -LiteralPath $lifecycleFile.FullName -Force -ErrorAction SilentlyContinue
}

foreach ($serviceAccountName in @($JobServiceAccountName, $SchedulerServiceAccountName)) {
    $email = "$serviceAccountName@$ProjectId.iam.gserviceaccount.com"
    if (-not (Test-GcloudResource @("iam", "service-accounts", "describe", $email))) {
        Invoke-Gcloud iam service-accounts create $serviceAccountName `
            "--display-name=$serviceAccountName" | Out-Null
    }
}

if (-not (Test-GcloudResource @("secrets", "describe", $SecretName))) {
    Invoke-Gcloud secrets create $SecretName --replication-policy=automatic | Out-Null
}

Invoke-Gcloud projects add-iam-policy-binding $ProjectId `
    "--member=serviceAccount:$JobServiceAccount" `
    --role=roles/aiplatform.user `
    --quiet | Out-Null
Invoke-Gcloud storage buckets add-iam-policy-binding "gs://$BucketName" `
    "--member=serviceAccount:$JobServiceAccount" `
    --role=roles/storage.objectUser | Out-Null
Invoke-Gcloud secrets add-iam-policy-binding $SecretName `
    "--member=serviceAccount:$JobServiceAccount" `
    --role=roles/secretmanager.secretAccessor | Out-Null

if ($EnableEmail) {
    $secretVersion = & gcloud secrets versions list $SecretName `
        --filter="state=ENABLED" --limit=1 --format="value(name)"
    if ($LASTEXITCODE -ne 0 -or -not $secretVersion) {
        throw "No enabled SMTP secret version exists. Run scripts/set_gcp_secret.ps1 first."
    }
}

Invoke-Gcloud builds submit --tag $Image . | Out-Null

$envFile = New-TemporaryFile
try {
    $envLines = @(
        "NEWSAGENT_DATABASE_PATH: '/tmp/newsagent/data/newsagent.db'",
        "NEWSAGENT_STATE_BUCKET: '$BucketName'",
        "NEWSAGENT_STATE_PREFIX: 'newsagent'",
        "NEWSAGENT_LLM_PROVIDER: 'vertex'",
        "NEWSAGENT_LLM_MODEL: 'gemini-2.5-flash-lite'",
        "NEWSAGENT_LLM_MAX_CALLS_PER_RUN: '12'",
        "NEWSAGENT_LLM_MAX_OUTPUT_TOKENS: '4096'",
        "GOOGLE_CLOUD_PROJECT: '$ProjectId'",
        "GOOGLE_CLOUD_LOCATION: 'global'",
        "GOOGLE_GENAI_USE_VERTEXAI: 'true'",
        "NEWSAGENT_EMAIL_ENABLED: '$($EnableEmail.IsPresent.ToString().ToLowerInvariant())'",
        "NEWSAGENT_SMTP_USERNAME: '$EmailUsername'",
        "NEWSAGENT_EMAIL_SENDER: '$EmailSender'",
        "NEWSAGENT_EMAIL_RECIPIENTS: '$EmailRecipients'"
    )
    $envLines | Set-Content -LiteralPath $envFile.FullName -Encoding UTF8

    $containerArgs = "cloud-daily,--output-language,zh"
    if ($EnableEmail) { $containerArgs += ",--email" }
    $deployArgs = @(
        "run", "jobs", "deploy", $JobName,
        "--image=$Image",
        "--region=$Region",
        "--service-account=$JobServiceAccount",
        "--cpu=1",
        "--memory=1Gi",
        "--tasks=1",
        "--max-retries=0",
        "--task-timeout=3600s",
        "--env-vars-file=$($envFile.FullName)",
        "--args=$containerArgs",
        "--quiet"
    )
    if ($EnableEmail) {
        $deployArgs += "--set-secrets=NEWSAGENT_SMTP_PASSWORD=$SecretName`:$secretVersion"
    }
    Invoke-Gcloud @deployArgs | Out-Null
}
finally {
    Remove-Item -LiteralPath $envFile.FullName -Force -ErrorAction SilentlyContinue
}

Invoke-Gcloud run jobs add-iam-policy-binding $JobName `
    --region=$Region `
    "--member=serviceAccount:$SchedulerServiceAccount" `
    --role=roles/run.invoker | Out-Null

$failureFilter = "resource.type=`"cloud_run_job`" AND resource.labels.job_name=`"$JobName`" AND severity>=ERROR"
if (Test-GcloudResource @("logging", "metrics", "describe", $FailureMetricName)) {
    Invoke-Gcloud logging metrics update $FailureMetricName `
        "--description=NewsAgent Cloud Run job errors" `
        "--log-filter=$failureFilter" | Out-Null
}
else {
    Invoke-Gcloud logging metrics create $FailureMetricName `
        "--description=NewsAgent Cloud Run job errors" `
        "--log-filter=$failureFilter" | Out-Null
}

$policyFile = New-TemporaryFile
try {
    $policy = @{
        displayName = "NewsAgent Cloud Run job failures"
        combiner = "OR"
        enabled = $true
        notificationChannels = @()
        conditions = @(
            @{
                displayName = "NewsAgent error log detected"
                conditionThreshold = @{
                    filter = "metric.type=`"logging.googleapis.com/user/$FailureMetricName`" AND resource.type=`"cloud_run_job`""
                    comparison = "COMPARISON_GT"
                    thresholdValue = 0
                    duration = "0s"
                    aggregations = @(
                        @{
                            alignmentPeriod = "60s"
                            perSeriesAligner = "ALIGN_SUM"
                        }
                    )
                    trigger = @{ count = 1 }
                }
            }
        )
    }
    if ($NotificationChannelId) {
        $policy.notificationChannels = @($NotificationChannelId)
    }
    $policy | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $policyFile.FullName -Encoding UTF8
    try {
        $existingPolicy = & gcloud alpha monitoring policies list `
            --filter="displayName=NewsAgent Cloud Run job failures" `
            --format="value(name)" --limit=1
        if ($LASTEXITCODE -ne 0) { throw "Unable to list alert policies" }
        if ($existingPolicy) {
            Invoke-Gcloud alpha monitoring policies update $existingPolicy `
                "--policy-from-file=$($policyFile.FullName)" | Out-Null
        }
        else {
            Invoke-Gcloud alpha monitoring policies create `
                "--policy-from-file=$($policyFile.FullName)" | Out-Null
        }
        if (-not $NotificationChannelId) {
            Write-Warning "Failure alert policy has no notification channel. Add one in Cloud Monitoring or pass -NotificationChannelId."
        }
    }
    catch {
        Write-Warning "Failure alert policy creation was skipped: $($_.Exception.Message)"
    }
}
finally {
    Remove-Item -LiteralPath $policyFile.FullName -Force -ErrorAction SilentlyContinue
}

if ($EnableScheduler) {
    $uri = "https://run.googleapis.com/v2/projects/$ProjectId/locations/$Region/jobs/$JobName`:run"
    $schedulerArgs = @(
        "--location=$Region",
        "--schedule=$Schedule",
        "--time-zone=$TimeZone",
        "--uri=$uri",
        "--http-method=POST",
        "--oauth-service-account-email=$SchedulerServiceAccount",
        "--oauth-token-scope=https://www.googleapis.com/auth/cloud-platform",
        "--attempt-deadline=30m",
        "--max-retry-attempts=0"
    )
    if (Test-GcloudResource @("scheduler", "jobs", "describe", $SchedulerName, "--location=$Region")) {
        Invoke-Gcloud scheduler jobs update http $SchedulerName @schedulerArgs | Out-Null
    }
    else {
        Invoke-Gcloud scheduler jobs create http $SchedulerName @schedulerArgs | Out-Null
    }
}

if ($BillingAccountId) {
    try {
        $existingBudget = & gcloud billing budgets list `
            "--billing-account=$BillingAccountId" `
            --filter="displayName=NewsAgent monthly budget" `
            --format="value(name)" --limit=1
        if ($LASTEXITCODE -ne 0) { throw "Unable to list billing budgets" }
        if (-not $existingBudget) {
            Invoke-Gcloud billing budgets create `
                "--billing-account=$BillingAccountId" `
                "--display-name=NewsAgent monthly budget" `
                --budget-amount=10USD `
                --threshold-rule=percent=0.5 `
                --threshold-rule=percent=0.8 `
                --threshold-rule=percent=1.0 | Out-Null
        }
    }
    catch {
        Write-Warning "Budget creation was skipped: $($_.Exception.Message)"
    }
}
else {
    Write-Warning "No BillingAccountId supplied; create a USD 10 budget alert in Cloud Billing."
}

Write-Host "Deployed image: $Image"
Write-Host "Cloud Run job: $JobName ($Region)"
Write-Host "State bucket: gs://$BucketName/newsagent/"
if (-not $EnableScheduler) {
    Write-Host "Scheduler remains disabled. Run scripts/run_gcp_job.ps1, validate output, then redeploy with -EnableScheduler."
}
