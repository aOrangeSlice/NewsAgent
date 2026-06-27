from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

from newsagent.config import ensure_settings_file
from newsagent.db import Database
from newsagent.models import NewsItem, Source
from newsagent.pipeline import NewsAgentApp


def sample_item() -> NewsItem:
    return NewsItem(
        source_id="test_source",
        source_name="Test Source",
        category="ai",
        subcategory="test",
        region="global",
        title="A new AI item",
        url="https://example.com/item",
        summary="Test summary",
        published_at="2026-06-25T00:00:00+00:00",
    )


def sample_market_item(retrieved_at: str) -> NewsItem:
    return NewsItem(
        source_id="market_source",
        source_name="Market Source",
        category="market",
        subcategory="quotes",
        region="global",
        title="Index: 100.00 (0.10%)",
        url="https://finance.yahoo.com/quote/^TEST",
        summary="Latest quote.",
        published_at="2026-06-25T00:00:00+00:00",
        retrieved_at=retrieved_at,
        metrics={
            "symbol": "^TEST",
            "quote_time": "2026-06-25T00:00:00+00:00",
            "market_state": "closed",
        },
    )


class FakeCollector:
    def collect(self, limit: int = 20) -> list[NewsItem]:
        return [sample_item(), sample_item()][:limit]


