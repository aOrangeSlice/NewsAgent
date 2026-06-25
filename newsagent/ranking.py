from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import sqlite3


PRIORITY_WEIGHT = {"P0": 40, "P1": 20, "P2": 5}
CATEGORY_WEIGHT = {
    "market": 18,
    "policy": 18,
    "medicine": 16,
    "ai": 16,
    "ai_engineering": 16,
    "ai_hardware": 16,
    "world_news": 17,
}

HIGH_IMPACT_TERMS = [
    "ai",
    "artificial intelligence",
    "chip",
    "semiconductor",
    "gpu",
    "nvidia",
    "tsmc",
    "data center",
    "interest rate",
    "central bank",
    "inflation",
    "tariff",
    "sanction",
    "election",
    "war",
    "ceasefire",
    "regulation",
    "security",
    "cyber",
    "earnings",
    "merger",
    "ipo",
    "market",
    "stocks",
    "oil",
    "health",
    "medicine",
    "medical",
    "clinical",
    "fda",
    "who",
    "disease",
    "vaccine",
    "drug",
    "trial",
    "digital health",
    "digital medicine",
    "jama",
    "nature medicine",
    "cctv",
    "cgtn",
    "xinhua",
]


def score_raw_item(row: sqlite3.Row) -> float:
    score = 0.0
    score += PRIORITY_WEIGHT.get(row["priority"] or "P1", 10)
    score += CATEGORY_WEIGHT.get(row["category"] or "", 8)
    score += max(0, 16 - int(row["tier"] or 3) * 4)
    score += recency_score(row["published_at"] or row["retrieved_at"])
    score += editorial_position_score(row["metrics_json"])
    score += keyword_importance_score(row["title"] or "", row["summary"] or "")
    if row["url"]:
        score += 5
    if row["summary"]:
        score += 3
    return score


def editorial_position_score(metrics_json: str | None) -> float:
    metrics = parse_metrics(metrics_json)
    feed_rank = metrics.get("feed_rank")
    if not isinstance(feed_rank, int):
        return 0
    if feed_rank <= 1:
        return 14
    if feed_rank <= 3:
        return 10
    if feed_rank <= 5:
        return 7
    if feed_rank <= 10:
        return 4
    return 0


def keyword_importance_score(title: str, summary: str) -> float:
    text = f"{title} {summary}".lower()
    hits = sum(1 for term in HIGH_IMPACT_TERMS if term in text)
    return min(hits * 3, 15)


def parse_metrics(metrics_json: str | None) -> dict:
    if not metrics_json:
        return {}
    try:
        value = json.loads(metrics_json)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def recency_score(value: str) -> float:
    dt = parse_dt(value)
    if not dt:
        return 2
    age_hours = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600)
    if age_hours <= 12:
        return 18
    if age_hours <= 24:
        return 14
    if age_hours <= 72:
        return 10
    if age_hours <= 168:
        return 5
    return 1


def parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(value)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
