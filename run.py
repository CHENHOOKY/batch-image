from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

import uvicorn


# ---- Frozen (PyInstaller) support: resolve bundled paths ----

def _resolve_base() -> Path:
    """Return the root project directory, even when bundled by PyInstaller."""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


_ROOT = _resolve_base()


def _resolve_data_dir() -> Path:
    """决定配置 / 任务记录的存放位置。

    打包版必须放到用户目录，不能放在分发目录里：冻结运行时 _MEIPASS 指向
    batch_image_dist/_internal，配置写在那里的话，任何人把用过的整个
    batch_image_dist 打包转发，API Key 就跟着一起发出去了。
    源码运行仍然放项目目录下，方便开发。
    """
    if not getattr(sys, "frozen", False):
        return _ROOT / "data"

    appdata = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "BatchImage" / "data"
    return Path.home() / ".batch-image" / "data"


def _migrate_legacy_data(new_dir: Path) -> None:
    """把旧版本写在分发目录里的配置搬到新位置，搬完清掉原文件里的密钥。"""
    legacy_dir = _ROOT / "data"
    if legacy_dir.resolve() == new_dir.resolve() or not legacy_dir.is_dir():
        return
    for name in ("settings.json", "jobs.json", "schedules.json"):
        src, dst = legacy_dir / name, new_dir / name
        if not src.is_file() or dst.exists():
            continue
        try:
            shutil.copy2(src, dst)
            if name == "settings.json":
                # 原地把残留在分发目录里的密钥抹掉
                data = json.loads(src.read_text(encoding="utf-8"))
                if data.get("api_key"):
                    data["api_key"] = ""
                    src.write_text(
                        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
            print(f"[批量出图] 已迁移 {name} 到 {new_dir}")
        except (OSError, ValueError) as exc:
            print(f"[批量出图] 迁移 {name} 失败（不影响使用）: {exc}")


def _resolve_default_output_dir() -> Path:
    """打包版的默认输出目录。

    冻结运行时 _ROOT 指向 _internal，把生成结果写进去既不合理、也可能落在
    受保护目录里（例如整个程序被解压到 C:\\Program Files 下），那样目录安全
    校验会拒绝，导致每次创建任务都失败。所以优先放在 exe 旁边，不可写或不
    安全时退回用户文档目录。
    """
    from backend.config import assert_safe_directory

    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "outputs")
        candidates.append(Path.home() / "Documents" / "BatchImage" / "outputs")
        candidates.append(Path.home() / "BatchImage" / "outputs")
    else:
        candidates.append(_ROOT / "outputs")

    for candidate in candidates:
        try:
            assert_safe_directory(candidate)
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return candidate
        except (ValueError, OSError):
            continue
    return candidates[-1]


# Patch backend.config so BASE_DIR / DATA_DIR point to the right locations.
import backend.config as _cfg

_DATA_DIR = _resolve_data_dir()
_DATA_DIR.mkdir(parents=True, exist_ok=True)
_migrate_legacy_data(_DATA_DIR)

_cfg.BASE_DIR = _ROOT
_cfg.DATA_DIR = _DATA_DIR
_cfg.CONFIG_PATH = _DATA_DIR / "settings.json"
_cfg.JOBS_PATH = _DATA_DIR / "jobs.json"
_cfg.SCHEDULES_PATH = _DATA_DIR / "schedules.json"

# 输出目录：放在用户能直接找到、且一定可写的位置
_OUTPUT_DIR = _resolve_default_output_dir()
_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
_cfg.DEFAULT_SETTINGS["output_dir"] = str(_OUTPUT_DIR)


def check_python_version() -> None:
    if sys.version_info < (3, 10):
        print("需要 Python 3.10 或更高版本，当前版本: " + sys.version)
        sys.exit(1)


# ---- 单实例：清理残存的旧服务 ----
#
# 上一次的进程如果还活着（例如用户只关了浏览器，或关窗口没结束进程），旧服务会
# 继续占着 8787。这时新进程会退到 8788，用户看到的却是旧界面，表现出来就是
# "重启了却没生效"。所以启动前先探测端口：只有当端口上确实是本工具（首页带本
# 工具特有的静态资源）时才结束它，不会误伤别的程序。

_APP_MARKERS = (b"/app.js", b"/favicon.svg")
_CREATE_NO_WINDOW = 0x08000000


def _port_in_use(port: int, timeout: float = 0.4) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _looks_like_this_app(port: int, timeout: float = 0.8) -> bool:
    """首页响应里同时出现本工具的标志性资源，才认定是本工具。"""
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/", timeout=timeout) as response:
            if response.status != 200:
                return False
            body = response.read(65536)
    except OSError:
        return False
    return all(marker in body for marker in _APP_MARKERS)


def _stop_listener_on(port: int) -> int | None:
    """结束监听该端口的进程并返回 PID（仅 Windows）。"""
    script = (
        f"$owner = (Get-NetTCPConnection -LocalPort {port} -State Listen "
        "-ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty OwningProcess); "
        "if ($owner) { Stop-Process -Id $owner -Force -ErrorAction Stop; $owner }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=_CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    if result.returncode == 0 and lines and lines[-1].isdigit():
        return int(lines[-1])
    return None


def stop_stale_instances(start: int = 8787, end: int = 8800) -> None:
    """结束仍然占着本项目端口的旧实例，避免新旧服务同时存在。"""
    if not sys.platform.startswith("win"):
        return
    for port in range(start, end + 1):
        if not _port_in_use(port):
            continue
        if not _looks_like_this_app(port):
            print(f"[批量出图] 端口 {port} 已被其它程序占用，换端口")
            continue
        pid = _stop_listener_on(port)
        if pid is None:
            print(f"[批量出图] 端口 {port} 上的旧实例无法结束（可能权限不足）")
        else:
            print(f"[批量出图] 已结束残存的旧实例 PID {pid}（原占用端口 {port}）")
    # 给操作系统一点时间回收端口
    time.sleep(0.5)


def find_free_port(start: int = 8787, end: int = 8800, attempts: int = 8) -> int:
    for _ in range(attempts):
        for port in range(start, end + 1):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    sock.bind(("127.0.0.1", port))
                    return port
                except OSError:
                    continue
        time.sleep(0.4)
    raise RuntimeError("8787-8800 端口都被占用，请手动指定端口")


def open_browser_when_ready(url: str, timeout: float = 10.0) -> None:
    """Open the local UI only after the server is accepting requests.

    Opening the browser before Uvicorn has bound its port can make Chromium show
    a stale cached page from an earlier package.  The readiness probe bypasses
    environment proxies so a system proxy cannot delay access to localhost.
    """
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with opener.open(url, timeout=0.5) as response:
                if response.status == 200:
                    webbrowser.open(url)
                    return
        except OSError:
            pass
        time.sleep(0.1)


def main() -> None:
    check_python_version()
    stop_stale_instances()
    port = find_free_port()
    url = f"http://127.0.0.1:{port}"
    print(f"批量出图服务启动: {url}")
    threading.Thread(target=open_browser_when_ready, args=(url,), daemon=True).start()
    # Use the object form so PyInstaller-frozen importlib can find it.
    from backend.main import app
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
