from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib import error, request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import html
import json
import re


WORLD_REGION_LABELS = {
    "europe": "欧洲",
    "china": "中国",
    "us": "美国",
    "japan": "日本",
    "korea": "韩国",
}
WORLD_REGION_LABELS_EN = {
    "europe": "Europe",
    "china": "China",
    "us": "United States",
    "japan": "Japan",
    "korea": "South Korea",
}

WORLD_REGION_ORDER = ["europe", "china", "us", "japan", "korea"]

SUPPORTED_OUTPUT_LANGUAGES = {"original", "zh", "en", "ja"}
LANGUAGE_NAMES = {
    "zh": "Simplified Chinese",
    "en": "English",
    "ja": "Japanese",
}


@dataclass
class BriefingOutput:
    canonical_body: str
    body: str
    output_language: str
    generation_mode: str
    generation_status: str
    translation_status: str
    generation_model: str = ""
    translation_model: str = ""

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


class OllamaClient:
    def __init__(self, base_url: str, model: str, temperature: float = 0.2, num_ctx: int = 8192):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.num_ctx = num_ctx

    def available(self) -> bool:
        try:
            req = request.Request(f"{self.base_url}/api/tags")
            with request.urlopen(req, timeout=2) as resp:
                return resp.status == 200
        except Exception:
            return False

    def generate(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
            },
        }
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/api/generate",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            with request.urlopen(req, timeout=180) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return body.get("response", "").strip()
        except error.HTTPError as exc:
            raise RuntimeError(exc.read().decode("utf-8", errors="replace")) from exc


class Summarizer:
    def __init__(self, settings: dict[str, Any], db: Any | None = None):
        self.settings = settings
        llm_settings = settings.get("llm", {})
        self.provider = llm_settings.get("provider", "ollama")
        self.model = llm_settings.get("model", "qwen3:8b")
        self.db = db
        self.ollama = OllamaClient(
            base_url=llm_settings.get("base_url", "http://localhost:11434"),
            model=self.model,
            temperature=float(llm_settings.get("temperature", 0.2)),
            num_ctx=int(llm_settings.get("num_ctx", 8192)),
        )

    def create_briefing(
        self,
        stories: list[dict[str, Any]],
        output_language: str = "zh",
        use_llm: bool | None = None,
    ) -> BriefingOutput:
        output_language = normalize_output_language(output_language)
        should_use_llm = (
            bool(self.settings.get("briefing", {}).get("use_llm", True))
            if use_llm is None
            else use_llm
        )
        canonical_body, generation_status = self.summarize_canonical_briefing(
            stories,
            use_llm=should_use_llm,
        )
        body, translation_status = self.translate_briefing(canonical_body, output_language)
        freshness = build_freshness_section(stories, self.settings, output_language)
        body = add_freshness_section(body, freshness)
        return BriefingOutput(
            canonical_body=canonical_body,
            body=body,
            output_language=output_language,
            generation_mode="llm" if should_use_llm else "rules",
            generation_status=generation_status,
            generation_model=self.model if generation_status == "generated" else "",
            translation_status=translation_status,
            translation_model=self.model if translation_status == "translated" else "",
        )

    def summarize_briefing(self, stories: list[dict[str, Any]], language: str = "zh") -> str:
        """Compatibility wrapper for callers that still pass ``language``."""
        return self.create_briefing(stories, output_language=language).body

    def summarize_canonical_briefing(
        self,
        stories: list[dict[str, Any]],
        use_llm: bool | None = None,
    ) -> tuple[str, str]:
        should_use_llm = (
            bool(self.settings.get("briefing", {}).get("use_llm", True))
            if use_llm is None
            else use_llm
        )
        if not should_use_llm:
            return fallback_briefing(stories, "original"), "deterministic"
        if self.ollama.available():
            prompt = build_briefing_prompt(stories, "original")
            try:
                result = self.ollama.generate(prompt)
                if self.db:
                    self.db.log_llm_run(self.provider, self.model, True)
                return clean_model_output(result), "generated"
            except Exception as exc:
                if self.db:
                    self.db.log_llm_run(self.provider, self.model, False, str(exc))
        return fallback_briefing(stories, "original"), "fallback_rules"

    def translate_briefing(self, canonical_body: str, output_language: str) -> tuple[str, str]:
        output_language = normalize_output_language(output_language)
        if output_language == "original":
            return canonical_body, "not_requested"

        translation_settings = self.settings.get("translation", {})
        if translation_settings.get("enabled", True) and self.ollama.available():
            try:
                result = self.translate_markdown_in_chunks(
                    canonical_body,
                    output_language,
                    max_chars=int(translation_settings.get("max_chunk_chars", 4500)),
                )
                if validate_translation(canonical_body, result):
                    if self.db:
                        self.db.log_llm_run(self.provider, self.model, True)
                    return result, "translated"
                raise ValueError("translated briefing changed or removed protected references")
            except Exception as exc:
                if self.db:
                    self.db.log_llm_run(self.provider, self.model, False, f"translation: {exc}")

        partial = localize_briefing_structure(canonical_body, output_language)
        warning = translation_fallback_warning(output_language)
        return f"{partial}\n\n{warning}".strip(), "fallback_original"

    def translate_markdown_in_chunks(
        self,
        markdown: str,
        output_language: str,
        max_chars: int = 4500,
    ) -> str:
        chunks = split_markdown_for_translation(markdown, max_chars=max_chars)
        translated_chunks = []
        for chunk_index, chunk in enumerate(chunks, start=1):
            protected, replacements = protect_translation_tokens(chunk)
            prompt = build_translation_prompt(
                protected,
                output_language,
                chunk_index=chunk_index,
                chunk_count=len(chunks),
            )
            translated = clean_model_output(self.ollama.generate(prompt))
            restored = restore_translation_tokens(translated, replacements)
            if not validate_translation(chunk, restored):
                raise ValueError(
                    f"translation chunk {chunk_index}/{len(chunks)} changed "
                    "or removed protected references"
                )
            translated_chunks.append(restored.strip())
        return "\n\n".join(translated_chunks).strip()

    def answer_question(self, question: str, stories: list[dict[str, Any]], language: str = "zh") -> str:
        if self.ollama.available():
            prompt = build_answer_prompt(question, stories, language)
            try:
                result = self.ollama.generate(prompt)
                if self.db:
                    self.db.log_llm_run(self.provider, self.model, True)
                return clean_model_output(result)
            except Exception as exc:
                if self.db:
                    self.db.log_llm_run(self.provider, self.model, False, str(exc))
        return fallback_answer(question, stories, language)


