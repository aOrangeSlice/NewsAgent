import unittest
from urllib import error
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


class HttpRetryTests(unittest.TestCase):
    def test_retries_transient_url_error(self):
        with patch(
            "newsagent.http.request.urlopen",
            side_effect=[error.URLError("temporary"), FakeResponse()],
        ) as urlopen, patch("newsagent.http.time.sleep") as sleep:
            text = fetch_text("https://example.com/feed.xml", retries=1, backoff_seconds=0.01)

        self.assertEqual(text, "ok")
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once()

    def test_does_not_retry_non_retryable_http_error(self):
        http_error = error.HTTPError(
            url="https://example.com/missing",
            code=404,
            msg="not found",
            hdrs=None,
            fp=None,
        )
        with patch("newsagent.http.request.urlopen", side_effect=http_error) as urlopen:
            with self.assertRaises(error.HTTPError):
                fetch_text("https://example.com/missing", retries=2, backoff_seconds=0)

        self.assertEqual(urlopen.call_count, 1)


if __name__ == "__main__":
    unittest.main()
