import unittest

from newsagent.llm import fallback_briefing
from newsagent.ranking import keyword_importance_score
from newsagent.pipeline import select_briefing_stories


def story(story_id, category="market", region="global", title=None, urls=None, published_at="2026-07-12T01:00:00+00:00"):
    return {
        "id": story_id,
        "title": title or f"{category.title()} story {story_id}",
        "summary": "",
        "category": category,
        "region": region,
        "tags": [],
        "source_urls": urls if urls is not None else [f"https://source{story_id}.example/{story_id}"],
        "published_at": published_at,
        "retrieved_at": published_at,
    }


class KeywordBoundaryTests(unittest.TestCase):
    def test_two_letter_terms_do_not_match_inside_words(self):
        # "ai" in "said", "war" in "software", "who" in "whole"
        self.assertEqual(keyword_importance_score("Software update said the whole email chain", ""), 0)

    def test_real_terms_still_score(self):
        self.assertEqual(keyword_importance_score("Nvidia chip earnings", ""), 9)

    def test_standalone_short_term_matches(self):
        self.assertGreaterEqual(keyword_importance_score("AI regulation draft", ""), 6)


class CatchAllSectionTests(unittest.TestCase):
    def test_selected_policy_stories_are_rendered(self):
        candidates = (
            [story(index) for index in range(50)]
            + [story(100 + index, "policy") for index in range(20)]
        )

        selected = select_briefing_stories(candidates, max_stories=90)
        body = fallback_briefing(selected, "original")

        rendered = [item for item in selected if item["title"] in body]
        self.assertEqual(len(rendered), len(selected))
        self.assertIn("## Other selected stories", body)
        self.assertEqual(
            [item["title"] for item in selected if item["category"] == "policy" and item["title"] not in body],
            [],
        )

    def test_catch_all_section_is_absent_when_everything_renders(self):
        body = fallback_briefing([story(1, "market")], "original")

        self.assertNotIn("## Other selected stories", body)

    def test_chinese_edition_uses_the_localized_heading(self):
        body = fallback_briefing([story(1, "policy")], "zh")

        self.assertIn("## 其他入选内容", body)

    def test_stale_pages_are_not_resurrected_by_the_catch_all(self):
        stale = story(
            5,
            "world_news",
            "europe",
            title="Old China Daily page",
            urls=["https://chinadaily.com.cn/a/2017/0101/WS1.html"],
        )

        body = fallback_briefing([stale], "original")

        self.assertNotIn("Old China Daily page", body)


class FillPassStalenessTests(unittest.TestCase):
    def test_stale_world_story_is_not_added_by_the_fill_pass(self):
        candidates = [
            story(
                1,
                "world_news",
                "europe",
                title="Old People Daily page",
                urls=["https://english.people.com.cn/n3/2017/0101/c90000-1.html"],
            ),
            story(2, "policy"),
            story(3, "policy"),
        ]

        selected = select_briefing_stories(candidates, max_stories=10)

        self.assertEqual([item["title"] for item in selected], ["Policy story 2", "Policy story 3"])


if __name__ == "__main__":
    unittest.main()
