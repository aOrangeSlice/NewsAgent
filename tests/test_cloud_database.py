from __future__ import annotations

from pathlib import Path
from unittest import TestCase
import sqlite3
import uuid

from newsagent.db import Database


class CloudDatabaseTests(TestCase):
    def test_llm_metrics_migration_and_consistent_backup(self) -> None:
        base = Path(__file__).resolve().parent
        source = base / f"cloud_db_{uuid.uuid4().hex}.db"
        snapshot = base / f"cloud_snapshot_{uuid.uuid4().hex}.db"
        legacy = sqlite3.connect(source)
        try:
            legacy.execute(
                """
                CREATE TABLE llm_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            legacy.commit()
        finally:
            legacy.close()
        db = Database(source)
        try:
            db.init()
            db.log_llm_run(
                "vertex",
                "gemini-2.5-flash-lite",
                True,
                metrics={
                    "input_tokens": 100,
                    "output_tokens": 25,
                    "elapsed_seconds": 1.25,
                },
            )
            db.backup_to(snapshot)
            restored = sqlite3.connect(snapshot)
            try:
                row = restored.execute(
                    "SELECT provider, input_tokens, output_tokens, elapsed_seconds FROM llm_runs"
                ).fetchone()
            finally:
                restored.close()
            self.assertEqual(row, ("vertex", 100, 25, 1.25))
        finally:
            db.close()
            for path in (source, Path(f"{source}-wal"), Path(f"{source}-shm"), snapshot):
                path.unlink(missing_ok=True)
