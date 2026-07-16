from __future__ import annotations

import asyncio
import hashlib
import json
import time
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


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def unique_output_path(directory: Path, stem: str, suffix: str) -> Path:
    candidate = directory / f"{stem}{suffix}"
    if not candidate.exists():
        return candidate
    for index in range(2, 10000):
        candidate = directory / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"输出文件名冲突过多: {stem}{suffix}")


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
    image_size: str = ""
    poll_interval_sec: float = 3.0
    tasks: list[JobTask] = field(default_factory=list)
    status: JobStatus = "queued"
    message: str = ""
    created_at: str = field(default_factory=now_str)
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
            "image_size": self.image_size,
            "concurrency": self.concurrency,
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
        tasks_data = data.get("tasks") or []
        tasks = [JobTask.from_dict(t) for t in tasks_data if isinstance(t, dict)]
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            source_dir=str(data.get("source_dir") or ""),
            output_dir=str(data.get("output_dir") or ""),
            model=str(data.get("model") or "gpt-image-2"),
            aspect_ratio=str(data.get("aspect_ratio") or "1:1"),
            quality=str(data.get("quality") or "auto"),
            image_size=str(data.get("image_size") or ""),
            concurrency=int(data.get("concurrency") or 2),
            poll_interval_sec=float(data.get("poll_interval_sec") or 3),
            tasks=tasks,
            status=data.get("status") or "queued",
            message=str(data.get("message") or ""),
            created_at=str(data.get("created_at") or now_str()),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            cancel_flag=bool(data.get("cancel_flag") or False),
        )


