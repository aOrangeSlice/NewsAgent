from pathlib import Path
import unittest
import uuid

from newsagent.security import scan_for_secrets


class SecretScanTests(unittest.TestCase):
    def make_root(self) -> Path:
        return Path(__file__).resolve().parent / f"tmp_security_{uuid.uuid4().hex}"

    def remove_root(self, root: Path) -> None:
        if not root.exists():
            return
        for path in sorted(root.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        root.rmdir()

    def test_flags_inline_json_secret(self):
        root = self.make_root()
        try:
            config_dir = root / "config"
            config_dir.mkdir(parents=True)
            (config_dir / "settings.json").write_text(
                '{"delivery": {"email": {"password": "real-password-12345"}}}',
                encoding="utf-8",
            )

            findings = scan_for_secrets(root)
        finally:
            self.remove_root(root)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["kind"], "inline_secret_config")

    def test_allows_env_var_secret_reference(self):
        root = self.make_root()
        try:
            config_dir = root / "config"
            config_dir.mkdir(parents=True)
            (config_dir / "settings.json").write_text(
                '{"delivery": {"email": {"password_env": "NEWSAGENT_SMTP_PASSWORD"}}}',
                encoding="utf-8",
            )

            findings = scan_for_secrets(root)
        finally:
            self.remove_root(root)

        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
