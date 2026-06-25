from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import hashlib
import json
import re


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_hash(*parts: str) -> str:
    raw = "|".join(part.strip().lower() for part in parts if part)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_title(title: str) -> str:
    text = re.sub(r"\s+", " ", title or "").strip().lower()
    text = re.sub(r"[^a-z0-9\u3040-\u30ff\u3400-\u9fff ]+", "", text)
    return text[:180]


@dataclass
class Source:
    id: str
    name: str
    kind: str
    category: str
    subcategory: str = ""
    region: str = "global"
    url: str = ""
    enabled: bool = True
    tier: int = 3
    priority: str = "P1"
    tags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Source":
        known = {
            "id",
            "name",
            "kind",
            "category",
            "subcategory",
            "region",
            "url",
            "enabled",
            "tier",
            "priority",
            "tags",
        }
        extra = {k: v for k, v in data.items() if k not in known}
        return cls(
            id=data["id"],
            name=data["name"],
            kind=data["kind"],
            category=data.get("category", "general"),
            subcategory=data.get("subcategory", ""),
            region=data.get("region", "global"),
            url=data.get("url", ""),
            enabled=bool(data.get("enabled", True)),
            tier=int(data.get("tier", 3)),
            priority=data.get("priority", "P1"),
            tags=list(data.get("tags", [])),
            extra=extra,
        )


@dataclass
class NewsItem:
    source_id: str
    source_name: str
    category: str
    subcategory: str
    region: str
    title: str
    url: str
    summary: str = ""
    published_at: str = ""
    retrieved_at: str = field(default_factory=utc_now_iso)
    language: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    tier: int = 3
    priority: str = "P1"

    @property
    def content_hash(self) -> str:
        if self.category == "market" and self.metrics.get("symbol"):
            return stable_hash(
                self.url,
                normalize_title(self.title),
                self.source_id,
                str(self.metrics.get("quote_time", "")),
                str(self.metrics.get("market_state", "")),
            )
        return stable_hash(self.url, normalize_title(self.title), self.source_id)

    @property
    def cluster_key(self) -> str:
        if self.category == "market" and self.metrics.get("symbol"):
            return stable_hash(self.category, self.source_id, str(self.metrics["symbol"]))
        return stable_hash(self.category, self.region, normalize_title(self.title))

    def to_record(self) -> dict[str, Any]:
        return {
            "content_hash": self.content_hash,
            "cluster_key": self.cluster_key,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "category": self.category,
            "subcategory": self.subcategory,
            "region": self.region,
            "title": self.title,
            "url": self.url,
            "summary": self.summary,
            "published_at": self.published_at,
            "retrieved_at": self.retrieved_at,
            "language": self.language,
            "metrics_json": json.dumps(self.metrics, ensure_ascii=False),
            "tags_json": json.dumps(self.tags, ensure_ascii=False),
            "tier": self.tier,
            "priority": self.priority,
        }


@dataclass
class Story:
    id: int
    cluster_key: str
    title: str
    summary: str
    category: str
    subcategory: str
    region: str
    score: float
    source_urls: list[str]
    item_ids: list[int]
    tags: list[str]
    created_at: str
    updated_at: str
