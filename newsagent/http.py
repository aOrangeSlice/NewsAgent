from __future__ import annotations

from typing import Any
from urllib import parse, request
import json
import ssl


USER_AGENT = "NewsAgent/0.1 (+local intelligence agent)"


def fetch_text(url: str, timeout: int = 20, verify_ssl: bool = True) -> str:
    req = request.Request(url, headers={"User-Agent": USER_AGENT})
    context = None
    if url.startswith("https://") and not verify_ssl:
        context = ssl._create_unverified_context()
    with request.urlopen(req, timeout=timeout, context=context) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def fetch_json(
    url: str,
    params: dict[str, Any] | None = None,
    timeout: int = 20,
    verify_ssl: bool = True,
) -> Any:
    if params:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{parse.urlencode(params)}"
    text = fetch_text(url, timeout=timeout, verify_ssl=verify_ssl)
    return json.loads(text)
