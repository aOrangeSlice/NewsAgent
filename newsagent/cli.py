from __future__ import annotations

import argparse
import json
import sys
from uuid import uuid4

from .models import tokyo_now_iso
from .pipeline import NewsAgentApp


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


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(prog="newsagent")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Initialize SQLite database.")
    sub.add_parser("doctor", help="Check configuration and local LLM availability.")

    collect_p = sub.add_parser("collect", help="Collect items from enabled sources.")
    collect_p.add_argument("--limit", type=int, default=None)

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
    app = NewsAgentApp()
    try:
        if args.command == "init-db":
            print(f"Initialized database: {app.settings['database']['path']}")
        elif args.command == "doctor":
            print(json.dumps(app.doctor(), indent=2, ensure_ascii=False))
        elif args.command == "collect":
            result = app.collect(limit=args.limit)
            print(json.dumps(result, indent=2, ensure_ascii=False))
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
