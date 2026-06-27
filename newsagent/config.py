from __future__ import annotations

from pathlib import Path
from typing import Any
import shutil
import json

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
    db_path = Path(settings["database"]["path"])
    if not db_path.is_absolute():
        settings["database"]["path"] = str(ROOT / db_path)
    return settings


def load_sources(path: Path = DEFAULT_SOURCES) -> list[Source]:
    return [Source.from_dict(item) for item in load_json(path)]
