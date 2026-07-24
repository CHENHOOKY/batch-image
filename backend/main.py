from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from .api_client import NoovaApiError, NoovaClient
from .config import (
    BASE_DIR,
    MODELS,
    ensure_dirs,
    load_settings,
    public_settings,
    save_settings,
)
from .job_manager import job_manager
from .models import ApiResponse, BatchJobCreate, SettingsUpdate

ensure_dirs()

app = FastAPI(title="批量出图", version="1.4.1")
frontend_dir = BASE_DIR / "frontend"

# --- Non-blocking folder/file picker sessions ---

_pick_sessions: dict[str, dict[str, Any]] = {}
_pick_lock = asyncio.Lock()
_PICK_SESSION_TTL = 300  # 5 minutes


async def _cleanup_pick_sessions() -> None:
    """Remove expired or excess pick sessions."""
    async with _pick_lock:
        now = time.monotonic()
        expired = [
            sid for sid, s in _pick_sessions.items()
            if s.get("done") and now - s.get("_completed_at", now) > 30
        ]
        for sid in expired:
            _pick_sessions.pop(sid, None)
        # Also remove very old incomplete sessions (user abandoned)
        stale = [
            sid for sid, s in _pick_sessions.items()
            if not s.get("done") and now - s.get("_created_at", now) > _PICK_SESSION_TTL
        ]
        for sid in stale:
            _pick_sessions.pop(sid, None)
        # Keep at most 5 sessions
        if len(_pick_sessions) > 5:
            done_ids = [sid for sid, s in _pick_sessions.items() if s.get("done")]
            for old_id in done_ids[:len(_pick_sessions) - 5]:
                _pick_sessions.pop(old_id, None)


async def _run_folder_picker(session_id: str, title: str, initial: str) -> None:
    """Run tkinter folder dialog in a thread, store result in session."""
    import os

    def _pick() -> str | None:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
            root.lift()
            root.focus_force()
        except Exception:
            pass
        root.update()
        selected = filedialog.askdirectory(
            title=title,
            initialdir=initial if initial and os.path.isdir(initial) else None,
            mustexist=True,
        )
        try:
            root.destroy()
        except Exception:
            pass
        return selected or None

    try:
        loop = asyncio.get_running_loop()
        selected = await loop.run_in_executor(None, _pick)
        async with _pick_lock:
            if selected:
                path = Path(selected).expanduser().resolve()
                _pick_sessions[session_id]["result"] = str(path)
                _pick_sessions[session_id]["cancelled"] = False
            else:
                _pick_sessions[session_id]["result"] = None
                _pick_sessions[session_id]["cancelled"] = True
    except Exception as exc:
        async with _pick_lock:
            _pick_sessions[session_id]["error"] = str(exc)
    finally:
        async with _pick_lock:
            _pick_sessions[session_id]["done"] = True
            _pick_sessions[session_id]["_completed_at"] = time.monotonic()


async def _run_file_picker(session_id: str, title: str, initial: str) -> None:
    """Run tkinter file dialog in a thread, store result in session."""
    import os

    def _pick() -> str | None:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
            root.lift()
            root.focus_force()
        except Exception:
            pass
        root.update()

        initial_dir = None
        if initial:
            p = Path(initial)
            if p.is_file():
                initial_dir = str(p.parent)
            elif p.is_dir():
                initial_dir = str(p)

        selected = filedialog.askopenfilename(
            title=title,
            initialdir=initial_dir if initial_dir and os.path.isdir(initial_dir) else None,
            filetypes=[
                ("Image files", "*.jpg;*.jpeg;*.png;*.webp;*.bmp"),
                ("All files", "*.*"),
            ],
        )
        try:
            root.destroy()
        except Exception:
            pass
        return selected or None

    try:
        loop = asyncio.get_running_loop()
        selected = await loop.run_in_executor(None, _pick)
        async with _pick_lock:
            if selected:
                path = Path(selected).expanduser().resolve()
                if path.exists() and path.is_file():
                    _pick_sessions[session_id]["result"] = str(path)
                    _pick_sessions[session_id]["cancelled"] = False
                else:
                    _pick_sessions[session_id]["error"] = f"文件不存在: {path}"
                    _pick_sessions[session_id]["cancelled"] = True
            else:
                _pick_sessions[session_id]["result"] = None
                _pick_sessions[session_id]["cancelled"] = True
    except Exception as exc:
        async with _pick_lock:
            _pick_sessions[session_id]["error"] = str(exc)
    finally:
        async with _pick_lock:
            _pick_sessions[session_id]["done"] = True
            _pick_sessions[session_id]["_completed_at"] = time.monotonic()


# --- Routes ---


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "service": "batch-image", "version": "1.4.1"}


@app.get("/api/settings")
async def get_settings() -> ApiResponse:
    return ApiResponse(data=public_settings())