def build_briefing_prompt(stories: list[dict[str, Any]], language: str) -> str:
    evidence = json.dumps(stories, ensure_ascii=False, indent=2)[:26000]
    language_instruction = (
        "Preserve each story title and summary in its source language. "
        "Use English only for section headings and connective editorial text."
        if language == "original"
        else f"Write in language: {language}."
    )
    return f"""
/no_think
You are NewsAgent, a local-first intelligence briefing assistant.
{language_instruction}
Use only the evidence JSON. Every factual bullet must cite a source URL.
Group market data into global indices, US sectors, and commodities/FX.
Group mainstream media by region and list up to 5 important stories per region.
Always include a medical/health section with 5 high-signal items when available; if evidence is sparse, write watch items instead of saying there is no evidence.

Evidence JSON:
{evidence}
""".strip()


def build_translation_prompt(
    markdown: str,
    output_language: str,
    chunk_index: int = 1,
    chunk_count: int = 1,
) -> str:
    target = LANGUAGE_NAMES[normalize_output_language(output_language)]
    return f"""
/no_think
Translate the following completed intelligence briefing into {target}.
This is Markdown chunk {chunk_index} of {chunk_count}.

Rules:
- Return only the translated Markdown document.
- Preserve Markdown structure exactly.
- Tokens such as __NEWSAGENT_PROTECTED_0001__ are immutable placeholders.
- Copy every immutable placeholder exactly once and do not translate, reformat, or remove it.
- Do not alter stock symbols, numbers, dates, or code spans.
- Translate headings, titles, summaries, analysis, labels, and warnings.
- Keep established organization and product names in their conventional form.
- Do not add facts, explanations, or commentary.

Markdown:
{markdown}
""".strip()


