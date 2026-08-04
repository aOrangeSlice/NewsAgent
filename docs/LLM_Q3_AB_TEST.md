# Qwen3-30B-A3B Q3 A/B Test

The production configuration uses `qwen3:30bq3`. The original `qwen3:8b`
remains installed as the fallback model.

## Build the local model

The experiment uses the Q3_K_M file from
`eaddario/Qwen3-30B-A3B-GGUF` and pins its SHA256 in
`models/Qwen3-30B-A3B-Q3_K_M.gguf.sha256`. Download the matching file into
`models/`, verify the checksum, then run from the repository root:

```powershell
ollama show qwen3:30bq3
```

The ignored `config/settings.q3.local.json` points the experiment at the Q3
model and keeps email delivery disabled.

## Run the controlled comparison

```powershell
python scripts/llm_ab_test.py --limit 12
```

Use `--q3-only` when the baseline has already been captured. Reports are
written under `data/ab-tests/` and include per-call elapsed time, Ollama token
timings, GPU memory snapshots, output previews, URL counts, and basic Markdown
validation.

If Q3 is unstable at `num_ctx=8192`, change only the local Q3 configuration to
6144 and rerun. Do not change `config/settings.json` unless the experiment is
accepted.

## Recorded result on 2026-07-13

- Model: `qwen3:30bq3`, `qwen3moe`, 30.5B, `Q3_K_M`.
- GGUF size: 13,506,726,080 bytes.
- SHA256: `7fa1a1fe1b7fc878b4ab78977db2e2b4ee639510ff926259fe5c0a1b5d571b86`.
- Runtime: 100% GPU on the RTX 5060 Ti; approximately 15.6GiB used with
  roughly 0.4GiB free at `num_ctx=8192`.
- Q3 completed the five-case smoke test in approximately 25–31 seconds per
  case. The 8B baseline took approximately 52–83 seconds per case.
- Both models produced the same existing `fallback_rules` / answer validation
  behavior on this dataset, so Q3 was not promoted to the default pipeline.

Reports:

- `data/ab-tests/llm_ab_20260713_172911.json` — Q3.
- `data/ab-tests/llm_ab_20260713_173640.json` — 8B baseline.
