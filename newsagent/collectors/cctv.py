from __future__ import annotations

from urllib.parse import urljoin
import html
import re

from newsagent.http import fetch_text

from .base import Collector, CollectorError


ANCHOR_RE = re.compile(
    r"<a\b[^>]*href=[\"'](?P<href>[^\"']+)[\"'][^>]*>(?P<body>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def page_date(text: str) -> str:
    dates = re.findall(r"20\d{2}-\d{2}-\d{2}", text)
    return max(dates) if dates else ""


class CCTVXinwenLianboCollector(Collector):
    def collect(self, limit: int = 20):
        if not self.source.url:
            raise CollectorError(f"{self.source.id} has no url")

        text = fetch_text(
            self.source.url,
            verify_ssl=bool(self.source.extra.get("verify_ssl", True)),
        )
        published = page_date(text)
        results = []
        seen: set[str] = set()

        for feed_rank, match in enumerate(ANCHOR_RE.finditer(text), start=1):
            title = clean_text(match.group("body"))
            href = html.unescape(match.group("href")).strip()
            if not title or not href:
                continue
            if not is_news_video(title, href):
                continue

            url = urljoin(self.source.url, href)
            if url in seen:
                continue
            seen.add(url)
            results.append(
                self.item(
                    title=title,
                    url=url,
                    summary="CCTV Xinwen Lianbo official video item.",
                    published_at=published,
                    metrics={"feed_rank": len(results) + 1, "page_rank": feed_rank},
                    tags=self.source.tags + ["official_video", "xinwen_lianbo"],
                )
            )
            if len(results) >= limit:
                break
        return results


def is_news_video(title: str, href: str) -> bool:
    lower_href = href.lower()
    looks_like_cctv_link = (
        "tv.cctv.com" in lower_href
        or lower_href.startswith("/")
        or lower_href.endswith(".shtml")
    )
    if not looks_like_cctv_link:
        return False
    if title in {"Image", "查看最新", "已无更多内容", "热门栏目", "播出信息"}:
        return False
    if "新闻联播" in title or "[视频]" in title or "完整版" in title:
        return True
    return "/vide" in lower_href or "/video" in lower_href
