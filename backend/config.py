from __future__ import annotations

import json
import ipaddress
import os
import socket
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = DATA_DIR / "settings.json"
JOBS_PATH = DATA_DIR / "jobs.json"
SCHEDULES_PATH = DATA_DIR / "schedules.json"
DEFAULT_BASE_URL = "https://noova.cn"
DEFAULT_IMAGE_PROXY_URL = "https://imageproxy.zhongzhuan.chat/api/upload"


# --- 敏感目录保护 ---------------------------------------------------------
#
# 鉴权中间件已经保证只有本工具的页面能调用接口，用户挑哪个文件夹是他自己的
# 自由。这里只再加一层"手滑/被诱导"防护：拒绝把系统目录、开机自启目录当成
# 源目录或输出目录，避免程序往这些地方建目录、写文件。


def _system_dirs() -> list[Path]:
    dirs: list[str] = []
    if sys.platform.startswith("win"):
        system_root = os.environ.get("SystemRoot") or r"C:\Windows"
        dirs += [
            system_root,
            os.environ.get("ProgramFiles") or r"C:\Program Files",
            os.environ.get("ProgramFiles(x86)") or r"C:\Program Files (x86)",
        ]
        # 开机自启目录：往这里写东西风险最高
        appdata = os.environ.get("APPDATA")
        if appdata:
            dirs.append(os.path.join(appdata, r"Microsoft\Windows\Start Menu"))
        programdata = os.environ.get("ProgramData")
        if programdata:
            dirs.append(os.path.join(programdata, r"Microsoft\Windows\Start Menu"))
    else:
        dirs += ["/etc", "/bin", "/sbin", "/usr", "/boot", "/sys", "/proc", "/dev", "/lib"]

    resolved: list[Path] = []
    for item in dirs:
        if not item:
            continue
        try:
            resolved.append(Path(item).resolve())
        except OSError:
            continue
    return resolved


def assert_safe_directory(path: Path, *, field_name: str = "目录") -> Path:
    """校验一个用户提供的目录路径，返回解析后的绝对路径。

    拒绝系统目录和开机自启目录，以及本程序自己的 data 目录。
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"{field_name}路径无效: {path}") from exc

    try:
        data_dir = Path(DATA_DIR).resolve()
    except OSError:
        data_dir = None

    # 盘符根 / 文件系统根：一次误选就会把子目录撒满整个盘，直接拒绝
    if resolved.parent == resolved:
        raise ValueError(f"{field_name}不能是磁盘根目录: {resolved}")

    candidates = _system_dirs()
    if data_dir is not None:
        candidates.append(data_dir)

    for blocked in candidates:
        if resolved == blocked or _is_within(resolved, blocked):
            raise ValueError(f"{field_name}不能指向系统目录或程序数据目录: {resolved}")
    return resolved


def assert_safe_file(path: Path, *, field_name: str = "文件") -> Path:
    """校验用户提供的单个文件路径（固定参考图）。

    和目录一样要挡住系统目录，否则 extra_image 会变成绕过目录校验的后门：
    既是任意路径存在性探测器，也可能把系统文件当图片上传出去。
    """
    try:
        resolved = Path(path).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"{field_name}路径无效: {path}") from exc
    assert_safe_directory(resolved.parent, field_name=f"{field_name}所在目录")
    if not resolved.is_file():
        raise ValueError(f"{field_name}不存在或不是文件")
    if resolved.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        raise ValueError(f"{field_name}必须是 jpg/jpeg/png/webp/bmp 图片")
    return resolved


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False

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

GPT_IMAGE_2_G_RATIOS = ["auto", *GPT_IMAGE_RATIOS]

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
        "image_param": "urls",
        "aspect_ratios": GPT_IMAGE_RATIOS,
        "image_sizes": [],
        "supports_quality": True,
    },
    {
        "id": "gpt-image-2-G",
        "name": "gpt-image-2-G",
        "endpoint": "/api/v1/gateway/invoke",
        "image_param": "urls",
        "poll_param": "task_id",
        "poll_timeout_sec": 960,
        "aspect_ratios": GPT_IMAGE_2_G_RATIOS,
        "image_sizes": IMAGE_SIZES_1K_ONLY,
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
]

_MODELS_BY_ID: dict[str, dict[str, Any]] = {m["id"]: m for m in MODELS}


def validate_external_http_url(value: str, *, field_name: str = "URL", resolve_host: bool = True) -> str:
    """Return a normalized public http(s) URL, rejecting local/private targets."""
    cleaned = str(value or "").strip().rstrip("/")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not parsed.hostname:
        raise ValueError(f"{field_name} 必须是 http/https 地址")
    if parsed.username or parsed.password:
        raise ValueError(f"{field_name} 不能包含用户名或密码")

    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise ValueError(f"{field_name} 不能指向本机地址")

    def reject_non_public(address_text: str) -> None:
        try:
            address = ipaddress.ip_address(address_text)
        except ValueError:
            return
        if not address.is_global:
            raise ValueError(f"{field_name} 不能指向内网、本机或保留地址")

    reject_non_public(host)

    if resolve_host:
        try:
            infos = socket.getaddrinfo(host, parsed.port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise ValueError(f"{field_name} 域名解析失败") from exc
        for info in infos:
            reject_non_public(info[4][0])

    return cleaned


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
    """Return the JSON field name for image references (all current models: 'urls')."""
    return get_model_info(model_id)["image_param"]


def get_model_poll_param(model_id: str) -> str:
    """Return the task-id field expected by the model's result endpoint."""
    return str(get_model_info(model_id).get("poll_param") or "id")


