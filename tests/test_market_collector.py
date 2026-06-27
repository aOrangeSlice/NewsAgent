import unittest

from newsagent.collectors.market import latest_and_previous_price


class MarketCollectorTests(unittest.TestCase):
    def test_regular_market_price_wins_when_latest_daily_close_is_empty(self):
        last, previous = latest_and_previous_price(
            {"regularMarketPrice": 69360.88, "chartPreviousClose": 72353.96},
            [72353.96, 69788.38, 69174.97, 72366.34, None],
        )

        self.assertEqual(last, 69360.88)
        self.assertEqual(previous, 72366.34)

    def test_completed_daily_close_uses_prior_close_for_change(self):
        last, previous = latest_and_previous_price(
            {"regularMarketPrice": 7354.02, "chartPreviousClose": 7500.58},
            [7472.79, 7365.46, 7358.22, 7357.49, 7354.02001953125],
        )

        self.assertEqual(last, 7354.02)
        self.assertEqual(previous, 7357.49)


if __name__ == "__main__":
    unittest.main()
