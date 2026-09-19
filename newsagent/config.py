from __future__ import annotations

from pathlib import Path
from typing import Any
import shutil
import json
import os

from .models import Source


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS = ROOT / "config" / "settings.json"
DEFAULT_SETTINGS_EXAMPLE = ROOT / "config" / "settings.example.json"
DEFAULT_SOURCES = ROOT / "config" / "sources.json"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def ensure_settings_file(
    path: Path = DEFAULT_SETTINGS,
    example_path: Path = DEFAULT_SETTINGS_EXAMPLE,
) -> bool:
    if path.exists():
        return False
    if not example_path.exists():
        raise FileNotFoundError(
            f"Missing settings file: {path}. Also missing example file: {example_path}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(example_path, path)
    return True


def load_settings(path: Path = DEFAULT_SETTINGS) -> dict[str, Any]:
    if path == DEFAULT_SETTINGS:
        ensure_settings_file(path)
    settings = load_json(path)
    apply_environment_overrides(settings)
    db_path = Path(settings["database"]["path"])
    if not db_path.is_absolute():
        settings["database"]["path"] = str(ROOT / db_path)
    return settings


def apply_environment_overrides(settings: dict[str, Any]) -> dict[str, Any]:
    """Apply the small, explicit environment surface used by cloud jobs.

    The JSON file remains the source of defaults for local runs. Environment
    variables override only deployment-sensitive values so Cloud Run does not
    need a generated or secret-bearing settings file baked into its image.
    """
    string_overrides = {
        "NEWSAGENT_DATABASE_PATH": ("database", "path"),
        "NEWSAGENT_LLM_PROVIDER": ("llm", "provider"),
        "NEWSAGENT_LLM_MODEL": ("llm", "model"),
        "NEWSAGENT_SMTP_HOST": ("delivery", "email", "host"),
        "NEWSAGENT_SMTP_USERNAME": ("delivery", "email", "username"),
        "NEWSAGENT_EMAIL_SENDER": ("delivery", "email", "sender"),
    }
    integer_overrides = {
        "NEWSAGENT_LLM_MAX_CALLS_PER_RUN": ("llm", "max_calls_per_run"),
        "NEWSAGENT_LLM_MAX_OUTPUT_TOKENS": ("llm", "max_output_tokens"),
        "NEWSAGENT_SMTP_PORT": ("delivery", "email", "port"),
    }
    boolean_overrides = {
        "NEWSAGENT_EMAIL_ENABLED": ("delivery", "email", "enabled"),
        "NEWSAGENT_SMTP_USE_TLS": ("delivery", "email", "use_tls"),
    }

    for name, path in string_overrides.items():
        if name in os.environ:
            _set_nested(settings, path, os.environ[name])
    for name, path in integer_overrides.items():
        if name in os.environ:
            try:
                value = int(os.environ[name])
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer") from exc
            _set_nested(settings, path, value)
    for name, path in boolean_overrides.items():
        if name in os.environ:
            _set_nested(settings, path, _parse_bool(name, os.environ[name]))

    recipients = os.environ.get("NEWSAGENT_EMAIL_RECIPIENTS")
    if recipients is not None:
        values = [value.strip() for value in recipients.split(",") if value.strip()]
        _set_nested(settings, ("delivery", "email", "recipients"), values)
    return settings


def _set_nested(settings: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    target = settings
    for key in path[:-1]:
        target = target.setdefault(key, {})
    target[path[-1]] = value


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def load_sources(path: Path = DEFAULT_SOURCES) -> list[Source]:
    return [Source.from_dict(item) for item in load_json(path)]
