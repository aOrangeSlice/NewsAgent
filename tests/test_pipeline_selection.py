import unittest
import uuid
from pathlib import Path

from newsagent.db import Database
from newsagent.llm import fallback_briefing
from newsagent.pipeline import NewsAgentApp, expand_query, select_briefing_stories


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

    def list_story_briefing_counts(self):
        return {}


class RepeatHistoryDB:
    def list_stories_by_category(self, category, limit=20, unique_by_source=False):
        return []

    def list_stories(self, limit=20, query=""):
        return [
            story(10, "world_news", "Already repeated", region="europe"),
            story(11, "world_news", "Still eligible", region="europe"),
        ][:limit]

    def list_story_briefing_counts(self):
        return {10: 2, 11: 1}


class RepeatedMarketDB:
    def __init__(self):
        self.market = [
            story(1, "market", "S&P 500: 100.00 (0.10%)"),
            story(2, "market", "Nasdaq Composite: 200.00 (0.20%)"),
        ]

    def list_stories_by_category(self, category, limit=20, unique_by_source=False):
        if category == "market":
            return self.market[:limit]
        return []

    def list_stories(self, limit=20, query=""):
        return []

    def list_story_briefing_counts(self):
        return {1: 99, 2: 99}


class RegionalCandidateDB:
    def __init__(self):
        self.regional = {
            region: [
                story(
                    2000 + index,
                    "world_news",
                    f"Fresh {region} headline",
                    region=region,
                    published_at="2026-08-02T01:00:00+00:00",
                )
            ]
            for index, region in enumerate(["europe", "china", "us", "japan", "korea"])
        }

    def list_stories_by_category_region(self, category, region, limit=100):
        if category != "world_news":
            return []
        return self.regional.get(region, [])[:limit]

    def list_stories_by_category(self, category, limit=20, unique_by_source=False):
        return []

    def list_stories(self, limit=20, query=""):
        return []

    def list_story_briefing_counts(self):
        return {}


