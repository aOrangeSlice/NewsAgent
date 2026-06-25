import sqlite3
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from newsagent.collectors.base import detect_language
from newsagent.db import Database
from newsagent.llm import (
    Summarizer,
    fallback_briefing,
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
