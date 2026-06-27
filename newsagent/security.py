from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import json
import re


TEXT_SUFFIXES = {
    ".json",
    ".md",
    ".py",
    ".ps1",
    ".txt",
    ".yml",
    ".yaml",
    ".env",
}

SKIP_DIRS = {
    ".git",
    ".agents",
    ".codex",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "data",
}

SECRET_KEYWORDS = ("password", "secret", "token", "api_key", "apikey", "key")

HIGH_CONFIDENCE_PATTERNS = [
    ("openai_api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]

PLACEHOLDER_VALUES = {
    "",
    "changeme",
    "change-me",
    "example",
    "example.com",
    "password",
    "secret",
    "token",
    "your-password",
    "your-secret",
    "your-token",
    "your-api-key",
    "your-smtp-app-password",
    "newsagent_smtp_password",
}


@dataclass
class SecretFinding:
    file: str
    line: int
    kind: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": self.line,
            "kind": self.kind,
            "detail": self.detail,
        }


def scan_for_secrets(root: Path) -> list[dict[str, Any]]:
    findings: list[SecretFinding] = []
    for path in iter_scan_files(root):
        text = read_text_if_small(path)
        if text is None:
            continue
        findings.extend(scan_text(path, root, text))
        if path.suffix.lower() == ".json":
            findings.extend(scan_json_config(path, root, text))
    return [finding.to_dict() for finding in findings]


def iter_scan_files(root: Path):
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != ".env":
            continue
        yield path


def read_text_if_small(path: Path, max_bytes: int = 1_000_000) -> str | None:
    try:
        if path.stat().st_size > max_bytes:
            return None
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None
    except OSError:
        return None


def scan_text(path: Path, root: Path, text: str) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    for index, line in enumerate(text.splitlines(), start=1):
        for kind, pattern in HIGH_CONFIDENCE_PATTERNS:
            if pattern.search(line):
                findings.append(
                    SecretFinding(
                        file=str(path.relative_to(root)),
                        line=index,
                        kind=kind,
                        detail="High-confidence secret-looking token.",
                    )
                )
    return findings


def scan_json_config(path: Path, root: Path, text: str) -> list[SecretFinding]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    findings: list[SecretFinding] = []
    for key_path, value in walk_json(data):
        if not isinstance(value, str):
            continue
        key = key_path[-1].lower() if key_path else ""
        if not any(keyword in key for keyword in SECRET_KEYWORDS):
            continue
        if key.endswith("_env") or is_placeholder_secret(value):
            continue
        findings.append(
            SecretFinding(
                file=str(path.relative_to(root)),
                line=find_json_key_line(text, key_path[-1]),
                kind="inline_secret_config",
                detail=f"Config value for {'.'.join(key_path)} should come from an environment variable.",
            )
        )
    return findings


def walk_json(value: Any, prefix: tuple[str, ...] = ()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk_json(item, prefix + (str(key),))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk_json(item, prefix + (str(index),))
    else:
        yield prefix, value


def is_placeholder_secret(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in PLACEHOLDER_VALUES:
        return True
    if normalized.startswith("your-"):
        return True
    if normalized.startswith("$env:") or normalized.startswith("${"):
        return True
    if re.fullmatch(r"[A-Z][A-Z0-9_]{4,}", value.strip()):
        return True
    return False


def find_json_key_line(text: str, key: str) -> int:
    pattern = re.compile(rf'"{re.escape(key)}"\s*:')
    for index, line in enumerate(text.splitlines(), start=1):
        if pattern.search(line):
            return index
    return 1
