from __future__ import annotations

import os
import socket
import sys
import webbrowser
from pathlib import Path

import uvicorn


# ---- Frozen (PyInstaller) support: resolve bundled paths ----

def _resolve_base() -> Path:
    """Return the root project directory, even when bundled by PyInstaller."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


_ROOT = _resolve_base()

# Patch backend.config so BASE_DIR / DATA_DIR point to the right locations.
import backend.config as _cfg

_cfg.BASE_DIR = _ROOT
_cfg.DATA_DIR = _ROOT / "data"
_cfg.CONFIG_PATH = _cfg.DATA_DIR / "settings.json"
_cfg.JOBS_PATH = _cfg.DATA_DIR / "jobs.json"

# Ensure data / outputs dirs exist (relative to the exe)
(_ROOT / "data").mkdir(parents=True, exist_ok=True)
(_ROOT / "outputs").mkdir(parents=True, exist_ok=True)


def check_python_version() -> None:
    if sys.version_info < (3, 10):
        print("需要 Python 3.10 或更高版本，当前版本: " + sys.version)
        sys.exit(1)


def find_free_port(start: int = 8787, end: int = 8800) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("8787-8800 端口都被占用，请手动指定端口")


def main() -> None:
    port = find_free_port()
    url = f"http://127.0.0.1:{port}"
    print(f"批量出图服务启动: {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    # Use the object form so PyInstaller-frozen importlib can find it.
    from backend.main import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()