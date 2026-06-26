from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import sqlite3

from .models import NewsItem, tokyo_now_iso


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    category TEXT NOT NULL,
    subcategory TEXT,
    region TEXT,
    tier INTEGER,
    priority TEXT,
    enabled INTEGER,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_hash TEXT NOT NULL UNIQUE,
    cluster_key TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    category TEXT NOT NULL,
    subcategory TEXT,
    region TEXT,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    summary TEXT,
    published_at TEXT,
    retrieved_at TEXT NOT NULL,
    language TEXT,
    metrics_json TEXT,
    tags_json TEXT,
    tier INTEGER,
    priority TEXT
);

CREATE INDEX IF NOT EXISTS idx_raw_items_category ON raw_items(category);
CREATE INDEX IF NOT EXISTS idx_raw_items_retrieved ON raw_items(retrieved_at);
CREATE INDEX IF NOT EXISTS idx_raw_items_cluster ON raw_items(cluster_key);

CREATE TABLE IF NOT EXISTS story_clusters (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    summary TEXT,
    category TEXT NOT NULL,
    subcategory TEXT,
    region TEXT,
    score REAL NOT NULL DEFAULT 0,
    source_urls_json TEXT NOT NULL,
    item_ids_json TEXT NOT NULL,
    tags_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_story_score ON story_clusters(score DESC);
CREATE INDEX IF NOT EXISTS idx_story_category ON story_clusters(category);

CREATE TABLE IF NOT EXISTS briefings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    language TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    canonical_body TEXT,
    briefing_group TEXT,
    generation_mode TEXT NOT NULL DEFAULT 'legacy',
    generation_status TEXT NOT NULL DEFAULT 'legacy',
    generation_model TEXT,
    translation_status TEXT NOT NULL DEFAULT 'legacy',
    translation_model TEXT,
    story_ids_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id INTEGER NOT NULL,
    feedback TEXT NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    ok INTEGER NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS delivery_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    level TEXT NOT NULL,
    event TEXT NOT NULL,
    message_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pipeline_logs_run_id ON pipeline_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_logs_created ON pipeline_logs(created_at);
CREATE INDEX IF NOT EXISTS idx_pipeline_logs_level_event ON pipeline_logs(level, event);

CREATE TABLE IF NOT EXISTS source_collection_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    status TEXT NOT NULL,
    fetched INTEGER NOT NULL,
    inserted INTEGER NOT NULL,
    existing INTEGER NOT NULL,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_collection_run_id ON source_collection_logs(run_id);
CREATE INDEX IF NOT EXISTS idx_source_collection_source ON source_collection_logs(source_id, created_at);
CREATE INDEX IF NOT EXISTS idx_source_collection_status ON source_collection_logs(status, created_at);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def init(self) -> None:
        self.conn.executescript(SCHEMA)
        self._ensure_column("briefings", "canonical_body", "TEXT")
        self._ensure_column("briefings", "briefing_group", "TEXT")
        self._ensure_column(
            "briefings",
            "generation_mode",
            "TEXT NOT NULL DEFAULT 'legacy'",
        )
        self._ensure_column(
            "briefings",
            "generation_status",
            "TEXT NOT NULL DEFAULT 'legacy'",
        )
        self._ensure_column("briefings", "generation_model", "TEXT")
        self._ensure_column(
            "briefings",
            "translation_status",
            "TEXT NOT NULL DEFAULT 'legacy'",
        )
        self._ensure_column("briefings", "translation_model", "TEXT")
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def upsert_source(self, source: Any) -> None:
        self.conn.execute(
            """
            INSERT INTO sources
                (id, name, kind, category, subcategory, region, tier, priority, enabled, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name,
                kind=excluded.kind,
                category=excluded.category,
                subcategory=excluded.subcategory,
                region=excluded.region,
                tier=excluded.tier,
                priority=excluded.priority,
                enabled=excluded.enabled,
                updated_at=excluded.updated_at
            """,
            (
                source.id,
                source.name,
                source.kind,
                source.category,
                source.subcategory,
                source.region,
                source.tier,
                source.priority,
                int(source.enabled),
                tokyo_now_iso(),
            ),
        )

    def insert_raw_item(self, item: NewsItem) -> int | None:
        record = item.to_record()
        fields = list(record.keys())
        placeholders = ",".join("?" for _ in fields)
        sql = f"INSERT OR IGNORE INTO raw_items ({','.join(fields)}) VALUES ({placeholders})"
        cur = self.conn.execute(sql, [record[field] for field in fields])
        if cur.rowcount == 0:
            return None
        return int(cur.lastrowid)

    def commit(self) -> None:
        self.conn.commit()

    def get_unclustered_raw_items(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """
            SELECT r.*
            FROM raw_items r
            LEFT JOIN story_clusters s ON r.cluster_key = s.cluster_key
            WHERE s.id IS NULL OR r.retrieved_at > s.updated_at
            ORDER BY r.retrieved_at ASC
            """
        ).fetchall()

    def upsert_story_from_raw(self, row: sqlite3.Row, score: float) -> int:
        now = tokyo_now_iso()
        tags = _json_load(row["tags_json"], [])
        source_urls = [row["url"]]
        item_ids = [int(row["id"])]
        self.conn.execute(
            """
            INSERT INTO story_clusters
                (cluster_key, title, summary, category, subcategory, region, score,
                 source_urls_json, item_ids_json, tags_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cluster_key) DO UPDATE SET
                title=excluded.title,
                summary=excluded.summary,
                source_urls_json=excluded.source_urls_json,
                item_ids_json=excluded.item_ids_json,
                tags_json=excluded.tags_json,
                score=max(score, excluded.score),
                updated_at=excluded.updated_at
            """,
            (
                row["cluster_key"],
                row["title"],
                row["summary"] or "",
                row["category"],
                row["subcategory"] or "",
                row["region"] or "global",
                score,
                json.dumps(source_urls, ensure_ascii=False),
                json.dumps(item_ids),
                json.dumps(tags, ensure_ascii=False),
                now,
                now,
            ),
        )
        story = self.conn.execute(
            "SELECT id FROM story_clusters WHERE cluster_key = ?", (row["cluster_key"],)
        ).fetchone()
        return int(story["id"])

    def list_stories(self, limit: int = 20, query: str = "") -> list[dict[str, Any]]:
        params: list[Any] = []
        where = ""
        if query:
            terms = [term for term in query.lower().split() if len(term) > 1]
            if terms:
                clauses = []
                for term in terms[:12]:
                    clauses.append("(lower(title) LIKE ? OR lower(summary) LIKE ? OR lower(tags_json) LIKE ? OR lower(source_urls_json) LIKE ?)")
                    like = f"%{term}%"
                    params.extend([like, like, like, like])
                where = "WHERE " + " OR ".join(clauses)
        rows = self.conn.execute(
            f"""
            SELECT *
            FROM story_clusters
            {where}
            ORDER BY score DESC, updated_at DESC
            LIMIT ?
            """,
            params + [max(limit * 5, limit)],
        ).fetchall()
        stories = [story_from_row(row) for row in rows]
        self._attach_latest_item_metadata(stories)
        return self._rank_with_feedback(stories)[:limit]

    def list_stories_by_category(
        self,
        category: str,
        limit: int = 20,
        unique_by_source: bool = False,
    ) -> list[dict[str, Any]]:
        row_limit = max(limit * 5, limit) if unique_by_source else limit
        rows = self.conn.execute(
            """
            SELECT *
            FROM story_clusters
            WHERE category = ?
            ORDER BY updated_at DESC, score DESC
            LIMIT ?
            """,
            (category, row_limit),
        ).fetchall()
        stories = [story_from_row(row) for row in rows]
        self._attach_latest_item_metadata(stories)
        if not unique_by_source:
            return stories

        unique_stories: list[dict[str, Any]] = []
        seen: set[str] = set()
        for story in stories:
            source_urls = story.get("source_urls") or []
            key = source_urls[0] if source_urls else story["cluster_key"]
            if key in seen:
                continue
            seen.add(key)
            unique_stories.append(story)
            if len(unique_stories) >= limit:
                break
        return unique_stories

    def _attach_latest_item_metadata(self, stories: list[dict[str, Any]]) -> None:
        item_ids = {
            int(item_id)
            for story in stories
            for item_id in story.get("item_ids", [])
            if str(item_id).isdigit()
        }
        if not item_ids:
            return
        placeholders = ",".join("?" for _ in item_ids)
        rows = self.conn.execute(
            f"""
            SELECT id, published_at, retrieved_at, metrics_json
            FROM raw_items
            WHERE id IN ({placeholders})
            """,
            list(item_ids),
        ).fetchall()
        metadata = {
            int(row["id"]): {
                "published_at": row["published_at"] or "",
                "retrieved_at": row["retrieved_at"] or "",
                "metrics": _json_load(row["metrics_json"], {}),
            }
            for row in rows
        }
        for story in stories:
            candidates = [
                metadata[int(item_id)]
                for item_id in story.get("item_ids", [])
                if int(item_id) in metadata
            ]
            if not candidates:
                story.update({"published_at": "", "retrieved_at": "", "metrics": {}})
                continue
            latest = max(
                candidates,
                key=lambda item: item["retrieved_at"] or item["published_at"],
            )
            story.update(latest)

    def save_briefing(
        self,
        language: str,
        title: str,
        body: str,
        story_ids: list[int],
        canonical_body: str = "",
        briefing_group: str = "",
        generation_mode: str = "legacy",
        generation_status: str = "legacy",
        generation_model: str = "",
        translation_status: str = "legacy",
        translation_model: str = "",
    ) -> int:
        cur = self.conn.execute(
            """
            INSERT INTO briefings
                (language, title, body, canonical_body, briefing_group,
                 generation_mode, generation_status, generation_model,
                 translation_status, translation_model, story_ids_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                language,
                title,
                body,
                canonical_body,
                briefing_group,
                generation_mode,
                generation_status,
                generation_model,
                translation_status,
                translation_model,
                json.dumps(story_ids),
                tokyo_now_iso(),
            ),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def save_feedback(self, story_id: int, feedback: str, note: str = "") -> int:
        cur = self.conn.execute(
            "INSERT INTO feedback (story_id, feedback, note, created_at) VALUES (?, ?, ?, ?)",
            (story_id, feedback, note, tokyo_now_iso()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def log_llm_run(self, provider: str, model: str, ok: bool, error: str = "") -> None:
        self.conn.execute(
            "INSERT INTO llm_runs (provider, model, ok, error, created_at) VALUES (?, ?, ?, ?, ?)",
            (provider, model, int(ok), error[:500], tokyo_now_iso()),
        )
        self.conn.commit()

    def log_delivery(self, channel: str, status: str, message: dict[str, Any] | str = "") -> None:
        if isinstance(message, dict):
            message_text = json.dumps(message, ensure_ascii=False)
        else:
            message_text = str(message)
        self.conn.execute(
            "INSERT INTO delivery_logs (channel, status, message, created_at) VALUES (?, ?, ?, ?)",
            (channel, status, message_text[:1000], tokyo_now_iso()),
        )
        self.conn.commit()

    def log_pipeline_event(
        self,
        run_id: str,
        level: str,
        event: str,
        message: dict[str, Any],
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO pipeline_logs (run_id, level, event, message_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                level,
                event,
                json.dumps(message, ensure_ascii=False),
                tokyo_now_iso(),
            ),
        )
        self.conn.commit()

    def log_source_collection(
        self,
        run_id: str,
        source_id: str,
        source_name: str,
        status: str,
        fetched: int,
        inserted: int,
        existing: int,
        error: str = "",
        started_at: str = "",
        finished_at: str = "",
    ) -> None:
        now = tokyo_now_iso()
        self.conn.execute(
            """
            INSERT INTO source_collection_logs
                (run_id, source_id, source_name, status, fetched, inserted, existing,
                 error, started_at, finished_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                source_id,
                source_name,
                status,
                int(fetched),
                int(inserted),
                int(existing),
                error[:500],
                started_at or now,
                finished_at or now,
                now,
            ),
        )
        self.conn.commit()

    def _rank_with_feedback(self, stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not stories:
            return stories
        feedback_rows = self.conn.execute(
            """
            SELECT
                f.story_id,
                f.feedback,
                s.category,
                s.tags_json
            FROM feedback f
            JOIN story_clusters s ON s.id = f.story_id
            ORDER BY f.id DESC
            LIMIT 200
            """
        ).fetchall()
        if not feedback_rows:
            for story in stories:
                story["rank_score"] = story["score"]
                story["feedback_boost"] = 0.0
            return stories

        for story in stories:
            boost = 0.0
            story_tags = set(str(tag).lower() for tag in story.get("tags", []))
            for row in feedback_rows:
                feedback = row["feedback"]
                if int(row["story_id"]) == story["id"]:
                    boost += direct_feedback_weight(feedback)
                    continue

                if row["category"] == story["category"]:
                    boost += category_feedback_weight(feedback)

                feedback_tags = set(str(tag).lower() for tag in _json_load(row["tags_json"], []))
                overlap = story_tags.intersection(feedback_tags)
                if overlap:
                    boost += min(len(overlap), 4) * tag_feedback_weight(feedback)

            story["feedback_boost"] = round(boost, 2)
            story["rank_score"] = round(float(story["score"]) + boost, 2)
        return sorted(stories, key=lambda item: (item["rank_score"], item["updated_at"]), reverse=True)


def _json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def story_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "cluster_key": row["cluster_key"],
        "title": row["title"],
        "summary": row["summary"] or "",
        "category": row["category"],
        "subcategory": row["subcategory"] or "",
        "region": row["region"] or "global",
        "score": float(row["score"]),
        "source_urls": _json_load(row["source_urls_json"], []),
        "item_ids": _json_load(row["item_ids_json"], []),
        "tags": _json_load(row["tags_json"], []),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def direct_feedback_weight(feedback: str) -> float:
    return {
        "important": 30.0,
        "track_more": 24.0,
        "show_less": -24.0,
        "irrelevant": -45.0,
    }.get(feedback, 0.0)


def category_feedback_weight(feedback: str) -> float:
    return {
        "important": 3.0,
        "track_more": 5.0,
        "show_less": -4.0,
        "irrelevant": -6.0,
    }.get(feedback, 0.0)


def tag_feedback_weight(feedback: str) -> float:
    return {
        "important": 1.5,
        "track_more": 2.5,
        "show_less": -1.5,
        "irrelevant": -2.5,
    }.get(feedback, 0.0)
