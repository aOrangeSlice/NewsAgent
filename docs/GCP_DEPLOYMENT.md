# Google Cloud Deployment

This runbook deploys NewsAgent as a low-cost scheduled batch workload:

```text
Cloud Scheduler
    -> Cloud Run Job
       -> public news sources
       -> Gemini on Vertex AI
       -> SMTP
       -> Cloud Storage (SQLite snapshot and Markdown outbox)
```

The first cloud execution starts with an empty database. Local `data/` is never
uploaded by the deployment script.

## 1. Prerequisites

Install the Google Cloud CLI, create a project with billing enabled, and sign in:

```powershell
gcloud auth login
gcloud auth application-default login
gcloud config set project "YOUR_PROJECT_ID"
```

The deploying user needs permission to enable APIs, build images, create Cloud
Run jobs, service accounts, IAM bindings, buckets, secrets, Scheduler jobs, and
Monitoring resources. Budget creation additionally requires access to the
billing account.

Default resource names are fixed by the deployment script:

- Region: `asia-northeast1`
- Cloud Run Job: `newsagent-daily`
- Artifact Registry repository: `newsagent`
- State bucket: `PROJECT_ID-newsagent-state`
- Secret: `newsagent-smtp-password`
- Schedule: 08:00 in `Asia/Tokyo`

## 2. Deploy without email or scheduling

The default deployment is deliberately safe: it creates the job but does not
send email or enable the schedule.

```powershell
.\scripts\deploy_gcp.ps1 `
  -ProjectId "YOUR_PROJECT_ID" `
  -BillingAccountId "YOUR_BILLING_ACCOUNT_ID"
```

The script is idempotent. It enables required APIs, creates the repository,
versioned bucket with a 14-day noncurrent-version lifecycle, service accounts,
least-privilege IAM, Secret Manager secret, image, Cloud Run Job, log metric,
Monitoring policy, and optional USD 10 billing budget.

If you already have a Monitoring email notification channel, pass its full
resource name with `-NotificationChannelId`. Without it, the alert policy is
created without outbound notifications and the script prints a warning.

## 3. Smoke test the empty cloud database

```powershell
.\scripts\run_gcp_job.ps1 -ProjectId "YOUR_PROJECT_ID"
.\scripts\logs_gcp.ps1 -ProjectId "YOUR_PROJECT_ID" -Limit 200
gcloud storage ls "gs://YOUR_PROJECT_ID-newsagent-state/newsagent/**"
```

Acceptance checks:

- The execution finishes successfully.
- Logs contain `cloud_state_restored` with `restored=false` on the first run.
- `newsagent/state/newsagent.db` exists in the bucket.
- `newsagent/outbox/latest.md`, `latest_rules.md`, and `latest_llm.md` exist.
- A second manual run logs `restored=true` and a later object generation.
- Vertex failure does not abort briefing generation; the rules fallback is used.

## 4. Configure and validate email

Add the SMTP app password without placing it in Git or command history:

```powershell
.\scripts\set_gcp_secret.ps1 -ProjectId "YOUR_PROJECT_ID"
```

Redeploy with email enabled but keep Scheduler disabled:

```powershell
.\scripts\deploy_gcp.ps1 `
  -ProjectId "YOUR_PROJECT_ID" `
  -EnableEmail `
  -EmailUsername "sender@example.com" `
  -EmailSender "sender@example.com" `
  -EmailRecipients "recipient@example.com"

.\scripts\run_gcp_job.ps1 -ProjectId "YOUR_PROJECT_ID"
```

Confirm the expected Rules and LLM messages. SMTP failures mark the Cloud Run
execution failed only after the database snapshot is safely stored. Automatic
retries remain disabled to prevent duplicate delivery.

## 5. Enable the daily schedule

After the manual email test succeeds, run the same deployment with
`-EnableScheduler`:

```powershell
.\scripts\deploy_gcp.ps1 `
  -ProjectId "YOUR_PROJECT_ID" `
  -EnableEmail `
  -EnableScheduler `
  -EmailUsername "sender@example.com" `
  -EmailSender "sender@example.com" `
  -EmailRecipients "recipient@example.com"
```

Cloud Scheduler uses `0 8 * * *`, `Asia/Tokyo`, OAuth with the dedicated
`newsagent-scheduler` service account, and zero retries.

## Configuration and cost controls

Cloud Run receives configuration through explicit environment variables. The
important defaults are:

- `/tmp/newsagent/data/newsagent.db`
- `vertex` / `gemini-2.5-flash-lite` / Vertex location `global`
- 12 model calls per execution
- 4096 maximum output tokens per call
- one task, 1 vCPU, 1 GiB memory, 60-minute timeout, zero retries
- a 75-minute Cloud Storage lock lease

Cloud Storage generation preconditions reject stale writers. The lock is not a
substitute for a database server; keep this deployment to one scheduled writer.
Budget alerts do not stop spending. Review Cloud Billing and Vertex token
telemetry in `llm_runs` after the first week.

## Rollback

Every deployment uses a new image tag. List images and point the job back to a
known tag:

```powershell
gcloud artifacts docker images list `
  "asia-northeast1-docker.pkg.dev/YOUR_PROJECT_ID/newsagent/newsagent" `
  --include-tags

gcloud run jobs update newsagent-daily `
  --region=asia-northeast1 `
  --image="PREVIOUS_IMAGE_URI"
```

Cloud Storage object versioning preserves earlier SQLite snapshots. Inspect and
restore a previous generation only while Scheduler is paused and no job is
running:

```powershell
gcloud scheduler jobs pause newsagent-daily-0800 --location=asia-northeast1
gcloud storage ls --all-versions `
  "gs://YOUR_PROJECT_ID-newsagent-state/newsagent/state/newsagent.db"
```

Copy the selected generation back to the live object, then run one manual job
before resuming Scheduler.

## Cleanup

The cleanup script deletes only the fixed NewsAgent resources, not the project.
It permanently removes the versioned SQLite state and requires two explicit
confirmations:

```powershell
.\scripts\destroy_gcp.ps1 `
  -ProjectId "YOUR_PROJECT_ID" `
  -ConfirmProjectId "YOUR_PROJECT_ID" `
  -BillingAccountId "YOUR_BILLING_ACCOUNT_ID" `
  -Force
```