@app.put("/api/settings")
async def update_settings(body: SettingsUpdate) -> ApiResponse:
    current = load_settings()
    patch = body.model_dump(exclude_none=True)

    # empty api_key means "keep existing key"
    if "api_key" in patch and not str(patch.get("api_key") or "").strip():
        patch.pop("api_key", None)

    current.update(patch)
    save_settings(current)
    return ApiResponse(message="设置已保存", data=public_settings(current))


@app.get("/api/models")
async def list_models() -> ApiResponse:
    return ApiResponse(data=MODELS)


@app.get("/api/options")
async def list_options() -> ApiResponse:
    return ApiResponse(data={"models": MODELS})


@app.post("/api/folder/scan")
async def scan_folder(body: dict[str, Any]) -> ApiResponse:
    folder = str(body.get("path") or "").strip()
    if not folder:
        raise HTTPException(status_code=400, detail="请提供文件夹路径")
    path = Path(folder).expanduser()
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=400, detail=f"文件夹不存在: {path}")

    exts = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
    files = sorted(
        [p for p in path.iterdir() if p.is_file() and p.suffix.lower() in exts],
        key=lambda p: p.name.lower(),
    )
    return ApiResponse(
        data={
            "path": str(path.resolve()),
            "count": len(files),
            "files": [p.name for p in files[:200]],
            "truncated": len(files) > 200,
        }
    )


@app.post("/api/folder/pick")
async def start_pick_folder(body: dict[str, Any] | None = None) -> ApiResponse:
    """Start a non-blocking folder picker dialog. Returns session_id for polling."""
    await _cleanup_pick_sessions()

    payload = body or {}
    title = str(payload.get("title") or "选择文件夹")
    initial = str(payload.get("initial") or "").strip()

    # Check if a picker is already running
    for sess in _pick_sessions.values():
        if not sess.get("done"):
            raise HTTPException(status_code=409, detail="已有选择窗口打开，请先完成当前选择")

    session_id = uuid.uuid4().hex[:12]
    _pick_sessions[session_id] = {
        "done": False,
        "result": None,
        "cancelled": False,
        "error": None,
        "type": "folder",
        "_created_at": time.monotonic(),
    }

    asyncio.create_task(_run_folder_picker(session_id, title, initial))
    return ApiResponse(data={"session_id": session_id, "done": False})


@app.post("/api/file/pick")
async def start_pick_file(body: dict[str, Any] | None = None) -> ApiResponse:
    """Start a non-blocking file picker dialog. Returns session_id for polling."""
    await _cleanup_pick_sessions()

    payload = body or {}
    title = str(payload.get("title") or "选择图片")
    initial = str(payload.get("initial") or "").strip()

    # Check if a picker is already running
    for sess in _pick_sessions.values():
        if not sess.get("done"):
            raise HTTPException(status_code=409, detail="已有选择窗口打开，请先完成当前选择")

    session_id = uuid.uuid4().hex[:12]
    _pick_sessions[session_id] = {
        "done": False,
        "result": None,
        "cancelled": False,
        "error": None,
        "type": "file",
        "_created_at": time.monotonic(),
    }

    asyncio.create_task(_run_file_picker(session_id, title, initial))
    return ApiResponse(data={"session_id": session_id, "done": False})


@app.get("/api/pick/{session_id}")
async def poll_pick(session_id: str) -> ApiResponse:
    """Poll for the result of a folder/file picker session."""
    await _cleanup_pick_sessions()

    session = _pick_sessions.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="选择会话不存在")

    if not session.get("done"):
        return ApiResponse(data={"session_id": session_id, "done": False})

    result: dict[str, Any] = {
        "session_id": session_id,
        "done": True,
        "cancelled": session.get("cancelled", False),
        "path": session.get("result"),
    }
    if session.get("error"):
        result["error"] = session["error"]
    return ApiResponse(data=result)


@app.post("/api/test-connection")
async def test_connection() -> ApiResponse:
    settings = load_settings()
    try:
        async with NoovaClient(
            api_key=str(settings.get("api_key") or ""),
            base_url=str(settings.get("base_url") or "https://noova.cn"),
            image_proxy_url=str(settings.get("image_proxy_url") or ""),
        ) as client:
            await client.verify_connection()
        return ApiResponse(message="连接成功，API Key 可用")
    except NoovaApiError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"连接失败: {exc}") from exc


@app.get("/api/jobs")
async def list_jobs() -> ApiResponse:
    return ApiResponse(data=job_manager.list_jobs())


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> ApiResponse:
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return ApiResponse(data=job.to_dict())


@app.post("/api/jobs")
async def create_job(body: BatchJobCreate) -> ApiResponse:
    try:
        job = await job_manager.create_job(body.model_dump())
        return ApiResponse(message="任务已创建", data=job.to_dict())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"创建任务失败: {exc}") from exc


