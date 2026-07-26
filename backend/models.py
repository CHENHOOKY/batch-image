from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .config import get_model_info, validate_external_http_url


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

    @field_validator("base_url", "image_proxy_url")
    @classmethod
    def validate_http_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        return validate_external_http_url(value, field_name="URL")

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        model_id = value.strip()
        info = get_model_info(model_id)
        if info.get("id") != model_id:
            raise ValueError("不支持的模型")
        return model_id

    @field_validator("aspect_ratio")
    @classmethod
    def validate_aspect_ratio(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        cleaned = value.strip()
        allowed = {
            "auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3",
            "5:4", "4:5", "21:9", "9:21", "1:2", "2:1", "1:4", "4:1",
            "1:8", "8:1",
        }
        if cleaned not in allowed:
            raise ValueError("不支持的图片比例")
        return cleaned

    @field_validator("quality")
    @classmethod
    def validate_quality(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        cleaned = value.strip()
        if cleaned not in {"auto", "low", "medium", "high"}:
            raise ValueError("不支持的质量选项")
        return cleaned

    @field_validator("image_size")
    @classmethod
    def validate_image_size(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        cleaned = value.strip()
        if cleaned not in {"1K", "2K", "4K"}:
            raise ValueError("不支持的图像尺寸")
        return cleaned


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

    @field_validator("image_extensions")
    @classmethod
    def validate_image_extensions(cls, values: list[str]) -> list[str]:
        allowed = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
        cleaned = []
        for value in values:
            ext = str(value or "").strip().lower()
            if not ext:
                continue
            if not ext.startswith("."):
                ext = f".{ext}"
            if ext not in allowed:
                raise ValueError("图片扩展名只允许 jpg/jpeg/png/webp/bmp")
            cleaned.append(ext)
        return sorted(set(cleaned)) or sorted(allowed)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        model_id = value.strip()
        info = get_model_info(model_id)
        if info.get("id") != model_id:
            raise ValueError("不支持的模型")
        return model_id

    @field_validator("aspect_ratio")
    @classmethod
    def validate_aspect_ratio(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        cleaned = value.strip()
        allowed = {
            "auto", "1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3",
            "5:4", "4:5", "21:9", "9:21", "1:2", "2:1", "1:4", "4:1",
            "1:8", "8:1",
        }
        if cleaned not in allowed:
            raise ValueError("不支持的图片比例")
        return cleaned

    @field_validator("quality")
    @classmethod
    def validate_quality(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        cleaned = value.strip()
        if cleaned not in {"auto", "low", "medium", "high"}:
            raise ValueError("不支持的质量选项")
        return cleaned

    @field_validator("image_size")
    @classmethod
    def validate_image_size(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return value
        cleaned = value.strip()
        if cleaned not in {"1K", "2K", "4K"}:
            raise ValueError("不支持的图像尺寸")
        return cleaned


class ApiResponse(BaseModel):
    ok: bool = True
    message: str = ""
    data: Any = None
