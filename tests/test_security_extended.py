import tempfile
import unittest
from pathlib import Path

from newsagent.security import scan_for_secrets

# Built at runtime so this fixture is not itself a scannable token in the repo.
FAKE_OPENAI_KEY = "sk-" + "probe" * 6


class ExtendedSecretScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_suffixed_env_files_are_scanned(self):
        self.write(".env.production", f"OPENAI_API_KEY={FAKE_OPENAI_KEY}\n")

        findings = scan_for_secrets(self.root)

        self.assertEqual([(item["file"], item["kind"]) for item in findings], [(".env.production", "openai_api_key")])

    def test_plain_credentials_in_env_file_are_reported(self):
        self.write(".env", "NEWSAGENT_SMTP_PASSWORD=hunter2-real-password\n")

        findings = scan_for_secrets(self.root)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["kind"], "inline_secret_config")

    def test_yaml_password_is_reported(self):
        self.write("deploy/app.yml", "smtp_password: hunter2-real-password\n")

        findings = scan_for_secrets(self.root)

        self.assertEqual([item["kind"] for item in findings], ["inline_secret_config"])

    def test_env_reference_and_placeholders_are_allowed(self):
        self.write(".env", "NEWSAGENT_SMTP_PASSWORD=${NEWSAGENT_SMTP_PASSWORD}\nDTOKEN=CHANGEME\n")
        self.write("app.yml", "password_env: NEWSAGENT_SMTP_PASSWORD\napi_key: your-api-key\n")

        self.assertEqual(scan_for_secrets(self.root), [])

    def test_comments_and_unrelated_keys_are_ignored(self):
        self.write(".env", "# password=not-a-real-secret\nLOG_LEVEL=debug\n")

        self.assertEqual(scan_for_secrets(self.root), [])

    def test_python_sources_do_not_get_the_key_value_rule(self):
        self.write("build.py", 'password = "hunter2-real-password"\n')

        self.assertEqual(scan_for_secrets(self.root), [])


if __name__ == "__main__":
    unittest.main()
