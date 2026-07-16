from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = DATA_DIR / "settings.json"
JOBS_PATH = DATA_DIR / "jobs.json"

# --- Model capability definitions ---

GPT_IMAGE_RATIOS = [
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "5:4",
    "4:5",
    "21:9",
    "9:21",
    "1:2",
    "2:1",
]

NANO_BANANA_RATIOS = [
    "auto",
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "5:4",
    "4:5",
    "21:9",
]

NANO_BANANA_2_EXTRA_RATIOS = ["1:4", "4:1", "1:8", "8:1"]

IMAGE_SIZES_FULL = ["1K", "2K", "4K"]
IMAGE_SIZES_1K_ONLY = ["1K"]

MODELS: list[dict[str, Any]] = [
    {
        "id": "gpt-image-2",
        "name": "gpt-image-2",
        "endpoint": "/v1/draw/completions",
        "image_param": "images",
        "aspect_ratios": GPT_IMAGE_RATIOS,
        "image_sizes": [],
        "supports_quality": True,
    },
    {
        "id": "nano-banana-2",
        "name": "nano-banana-2",
        "endpoint": "/v1/draw/nano-banana",
        "image_param": "urls",
        "aspect_ratios": NANO_BANANA_RATIOS + NANO_BANANA_2_EXTRA_RATIOS,
        "image_sizes": IMAGE_SIZES_FULL,
        "supports_quality": False,
    },
    {
        "id": "nano-banana-pro",
        "name": "nano-banana-pro",
        "endpoint": "/v1/draw/nano-banana",
        "image_param": "urls",
        "image_sizes": IMAGE_SIZES_FULL,
        "aspect_ratios": NANO_BANANA_RATIOS,
        "supports_quality": False,
    },
    {
        "id": "nano-banana-fast",
        "name": "nano-banana-fast",
        "endpoint": "/v1/draw/nano-banana",
        "image_param": "urls",
        "aspect_ratios": NANO_BANANA_RATIOS,
        "image_sizes": IMAGE_SIZES_1K_ONLY,
        "supports_quality": False,
    },
    {
        "id": "nano-banana",
        "name": "nano-banana",
        "endpoint": "/v1/draw/nano-banana",
        "image_param": "urls",
        "aspect_ratios": NANO_BANANA_RATIOS,
        "image_sizes": [],
        "supports_quality": False,
    },
]

_MODELS_BY_ID: dict[str, dict[str, Any]] = {m["id"]: m for m in MODELS}


def get_model_info(model_id: str) -> dict[str, Any]:
    """Return model capability dict, falling back to gpt-image-2."""
    return _MODELS_BY_ID.get(model_id, _MODELS_BY_ID["gpt-image-2"])


def get_model_ratios(model_id: str) -> list[str]:
    return get_model_info(model_id)["aspect_ratios"]


def get_model_sizes(model_id: str) -> list[str]:
    return get_model_info(model_id)["image_sizes"]


def get_model_endpoint(model_id: str) -> str:
    return get_model_info(model_id)["endpoint"]


def get_model_image_param(model_id: str) -> str:
    """Return the JSON field name for images: 'images' (base64) or 'urls'."""
    return get_model_info(model_id)["image_param"]


def model_supports_quality(model_id: str) -> bool:
    return get_model_info(model_id).get("supports_quality", False)


DEFAULT_SETTINGS: dict[str, Any] = {
    "api_key": "",
    "base_url": "https://noova.cn",
    "model": "gpt-image-2",
    "aspect_ratio": "1:1",
    "quality": "auto",
    "image_size": "",
    "concurrency": 2,
    "poll_interval_sec": 3,
    "poll_timeout_sec": 300,
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
