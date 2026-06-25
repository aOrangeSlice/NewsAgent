from __future__ import annotations

from newsagent.models import Source

from .base import Collector, CollectorError
from .cctv import CCTVXinwenLianboCollector
from .github import GitHubSearchCollector
from .huggingface import HuggingFaceModelsCollector
from .market import YahooQuotesCollector
from .rss import RSSCollector


def build_collector(source: Source) -> Collector:
    if source.kind == "rss":
        return RSSCollector(source)
    if source.kind == "cctv_xinwen_lianbo":
        return CCTVXinwenLianboCollector(source)
    if source.kind == "github_search":
        return GitHubSearchCollector(source)
    if source.kind == "huggingface_models":
        return HuggingFaceModelsCollector(source)
    if source.kind == "yahoo_quotes":
        return YahooQuotesCollector(source)
    raise CollectorError(f"Unsupported source kind: {source.kind}")
