from __future__ import annotations

from typing import Any
from urllib.parse import urlparse
import re


MEDICAL_BASE_TERMS = [
    "medicine",
    "medical",
    "health",
    "journal",
    "fda",
    "who",
    "lancet",
    "nejm",
    "jama",
    "nature",
    "digital_medicine",
    "digital_health",
    "regulation",
    "clinical",
]

MEDICAL_AGENT_TERMS = [
    "ai agent",
    "ai agents",
    "agentic ai",
    "agentic artificial intelligence",
    "multi-agent",
    "multiagent",
    "autonomous agent",
    "autonomous agents",
]

MEDICAL_FOCUS_TERMS = [
    "ai",
    "artificial intelligence",
    *MEDICAL_AGENT_TERMS,
    "machine learning",
    "deep learning",
    "llm",
    "large language model",
    "large language models",
    "mllm",
    "multimodal large language model",
    "foundation model",
    "generative ai",
    "generative artificial intelligence",
    "rag",
    "retrieval-augmented generation",
    "cognition",
    "cognitive",
    "neuroscience",
    "neurology",
    "neural",
    "nervous system",
    "motor system",
    "motor function",
    "认知",
    "运动系统",
    "神经系统",
    "人工智能",
]

MEDICAL_RECALL_QUERY = " ".join(MEDICAL_BASE_TERMS + MEDICAL_FOCUS_TERMS)

MEDICAL_STORY_TAGS = {
    "medicine",
    "medical",
    "health",
    "journal",
    "clinical",
    "digital_medicine",
    "digital_health",
    "public_health",
    "regulation",
}

TOP_MEDICAL_SOURCE_MARKERS = {
    "nature.com",
    "thelancet.com",
    "nejm.org",
    "jamanetwork.com",
}

AUTHORITATIVE_MEDICAL_SOURCE_MARKERS = TOP_MEDICAL_SOURCE_MARKERS | {
    "who.int",
    "fda.gov",
}

TOP_MEDICAL_TAGS = {
    "nature",
    "nature_medicine",
    "lancet",
    "nejm",
    "jama",
}


def is_medical_story(story: dict[str, Any]) -> bool:
    tags = story_tags(story)
    return story.get("category") == "medicine" or bool(tags.intersection(MEDICAL_STORY_TAGS))


def is_high_signal_medical_story(story: dict[str, Any]) -> bool:
    return medical_signal_score(story) >= 4


def medical_signal_score(story: dict[str, Any]) -> int:
    score = 0
    tags = story_tags(story)
    source_text = " ".join(str(url).lower() for url in story.get("source_urls", []))

    if unique_source_count(story) >= 2:
        score += 8
    if any(marker in source_text for marker in TOP_MEDICAL_SOURCE_MARKERS) or tags.intersection(TOP_MEDICAL_TAGS):
        score += 8
    elif any(marker in source_text for marker in AUTHORITATIVE_MEDICAL_SOURCE_MARKERS):
        score += 6
    if tags.intersection({"journal", "clinical", "public_health", "regulation"}):
        score += 2
    if story.get("category") == "medicine":
        score += 1

    story_text = medical_story_text(story)
    focus_hits = sum(1 for term in MEDICAL_FOCUS_TERMS if term_matches(term, story_text))
    score += min(focus_hits * 3, 6)
    if any(term_matches(term, story_text) for term in MEDICAL_AGENT_TERMS):
        score += 3
    return score


def unique_source_count(story: dict[str, Any]) -> int:
    hosts = set()
    for url in story.get("source_urls", []) or []:
        parsed = urlparse(str(url))
        host = parsed.netloc.lower()
        hosts.add(host or str(url).lower())
    return len(hosts)


def story_tags(story: dict[str, Any]) -> set[str]:
    return {str(tag).lower() for tag in story.get("tags", [])}


def medical_story_text(story: dict[str, Any]) -> str:
    parts = [
        story.get("title", ""),
        story.get("summary", ""),
        " ".join(str(tag) for tag in story.get("tags", [])),
    ]
    return " ".join(parts).lower()


def term_matches(term: str, text: str) -> bool:
    normalized = term.lower()
    if any(ord(char) > 127 for char in normalized):
        return normalized in text
    if len(normalized) <= 3 and normalized.isalnum():
        return bool(re.search(rf"\b{re.escape(normalized)}\b", text))
    return normalized in text
