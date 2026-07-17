from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SettingsUpdate(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    aspect_ratio: str | None = None
    quality: str | None = None
    image_size: str | None = None
    concurrency: int | None = Field(default=None, ge=1, le=10)
    poll_interval_sec: float | None = Field(default=None, ge=1, le=60)
    poll_timeout_sec: int | None = Field(default=None, ge=30, le=3600)
    source_dir: str | None = None
    output_dir: str | None = None
    image_proxy_url: str | None = None


class PromptItem(BaseModel):
    prompt: str = Field(..., min_length=1)
    enabled: bool = True
    source_dir: str | None = None
    output_dir: str | None = None
    image_proxy_url: str | None = None
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
    image_proxy_url: str | None = None
    model: str | None = None
    aspect_ratio: str | None = None
    quality: str | None = None
    image_size: str | None = None
    concurrency: int | None = Field(default=None, ge=1, le=10)
    poll_interval_sec: float | None = Field(default=None, ge=1, le=60)
    prompts: list[PromptItem] = Field(..., min_length=1, max_length=10)
    image_extensions: list[str] = Field(
        default_factory=lambda: [".jpg", ".jpeg", ".png", ".webp", ".bmp"]
    )


class ApiResponse(BaseModel):
    ok: bool = True
    message: str = ""
    data: Any = None
