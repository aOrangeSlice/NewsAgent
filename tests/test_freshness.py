from datetime import datetime, timezone
import unittest

from newsagent.collectors.market import market_session_state
from newsagent.llm import build_freshness_section
from newsagent.pipeline import filter_recent_news


class MarketSessionStateTests(unittest.TestCase):
    def setUp(self):
        self.meta = {
            "currentTradingPeriod": {
                "pre": {"start": 100},
                "regular": {"start": 200, "end": 300},
                "post": {"end": 400},
            }
        }

    def test_detects_pre_market_and_next_open(self):
        state, next_open = market_session_state(self.meta, now_timestamp=150)
        self.assertEqual(state, "pre_market")
        self.assertTrue(next_open)

    def test_detects_regular_and_after_hours(self):
        self.assertEqual(market_session_state(self.meta, 250)[0], "regular")
        self.assertEqual(market_session_state(self.meta, 350)[0], "after_hours")


class NewsFreshnessTests(unittest.TestCase):
    def test_filters_old_news_but_keeps_market_and_unknown_dates(self):
        now = datetime(2026, 6, 24, 12, tzinfo=timezone.utc)
        stories = [
            {
                "id": 1,
                "category": "world_news",
                "published_at": "2026-06-24T06:00:00+00:00",
                "retrieved_at": "2026-06-24T07:00:00+00:00",
            },
            {
                "id": 2,
                "category": "world_news",
                "published_at": "2026-06-20T06:00:00+00:00",
                "retrieved_at": "2026-06-24T07:00:00+00:00",
            },
            {"id": 3, "category": "market", "published_at": "2026-06-20T06:00:00+00:00"},
            {"id": 4, "category": "ai", "published_at": "", "retrieved_at": ""},
        ]
        result = filter_recent_news(stories, lookback_hours=48, now=now)
        self.assertEqual([story["id"] for story in result], [1, 3, 4])

    def test_invalid_publish_time_falls_back_to_retrieval_time(self):
        now = datetime(2026, 6, 24, 12, tzinfo=timezone.utc)
        stories = [
            {
                "id": 1,
                "category": "world_news",
                "published_at": "not-a-date",
                "retrieved_at": "2026-06-20T07:00:00+00:00",
            }
        ]
        self.assertEqual(filter_recent_news(stories, 48, now), [])


class FreshnessHeaderTests(unittest.TestCase):
    def test_explains_pre_market_close_data(self):
        stories = [
            {
                "category": "market",
                "retrieved_at": "2026-06-24T10:00:00+00:00",
                "metrics": {
                    "symbol": "XLK",
                    "quote_time": "2026-06-23T20:00:00+00:00",
                    "market_state": "pre_market",
                    "price_type": "regular_close",
                    "next_regular_open": "2026-06-24T13:30:00+00:00",
                },
            },
            {
                "category": "world_news",
                "retrieved_at": "2026-06-24T10:30:00+00:00",
                "metrics": {},
            },
        ]
        settings = {"user": {"timezone": "Asia/Tokyo"}}
        header = build_freshness_section(
            stories,
            settings,
            "zh",
            generated_at=datetime(2026, 6, 24, 10, 45, tzinfo=timezone.utc),
        )
        self.assertIn("美股状态：盘前", header)
        self.assertIn("上一常规交易时段收盘价", header)
        self.assertIn("2026-06-24 22:30 Asia/Tokyo", header)


if __name__ == "__main__":
    unittest.main()
