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
DEFAULT_BASE_URL = "https://noova.vip"
LEGACY_DEFAULT_BASE_URL = "https://noova.cn"
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

#: GPT Image 系列（sunburst / flare / GPT-Image-2-L）支持的比例，仅 1K
GPT_IMAGE_RATIOS = [
    "auto",
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "21:9",
    "9:21",
    "2:1",
    "1:2",
    "3:1",
    "1:3",
]

#: nano-banana-2 / Nano Banana 2.1 支持的比例
NANO_BANANA_RATIOS = [
    "auto",
    "1:1",
    "3:2",
    "2:3",
    "16:9",
    "9:16",
    "21:9",
    "5:4",
    "4:5",
    "1:4",
    "4:1",
    "1:8",
    "8:1",
    "4:3",
    "3:4",
]

#: nano-banana-pro 支持的比例（不含极限长宽比）
NANO_BANANA_PRO_RATIOS = [
    "auto",
    "1:1",
    "16:9",
    "9:16",
    "21:9",
    "2:3",
    "3:2",
    "4:3",
    "3:4",
    "4:5",
    "5:4",
]

#: 比例别名表。当前模型直接透传比例值，保留结构以便后续做兼容映射。
GPT_IMAGE_ASPECT_RATIO_ALIASES = {"1:1": "1:1", "16:9": "16:9", "9:16": "9:16"}

#: nano 系列支持的分辨率档位（上游必传 imageSize）
IMAGE_SIZES_FULL = ["1K", "2K", "4K"]

#: GPT Image 系列上游要求传「比例对应的具体尺寸」，而界面仍然展示比例。
GPT_IMAGE_PIXEL_VALUES = {
    "auto": "auto",
    "1:1": "1024x1024",
    "16:9": "1280x720",
    "9:16": "720x1280",
    "4:3": "1152x864",
    "3:4": "864x1152",
    "3:2": "1536x1024",
    "2:3": "1024x1536",
    "21:9": "1456x624",
    "9:21": "624x1456",
    "2:1": "1536x768",
    "1:2": "768x1536",
    "3:1": "2048x688",
    "1:3": "688x2048",
}

GPT_IMAGE_COMMON = {
    "endpoint": "/api/v1/invoke",
    "poll_endpoint": "/v1/content",
    "poll_param": "id",
    "image_param": "referenceImages",
    "aspect_ratio_param": "aspect_ratio",
    "aspect_ratio_aliases": GPT_IMAGE_ASPECT_RATIO_ALIASES,
    "aspect_ratio_values": GPT_IMAGE_PIXEL_VALUES,
    "include_image_size": False,
    "aspect_ratios": GPT_IMAGE_RATIOS,
    "image_sizes": [],
    "supports_quality": True,
}

NANO_BANANA_COMMON = {
    "endpoint": "/api/v1/invoke",
    "poll_endpoint": "/v1/content",
    "poll_param": "id",
    "image_param": "referenceImages",
    "aspect_ratio_param": "aspect_ratio",
    "aspect_ratio_aliases": {},
    # nano 系列直接传比例本身，不做像素换算
    "aspect_ratio_values": {},
    # nano 系列必须显式传 imageSize（1K/2K/4K）
    "include_image_size": True,
    "image_sizes": IMAGE_SIZES_FULL,
    "supports_quality": False,
}


def _model(model_id: str, common: dict[str, Any], **overrides: Any) -> dict[str, Any]:
    """按统一协议生成一条模型定义，模型编码同时作为上游 model 字段。"""
    return {
        **common,
        "id": model_id,
        "name": model_id,
        "api_model": model_id,
        **overrides,
    }


MODELS: list[dict[str, Any]] = [
    _model("GPT Image 2.5 sunburst", GPT_IMAGE_COMMON),
    _model("gpt-image-2.5-flare-1K", GPT_IMAGE_COMMON),
    _model("GPT-Image-2-L", GPT_IMAGE_COMMON),
    _model("nano-banana-2", NANO_BANANA_COMMON, aspect_ratios=NANO_BANANA_RATIOS),
    _model("Nano Banana 2.1", NANO_BANANA_COMMON, aspect_ratios=NANO_BANANA_RATIOS),
    _model("nano-banana-pro", NANO_BANANA_COMMON, aspect_ratios=NANO_BANANA_PRO_RATIOS),
]

_MODELS_BY_ID: dict[str, dict[str, Any]] = {m["id"]: m for m in MODELS}
DEFAULT_MODEL_ID = "GPT Image 2.5 sunburst"
#: 旧版本模型编码到新模型编码的迁移映射
_LEGACY_MODEL_ALIASES = {
    "gpt-image-2": "GPT Image 2.5 sunburst",
    "gpt-image-2-G": "gpt-image-2.5-flare-1K",
}

#: 接口层允许的全部比例：各模型比例并集，外加历史遗留的像素尺寸写法
_LEGACY_ASPECT_RATIOS = [
    "1024x1024", "1536x1024", "1024x1536", "1774x887", "887x1774", "800x800",
]
ALL_ASPECT_RATIOS: frozenset[str] = frozenset(
    {ratio for info in MODELS for ratio in info["aspect_ratios"]}
    | set(_LEGACY_ASPECT_RATIOS)
)


def normalize_model_id(model_id: Any) -> str:
    """把旧模型编码迁移到新编码；无法识别时回退到默认模型。"""
    cleaned = str(model_id or "").strip()
    cleaned = _LEGACY_MODEL_ALIASES.get(cleaned, cleaned)
    return cleaned if cleaned in _MODELS_BY_ID else DEFAULT_MODEL_ID


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
    """Return model capability dict, including the old L-model alias."""
    normalized = _LEGACY_MODEL_ALIASES.get(model_id, model_id)
    return _MODELS_BY_ID.get(normalized, _MODELS_BY_ID[DEFAULT_MODEL_ID])


def get_model_endpoint(model_id: str) -> str:
    return get_model_info(model_id)["endpoint"]


def get_model_poll_endpoint(model_id: str) -> str:
    return str(get_model_info(model_id).get("poll_endpoint") or "/v1/draw/result")


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
    "model": DEFAULT_MODEL_ID,
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
    if str(merged.get("base_url") or "").strip().rstrip("/") == LEGACY_DEFAULT_BASE_URL:
        merged["base_url"] = DEFAULT_BASE_URL
        try:
            save_settings(merged)
        except OSError:
            pass
    if not str(merged.get("base_url") or "").strip():
        merged["base_url"] = DEFAULT_BASE_URL
    if not str(merged.get("image_proxy_url") or "").strip():
        merged["image_proxy_url"] = DEFAULT_IMAGE_PROXY_URL
    merged["auto_retry_failed"] = bool(merged.get("auto_retry_failed") or False)
    try:
        merged["max_retry_count"] = max(0, min(int(merged.get("max_retry_count") or 0), 10))
    except (TypeError, ValueError):
        merged["max_retry_count"] = 0
    normalized_model = normalize_model_id(merged.get("model"))
    if normalized_model != merged.get("model"):
        merged["model"] = normalized_model
        try:
            save_settings(merged)
        except OSError:
            pass
    return merged


def save_settings(settings: dict[str, Any]) -> dict[str, Any]:
    ensure_dirs()
    merged = dict(DEFAULT_SETTINGS)
    merged.update({k: v for k, v in settings.items() if k in DEFAULT_SETTINGS})
    merged["model"] = normalize_model_id(merged.get("model"))
    if str(merged.get("base_url") or "").strip().rstrip("/") == LEGACY_DEFAULT_BASE_URL:
        merged["base_url"] = DEFAULT_BASE_URL
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
