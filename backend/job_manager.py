from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from .api_client import NoovaApiError, NoovaClient
from .config import JOBS_PATH, ensure_dirs, load_settings

TaskStatus = Literal[
    "pending",
    "uploading",
    "submitting",
    "processing",
    "downloading",
    "success",
    "failed",
    "cancelled",
]
JobStatus = Literal["queued", "running", "completed", "failed", "cancelled"]


def folder_name_from_prompt(prompt: str, max_len: int = 40) -> str:
    text = " ".join(str(prompt or "").split())
    invalid = '<>:"/\\|?*'
    for ch in invalid:
        text = text.replace(ch, "_")
    text = text.strip(" .")
    if not text:
        text = "未命名"
    digest = hashlib.sha1(str(prompt or "").encode("utf-8")).hexdigest()[:6]
    # keep room for _xxxxxx uniqueness suffix
    keep = max(8, max_len - 7)
    if len(text) > keep:
        text = text[:keep].rstrip(" .") or "未命名"
    return f"{text}_{digest}"


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def unique_output_path(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate
    index = 2
    while True:
        candidate = directory / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


@dataclass
class JobTask:
    id: str
    image_path: str
    image_name: str
    prompt_name: str
    prompt: str
    source_dir: str
    output_dir: str
    extra_image_1: str = ""
    extra_image_2: str = ""
    status: TaskStatus = "pending"
    message: str = ""
    remote_task_id: str | None = None
    output_path: str | None = None
    result_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "image_path": self.image_path,
            "image_name": self.image_name,
            "prompt_name": self.prompt_name,
            "prompt": self.prompt,
            "source_dir": self.source_dir,
            "output_dir": self.output_dir,
            "extra_image_1": self.extra_image_1,
            "extra_image_2": self.extra_image_2,
            "status": self.status,
            "message": self.message,
            "remote_task_id": self.remote_task_id,
            "output_path": self.output_path,
            "result_url": self.result_url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "JobTask":
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:10]),
            image_path=str(data.get("image_path") or ""),
            image_name=str(data.get("image_name") or ""),
            prompt_name=str(data.get("prompt_name") or ""),
            prompt=str(data.get("prompt") or ""),
            source_dir=str(data.get("source_dir") or ""),
            output_dir=str(data.get("output_dir") or ""),
            extra_image_1=str(data.get("extra_image_1") or ""),
            extra_image_2=str(data.get("extra_image_2") or ""),
            status=data.get("status") or "pending",
            message=str(data.get("message") or ""),
            remote_task_id=data.get("remote_task_id"),
            output_path=data.get("output_path"),
            result_url=data.get("result_url"),
        )


