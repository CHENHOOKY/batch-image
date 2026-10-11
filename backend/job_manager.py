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

from .api_client import NoovaApiError, NoovaClient, clip
from .config import (
    DEFAULT_MODEL_ID,
    JOBS_PATH,
    SCHEDULES_PATH,
    assert_safe_directory,
    assert_safe_file,
    ensure_dirs,
    get_model_poll_param,
    get_model_poll_endpoint,
    get_model_poll_timeout_sec,
    load_settings,
)

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
ScheduleStatus = Literal["pending", "waiting", "submitting", "submitted", "cancelled", "failed"]


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
    auto_retry_failed: bool = False
    max_retry_count: int = 0
    retry_attempt_count: int = 0
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

    def to_summary(self) -> dict[str, Any]:
        """不含子任务的概要。列表接口用它，避免把几万条子任务全量吐给前端。"""
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
            # 必须保留：to_dict 基于 to_summary，漏掉它会导致重启后
            # from_dict 拿不到轮询间隔而回落到 3 秒，重试时把上游请求量放大数倍
            "poll_interval_sec": self.poll_interval_sec,
            "auto_retry_failed": self.auto_retry_failed,
            "max_retry_count": self.max_retry_count,
            "retry_attempt_count": self.retry_attempt_count,
            "total": c["total"],
            "success": c["success"],
            "failed": c["failed"],
            "cancelled": c["cancelled"],
            "pending": c["pending"],
            "message": self.message,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self.to_summary(), "tasks": [t.to_dict() for t in self.tasks]}

    def state_signature(self) -> str:
        """任务当前状态的廉价指纹。

        前端每 2 秒轮询一次详情，而绝大多数轮询之间状态没有任何变化。
        用它做条件返回：指纹没变就只回一个极小的响应，省掉几 MB 的
        序列化和传输（大批量任务时这是主要开销）。
        """
        digest = hashlib.blake2b(digest_size=16)
        digest.update(
            f"{self.status}\x1f{self.message}\x1f{self.retry_attempt_count}\x1f{self.max_retry_count}".encode("utf-8")
        )
        for task in self.tasks:
            digest.update(
                "\x1f".join(
                    (
                        task.id,
                        task.status,
                        task.message,
                        task.output_path or "",
                        task.result_url or "",
                    )
                ).encode("utf-8")
            )
            digest.update(b"\x1e")
        return digest.hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BatchJob":
        tasks_data = data.get("tasks") or []
        tasks = [JobTask.from_dict(t) for t in tasks_data if isinstance(t, dict)]
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            source_dir=str(data.get("source_dir") or ""),
            output_dir=str(data.get("output_dir") or ""),
            model=str(data.get("model") or DEFAULT_MODEL_ID),
            aspect_ratio=str(data.get("aspect_ratio") or "1:1"),
            quality=str(data.get("quality") or "auto"),
            image_size=str(data.get("image_size") or ""),
            concurrency=int(data.get("concurrency") or 2),
            poll_interval_sec=float(data.get("poll_interval_sec") or 3),
            auto_retry_failed=bool(data.get("auto_retry_failed") or False),
            max_retry_count=max(0, min(int(data.get("max_retry_count") or 0), 10)),
            retry_attempt_count=max(0, int(data.get("retry_attempt_count") or 0)),
            tasks=tasks,
            status=data.get("status") or "queued",
            message=str(data.get("message") or ""),
            created_at=str(data.get("created_at") or now_str()),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            cancel_flag=bool(data.get("cancel_flag") or False),
        )


def parse_schedule_time(value: str) -> datetime:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError("请提供定时运行时间")
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError as exc:
        raise ValueError("定时运行时间格式不正确") from exc
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone().replace(tzinfo=None)
    return parsed.replace(microsecond=0)


