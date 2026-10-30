from __future__ import annotations

import io
import json
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from newsagent import cli
from newsagent.db import DEFAULT_RETENTION, Database
from newsagent.models import NewsItem


class CleanupCommandTests(TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(Path(self.tmp.name) / "newsagent.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.db.insert_raw_item(
            NewsItem(
                source_id="s1",
                source_name="S1",
                category="world_news",
                subcategory="",
                region="global",
                title="ancient",
                url="https://a.example/old",
                retrieved_at="1999-01-01T00:00:00+09:00",
            )
        )
        app = MagicMock()
        app.settings = {"retention": DEFAULT_RETENTION}
        app.db = self.db
        self.app = app

    def run_cli(self, argv: list[str]) -> tuple[int, str]:
        buffer = io.StringIO()
        with patch("newsagent.cli.NewsAgentApp", return_value=self.app), redirect_stdout(buffer):
            cli.main(argv)
        return 0, buffer.getvalue()

    def test_cleanup_json_dry_run_reports_without_deleting(self) -> None:
        code, output = self.run_cli(["cleanup", "--dry-run", "--json"])

        payload = json.loads(output)
        self.assertEqual(code, 0)
        self.assertTrue(payload["dry_run"])
        self.assertGreaterEqual(payload["total"], 1)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0], 1)

    def test_cleanup_deletes_and_reports(self) -> None:
        code, output = self.run_cli(["cleanup", "--json"])

        payload = json.loads(output)
        self.assertFalse(payload["dry_run"])
        self.assertGreaterEqual(payload["total"], 1)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM raw_items").fetchone()[0], 0)

    def test_cleanup_human_output_names_the_windows(self) -> None:
        code, output = self.run_cli(["cleanup", "--dry-run"])

        self.assertEqual(code, 0)
        self.assertIn("raw_items:raw_item_retention_days", output)
        self.assertIn("Would delete", output)


class HelpSurfaceTests(TestCase):
    def test_cleanup_is_documented_in_help(self) -> None:
        buffer = io.StringIO()
        with redirect_stdout(buffer), self.assertRaises(SystemExit):
            cli.main(["--help"])

        self.assertIn("cleanup", buffer.getvalue())
