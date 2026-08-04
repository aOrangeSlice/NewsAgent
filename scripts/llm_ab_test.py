"""Run a repeatable local-model A/B smoke test without changing production config."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from newsagent.pipeline import NewsAgentApp  # noqa: E402


def command_output(command: list[str]) -> str:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return f"unavailable: {exc}"
    output = result.stdout.strip()
    if result.stderr.strip():
        output = f"{output}\nstderr: {result.stderr.strip()}".strip()
    return output


def hardware_snapshot() -> dict[str, str]:
    return {
        "gpu": command_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used,memory.free",
                "--format=csv,noheader",
            ]
        ),
        "ollama_ps": command_output(["ollama", "ps"]),
    }


def validate_output(text: str) -> dict[str, Any]:
    return {
        "non_empty": bool(text.strip()),
        "length_chars": len(text),
        "source_urls": text.count("http://") + text.count("https://"),
        "has_markdown_headings": "## " in text,
    }


def briefing_case(app: NewsAgentApp, stories: list[dict[str, Any]], language: str) -> dict[str, Any]:
    briefing = app.summarizer.create_briefing(stories, output_language=language, use_llm=True)
    return {
        "text": briefing.body,
        "generation_status": briefing.generation_status,
        "translation_status": briefing.translation_status,
        "generation_model": briefing.generation_model,
        "translation_model": briefing.translation_model,
    }


def run_case(app: NewsAgentApp, name: str, action: Any) -> dict[str, Any]:
    app.summarizer.ollama.drain_metrics()
    started = time.perf_counter()
    raw_output: Any = ""
    try:
        raw_output = action()
        metadata = raw_output if isinstance(raw_output, dict) else {}
        output = metadata.get("text", raw_output) if isinstance(raw_output, dict) else raw_output
        status = "ok"
        error = ""
    except Exception as exc:  # Keep the other model/cases running for comparison.
        output = ""
        status = "error"
        error = f"{type(exc).__name__}: {exc}"
    elapsed = round(time.perf_counter() - started, 3)
    metrics = app.summarizer.ollama.drain_metrics()
    result = {
        "name": name,
        "status": status,
        "error": error,
        "elapsed_seconds": elapsed,
        "ollama_metrics": metrics,
        "output_validation": validate_output(output),
        "output_preview": output[:1000],
    }
    if isinstance(raw_output, dict):
        result.update({key: value for key, value in metadata.items() if key != "text"})
    return result


def run_model(label: str, settings_path: Path, limit: int) -> dict[str, Any]:
    app = NewsAgentApp(settings_path=settings_path)
    try:
        stories = app._select_stories(limit)
        cases = [
            (
                "brief_zh",
                lambda: briefing_case(app, stories, "zh"),
            ),
            (
                "brief_en",
                lambda: briefing_case(app, stories, "en"),
            ),
            (
                "brief_ja",
                lambda: briefing_case(app, stories, "ja"),
            ),
            (
                "ask_ai_infrastructure",
                lambda: app.ask(
                    "What are the most important AI infrastructure stories?",
                    language="en",
                    limit=12,
                ),
            ),
            (
                "ask_market",
                lambda: app.ask("哪些市场和宏观变化最值得关注？", language="zh", limit=12),
            ),
        ]
        results = [run_case(app, name, action) for name, action in cases]
        return {
            "label": label,
            "model": app.summarizer.model,
            "settings_path": str(settings_path),
            "story_count": len(stories),
            "cases": results,
            "hardware_after": hardware_snapshot(),
        }
    finally:
        app.close()


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-settings", type=Path, default=ROOT / "config" / "settings.json")
    parser.add_argument("--q3-settings", type=Path, default=ROOT / "config" / "settings.q3.local.json")
    parser.add_argument("--limit", type=int, default=12, help="Number of stories used in each controlled case.")
    parser.add_argument("--q3-only", action="store_true", help="Run only the Q3 model.")
    parser.add_argument("--baseline-only", action="store_true", help="Run only the baseline model.")
    parser.add_argument("--output", type=Path, default=None, help="JSON report path.")
    args = parser.parse_args()

    if args.q3_only and args.baseline_only:
        parser.error("--q3-only and --baseline-only cannot be used together")

    models = []
    if not args.q3_only:
        models.append(("baseline_8b", args.baseline_settings))
    if not args.baseline_only:
        models.append(("q3", args.q3_settings))

    report: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "limit": args.limit,
        "hardware_before": hardware_snapshot(),
        "models": [],
    }
    for label, settings_path in models:
        report["models"].append(run_model(label, settings_path, args.limit))

    output_path = args.output or ROOT / "data" / "ab-tests" / (
        f"llm_ab_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(output_path), "models": [item["label"] for item in report["models"]]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