def split_markdown_for_translation(markdown: str, max_chars: int = 4500) -> list[str]:
    if max_chars < 500:
        raise ValueError("translation max_chunk_chars must be at least 500")
    blocks = re.split(r"\n\s*\n", markdown.strip())
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    def flush() -> None:
        nonlocal current, current_length
        if current:
            chunks.append("\n\n".join(current))
            current = []
            current_length = 0

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        if len(block) > max_chars:
            flush()
            lines = block.splitlines()
            line_group: list[str] = []
            line_length = 0
            for line in lines:
                added = len(line) + (1 if line_group else 0)
                if line_group and line_length + added > max_chars:
                    chunks.append("\n".join(line_group))
                    line_group = []
                    line_length = 0
                line_group.append(line)
                line_length += len(line) + (1 if line_length else 0)
            if line_group:
                chunks.append("\n".join(line_group))
            continue

        added = len(block) + (2 if current else 0)
        if current and current_length + added > max_chars:
            flush()
        current.append(block)
        current_length += len(block) + (2 if current_length else 0)

    flush()
    return chunks or [""]


def protect_translation_tokens(text: str) -> tuple[str, dict[str, str]]:
    replacements: dict[str, str] = {}
    pattern = re.compile(r"https?://[^\s)>]+|\[\d+\]")

    def replace(match: re.Match[str]) -> str:
        token = f"__NEWSAGENT_PROTECTED_{len(replacements) + 1:04d}__"
        replacements[token] = match.group(0)
        return token

    return pattern.sub(replace, text), replacements


def restore_translation_tokens(text: str, replacements: dict[str, str]) -> str:
    restored = text
    for token, original in replacements.items():
        if restored.count(token) != 1:
            raise ValueError(f"translation changed protected token {token}")
        restored = restored.replace(token, original)
    return restored


