from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from newsagent import cli


def daily_args(email: bool) -> Namespace:
    return Namespace(
        command="daily",
        output_language="original",
        collect_limit=None,
        brief_limit=None,
        email=email,
    )


class DailyExitCodeTests(TestCase):
    def make_app(self) -> MagicMock:
        app = MagicMock()
        app.settings = {}
        app.daily.return_value = (1, "body", {}, Path("/tmp/latest.md"), None)
        app.last_daily_summary = {}
        return app

    def test_successful_run_exits_zero(self) -> None:
        app = self.make_app()
        app.daily.return_value = (1, "body", {}, Path("/tmp/latest.md"), {"ok": True})

        result = cli.run_daily_with_logs(app, daily_args(email=True))

        self.assertEqual(result["exit_code"], 0)

    def test_failed_email_delivery_reports_non_zero_exit(self) -> None:
        app = self.make_app()
        app.daily.return_value = (1, "body", {}, Path("/tmp/latest.md"), {"ok": False, "error": "SMTP 535"})

        result = cli.run_daily_with_logs(app, daily_args(email=True))

        self.assertEqual(result["exit_code"], 1)

    def test_failed_delivery_without_email_flag_still_exits_zero(self) -> None:
        app = self.make_app()

        result = cli.run_daily_with_logs(app, daily_args(email=False))

        self.assertEqual(result["exit_code"], 0)

    def test_run_finished_log_carries_the_exit_code(self) -> None:
        app = self.make_app()
        app.daily.return_value = (1, "body", {}, Path("/tmp/latest.md"), {"ok": False})

        with patch("newsagent.cli.emit_pipeline_log") as emit:
            cli.run_daily_with_logs(app, daily_args(email=True))

        finished = [call for call in emit.call_args_list if call.args[3] == "run_finished"][0]
        self.assertEqual(finished.kwargs["exit_code"], 1)

    def test_daily_command_raises_system_exit_on_delivery_failure(self) -> None:
        app = self.make_app()
        app.daily.return_value = (1, "body", {}, Path("/tmp/latest.md"), {"ok": False})
        app.settings = {}

        with (
            patch("newsagent.cli.NewsAgentApp", return_value=app),
            patch("newsagent.cli.load_settings", return_value={}),
            self.assertRaises(SystemExit) as ctx,
        ):
            cli.main(["daily", "--email"])

        self.assertEqual(ctx.exception.code, 1)
        app.close.assert_called_once_with()
