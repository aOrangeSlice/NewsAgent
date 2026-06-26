# NewsAgent Operations Runbook

Last updated: 2026-06-27

## Daily Commands

Check local configuration and service status:

```powershell
python -m newsagent doctor
```

Initialize or migrate the local SQLite database:

```powershell
python -m newsagent init-db
```

Collect source items without generating a brief:

```powershell
python -m newsagent collect --limit 30
```

Generate a brief without collecting:

```powershell
python -m newsagent brief --output-language zh
```

Run the full daily pipeline:

```powershell
python -m newsagent daily --output-language zh
```

Run the full daily pipeline and send email:

```powershell
python -m newsagent daily --output-language zh --email
```

## Output Files

Daily runs write Markdown files under `data/outbox`:

- `latest_rules.md`: deterministic rules edition.
- `latest_llm.md`: LLM-written edition.
- `latest.md`: preferred edition selected by `briefing.use_llm`.
- `briefing_<id>_rules.md` and `briefing_<id>_llm.md`: historical per-run files.

`briefing.use_llm` chooses which edition is written to `latest.md`. It does not disable generation of the other edition.

When `--email` is used:

- `use_llm: true` sends both `[Rules]` and `[LLM]` labeled messages.
- `use_llm: false` sends only the rules edition.

## Email Setup

Prefer environment variables for secrets:

```powershell
$env:NEWSAGENT_SMTP_PASSWORD = "your-smtp-app-password"
```

For Windows scheduled tasks, set the password persistently:

```powershell
setx NEWSAGENT_SMTP_PASSWORD "your-smtp-app-password"
```

Open a new PowerShell window after `setx`, then install the scheduled task:

```powershell
.\scripts\install_windows_task.ps1 -Time "08:00"
```

Send the latest generated brief again:

```powershell
python -m newsagent send-latest --subject "NewsAgent Daily Brief"
```

## Logs and Troubleshooting

Operational data is stored in `data/newsagent.db`.

Useful tables:

- `pipeline_logs`: run-level events such as `run_started`, `collect_finished`, `briefing_created`, `email_finished`, and failures.
- `source_collection_logs`: per-source fetched, inserted, existing, and error counts.
- `delivery_logs`: email success/failure records.
- `llm_runs`: LLM and translation success/failure telemetry.

Common checks:

- If no new items appear, inspect `source_collection_logs` for repeated source failures or all-duplicate runs.
- If email is not sent, run `python -m newsagent doctor` and check `delivery_logs`.
- If LLM output is weak or unavailable, check `llm_runs`; the app should fall back to deterministic rules output.
- If translated output loses URLs or Story IDs, the translation validator should fall back to the canonical original-language body with a warning.

## Testing

Run the test suite with:

```powershell
python -m unittest discover -s tests -v
```

The MVP intentionally avoids pip dependencies; `pytest` is not required.

Before using a brief as a daily production email, also run the manual checklist in `docs/QUICK_TEST_CHECKLIST.md`.
