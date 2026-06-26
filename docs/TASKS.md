# NewsAgent Build Tasks

## Current completed scope

- Create modular Python package.
- Add JSON configuration for settings and source registry.
- Add SQLite storage.
- Add collectors for RSS/Atom, CCTV Xinwen Lianbo pages, GitHub Search, Hugging Face models, and Yahoo quotes.
- Add local-first summarizer with Ollama support and deterministic fallback.
- Add Daily Brief generation with both `rules` and `llm` variants from the same selected story set.
- Add output-language handling for `original`, `zh`, `en`, and `ja`, including Markdown-aware chunked translation.
- Add local question answering against collected stories.
- Add feedback recording.
- Add `daily` command that runs collection and writes Markdown files to `data/outbox`.
- Add SMTP email delivery and `send-latest`.
- Add Windows Task Scheduler helper scripts.
- Add pipeline logs, source collection logs, delivery logs, and LLM run logs.
- Add automated tests with the standard-library `unittest` runner.

## Recommended local model

Detected machine:

- AMD Ryzen 9 9950X, 16 cores / 32 threads.
- 32 GB RAM.
- NVIDIA GeForce RTX 5060 Ti, 16 GB VRAM.

Recommended default:

- `qwen3:8b` through Ollama.

Why:

- Good Chinese, English, and Japanese coverage.
- Fits comfortably in 16 GB VRAM with room for context.
- Strong enough for summarization, routing, and structured output.
- Faster and safer for MVP than jumping directly to 14B/20B.

Second-stage experiment:

- Try `gpt-oss:20b` or a Qwen 14B quantized model for long-form analysis only.
- Keep `qwen3:8b` for daily summaries if latency matters.

## Next implementation tasks

1. Add a `web_page` collector for official pages that do not expose RSS.
2. Add more policy collectors for China, Japan, US, and EU official sources.
3. Improve story clustering so repeated URLs and related titles merge without overwriting existing source URLs or item IDs.
4. Add source health checks and optional auto-disable for repeatedly failing sources.
5. Add a retention cleanup command for old raw items, logs, and LLM telemetry.
6. Add a simple HTML report output in addition to Markdown.
7. Add a secret scan or preflight check for SMTP passwords, API tokens, and cookies.
8. Add explicit mojibake regression tests for Chinese and Japanese output.
9. Add more mocked HTTP tests for individual collectors and failure modes.
10. Decide whether Breaking Alert and Deep Dive are still in scope for this local MVP or should remain roadmap-only.

## Planned but not implemented

- Breaking Alert event monitors.
- Dedicated Deep Dive workflow.
- HTML report rendering.
- Retention cleanup execution.
- Source health dashboard and automatic source disabling.
- Telegram, LINE, Slack, or enterprise chat delivery.