class CollectionStatsTests(unittest.TestCase):
    def make_db_path(self) -> Path:
        return Path(__file__).resolve().parent / f"collection_{uuid.uuid4().hex}.db"

    def remove_db_files(self, path: Path) -> None:
        for candidate in [path, Path(f"{path}-wal"), Path(f"{path}-shm")]:
            if candidate.exists():
                candidate.unlink()

    def test_duplicate_raw_item_is_not_reported_as_inserted(self):
        path = self.make_db_path()
        db = Database(path)
        try:
            db.init()
            self.assertIsInstance(db.insert_raw_item(sample_item()), int)
            self.assertIsNone(db.insert_raw_item(sample_item()))
        finally:
            db.close()
            self.remove_db_files(path)

    def test_duplicate_market_item_refreshes_retrieved_at_for_reclustering(self):
        path = self.make_db_path()
        db = Database(path)
        try:
            db.init()
            self.assertIsInstance(
                db.insert_raw_item(sample_market_item("2026-06-25T09:00:00+09:00")),
                int,
            )
            self.assertIsNone(
                db.insert_raw_item(sample_market_item("2026-06-27T09:00:00+09:00"))
            )
            row = db.conn.execute(
                "SELECT retrieved_at FROM raw_items WHERE url = ?",
                ("https://finance.yahoo.com/quote/^TEST",),
            ).fetchone()
        finally:
            db.close()
            self.remove_db_files(path)

        self.assertEqual(row["retrieved_at"], "2026-06-27T09:00:00+09:00")

    def test_pipeline_event_is_logged_by_run_id(self):
        path = self.make_db_path()
        db = Database(path)
        try:
            db.init()
            db.log_pipeline_event(
                "run-123",
                "WARNING",
                "source_failed",
                {"run_id": "run-123", "source": "nature_medicine"},
            )
            row = db.conn.execute(
                "SELECT run_id, level, event, message_json, created_at FROM pipeline_logs"
            ).fetchone()
        finally:
            db.close()
            self.remove_db_files(path)

        self.assertEqual(row["run_id"], "run-123")
        self.assertEqual(row["level"], "WARNING")
        self.assertEqual(row["event"], "source_failed")
        self.assertIn("nature_medicine", row["message_json"])
        self.assertTrue(row["created_at"].endswith("+09:00"))

    def test_settings_file_is_created_from_example(self):
        base = Path(__file__).resolve().parent / f"settings_{uuid.uuid4().hex}"
        settings = base / "settings.json"
        example = base / "settings.example.json"
        try:
            base.mkdir()
            example.write_text('{"database": {"path": "data/test.db"}}', encoding="utf-8")

            created = ensure_settings_file(settings, example)
            created_again = ensure_settings_file(settings, example)

            self.assertTrue(created)
            self.assertFalse(created_again)
            self.assertEqual(settings.read_text(encoding="utf-8"), example.read_text(encoding="utf-8"))
        finally:
            for candidate in [settings, example]:
                if candidate.exists():
                    candidate.unlink()
            if base.exists():
                base.rmdir()

    def test_story_cluster_merges_source_urls_and_item_ids(self):
        path = self.make_db_path()
        db = Database(path)
        try:
            db.init()
            first = NewsItem(
                source_id="source_a",
                source_name="Source A",
                category="ai",
                subcategory="",
                region="global",
                title="Shared story title",
                url="https://example.com/a",
                summary="First summary",
                tags=["ai", "chips"],
            )
            second = NewsItem(
                source_id="source_b",
                source_name="Source B",
                category="ai",
                subcategory="",
                region="global",
                title="Shared story title",
                url="https://example.com/b",
                summary="Second summary",
                tags=["ai", "infrastructure"],
            )
            first_id = db.insert_raw_item(first)
            second_id = db.insert_raw_item(second)
            rows = db.conn.execute("SELECT * FROM raw_items ORDER BY id").fetchall()

            story_id = db.upsert_story_from_raw(rows[0], 10.0)
            same_story_id = db.upsert_story_from_raw(rows[1], 12.0)
            story = db.conn.execute("SELECT * FROM story_clusters WHERE id = ?", (story_id,)).fetchone()
        finally:
            db.close()
            self.remove_db_files(path)

        self.assertEqual(same_story_id, story_id)
        self.assertEqual(first_id, 1)
        self.assertEqual(second_id, 2)
        self.assertIn("https://example.com/a", story["source_urls_json"])
        self.assertIn("https://example.com/b", story["source_urls_json"])
        self.assertIn("[1, 2]", story["item_ids_json"])
        self.assertIn("infrastructure", story["tags_json"])

    def test_source_health_summarizes_recent_runs(self):
        path = self.make_db_path()
        db = Database(path)
        try:
            db.init()
            db.log_source_collection(
                run_id="run-1",
                source_id="test_source",
                source_name="Test Source",
                status="success",
                fetched=3,
                inserted=2,
                existing=1,
            )
            db.log_source_collection(
                run_id="run-2",
                source_id="test_source",
                source_name="Test Source",
                status="failed",
                fetched=0,
                inserted=0,
                existing=0,
                error="timeout",
            )
            health = db.list_source_health(["test_source"], recent_runs=10)[0]
        finally:
            db.close()
            self.remove_db_files(path)

        self.assertEqual(health["source"], "test_source")
        self.assertEqual(health["recent_runs"], 2)
        self.assertEqual(health["recent_successes"], 1)
        self.assertEqual(health["recent_failures"], 1)
        self.assertEqual(health["recent_inserted"], 2)
        self.assertEqual(health["last_status"], "failed")
        self.assertEqual(health["last_error"], "timeout")

    @patch("newsagent.pipeline.build_collector", return_value=FakeCollector())
    def test_collect_separates_fetched_inserted_and_existing(self, _build_collector):
        path = self.make_db_path()
        app = NewsAgentApp.__new__(NewsAgentApp)
        app.settings = {"collection": {"per_source_limit": 3}}
        app.sources = [
            Source(
                id="test_source",
                name="Test Source",
                kind="rss",
                category="ai",
            )
        ]
        app.db = Database(path)
        try:
            app.db.init()
            result = app.collect(limit=3)
            source_log = app.db.conn.execute(
                """
                SELECT run_id, source_id, source_name, status, fetched, inserted, existing,
                       error, started_at, finished_at, created_at
                FROM source_collection_logs
                """
            ).fetchone()
        finally:
            app.db.close()
            self.remove_db_files(path)

        self.assertTrue(result["run_id"])
        self.assertEqual(result["fetched"], 2)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["existing"], 1)
        self.assertEqual(result["clustered"], 1)
        self.assertEqual(result["errors"], [])
        self.assertEqual(
            result["sources"],
            [
                {
                    "source": "test_source",
                    "source_name": "Test Source",
                    "status": "success",
                    "fetched": 2,
                    "inserted": 1,
                    "existing": 1,
                }
            ],
        )
        self.assertEqual(source_log["run_id"], result["run_id"])
        self.assertEqual(source_log["source_id"], "test_source")
        self.assertEqual(source_log["source_name"], "Test Source")
        self.assertEqual(source_log["status"], "success")
        self.assertEqual(source_log["fetched"], 2)
        self.assertEqual(source_log["inserted"], 1)
        self.assertEqual(source_log["existing"], 1)
        self.assertEqual(source_log["error"], "")
        self.assertTrue(source_log["started_at"].endswith("+09:00"))
        self.assertTrue(source_log["finished_at"].endswith("+09:00"))
        self.assertTrue(source_log["created_at"].endswith("+09:00"))


if __name__ == "__main__":
    unittest.main()
