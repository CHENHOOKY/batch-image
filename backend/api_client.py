from __future__ import annotations

import base64
import io
import mimetypes
import time as time_module
import uuid
from pathlib import Path
from typing import Any

import httpx

from .config import get_model_endpoint

MAX_IMAGE_DIM = 2048
MAX_POLL_RETRIES = 120


class NoovaApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class NoovaClient:
    def __init__(self, api_key: str, base_url: str = "https://noova.cn", timeout: float = 900.0, image_proxy_url: str = ""):
        if not api_key or not api_key.strip():
            raise NoovaApiError("\u8bf7\u5148\u5728\u8bbe\u7f6e\u4e2d\u586b\u5199 API Key")
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.image_proxy_url = image_proxy_url.strip() if image_proxy_url else ""
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "NoovaClient":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=60.0, read=self.timeout, write=self.timeout, pool=60.0),
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
        )
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        content: bytes | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=60.0, read=self.timeout, write=self.timeout, pool=60.0),
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        try:
            response = await client.request(
                method, url, params=params, json=json_body,
                headers=headers, content=content,
            )
        finally:
            if owns_client:
                await client.aclose()

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                payload = response.json()
            except ValueError:
                payload = response.text
        else:
            payload = response.text

        if response.status_code >= 400:
            detail = payload
            if isinstance(payload, dict):
                detail = payload.get("message") or payload.get("error") or payload
            raise NoovaApiError(
                f"API \u8bf7\u6c42\u5931\u8d25 ({response.status_code}): {detail}",
                status_code=response.status_code,
                payload=payload,
            )
        return payload

    # ---- image preparation / upload ----

    @staticmethod
    def _prepare_image_bytes(file_path: Path) -> tuple[bytes, str]:
        """Read + resize an image, return (bytes, content_type).

        Mirrors the old encode_image_base64 behavior (max 2048px, JPEG q85),
        but returns raw bytes so they can be PUT to object storage directly.
        Falls back to the raw file bytes when PIL is unavailable."""
        if not file_path.exists() or not file_path.is_file():
            raise NoovaApiError(f"\u6587\u4ef6\u4e0d\u5b58\u5728: {file_path}")

        try:
            from PIL import Image
        except ImportError:
            return file_path.read_bytes(), mimetypes.guess_type(file_path.name)[0] or "image/png"

        img = Image.open(file_path)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        w, h = img.size
        if max(w, h) > MAX_IMAGE_DIM:
            ratio = MAX_IMAGE_DIM / max(w, h)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
        # JPEG has no alpha: flatten RGBA -> RGB to avoid save errors
        if img.mode == "RGBA":
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return buf.getvalue(), "image/jpeg"

    def encode_image_base64(self, file_path: Path) -> str:
        """Fallback: encode image as base64 string (no data: prefix)."""
        data, _ = self._prepare_image_bytes(file_path)
        return base64.b64encode(data).decode("utf-8")

    async def upload_image_direct(self, file_path: Path) -> str:
        """Upload an image to the configured image proxy (multipart/form-data)
        and return the public URL.

        The image is resized via _prepare_image_bytes (max 2048px, JPEG q85),
        then POSTed as a `file` field to image_proxy_url. The proxy returns
        {"url": "...", "created": ...}; we return that url.

        Falls back to a data: URL when no image_proxy_url is configured.
        """
        if not self.image_proxy_url:
            return "data:image/jpeg;base64," + self.encode_image_base64(file_path)

        data, content_type = self._prepare_image_bytes(file_path)
        ext = "jpg" if content_type == "image/jpeg" else "png"
        filename = f"{file_path.stem or 'image'}_{uuid.uuid4().hex[:8]}.{ext}"

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=60.0, read=self.timeout, write=self.timeout, pool=60.0),
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        try:
            response = await client.post(
                self.image_proxy_url,
                files={"file": (filename, data, content_type)},
            )
        finally:
            if owns_client:
                await client.aclose()

        if response.status_code >= 400:
            raise NoovaApiError(
                f"\u56fe\u5e8a\u4e0a\u4f20\u5931\u8d25 ({response.status_code}): {response.text[:300]}",
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError:
            raise NoovaApiError(f"\u56fe\u5e8a\u8fd4\u56de\u975e JSON: {response.text[:300]}")
        url = payload.get("url") if isinstance(payload, dict) else None
        if not url:
            raise NoovaApiError(f"\u56fe\u5e8a\u672a\u8fd4\u56de url: {payload}")
        return str(url)

    async def verify_connection(self) -> None:
        """Verify API key and connectivity."""
        await self._request(
            "GET",
            "/v1/client/resource/sts",
            params={
                "filename": "connection-test.png",
                "content_type": "image/png",
                "size": "128",
            },
            headers=self._headers(),
        )

    # ---- create task ----

    async def create_draw_task(
        self,
        *,
        model: str,
        prompt: str,
        aspect_ratio: str = "1:1",
        quality: str = "",
        image_size: str = "1K",
        urls: list[str] | None = None,
    ) -> str:
        """Create a draw task.
        urls: list of image URLs (from upload_image_direct) OR data: URLs/base64.
        All models go to /v1/draw/completions.
        gpt-image-2 gets quality param, nano-banana does not.
        The image field name is model-specific and resolved via
        backend.config.get_model_image_param (all current models use `urls`)."""
        # The image field name is model-specific; all current models use
        # `urls`. See backend.config.get_model_image_param.
        from .config import get_model_image_param
        image_param = get_model_image_param(model)
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "aspectRatio": aspect_ratio,
            "imageSize": image_size,
        }
        if urls:
            body[image_param] = urls
        # gpt-image-2 family supports quality
        if model in ("gpt-image-2", "gpt-image-2-vip"):
            body["quality"] = quality or "auto"

        # Retry up to 5 times for timeout/connection errors
        last_exc = None
        for attempt in range(5):
            try:
                payload = await self._request(
                    "POST",
                    "/v1/draw/completions",
                    json_body=body,
                    headers=self._headers(),
                )
                break
            except (httpx.TimeoutException, httpx.ConnectError, ConnectionResetError) as exc:
                last_exc = exc
                if attempt < 4:
                    import asyncio
                    await asyncio.sleep(5 * (attempt + 1))
                continue
        else:
            raise NoovaApiError(f"\u521b\u5efa\u4efb\u52a1\u5931\u8d25\uff08\u5df2\u91cd\u8bd95 5 \u6b21\uff09: {last_exc}")

        data = self._unwrap(payload)
        task_id: str | None = None
        if isinstance(data, dict):
            task_id = data.get("id")
        if not task_id:
            # Try response level
            if isinstance(payload, dict):
                task_id = payload.get("id")
        if not task_id:
            raise NoovaApiError(f"\u672a\u8fd4\u56de\u4efb\u52a1 ID: {payload}")
        return str(task_id)

    # ---- poll result ----

    async def poll_task_result(
        self,
        task_id: str,
        poll_interval: int,
        cancel_check=None,
        poll_timeout: float = 300.0,
    ) -> dict[str, Any]:
        """Poll until task completes, fails, or poll_timeout elapses.
        cancel_check: optional async/await callable returning True to abort.
        poll_timeout: max wall-clock seconds to wait (default 300)."""
        import asyncio
        poll_url = f"/v1/draw/result"
        deadline = asyncio.get_event_loop().time() + max(1.0, poll_timeout)
        while asyncio.get_event_loop().time() < deadline:
            if cancel_check:
                try:
                    if await cancel_check():
                        raise NoovaApiError("\u4efb\u52a1\u5df2\u53d6\u6d88")
                except TypeError:
                    if cancel_check():
                        raise NoovaApiError("\u4efb\u52a1\u5df2\u53d6\u6d88")
            await asyncio.sleep(poll_interval)

            last_exc = None
            for retry in range(3):
                try:
                    payload = await self._request(
                        "POST", poll_url,
                        json_body={"id": task_id},
                        headers=self._headers(),
                    )
                    break
                except (httpx.TimeoutException, httpx.ConnectError) as exc:
                    last_exc = exc
                    if retry < 2:
                        await asyncio.sleep(3 * (retry + 1))
                    continue
                except NoovaApiError as exc:
                    # 429 or other HTTP errors
                    if exc.status_code == 429:
                        if retry < 2:
                            await asyncio.sleep(2 ** (retry + 1))
                            continue
                    raise
            else:
                raise NoovaApiError(f"\u8f6e\u8be2\u8bf7\u6c42\u8fde\u7eed\u5931\u8d25: {last_exc}")

            data = self._unwrap(payload)
            if not isinstance(data, dict):
                data = payload if isinstance(payload, dict) else {}

            status = str(data.get("status") or "")
            if status in {"succeeded", "failed", "violation", "cancelled"}:
                return data

        raise NoovaApiError(f"\u8f6e\u8be2\u8d85\u65f6\uff08{int(poll_timeout)} \u79d2\uff09")

    # ---- download ----

    async def download_file(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=60.0, read=self.timeout, write=self.timeout, pool=60.0),
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        try:
            response = await client.get(url)
        finally:
            if owns_client:
                await client.aclose()
        if response.status_code >= 400:
            raise NoovaApiError(
                f"\u4e0b\u8f7d\u56fe\u7247\u5931\u8d25 ({response.status_code}): {response.text[:300]}",
                status_code=response.status_code,
            )
        dest.write_bytes(response.content)
        return dest

    # ---- helpers ----

    @staticmethod
    def get_succeeded_url(result: dict[str, Any]) -> str | None:
        """Extract the first result URL from a succeeded poll response.
        Noova returns: {"data": {"results": [{"url": "https://..."}]}}"""
        data = result if isinstance(result, dict) else {}
        if "data" in data:
            data = data["data"]
        if not isinstance(data, dict):
            return None
        results = data.get("results")
        if isinstance(results, list) and results:
            first = results[0]
            if isinstance(first, dict):
                return first.get("url")
        return None

    @staticmethod
    def get_status(result: dict[str, Any]) -> str:
        data = result if isinstance(result, dict) else {}
        if "data" in data:
            data = data["data"]
        if not isinstance(data, dict):
            return "unknown"
        return str(data.get("status") or "running")

    @staticmethod
    def get_progress(result: dict[str, Any]) -> int:
        data = result if isinstance(result, dict) else {}
        if "data" in data:
            data = data["data"]
        if not isinstance(data, dict):
            return 0
        return int(data.get("progress") or 0)

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        if "data" in payload:
            return payload["data"]
        return payload