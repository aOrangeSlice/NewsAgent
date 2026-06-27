from __future__ import annotations

from typing import Any
from urllib import error, parse, request
import json
import ssl
import time


USER_AGENT = "NewsAgent/0.1 (+local intelligence agent)"
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def fetch_text(
    url: str,
    timeout: int = 20,
    verify_ssl: bool = True,
    retries: int = 2,
    backoff_seconds: float = 0.5,
) -> str:
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    context = None
    if url.startswith("https://") and not verify_ssl:
        context = ssl._create_unverified_context()
    last_error: BaseException | None = None
    for attempt in range(retries + 1):
        try:
            with request.urlopen(req, timeout=timeout, context=context) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        except error.HTTPError as exc:
            last_error = exc
            if exc.code not in RETRYABLE_STATUS_CODES or attempt >= retries:
                raise
        except (TimeoutError, error.URLError) as exc:
            last_error = exc
            if attempt >= retries:
                raise
        if backoff_seconds > 0:
            time.sleep(backoff_seconds * (2 ** attempt))
    if last_error:
        raise last_error
    raise RuntimeError(f"Failed to fetch {url}")


def fetch_json(
    url: str,
    params: dict[str, Any] | None = None,
    timeout: int = 20,
    verify_ssl: bool = True,
    retries: int = 2,
    backoff_seconds: float = 0.5,
) -> Any:
    if params:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{parse.urlencode(params)}"
    text = fetch_text(
        url,
        timeout=timeout,
        verify_ssl=verify_ssl,
        retries=retries,
        backoff_seconds=backoff_seconds,
    )
    return json.loads(text)