class JobManager:
    _SAVE_INTERVAL = 2.0

    def __init__(self) -> None:
        self._jobs: dict[str, BatchJob] = {}
        self._lock = asyncio.Lock()
        self._persist_lock = asyncio.Lock()
        self._active_job_id: str | None = None
        self._last_save_time: float = 0.0
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
                job.finished_at = job.finished_at or now_str()
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

    async def _save_jobs(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_save_time < self._SAVE_INTERVAL:
            return
        self._last_save_time = now
        ensure_dirs()
        payload = {
            "updated_at": now_str(),
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
        image_size = payload.get("image_size") or settings.get("image_size") or ""
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
            image_size=str(image_size),
            concurrency=concurrency,
            poll_interval_sec=poll_interval_sec,
            tasks=job_tasks,
        )

        async with self._lock:
            self._jobs[job.id] = job
            self._active_job_id = job.id

        await self._save_jobs(force=True)
        asyncio.create_task(self._run_job(job.id))
        return job

    async def cancel_job(self, job_id: str) -> BatchJob:
        job = self._jobs.get(job_id)
        if not job:
            raise ValueError("任务不存在")
        job.cancel_flag = True
        if job.status in {"queued", "running"}:
            job.message = "正在取消..."
        await self._save_jobs(force=True)
        return job

    async def delete_job(self, job_id: str) -> BatchJob:
        job = self._jobs.get(job_id)
        if not job:
            raise ValueError("任务不存在")
        if job.status in {"queued", "running"}:
            raise ValueError("任务正在运行，请先取消再删除")
        del self._jobs[job_id]
        if self._active_job_id == job_id:
            self._active_job_id = None
        await self._save_jobs(force=True)
        return job

    async def _run_job(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if not job:
            return

        settings = load_settings()
        job.status = "running"
        job.started_at = now_str()
        job.message = "任务执行中"
        await self._save_jobs(force=True)

        try:
            client = NoovaClient(
                api_key=str(settings.get("api_key") or ""),
                base_url=str(settings.get("base_url") or "https://noova.cn"),
            )
        except NoovaApiError as exc:
            job.status = "failed"
            job.message = str(exc)
            job.finished_at = now_str()
            for task in job.tasks:
                if task.status == "pending":
                    task.status = "failed"
                    task.message = str(exc)
            if self._active_job_id == job.id:
                self._active_job_id = None
            await self._save_jobs(force=True)
            return

        poll_interval = float(job.poll_interval_sec or settings.get("poll_interval_sec") or 3)
        poll_timeout = float(settings.get("poll_timeout_sec") or 300)
        semaphore = asyncio.Semaphore(job.concurrency)
        image_cache: dict[str, str] = {}

        # Pre-upload fixed reference images via STS direct upload (once per unique image)
        fixed_url_lookup: dict[str, str] = {}
        for task in job.tasks:
            for raw_path in (task.extra_image_1, task.extra_image_2):
                if raw_path and raw_path not in fixed_url_lookup:
                    try:
                        task.status = "uploading"
                        task.message = "upload fixed: " + Path(raw_path).name
                        url = await client.upload_image_direct(Path(raw_path))
                        fixed_url_lookup[raw_path] = url
                    except Exception as exc:
                        # Mark all tasks that use this fixed image as failed
                        for t in job.tasks:
                            if t.extra_image_1 == raw_path or t.extra_image_2 == raw_path:
                                t.status = "failed"
                                t.message = "failed to upload fixed image: " + str(exc)
                        await self._save_jobs()
                        # Continue without this fixed image
        await self._save_jobs()

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
                    image_cache=image_cache,
                    fixed_url_lookup=fixed_url_lookup,
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
            job.finished_at = now_str()
            if self._active_job_id == job.id:
                self._active_job_id = None
            image_cache.clear()
            await self._save_jobs(force=True)

    async def _run_task(
        self,
        *,
        client: NoovaClient,
        job: BatchJob,
        task: JobTask,
        poll_interval: float,
        poll_timeout: float,
        image_cache: dict[str, str],
        fixed_url_lookup: dict[str, str],
    ) -> None:
        # Same logic as folder_batch_draw.py _process_image()
        try:
            if job.cancel_flag:
                task.status = "cancelled"
                task.message = "canceled"
                return

            image_path = Path(task.image_path)
            prompt_dir = Path(task.output_dir)
            prompt_dir.mkdir(parents=True, exist_ok=True)
            fname = task.image_name

            # Build fixed_url_list from pre-uploaded lookup
            fixed_url_list: list[str] = []
            for raw_path in (task.extra_image_1, task.extra_image_2):
                if raw_path and raw_path in fixed_url_lookup:
                    fixed_url_list.append(fixed_url_lookup[raw_path])

            # Upload source image via STS direct upload (cached per unique image path)
            task.status = "uploading"
            task.message = "upload: " + fname
            cache_key = str(image_path)
            source_url = image_cache.get(cache_key)
            if source_url is None:
                source_url = await client.upload_image_direct(image_path)
                image_cache[cache_key] = source_url

            if job.cancel_flag:
                task.status = "cancelled"
                task.message = "canceled"
                return

            # Submit: fixed refs first, then source
            task.status = "submitting"
            task.message = "submit: " + fname
            all_urls = fixed_url_list + [source_url]
            remote_id = await client.create_draw_task(
                model=job.model,
                prompt=task.prompt,
                aspect_ratio=job.aspect_ratio,
                quality=job.quality,
                image_size=job.image_size or "1K",
                urls=all_urls,
            )
            task.remote_task_id = remote_id

            # Poll for result
            task.status = "processing"
            task.message = "polling (0%)"

            async def cancel_check():
                return job.cancel_flag

            result_data = await client.poll_task_result(
                task_id=remote_id,
                poll_interval=int(poll_interval),
                cancel_check=cancel_check,
            )

            if job.cancel_flag:
                task.status = "cancelled"
                task.message = "canceled"
                return

            status = client.get_status(result_data)
            progress = client.get_progress(result_data)
            task.message = "status: " + status + " (" + str(progress) + "%)"

            if status == "succeeded":
                final_url = client.get_succeeded_url(result_data)
                if final_url:
                    task.status = "downloading"
                    task.message = "downloading"
                    suffix = self._guess_suffix(final_url, image_path.suffix or ".png")
                    output_path = unique_output_path(prompt_dir, image_path.stem, suffix)
                    await client.download_file(final_url, output_path)
                    task.output_path = str(output_path.resolve())
                    task.result_url = final_url
                    task.status = "success"
                    task.message = "done"
                else:
                    task.status = "failed"
                    task.message = "API succeeded but no image URL"
            else:
                err = (
                    result_data.get("error")
                    or result_data.get("failure_reason")
                    or status
                )
                task.status = "failed"
                task.message = str(err)
        except Exception as exc:
            if job.cancel_flag and task.status != "success":
                task.status = "cancelled"
                task.message = "canceled"
            else:
                task.status = "failed"
                task.message = str(exc)

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