@dataclass
class BatchJob:
    id: str
    source_dir: str
    output_dir: str
    model: str
    aspect_ratio: str
    quality: str
    concurrency: int
    poll_interval_sec: float = 3.0
    tasks: list[JobTask] = field(default_factory=list)
    status: JobStatus = "queued"
    message: str = ""
    created_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    cancel_flag: bool = False

    def counts(self) -> dict[str, int]:
        total = len(self.tasks)
        success = sum(1 for t in self.tasks if t.status == "success")
        failed = sum(1 for t in self.tasks if t.status == "failed")
        cancelled = sum(1 for t in self.tasks if t.status == "cancelled")
        pending = total - success - failed - cancelled
        return {
            "total": total,
            "success": success,
            "failed": failed,
            "cancelled": cancelled,
            "pending": pending,
        }

    def to_dict(self) -> dict[str, Any]:
        c = self.counts()
        return {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "source_dir": self.source_dir,
            "output_dir": self.output_dir,
            "model": self.model,
            "aspect_ratio": self.aspect_ratio,
            "quality": self.quality,
            "concurrency": self.concurrency,
            "poll_interval_sec": self.poll_interval_sec,
            "total": c["total"],
            "success": c["success"],
            "failed": c["failed"],
            "cancelled": c["cancelled"],
            "pending": c["pending"],
            "message": self.message,
            "tasks": [t.to_dict() for t in self.tasks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BatchJob":
        tasks = [JobTask.from_dict(t) for t in (data.get("tasks") or [])]
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            source_dir=str(data.get("source_dir") or ""),
            output_dir=str(data.get("output_dir") or ""),
            model=str(data.get("model") or "gpt-image-2"),
            aspect_ratio=str(data.get("aspect_ratio") or "1:1"),
            quality=str(data.get("quality") or "auto"),
            concurrency=int(data.get("concurrency") or 2),
            poll_interval_sec=float(data.get("poll_interval_sec") or 3),
            tasks=tasks,
            status=data.get("status") or "queued",
            message=str(data.get("message") or ""),
            created_at=str(data.get("created_at") or now_iso()),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            cancel_flag=bool(data.get("cancel_flag") or False),
        )


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, BatchJob] = {}
        self._lock = asyncio.Lock()
        self._persist_lock = asyncio.Lock()
        self._active_job_id: str | None = None
        self._load_jobs()

    def _load_jobs(self) -> None:
        ensure_dirs()
        if not JOBS_PATH.exists():
            return
        try:
            raw = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        jobs_data = raw.get("jobs") if isinstance(raw, dict) else raw
        if not isinstance(jobs_data, list):
            return

        for item in jobs_data:
            if not isinstance(item, dict):
                continue
            job = BatchJob.from_dict(item)
            # process interrupted by restart cannot continue reliably without remote resume
            if job.status in {"queued", "running"}:
                job.status = "failed"
                job.message = "服务重启，任务中断"
                job.finished_at = job.finished_at or now_iso()
                for task in job.tasks:
                    if task.status in {
                        "pending",
                        "uploading",
                        "submitting",
                        "processing",
                        "downloading",
                    }:
                        task.status = "failed"
                        task.message = "服务重启，任务中断"
            self._jobs[job.id] = job

    async def _save_jobs(self) -> None:
        ensure_dirs()
        payload = {
            "updated_at": now_iso(),
            "jobs": [job.to_dict() | {"cancel_flag": job.cancel_flag} for job in self._jobs.values()],
        }
        async with self._persist_lock:
            tmp = JOBS_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(JOBS_PATH)

    def list_jobs(self) -> list[dict[str, Any]]:
        jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return [j.to_dict() for j in jobs]

    def get_job(self, job_id: str) -> BatchJob | None:
        return self._jobs.get(job_id)

    def has_active_job(self) -> BatchJob | None:
        if self._active_job_id and self._active_job_id in self._jobs:
            job = self._jobs[self._active_job_id]
            if job.status in {"queued", "running"}:
                return job
        for job in self._jobs.values():
            if job.status in {"queued", "running"}:
                return job
        return None

    async def create_job(self, payload: dict[str, Any]) -> BatchJob:
        active = self.has_active_job()
        if active:
            raise ValueError(f"已有任务正在运行：{active.id}，请等待完成或先取消")

        settings = load_settings()
        global_source = str(payload.get("source_dir") or settings.get("source_dir") or "").strip()
        global_output = str(payload.get("output_dir") or settings.get("output_dir") or "").strip()
        model = payload.get("model") or settings.get("model") or "gpt-image-2"
        aspect_ratio = payload.get("aspect_ratio") or settings.get("aspect_ratio") or "1:1"
        quality = payload.get("quality") or settings.get("quality") or "auto"
        concurrency = int(payload.get("concurrency") or settings.get("concurrency") or 2)
        concurrency = max(1, min(concurrency, 10))
        poll_interval_sec = float(
            payload.get("poll_interval_sec")
            if payload.get("poll_interval_sec") is not None
            else (settings.get("poll_interval_sec") or 3)
        )
        poll_interval_sec = max(1.0, min(poll_interval_sec, 60.0))

        prompts = payload.get("prompts") or []
        enabled_prompts = [
            p for p in prompts if p.get("enabled", True) and str(p.get("prompt", "")).strip()
        ]
        if not enabled_prompts:
            raise ValueError("至少填写 1 个有效提示词")
        if len(enabled_prompts) > 10:
            raise ValueError("最多支持 10 个提示词")

        exts = {
            e.lower() if str(e).startswith(".") else f".{str(e).lower()}"
            for e in (payload.get("image_extensions") or [".jpg", ".jpeg", ".png", ".webp", ".bmp"])
        }

        def list_images(folder: Path) -> list[Path]:
            if not folder.exists() or not folder.is_dir():
                raise ValueError(f"源图片文件夹不存在: {folder}")
            images = sorted(
                [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in exts],
                key=lambda p: p.name.lower(),
            )
            if not images:
                raise ValueError(f"源文件夹中没有找到图片: {folder}")
            return images

        job_tasks: list[JobTask] = []
        used_sources: list[str] = []
        used_outputs: list[str] = []

        for item in enabled_prompts:
            prompt_text = str(item.get("prompt") or "").strip()
            prompt_name = folder_name_from_prompt(prompt_text)
            source_raw = str(item.get("source_dir") or "").strip() or global_source
            output_raw = str(item.get("output_dir") or "").strip()
            extra_1_raw = str(item.get("extra_image_1") or "").strip()
            extra_2_raw = str(item.get("extra_image_2") or "").strip()

            if not source_raw:
                raise ValueError(f"提示词「{prompt_name}」未指定源图片文件夹，且全局源文件夹也为空")

            source_dir = Path(source_raw).expanduser()
            images = list_images(source_dir)

            if output_raw:
                output_dir = Path(output_raw).expanduser()
            else:
                if not global_output:
                    raise ValueError(f"提示词「{prompt_name}」未指定输出文件夹，且全局输出文件夹也为空")
                output_dir = Path(global_output).expanduser() / prompt_name

            output_dir.mkdir(parents=True, exist_ok=True)
            used_sources.append(str(source_dir.resolve()))
            used_outputs.append(str(output_dir.resolve()))

            extra_image_1 = ""
            extra_image_2 = ""
            if extra_1_raw:
                p1 = Path(extra_1_raw).expanduser()
                if not p1.exists() or not p1.is_file():
                    raise ValueError(f"提示词「{prompt_name}」的图一不存在: {p1}")
                extra_image_1 = str(p1.resolve())
            if extra_2_raw:
                p2 = Path(extra_2_raw).expanduser()
                if not p2.exists() or not p2.is_file():
                    raise ValueError(f"提示词「{prompt_name}」的图二不存在: {p2}")
                extra_image_2 = str(p2.resolve())

            skip_paths = {p for p in (extra_image_1, extra_image_2) if p}
            for image in images:
                image_resolved = str(image.resolve())
                if image_resolved in skip_paths:
                    # 固定参考图不作为批量出图目标，避免重复生成
                    continue
                job_tasks.append(
                    JobTask(
                        id=uuid.uuid4().hex[:10],
                        image_path=image_resolved,
                        image_name=image.name,
                        prompt_name=prompt_name,
                        prompt=prompt_text,
                        source_dir=str(source_dir.resolve()),
                        output_dir=str(output_dir.resolve()),
                        extra_image_1=extra_image_1,
                        extra_image_2=extra_image_2,
                    )
                )

        if not job_tasks:
            raise ValueError("没有可执行的子任务")

        job = BatchJob(
            id=uuid.uuid4().hex[:12],
            source_dir=global_source or (used_sources[0] if used_sources else ""),
            output_dir=global_output or (used_outputs[0] if used_outputs else ""),
            model=str(model),
            aspect_ratio=str(aspect_ratio),
            quality=str(quality),
            concurrency=concurrency,
            poll_interval_sec=poll_interval_sec,
            tasks=job_tasks,
        )

        async with self._lock:
            self._jobs[job.id] = job
            self._active_job_id = job.id

        await self._save_jobs()
        asyncio.create_task(self._run_job(job.id))
        return job

    async def cancel_job(self, job_id: str) -> BatchJob:
        job = self._jobs.get(job_id)
        if not job:
            raise ValueError("任务不存在")
        job.cancel_flag = True
        if job.status in {"queued", "running"}:
            job.message = "正在取消..."
        await self._save_jobs()
        return job

    async def _run_job(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return

        settings = load_settings()
        job.status = "running"
        job.started_at = now_iso()
        job.message = "任务执行中"
        await self._save_jobs()

        try:
            client = NoovaClient(
                api_key=str(settings.get("api_key") or ""),
                base_url=str(settings.get("base_url") or "https://noova.cn"),
            )
        except NoovaApiError as exc:
            job.status = "failed"
            job.message = str(exc)
            job.finished_at = now_iso()
            for task in job.tasks:
                if task.status == "pending":
                    task.status = "failed"
                    task.message = str(exc)
            if self._active_job_id == job.id:
                self._active_job_id = None
            await self._save_jobs()
            return

        poll_interval = float(job.poll_interval_sec or settings.get("poll_interval_sec") or 3)
        poll_timeout = float(settings.get("poll_timeout_sec") or 600)
        semaphore = asyncio.Semaphore(job.concurrency)
        upload_cache: dict[str, str] = {}
        upload_locks: dict[str, asyncio.Lock] = {}
        upload_locks_guard = asyncio.Lock()

        async def get_upload_lock(path_key: str) -> asyncio.Lock:
            async with upload_locks_guard:
                lock = upload_locks.get(path_key)
                if lock is None:
                    lock = asyncio.Lock()
                    upload_locks[path_key] = lock
                return lock

        async def worker(task: JobTask) -> None:
            async with semaphore:
                if job.cancel_flag:
                    task.status = "cancelled"
                    task.message = "已取消"
                    await self._save_jobs()
                    return
                await self._run_task(
                    client=client,
                    job=job,
                    task=task,
                    poll_interval=poll_interval,
                    poll_timeout=poll_timeout,
                    upload_cache=upload_cache,
                    get_upload_lock=get_upload_lock,
                )
                await self._save_jobs()

        try:
            async with client:
                await asyncio.gather(*(worker(task) for task in job.tasks))
        finally:
            if job.cancel_flag:
                job.status = "cancelled"
                job.message = "任务已取消"
            else:
                counts = job.counts()
                if counts["failed"] == 0 and counts["cancelled"] == 0:
                    job.status = "completed"
                    job.message = "全部完成"
                elif counts["success"] == 0 and counts["cancelled"] == 0:
                    job.status = "failed"
                    job.message = "全部失败"
                elif counts["success"] == 0 and counts["failed"] == 0:
                    job.status = "cancelled"
                    job.message = "任务已取消"
                else:
                    job.status = "completed"
                    job.message = (
                        f"部分完成：成功 {counts['success']}，"
                        f"失败 {counts['failed']}，取消 {counts['cancelled']}"
                    )
            job.finished_at = now_iso()
            if self._active_job_id == job.id:
                self._active_job_id = None
            await self._save_jobs()

    async def _run_task(
        self,
        *,
        client: NoovaClient,
        job: BatchJob,
        task: JobTask,
        poll_interval: float,
        poll_timeout: float,
        upload_cache: dict[str, str],
        get_upload_lock,
    ) -> None:
        try:
            if job.cancel_flag:
                task.status = "cancelled"
                task.message = "已取消"
                return

            image_path = Path(task.image_path)
            prompt_dir = Path(task.output_dir)
            prompt_dir.mkdir(parents=True, exist_ok=True)

            task.status = "uploading"
            task.message = "上传参考图中"

            async def ensure_uploaded(local_path: Path, cache_key: str, label: str) -> str:
                if cache_key in upload_cache:
                    task.message = f"复用已上传{label}"
                    return upload_cache[cache_key]
                lock = await get_upload_lock(cache_key)
                async with lock:
                    cached = upload_cache.get(cache_key)
                    if cached:
                        task.message = f"复用已上传{label}"
                        return cached
                    task.message = f"上传{label}"
                    uploaded = await client.upload_local_file(local_path)
                    upload_cache[cache_key] = uploaded
                    return uploaded

            # 固定图优先：图一、图二，再是文件夹当前图
            ordered_urls: list[str] = []
            if task.extra_image_1:
                ordered_urls.append(
                    await ensure_uploaded(Path(task.extra_image_1), task.extra_image_1, "图一")
                )
            if job.cancel_flag:
                task.status = "cancelled"
                task.message = "已取消"
                return

            if task.extra_image_2:
                ordered_urls.append(
                    await ensure_uploaded(Path(task.extra_image_2), task.extra_image_2, "图二")
                )
            if job.cancel_flag:
                task.status = "cancelled"
                task.message = "已取消"
                return

            main_url = await ensure_uploaded(image_path, task.image_path, "源图")
            ordered_urls.append(main_url)

            if job.cancel_flag:
                task.status = "cancelled"
                task.message = "已取消"
                return

            task.status = "submitting"
            task.message = "提交生成任务"
            remote_id = await client.create_draw_task(
                model=job.model,
                prompt=task.prompt,
                aspect_ratio=job.aspect_ratio,
                quality=job.quality,
                urls=ordered_urls,
            )
            task.remote_task_id = remote_id

            task.status = "processing"
            task.message = "等待出图结果"
            result_url = await self._poll_result(
                client=client,
                task=task,
                job=job,
                poll_interval=poll_interval,
                poll_timeout=poll_timeout,
            )
            if task.status == "cancelled":
                return

            task.status = "downloading"
            task.message = "下载生成图"
            suffix = self._guess_suffix(result_url, image_path.suffix or ".png")
            output_path = unique_output_path(prompt_dir, image_path.stem, suffix)
            await client.download_file(result_url, output_path)

            task.output_path = str(output_path.resolve())
            task.result_url = result_url
            task.status = "success"
            task.message = "完成"
        except Exception as exc:  # noqa: BLE001
            if job.cancel_flag and task.status != "success":
                task.status = "cancelled"
                task.message = "已取消"
            else:
                task.status = "failed"
                task.message = str(exc)

    async def _poll_result(
        self,
        *,
        client: NoovaClient,
        task: JobTask,
        job: BatchJob,
        poll_interval: float,
        poll_timeout: float,
    ) -> str:
        if not task.remote_task_id:
            raise NoovaApiError("缺少远程任务 ID")

        elapsed = 0.0
        while elapsed <= poll_timeout:
            if job.cancel_flag:
                task.status = "cancelled"
                task.message = "已取消"
                return ""

            result = await client.query_draw_result(task.remote_task_id)
            status = client.extract_status(result)
            urls = client.extract_result_urls(result)

            if client.is_failed_status(status):
                message = (
                    result.get("message")
                    or result.get("error")
                    or result.get("msg")
                    or f"远程任务失败: {status}"
                )
                raise NoovaApiError(str(message), payload=result)

            # only download on explicit success status + url
            if urls and client.is_success_status(status):
                return urls[0]

            # some APIs omit status but return final payload fields
            if urls and status in {"", "unknown"} and (
                result.get("completed") is True or result.get("done") is True
            ):
                return urls[0]

            task.message = f"处理中 ({status or 'processing'})"
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise NoovaApiError(f"等待结果超时（{int(poll_timeout)} 秒）")

    @staticmethod
    def _guess_suffix(url: str, fallback: str) -> str:
        clean = url.split("?", 1)[0]
        suffix = Path(clean).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}:
            return suffix
        if fallback.lower() in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}:
            return fallback.lower()
        return ".png"


job_manager = JobManager()
