"""Load debug parameters shared by NLP extraction and post-processing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DATA_PROCESSING_DIR = Path(__file__).resolve().parents[1]
DEFAULT_DEBUG_PARAMS_PATH = Path(__file__).resolve().with_name("debug_params.json")


def load_debug_params(path: Path = DEFAULT_DEBUG_PARAMS_PATH) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain one JSON object.")
    return data


def config_path(value: Any, default: Path | str | None = None) -> Path | None:
    if value in (None, ""):
        return Path(default) if default is not None else None
    path = Path(str(value))
    if path.is_absolute():
        return path
    return DATA_PROCESSING_DIR / path


def config_int(value: Any, default: int) -> int:
    if value in (None, ""):
        return default
    return int(value)


def config_float(value: Any, default: float) -> float:
    if value in (None, ""):
        return default
    return float(value)


def config_keyword(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
