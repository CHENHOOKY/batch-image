from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SettingsUpdate(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    aspect_ratio: str | None = None
    quality: str | None = None
    concurrency: int | None = Field(default=None, ge=1, le=10)
    poll_interval_sec: float | None = Field(default=None, ge=1, le=60)
    poll_timeout_sec: int | None = Field(default=None, ge=30, le=3600)
    source_dir: str | None = None
    output_dir: str | None = None


class PromptItem(BaseModel):
    prompt: str = Field(..., min_length=1)
    enabled: bool = True
    source_dir: str | None = None
    output_dir: str | None = None
    extra_image_1: str | None = None
    extra_image_2: str | None = None
    # 兼容旧前端字段
    name: str | None = None

    @field_validator("source_dir", "output_dir", "extra_image_1", "extra_image_2")
    @classmethod
    def clean_optional_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class BatchJobCreate(BaseModel):
    source_dir: str | None = None
    output_dir: str | None = None
    model: str | None = None
    aspect_ratio: str | None = None
    quality: str | None = None
    concurrency: int | None = Field(default=None, ge=1, le=10)
    poll_interval_sec: float | None = Field(default=None, ge=1, le=60)
    prompts: list[PromptItem] = Field(..., min_length=1, max_length=10)
    image_extensions: list[str] = Field(
        default_factory=lambda: [".jpg", ".jpeg", ".png", ".webp", ".bmp"]
    )


class JobTaskView(BaseModel):
    id: str
    image_name: str
    prompt_name: str
    prompt: str
    status: Literal[
        "pending",
        "uploading",
        "submitting",
        "processing",
        "downloading",
        "success",
        "failed",
        "cancelled",
    ]
    message: str = ""
    remote_task_id: str | None = None
    output_path: str | None = None
    result_url: str | None = None


class JobView(BaseModel):
    id: str
    status: Literal["queued", "running", "completed", "failed", "cancelled"]
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    source_dir: str
    output_dir: str
    model: str
    aspect_ratio: str
    quality: str
    concurrency: int
    total: int
    success: int
    failed: int
    cancelled: int = 0
    pending: int
    message: str = ""
    tasks: list[JobTaskView] = Field(default_factory=list)


class ApiResponse(BaseModel):
    ok: bool = True
    message: str = ""
    data: Any = None