def normalize_output_language(language: str | None) -> str:
    normalized = (language or "zh").strip().lower()
    aliases = {
        "cn": "zh",
        "zh-cn": "zh",
        "chinese": "zh",
        "english": "en",
        "jp": "ja",
        "ja-jp": "ja",
        "japanese": "ja",
        "source": "original",
        "raw": "original",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in SUPPORTED_OUTPUT_LANGUAGES:
        allowed = ", ".join(sorted(SUPPORTED_OUTPUT_LANGUAGES))
        raise ValueError(f"output language must be one of: {allowed}")
    return normalized


def validate_translation(source: str, translated: str) -> bool:
    if not translated.strip():
        return False
    protected_patterns = [
        r"https?://[^\s)>]+",
        r"\[\d+\]",
    ]
    for pattern in protected_patterns:
        if sorted(re.findall(pattern, source)) != sorted(re.findall(pattern, translated)):
            return False
    return True


def localize_briefing_structure(markdown: str, language: str) -> str:
    replacements = {
        "zh": {
            "# Daily Brief": "# 每日情报简报",
            "## Market overview: global indices and sectors": "## 市场先看：全球指数与板块",
            "### Global indices": "### 全球主要指数",
            "### U.S. sectors": "### 美股行业板块",
            "### Commodities and FX": "### 商品与外汇",
            "## Important mainstream news by region — Top 5": "## 各地区主流媒体重要新闻 Top 5",
            "## Medical and health — Top 5": "## 医疗与健康资讯 Top 5",
            "## AI and technology": "## AI 与科技",
            "## Selection logic": "## 判断逻辑",
            "## Sources": "## 来源",
            "### Europe": "### 欧洲",
            "### China": "### 中国",
            "### United States": "### 美国",
            "### Japan": "### 日本",
            "### South Korea": "### 韩国",
            "Source:": "来源：",
        },
        "ja": {
            "# Daily Brief": "# デイリー情報ブリーフ",
            "## Market overview: global indices and sectors": "## 市場概況：世界の指数とセクター",
            "### Global indices": "### 世界の主要指数",
            "### U.S. sectors": "### 米国株セクター",
            "### Commodities and FX": "### 商品・為替",
            "## Important mainstream news by region — Top 5": "## 地域別の主要ニュース Top 5",
            "## Medical and health — Top 5": "## 医療・ヘルスケア Top 5",
            "## AI and technology": "## AI・テクノロジー",
            "## Selection logic": "## 選定ロジック",
            "## Sources": "## 情報源",
            "### Europe": "### 欧州",
            "### China": "### 中国",
            "### United States": "### 米国",
            "### Japan": "### 日本",
            "### South Korea": "### 韓国",
            "Source:": "情報源：",
        },
    }
    result = markdown
    for source, target in replacements.get(language, {}).items():
        result = result.replace(source, target)
    return result


def translation_fallback_warning(language: str) -> str:
    return {
        "zh": "> 翻译不可用：新闻正文保留原文语言，链接和编号未改变。",
        "ja": "> 翻訳を利用できないため、ニュース本文は原文のまま表示しています。リンクと番号は変更していません。",
        "en": "> Translation unavailable: story text remains in its original language; links and IDs are unchanged.",
    }.get(language, "")


def build_answer_prompt(question: str, stories: list[dict[str, Any]], language: str) -> str:
    evidence = json.dumps(stories, ensure_ascii=False, indent=2)[:26000]
    return f"""
/no_think
You are NewsAgent, a local-first evidence-based news analyst.
Answer in language: {language}.
Question: {question}

Rules:
- Use only the evidence JSON.
- Separate facts, interpretation, and uncertainty.
- Cite source URLs.
- If the evidence is insufficient, say exactly what is missing.
- Keep the answer concise but useful.

Evidence JSON:
{evidence}
""".strip()


def build_freshness_section(
    stories: list[dict[str, Any]],
    settings: dict[str, Any],
    language: str,
    generated_at: datetime | None = None,
) -> str:
    timezone_name = settings.get("user", {}).get("timezone", "UTC")
    try:
        local_tz = ZoneInfo(timezone_name)
    except Exception:
        timezone_name = "UTC"
        local_tz = timezone.utc
    generated = (generated_at or datetime.now(timezone.utc)).astimezone(local_tz)

    news_times = [
        parse_iso_datetime(story.get("retrieved_at"))
        for story in stories
        if story.get("category") != "market"
    ]
    news_times = [value for value in news_times if value is not None]
    latest_news = max(news_times).astimezone(local_tz) if news_times else None

    us_market = [
        story
        for story in stories
        if story.get("category") == "market"
        and str((story.get("metrics") or {}).get("symbol", "")) in US_SECTOR_SYMBOLS
    ]
    market_story = max(
        us_market,
        key=lambda story: (
            (story.get("metrics") or {}).get("quote_time", ""),
            story.get("retrieved_at", ""),
        ),
        default=None,
    )

    if language == "zh":
        lines = [
            "## 数据新鲜度",
            "",
            f"- 简报生成：{format_local_time(generated, timezone_name)}",
        ]
        if latest_news:
            lines.append(f"- 入选新闻最新采集：{format_local_time(latest_news, timezone_name)}")
        else:
            lines.append("- 入选新闻最新采集：无可用时间信息")
        lines.extend(render_us_market_freshness_zh(market_story, local_tz, timezone_name))
        return "\n".join(lines)

    if language == "ja":
        lines = [
            "## データ鮮度",
            "",
            f"- ブリーフ生成：{format_local_time(generated, timezone_name)}",
        ]
        if latest_news:
            lines.append(f"- 採用ニュースの最新取得：{format_local_time(latest_news, timezone_name)}")
        else:
            lines.append("- 採用ニュースの最新取得：利用可能な時刻情報なし")
        lines.extend(render_us_market_freshness_ja(market_story, local_tz, timezone_name))
        return "\n".join(lines)

    lines = [
        "## Data freshness",
        "",
        f"- Brief generated: {format_local_time(generated, timezone_name)}",
    ]
    if latest_news:
        lines.append(f"- Latest news retrieval: {format_local_time(latest_news, timezone_name)}")
    else:
        lines.append("- Latest news retrieval: no timestamp available")
    lines.extend(render_us_market_freshness_en(market_story, local_tz, timezone_name))
    return "\n".join(lines)


def render_us_market_freshness_zh(
    story: dict[str, Any] | None,
    local_tz,
    timezone_name: str,
) -> list[str]:
    if not story:
        return ["- 美股板块行情：无可用数据"]
    metrics = story.get("metrics") or {}
    state = metrics.get("market_state", "unknown")
    quote_time = localize_iso(metrics.get("quote_time"), local_tz)
    quote_label = format_local_time(quote_time, timezone_name) if quote_time else "时间未知"
    next_open = localize_iso(metrics.get("next_regular_open"), local_tz)

    if state == "pre_market":
        text = f"- 美股状态：盘前；板块数据为上一常规交易时段收盘价，截至 {quote_label}"
        if next_open:
            text += f"；常规交易预计于 {format_local_time(next_open, timezone_name)} 开始"
        return [text, "- 提示：当前简报不包含盘前价格，开盘后走势可能明显变化"]
    if state == "regular":
        return [f"- 美股状态：交易中；板块行情截至 {quote_label}"]
    if state == "after_hours":
        return [
            f"- 美股状态：盘后；板块数据为常规交易时段收盘价，截至 {quote_label}",
            "- 提示：当前简报不包含盘后价格",
        ]
    if state == "closed":
        text = f"- 美股状态：休市；板块数据为上一常规交易时段收盘价，截至 {quote_label}"
        if next_open:
            text += f"；下一常规交易预计于 {format_local_time(next_open, timezone_name)} 开始"
        return [text]
    return [f"- 美股板块行情：状态未知；报价截至 {quote_label}"]


def render_us_market_freshness_en(
    story: dict[str, Any] | None,
    local_tz,
    timezone_name: str,
) -> list[str]:
    if not story:
        return ["- U.S. sector data: unavailable"]
    metrics = story.get("metrics") or {}
    state = metrics.get("market_state", "unknown")
    quote_time = localize_iso(metrics.get("quote_time"), local_tz)
    quote_label = format_local_time(quote_time, timezone_name) if quote_time else "unknown time"
    next_open = localize_iso(metrics.get("next_regular_open"), local_tz)

    if state == "pre_market":
        text = f"- U.S. market: pre-market; sector values are prior regular-session closes as of {quote_label}"
        if next_open:
            text += f"; regular trading is expected to begin at {format_local_time(next_open, timezone_name)}"
        return [text, "- Note: this briefing does not include pre-market prices"]
    if state == "regular":
        return [f"- U.S. market: regular session; sector values as of {quote_label}"]
    if state == "after_hours":
        return [
            f"- U.S. market: after hours; sector values are regular-session closes as of {quote_label}",
            "- Note: this briefing does not include after-hours prices",
        ]
    if state == "closed":
        return [f"- U.S. market: closed; sector values are prior regular-session closes as of {quote_label}"]
    return [f"- U.S. sector data: market state unknown; quote as of {quote_label}"]


def render_us_market_freshness_ja(
    story: dict[str, Any] | None,
    local_tz,
    timezone_name: str,
) -> list[str]:
    if not story:
        return ["- 米国株セクターデータ：利用不可"]
    metrics = story.get("metrics") or {}
    state = metrics.get("market_state", "unknown")
    quote_time = localize_iso(metrics.get("quote_time"), local_tz)
    quote_label = format_local_time(quote_time, timezone_name) if quote_time else "時刻不明"
    next_open = localize_iso(metrics.get("next_regular_open"), local_tz)

    if state == "pre_market":
        text = f"- 米国市場：プレマーケット；セクター値は前通常取引の終値（{quote_label} 時点）"
        if next_open:
            text += f"；通常取引開始予定は {format_local_time(next_open, timezone_name)}"
        return [text, "- 注：このブリーフにはプレマーケット価格を含みません"]
    if state == "regular":
        return [f"- 米国市場：通常取引中；セクター値は {quote_label} 時点"]
    if state == "after_hours":
        return [
            f"- 米国市場：時間外；セクター値は通常取引の終値（{quote_label} 時点）",
            "- 注：このブリーフには時間外価格を含みません",
        ]
    if state == "closed":
        return [f"- 米国市場：休場；セクター値は前通常取引の終値（{quote_label} 時点）"]
    return [f"- 米国株セクターデータ：市場状態不明；価格は {quote_label} 時点"]


def add_freshness_section(body: str, freshness: str) -> str:
    lines = body.strip().splitlines()
    if lines and lines[0].startswith("# "):
        return "\n".join([lines[0], "", freshness, "", *lines[1:]]).strip()
    return f"{freshness}\n\n{body.strip()}".strip()


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def localize_iso(value: str | None, local_tz) -> datetime | None:
    parsed = parse_iso_datetime(value)
    return parsed.astimezone(local_tz) if parsed else None


def format_local_time(value: datetime, timezone_name: str) -> str:
    return f"{value:%Y-%m-%d %H:%M} {timezone_name}"


def fallback_briefing(stories: list[dict[str, Any]], language: str) -> str:
    if language != "original":
        language = normalize_output_language(language)
    title = "# Daily Brief" if language != "zh" else "# 每日情报简报"
    if not stories:
        message = (
            "No stories are available. Run `python -m newsagent collect` first."
            if language == "original"
            else "当前没有可用 story。请先运行 `python -m newsagent collect` 更新本地数据。"
        )
        return f"{title}\n\n{message}"

    market = sorted(
        unique_by_source([s for s in stories if s["category"] == "market"]),
        key=lambda story: abs(extract_change_pct(story["title"])),
        reverse=True,
    )
    market_groups = group_market_stories(market)
    world_news = [s for s in stories if s["category"] == "world_news" and not is_obviously_stale(s)]
    medical = select_medical_stories(stories)
    ai_tech = [s for s in stories if s["category"] in {"ai", "ai_engineering", "ai_hardware"}]

    lines = [title]

    original = language == "original"
    lines.extend(["", "## Market overview: global indices and sectors" if original else "## 市场先看：全球指数与板块"])
    render_market_group(lines, "Global indices" if original else "全球主要指数", market_groups["global_indices"], original)
    render_market_group(lines, "U.S. sectors" if original else "美股行业板块", market_groups["us_sectors"], original)
    render_market_group(lines, "Commodities and FX" if original else "商品与外汇", market_groups["commodities_fx"], original)

    lines.extend(["", "## Important mainstream news by region — Top 5" if original else "## 各地区主流媒体重要新闻 Top 5"])
    for region in WORLD_REGION_ORDER:
        bucket = [story for story in world_news if story.get("region") == region][:5]
        if not bucket:
            continue
        region_label = WORLD_REGION_LABELS_EN[region] if original else WORLD_REGION_LABELS[region]
        lines.extend(["", f"### {region_label}"])
        for story in bucket:
            summary = clean_summary(story.get("summary") or ("High-ranking item from a mainstream RSS feed." if original else "来自主流媒体 RSS 的高排序新闻。"))
            source_label = "Source:" if original else "来源："
            lines.append(f"- [{story['id']}] {story['title']} — {summary} {source_label} {first_source(story)}")

    lines.extend(["", "## Medical and health — Top 5" if original else "## 医疗与健康资讯 Top 5"])
    if medical:
        for story in medical[:5]:
            summary = clean_summary(story.get("summary") or ("High-ranking item from a medical, public-health, or regulatory source." if original else "来自医疗、公共卫生或监管来源的高排序条目。"))
            source_label = "Source:" if original else "来源："
            lines.append(f"- [{story['id']}] {story['title']} — {summary} {source_label} {first_source(story)}")
    else:
        lines.append(
            "- No new medical items were selected; continue monitoring WHO, FDA, The Lancet, and NEJM."
            if original
            else "- 本轮医疗源暂无新增入库条目，继续跟踪 WHO、FDA、The Lancet、NEJM 等来源的公共卫生、药物审批、临床研究和监管更新。"
        )

    if ai_tech:
        lines.extend(["", "## AI and technology" if original else "## AI 与科技"])
        for story in ai_tech[:8]:
            summary = clean_summary(story.get("summary") or ("High-ranking item from an AI or technology source." if original else "来自 AI/科技来源的高排序条目。"))
            source_label = "Source:" if original else "来源："
            lines.append(f"- [{story['id']}] {story['title']} — {summary} {source_label} {first_source(story)}")

    lines.extend(["", "## Selection logic" if original else "## 判断逻辑"])
    if original:
        lines.extend([
            "- Mainstream stories are selected separately by region; earlier RSS position is treated as stronger editorial priority.",
            "- Ranking also considers source tier/priority, freshness, important-topic keywords, and user feedback.",
            "- RSS feeds rarely expose real readership, so editorial position and source weight act as popularity proxies.",
            "- The medical section is retained even when the current evidence is sparse.",
        ])
    else:
        lines.extend([
            "- 主流媒体 Top 5 按地区分别筛选；RSS 越靠前，代表该媒体编辑优先级越高。",
            "- 排序同时参考来源 tier/priority、新闻时效性、重大主题关键词和你的 feedback。",
            "- RSS 通常不提供真实阅读量，所以“热度”使用编辑顺位、来源权重和多来源重要性做代理。",
            "- 医疗板块固定保留；有真实医疗新闻时列新闻，暂无入库时列继续跟踪方向。",
        ])

    return "\n".join(lines)


def fallback_answer(question: str, stories: list[dict[str, Any]], language: str) -> str:
    if not stories:
        return "当前没有找到相关本地记录。可以先运行 `python -m newsagent collect` 更新数据，再重新提问。"
    lines = [f"问题：{question}", "", "基于当前本地数据库，可参考："]
    for story in stories[:8]:
        source = first_source(story)
        summary = f" - {story['summary']}" if story.get("summary") else ""
        lines.append(f"- [{story['id']}] {story['title']}{summary} ({source})")
    lines.append("")
    lines.append("说明：这是无 LLM 或 LLM 失败时的降级回答，只做证据列举，不做额外推断。")
    return "\n".join(lines)


def render_market_group(
    lines: list[str],
    heading: str,
    stories: list[dict[str, Any]],
    original: bool = False,
) -> None:
    if not stories:
        return
    lines.extend(["", f"### {heading}"])
    for story in stories:
        source_label = "Source:" if original else "来源："
        lines.append(f"- [{story['id']}] {story['title']} {source_label} {first_source(story)}")


def group_market_stories(stories: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups = {"global_indices": [], "us_sectors": [], "commodities_fx": []}
    for story in stories:
        symbol = extract_symbol(first_source(story))
        if symbol in GLOBAL_INDEX_SYMBOLS:
            groups["global_indices"].append(story)
        elif symbol in US_SECTOR_SYMBOLS:
            groups["us_sectors"].append(story)
        else:
            groups["commodities_fx"].append(story)
    return groups


def select_medical_stories(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for story in stories:
        tags = set(str(tag).lower() for tag in story.get("tags", []))
        if story.get("category") == "medicine" or tags.intersection({"medicine", "medical", "health", "journal", "clinical"}):
            result.append(story)
    return unique_by_source(result)


def clean_summary(text: str, max_chars: int = 360) -> str:
    cleaned = html.unescape(re.sub(r"\s+", " ", text or "")).strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[: max_chars - 1].rstrip() + "…"


def first_source(story: dict[str, Any]) -> str:
    urls = story.get("source_urls") or []
    return urls[0] if urls else "no source URL"


def unique_by_source(stories: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    seen = set()
    for story in stories:
        source = first_source(story)
        key = source or story.get("title")
        if key in seen:
            continue
        seen.add(key)
        result.append(story)
    return result


def extract_symbol(url: str) -> str:
    marker = "/quote/"
    if marker not in url:
        return ""
    return url.split(marker, 1)[1].split("?", 1)[0].strip("/")


def extract_change_pct(title: str) -> float:
    match = re.search(r"\((-?\d+(?:\.\d+)?)%\)", title)
    if not match:
        return 0.0
    try:
        return float(match.group(1))
    except ValueError:
        return 0.0


def is_obviously_stale(story: dict[str, Any]) -> bool:
    source = first_source(story)
    if "english.people.com.cn" in source or "chinadaily.com.cn/a/2017" in source:
        return True
    match = re.search(r"/(20\d{2})(?:/|\d{2})", source)
    if not match:
        return False
    year = int(match.group(1))
    return year < datetime.now().year - 1


def clean_model_output(text: str) -> str:
    text = text.strip()
    if "</think>" in text:
        text = text.split("</think>", 1)[1].strip()
    if text.lower().startswith("thinking..."):
        marker = "...done thinking."
        lower = text.lower()
        idx = lower.find(marker)
        if idx >= 0:
            text = text[idx + len(marker):].strip()
    return remove_empty_evidence_sections(text)


def remove_empty_evidence_sections(text: str) -> str:
    banned_phrases = [
        "无相关证据",
        "暂无相关证据",
        "无相关内容",
        "暂无相关内容",
        "no relevant evidence",
        "no relevant items",
        "no relevant content",
    ]
    lines = []
    for line in text.splitlines():
        lower = line.lower()
        if any(phrase in lower for phrase in banned_phrases):
            continue
        lines.append(line.rstrip())

    compact: list[str] = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if line.startswith("## "):
            next_idx = idx + 1
            while next_idx < len(lines) and not lines[next_idx].strip():
                next_idx += 1
            if next_idx >= len(lines) or lines[next_idx].startswith("## "):
                idx += 1
                continue
        compact.append(line)
        idx += 1

    return "\n".join(compact).strip()
