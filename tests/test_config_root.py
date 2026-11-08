import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from newsagent.config import resolve_root


class ResolveRootTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    def test_env_override_wins(self):
        target = self.base / "data"
        with patch.dict("os.environ", {"NEWSAGENT_ROOT": str(target)}):
            self.assertEqual(resolve_root(self.base / "pkg"), target.resolve())

    def test_checkout_root_is_preferred_when_config_exists(self):
        (self.base / "config").mkdir()
        package_dir = self.base / "pkg"

        with patch.dict("os.environ", {}, clear=False):
            with patch.dict("os.environ"):
                import os

                os.environ.pop("NEWSAGENT_ROOT", None)
                self.assertEqual(resolve_root(package_dir), self.base)

    def test_installed_package_falls_back_to_cwd(self):
        package_dir = self.base / "site-packages" / "newsagent"
        package_dir.mkdir(parents=True)

        with patch.dict("os.environ"):
            import os

            os.environ.pop("NEWSAGENT_ROOT", None)
            self.assertEqual(resolve_root(package_dir), Path.cwd())


if __name__ == "__main__":
    unittest.main()
