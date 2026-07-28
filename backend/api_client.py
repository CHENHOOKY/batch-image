from __future__ import annotations

import base64
import io
import ipaddress
import mimetypes
import uuid
from pathlib import Path
from typing import Any

import httpx

from .config import (
    get_model_endpoint,
    get_model_image_param,
    model_supports_quality,
    validate_external_http_url,
)

MAX_IMAGE_DIM = 2048
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
#: 写入任务记录 / 展示给用户的错误文本上限，避免把超长上游响应灌进 jobs.json
MAX_ERROR_TEXT = 300


def clip(text: Any, limit: int = MAX_ERROR_TEXT) -> str:
    """截断错误文本，防止 jobs.json 被超长上游响应撑爆。"""
    value = str(text)
    return value if len(value) <= limit else value[:limit] + "…"


async def _validate_url_async(value: str, *, field_name: str) -> str:
    """在线程里做 URL 校验：内部的 DNS 解析是阻塞调用，不能占住事件循环。"""
    import asyncio

    return await asyncio.to_thread(
        validate_external_http_url, value, field_name=field_name
    )


def _assert_peer_public(response: "httpx.Response", field_name: str = "远端地址") -> None:
    """核对这条连接实际连到的 IP 是否是公网地址。

    URL 校验和真正建立连接之间隔着第二次 DNS 解析，恶意 DNS 可以在两次之间
    把结果换成内网地址（TOCTOU）。这里读取连接的对端地址做事后核对：对下载
    来说发生在读取响应体之前，属于有效拦截。
    """
    stream = response.extensions.get("network_stream")
    if stream is None:
        return  # 拿不到底层连接信息就不阻断，避免误伤
    try:
        peer = stream.get_extra_info("server_addr")
    except Exception:
        return
    if not peer:
        return
    try:
        address = ipaddress.ip_address(str(peer[0]))
    except (ValueError, IndexError, TypeError):
        return
    if not address.is_global:
        raise NoovaApiError(
            f"{field_name}实际连接到了非公网地址 {address}，已中断（疑似 DNS 重绑定）"
        )


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
        try:
            self.base_url = validate_external_http_url(base_url, field_name="API Base URL")
        except ValueError as exc:
            raise NoovaApiError(str(exc)) from exc
        self.timeout = timeout
        try:
            self.image_proxy_url = (
                validate_external_http_url(image_proxy_url, field_name="图床 API URL")
                if image_proxy_url else ""
            )
        except ValueError as exc:
            raise NoovaApiError(str(exc)) from exc
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "NoovaClient":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=60.0, read=self.timeout, write=self.timeout, pool=60.0),
            follow_redirects=False,
            trust_env=False,
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
        # 每次出站前重新校验一次：设置可能是很久以前保存的，域名解析结果可能已变化
        base = await _validate_url_async(self.base_url, field_name="API Base URL")
        url = f"{base}{path}"
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=60.0, read=self.timeout, write=self.timeout, pool=60.0),
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        try:
            response = await client.request(
                method, url, params=params, json=json_body,
                headers=headers, content=content,
            )
            _assert_peer_public(response, "API Base URL")
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

        if isinstance(payload, dict) and "code" in payload and str(payload.get("code")) != "0":
            detail = payload.get("msg") or payload.get("message") or payload
            raise NoovaApiError(
                f"API 业务请求失败: {clip(detail)}",
                status_code=response.status_code,
                payload=payload,
            )

        if 300 <= response.status_code < 400:
            raise NoovaApiError(
                f"API 请求被重定向 ({response.status_code})，请直接填写最终 API 地址",
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            detail = payload
            if isinstance(payload, dict):
                detail = payload.get("message") or payload.get("error") or payload
            raise NoovaApiError(
                f"API \u8bf7\u6c42\u5931\u8d25 ({response.status_code}): {clip(detail)}",
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
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        proxy_url = await _validate_url_async(self.image_proxy_url, field_name="图床 API URL")
        try:
            response = await client.post(
                proxy_url,
                files={"file": (filename, data, content_type)},
            )
            _assert_peer_public(response, "图床 API URL")
        finally:
            if owns_client:
                await client.aclose()

        if 300 <= response.status_code < 400:
            raise NoovaApiError(
                f"图床上传被重定向 ({response.status_code})，请直接填写最终图床地址",
                status_code=response.status_code,
            )
        if response.status_code >= 400:
            raise NoovaApiError(
                f"\u56fe\u5e8a\u4e0a\u4f20\u5931\u8d25 ({response.status_code}): {clip(response.text)}",
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError:
            raise NoovaApiError(f"\u56fe\u5e8a\u8fd4\u56de\u975e JSON: {clip(response.text)}")
        url = payload.get("url") if isinstance(payload, dict) else None
        if not url:
            raise NoovaApiError(f"\u56fe\u5e8a\u672a\u8fd4\u56de url: {clip(payload)}")
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
        Endpoint, image field, and quality support are model-specific and
        resolved from backend.config."""
        image_param = get_model_image_param(model)
        endpoint = get_model_endpoint(model)
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "aspectRatio": aspect_ratio,
            "imageSize": image_size,
        }
        if urls:
            body[image_param] = urls
        if model_supports_quality(model):
            body["quality"] = quality or "auto"

        # Retry up to 5 times for timeout/connection errors
        last_exc = None
        for attempt in range(5):
            try:
                payload = await self._request(
                    "POST",
                    endpoint,
                    json_body=body,
                    headers=self._headers(),
                )
                break
            except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError, ConnectionResetError) as exc:
                last_exc = exc
                if attempt < 4:
                    import asyncio
                    await asyncio.sleep(5 * (attempt + 1))
                continue
        else:
            raise NoovaApiError(f"\u521b\u5efa\u4efb\u52a1\u5931\u8d25\uff08\u5df2\u91cd\u8bd95 5 \u6b21\uff09: {clip(last_exc)}")

        data = self._unwrap(payload)
        task_id: str | None = None
        if isinstance(data, dict):
            task_id = data.get("id")
        if not task_id:
            # Try response level
            if isinstance(payload, dict):
                task_id = payload.get("id")
        if not task_id:
            raise NoovaApiError(f"\u672a\u8fd4\u56de\u4efb\u52a1 ID: {clip(payload)}")
        return str(task_id)

    # ---- poll result ----

    async def poll_task_result(
        self,
        task_id: str,
        poll_interval: int,
        cancel_check=None,
        poll_timeout: float = 300.0,
        poll_param: str = "id",
    ) -> dict[str, Any]:
        """Poll until task completes, fails, or poll_timeout elapses.
        cancel_check: optional async/await callable returning True to abort.
        poll_timeout: max wall-clock seconds to wait (default 300)."""
        import asyncio
        poll_url = f"/v1/draw/result"
        deadline = asyncio.get_event_loop().time() + max(1.0, poll_timeout)
        first_round = True
        while asyncio.get_event_loop().time() < deadline:
            if cancel_check:
                try:
                    if await cancel_check():
                        raise NoovaApiError("\u4efb\u52a1\u5df2\u53d6\u6d88")
                except TypeError:
                    if cancel_check():
                        raise NoovaApiError("\u4efb\u52a1\u5df2\u53d6\u6d88")
            # \u9996\u8f6e\u7acb\u5373\u67e5\u8be2\uff1a\u539f\u6765\u5148 sleep \u518d\u67e5\uff0c\u767d\u767d\u591a\u7b49\u4e00\u4e2a\u8f6e\u8be2\u5468\u671f\uff08\u9ed8\u8ba4 20 \u79d2\uff09
            if first_round:
                first_round = False
            else:
                await asyncio.sleep(poll_interval)

            last_exc = None
            for retry in range(3):
                try:
                    payload = await self._request(
                        "POST", poll_url,
                        json_body={poll_param: task_id},
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
                raise NoovaApiError(f"\u8f6e\u8be2\u8bf7\u6c42\u8fde\u7eed\u5931\u8d25: {clip(last_exc)}")

            data = self._unwrap(payload)
            if not isinstance(data, dict):
                data = payload if isinstance(payload, dict) else {}

            status = str(data.get("status") or "")
            if status in {"completed", "succeeded", "failed", "violation", "cancelled"}:
                return data

        raise NoovaApiError(f"\u8f6e\u8be2\u8d85\u65f6\uff08{int(poll_timeout)} \u79d2\uff09")

    # ---- download ----

    async def download_file(self, url: str, dest: Path) -> Path:
        try:
            url = await _validate_url_async(str(url), field_name="下载地址")
        except ValueError as exc:
            raise NoovaApiError(str(exc)) from exc
        dest.parent.mkdir(parents=True, exist_ok=True)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(
            timeout=httpx.Timeout(connect=60.0, read=self.timeout, write=self.timeout, pool=60.0),
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        try:
            async with client.stream("GET", url) as response:
                # 在读取任何响应体之前核对对端 IP，拦截 DNS 重绑定
                _assert_peer_public(response, "下载地址")
                if 300 <= response.status_code < 400:
                    raise NoovaApiError(
                        f"下载地址被重定向 ({response.status_code})，已拒绝跟随",
                        status_code=response.status_code,
                    )
                if response.status_code >= 400:
                    detail = clip((await response.aread())[:600].decode("utf-8", "replace"))
                    raise NoovaApiError(
                        f"下载图片失败 ({response.status_code}): {clip(detail)}",
                        status_code=response.status_code,
                    )
                content_length = response.headers.get("content-length")
                try:
                    declared_size = int(content_length or 0)
                except ValueError:
                    declared_size = 0
                if declared_size > MAX_DOWNLOAD_BYTES:
                    raise NoovaApiError("下载图片超过 25 MB 限制")
                total = 0
                try:
                    with dest.open("wb") as output:
                        async for chunk in response.aiter_bytes(64 * 1024):
                            total += len(chunk)
                            if total > MAX_DOWNLOAD_BYTES:
                                raise NoovaApiError("下载图片超过 25 MB 限制")
                            output.write(chunk)
                except Exception:
                    dest.unlink(missing_ok=True)
                    raise
        finally:
            if owns_client:
                await client.aclose()
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