def format_schedule_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class ScheduledJob:
    id: str
    run_at: str
    payload: dict[str, Any]
    status: ScheduleStatus = "pending"
    message: str = ""
    job_id: str | None = None
    created_at: str = field(default_factory=now_str)
    updated_at: str = field(default_factory=now_str)

    def run_datetime(self) -> datetime:
        return parse_schedule_time(self.run_at)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_at": self.run_at,
            "payload": self.payload,
            "status": self.status,
            "message": self.message,
            "job_id": self.job_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ScheduledJob":
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:12]),
            run_at=str(data.get("run_at") or ""),
            payload=data.get("payload") if isinstance(data.get("payload"), dict) else {},
            status=data.get("status") or "pending",
            message=str(data.get("message") or ""),
            job_id=data.get("job_id"),
            created_at=str(data.get("created_at") or now_str()),
            updated_at=str(data.get("updated_at") or now_str()),
        )


class JobManager:
    _SAVE_INTERVAL = 2.0
    #: 只保留最近这么多条历史任务，防止 jobs.json 无限增长
    MAX_PERSISTED_JOBS = 30
    MAX_PERSISTED_SCHEDULES = 30

    def __init__(self) -> None:
        self._jobs: dict[str, BatchJob] = {}
        self._schedules: dict[str, ScheduledJob] = {}
        self._lock = asyncio.Lock()
        self._schedule_lock = asyncio.Lock()
        self._persist_lock = asyncio.Lock()
        self._schedule_persist_lock = asyncio.Lock()
        self._active_job_id: str | None = None
        self._schedule_task: asyncio.Task[None] | None = None
        self._schedule_event = asyncio.Event()
        self._last_save_time: float = 0.0
        self._persist_error: str = ""
        self._load_jobs()
        self._load_schedules()

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

    def _load_schedules(self) -> None:
        ensure_dirs()
        if not SCHEDULES_PATH.exists():
            return
        try:
            raw = json.loads(SCHEDULES_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        schedules_data = raw.get("schedules") if isinstance(raw, dict) else raw
        if not isinstance(schedules_data, list):
            return

        for item in schedules_data:
            if not isinstance(item, dict):
                continue
            schedule = ScheduledJob.from_dict(item)
            if schedule.status in {"waiting", "submitting"}:
                schedule.status = "pending"
                schedule.message = "服务重启，等待重新调度"
                schedule.updated_at = now_str()
            self._schedules[schedule.id] = schedule

    def start_scheduler(self) -> None:
        if self._schedule_task is None or self._schedule_task.done():
            self._schedule_task = asyncio.create_task(self._scheduler_loop())

    async def stop_scheduler(self) -> None:
        if self._schedule_task and not self._schedule_task.done():
            self._schedule_task.cancel()
            try:
                await self._schedule_task
            except asyncio.CancelledError:
                pass
        self._schedule_task = None

    def _prune_jobs(self) -> None:
        """只保留最近 MAX_PERSISTED_JOBS 条，且绝不淘汰正在跑的任务。"""
        if len(self._jobs) <= self.MAX_PERSISTED_JOBS:
            return
        ordered = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        keep: list[str] = []
        for job in ordered:
            if job.status in {"queued", "running"} or len(keep) < self.MAX_PERSISTED_JOBS:
                keep.append(job.id)
        keep_set = set(keep)
        for job_id in [j for j in self._jobs if j not in keep_set]:
            self._jobs.pop(job_id, None)

    def _prune_schedules(self) -> None:
        if len(self._schedules) <= self.MAX_PERSISTED_SCHEDULES:
            return
        ordered = sorted(self._schedules.values(), key=lambda s: s.created_at, reverse=True)
        keep: list[str] = []
        for schedule in ordered:
            if schedule.status in {"pending", "waiting", "submitting"} or len(keep) < self.MAX_PERSISTED_SCHEDULES:
                keep.append(schedule.id)
        keep_set = set(keep)
        for schedule_id in [s for s in self._schedules if s not in keep_set]:
            self._schedules.pop(schedule_id, None)

    @staticmethod
    def _write_jobs_file(text: str) -> None:
        """同步写盘，由 to_thread 调用，不阻塞事件循环。"""
        tmp = JOBS_PATH.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(JOBS_PATH)

    async def _save_jobs(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_save_time < self._SAVE_INTERVAL:
            return
        self._last_save_time = now
        ensure_dirs()
        self._prune_jobs()
        payload = {
            "updated_at": now_str(),
            "jobs": [job.to_dict() | {"cancel_flag": job.cancel_flag} for job in self._jobs.values()],
        }
        async with self._persist_lock:
            # 写盘放到线程里（真正的阻塞 IO）。json.dumps 持有 GIL，放线程里
            # 并不能真正让出事件循环，控制体积的是上面的历史条数上限和紧凑格式。
            # 写盘失败不能让异常冒泡：它会穿过 worker -> gather，导致任务被误判
            # 为已结束，而其余 worker 仍在后台继续消耗额度。
            try:
                text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                await asyncio.to_thread(self._write_jobs_file, text)
                self._persist_error = ""
            except (OSError, TypeError, ValueError) as exc:
                self._persist_error = clip(exc)
                print(f"[批量出图] 任务记录写盘失败（不影响本次运行）: {self._persist_error}")

    @staticmethod
    def _write_schedules_file(text: str) -> None:
        tmp = SCHEDULES_PATH.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(SCHEDULES_PATH)

    async def _save_schedules(self) -> None:
        ensure_dirs()
        self._prune_schedules()
        payload = {
            "updated_at": now_str(),
            "schedules": [schedule.to_dict() for schedule in self._schedules.values()],
        }
        async with self._schedule_persist_lock:
            try:
                text = json.dumps(payload, ensure_ascii=False, indent=2)
                await asyncio.to_thread(self._write_schedules_file, text)
            except (OSError, TypeError, ValueError) as exc:
                print(f"[批量出图] 定时计划写盘失败: {clip(exc)}")

    def list_jobs(self) -> list[dict[str, Any]]:
        """只返回概要。前端拿列表只为了知道最近任务的 id，不需要子任务明细。"""
        jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return [j.to_summary() for j in jobs]

    def list_schedules(self) -> list[dict[str, Any]]:
        schedules = sorted(self._schedules.values(), key=lambda s: s.created_at, reverse=True)
        return [schedule.to_dict() for schedule in schedules]

    async def create_schedule(self, *, run_at: str, payload: dict[str, Any]) -> ScheduledJob:
        run_dt = parse_schedule_time(run_at)
        if run_dt <= datetime.now():
            raise ValueError("定时运行时间必须晚于当前时间")

        schedule = ScheduledJob(
            id=uuid.uuid4().hex[:12],
            run_at=format_schedule_time(run_dt),
            payload=payload,
            status="pending",
            message="等待定时运行",
        )

        async with self._schedule_lock:
            for existing in self._schedules.values():
                if existing.status in {"pending", "waiting"}:
                    existing.status = "cancelled"
                    existing.message = "已被新的定时计划替换"
                    existing.updated_at = now_str()
            self._schedules[schedule.id] = schedule

        await self._save_schedules()
        self._schedule_event.set()
        return schedule

    async def cancel_schedule(self, schedule_id: str) -> ScheduledJob:
        schedule = self._schedules.get(schedule_id)
        if not schedule:
            raise ValueError("定时计划不存在")
        if schedule.status not in {"pending", "waiting"}:
            raise ValueError("该定时计划已不能取消")
        schedule.status = "cancelled"
        schedule.message = "已取消"
        schedule.updated_at = now_str()
        await self._save_schedules()
        self._schedule_event.set()
        return schedule

    def _scheduler_delay(self) -> float:
        now = datetime.now()
        delays: list[float] = []
        for schedule in self._schedules.values():
            if schedule.status not in {"pending", "waiting"}:
                continue
            try:
                run_dt = schedule.run_datetime()
            except ValueError:
                continue
            delays.append(max(0.0, (run_dt - now).total_seconds()))
        if not delays:
            return 30.0
        return min(max(min(delays), 0.5), 30.0)

    async def _scheduler_loop(self) -> None:
        while True:
            try:
                await self._run_due_schedules()
                delay = self._scheduler_delay()
                try:
                    await asyncio.wait_for(self._schedule_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
                self._schedule_event.clear()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                print(f"[批量出图] 定时调度器异常: {clip(exc)}")
                await asyncio.sleep(5)

    async def _run_due_schedules(self) -> None:
        now = datetime.now()
        candidates = sorted(
            [s for s in self._schedules.values() if s.status in {"pending", "waiting"}],
            key=lambda s: s.run_at,
        )

        for schedule in candidates:
            try:
                run_dt = schedule.run_datetime()
            except ValueError as exc:
                schedule.status = "failed"
                schedule.message = str(exc)
                schedule.updated_at = now_str()
                await self._save_schedules()
                continue

            if run_dt > now:
                if schedule.status != "pending":
                    schedule.status = "pending"
                    schedule.message = "等待定时运行"
                    schedule.updated_at = now_str()
                    await self._save_schedules()
                continue

            active = self.has_active_job()
            if active:
                message = f"到点，等待当前任务 {active.id[:8]} 完成"
                if schedule.status != "waiting" or schedule.message != message:
                    schedule.status = "waiting"
                    schedule.message = message
                    schedule.updated_at = now_str()
                    await self._save_schedules()
                continue

            schedule.status = "submitting"
            schedule.message = "正在提交定时任务"
            schedule.updated_at = now_str()
            await self._save_schedules()

            try:
                job = await self.create_job(dict(schedule.payload))
            except ValueError as exc:
                if "已有任务正在运行" in str(exc):
                    schedule.status = "waiting"
                    schedule.message = str(exc)
                else:
                    schedule.status = "failed"
                    schedule.message = str(exc)
                schedule.updated_at = now_str()
                await self._save_schedules()
                continue
            except Exception as exc:  # noqa: BLE001
                schedule.status = "failed"
                schedule.message = f"提交失败: {clip(exc)}"
                schedule.updated_at = now_str()
                await self._save_schedules()
                continue

            schedule.status = "submitted"
            schedule.job_id = job.id
            schedule.message = f"已提交任务：{job.id}"
            schedule.updated_at = now_str()
            await self._save_schedules()

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
        model = payload.get("model") or settings.get("model") or DEFAULT_MODEL_ID
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
        auto_retry_failed = bool(
            payload.get("auto_retry_failed")
            if payload.get("auto_retry_failed") is not None
            else settings.get("auto_retry_failed")
        )
        max_retry_count = int(
            payload.get("max_retry_count")
            if payload.get("max_retry_count") is not None
            else (settings.get("max_retry_count") or 0)
        )
        max_retry_count = max(0, min(max_retry_count, 10))
        if not auto_retry_failed:
            max_retry_count = 0

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

            source_dir = assert_safe_directory(Path(source_raw), field_name="源图片文件夹")
            images = list_images(source_dir)

            if output_raw:
                output_dir = assert_safe_directory(Path(output_raw), field_name="输出文件夹")
            else:
                if not global_output:
                    raise ValueError(f"提示词「{prompt_name}」未指定输出文件夹，且全局输出文件夹也为空")
                output_dir = assert_safe_directory(
                    Path(global_output), field_name="输出文件夹"
                ) / prompt_name

            output_dir.mkdir(parents=True, exist_ok=True)
            used_sources.append(str(source_dir.resolve()))
            used_outputs.append(str(output_dir.resolve()))

            extra_image_1 = ""
            extra_image_2 = ""
            if extra_1_raw:
                extra_image_1 = str(
                    assert_safe_file(Path(extra_1_raw), field_name=f"提示词「{prompt_name}」的图一")
                )
            if extra_2_raw:
                extra_image_2 = str(
                    assert_safe_file(Path(extra_2_raw), field_name=f"提示词「{prompt_name}」的图二")
                )

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
            auto_retry_failed=auto_retry_failed,
            max_retry_count=max_retry_count,
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

    async def retry_failed(self, job_id: str) -> BatchJob:
        """Reset failed/cancelled tasks of a finished job and re-run it."""
        job = self._jobs.get(job_id)
        if not job:
            raise ValueError("任务不存在")
        if job.status in {"queued", "running"}:
            raise ValueError("任务正在运行中，无法重试")
        active = self.has_active_job()
        if active:
            raise ValueError(f"已有任务正在运行：{active.id}，请等待完成或先取消")

        retry_tasks = [t for t in job.tasks if t.status in {"failed", "cancelled"}]
        if not retry_tasks:
            raise ValueError("没有失败或已取消的子任务需要重试")

        for task in retry_tasks:
            task.status = "pending"
            task.message = "等待重试"
            task.remote_task_id = None
            task.output_path = None
            task.result_url = None

        job.cancel_flag = False
        job.status = "queued"
        job.message = f"正在重试 {len(retry_tasks)} 个子任务"
        job.finished_at = None
        job.retry_attempt_count = 0

        async with self._lock:
            self._active_job_id = job.id

        await self._save_jobs(force=True)
        asyncio.create_task(self._run_job(job.id))
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
        job.started_at = job.started_at or now_str()
        job.finished_at = None
        job.message = self._running_message(job)
        await self._save_jobs(force=True)

        try:
            client = NoovaClient(
                api_key=str(settings.get("api_key") or ""),
                base_url=str(settings.get("base_url") or "https://noova.vip"),
                image_proxy_url=str(settings.get("image_proxy_url") or ""),
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
            self._schedule_event.set()
            await self._save_jobs(force=True)
            return

        run_error: Exception | None = None
        try:
            async with client:
                while True:
                    await self._run_job_round(job=job, client=client, settings=settings)
                    if job.cancel_flag:
                        break

                    counts = job.counts()
                    if (
                        job.auto_retry_failed
                        and counts["failed"] > 0
                        and job.retry_attempt_count < job.max_retry_count
                    ):
                        retry_count = self._reset_failed_tasks_for_retry(job)
                        if retry_count <= 0:
                            break
                        job.retry_attempt_count += 1
                        job.status = "running"
                        job.finished_at = None
                        job.message = (
                            f"自动重试第 {job.retry_attempt_count}/{job.max_retry_count} 轮，"
                            f"重新提交 {retry_count} 个失败子任务"
                        )
                        await self._save_jobs(force=True)
                        continue

                    break
        except Exception as exc:  # noqa: BLE001
            run_error = exc
            for task in job.tasks:
                if task.status in {"pending", "uploading", "submitting", "processing", "downloading"}:
                    task.status = "failed"
                    task.message = "任务执行异常：" + clip(exc)
        finally:
            if job.cancel_flag:
                job.status = "cancelled"
                job.message = "任务已取消"
            elif run_error is not None:
                job.status = "failed"
                job.message = "任务执行异常：" + clip(run_error)
            else:
                counts = job.counts()
                if counts["failed"] == 0 and counts["cancelled"] == 0:
                    job.status = "completed"
                    job.message = "全部完成"
                    if job.auto_retry_failed and job.retry_attempt_count:
                        job.message += f"（自动重试 {job.retry_attempt_count} 轮）"
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
                if (
                    job.auto_retry_failed
                    and counts["failed"] > 0
                    and job.retry_attempt_count >= job.max_retry_count
                    and job.max_retry_count > 0
                ):
                    job.message += f"（已达到最大重试次数 {job.max_retry_count}）"

            job.finished_at = now_str()
            if self._active_job_id == job.id:
                self._active_job_id = None
            self._schedule_event.set()
            await self._save_jobs(force=True)

    @staticmethod
    def _running_message(job: BatchJob) -> str:
        if job.auto_retry_failed and job.max_retry_count > 0:
            return f"任务执行中（失败自动重试最多 {job.max_retry_count} 轮）"
        return "任务执行中"

    @staticmethod
    def _reset_failed_tasks_for_retry(job: BatchJob) -> int:
        retry_tasks = [task for task in job.tasks if task.status == "failed"]
        for task in retry_tasks:
            task.status = "pending"
            task.message = "等待自动重试"
            task.remote_task_id = None
            task.output_path = None
            task.result_url = None
        return len(retry_tasks)

    async def _run_job_round(
        self,
        *,
        job: BatchJob,
        client: NoovaClient,
        settings: dict[str, Any],
    ) -> None:
        poll_interval = float(job.poll_interval_sec or settings.get("poll_interval_sec") or 3)
        configured_timeout = float(settings.get("poll_timeout_sec") or 300)
        model_timeout = get_model_poll_timeout_sec(job.model)
        poll_timeout = max(configured_timeout, float(model_timeout or 0))
        semaphore = asyncio.Semaphore(job.concurrency)
        image_cache: dict[str, str] = {}
        image_upload_tasks: dict[str, asyncio.Task[str]] = {}
        cache_lock = asyncio.Lock()

        # Pre-upload fixed reference images via STS direct upload (once per unique image)
        fixed_url_lookup: dict[str, str] = {}
        for task in job.tasks:
            # Do not touch tasks that already finished (e.g. on retry runs)
            if task.status in {"success", "failed", "cancelled"}:
                continue
            for raw_path in (task.extra_image_1, task.extra_image_2):
                if raw_path and raw_path not in fixed_url_lookup:
                    try:
                        task.status = "uploading"
                        task.message = "正在上传固定参考图：" + Path(raw_path).name
                        url = await client.upload_image_direct(Path(raw_path))
                        fixed_url_lookup[raw_path] = url
                        task.status = "pending"
                        task.message = "固定参考图已上传"
                    except Exception as exc:
                        # Mark all unfinished tasks that use this fixed image as failed
                        for t in job.tasks:
                            if t.status == "success":
                                continue
                            if t.extra_image_1 == raw_path or t.extra_image_2 == raw_path:
                                t.status = "failed"
                                t.message = "固定参考图上传失败：" + clip(exc)
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
                # Skip tasks already marked terminal (e.g. fixed-image upload failed)
                if task.status in {"failed", "cancelled", "success"}:
                    return
                await self._run_task(
                    client=client,
                    job=job,
                    task=task,
                    poll_interval=poll_interval,
                    poll_timeout=poll_timeout,
                    image_cache=image_cache,
                    image_upload_tasks=image_upload_tasks,
                    cache_lock=cache_lock,
                    fixed_url_lookup=fixed_url_lookup,
                )
                await self._save_jobs()

        await asyncio.gather(*(worker(task) for task in job.tasks))
        image_cache.clear()
        image_upload_tasks.clear()
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
        image_upload_tasks: dict[str, asyncio.Task[str]],
        cache_lock: asyncio.Lock,
        fixed_url_lookup: dict[str, str],
    ) -> None:
        # Same logic as folder_batch_draw.py _process_image()
        try:
            if job.cancel_flag:
                task.status = "cancelled"
                task.message = "已取消"
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
            task.message = "正在上传：" + fname
            cache_key = str(image_path)

            async with cache_lock:
                source_url = image_cache.get(cache_key)
                if source_url is None:
                    upload_task = image_upload_tasks.get(cache_key)
                    if upload_task is None:
                        upload_task = asyncio.create_task(client.upload_image_direct(image_path))
                        image_upload_tasks[cache_key] = upload_task

            if source_url is None:
                try:
                    source_url = await upload_task
                except Exception:
                    async with cache_lock:
                        if image_upload_tasks.get(cache_key) is upload_task:
                            image_upload_tasks.pop(cache_key, None)
                    raise
                async with cache_lock:
                    image_cache[cache_key] = source_url
                    if image_upload_tasks.get(cache_key) is upload_task:
                        image_upload_tasks.pop(cache_key, None)

            if job.cancel_flag:
                task.status = "cancelled"
                task.message = "已取消"
                return

            # Submit: fixed refs first, then source
            task.status = "submitting"
            task.message = "正在提交：" + fname
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
            task.message = "正在生成（0%）"

            async def cancel_check():
                return job.cancel_flag

            result_data = await client.poll_task_result(
                task_id=remote_id,
                poll_interval=int(poll_interval),
                cancel_check=cancel_check,
                poll_timeout=poll_timeout,
                poll_param=get_model_poll_param(job.model),
                poll_endpoint=get_model_poll_endpoint(job.model),
            )

            if job.cancel_flag:
                task.status = "cancelled"
                task.message = "已取消"
                return

            status = client.get_status(result_data)
            progress = client.get_progress(result_data)
            task.message = "生成状态：" + status + "（" + str(progress) + "%）"

            if status in {"succeeded", "completed"}:
                final_url = client.get_succeeded_url(result_data)
                if final_url:
                    task.status = "downloading"
                    task.message = "正在下载结果"
                    suffix = self._guess_suffix(final_url, image_path.suffix or ".png")
                    output_path = unique_output_path(prompt_dir, image_path.stem, suffix)
                    await client.download_file(final_url, output_path)
                    task.output_path = str(output_path.resolve())
                    task.result_url = final_url
                    task.status = "success"
                    task.message = "处理完成"
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
                task.message = "已取消"
            else:
                task.status = "failed"
                task.message = clip(exc)

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
