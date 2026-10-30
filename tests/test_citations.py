import unittest

from newsagent.llm import (
    answer_cites_known_sources,
    build_answer_rewrite_prompt,
    build_briefing_prompt,
    build_translation_prompt,
    enforce_deterministic_market_section,
    extract_cited_urls,
    known_source_urls,
    localize_briefing_structure,
    normalize_cited_url,
    source_domain,
    unique_by_source,
    unknown_cited_urls,
)


def story(story_id: int, title: str = "Story", urls: list[str] | None = None, category: str = "world_news"):
    return {
        "id": story_id,
        "title": title,
        "summary": "",
        "category": category,
        "region": "europe",
        "tags": [],
        "source_urls": urls if urls is not None else [f"https://example.com/{story_id}"],
        "published_at": "2026-07-12T01:00:00+00:00",
        "retrieved_at": "2026-07-12T01:00:00+00:00",
    }


PAREN_URL = "https://en.wikipedia.org/wiki/Apple_(company)"


class CitationTests(unittest.TestCase):
    def test_parenthesised_source_url_is_recognised(self):
        stories = [story(1, urls=[PAREN_URL])]

        self.assertIn(PAREN_URL, known_source_urls(stories))
        self.assertEqual(extract_cited_urls(f"see {PAREN_URL}"), [PAREN_URL])
        self.assertTrue(answer_cites_known_sources(f"Fact. Source: {PAREN_URL}", stories))
        self.assertEqual(unknown_cited_urls(f"see {PAREN_URL}", stories), [])

    def test_markdown_link_url_is_extracted_cleanly(self):
        self.assertEqual(
            extract_cited_urls("[label](https://example.com/a)"),
            ["https://example.com/a"],
        )

    def test_trailing_sentence_punctuation_is_dropped(self):
        self.assertEqual(normalize_cited_url("https://example.com/a."), "https://example.com/a")
        self.assertEqual(normalize_cited_url("https://example.com/a),"), "https://example.com/a")

    def test_real_hallucination_is_still_flagged(self):
        stories = [story(1, urls=["https://example.com/a"])]

        self.assertEqual(
            unknown_cited_urls("see https://invented.example/b", stories),
            ["https://invented.example/b"],
        )


class MarketSectionTests(unittest.TestCase):
    market_stories = [
        {
            "id": 7,
            "title": "XLE: 90.1000 (1.50%)",
            "category": "market",
            "summary": "ETF",
            "source_urls": ["https://finance.yahoo.com/quote/XLE"],
        }
    ]

    def test_prompt_heading_matches_deterministic_heading(self):
        prompt = build_briefing_prompt(self.market_stories, "original")

        self.assertIn("## Market overview: global indices and sectors", prompt)
        self.assertNotIn("## Financial market signals and insights", prompt)

    def test_model_market_section_is_replaced_not_duplicated(self):
        for heading in (
            "## Market overview: global indices and sectors",
            "## Financial market signals and insights",
        ):
            with self.subTest(heading=heading):
                body = f"# Daily Brief\n\n{heading}\n- XLE up 1.5%\n\n## Selection logic\n- x"

                result = enforce_deterministic_market_section(body, self.market_stories)

                self.assertEqual(result.count("## Market overview: global indices and sectors"), 1)
                self.assertNotIn("Financial market signals", result)
                self.assertNotIn("XLE up 1.5%", result)


class LocalizationTests(unittest.TestCase):
    def test_deep_heading_replacement_wins_over_shallow_prefix(self):
        self.assertEqual(localize_briefing_structure("#### China A-shares", "zh"), "#### 中国A股")
        self.assertEqual(localize_briefing_structure("#### China A-shares", "ja"), "#### 中国A株")

    def test_shallow_heading_still_translates(self):
        self.assertEqual(localize_briefing_structure("### China", "zh"), "### 中国")

    def test_catch_all_heading_is_localized(self):
        self.assertEqual(localize_briefing_structure("## Other selected stories", "zh"), "## 其他入选内容")


class SourceKeyTests(unittest.TestCase):
    def test_url_less_stories_are_not_collapsed(self):
        stories = [story(1, "A", urls=[]), story(2, "B", urls=[])]

        self.assertEqual(len(unique_by_source(stories)), 2)
        self.assertEqual(source_domain(stories[0]), "1")
        self.assertEqual(source_domain(stories[1]), "2")

    def test_same_url_is_still_deduplicated(self):
        stories = [story(1, urls=["https://a.example/x"]), story(2, urls=["https://a.example/x"])]

        self.assertEqual(len(unique_by_source(stories)), 1)


class LanguagePromptTests(unittest.TestCase):
    def test_original_language_raises_named_error(self):
        with self.assertRaises(ValueError):
            build_translation_prompt("markdown", "original")
        with self.assertRaises(ValueError):
            build_answer_rewrite_prompt("answer", "original")

    def test_supported_languages_still_build(self):
        self.assertIn("Simplified Chinese", build_translation_prompt("markdown", "zh"))
        self.assertIn("Japanese", build_answer_rewrite_prompt("answer", "ja"))


if __name__ == "__main__":
    unittest.main()