class PipelineSelectionTests(unittest.TestCase):
    def test_daily_selection_recalls_each_world_region_before_global_ranking(self):
        app = NewsAgentApp.__new__(NewsAgentApp)
        app.settings = {
            "briefing": {
                "lookback_hours": 0,
                "regional_candidate_limit": 100,
                "world_region_limit": 5,
                "world_region_minimum": 1,
            }
        }
        app.db = RegionalCandidateDB()

        selected = app._select_stories(90)
        selected_regions = {
            item["region"]
            for item in selected
            if item["category"] == "world_news"
        }

        self.assertEqual(
            selected_regions,
            {"europe", "china", "us", "japan", "korea"},
        )

    def test_each_region_uses_recent_repeat_fallback_when_no_fresh_story_exists(self):
        regions = ["europe", "china", "us", "japan", "korea"]
        candidates = [
            story(
                2100 + index,
                "world_news",
                f"Repeated {region} headline",
                region=region,
                published_at="2026-08-02T01:00:00+00:00",
            )
            for index, region in enumerate(regions)
        ]

        selected = select_briefing_stories(
            candidates,
            max_stories=90,
            story_briefing_counts={item["id"]: 2 for item in candidates},
            repeat_backfill_limit=5,
        )
        body = fallback_briefing(selected, "original")
        regional_section = body.split(
            "## Important mainstream news by region — Top 5",
            1,
        )[1].split("## Selection logic", 1)[0]

        self.assertEqual(
            {item["region"] for item in selected},
            set(regions),
        )
        self.assertTrue(all(item.get("regional_repeat_fallback") for item in selected))
        for label in ["Europe", "China", "United States", "Japan", "South Korea"]:
            self.assertIn(f"### {label}", regional_section)

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

    def test_recent_world_news_beats_older_high_score_items(self):
        candidates = [
            story(
                1,
                "world_news",
                "Old high score",
                region="europe",
                score=120,
                published_at="2026-06-25T00:00:00+00:00",
            ),
            story(
                2,
                "world_news",
                "Fresh lower score",
                region="europe",
                score=90,
                published_at="2026-06-27T00:00:00+00:00",
            ),
        ]

        selected = select_briefing_stories(candidates, max_stories=5)

        self.assertEqual(selected[0]["id"], 2)

    def test_non_news_categories_keep_reserved_daily_slots(self):
        candidates = [
            story(
                100 + index,
                "market",
                f"Market {index}: 100.00 (0.10%)",
                source_urls=[f"https://finance.yahoo.com/quote/M{index}"],
            )
            for index in range(45)
        ] + [
            story(
                200 + index,
                "world_news",
                f"World {index}",
                region="europe",
                published_at=f"2026-06-27T00:{index:02d}:00+00:00",
            )
            for index in range(25)
        ] + [
            story(300, "medicine", "Medical item", published_at="2026-06-27T01:00:00+00:00"),
            story(301, "ai", "AI item", published_at="2026-06-27T01:00:00+00:00"),
        ]

        selected = select_briefing_stories(candidates, max_stories=65)
        selected_categories = {item["category"] for item in selected}

        self.assertIn("medicine", selected_categories)
        self.assertIn("ai", selected_categories)

    def test_medical_slots_prioritize_authoritative_multi_source_and_focus_terms(self):
        candidates = [
            story(
                400,
                "medicine",
                "Fresh weak single-source medical item",
                score=999,
                published_at="2026-06-27T03:00:00+00:00",
                source_urls=["https://example.com/medical/400"],
            ),
            story(
                401,
                "medicine",
                "Nature Medicine clinical study",
                score=20,
                published_at="2026-06-27T00:00:00+00:00",
                tags=["medicine", "journal", "nature_medicine"],
                source_urls=["https://www.nature.com/articles/s41591-026-00001-1"],
            ),
            story(
                402,
                "medicine",
                "Multi-source health evidence update",
                score=20,
                published_at="2026-06-27T01:00:00+00:00",
                source_urls=[
                    "https://source-a.example/health/402",
                    "https://source-b.example/health/402",
                ],
            ),
            story(
                403,
                "medicine",
                "AI cognition and nervous system rehabilitation study",
                score=20,
                published_at="2026-06-27T02:00:00+00:00",
                source_urls=["https://example.com/medical/403"],
            ),
        ]

        selected = select_briefing_stories(candidates, max_stories=65)
        selected_ids = [item["id"] for item in selected]

        self.assertLess(selected_ids.index(401), selected_ids.index(400))
        self.assertLess(selected_ids.index(402), selected_ids.index(400))
        self.assertLess(selected_ids.index(403), selected_ids.index(400))

    def test_medical_slots_prioritize_llm_and_machine_learning_terms(self):
        candidates = [
            story(
                410,
                "medicine",
                "General healthcare operations update",
                score=999,
                published_at="2026-06-27T03:00:00+00:00",
            ),
            story(
                411,
                "medicine",
                "LLM framework for clinical decision support",
                score=20,
                published_at="2026-06-27T01:00:00+00:00",
            ),
            story(
                412,
                "medicine",
                "Causal machine learning for treatment planning",
                score=20,
                published_at="2026-06-27T02:00:00+00:00",
            ),
        ]

        selected = select_briefing_stories(candidates, max_stories=65)
        selected_ids = [item["id"] for item in selected]

        self.assertLess(selected_ids.index(411), selected_ids.index(410))
        self.assertLess(selected_ids.index(412), selected_ids.index(410))

    def test_llm_focus_term_expands_question_query(self):
        expanded = expand_query("latest LLM clinical research")

        self.assertIn("medicine", expanded)
        self.assertIn("large language model", expanded)

    def test_medical_slots_give_ai_agent_terms_extra_priority(self):
        candidates = [
            story(
                420,
                "medicine",
                "Artificial intelligence for clinical decision support",
                score=999,
                published_at="2026-06-27T03:00:00+00:00",
            ),
            story(
                421,
                "medicine",
                "Multi-agent framework for clinical decision support",
                score=20,
                published_at="2026-06-27T01:00:00+00:00",
            ),
        ]

        selected = select_briefing_stories(candidates, max_stories=65)
        selected_ids = [item["id"] for item in selected]

        self.assertLess(selected_ids.index(421), selected_ids.index(420))

    def test_ai_agent_focus_term_expands_question_query(self):
        expanded = expand_query("latest agentic AI research")

        self.assertIn("medicine", expanded)
        self.assertIn("ai agents", expanded)

    def test_medical_focus_terms_expand_question_query(self):
        expanded = expand_query("神经系统 AI 和认知研究")

        self.assertIn("medicine", expanded)
        self.assertIn("nervous system", expanded)
        self.assertIn("认知", expanded)

    def test_story_briefing_counts_deduplicate_same_briefing_group(self):
        path = Path(__file__).resolve().parent / f"briefing_counts_{uuid.uuid4().hex}.db"
        db = Database(path)
        try:
            db.init()
            db.save_briefing(
                language="zh",
                title="Rules",
                body="body",
                story_ids=[1, 2],
                briefing_group="daily-1",
                generation_mode="rules",
            )
            db.save_briefing(
                language="zh",
                title="LLM",
                body="body",
                story_ids=[1, 2],
                briefing_group="daily-1",
                generation_mode="llm",
            )
            db.save_briefing(
                language="zh",
                title="Rules",
                body="body",
                story_ids=[1],
                briefing_group="daily-2",
                generation_mode="rules",
            )

            self.assertEqual(db.list_story_briefing_counts(), {1: 2, 2: 1})
        finally:
            db.close()
            path.unlink(missing_ok=True)

    def test_select_stories_backfills_repeated_items_after_fresh_items(self):
        app = NewsAgentApp.__new__(NewsAgentApp)
        app.settings = {"briefing": {"lookback_hours": 0}}
        app.db = RepeatHistoryDB()

        selected = app._select_stories(10)
        selected_ids = [item["id"] for item in selected]

        self.assertEqual(selected_ids, [11, 10])

    def test_repeated_market_items_are_kept_and_render_market_overview(self):
        app = NewsAgentApp.__new__(NewsAgentApp)
        app.settings = {"briefing": {"lookback_hours": 0}}
        app.db = RepeatedMarketDB()

        selected = app._select_stories(10)
        selected_ids = [item["id"] for item in selected]
        body = fallback_briefing(selected, "original")

        self.assertEqual(selected_ids, [1, 2])
        self.assertIn("## Market overview: global indices and sectors", body)
        self.assertIn("S&P 500: 100.00 (0.10%)", body)

    def test_repeated_non_market_items_do_not_enter_fresh_top_slots_when_fresh_is_enough(self):
        candidates = [
            story(
                1200,
                "world_news",
                "Repeated high-score Europe story",
                region="europe",
                score=999,
                published_at="2026-06-27T05:00:00+00:00",
            )
        ] + [
            story(
                1201 + index,
                "world_news",
                f"Fresh Europe story {index}",
                region="europe",
                score=10,
                published_at=f"2026-06-27T0{index}:00:00+00:00",
            )
            for index in range(5)
        ]

        selected = select_briefing_stories(
            candidates,
            max_stories=5,
            story_briefing_counts={1200: 2},
        )

        self.assertNotIn(1200, [item["id"] for item in selected])

    def test_repeated_world_backfill_is_not_rendered_in_regional_top_five(self):
        candidates = [
            story(1250, "world_news", "Fresh Europe story", region="europe"),
            story(1251, "world_news", "Repeated Europe story", region="europe"),
        ]

        selected = select_briefing_stories(
            candidates,
            max_stories=2,
            story_briefing_counts={1251: 2},
        )
        body = fallback_briefing(selected, "original")
        regional_section = body.split("## Important mainstream news by region — Top 5", 1)[1].split(
            "## Earlier coverage / repeat backfill", 1
        )[0]

        self.assertIn("Fresh Europe story", regional_section)
        self.assertNotIn("Repeated Europe story", regional_section)
        self.assertIn("## Earlier coverage / repeat backfill", body)
        self.assertIn("Repeated Europe story", body)

    def test_repeated_backfill_is_limited_to_five_items(self):
        candidates = [
            story(
                1300 + index,
                "world_news",
                f"Repeated story {index}",
                region="europe",
                score=100 - index,
                published_at=f"2026-06-27T0{index}:00:00+00:00",
            )
            for index in range(8)
        ]
        counts = {item["id"]: 2 for item in candidates}

        selected = select_briefing_stories(
            candidates,
            max_stories=10,
            story_briefing_counts=counts,
            repeat_backfill_limit=5,
        )

        self.assertEqual(len(selected), 5)
        self.assertTrue(all(counts[item["id"]] >= 2 for item in selected))

    def test_medical_section_diversifies_sources_when_possible(self):
        candidates = [
            story(
                500 + index,
                "medicine",
                f"AI cognition study {index}",
                published_at=f"2026-06-27T0{index}:00:00+00:00",
                source_urls=[f"https://source-a.example/medical/{index}"],
            )
            for index in range(5)
        ] + [
            story(
                600,
                "medicine",
                "AI cognition study from another source",
                published_at="2026-06-27T00:30:00+00:00",
                source_urls=["https://source-b.example/medical/600"],
            )
        ]

        selected = select_briefing_stories(candidates, max_stories=65)
        medical_urls = [item["source_urls"][0] for item in selected[:5]]

        self.assertTrue(any("source-b.example" in url for url in medical_urls))

    def test_medical_section_renders_up_to_ten_items_when_only_one_source_is_available(self):
        candidates = [
            story(
                700 + index,
                "medicine",
                f"AI cognition single-source study {index}",
                published_at=f"2026-06-27T0{index}:00:00+00:00",
                source_urls=[f"https://source-a.example/medical/{index}"],
            )
            for index in range(11)
        ]

        selected = select_briefing_stories(candidates, max_stories=75)
        body = fallback_briefing(selected, "original")
        medical_section = body.split("## Medical and health — Top 10", 1)[1].split("## Selection logic", 1)[0]

        self.assertEqual(len([item for item in selected[:10] if item["category"] == "medicine"]), 10)
        self.assertEqual(medical_section.count("- ["), 10)

    def test_ai_section_diversifies_sources_when_possible(self):
        candidates = [
            story(
                800 + index,
                "ai",
                f"AI platform story {index}",
                published_at=f"2026-06-27T0{index}:00:00+00:00",
                source_urls=[f"https://source-a.example/ai/{index}"],
            )
            for index in range(5)
        ] + [
            story(
                900,
                "ai",
                "AI platform story from another source",
                published_at="2026-06-27T00:30:00+00:00",
                source_urls=["https://source-b.example/ai/900"],
            )
        ]

        selected = select_briefing_stories(candidates, max_stories=65)
        ai_urls = [item["source_urls"][0] for item in selected[:5]]

        self.assertTrue(any("source-b.example" in url for url in ai_urls))

    def test_fallback_ai_section_rotates_sources_and_limits_to_ten_items(self):
        stories = [
            story(
                950 + index,
                "ai",
                f"AI platform story {index}",
                source_urls=[f"https://source-a.example/ai/{index}"],
            )
            for index in range(10)
        ] + [
            story(
                960,
                "ai",
                "AI platform story from another source",
                source_urls=["https://source-b.example/ai/960"],
            )
        ]

        body = fallback_briefing(stories, "original")
        ai_section = body.split("## AI and technology — Top 10", 1)[1].split("## Selection logic", 1)[0]

        self.assertEqual(ai_section.count("- ["), 10)
        self.assertIn("source-b.example", ai_section)
        self.assertLess(
            ai_section.index("source-b.example"),
            ai_section.index("source-a.example/ai/1"),
        )

    def test_world_region_section_diversifies_sources_when_possible(self):
        candidates = [
            story(
                1000 + index,
                "world_news",
                f"Europe story {index}",
                region="europe",
                published_at=f"2026-06-27T0{index}:00:00+00:00",
                source_urls=[f"https://source-a.example/world/{index}"],
            )
            for index in range(5)
        ] + [
            story(
                1100,
                "world_news",
                "Europe story from another source",
                region="europe",
                published_at="2026-06-27T00:30:00+00:00",
                source_urls=["https://source-b.example/world/1100"],
            )
        ]

        selected = select_briefing_stories(candidates, max_stories=65)
        world_urls = [item["source_urls"][0] for item in selected[:5]]

        self.assertTrue(any("source-b.example" in url for url in world_urls))


if __name__ == "__main__":
    unittest.main()
