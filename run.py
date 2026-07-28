from __future__ import annotations

import json
import os
import shutil
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
    check_python_version()
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
