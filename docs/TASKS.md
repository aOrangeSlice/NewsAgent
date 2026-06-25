# NewsAgent Build Tasks

## Done in MVP scaffold

- Create modular Python package.
- Add JSON configuration for settings and source registry.
- Add SQLite storage.
- Add collectors for RSS/Atom, GitHub Search, Hugging Face models, and Yahoo quotes.
- Add local-first summarizer with Ollama support and deterministic fallback.
- Add Daily Brief generation.
- Add local question answering against collected stories.
- Add feedback recording.
- Add `daily` command that runs collection and writes Markdown to `data/outbox`.

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

- Try `gpt-oss:20b` or a Qwen 14B quantized model for Deep Dive only.
- Keep `qwen3:8b` for daily summaries if latency matters.

## Next MVP tasks

1. Install Ollama and pull the selected local model.
2. Add a delivery module: Email first, then Telegram/LINE/Slack.
3. Add Windows Task Scheduler script for daily execution.
4. Add a `web_page` collector for official pages that do not expose RSS.
5. Add policy collectors for China, Japan, US, and EU official sources.
6. Add stronger duplicate merging for market snapshots and repeated stories.
7. Add source health checks and auto-disable failing sources.
8. Add simple HTML report output.
9. Add retention cleanup command.
10. Add tests with mocked HTTP responses.

