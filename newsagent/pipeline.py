from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .collectors import build_collector
from .config import load_settings, load_sources
from .db import Database
from .delivery import EmailDelivery
from .llm import Summarizer, normalize_output_language
from .ranking import parse_dt, score_raw_item


WORLD_REGIONS = ["europe", "china", "us", "japan", "korea"]


class NewsAgentApp:
    def __init__(self, settings_path=None, sources_path=None):
        self.settings = load_settings(settings_path) if settings_path else load_settings()
        self.sources = load_sources(sources_path) if sources_path else load_sources()
        self.db = Database(self.settings["database"]["path"])
        self.db.init()
        for source in self.sources:
            self.db.upsert_source(source)
        self.db.commit()
        self.summarizer = Summarizer(self.settings, db=self.db)

    def close(self) -> None:
        self.db.close()

    def collect(self, limit: int | None = None) -> dict[str, Any]:
        per_source_limit = limit or int(self.settings.get("collection", {}).get("per_source_limit", 30))
        fetched = 0
        inserted = 0
        existing = 0
        errors = []
        for source in self.sources:
            if not source.enabled:
                continue
            try:
                collector = build_collector(source)
                items = collector.collect(limit=per_source_limit)
                for item in items:
                    fetched += 1
                    if self.db.insert_raw_item(item):
                        inserted += 1
                    else:
                        existing += 1
                self.db.commit()
            except Exception as exc:
                errors.append({"source": source.id, "error": str(exc)})
        clustered = self.cluster()
        return {
            "fetched": fetched,
            "inserted": inserted,
            "existing": existing,
            "clustered": clustered,
            "errors": errors,
        }

    def cluster(self) -> int:
        rows = self.db.get_unclustered_raw_items()
        count = 0
        for row in rows:
            self.db.upsert_story_from_raw(row, score_raw_item(row))
            count += 1
        self.db.commit()
        return count

    def brief(
        self,
        language: str | None = None,
        limit: int | None = None,
        output_language: str | None = None,
    ) -> tuple[int, str]:
        briefing_settings = self.settings.get("briefing", {})
        selected_language = normalize_output_language(
            output_language
            or language
            or self.settings.get("user", {}).get("default_language", "zh")
        )
        max_stories = limit or int(briefing_settings.get("max_stories", 65))
        stories = self._select_stories(max_stories)
        variants = self._save_briefing_variants(stories, selected_language)
        preferred = select_preferred_variant(
            variants,
            use_llm=bool(briefing_settings.get("use_llm", True)),
        )
        return preferred["id"], preferred["body"]

    def _save_briefing_variants(
        self,
        stories: list[dict[str, Any]],
        output_language: str,
    ) -> list[dict[str, Any]]:
        group = uuid4().hex
        story_ids = [story["id"] for story in stories]
        variants: list[dict[str, Any]] = []
        for use_llm in (False, True):
            briefing = self.summarizer.create_briefing(
                stories,
                output_language=output_language,
                use_llm=use_llm,
            )
            briefing_id = self.db.save_briefing(
                language=output_language,
                title=briefing_title(output_language, briefing.generation_mode),
                body=briefing.body,
                story_ids=story_ids,
                canonical_body=briefing.canonical_body,
                briefing_group=group,
                generation_mode=briefing.generation_mode,
                generation_status=briefing.generation_status,
                generation_model=briefing.generation_model,
                translation_status=briefing.translation_status,
                translation_model=briefing.translation_model,
            )
            variants.append(
                {
                    "id": briefing_id,
                    "body": briefing.body,
                    "mode": briefing.generation_mode,
                    "generation_status": briefing.generation_status,
                }
            )
        return variants

    def ask(self, question: str, language: str = "zh", limit: int = 12) -> str:
        stories = self.db.list_stories(limit=limit, query=expand_query(question))
        if not stories:
            stories = self.db.list_stories(limit=limit)
        return self.summarizer.answer_question(question, stories, language=language)

    def daily(
        self,
        language: str | None = None,
        collect_limit: int | None = None,
        brief_limit: int | None = None,
        email: bool = False,
        output_language: str | None = None,
    ) -> tuple[int, str, dict[str, Any], Path, dict[str, Any] | None]:
        collect_result = self.collect(limit=collect_limit)
        selected_language = normalize_output_language(
            output_language
            or language
            or self.settings.get("user", {}).get("default_language", "zh")
        )
        briefing_settings = self.settings.get("briefing", {})
        max_stories = brief_limit or int(briefing_settings.get("max_stories", 65))
        stories = self._select_stories(max_stories)
        variants = self._save_briefing_variants(stories, selected_language)
        preferred = select_preferred_variant(
            variants,
            use_llm=bool(briefing_settings.get("use_llm", True)),
        )
        briefing_id = preferred["id"]
        body = preferred["body"]
        outbox = Path(self.settings["database"]["path"]).parent / "outbox"
        outbox.mkdir(parents=True, exist_ok=True)
        for variant in variants:
            variant_path = outbox / f"briefing_{variant['id']}_{variant['mode']}.md"
            variant_path.write_text(variant["body"], encoding="utf-8")
            (outbox / f"latest_{variant['mode']}.md").write_text(
                variant["body"],
                encoding="utf-8",
            )
        path = outbox / f"briefing_{briefing_id}_{preferred['mode']}.md"
        (outbox / "latest.md").write_text(body, encoding="utf-8")
        email_result = self.send_email(body, briefing_id=briefing_id) if email else None
        return briefing_id, body, collect_result, path, email_result

    def _select_stories(self, max_stories: int) -> list[dict[str, Any]]:
        briefing_settings = self.settings.get("briefing", {})
        candidate_limit = max(max_stories * 3, 120)
        candidates = merge_unique_stories(
            self.db.list_stories(limit=40, query="market stock_index sector oil fx")
            + self.db.list_stories(limit=300, query="mainstream world europe china us japan korea globaltimes cctv cgtn xinhua bbc npr nhk yonhap")
            + self.db.list_stories(limit=80, query="china cctv cgtn xinhua globaltimes youtube video official xinwen_lianbo")
            + self.db.list_stories(limit=80, query="medicine medical health journal fda who lancet nejm jama nature digital_medicine digital_health regulation clinical")
            + self.db.list_stories(limit=candidate_limit)
        )
        candidates = filter_recent_news(
            candidates,
            lookback_hours=int(briefing_settings.get("lookback_hours", 48)),
        )
        return select_briefing_stories(candidates, max_stories=max_stories)

    def send_email(
        self,
        body: str,
        briefing_id: int | None = None,
        subject: str | None = None,
    ) -> dict[str, Any]:
        subject_id = f" #{briefing_id}" if briefing_id else ""
        subject = subject or f"NewsAgent Daily Brief{subject_id}"
        return EmailDelivery.from_settings(self.settings).send(subject=subject, body=body)

    def feedback(self, story_id: int, feedback: str, note: str = "") -> int:
        allowed = {"important", "irrelevant", "show_less", "track_more"}
        if feedback not in allowed:
            raise ValueError(f"feedback must be one of: {', '.join(sorted(allowed))}")
        return self.db.save_feedback(story_id, feedback, note)

    def doctor(self) -> dict[str, Any]:
        llm = self.settings.get("llm", {})
        delivery = self.settings.get("delivery", {}).get("email", {})
        return {
            "database": self.settings["database"]["path"],
            "sources": len(self.sources),
            "enabled_sources": len([s for s in self.sources if s.enabled]),
            "llm_provider": llm.get("provider"),
            "llm_model": llm.get("model"),
            "ollama_available": self.summarizer.ollama.available(),
            "email_enabled": bool(delivery.get("enabled", False)),
            "email_configured": EmailDelivery.is_configured(delivery),
        }


