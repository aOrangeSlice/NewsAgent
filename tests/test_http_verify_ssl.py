import ssl
import unittest
from unittest.mock import patch

from newsagent.http import fetch_text


class FakeHeaders:
    def get_content_charset(self):
        return "utf-8"


class FakeResponse:
    headers = FakeHeaders()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return "ok".encode("utf-8")


class VerifySslTests(unittest.TestCase):
    def test_unverified_context_is_used_even_for_plain_http_urls(self):
        # urlopen builds TLS from the final URL, so an http:// source that
        # redirects to https:// must still inherit verify_ssl=False.
        with patch("newsagent.http.request.urlopen", return_value=FakeResponse()) as urlopen:
            fetch_text("http://example.com/feed.xml", verify_ssl=False, retries=0)

        context = urlopen.call_args.kwargs["context"]
        self.assertIsInstance(context, ssl.SSLContext)
        self.assertFalse(context.verify_mode)
        self.assertFalse(context.check_hostname)

    def test_verified_fetch_passes_no_context(self):
        with patch("newsagent.http.request.urlopen", return_value=FakeResponse()) as urlopen:
            fetch_text("https://example.com/feed.xml", retries=0)

        self.assertIsNone(urlopen.call_args.kwargs["context"])


if __name__ == "__main__":
    unittest.main()
