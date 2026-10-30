import unittest
from unittest.mock import patch

from newsagent.collectors.market import YahooQuotesCollector
from newsagent.models import Source


def make_source(extra: dict | None = None) -> Source:
    return Source.from_dict(
        {
            "id": "yahoo",
            "name": "Yahoo Finance",
            "kind": "yahoo_quotes",
            "category": "market",
            "symbols": [{"symbol": "^GSPC", "label": "S&P 500"}],
            **(extra or {}),
        }
    )


def collect(payload: dict):
    with patch("newsagent.collectors.market.fetch_json", return_value=payload):
        return YahooQuotesCollector(make_source()).collect(limit=10)


class YahooErrorPayloadTests(unittest.TestCase):
    def test_error_payload_produces_no_item(self):
        payload = {"chart": {"result": None, "error": {"code": "Not Found"}}}

        self.assertEqual(collect(payload), [])

    def test_missing_price_produces_no_item(self):
        payload = {"chart": {"result": [{"meta": {}, "indicators": {"quote": [{"close": None}]}}], "error": None}}

        self.assertEqual(collect(payload), [])

    def test_null_close_list_does_not_raise(self):
        payload = {
            "chart": {
                "result": [
                    {
                        "meta": {"regularMarketPrice": 5123.4, "chartPreviousClose": 5100.0},
                        "indicators": {"quote": [{"close": None}]},
                    }
                ]
            }
        }

        items = collect(payload)

        self.assertEqual(len(items), 1)
        self.assertIn("S&P 500: 5123.4000", items[0].title)

    def test_one_bad_symbol_does_not_drop_the_rest(self):
        source = Source.from_dict(
            {
                "id": "yahoo",
                "name": "Yahoo Finance",
                "kind": "yahoo_quotes",
                "category": "market",
                "symbols": [
                    {"symbol": "^GSPC", "label": "S&P 500"},
                    {"symbol": "^N225", "label": "Nikkei 225"},
                ],
            }
        )
        bad = {"chart": {"result": None, "error": {"code": "Not Found"}}}
        good = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "regularMarketPrice": 5123.4,
                            "chartPreviousClose": 5100.0,
                            "regularMarketTime": 1780000000,
                        },
                        "indicators": {"quote": [{"close": [5100.0, 5123.4]}]},
                    }
                ]
            }
        }
        with patch("newsagent.collectors.market.fetch_json", side_effect=[bad, good]):
            items = YahooQuotesCollector(source).collect(limit=10)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].metrics["price"], 5123.4)
        self.assertEqual(items[0].metrics["symbol"], "^N225")


if __name__ == "__main__":
    unittest.main()
