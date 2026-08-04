import json
import unittest
from unittest.mock import patch

from newsagent.llm import OllamaClient


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(
            {
                "response": "ok",
                "prompt_eval_count": 10,
                "prompt_eval_duration": 1_000_000_000,
                "eval_count": 20,
                "eval_duration": 2_000_000_000,
            }
        ).encode("utf-8")


class OllamaMetricsTests(unittest.TestCase):
    @patch("newsagent.llm.request.urlopen", return_value=FakeResponse())
    def test_generate_records_and_drains_metrics(self, _urlopen):
        client = OllamaClient("http://localhost:11434", "test-model")

        self.assertEqual(client.generate("hello"), "ok")
        metrics = client.drain_metrics()

        self.assertEqual(len(metrics), 1)
        self.assertEqual(metrics[0]["eval_count"], 20)
        self.assertEqual(metrics[0]["eval_tokens_per_second"], 10.0)
        self.assertEqual(client.drain_metrics(), [])


if __name__ == "__main__":
    unittest.main()
