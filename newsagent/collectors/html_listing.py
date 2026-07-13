from __future__ import annotations

from urllib.parse import urljoin, urlparse
import html
import re

from newsagent.http import fetch_text

from .base import Collector, CollectorError
from .rss import keyword_filter_allows


ANCHOR_RE = re.compile(
    r"<a\b[^>]*href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<body>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)


def clean_text(value: str) -> str:
    value = re.sub(r"<script\b.*?</script>", " ", value or "", flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<style\b.*?</style>", " ", value, flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_url(base_url: str, href: str) -> str:
    return urljoin(base_url, html.unescape(href).strip())


def host_matches(url: str, allowed_hosts: list[str]) -> bool:
    if not allowed_hosts:
        return True
    host = urlparse(url).netloc.lower().removeprefix("www.")
    return any(host == allowed.lower().removeprefix("www.") for allowed in allowed_hosts)


def prefix_matches(url: str, prefixes: list[str]) -> bool:
    if not prefixes:
        return True
    return any(url.startswith(prefix) for prefix in prefixes)


class HTMLListingCollector(Collector):
    def collect(self, limit: int = 20):
        if not self.source.url:
            raise CollectorError(f"{self.source.id} has no url")

        text = fetch_text(
            self.source.url,
            verify_ssl=bool(self.source.extra.get("verify_ssl", True)),
        )
        min_title_length = int(self.source.extra.get("min_title_length", 8))
        allowed_hosts = list(self.source.extra.get("allowed_hosts", []))
        url_prefixes = list(self.source.extra.get("url_prefixes", []))
        include_keywords = self.source.extra.get("include_keywords", [])
        exclude_keywords = self.source.extra.get("exclude_keywords", [])
        summary = self.source.extra.get("summary", "")
        results = []
        seen: set[str] = set()

        for page_rank, match in enumerate(ANCHOR_RE.finditer(text), start=1):
            title = clean_text(match.group("body"))
            url = normalize_url(self.source.url, match.group("href"))
            if len(title) < min_title_length or not url.startswith(("http://", "https://")):
                continue
            if not host_matches(url, allowed_hosts):
                continue
            if not prefix_matches(url, url_prefixes):
                continue
            if not keyword_filter_allows(title, "", include_keywords, exclude_keywords):
                continue
            if url in seen:
                continue
            seen.add(url)
            results.append(
                self.item(
                    title=title,
                    url=url,
                    summary=summary,
                    metrics={"feed_rank": len(results) + 1, "page_rank": page_rank},
                )
            )
            if len(results) >= limit:
                break
        return results
