import sqlite3
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from newsagent.collectors.base import detect_language
from newsagent.db import Database
from newsagent.llm import (
    Summarizer,
    answer_cites_known_sources,
    answer_matches_language,
    build_answer_prompt,
    build_answer_regeneration_prompt,
    build_answer_rewrite_prompt,
    enforce_deterministic_market_section,
    fallback_answer,
    fallback_briefing,
    group_market_stories,
    is_valid_generated_briefing,
    normalize_output_language,
    protect_translation_tokens,
    restore_translation_tokens,
    split_markdown_for_translation,
    validate_translation,
)


class OutputLanguageTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "briefing": {"use_llm": False},
            "translation": {"enabled": True},
            "llm": {"base_url": "http://localhost:11434", "model": "test-model"},
            "user": {"timezone": "Asia/Tokyo"},
        }

    def test_normalizes_supported_aliases(self):
        self.assertEqual(normalize_output_language("zh-CN"), "zh")
        self.assertEqual(normalize_output_language("jp"), "ja")
        self.assertEqual(normalize_output_language("source"), "original")
        with self.assertRaises(ValueError):
            normalize_output_language("fr")

    @patch("newsagent.llm.OllamaClient.available", return_value=False)
    def test_translation_falls_back_to_original_content(self, _available):
        output = Summarizer(self.settings).create_briefing([], output_language="zh")
        self.assertEqual(output.translation_status, "fallback_original")
        self.assertEqual(output.generation_mode, "rules")
        self.assertEqual(output.generation_status, "deterministic")
        self.assertTrue(output.canonical_body.startswith("# Daily Brief"))
        self.assertTrue(output.body.startswith("# 每日情报简报"))
        self.assertIn("翻译不可用", output.body)

    @patch("newsagent.llm.OllamaClient.available", return_value=False)
    def test_original_mode_never_translates(self, _available):
        output = Summarizer(self.settings).create_briefing([], output_language="original")
        self.assertEqual(output.translation_status, "not_requested")
        self.assertIn("# Daily Brief", output.body)
        self.assertNotIn("翻译不可用", output.body)

    @patch("newsagent.llm.OllamaClient.generate")
    @patch("newsagent.llm.OllamaClient.available", return_value=True)
    def test_llm_and_rules_generation_are_independent_from_translation(
        self,
        _available,
        generate,
    ):
        generate.side_effect = [
            "# Daily Brief\n\n- [12] LLM summary https://example.com/a",
            "# 每日情报简报\n\n- __NEWSAGENT_PROTECTED_0001__ LLM 摘要 __NEWSAGENT_PROTECTED_0002__",
            "# 每日情报简报\n\n- __NEWSAGENT_PROTECTED_0001__ 规则摘要 __NEWSAGENT_PROTECTED_0002__",
        ]
        summarizer = Summarizer(self.settings)
        llm_output = summarizer.create_briefing(
            [{"id": 12, "title": "Title", "source_urls": ["https://example.com/a"]}],
            output_language="zh",
            use_llm=True,
        )
        rules_body, rules_status = summarizer.translate_briefing(
            "# Daily Brief\n\n- [12] Rule summary https://example.com/a",
            "zh",
        )
        self.assertEqual(llm_output.generation_mode, "llm")
        self.assertEqual(llm_output.generation_status, "generated")
        self.assertEqual(llm_output.translation_status, "translated")
        self.assertEqual(rules_status, "translated")
        self.assertIn("规则摘要", rules_body)

    @patch("newsagent.llm.OllamaClient.generate")
    @patch("newsagent.llm.OllamaClient.available", return_value=True)
    def test_completed_brief_is_translated_only_at_output_stage(
        self,
        _available,
        generate,
    ):
        generate.return_value = (
            "# 每日情报简报\n\n- __NEWSAGENT_PROTECTED_0001__ "
            "标题 __NEWSAGENT_PROTECTED_0002__"
        )
        summarizer = Summarizer(self.settings)
        body, status = summarizer.translate_briefing(
            "# Daily Brief\n\n- [12] Title https://example.com/a",
            "zh",
        )
        self.assertEqual(status, "translated")
        self.assertIn("# 每日情报简报", body)
        self.assertIn("https://example.com/a", body)
        generate.assert_called_once()

    def test_translation_validation_protects_urls_and_story_ids(self):
        source = "- [12] Title https://example.com/a"
        self.assertTrue(validate_translation(source, "- [12] 标题 https://example.com/a"))
        self.assertFalse(validate_translation(source, "- 标题 https://example.com/a"))
        self.assertFalse(validate_translation(source, "- [12] 标题 https://example.com/b"))

    def test_long_markdown_is_split_without_losing_content(self):
        source = "\n\n".join(
            f"- [{index}] Story https://example.com/{index} " + ("x" * 300)
            for index in range(20)
        )
        chunks = split_markdown_for_translation(source, max_chars=1200)
        self.assertGreater(len(chunks), 1)
        self.assertEqual("\n\n".join(chunks), source)
        self.assertTrue(all(len(chunk) <= 1200 for chunk in chunks))

    def test_protected_translation_tokens_round_trip(self):
        source = "- [12] Story https://example.com/a"
        protected, replacements = protect_translation_tokens(source)
        self.assertNotIn("https://example.com/a", protected)
        self.assertNotIn("[12]", protected)
        self.assertEqual(restore_translation_tokens(protected, replacements), source)

    @patch("newsagent.llm.OllamaClient.generate")
    @patch("newsagent.llm.OllamaClient.available", return_value=True)
    def test_long_translation_uses_multiple_llm_chunks(self, _available, generate):
        generate.side_effect = lambda prompt: prompt.split("Markdown:\n", 1)[1]
        summarizer = Summarizer(self.settings)
        source = "\n\n".join(
            f"- [{index}] Story https://example.com/{index} " + ("x" * 300)
            for index in range(12)
        )
        result = summarizer.translate_markdown_in_chunks(
            source,
            "zh",
            max_chars=1000,
        )
        self.assertEqual(result, source)
        self.assertGreater(generate.call_count, 1)

    def test_rules_brief_keeps_inline_source_without_sources_section(self):
        body = fallback_briefing(
            [
                {
                    "id": 12,
                    "title": "AI headline",
                    "summary": "AI summary",
                    "category": "ai",
                    "source_urls": ["https://example.com/a"],
                }
            ],
            "original",
        )

        self.assertIn("Source: https://example.com/a", body)
        self.assertNotIn("## Sources", body)

    def test_market_grouping_keeps_international_sectors_out_of_commodities_fx(self):
        groups = group_market_stories(
            [
                {
                    "id": 1,
                    "title": "Nikkei 225: 100.00 (0.10%)",
                    "category": "market",
                    "region": "global",
                    "source_urls": ["https://finance.yahoo.com/quote/^N225"],
                },
                {
                    "id": 2,
                    "title": "Japan TOPIX Banks ETF: 100.00 (0.20%)",
                    "category": "market",
                    "region": "japan",
                    "source_urls": ["https://finance.yahoo.com/quote/1615.T"],
                },
                {
                    "id": 3,
                    "title": "WTI Crude Oil: 70.00 (-1.00%)",
                    "category": "market",
                    "region": "global",
                    "source_urls": ["https://finance.yahoo.com/quote/CL=F"],
                },
            ]
        )

        self.assertEqual([story["id"] for story in groups["global_indices"]], [1])
        self.assertEqual([story["id"] for story in groups["international_sectors"]["japan"]], [2])
        self.assertEqual([story["id"] for story in groups["commodities_fx"]], [3])

    def test_llm_market_section_is_replaced_with_deterministic_grouping(self):
        body = """# Daily Brief

## Market overview: global indices and sectors

### Commodities and FX
- [2] Japan TOPIX Banks ETF: 100.00 (0.20%) Source: https://finance.yahoo.com/quote/1615.T

## Other section
- Keep this."""
        stories = [
            {
                "id": 1,
                "title": "S&P 500: 100.00 (0.10%)",
                "category": "market",
                "region": "global",
                "source_urls": ["https://finance.yahoo.com/quote/^GSPC"],
            },
            {
                "id": 2,
                "title": "Japan TOPIX Banks ETF: 100.00 (0.20%)",
                "category": "market",
                "region": "japan",
                "source_urls": ["https://finance.yahoo.com/quote/1615.T"],
            },
            {
                "id": 3,
                "title": "WTI Crude Oil: 70.00 (-1.00%)",
                "category": "market",
                "region": "global",
                "source_urls": ["https://finance.yahoo.com/quote/CL=F"],
            },
        ]

        result = enforce_deterministic_market_section(body, stories)

        self.assertIn("### Global indices", result)
        self.assertIn("### International sectors by region", result)
        self.assertIn("#### Japan", result)
        self.assertIn("### Commodities and FX", result)
        self.assertLess(result.index("#### Japan"), result.index("### Commodities and FX"))
        self.assertIn("## Other section", result)

    def test_deterministic_market_section_lists_up_to_ten_global_indices(self):
        symbols = [
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
        stories = [
            {
                "id": 100 + index,
                "title": f"Index {symbol}: 100.00 (0.{index}0%)",
                "category": "market",
                "region": "global",
                "source_urls": [f"https://finance.yahoo.com/quote/{symbol}"],
            }
            for index, symbol in enumerate(symbols, start=1)
        ]

        result = enforce_deterministic_market_section("# Daily Brief\n\n## Other", stories)
        global_section = result.split("### Global indices", 1)[1].split("## Other", 1)[0]

        for symbol in symbols:
            self.assertIn(f"https://finance.yahoo.com/quote/{symbol}", global_section)

    def test_rules_brief_market_section_lists_ten_indices_and_regions(self):
        symbols = [
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
        stories = [
            {
                "id": 100 + index,
                "title": f"Index {symbol}: 100.00 (0.{index}0%)",
                "category": "market",
                "region": "global",
                "source_urls": [f"https://finance.yahoo.com/quote/{symbol}"],
                "tags": [],
            }
            for index, symbol in enumerate(symbols, start=1)
        ] + [
            {
                "id": 300,
                "title": "Japan TOPIX Banks ETF: 100.00 (0.20%)",
                "category": "market",
                "region": "japan",
                "source_urls": ["https://finance.yahoo.com/quote/1615.T"],
                "tags": [],
            },
            {
                "id": 301,
                "title": "WTI Crude Oil: 70.00 (-1.00%)",
                "category": "market",
                "region": "global",
                "source_urls": ["https://finance.yahoo.com/quote/CL=F"],
                "tags": [],
            },
        ]

        body = fallback_briefing(stories, "original")
        global_section = body.split("### Global indices", 1)[1].split("### International sectors", 1)[0]

        for symbol in symbols:
            self.assertIn(f"https://finance.yahoo.com/quote/{symbol}", global_section)
        self.assertIn("#### Japan", body)
        self.assertLess(body.index("#### Japan"), body.index("### Commodities and FX"))

    def test_answer_prompt_uses_strong_target_language_instruction(self):
        prompt = build_answer_prompt("AIインフラの重要ニュースは？", [], "ja")

        self.assertIn("Answer in Japanese only.", prompt)
        self.assertNotIn("Answer in language: ja.", prompt)

    def test_answer_language_detection_flags_mismatch(self):
        self.assertTrue(answer_matches_language("これは日本語の回答です。", "ja"))
        self.assertFalse(answer_matches_language("这是中文回答。", "ja"))

    def test_answer_source_validation_rejects_unknown_urls(self):
        stories = [{"source_urls": ["https://example.com/source"]}]

        self.assertTrue(answer_cites_known_sources("根拠：https://example.com/source", stories))
        self.assertFalse(answer_cites_known_sources("根拠：https://example.com/other", stories))

    def test_generated_briefing_validation_rejects_chatty_non_brief_output(self):
        stories = [{"source_urls": ["https://example.com/source"]}]

        self.assertFalse(
            is_valid_generated_briefing(
                "It looks like you've provided market data.\n\nWould you like a chart?",
                stories,
            )
        )
        self.assertTrue(
            is_valid_generated_briefing(
                "# Daily Brief\n\n- [1] Item Source: https://example.com/source",
                stories,
            )
        )

    def test_answer_rewrite_prompt_requests_target_language_only(self):
        prompt = build_answer_rewrite_prompt("这是中文回答。", "ja")

        self.assertIn("Japanese only", prompt)
        self.assertIn("Do not add new facts.", prompt)

    def test_answer_regeneration_prompt_requires_source_urls(self):
        prompt = build_answer_regeneration_prompt(
            "AIインフラの重要ニュースは？",
            [{"id": 12, "title": "AI chip", "source_urls": ["https://example.com/a"]}],
            "ja",
        )

        self.assertIn("Regenerate a validated answer", prompt)
        self.assertIn("Copy citation URLs exactly from story.source_urls", prompt)
        self.assertIn("https://example.com/a", prompt)

    @patch("newsagent.llm.OllamaClient.generate")
    @patch("newsagent.llm.OllamaClient.available", return_value=True)
    def test_answer_question_rewrites_language_mismatch(self, _available, generate):
        generate.side_effect = [
            "这是中文回答。",
            "これは日本語の回答です。",
        ]

        body = Summarizer(self.settings).answer_question("AIインフラの重要ニュースは？", [], language="ja")

        self.assertEqual(body, "これは日本語の回答です。")
        self.assertEqual(generate.call_count, 2)

    @patch("newsagent.llm.OllamaClient.generate")
    @patch("newsagent.llm.OllamaClient.available", return_value=True)
    def test_answer_question_regenerates_when_sources_fail_validation(self, _available, generate):
        generate.side_effect = [
            "これは日本語ですが、根拠は https://example.com/wrong です。",
            "これは日本語ですが、根拠は https://example.com/wrong です。",
            "これは検証済みの回答です。根拠：https://example.com/a",
        ]

        body = Summarizer(self.settings).answer_question(
            "AIインフラの重要ニュースは？",
            [{"id": 12, "title": "AI chip", "source_urls": ["https://example.com/a"]}],
            language="ja",
        )

        self.assertIn("検証済み", body)
        self.assertEqual(generate.call_count, 3)

    @patch("newsagent.llm.OllamaClient.generate")
    @patch("newsagent.llm.OllamaClient.available", return_value=True)
    def test_answer_validation_failures_are_logged_to_llm_runs(self, _available, generate):
        class FakeDB:
            def __init__(self):
                self.records = []

            def log_llm_run(self, provider, model, ok, error=""):
                self.records.append(
                    {
                        "provider": provider,
                        "model": model,
                        "ok": ok,
                        "error": error,
                    }
                )

        fake_db = FakeDB()
        generate.side_effect = [
            "English answer without a known citation.",
            "English rewrite without a known citation.",
            "English regeneration without a known citation.",
        ]

        Summarizer(self.settings, db=fake_db).answer_question(
            "question",
            [{"id": 12, "title": "AI chip", "source_urls": ["https://example.com/a"]}],
            language="ja",
        )

        validation_errors = [
            record["error"]
            for record in fake_db.records
            if not record["ok"] and "answer validation failed" in record["error"]
        ]
        self.assertEqual(len(validation_errors), 3)
        self.assertTrue(all("target_language=ja" in error for error in validation_errors))

    def test_fallback_answer_honors_japanese_language(self):
        body = fallback_answer(
            "AIインフラの重要ニュースは？",
            [
                {
                    "id": 12,
                    "title": "AI infrastructure headline",
                    "summary": "AI infrastructure summary",
                    "source_urls": ["https://example.com/a"],
                }
            ],
            "ja",
        )

        self.assertTrue(body.startswith("質問：AIインフラの重要ニュースは？"))
        self.assertIn("現在のローカルデータベース", body)
        self.assertIn("フォールバック回答", body)
        self.assertIn("LLM が利用できない", body)
        self.assertNotIn("问题：", body)

    @patch("newsagent.llm.OllamaClient.available", return_value=False)
    def test_answer_question_normalizes_language_aliases(self, _available):
        body = Summarizer(self.settings).answer_question("Any news?", [], language="jp")

        self.assertIn("関連するローカル記録", body)

    def test_detects_common_source_languages(self):
        self.assertEqual(detect_language("English headline"), "en")
        self.assertEqual(detect_language("中文标题"), "zh")
        self.assertEqual(detect_language("日本語のニュース"), "ja")


class BriefingSchemaMigrationTests(unittest.TestCase):
    def test_adds_translation_columns_to_existing_database(self):
        path = Path(__file__).resolve().parent / f"migration_{uuid.uuid4().hex}.db"
        try:
            conn = sqlite3.connect(path)
            conn.execute(
                """
                CREATE TABLE briefings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    language TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    story_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.commit()
            conn.close()

            db = Database(path)
            try:
                db.init()
                columns = {
                    row["name"]
                    for row in db.conn.execute("PRAGMA table_info(briefings)").fetchall()
                }
                self.assertIn("canonical_body", columns)
                self.assertIn("translation_status", columns)
                self.assertIn("translation_model", columns)
                self.assertIn("briefing_group", columns)
                self.assertIn("generation_mode", columns)
                self.assertIn("generation_status", columns)
                self.assertIn("generation_model", columns)
            finally:
                db.close()
        finally:
            for candidate in [path, Path(f"{path}-wal"), Path(f"{path}-shm")]:
                if candidate.exists():
                    candidate.unlink()


if __name__ == "__main__":
    unittest.main()
