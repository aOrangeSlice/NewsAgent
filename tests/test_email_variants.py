import unittest
from unittest.mock import call, patch

from newsagent.pipeline import NewsAgentApp


class EmailVariantTests(unittest.TestCase):
    def setUp(self):
        self.app = NewsAgentApp.__new__(NewsAgentApp)
        self.variants = [
            {
                "id": 11,
                "body": "rules body",
                "mode": "rules",
                "generation_status": "deterministic",
            },
            {
                "id": 12,
                "body": "llm body",
                "mode": "llm",
                "generation_status": "generated",
            },
        ]

    def test_use_llm_sends_rules_and_llm_editions(self):
        with patch.object(
            self.app,
            "send_email",
            side_effect=[
                {"ok": True, "recipients": ["reader@example.com"]},
                {"ok": True, "recipients": ["reader@example.com"]},
            ],
        ) as send_email:
            result = self.app._send_daily_email_variants(self.variants, use_llm=True)

        self.assertEqual(
            send_email.call_args_list,
            [
                call(
                    "rules body",
                    briefing_id=11,
                    subject="NewsAgent Daily Brief [Rules] #11",
                ),
                call(
                    "llm body",
                    briefing_id=12,
                    subject="NewsAgent Daily Brief [LLM] #12",
                ),
            ],
        )
        self.assertTrue(result["ok"])
        self.assertEqual(
            [delivery["mode"] for delivery in result["deliveries"]],
            ["rules", "llm"],
        )

    def test_rules_mode_sends_only_rules_edition(self):
        with patch.object(
            self.app,
            "send_email",
            return_value={"ok": True, "recipients": ["reader@example.com"]},
        ) as send_email:
            result = self.app._send_daily_email_variants(self.variants, use_llm=False)

        send_email.assert_called_once_with(
            "rules body",
            briefing_id=11,
            subject="NewsAgent Daily Brief [Rules] #11",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["deliveries"]), 1)
        self.assertEqual(result["deliveries"][0]["mode"], "rules")

    def test_aggregate_result_is_false_when_one_delivery_fails(self):
        with patch.object(
            self.app,
            "send_email",
            side_effect=[
                {"ok": True, "recipients": ["reader@example.com"]},
                {"ok": False, "error": "SMTP unavailable"},
            ],
        ):
            result = self.app._send_daily_email_variants(self.variants, use_llm=True)

        self.assertFalse(result["ok"])
        self.assertEqual(len(result["deliveries"]), 2)


if __name__ == "__main__":
    unittest.main()
