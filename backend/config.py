from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = DATA_DIR / "settings.json"
JOBS_PATH = DATA_DIR / "jobs.json"

DEFAULT_SETTINGS: dict[str, Any] = {
    "api_key": "",
    "base_url": "https://noova.cn",
    "model": "gpt-image-2",
    "aspect_ratio": "1:1",
    "quality": "auto",
    "concurrency": 2,
    "poll_interval_sec": 3,
    "poll_timeout_sec": 600,
    "source_dir": "",
    "output_dir": str(BASE_DIR / "outputs"),
}


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (BASE_DIR / "outputs").mkdir(parents=True, exist_ok=True)


def load_settings() -> dict[str, Any]:
    ensure_dirs()
    if not CONFIG_PATH.exists():
        save_settings(DEFAULT_SETTINGS)
        return dict(DEFAULT_SETTINGS)

    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        data = {}

    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in data.items() if k in DEFAULT_SETTINGS})
    return merged


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    ensure_dirs()
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in settings.items() if k in DEFAULT_SETTINGS})
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)
    return merged


def public_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return settings safe for frontend display (mask API key)."""
    data = dict(settings or load_settings())
    key = str(data.get("api_key") or "")
    if key:
        if len(key) <= 8:
            data["api_key_masked"] = "*" * len(key)
        else:
            data["api_key_masked"] = f"{key[:4]}{'*' * max(4, len(key) - 8)}{key[-4:]}"
        data["api_key_set"] = True
    else:
        data["api_key_masked"] = ""
        data["api_key_set"] = False
    # Do not send raw key to browser by default.
    data["api_key"] = ""
    return data
