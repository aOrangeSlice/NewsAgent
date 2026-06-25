from __future__ import annotations

from abc import ABC, abstractmethod
import re

from newsagent.models import NewsItem, Source


class CollectorError(RuntimeError):
    pass


class Collector(ABC):
    def __init__(self, source: Source):
        self.source = source

    @abstractmethod
    def collect(self, limit: int = 20) -> list[NewsItem]:
        raise NotImplementedError

    def item(
        self,
        title: str,
        url: str,
        summary: str = "",
        published_at: str = "",
        metrics: dict | None = None,
        tags: list[str] | None = None,
    ) -> NewsItem:
        return NewsItem(
            source_id=self.source.id,
            source_name=self.source.name,
            category=self.source.category,
            subcategory=self.source.subcategory,
            region=self.source.region,
            title=title.strip(),
            url=url.strip(),
            summary=(summary or "").strip(),
            published_at=published_at or "",
            language=detect_language(f"{title} {summary}"),
            metrics=metrics or {},
            tags=tags or self.source.tags,
            tier=self.source.tier,
            priority=self.source.priority,
        )


def detect_language(text: str) -> str:
    sample = text or ""
    if re.search(r"[\u3040-\u30ff]", sample):
        return "ja"
    if re.search(r"[\u3400-\u9fff]", sample):
        return "zh"
    if re.search(r"[A-Za-z]", sample):
        return "en"
    return ""
