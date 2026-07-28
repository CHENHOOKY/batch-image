from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from starlette.responses import FileResponse, HTMLResponse, JSONResponse

from .api_client import NoovaApiError, NoovaClient
from .config import (
    BASE_DIR,
    MODELS,
    assert_safe_directory,
    ensure_dirs,
    load_settings,
    public_settings,
    save_settings,
    validate_external_http_url,
)
from .job_manager import job_manager
from .models import ApiResponse, BatchJobCreate, ScheduleCreate, SettingsUpdate
from .security import APP_TOKEN, HostGuardMiddleware, TokenAuthMiddleware

ensure_dirs()

app = FastAPI(title="批量出图", version="1.7.0")
frontend_dir = BASE_DIR / "frontend"

# 中间件按添加顺序的逆序执行：先校验 Host（阻断 DNS rebinding），再校验令牌。
app.add_middleware(TokenAuthMiddleware, token=APP_TOKEN)
app.add_middleware(HostGuardMiddleware)


@app.on_event("startup")
async def _start_background_workers() -> None:
    job_manager.start_scheduler()


@app.on_event("shutdown")
async def _stop_background_workers() -> None:
    await job_manager.stop_scheduler()

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
    return {"ok": True, "service": "batch-image", "version": "1.7.0"}


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
    try:
        path = assert_safe_directory(Path(folder), field_name="源图片文件夹")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=400, detail="文件夹不存在或不可访问")

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


@app.get("/api/schedules")
async def list_schedules() -> ApiResponse:
    return ApiResponse(data=job_manager.list_schedules())


@app.post("/api/schedules")
async def create_schedule(body: ScheduleCreate) -> ApiResponse:
    try:
        schedule = await job_manager.create_schedule(
            run_at=body.run_at,
            payload=body.payload.model_dump(),
        )
        return ApiResponse(message="定时任务已保存", data=schedule.to_dict())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/schedules/{schedule_id}")
async def cancel_schedule(schedule_id: str) -> ApiResponse:
    try:
        schedule = await job_manager.cancel_schedule(schedule_id)
        return ApiResponse(message="定时任务已取消", data=schedule.to_dict())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str, sig: str = "") -> JSONResponse:
    """任务详情。

    带上 ?sig=<上次拿到的指纹> 时，如果状态没变就只回一个极小的响应。
    前端每 2 秒轮询一次，大批量任务的详情有好几 MB，绝大多数轮询之间
    其实什么都没变，条件返回能把这部分开销直接省掉。
    这里返回 JSONResponse 而不是 ApiResponse，是为了绕开 FastAPI 对
    response_model 的全量再校验（几万条子任务时这一步很贵）。
    """
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    signature = job.state_signature()
    if sig and sig == signature:
        return JSONResponse({"ok": True, "message": "", "data": {"id": job.id, "unchanged": True, "sig": signature}})
    return JSONResponse({"ok": True, "message": "", "data": {**job.to_dict(), "sig": signature}})


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

@app.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str) -> ApiResponse:
    """Re-run the failed / cancelled sub-tasks of a finished job."""
    try:
        job = await job_manager.retry_failed(job_id)
        return ApiResponse(message="已开始重试失败任务", data=job.to_dict())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/credit")
async def get_credit() -> ApiResponse:
    """Proxy Noova credit balance query via stored API key."""
    import httpx

    settings = load_settings()
    api_key = str(settings.get("api_key") or "").strip()
    try:
        base_url = validate_external_http_url(
            str(settings.get("base_url") or "https://noova.cn"),
            field_name="API Base URL",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not api_key:
        raise HTTPException(status_code=400, detail="请先在设置中填写 API Key")

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=15.0, read=30.0, write=30.0, pool=15.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            resp = await client.get(
                f"{base_url}/api/v1/gateway/credit",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if 300 <= resp.status_code < 400:
                raise HTTPException(status_code=502, detail="积分服务发生重定向，请检查 API Base URL")
            data = resp.json()
    except httpx.ConnectError as exc:
        raise HTTPException(status_code=502, detail="无法连接到积分服务，请检查网络") from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="积分接口请求超时") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"请求积分接口失败: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"积分接口返回格式异常: {exc}") from exc

    if not isinstance(data, dict):
        # 上游返回数组/字符串等非对象结构时不要直接 .get，否则 500
        raise HTTPException(status_code=502, detail="积分接口返回结构异常")

    if resp.status_code != 200:
        msg = data.get("message") or f"积分接口返回 {resp.status_code}"
        raise HTTPException(status_code=resp.status_code, detail=str(msg))

    return ApiResponse(message=str(data.get("message") or "获取积分成功"), data=data.get("data"))


@app.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str) -> ApiResponse:
    try:
        job = await job_manager.delete_job(job_id)
        return ApiResponse(message="任务已删除", data=job.to_dict())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc




# ---- 首页：把本次启动的访问令牌注入页面 ----
#
# 令牌只出现在由本服务渲染的 HTML 里。跨域网页受同源策略限制读不到响应体，
# 因此无法拿到令牌，也就无法伪造任何 /api/ 请求。

_TOKEN_PLACEHOLDER = "%%APP_TOKEN%%"


def _render_index() -> str:
    html = (frontend_dir / "index.html").read_text(encoding="utf-8")
    if _TOKEN_PLACEHOLDER not in html:
        # 兼容没有占位符的旧页面：直接在 <head> 后插入
        html = html.replace(
            "<head>",
            f'<head>\n    <script>window.__APP_TOKEN__ = "{APP_TOKEN}";</script>',
            1,
        )
        return html
    return html.replace(_TOKEN_PLACEHOLDER, APP_TOKEN)


@app.get("/")
async def index() -> HTMLResponse:
    if not (frontend_dir / "index.html").exists():
        raise HTTPException(status_code=404, detail="前端文件缺失")
    return HTMLResponse(
        _render_index(),
        headers={
            "Cache-Control": "no-store",
            # 令牌只靠同源策略保护，禁止被任意站点嵌套以防点击劫持
            "X-Frame-Options": "DENY",
            "Content-Security-Policy": "frame-ancestors 'none'",
        },
    )


@app.get("/index.html")
async def index_html() -> HTMLResponse:
    return await index()


#: 静态资源白名单。不再用 StaticFiles 挂载整个目录：那样 //index.html、
#: /index.html/ 之类的路径会绕过上面的渲染路由，把没替换令牌的原始页面发出去，
#: 用户会看到一个所有请求都 401 的界面且刷新无效。
_STATIC_FILES: dict[str, str] = {
    "styles.css": "text/css; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
    "lucide.min.js": "application/javascript; charset=utf-8",
    "favicon.svg": "image/svg+xml",
}


@app.get("/{asset}")
async def static_asset(asset: str) -> FileResponse:
    media_type = _STATIC_FILES.get(asset)
    if media_type is None:
        raise HTTPException(status_code=404, detail="资源不存在")
    target = frontend_dir / asset
    if not target.is_file():
        raise HTTPException(status_code=404, detail="资源不存在")
    return FileResponse(target, media_type=media_type)
