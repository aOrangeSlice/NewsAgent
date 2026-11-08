import unittest
from unittest.mock import patch

from newsagent.collectors.html_listing import HTMLListingCollector
from newsagent.collectors.rss import RSSCollector
from newsagent.models import Source
from newsagent.medical import is_medical_content


RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>AI platform launch</title>
      <link>https://example.com/ai</link>
      <description>New developer tooling.</description>
      <pubDate>Sun, 12 Jul 2026 01:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Healthcare AI deployment</title>
      <link>https://example.com/health</link>
      <description>Hospital patient workflow update.</description>
      <pubDate>Sun, 12 Jul 2026 02:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


HTML_FIXTURE = """
<html>
  <body>
    <a href="/news/short">Tiny</a>
    <a href="/news/claude-healthcare">Claude Healthcare Deployment</a>
    <a href="/news/claude-healthcare">Claude Healthcare Deployment</a>
    <a href="https://other.example/news/healthcare">Other Healthcare Story</a>
    <a href="/news/claude-science">Claude Science Workbench for Researchers</a>
    <a href="/news/consumer">Consumer Productivity Update</a>
  </body>
</html>
"""


class CollectorTests(unittest.TestCase):
    @patch("newsagent.collectors.rss.fetch_text", return_value=RSS_FIXTURE)
    def test_mixed_google_feed_routes_healthcare_to_medicine_without_relabeling_all_ai(self, _fetch):
        source = Source.from_dict({
            "id": "google_test", "name": "Google Official", "kind": "rss",
            "category": "ai", "url": "https://example.com/feed.xml",
            "tags": ["ai", "google"], "classify_medical": True,
        })
        items = RSSCollector(source).collect(limit=10)
        self.assertEqual([item.category for item in items], ["ai", "medicine"])
        self.assertNotIn("medicine", items[0].tags)
        self.assertIn("medicine", items[1].tags)
        self.assertIn("google", items[1].tags)
        self.assertEqual(items[1].source_id, source.id)
        self.assertEqual(source.tags, ["ai", "google"])
        self.assertEqual([item.metrics["feed_rank"] for item in items], [1, 2])

    @patch("newsagent.collectors.rss.fetch_text", return_value=RSS_FIXTURE)
    def test_labs_health_filter_scans_past_nonmedical_entries_before_limit(self, _fetch):
        source = Source.from_dict({
            "id": "labs_test", "name": "Labs Health", "kind": "rss",
            "category": "medicine", "url": "https://example.com/feed.xml",
            "medical_only": True,
        })
        items = RSSCollector(source).collect(limit=1)
        self.assertEqual([item.title for item in items], ["Healthcare AI deployment"])
        self.assertEqual(items[0].metrics["feed_rank"], 2)

    def test_medical_content_covers_biopharma_models_and_summary_without_generic_ai(self):
        for title in ["AlphaFold update", "AlphaGenome atlas", "MedGemma release", "AMIE evaluation"]:
            with self.subTest(title=title):
                self.assertTrue(is_medical_content(title))
        self.assertTrue(is_medical_content("New Gemini application", "Clinical evaluation in hospitals"))
        self.assertFalse(is_medical_content("Jamie presents a new AI agent for video editing"))

    @patch("newsagent.collectors.rss.fetch_text", return_value=RSS_FIXTURE)
    def test_rss_without_keyword_filter_keeps_existing_behavior(self, _fetch_text):
        collector = RSSCollector(
            Source.from_dict(
                {
                    "id": "test_rss",
                    "name": "Test RSS",
                    "kind": "rss",
                    "category": "ai",
                    "url": "https://example.com/feed.xml",
                }
            )
        )

        items = collector.collect(limit=10)

        self.assertEqual([item.title for item in items], ["AI platform launch", "Healthcare AI deployment"])
        self.assertEqual([item.metrics["feed_rank"] for item in items], [1, 2])

    @patch("newsagent.collectors.rss.fetch_text", return_value=RSS_FIXTURE)
    def test_rss_include_keywords_filters_items(self, _fetch_text):
        collector = RSSCollector(
            Source.from_dict(
                {
                    "id": "test_health_rss",
                    "name": "Test Health RSS",
                    "kind": "rss",
                    "category": "medicine",
                    "url": "https://example.com/feed.xml",
                    "include_keywords": ["healthcare", "hospital"],
                }
            )
        )

        items = collector.collect(limit=10)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Healthcare AI deployment")
        self.assertEqual(items[0].metrics["feed_rank"], 2)

    @patch("newsagent.collectors.html_listing.fetch_text", return_value=HTML_FIXTURE)
    def test_html_listing_extracts_filters_deduplicates_and_limits_items(self, _fetch_text):
        collector = HTMLListingCollector(
            Source.from_dict(
                {
                    "id": "test_html",
                    "name": "Test HTML",
                    "kind": "html_listing",
                    "category": "ai",
                    "url": "https://www.anthropic.com/news",
                    "allowed_hosts": ["anthropic.com"],
                    "url_prefixes": ["https://www.anthropic.com/news/"],
                    "include_keywords": ["healthcare", "science"],
                    "min_title_length": 12,
                    "summary": "Official listing item.",
                }
            )
        )

        items = collector.collect(limit=1)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Claude Healthcare Deployment")
        self.assertEqual(items[0].url, "https://www.anthropic.com/news/claude-healthcare")
        self.assertEqual(items[0].summary, "Official listing item.")
        self.assertEqual(items[0].metrics, {"feed_rank": 1, "page_rank": 2})


if __name__ == "__main__":
    unittest.main()
