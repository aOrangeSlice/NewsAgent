from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .models import Source


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SETTINGS = ROOT / "config" / "settings.json"
DEFAULT_SOURCES = ROOT / "config" / "sources.json"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def load_settings(path: Path = DEFAULT_SETTINGS) -> dict[str, Any]:
    settings = load_json(path)
    db_path = Path(settings["database"]["path"])
    if not db_path.is_absolute():
        settings["database"]["path"] = str(ROOT / db_path)
    return settings


def load_sources(path: Path = DEFAULT_SOURCES) -> list[Source]:
    return [Source.from_dict(item) for item in load_json(path)]

