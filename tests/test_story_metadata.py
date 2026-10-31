import tempfile
import unittest
from pathlib import Path

import newsagent.db as db_module
from newsagent.db import Database
from newsagent.medical import MEDICAL_RECALL_QUERY
from newsagent.models import NewsItem


def news_item(title: str, url: str, source_id: str = "s1", published_at: str = "", retrieved_at: str = "") -> NewsItem:
    return NewsItem(
        source_id=source_id,
        source_name=source_id.upper(),
        category="world_news",
        subcategory="",
        region="europe",
        title=title,
        url=url,
        summary="",
        published_at=published_at or "2026-01-01T00:30:00+09:00",
        retrieved_at=retrieved_at or "2026-01-01T01:00:00+09:00",
    )


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(Path(self.tmp.name) / "newsagent.db")
        self.db.init()
        self.addCleanup(self.db.close)
        self.original_clock = db_module.tokyo_now_iso

    def tearDown(self):
        db_module.tokyo_now_iso = self.original_clock

    def set_clock(self, value: str) -> None:
        db_module.tokyo_now_iso = lambda: value

    def cluster_all(self, score: float = 40.0) -> None:
        for row in self.db.get_unclustered_raw_items():
            self.db.upsert_story_from_raw(row, score)
        self.db.commit()


class StoryMetadataTests(DatabaseTestCase):
    def test_story_published_at_tracks_the_newest_article(self):
        self.set_clock("2026-01-01T01:00:00+09:00")
        self.db.insert_raw_item(
            news_item(
                "Same Headline",
                "https://a.example/1",
                published_at="2026-01-01T00:30:00+09:00",
                retrieved_at="2026-01-01T01:00:00+09:00",
            )
        )
        self.cluster_all()

        self.set_clock("2026-01-01T02:00:00+09:00")
        self.db.insert_raw_item(
            news_item(
                "Same Headline",
                "https://b.example/1",
                source_id="s2",
                published_at="2025-01-05T00:00:00+09:00",
                retrieved_at="2026-01-01T02:00:00+09:00",
            )
        )
        self.cluster_all()

        story = self.db.list_stories_by_category("world_news", limit=5)[0]

        # The newest *publication* wins, not the item that was merely fetched last.
        self.assertEqual(story["published_at"], "2026-01-01T00:30:00+09:00")
        self.assertEqual(story["retrieved_at"], "2026-01-01T02:00:00+09:00")
        self.assertEqual(len(story["item_ids"]), 2)

    def test_same_second_item_is_not_orphaned(self):
        self.set_clock("2026-01-01T09:00:00+09:00")
        self.db.insert_raw_item(
            news_item(
                "Same Headline",
                "https://a.example/1",
                published_at="2026-01-01T09:00:00+09:00",
                retrieved_at="2026-01-01T09:00:00+09:00",
            )
        )
        self.cluster_all()

        self.db.insert_raw_item(
            news_item(
                "Same Headline",
                "https://b.example/1",
                source_id="s2",
                published_at="2026-01-01T09:00:00+09:00",
                retrieved_at="2026-01-01T09:00:00+09:00",
            )
        )

        pending = self.db.get_unclustered_raw_items()
        # The first item comes back too: its retrieved_at equals the story's
        # updated_at to the second, and merging is idempotent.
        self.assertIn("https://b.example/1", {row["url"] for row in pending})

        self.cluster_all()
        story = self.db.list_stories_by_category("world_news", limit=5)[0]
        self.assertEqual(len(story["item_ids"]), 2)
        self.assertEqual(len(story["source_urls"]), 2)

    def test_reclustering_is_idempotent(self):
        self.set_clock("2026-01-01T09:00:00+09:00")
        self.db.insert_raw_item(
            news_item(
                "Same Headline",
                "https://a.example/1",
                published_at="2026-01-01T09:00:00+09:00",
                retrieved_at="2026-01-01T09:00:00+09:00",
            )
        )
        for _ in range(3):
            self.cluster_all()
            story = self.db.list_stories_by_category("world_news", limit=5)[0]
            self.assertEqual(story["item_ids"], [1])
            self.assertEqual(story["source_urls"], ["https://a.example/1"])


class RecallQueryTests(DatabaseTestCase):
    def test_long_recall_query_is_not_truncated(self):
        self.db.conn.execute(
            """
            INSERT INTO story_clusters
                (cluster_key, title, summary, category, subcategory, region, score,
                 source_urls_json, item_ids_json, tags_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "k1",
                "Neural implant restores motor function in trial",
                "",
                "medicine",
                "",
                "global",
                10.0,
                '["https://x.example/1"]',
                "[]",
                "[]",
                "2026-01-01T00:00:00+09:00",
                "2026-01-01T00:00:00+09:00",
            ),
        )
        self.db.commit()

        self.assertGreater(len(MEDICAL_RECALL_QUERY.split()), 12)
        self.assertEqual(len(self.db.list_stories(limit=5, query=MEDICAL_RECALL_QUERY)), 1)
        self.assertEqual(len(self.db.list_stories(limit=5, query="neural motor")), 1)


class UniqueBySourceTests(DatabaseTestCase):
    def test_unique_by_source_keys_on_the_outlet(self):
        for index in range(3):
            self.db.insert_raw_item(
                news_item(f"Story {index}", f"https://same.example/article{index}", source_id="same")
            )
        self.cluster_all(score=10.0)

        self.assertEqual(len(self.db.list_stories_by_category("world_news", limit=10)), 3)
        self.assertEqual(
            len(self.db.list_stories_by_category("world_news", limit=10, unique_by_source=True)),
            1,
        )


if __name__ == "__main__":
    unittest.main()
