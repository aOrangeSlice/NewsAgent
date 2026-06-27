from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .collectors import build_collector
from .config import ROOT, load_settings, load_sources
from .db import Database
from .delivery import EmailDelivery
from .llm import Summarizer, normalize_output_language
from .models import tokyo_now_iso
from .ranking import parse_dt, score_raw_item
from .security import scan_for_secrets


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

    def collect(self, limit: int | None = None, run_id: str | None = None) -> dict[str, Any]:
        run_id = run_id or uuid4().hex
        per_source_limit = limit or int(self.settings.get("collection", {}).get("per_source_limit", 30))
        fetched = 0
        inserted = 0
        existing = 0
        errors = []
        source_stats = []
        for source in self.sources:
            if not source.enabled:
                continue
            source_fetched = 0
            source_inserted = 0
            source_existing = 0
            source_started_at = tokyo_now_iso()
            try:
                collector = build_collector(source)
                items = collector.collect(limit=per_source_limit)
                for item in items:
                    fetched += 1
                    source_fetched += 1
                    if self.db.insert_raw_item(item):
                        inserted += 1
                        source_inserted += 1
                    else:
                        existing += 1
                        source_existing += 1
                self.db.commit()
                source_finished_at = tokyo_now_iso()
                self.db.log_source_collection(
                    run_id=run_id,
                    source_id=source.id,
                    source_name=source.name,
                    status="success",
                    fetched=source_fetched,
                    inserted=source_inserted,
                    existing=source_existing,
                    started_at=source_started_at,
                    finished_at=source_finished_at,
                )
                source_stats.append(
                    {
                        "source": source.id,
                        "source_name": source.name,
                        "status": "success",
                        "fetched": source_fetched,
                        "inserted": source_inserted,
                        "existing": source_existing,
                    }
                )
            except Exception as exc:
                source_finished_at = tokyo_now_iso()
                error = {"source": source.id, "error": str(exc)}
                errors.append(error)
                self.db.log_source_collection(
                    run_id=run_id,
                    source_id=source.id,
                    source_name=source.name,
                    status="failed",
                    fetched=source_fetched,
                    inserted=source_inserted,
                    existing=source_existing,
                    error=str(exc),
                    started_at=source_started_at,
                    finished_at=source_finished_at,
                )
                source_stats.append(
                    {
                        "source": source.id,
                        "source_name": source.name,
                        "status": "failed",
                        "fetched": source_fetched,
                        "inserted": source_inserted,
                        "existing": source_existing,
                        "error": str(exc),
                    }
                )
        clustered = self.cluster()
        return {
            "run_id": run_id,
            "fetched": fetched,
            "inserted": inserted,
            "existing": existing,
            "clustered": clustered,
            "errors": errors,
            "sources": source_stats,
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
        selected_language = normalize_output_language(language)
        stories = self.db.list_stories(limit=limit, query=expand_query(question))
        if not stories:
            stories = self.db.list_stories(limit=limit)
        return self.summarizer.answer_question(question, stories, language=selected_language)

    def daily(
        self,
        language: str | None = None,
        collect_limit: int | None = None,
        brief_limit: int | None = None,
        email: bool = False,
        output_language: str | None = None,
        run_id: str | None = None,
    ) -> tuple[int, str, dict[str, Any], Path, dict[str, Any] | None]:
        run_id = run_id or uuid4().hex
        collect_result = self.collect(limit=collect_limit, run_id=run_id)
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
        email_result = (
            self._send_daily_email_variants(
                variants,
                use_llm=bool(briefing_settings.get("use_llm", True)),
            )
            if email
            else None
        )
        self.last_daily_summary = build_daily_summary(
            run_id=run_id,
            briefing_id=briefing_id,
            preferred=preferred,
            variants=variants,
            stories=stories,
            collect_result=collect_result,
            outbox_path=path,
            email_result=email_result,
            min_stories=int(briefing_settings.get("min_stories", 0)),
        )
        return briefing_id, body, collect_result, path, email_result

    def _send_daily_email_variants(
        self,
        variants: list[dict[str, Any]],
        use_llm: bool,
    ) -> dict[str, Any]:
        modes_to_send = {"rules", "llm"} if use_llm else {"rules"}
        deliveries = []
        for variant in variants:
            mode = variant["mode"]
            if mode not in modes_to_send:
                continue
            label = "LLM" if mode == "llm" else "Rules"
            result = self.send_email(
                variant["body"],
                briefing_id=variant["id"],
                subject=f"NewsAgent Daily Brief [{label}] #{variant['id']}",
            )
            deliveries.append(
                {
                    "mode": mode,
                    "briefing_id": variant["id"],
                    "generation_status": variant["generation_status"],
                    **result,
                }
            )
        return {
            "ok": bool(deliveries) and all(item.get("ok", False) for item in deliveries),
            "deliveries": deliveries,
        }

    def _select_stories(self, max_stories: int) -> list[dict[str, Any]]:
        briefing_settings = self.settings.get("briefing", {})
        candidate_limit = max(max_stories * 3, 120)
        candidates = merge_unique_stories(
            self.db.list_stories_by_category("market", limit=80, unique_by_source=True)
            + self.db.list_stories(limit=80, query="market stock_index sector oil fx")
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
        try:
            result = EmailDelivery.from_settings(self.settings).send(subject=subject, body=body)
        except Exception as exc:
            if hasattr(self, "db"):
                self.db.log_delivery(
                    "email",
                    "failed",
                    {
                        "briefing_id": briefing_id,
                        "subject": subject,
                        "error": str(exc),
                    },
                )
            raise

        if hasattr(self, "db"):
            self.db.log_delivery(
                "email",
                "success" if result.get("ok") else "failed",
                {
                    "briefing_id": briefing_id,
                    "subject": subject,
                    "ok": bool(result.get("ok")),
                    "recipient_count": len(result.get("recipients", [])),
                    "error": result.get("error", ""),
                },
            )
        return result

    def feedback(self, story_id: int, feedback: str, note: str = "") -> int:
        allowed = {"important", "irrelevant", "show_less", "track_more"}
        if feedback not in allowed:
            raise ValueError(f"feedback must be one of: {', '.join(sorted(allowed))}")
        return self.db.save_feedback(story_id, feedback, note)

    def doctor(self) -> dict[str, Any]:
        llm = self.settings.get("llm", {})
        delivery = self.settings.get("delivery", {}).get("email", {})
        secret_findings = scan_for_secrets(ROOT)
        return {
            "database": self.settings["database"]["path"],
            "sources": len(self.sources),
            "enabled_sources": len([s for s in self.sources if s.enabled]),
            "llm_provider": llm.get("provider"),
            "llm_model": llm.get("model"),
            "ollama_available": self.summarizer.ollama.available(),
            "email_enabled": bool(delivery.get("enabled", False)),
            "email_configured": EmailDelivery.is_configured(delivery),
            "secret_scan_ok": not secret_findings,
            "secret_findings": len(secret_findings),
        }

    def source_health(self, recent_runs: int = 10) -> list[dict[str, Any]]:
        logged = {
            item["source"]: item
            for item in self.db.list_source_health(
                [source.id for source in self.sources],
                recent_runs=recent_runs,
            )
        }
        result = []
        for source in self.sources:
            health = logged.get(source.id)
            if health is None:
                health = {
                    "source": source.id,
                    "source_name": source.name,
                    "recent_runs": 0,
                    "recent_successes": 0,
                    "recent_failures": 0,
                    "recent_fetched": 0,
                    "recent_inserted": 0,
                    "recent_existing": 0,
                    "last_status": "never_run",
                    "last_error": "",
                    "last_started_at": "",
                    "last_finished_at": "",
                    "last_checked_at": "",
                }
            health = dict(health)
            health["enabled"] = bool(source.enabled)
            health["kind"] = source.kind
            health["category"] = source.category
            runs = int(health["recent_runs"])
            failures = int(health["recent_failures"])
            health["failure_rate"] = round(failures / runs, 2) if runs else 0.0
            result.append(health)
        return sorted(
            result,
            key=lambda item: (
                not item["enabled"],
                -int(item["recent_failures"]),
                item["last_status"] == "success",
                item["source"],
            ),
        )

    def secret_scan(self) -> list[dict[str, Any]]:
        return scan_for_secrets(ROOT)


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

    market_quota = min(45, max(12, max_stories // 2))
    medicine_quota = min(5, max(0, max_stories // 12))
    ai_quota = min(5, max(0, max_stories // 12))
    world_quota = max(0, max_stories - market_quota - medicine_quota - ai_quota)

    market = prioritize_market_stories([s for s in candidates if s.get("category") == "market"])
    for story in market[:market_quota]:
        add(story)

    world_added = 0
    world_news = sort_by_freshness_and_score(
        [s for s in candidates if s.get("category") == "world_news"]
    )
    for region in WORLD_REGIONS:
        if world_added >= world_quota:
            break
        for story in [s for s in world_news if s.get("region") == region and not is_stale_world_story(s)][:5]:
            if world_added >= world_quota:
                break
            before = len(selected)
            add(story)
            if len(selected) > before:
                world_added += 1

    medicine = sort_by_freshness_and_score([s for s in candidates if s.get("category") == "medicine"])
    policy_medical = sort_by_freshness_and_score([
        s
        for s in candidates
        if s.get("category") == "policy"
        and ("medicine" in s.get("tags", []) or "medical" in s.get("tags", []) or "health" in s.get("tags", []))
    ])
    for story in (medicine + policy_medical)[:medicine_quota]:
        add(story)

    ai_tech = sort_by_freshness_and_score(
        [s for s in candidates if s.get("category") in {"ai", "ai_engineering", "ai_hardware"}]
    )
    for story in ai_tech[:ai_quota]:
        add(story)

    for story in sort_by_freshness_and_score(candidates):
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


GLOBAL_INDEX_SYMBOLS = {
    "^GSPC",
    "^DJI",
    "^IXIC",
    "000001.SS",
    "^N225",
    "^FTSE",
    "^GDAXI",
    "^FCHI",
    "^HSI",
    "^KS11",
}

US_SECTOR_SYMBOLS = {"XLK", "XLF", "XLV", "XLE", "XLI", "XLY"}

COMMODITY_FX_SYMBOLS = {
    "CL=F",
    "BZ=F",
    "JPY=X",
    "JPYUSD=X",
    "GBPUSD=X",
    "CNY=X",
    "CNYUSD=X",
    "CNYJPY=X",
    "JPYCNY=X",
}


def prioritize_market_stories(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    stories = unique_market_stories_by_source(stories)
    buckets = {
        "global_indices": [],
        "commodities_fx": [],
        "us_sectors": [],
        "international_sectors": [],
        "other": [],
    }
    for story in stories:
        symbol = extract_market_symbol((story.get("source_urls") or [""])[0])
        if symbol in GLOBAL_INDEX_SYMBOLS:
            buckets["global_indices"].append(story)
        elif symbol in COMMODITY_FX_SYMBOLS:
            buckets["commodities_fx"].append(story)
        elif symbol in US_SECTOR_SYMBOLS:
            buckets["us_sectors"].append(story)
        elif story.get("category") == "market":
            buckets["international_sectors"].append(story)
        else:
            buckets["other"].append(story)
    return (
        buckets["global_indices"]
        + buckets["commodities_fx"]
        + buckets["us_sectors"]
        + buckets["international_sectors"]
        + buckets["other"]
    )


def unique_market_stories_by_source(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for story in stories:
        source_urls = story.get("source_urls") or []
        key = source_urls[0] if source_urls else story.get("cluster_key") or story.get("title")
        if key in seen:
            continue
        seen.add(key)
        result.append(story)
    return result


def sort_by_freshness_and_score(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(stories, key=freshness_score_key, reverse=True)


def freshness_score_key(story: dict[str, Any]) -> tuple[float, float]:
    timestamp = parse_dt(story.get("published_at") or "") or parse_dt(story.get("retrieved_at") or "")
    return (
        timestamp.timestamp() if timestamp else 0.0,
        float(story.get("score") or 0),
    )


def extract_market_symbol(url: str) -> str:
    marker = "/quote/"
    if marker not in url:
        return ""
    return url.split(marker, 1)[1].split("?", 1)[0].strip("/")


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


def build_daily_summary(
    run_id: str,
    briefing_id: int,
    preferred: dict[str, Any],
    variants: list[dict[str, Any]],
    stories: list[dict[str, Any]],
    collect_result: dict[str, Any],
    outbox_path: Path,
    email_result: dict[str, Any] | None,
    min_stories: int = 0,
) -> dict[str, Any]:
    category_counts: dict[str, int] = {}
    for story in stories:
        category = str(story.get("category") or "unknown")
        category_counts[category] = category_counts.get(category, 0) + 1

    return {
        "run_id": run_id,
        "collect": {
            "run_id": run_id,
            "fetched": collect_result.get("fetched", 0),
            "inserted": collect_result.get("inserted", 0),
            "existing": collect_result.get("existing", 0),
            "clustered": collect_result.get("clustered", 0),
            "error_count": len(collect_result.get("errors", [])),
            "errors": collect_result.get("errors", []),
            "sources": collect_result.get("sources", []),
        },
        "briefing": {
            "run_id": run_id,
            "briefing_id": briefing_id,
            "mode": preferred.get("mode", ""),
            "generation_status": preferred.get("generation_status", ""),
            "story_count": len(stories),
            "min_stories": min_stories,
            "story_ids": [int(story["id"]) for story in stories if "id" in story],
            "category_counts": category_counts,
            "top_titles": [
                {
                    "id": int(story["id"]),
                    "title": story.get("title", ""),
                    "url": (story.get("source_urls") or [""])[0],
                }
                for story in stories[:5]
                if "id" in story
            ],
            "variants": [
                {
                    "id": variant.get("id"),
                    "mode": variant.get("mode"),
                    "generation_status": variant.get("generation_status"),
                }
                for variant in variants
            ],
            "outbox_path": str(outbox_path),
        },
        "email": email_result,
    }


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
