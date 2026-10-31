import unittest
from unittest.mock import patch

from newsagent.collectors.rss import RSSCollector
from newsagent.models import Source


def make_collector(url: str = "https://example.com/feed.xml") -> RSSCollector:
    return RSSCollector(
        Source.from_dict(
            {
                "id": "test_rss",
                "name": "Test RSS",
                "kind": "rss",
                "category": "ai",
                "url": url,
            }
        )
    )


def collect(xml_text: str, limit: int = 10):
    with patch("newsagent.collectors.rss.fetch_text", return_value=xml_text):
        return make_collector().collect(limit=limit)


class InlineMarkupTests(unittest.TestCase):
    def test_summary_keeps_text_after_inline_markup(self):
        xml = """<rss><channel><item>
            <title>AI <b>bold</b> title</title>
            <link>https://e.com/1</link>
            <description>Leading text <b>bold</b> trailing text</description>
        </item></channel></rss>"""

        items = collect(xml)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].summary, "Leading text bold trailing text")
        self.assertEqual(items[0].title, "AI bold title")

    def test_atom_xhtml_content_is_not_lost(self):
        xml = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
            <title>Real story title</title>
            <link rel="alternate" href="https://e.com/a/"/>
            <content type="xhtml"><div xmlns="http://www.w3.org/1999/xhtml">
                <p>Body text here</p>
            </div></content>
        </entry></feed>"""

        items = collect(xml)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].summary, "Body text here")


class LinkSelectionTests(unittest.TestCase):
    def test_atom_alternate_link_wins_over_replies_link(self):
        xml = """<feed xmlns="http://www.w3.org/2005/Atom"><entry>
            <title>Real story title</title>
            <link rel="replies" href="https://e.com/a/comments"/>
            <link rel="enclosure" href="https://e.com/a/audio.mp3"/>
            <link rel="alternate" href="https://e.com/a/"/>
        </entry></feed>"""

        items = collect(xml)

        self.assertEqual(items[0].url, "https://e.com/a/")

    def test_guid_permalink_is_used_when_link_is_missing(self):
        xml = """<rss><channel><item>
            <title>Guid only item</title>
            <guid isPermaLink="true">https://e.com/guid-only</guid>
            <description>d</description>
        </item></channel></rss>"""

        items = collect(xml)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].url, "https://e.com/guid-only")

    def test_non_url_guid_is_not_used_as_link(self):
        xml = """<rss><channel><item>
            <title>Non URL guid</title>
            <guid isPermaLink="false">tag:example.com,2026:1</guid>
            <description>d</description>
        </item></channel></rss>"""

        self.assertEqual(collect(xml), [])


class DateParsingTests(unittest.TestCase):
    def test_lowercase_pubdate_is_parsed(self):
        xml = """<rss><channel><item>
            <title>Lowercase date item</title><link>https://e.com/x</link>
            <pubdate>Sun, 12 Jul 2026 01:00:00 GMT</pubdate>
        </item></channel></rss>"""

        items = collect(xml)

        self.assertEqual(items[0].published_at, "2026-07-12T01:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
