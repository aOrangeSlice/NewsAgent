from __future__ import annotations

from types import SimpleNamespace
from unittest import TestCase, mock

from newsagent.llm import (
    GenerationBudgetExceeded,
    Summarizer,
    VertexAIClient,
    aggregate_generation_metrics,
)


class FakeConfig:
    def __init__(self, **kwargs):
        self.values = kwargs


class FakeModels:
    def __init__(self):
        self.calls = []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        usage = SimpleNamespace(
            prompt_token_count=120,
            candidates_token_count=30,
            total_token_count=150,
            thoughts_token_count=0,
        )
        return SimpleNamespace(text="Vertex response", usage_metadata=usage)


class VertexProviderTests(TestCase):
    def make_client(self, max_calls: int = 2) -> tuple[VertexAIClient, FakeModels]:
        client = VertexAIClient(
            project="test-project",
            location="global",
            model="gemini-2.5-flash-lite",
            max_calls=max_calls,
            max_output_tokens=2048,
        )
        models = FakeModels()
        client._client = SimpleNamespace(models=models)
        client._types = SimpleNamespace(GenerateContentConfig=FakeConfig)
        return client, models

    def test_vertex_generate_uses_limits_and_records_usage(self) -> None:
        client, models = self.make_client()
        self.assertEqual(client.generate("hello"), "Vertex response")
        config = models.calls[0]["config"].values
        self.assertEqual(config["max_output_tokens"], 2048)
        metrics = client.drain_metrics()
        self.assertEqual(metrics[0]["input_tokens"], 120)
        self.assertEqual(metrics[0]["output_tokens"], 30)

    def test_call_budget_is_enforced(self) -> None:
        client, _models = self.make_client(max_calls=1)
        client.generate("first")
        with self.assertRaises(GenerationBudgetExceeded):
            client.generate("second")

    def test_metrics_aggregate_ollama_and_vertex_names(self) -> None:
        metrics = aggregate_generation_metrics(
            [
                {"prompt_eval_count": 10, "eval_count": 4, "elapsed_seconds": 1.5},
                {"input_tokens": 20, "output_tokens": 6, "elapsed_seconds": 2.0},
            ]
        )
        self.assertEqual(metrics["call_count"], 2)
        self.assertEqual(metrics["input_tokens"], 30)
        self.assertEqual(metrics["output_tokens"], 10)
        self.assertEqual(metrics["elapsed_seconds"], 3.5)

    def test_unavailable_vertex_falls_back_to_deterministic_briefing(self) -> None:
        settings = {
            "llm": {
                "provider": "vertex",
                "model": "gemini-2.5-flash-lite",
                "project": "test-project",
                "location": "global",
            },
            "briefing": {"use_llm": True},
        }
        with mock.patch.object(VertexAIClient, "available", return_value=False):
            output = Summarizer(settings).create_briefing([], output_language="original")

        self.assertEqual(output.generation_status, "fallback_rules")
        self.assertEqual(output.generation_model, "")
