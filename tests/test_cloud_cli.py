from __future__ import annotations

from argparse import Namespace
from contextlib import nullcontext
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch
import uuid

from newsagent.cli import run_cloud_daily


class CloudCliTests(TestCase):
    def make_args(self, email=False):
        return Namespace(
            command="cloud-daily",
            output_language="zh",
            collect_limit=None,
            brief_limit=None,
            email=email,
        )

    def test_cloud_daily_restores_runs_and_uploads_state(self) -> None:
        db_path = Path(__file__).resolve().parent / f"cloud_cli_{uuid.uuid4().hex}.db"
        state = MagicMock()
        state.lease.return_value = nullcontext()
        state.restore_database.return_value = False
        state.state_generation = 0
        state.upload_database.return_value = 1
        state.upload_outbox.return_value = 3
        app = MagicMock()
        result = {"email_result": None}
        with (
            patch("newsagent.cli.load_settings", return_value={"database": {"path": str(db_path)}}),
            patch("newsagent.cloud_state.CloudStateStore.from_environment", return_value=state),
            patch("newsagent.cli.NewsAgentApp", return_value=app),
            patch("newsagent.cli.run_daily_with_logs", return_value=result),
        ):
            run_cloud_daily(self.make_args())
        state.restore_database.assert_called_once_with()
        app.db.backup_to.assert_called_once()
        state.upload_database.assert_called_once()
        state.upload_outbox.assert_called_once()
        app.close.assert_called_once_with()

    def test_email_failure_is_reported_after_state_upload(self) -> None:
        db_path = Path(__file__).resolve().parent / f"cloud_cli_{uuid.uuid4().hex}.db"
        state = MagicMock()
        state.lease.return_value = nullcontext()
        state.restore_database.return_value = False
        state.state_generation = 0
        state.upload_database.return_value = 1
        state.upload_outbox.return_value = 3
        app = MagicMock()
        result = {"email_result": {"ok": False}}
        with (
            patch("newsagent.cli.load_settings", return_value={"database": {"path": str(db_path)}}),
            patch("newsagent.cloud_state.CloudStateStore.from_environment", return_value=state),
            patch("newsagent.cli.NewsAgentApp", return_value=app),
            patch("newsagent.cli.run_daily_with_logs", return_value=result),
        ):
            with self.assertRaisesRegex(RuntimeError, "state was saved"):
                run_cloud_daily(self.make_args(email=True))
        state.upload_database.assert_called_once()
        app.close.assert_called_once_with()
