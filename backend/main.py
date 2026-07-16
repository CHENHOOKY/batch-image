from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from .api_client import NoovaApiError, NoovaClient
from .config import BASE_DIR, ensure_dirs, load_settings, public_settings, save_settings
from .job_manager import job_manager
from .models import ApiResponse, BatchJobCreate, SettingsUpdate

ensure_dirs()

app = FastAPI(title="批量出图", version="1.1.0")
frontend_dir = BASE_DIR / "frontend"
_folder_pick_lock = asyncio.Lock()


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "service": "batch-image", "version": "1.1.0"}


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
    return ApiResponse(
        data=[
            {"id": "gpt-image-2", "name": "gpt-image-2"},
            {"id": "gpt-image-2-vip", "name": "gpt-image-2-vip"},
        ]
    )


@app.get("/api/options")
async def list_options() -> ApiResponse:
    return ApiResponse(
        data={
            "aspect_ratios": [
                "auto",
                "1:1",
                "3:2",
                "2:3",
                "16:9",
                "9:16",
                "5:4",
                "4:5",
                "4:3",
                "3:4",
                "21:9",
                "9:21",
                "1:3",
                "3:1",
                "2:1",
                "1:2",
            ],
            "qualities": ["auto", "low", "medium", "high"],
            "models": [
                {"id": "gpt-image-2", "name": "gpt-image-2"},
                {"id": "gpt-image-2-vip", "name": "gpt-image-2-vip"},
            ],
        }
    )


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
async def pick_folder(body: dict[str, Any] | None = None) -> ApiResponse:
    """Open a native Windows folder dialog and return the selected path."""
    import os

    payload = body or {}
    title = str(payload.get("title") or "选择文件夹")
    initial = str(payload.get("initial") or "").strip()

    if _folder_pick_lock.locked():
        raise HTTPException(status_code=409, detail="已有文件夹选择窗口打开，请先完成当前选择")

    def _pick() -> str | None:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
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

    async with _folder_pick_lock:
        loop = asyncio.get_running_loop()
        selected = await loop.run_in_executor(None, _pick)

    if not selected:
        return ApiResponse(ok=True, message="已取消", data={"path": None, "cancelled": True})
    path = Path(selected).expanduser().resolve()
    return ApiResponse(data={"path": str(path), "cancelled": False})


@app.post("/api/file/pick")
async def pick_file(body: dict[str, Any] | None = None) -> ApiResponse:
    """Open a native Windows file dialog and return the selected image path."""
    import os

    payload = body or {}
    title = str(payload.get("title") or "选择图片")
    initial = str(payload.get("initial") or "").strip()

    if _folder_pick_lock.locked():
        raise HTTPException(status_code=409, detail="已有选择窗口打开，请先完成当前选择")

    def _pick() -> str | None:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
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

    async with _folder_pick_lock:
        loop = asyncio.get_running_loop()
        selected = await loop.run_in_executor(None, _pick)

    if not selected:
        return ApiResponse(ok=True, message="已取消", data={"path": None, "cancelled": True})
    path = Path(selected).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=400, detail=f"文件不存在: {path}")
    return ApiResponse(data={"path": str(path), "cancelled": False})


@app.post("/api/test-connection")
async def test_connection() -> ApiResponse:
    settings = load_settings()
    try:
        async with NoovaClient(
            api_key=str(settings.get("api_key") or ""),
            base_url=str(settings.get("base_url") or "https://noova.cn"),
        ) as client:
            await client.get_upload_credential(
                filename="connection-test.png",
                content_type="image/png",
                size=128,
            )
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


if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
