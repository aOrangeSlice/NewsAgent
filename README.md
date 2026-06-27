# NewsAgent

Local-first market, policy, AI, and healthcare intelligence agent.

## Recommended local LLM

Detected hardware:

- CPU: AMD Ryzen 9 9950X, 16 cores / 32 threads
- RAM: about 32 GB
- GPU: NVIDIA GeForce RTX 5060 Ti, 16 GB VRAM

Recommended MVP model:

- Primary: `qwen3:8b` via Ollama
- Optional stronger model to try later: `gpt-oss:20b` or a Qwen 14B quantized model if latency is acceptable

Install Ollama, then run:

```powershell
ollama pull qwen3:8b
ollama serve
```

The app also works without Ollama by using a deterministic fallback summary.

## Quick start

```powershell
python -m newsagent init-config
python -m newsagent doctor
python -m newsagent init-db
python -m newsagent secrets-scan
python -m newsagent collect --limit 30
python -m newsagent source-health
python -m newsagent brief --output-language zh
python -m newsagent ask "AIインフラに関する最も重要なトピックは何でしょうか？" --language ja
```

`init-config` creates `config/settings.json` from
`config/settings.example.json` if it does not already exist. Other commands also
create it automatically on first run. Data is stored in `data/newsagent.db`.

Brief output supports four modes:

- `original`: preserve each story in its source language.
- `zh`: translate the completed brief into Simplified Chinese.
- `en`: translate the completed brief into English.
- `ja`: translate the completed brief into Japanese.

Translation runs only after collection, storage, ranking, and canonical brief
generation. Raw items and story clusters remain in their source languages.
`--language` is retained as a compatibility alias for `--output-language`.

Each run saves two editions built from the same selected stories:

- `rules`: deterministic edition, equivalent to `use_llm: false`.
- `llm`: LLM-written edition, equivalent to `use_llm: true`.

The `briefing.use_llm` setting chooses which edition is printed and written to
`latest.md`. It does not disable generation of the other edition. When a daily
run uses `--email`, `use_llm: true` sends two separately labeled messages
(`[Rules]` and `[LLM]`), while `use_llm: false` sends only the rules edition.
Both editions are saved in SQLite. Daily runs also write
`latest_rules.md` and `latest_llm.md`. If the selected output language is not
`original`, final translation still uses the configured LLM for both editions.
Long briefs are translated in Markdown-aware chunks. Story IDs and URLs are
protected during each LLM call, restored afterward, and validated before the
translated version is accepted.

## Feedback loop

Use story IDs from `ask`, `brief`, or the database to tune ranking:

```powershell
python -m newsagent feedback 15 important
python -m newsagent feedback 15 track_more --note "AI infrastructure signal"
python -m newsagent feedback 42 show_less
python -m newsagent feedback 43 irrelevant
```

Feedback affects future ranking:

- `important`: strongly boosts this story and lightly boosts similar category/tag matches.
- `track_more`: boosts this story and future similar items more than ordinary interest.
- `show_less`: lowers this story and similar category/tag matches.
- `irrelevant`: strongly lowers this story and similar matches.

## Email delivery

Edit `config/settings.json` and keep secrets in environment variables:

```json
{
  "delivery": {
    "email": {
      "enabled": true,
      "host": "smtp.example.com",
      "port": 587,
      "use_tls": true,
      "username": "user@example.com",
      "password_env": "NEWSAGENT_SMTP_PASSWORD",
      "sender": "user@example.com",
      "recipients": ["recipient@example.com"]
    }
  }
}
```

Set the SMTP password in the shell:

```powershell
$env:NEWSAGENT_SMTP_PASSWORD = "your-smtp-app-password"
```

Run the local secret preflight before enabling email automation:

```powershell
python -m newsagent secrets-scan
```

Generate and send a daily brief:

```powershell
python -m newsagent daily --output-language zh --email
```

Send the latest generated brief again:

```powershell
python -m newsagent send-latest --subject "NewsAgent Daily Brief"
```

## Daily automation on Windows

The daily email script is:

```powershell
.\scripts\newsagent_daily_email.ps1
```

For scheduled tasks, set the Gmail app password as a persistent user environment variable:

```powershell
setx NEWSAGENT_SMTP_PASSWORD "your-gmail-app-password"
```

Open a new PowerShell window after `setx`, then install the task:

```powershell
.\scripts\install_windows_task.ps1 -Time "08:00"
```

This creates a Windows Scheduled Task named `NewsAgent Daily Email`.

Important: Windows Task Scheduler can run only when this computer is powered on. It may run after wake if the machine is asleep and the task is configured to wake the computer, but it cannot run while the computer is fully shut down. If you need email delivery even when this PC is off, run NewsAgent on an always-on device or cloud runner, such as:

- a small VPS
- GitHub Actions with a scheduled workflow
- a NAS/home server
- another always-on Windows machine

Keep the same SMTP configuration and run:

```powershell
python -m newsagent daily --output-language zh --email
```

## Development and testing

The MVP uses only the Python standard library. Run the automated test suite and
local security preflight with:

```powershell
python -m unittest discover -s tests -v
python -m newsagent secrets-scan
```

Useful project docs:

- `docs/IMPLEMENTATION_STATUS.md`: current implemented, partial, and planned scope.
- `docs/OPERATIONS.md`: local runbook for daily runs, email, logs, and troubleshooting.
- `docs/DAILY_BRIEF_SELECTION_LOGIC.md`: how stories are selected for daily briefs.

## Add a source

Edit `config/sources.json`. Most new RSS/Atom feeds can be added without code changes:

```json
{
  "id": "example_feed",
  "name": "Example Feed",
  "kind": "rss",
  "category": "ai",
  "subcategory": "official",
  "region": "global",
  "url": "https://example.com/feed.xml",
  "enabled": true,
  "tier": 1,
  "priority": "P0",
  "tags": ["ai"]
}
```