def get_model_poll_timeout_sec(model_id: str) -> int | None:
    value = get_model_info(model_id).get("poll_timeout_sec")
    return int(value) if value else None


def model_supports_quality(model_id: str) -> bool:
    return get_model_info(model_id).get("supports_quality", False)


DEFAULT_SETTINGS: dict[str, Any] = {
    "api_key": "",
    "base_url": DEFAULT_BASE_URL,
    "model": "gpt-image-2",
    "aspect_ratio": "9:16",
    "quality": "auto",
    "image_size": "",
    "concurrency": 10,
    "poll_interval_sec": 20,
    "poll_timeout_sec": 300,
    "source_dir": "",
    "output_dir": str(BASE_DIR / "outputs"),
    "image_proxy_url": DEFAULT_IMAGE_PROXY_URL,
    "auto_retry_failed": False,
    "max_retry_count": 0,
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
    if not str(merged.get("base_url") or "").strip():
        merged["base_url"] = DEFAULT_BASE_URL
    if not str(merged.get("image_proxy_url") or "").strip():
        merged["image_proxy_url"] = DEFAULT_IMAGE_PROXY_URL
    merged["auto_retry_failed"] = bool(merged.get("auto_retry_failed") or False)
    try:
        merged["max_retry_count"] = max(0, min(int(merged.get("max_retry_count") or 0), 10))
    except (TypeError, ValueError):
        merged["max_retry_count"] = 0
    if merged.get("model") not in _MODELS_BY_ID:
        merged["model"] = DEFAULT_SETTINGS["model"]
    return merged


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    ensure_dirs()
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in settings.items() if k in DEFAULT_SETTINGS})
    if not str(merged.get("base_url") or "").strip():
        merged["base_url"] = DEFAULT_BASE_URL
    if not str(merged.get("image_proxy_url") or "").strip():
        merged["image_proxy_url"] = DEFAULT_IMAGE_PROXY_URL
    merged["auto_retry_failed"] = bool(merged.get("auto_retry_failed") or False)
    try:
        merged["max_retry_count"] = max(0, min(int(merged.get("max_retry_count") or 0), 10))
    except (TypeError, ValueError):
        merged["max_retry_count"] = 0
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