@app.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> ApiResponse:
    try:
        job = await job_manager.cancel_job(job_id)
        return ApiResponse(message="已发送取消请求", data=job.to_dict())
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@app.get("/api/credit")
async def get_credit() -> ApiResponse:
    """Proxy Noova credit balance query via stored API key."""
    import httpx

    settings = load_settings()
    api_key = str(settings.get("api_key") or "").strip()
    base_url = str(settings.get("base_url") or "https://noova.cn").rstrip("/")

    if not api_key:
        raise HTTPException(status_code=400, detail="请先在设置中填写 API Key")

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=30.0, write=30.0, pool=15.0),
            follow_redirects=True,
        ) as client:
            resp = await client.get(
                f"{base_url}/api/v1/gateway/credit",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            data = resp.json()
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=502, detail="无法连接到积分服务，请检查网络") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="积分接口请求超时") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"请求积分接口失败: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"积分接口返回格式异常: {exc}") from exc

    if resp.status_code != 200:
        msg = data.get("message", f"积分接口返回 {resp.status_code}") if isinstance(data, dict) else f"积分接口返回 {resp.status_code}"
        raise HTTPException(status_code=resp.status_code, detail=msg)

    return ApiResponse(message=data.get("message", "获取积分成功"), data=data.get("data"))


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str) -> ApiResponse:
    try:
        job = await job_manager.delete_job(job_id)
        return ApiResponse(message="任务已删除", data=job.to_dict())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc




# ---- Browser-native folder / file path resolution ----
# When the user picks a folder via <input webkitdirectory>, the browser
# only gives us a relative path.  We resolve it by asking the OS to locate
# the file we just saw, then strip the file name to get the folder.

import subprocess as _subprocess
import platform as _platform

@app.post("/api/folder/resolve-prefix")
async def resolve_folder_prefix(body: dict[str, Any]) -> ApiResponse:
    """Given a filename and its relative_path from webkitdirectory,
    find the absolute folder on disk by scanning the file system
    inside likely roots (exe dir, desktop, home)."""
    fname = str(body.get("filename") or "").strip()
    rel = str(body.get("relative_path") or "").strip()
    if not fname or not rel:
        raise HTTPException(status_code=400, detail="filename / relative_path required")

    dir_prefix = rel[: -len(fname)].lstrip("/").lstrip("\\")
    import os as _os
    _matched = None

    # Walk from the exe directory and its parents (up to 3 levels up),
    # then desktop, then home.  Stop as soon as we find the file.
    roots = []
    try:
        cur = str(BASE_DIR)
        for _ in range(5):
            roots.append(cur)
            parent = _os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        roots.append(_os.path.join(_os.path.expanduser("~"), "Desktop"))
        roots.append(_os.path.expanduser("~"))
    except Exception:
        roots = [str(BASE_DIR)]

    for sysroot in roots:
        try:
            for dirpath, _dirnames, filenames in _os.walk(sysroot):
                if fname in filenames:
                    # If the relative prefix matches, prefer that
                    norm = dirpath.replace("\\", "/")
                    if dir_prefix and norm.endswith("/" + dir_prefix):
                        _matched = norm[: -len(dir_prefix)].rstrip("/")
                        break
                    # Otherwise take any match
                    if _matched is None:
                        _matched = norm
            if _matched:
                break
        except (PermissionError, OSError):
            continue

    if _matched:
        return ApiResponse(data={"path": _matched, "root": sysroot})
    # Fallback: just return the relative parent as a best-guess path
    fallback = str(Path(BASE_DIR) / dir_prefix) if dir_prefix else str(BASE_DIR)
    return ApiResponse(data={"path": fallback, "root": str(BASE_DIR)})


@app.post("/api/file/resolve-abs")
async def resolve_file_abs(body: dict[str, Any]) -> ApiResponse:
    """Given a file picked via <input type=file>, find its absolute path
    in the workspace.  The browser only sends filename/size/type/lastModified,
    so we best-effort locate the file."""
    fname = str(body.get("filename") or "").strip()
    if not fname:
        raise HTTPException(status_code=400, detail="filename required")
    import os as _os
    roots = {str(BASE_DIR)}
    try:
        roots.add(_os.path.expanduser("~"))
        if _platform.system() == "Windows":
            for sub in ("Desktop", "Documents", "Downloads", "Pictures"):
                d = _os.path.join(_os.path.expanduser("~"), sub)
                if _os.path.isdir(d):
                    roots.add(d)
    except Exception:
        pass
    for sysroot in roots:
        try:
            if _platform.system() == "Windows":
                result = _subprocess.run(
                    ["cmd", "/c", "where", "/r", f"{sysroot}", fname],
                    capture_output=True, encoding="gbk", errors="replace", timeout=20,
                )
            else:
                result = _subprocess.run(
                    ["find", sysroot, "-name", fname, "-maxdepth", 15],
                    capture_output=True, encoding="gbk", errors="replace", timeout=20,
                )
            lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
            if lines:
                return ApiResponse(data={"path": lines[0]})
        except Exception:
            continue
    return ApiResponse(data={"path": ""})

if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")


