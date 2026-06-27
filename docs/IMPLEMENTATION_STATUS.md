# NewsAgent Implementation Status

Last updated: 2026-06-27

This file records what the local MVP currently does, what is partial, and what remains roadmap-only. Use this as the bridge between `PRD.md`, `SPECIFICATION.md`, and the code.

## Implemented

| Area | Status |
| --- | --- |
| Configuration | `init-config` creates `config/settings.json` from the example; settings and `config/sources.json` drive local behavior. |
| Collectors | RSS/Atom, CCTV page, GitHub Search, Hugging Face Models, and Yahoo Quotes. |
| HTTP fetch | Shared fetch helpers include timeouts plus retry/backoff for transient network errors, 429, and 5xx responses. |
| Storage | SQLite database with source, raw item, story, briefing, feedback, LLM, delivery, pipeline, and source collection tables. |
| Clustering | Stable keys deduplicate items; story upserts merge source URLs, item IDs, and tags without dropping older evidence. |
| Daily brief | `daily` runs collection, ranking, briefing generation, and Markdown output. |
| Brief variants | Each run creates both `rules` and `llm` variants from the same selected story set. |
| Output language | `original`, `zh`, `en`, and `ja`; `--language` remains an alias for `--output-language`. |
| Translation | Translation happens after canonical brief generation; long Markdown is chunked and URLs / Story IDs are protected. |
| Preferred edition | `briefing.use_llm` chooses the printed `latest.md` edition but does not stop the other edition from being generated and stored. |
| Email | SMTP email delivery, `send-latest`, and daily email variant behavior. |
| Windows automation | PowerShell scripts for local scheduled daily email runs. |
| Feedback | `important`, `track_more`, `show_less`, and `irrelevant` are stored and influence future ranking. |
| Operational logs | Pipeline logs, source collection logs, delivery logs, and LLM run telemetry. |
| Source health | `source-health` summarizes recent per-source runs from `source_collection_logs`. |
| Security checks | `secrets-scan` flags likely inline secrets and `doctor` reports the scan summary. |
| Tests | Standard-library `unittest` suite: `python -m unittest discover -s tests -v`. |

## Partial

| Area | Current behavior | Gap |
| --- | --- | --- |
| Citation checks | Translation protects URLs and Story IDs; answer generation validates cited URLs in some flows. | There is no standalone complete Citation Verifier for every briefing fact. |
| `min_stories` | Daily runs can emit a pipeline warning when selected stories are below the threshold. | No alert policy yet. |
| Retention settings | Retention values exist in configuration. | No cleanup command currently enforces them. |
| Source health | Source failures are logged and visible in `source-health`. | No automatic disabling or external alerting. |
| Security checks | SMTP password can be read from an environment variable; `secrets-scan` covers common local leakage patterns. | Not a replacement for a full repository secret-scanning service. |

## Planned

- Breaking Alert event monitors.
- Dedicated Deep Dive workflow.
- HTML report rendering.
- Retention cleanup command.
- Optional source auto-disable after repeated failures.
- Additional delivery channels such as Telegram, LINE, Slack, or enterprise chat.
- More official `web_page` collectors for sources without RSS.
- More mocked HTTP tests for collector-specific failures.

## Brief Output Flow

```text
collect source items
  -> store original title and summary
  -> cluster, rank, and select stories
  -> generate canonical rules brief
  -> generate canonical LLM brief
  -> optionally translate each completed brief
  -> save both variants
  -> write latest_rules.md, latest_llm.md, and preferred latest.md
  -> optionally send email
```

Saved briefing fields:

- `canonical_body`: brief before optional translation.
- `body`: final output-language body.
- `generation_mode`: `rules` or `llm`.
- `generation_model`: model used by the LLM variant, when generation succeeded.
- `translation_status`: `not_requested`, `translated`, `fallback_original`, or legacy values.
- `translation_model`: model used for translation, when translation succeeded.
- `briefing_group`: shared ID tying the rules and LLM variants to the same run.
