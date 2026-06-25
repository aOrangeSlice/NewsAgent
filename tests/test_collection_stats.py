from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

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
        finally:
            app.db.close()
            self.remove_db_files(path)

        self.assertEqual(result["fetched"], 2)
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["existing"], 1)
        self.assertEqual(result["clustered"], 1)
        self.assertEqual(result["errors"], [])


if __name__ == "__main__":
    unittest.main()
