import json
import tempfile
import unittest
from pathlib import Path

from newsagent.config import ROOT
from newsagent.db import DEFAULT_RETENTION, RETENTION_WINDOWS, Database, retention_cutoff
from newsagent.models import TOKYO_TZ, NewsItem
from datetime import datetime


def insert_old_row(db: Database, table: str, columns: dict) -> None:
    names = ", ".join(columns)
    placeholders = ", ".join("?" for _ in columns)
    db.conn.execute(f"INSERT INTO {table} ({names}) VALUES ({placeholders})", tuple(columns.values()))
    db.conn.commit()


class RetentionWindowTests(unittest.TestCase):
    def test_defaults_match_the_documented_example_settings(self):
        example = json.loads((ROOT / "config" / "settings.example.json").read_text(encoding="utf-8"))

        self.assertEqual(example["retention"], DEFAULT_RETENTION)
        self.assertEqual(set(DEFAULT_RETENTION), {window[0] for window in RETENTION_WINDOWS})

    def test_cutoff_rejects_zero_and_negative_days(self):
        for bad in (0, -5):
            with self.subTest(days=bad), self.assertRaises(ValueError) as ctx:
                retention_cutoff("raw_item_retention_days", bad)
            self.assertIn("raw_item_retention_days", str(ctx.exception))

    def test_cutoff_rejects_non_numeric(self):
        with self.assertRaises(ValueError):
            retention_cutoff("story_retention_days", "ninety")

    def test_cutoff_is_tokyo_iso_with_seconds(self):
        now = datetime(2026, 7, 12, 3, 4, 5, tzinfo=TOKYO_TZ)

        self.assertEqual(retention_cutoff("raw_item_retention_days", 10, now=now), "2026-07-02T03:04:05+09:00")


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(Path(self.tmp.name) / "newsagent.db")
        self.db.init()
        self.addCleanup(self.db.close)

    def seed(self) -> None:
        old = "2020-01-01T00:00:00+09:00"
        fresh = "2099-01-01T00:00:00+09:00"
        for category, url in (("world_news", "https://a.example/old"), ("market", "https://a.example/quote")):
            self.db.insert_raw_item(
                NewsItem(
                    source_id="s1",
                    source_name="S1",
                    category=category,
                    subcategory="",
                    region="global",
                    title=f"{category} item",
                    url=url,
                    retrieved_at=old,
                )
            )
        self.db.insert_raw_item(
            NewsItem(
                source_id="s1",
                source_name="S1",
                category="world_news",
                subcategory="",
                region="global",
                title="fresh item",
                url="https://a.example/fresh",
                retrieved_at=fresh,
            )
        )
        insert_old_row(
            self.db,
            "story_clusters",
            {
                "cluster_key": "k",
                "title": "Old story",
                "category": "world_news",
                "score": 1.0,
                "source_urls_json": "[]",
                "item_ids_json": "[]",
                "tags_json": "[]",
                "created_at": old,
                "updated_at": old,
            },
        )
        insert_old_row(
            self.db,
            "briefings",
            {
                "language": "original",
                "title": "Old brief",
                "body": "body",
                "story_ids_json": "[]",
                "created_at": old,
            },
        )
        insert_old_row(
            self.db,
            "llm_runs",
            {"provider": "ollama", "model": "m", "ok": 1, "created_at": old},
        )
        insert_old_row(
            self.db,
            "pipeline_logs",
            {"run_id": "r", "level": "INFO", "event": "e", "message_json": "{}", "created_at": old},
        )
        insert_old_row(
            self.db,
            "delivery_logs",
            {"channel": "email", "status": "ok", "created_at": old},
        )
        insert_old_row(
            self.db,
            "source_collection_logs",
            {
                "run_id": "r",
                "source_id": "s1",
                "source_name": "S1",
                "status": "success",
                "fetched": 1,
                "inserted": 1,
                "existing": 0,
                "started_at": old,
                "finished_at": old,
                "created_at": old,
            },
        )

    def row_count(self, table: str) -> int:
        return self.db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def test_dry_run_reports_without_deleting(self):
        self.seed()
        before = {table: self.row_count(table) for table in ("raw_items", "story_clusters", "briefings", "llm_runs")}

        counts = self.db.prune(DEFAULT_RETENTION, dry_run=True)

        self.assertGreater(sum(counts.values()), 0)
        self.assertEqual(
            before,
            {table: self.row_count(table) for table in ("raw_items", "story_clusters", "briefings", "llm_runs")},
        )

    def test_prune_deletes_only_rows_past_their_window(self):
        self.seed()

        counts = self.db.prune(DEFAULT_RETENTION)

        self.assertEqual(counts["raw_items:raw_item_retention_days"], 1)
        self.assertEqual(counts["raw_items:market_snapshot_retention_days"], 1)
        self.assertEqual(counts["story_clusters:story_retention_days"], 1)
        self.assertEqual(counts["briefings:briefing_retention_days"], 1)
        self.assertEqual(counts["llm_runs:llm_run_retention_days"], 1)
        self.assertEqual(counts["pipeline_logs:pipeline_log_retention_days"], 1)
        self.assertEqual(counts["delivery_logs:delivery_log_retention_days"], 1)
        self.assertEqual(counts["source_collection_logs:source_collection_log_retention_days"], 1)

        remaining_urls = {row["url"] for row in self.db.conn.execute("SELECT url FROM raw_items")}
        self.assertEqual(remaining_urls, {"https://a.example/fresh"})
        self.assertEqual(self.row_count("story_clusters"), 0)
        self.assertEqual(self.row_count("briefings"), 0)

    def test_market_and_news_windows_are_independent(self):
        self.seed()

        counts = self.db.prune({"market_snapshot_retention_days": 1095})

        self.assertEqual(list(counts), ["raw_items:market_snapshot_retention_days"])
        self.assertEqual(self.row_count("raw_items"), 2)

    def test_partial_retention_block_is_accepted(self):
        self.seed()

        counts = self.db.prune({})

        self.assertEqual(counts, {})
        self.assertEqual(self.row_count("raw_items"), 3)

    def test_invalid_window_after_a_valid_one_does_not_delete(self):
        self.seed()

        with self.assertRaises(ValueError):
            self.db.prune(
                {
                    "market_snapshot_retention_days": 1095,
                    "raw_item_retention_days": 0,
                }
            )

        # The first window committed nothing: prune only commits at the end.
        self.assertEqual(self.row_count("raw_items"), 3)


if __name__ == "__main__":
    unittest.main()