def select_briefing_stories(
    candidates: list[dict[str, Any]],
    max_stories: int = 65,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()

    def add(story: dict[str, Any]) -> None:
        story_id = int(story["id"])
        if story_id in seen or len(selected) >= max_stories:
            return
        seen.add(story_id)
        selected.append(story)

    market = [s for s in candidates if s.get("category") == "market"]
    for story in market[:24]:
        add(story)

    world_news = [s for s in candidates if s.get("category") == "world_news"]
    for region in WORLD_REGIONS:
        for story in [s for s in world_news if s.get("region") == region and not is_stale_world_story(s)][:5]:
            add(story)

    medicine = [s for s in candidates if s.get("category") == "medicine"]
    policy_medical = [
        s
        for s in candidates
        if s.get("category") == "policy"
        and ("medicine" in s.get("tags", []) or "medical" in s.get("tags", []) or "health" in s.get("tags", []))
    ]
    for story in (medicine + policy_medical)[:8]:
        add(story)

    ai_tech = [s for s in candidates if s.get("category") in {"ai", "ai_engineering", "ai_hardware"}]
    for story in ai_tech[:8]:
        add(story)

    for story in candidates:
        add(story)
        if len(selected) >= max_stories:
            break
    return selected


def merge_unique_stories(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[int] = set()
    for story in stories:
        story_id = int(story["id"])
        if story_id in seen:
            continue
        seen.add(story_id)
        merged.append(story)
    return merged


def filter_recent_news(
    stories: list[dict[str, Any]],
    lookback_hours: int,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    if lookback_hours <= 0:
        return stories
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(hours=lookback_hours)
    result = []
    for story in stories:
        if story.get("category") == "market":
            result.append(story)
            continue
        timestamp = parse_dt(story.get("published_at") or "")
        if timestamp is None:
            timestamp = parse_dt(story.get("retrieved_at") or "")
        if timestamp is None or timestamp >= cutoff:
            result.append(story)
    return result


def is_stale_world_story(story: dict[str, Any]) -> bool:
    urls = story.get("source_urls") or []
    source = urls[0] if urls else ""
    return "english.people.com.cn" in source or "chinadaily.com.cn/a/2017" in source


def expand_query(question: str) -> str:
    text = question.strip()
    lower = text.lower()
    expansions: list[str] = []
    if any(token in lower for token in ["ai chip", "gpu", "semiconductor", "data center", "infrastructure", "芯片", "算力", "数据中心"]):
        expansions.extend(
            [
                "ai_hardware",
                "hardware",
                "gpu",
                "nvidia",
                "amd",
                "intel",
                "tsmc",
                "semiconductor",
                "data center",
                "infrastructure",
                "cloud",
            ]
        )
    if any(token in lower for token in ["medicine", "health", "medical", "clinical", "医学", "医疗", "药物", "临床", "健康"]):
        expansions.extend(["medicine", "health", "medical", "journal", "regulation", "clinical"])
    if any(token in lower for token in ["policy", "regulation", "government", "政策", "监管"]):
        expansions.extend(["policy", "regulation", "government"])
    if any(token in lower for token in ["market", "fx", "oil", "usd", "jpy", "市场", "油价", "汇率", "美元", "日元"]):
        expansions.extend(["market", "oil", "fx", "usd", "jpy", "gbp", "cny"])
    if any(token in lower for token in ["github", "hugging face", "model", "open source", "模型", "开源", "项目"]):
        expansions.extend(["github", "huggingface", "model", "ai_engineering"])
    return " ".join([text] + expansions)


def briefing_title(language: str, generation_mode: str = "") -> str:
    base = {
        "original": "Daily Brief (Original Languages)",
        "zh": "每日情报简报",
        "en": "Daily Brief",
        "ja": "デイリー情報ブリーフ",
    }[normalize_output_language(language)]
    suffixes = {
        "rules": {
            "original": "Rules",
            "zh": "规则版",
            "en": "Rules",
            "ja": "ルール版",
        },
        "llm": {
            "original": "LLM",
            "zh": "LLM 版",
            "en": "LLM",
            "ja": "LLM 版",
        },
    }
    if generation_mode not in suffixes:
        return base
    suffix = suffixes[generation_mode][normalize_output_language(language)]
    return f"{base} ({suffix})"


def select_preferred_variant(
    variants: list[dict[str, Any]],
    use_llm: bool,
) -> dict[str, Any]:
    preferred_mode = "llm" if use_llm else "rules"
    return next(
        (variant for variant in variants if variant["mode"] == preferred_mode),
        variants[0],
    )
