from __future__ import annotations

from datetime import datetime, timezone
import math
import time

from newsagent.http import fetch_json

from .base import Collector


class YahooQuotesCollector(Collector):
    API = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

    def collect(self, limit: int = 20):
        results = []
        for spec in self.source.extra.get("symbols", [])[:limit]:
            symbol = spec["symbol"]
            label = spec.get("label", symbol)
            data = fetch_json(
                self.API.format(symbol=symbol),
                params={"range": "5d", "interval": "1d"},
                verify_ssl=bool(self.source.extra.get("verify_ssl", True)),
            )
            chart = data.get("chart", {})
            result = (chart.get("result") or [{}])[0]
            meta = result.get("meta", {})
            quote = (result.get("indicators", {}).get("quote") or [{}])[0]
            last, previous = latest_and_previous_price(
                meta,
                quote.get("close", []),
            )
            quote_timestamp = meta.get("regularMarketTime")
            quote_time = timestamp_to_iso(quote_timestamp)
            market_state, next_regular_open = market_session_state(meta)
            price_type = "regular_live" if market_state == "regular" else "regular_close"
            change_pct = None
            if last is not None and previous:
                change_pct = (last - previous) / previous * 100
            metrics = {
                "symbol": symbol,
                "price": last,
                "previous": previous,
                "change_pct": change_pct,
                "currency": meta.get("currency"),
                "exchange": meta.get("exchangeName"),
                "quote_time": quote_time,
                "market_state": market_state,
                "price_type": price_type,
                "market_timezone": meta.get("exchangeTimezoneName") or meta.get("timezone"),
                "next_regular_open": next_regular_open,
            }
            pct = f"{change_pct:.2f}%" if change_pct is not None else "n/a"
            price = f"{last:.4f}" if isinstance(last, (float, int)) else str(last)
            title = f"{label}: {price} ({pct})"
            results.append(
                self.item(
                    title=title,
                    url=f"https://finance.yahoo.com/quote/{symbol}",
                    summary=f"Latest quote for {label}.",
                    published_at=quote_time,
                    metrics=metrics,
                    tags=self.source.tags + [symbol],
                )
            )
        return results


def timestamp_to_iso(value) -> str:
    if not isinstance(value, (int, float)):
        return ""
    return datetime.fromtimestamp(value, timezone.utc).replace(microsecond=0).isoformat()


def latest_and_previous_price(meta: dict, closes: list) -> tuple[float | int | None, float | int | None]:
    market_price = meta.get("regularMarketPrice")
    numeric_market_price = market_price if isinstance(market_price, (float, int)) else None
    numeric_closes = [value for value in closes if isinstance(value, (float, int))]

    if numeric_market_price is None:
        last = numeric_closes[-1] if numeric_closes else None
        previous = numeric_closes[-2] if len(numeric_closes) > 1 else meta.get("chartPreviousClose")
        return last, previous

    last = numeric_market_price
    if closes and closes[-1] is None:
        previous = numeric_closes[-1] if numeric_closes else meta.get("chartPreviousClose")
    elif numeric_closes and math.isclose(
        float(numeric_market_price),
        float(numeric_closes[-1]),
        rel_tol=1e-6,
        abs_tol=1e-3,
    ):
        previous = numeric_closes[-2] if len(numeric_closes) > 1 else meta.get("chartPreviousClose")
    else:
        previous = numeric_closes[-1] if numeric_closes else meta.get("chartPreviousClose")
    return last, previous


def market_session_state(meta: dict, now_timestamp: float | None = None) -> tuple[str, str]:
    now = now_timestamp if now_timestamp is not None else time.time()
    periods = meta.get("currentTradingPeriod") or {}
    pre = periods.get("pre") or {}
    regular = periods.get("regular") or {}
    post = periods.get("post") or {}

    pre_start = pre.get("start")
    regular_start = regular.get("start")
    regular_end = regular.get("end")
    post_end = post.get("end")

    if isinstance(pre_start, (int, float)) and isinstance(regular_start, (int, float)):
        if pre_start <= now < regular_start:
            return "pre_market", timestamp_to_iso(regular_start)
    if isinstance(regular_start, (int, float)) and isinstance(regular_end, (int, float)):
        if regular_start <= now < regular_end:
            return "regular", ""
    if isinstance(regular_end, (int, float)) and isinstance(post_end, (int, float)):
        if regular_end <= now < post_end:
            return "after_hours", ""
    if isinstance(regular_start, (int, float)) and now < regular_start:
        return "closed", timestamp_to_iso(regular_start)
    return "closed", ""
