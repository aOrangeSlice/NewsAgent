from __future__ import annotations

from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
import json
import os
import uuid

from newsagent.config import load_settings


class CloudConfigTests(TestCase):
    def test_environment_overrides_cloud_runtime_values(self) -> None:
        path = Path(__file__).resolve().parent / f"cloud_settings_{uuid.uuid4().hex}.json"
        try:
            path.write_text(
                json.dumps(
                    {
                        "database": {"path": "data/local.db"},
                        "llm": {"provider": "ollama", "model": "local"},
                        "delivery": {"email": {"enabled": False, "recipients": []}},
                    }
                ),
                encoding="utf-8",
            )
            env = {
                "NEWSAGENT_DATABASE_PATH": str(path.with_suffix(".db")),
                "NEWSAGENT_LLM_PROVIDER": "vertex",
                "NEWSAGENT_LLM_MODEL": "gemini-2.5-flash-lite",
                "NEWSAGENT_LLM_MAX_CALLS_PER_RUN": "7",
                "NEWSAGENT_LLM_MAX_OUTPUT_TOKENS": "2048",
                "NEWSAGENT_EMAIL_ENABLED": "true",
                "NEWSAGENT_SMTP_USERNAME": "sender@example.com",
                "NEWSAGENT_EMAIL_RECIPIENTS": "a@example.com, b@example.com",
            }
            with patch.dict(os.environ, env, clear=True):
                settings = load_settings(path)
        finally:
            path.unlink(missing_ok=True)

        self.assertEqual(settings["llm"]["provider"], "vertex")
        self.assertEqual(settings["llm"]["max_calls_per_run"], 7)
        self.assertEqual(settings["llm"]["max_output_tokens"], 2048)
        self.assertTrue(settings["delivery"]["email"]["enabled"])
        self.assertEqual(
            settings["delivery"]["email"]["recipients"],
            ["a@example.com", "b@example.com"],
        )

    def test_invalid_boolean_override_is_rejected(self) -> None:
        path = Path(__file__).resolve().parent / f"cloud_settings_{uuid.uuid4().hex}.json"
        try:
            path.write_text(
                '{"database":{"path":"test.db"},"delivery":{"email":{}}}',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"NEWSAGENT_EMAIL_ENABLED": "maybe"}, clear=True):
                with self.assertRaisesRegex(ValueError, "must be true or false"):
                    load_settings(path)
        finally:
            path.unlink(missing_ok=True)
