from __future__ import annotations

from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET
import html
import re

from newsagent.http import fetch_text

from .base import Collector, CollectorError


def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return html.unescape(re.sub(r"\s+", " ", text).strip())


def parse_date(value: str) -> str:
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).isoformat()
    except Exception:
        return value.strip()


def child_text(elem: ET.Element, names: list[str]) -> str:
    for name in names:
        found = elem.find(name)
        if found is not None and found.text:
            return found.text.strip()
    for child in list(elem):
        local = child.tag.split("}", 1)[-1]
        if local in names and child.text:
            return child.text.strip()
    return ""


def descendant_text(elem: ET.Element, names: list[str]) -> str:
    for child in elem.iter():
        local = child.tag.split("}", 1)[-1]
        if local in names and child.text:
            return child.text.strip()
    return ""


def children_by_local_name(elem: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in elem.iter() if child.tag.split("}", 1)[-1] == name]


def link_href(elem: ET.Element) -> str:
    for child in elem.iter():
        local = child.tag.split("}", 1)[-1]
        if local == "link" and child.attrib.get("href"):
            return child.attrib["href"].strip()
    return ""


class RSSCollector(Collector):
    def collect(self, limit: int = 20):
        if not self.source.url:
            raise CollectorError(f"{self.source.id} has no url")
        xml_text = fetch_text(
            self.source.url,
            verify_ssl=bool(self.source.extra.get("verify_ssl", True)),
        )
        root = ET.fromstring(xml_text)
        items = children_by_local_name(root, "item")
        if not items:
            items = children_by_local_name(root, "entry")
        results = []
        for feed_rank, entry in enumerate(items, start=1):
            title = child_text(entry, ["title"])
            link = child_text(entry, ["link"])
            if not link:
                link = link_href(entry)
            summary = child_text(entry, ["description", "summary", "content", "encoded"])
            if not summary:
                summary = descendant_text(entry, ["description", "summary", "content", "encoded"])
            published = child_text(entry, ["pubDate", "published", "updated", "date"])
            clean_title = strip_html(title)
            clean_summary = strip_html(summary)
            if not clean_title or not link:
                continue
            if not keyword_filter_allows(
                clean_title,
                clean_summary,
                include_keywords=self.source.extra.get("include_keywords", []),
                exclude_keywords=self.source.extra.get("exclude_keywords", []),
            ):
                continue
            results.append(
                self.item(
                    title=clean_title,
                    url=link,
                    summary=clean_summary,
                    published_at=parse_date(published),
                    metrics={"feed_rank": feed_rank},
                )
            )
            if len(results) >= limit:
                break
        return results


def keyword_filter_allows(
    title: str,
    summary: str = "",
    include_keywords: list[str] | None = None,
    exclude_keywords: list[str] | None = None,
) -> bool:
    text = f"{title} {summary}".lower()
    includes = [keyword.lower() for keyword in include_keywords or [] if keyword]
    excludes = [keyword.lower() for keyword in exclude_keywords or [] if keyword]
    if includes and not any(keyword in text for keyword in includes):
        return False
    if excludes and any(keyword in text for keyword in excludes):
        return False
    return True
