import unittest

from newsagent.pipeline import NewsAgentApp, select_briefing_stories


def story(story_id, category, title, **extra):
    result = {
        "id": story_id,
        "title": title,
        "summary": "",
        "category": category,
        "region": "global",
        "tags": [],
        "source_urls": [f"https://example.com/{story_id}"],
        "published_at": "",
        "retrieved_at": "",
    }
    result.update(extra)
    return result


class FakeDB:
    def __init__(self):
        self.market = [
            story(1, "market", "S&P 500: 100.00 (0.10%)"),
            story(2, "market", "Nasdaq Composite: 200.00 (0.20%)"),
            story(3, "market", "Shanghai Composite: 300.00 (-0.30%)"),
        ]
        self.noisy_market_query = [
            story(100 + index, "world_news", f"Market-adjacent headline {index}")
            for index in range(60)
        ]

    def list_stories_by_category(self, category, limit=20, unique_by_source=False):
        if category == "market":
            return self.market[:limit]
        return []

    def list_stories(self, limit=20, query=""):
        if query == "market stock_index sector oil fx":
            return self.noisy_market_query[:limit]
        return []


class PipelineSelectionTests(unittest.TestCase):
    def test_select_stories_prepends_exact_market_snapshots(self):
        app = NewsAgentApp.__new__(NewsAgentApp)
        app.settings = {"briefing": {"lookback_hours": 0}}
        app.db = FakeDB()

        selected = app._select_stories(65)
        market_titles = [
            item["title"]
            for item in selected
            if item["category"] == "market"
        ]

        self.assertEqual(
            market_titles,
            [
                "S&P 500: 100.00 (0.10%)",
                "Nasdaq Composite: 200.00 (0.20%)",
                "Shanghai Composite: 300.00 (-0.30%)",
            ],
        )

    def test_global_indices_are_prioritized_when_market_slots_are_tight(self):
        global_symbols = [
            "^GSPC",
            "^DJI",
            "^IXIC",
            "000001.SS",
            "^N225",
            "^FTSE",
            "^GDAXI",
            "^FCHI",
            "^HSI",
            "^KS11",
        ]
        sector_symbols = [
            "1615.T",
            "1618.T",
            "1621.T",
            "1624.T",
            "1625.T",
            "1627.T",
            "512010.SS",
            "512480.SS",
        ]
        candidates = [
            story(
                100 + index,
                "market",
                f"Sector {symbol}: 100.00 (1.00%)",
                source_urls=[f"https://finance.yahoo.com/quote/{symbol}"],
            )
            for index, symbol in enumerate(sector_symbols)
        ] + [
            story(
                200 + index,
                "market",
                f"Index {symbol}: 100.00 (0.10%)",
                source_urls=[f"https://finance.yahoo.com/quote/{symbol}"],
            )
            for index, symbol in enumerate(global_symbols)
        ]

        selected = select_briefing_stories(candidates, max_stories=12)
        selected_market_urls = [
            item["source_urls"][0]
            for item in selected
            if item["category"] == "market"
        ]

        self.assertEqual(
            selected_market_urls[:10],
            [f"https://finance.yahoo.com/quote/{symbol}" for symbol in global_symbols],
        )


if __name__ == "__main__":
    unittest.main()
