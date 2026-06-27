from __future__ import annotations

import argparse
import json
import sys
from uuid import uuid4

from .config import DEFAULT_SETTINGS, ROOT, ensure_settings_file
from .models import tokyo_now_iso
from .pipeline import NewsAgentApp
from .security import scan_for_secrets


def emit_log(level: str, event: str, **fields) -> dict[str, object]:
    record = {
        "ts": tokyo_now_iso(),
        "level": level,
        "event": event,
        **fields,
    }
    print(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return record


def emit_pipeline_log(app: NewsAgentApp, pipeline_run_id: str, level: str, event: str, **fields) -> None:
    payload = dict(fields)
    payload["run_id"] = pipeline_run_id
    record = emit_log(level, event, **payload)
    app.db.log_pipeline_event(pipeline_run_id, level, event, record)


def print_source_health(rows: list[dict[str, object]]) -> None:
    columns = [
        ("source", 28),
        ("enabled", 7),
        ("last_status", 11),
        ("recent_runs", 4),
        ("recent_failures", 5),
        ("recent_inserted", 8),
        ("last_error", 42),
    ]
    header = " ".join(name.ljust(width) for name, width in columns)
    print(header)
    print("-" * len(header))
    for row in rows:
        values = []
        for name, width in columns:
            value = str(row.get(name, ""))
            if len(value) > width:
                value = value[: max(width - 3, 0)] + "..."
            values.append(value.ljust(width))
        print(" ".join(values))


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(prog="newsagent")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-config", help="Create config/settings.json from settings.example.json.")
    sub.add_parser("init-db", help="Initialize SQLite database.")
    sub.add_parser("doctor", help="Check configuration and local LLM availability.")

    collect_p = sub.add_parser("collect", help="Collect items from enabled sources.")
    collect_p.add_argument("--limit", type=int, default=None)

    health_p = sub.add_parser("source-health", help="Show recent per-source collection health.")
    health_p.add_argument("--recent-runs", type=int, default=10)
    health_p.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    secrets_p = sub.add_parser("secrets-scan", help="Scan local text/config files for likely secrets.")
    secrets_p.add_argument("--json", action="store_true", help="Print machine-readable JSON.")

    brief_p = sub.add_parser("brief", help="Generate a Daily Brief from local stories.")
    brief_p.add_argument(
        "--output-language",
        "--language",
        dest="output_language",
        choices=["original", "zh", "en", "ja"],
        default=None,
        help="Brief output language. --language remains as a compatibility alias.",
    )
    brief_p.add_argument("--limit", type=int, default=None)

    daily_p = sub.add_parser("daily", help="Run collect + brief and write Markdown to data/outbox.")
    daily_p.add_argument(
        "--output-language",
        "--language",
        dest="output_language",
        choices=["original", "zh", "en", "ja"],
        default=None,
        help="Brief output language. --language remains as a compatibility alias.",
    )
    daily_p.add_argument("--collect-limit", type=int, default=None)
    daily_p.add_argument("--brief-limit", type=int, default=None)
    daily_p.add_argument("--email", action="store_true", help="Send the generated brief by email.")

    send_p = sub.add_parser("send-latest", help="Send data/outbox/latest.md by email.")
    send_p.add_argument("--subject", default="NewsAgent Daily Brief")

    ask_p = sub.add_parser("ask", help="Ask a question against local collected stories.")
    ask_p.add_argument("question")
    ask_p.add_argument("--language", choices=["original", "zh", "en", "ja"], default="zh")
    ask_p.add_argument("--limit", type=int, default=12)

    fb_p = sub.add_parser("feedback", help="Record feedback for a story.")
    fb_p.add_argument("story_id", type=int)
    fb_p.add_argument("feedback", choices=["important", "irrelevant", "show_less", "track_more"])
    fb_p.add_argument("--note", default="")

    args = parser.parse_args(argv)
    if args.command == "init-config":
        created = ensure_settings_file(DEFAULT_SETTINGS)
        action = "Created" if created else "Already exists"
        print(f"{action}: {DEFAULT_SETTINGS}")
        return
    if args.command == "secrets-scan":
        findings = scan_for_secrets(ROOT)
        if args.json:
            print(json.dumps({"ok": not findings, "findings": findings}, indent=2, ensure_ascii=False))
        elif findings:
            print(f"Secret scan found {len(findings)} issue(s):")
            for finding in findings:
                print(
                    f"- {finding['file']}:{finding['line']} "
                    f"{finding['kind']}: {finding['detail']}"
                )
        else:
            print("Secret scan OK: no likely secrets found.")
        if findings:
            raise SystemExit(1)
        return

    app = NewsAgentApp()
    try:
        if args.command == "init-db":
            print(f"Initialized database: {app.settings['database']['path']}")
        elif args.command == "doctor":
            print(json.dumps(app.doctor(), indent=2, ensure_ascii=False))
        elif args.command == "collect":
            result = app.collect(limit=args.limit)
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.command == "source-health":
            rows = app.source_health(recent_runs=args.recent_runs)
            if args.json:
                print(json.dumps(rows, indent=2, ensure_ascii=False))
            else:
                print_source_health(rows)
        elif args.command == "brief":
            briefing_id, body = app.brief(
                output_language=args.output_language,
                limit=args.limit,
            )
            print(f"Briefing #{briefing_id}\n")
            print(body)
        elif args.command == "daily":
            run_id = uuid4().hex
            emit_pipeline_log(
                app,
                run_id,
                "INFO",
                "run_started",
                command="daily",
                output_language=args.output_language,
                collect_limit=args.collect_limit,
                brief_limit=args.brief_limit,
                email=args.email,
            )
            try:
                briefing_id, _body, collect_result, path, email_result = app.daily(
                    output_language=args.output_language,
                    collect_limit=args.collect_limit,
                    brief_limit=args.brief_limit,
                    email=args.email,
                    run_id=run_id,
                )
            except Exception as exc:
                emit_pipeline_log(app, run_id, "ERROR", "run_failed", error=str(exc))
                raise

            summary = getattr(app, "last_daily_summary", {})
            collect_summary = summary.get("collect", collect_result)
            briefing_summary = summary.get("briefing", {})
            emit_pipeline_log(app, run_id, "INFO", "collect_finished", **collect_summary)
            for source_error in collect_summary.get("errors", []):
                emit_pipeline_log(app, run_id, "WARNING", "source_failed", **source_error)
            if (
                briefing_summary.get("min_stories", 0)
                and briefing_summary.get("story_count", 0) < briefing_summary.get("min_stories", 0)
            ):
                emit_pipeline_log(
                    app,
                    run_id,
                    "WARNING",
                    "briefing_below_min_stories",
                    story_count=briefing_summary.get("story_count", 0),
                    min_stories=briefing_summary.get("min_stories", 0),
                )
            emit_pipeline_log(app, run_id, "INFO", "briefing_created", **briefing_summary)
            if email_result is not None:
                email_level = "INFO" if email_result.get("ok") else "ERROR"
                emit_pipeline_log(app, run_id, email_level, "email_finished", **email_result)
            emit_pipeline_log(
                app,
                run_id,
                "INFO",
                "run_finished",
                briefing_id=briefing_id,
                outbox_path=str(path),
                exit_code=0,
            )
        elif args.command == "send-latest":
            from pathlib import Path

            path = Path(app.settings["database"]["path"]).parent / "outbox" / "latest.md"
            if not path.exists():
                raise FileNotFoundError(f"No latest brief found at {path}")
            result = app.send_email(path.read_text(encoding="utf-8"), subject=args.subject)
            print(json.dumps({"email": result}, indent=2, ensure_ascii=False))
        elif args.command == "ask":
            print(app.ask(args.question, language=args.language, limit=args.limit))
        elif args.command == "feedback":
            feedback_id = app.feedback(args.story_id, args.feedback, args.note)
            print(f"Recorded feedback #{feedback_id}")
    finally:
        app.close()


if __name__ == "__main__":
    main(sys.argv[1:])
