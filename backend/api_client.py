from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

import httpx


class NoovaApiError(RuntimeError):
    def __init__(self, message: str, status_code: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class NoovaClient:
    def __init__(self, api_key: str, base_url: str = "https://noova.cn", timeout: float = 120.0):
        if not api_key or not api_key.strip():
            raise NoovaApiError("请先在设置中填写 API Key")
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "NoovaClient":
        self._client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
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
        absolute_url: str | None = None,
    ) -> Any:
        url = absolute_url or f"{self.base_url}{path}"
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        try:
            response = await client.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                content=content,
            )
        finally:
            if owns_client:
                await client.aclose()

        content_type = response.headers.get("content-type", "")
        payload: Any
        if "application/json" in content_type:
            try:
                payload = response.json()
            except ValueError:
                payload = response.text
        else:
            payload = response.content if method.upper() == "GET" and absolute_url else response.text

        if response.status_code >= 400:
            detail = payload
            if isinstance(payload, dict):
                detail = payload.get("message") or payload.get("error") or payload
            raise NoovaApiError(
                f"API 请求失败 ({response.status_code}): {detail}",
                status_code=response.status_code,
                payload=payload,
            )
        return payload

    async def get_upload_credential(
        self,
        filename: str,
        content_type: str,
        size: int,
    ) -> dict[str, Any]:
        payload = await self._request(
            "GET",
            "/v1/client/resource/sts",
            params={
                "filename": filename,
                "content_type": content_type,
                "size": str(size),
            },
            headers=self._headers(),
        )
        data = self._unwrap(payload)
        if not isinstance(data, dict) or "upload_url" not in data or "file_url" not in data:
            raise NoovaApiError(f"上传凭证返回异常: {payload}")
        return data

    async def upload_local_file(self, file_path: Path) -> str:
        if not file_path.exists() or not file_path.is_file():
            raise NoovaApiError(f"文件不存在: {file_path}")

        content = file_path.read_bytes()
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        credential = await self.get_upload_credential(
            filename=file_path.name,
            content_type=content_type,
            size=len(content),
        )

        upload_url = credential["upload_url"]
        put_headers = dict(credential.get("headers") or {})
        put_headers.setdefault("Content-Type", content_type)

        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        try:
            put_resp = await client.put(upload_url, content=content, headers=put_headers)
        finally:
            if owns_client:
                await client.aclose()

        if put_resp.status_code >= 400:
            raise NoovaApiError(
                f"文件上传失败 ({put_resp.status_code}): {put_resp.text[:500]}",
                status_code=put_resp.status_code,
                payload=put_resp.text,
            )

        file_url = credential.get("file_url")
        if not file_url:
            raise NoovaApiError(f"上传成功但未返回 file_url: {credential}")
        return str(file_url)

    async def create_draw_task(
        self,
        *,
        model: str,
        prompt: str,
        aspect_ratio: str = "1:1",
        quality: str = "auto",
        urls: list[str] | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "aspectRatio": aspect_ratio,
            "quality": quality,
        }
        if urls:
            body["urls"] = urls

        payload = await self._request(
            "POST",
            "/v1/draw/completions",
            json_body=body,
            headers=self._headers(),
        )
        data = self._unwrap(payload)
        task_id = self._extract_task_id(data if data is not None else payload)
        if not task_id:
            raise NoovaApiError(f"创建任务成功但未返回 id: {payload}")
        return task_id

    async def query_draw_result(self, task_id: str) -> dict[str, Any]:
        payload = await self._request(
            "POST",
            "/v1/draw/result",
            json_body={"id": task_id},
            headers=self._headers(),
        )
        data = self._unwrap(payload)
        if isinstance(data, dict):
            return data
        if isinstance(payload, dict):
            return payload
        raise NoovaApiError(f"查询结果返回异常: {payload}")

    async def download_file(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        try:
            response = await client.get(url)
        finally:
            if owns_client:
                await client.aclose()
        if response.status_code >= 400:
            raise NoovaApiError(
                f"下载图片失败 ({response.status_code}): {response.text[:300]}",
                status_code=response.status_code,
            )
        dest.write_bytes(response.content)
        return dest

    @staticmethod
    def _unwrap(payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        if "data" in payload:
            return payload["data"]
        if "result" in payload:
            return payload["result"]
        return payload

    @staticmethod
    def _extract_task_id(payload: Any) -> str | None:
        if isinstance(payload, str) and payload.strip():
            return payload.strip()
        if not isinstance(payload, dict):
            return None
        for key in ("id", "task_id", "taskId", "request_id", "requestId"):
            value = payload.get(key)
            if value:
                return str(value)
        nested = payload.get("task") or payload.get("data")
        if isinstance(nested, dict):
            for key in ("id", "task_id", "taskId"):
                value = nested.get(key)
                if value:
                    return str(value)
        return None

    @staticmethod
    def extract_result_urls(result: dict[str, Any]) -> list[str]:
        candidates: list[Any] = []
        for key in ("urls", "images", "image_urls", "imageUrls", "files", "results"):
            if key in result:
                candidates.append(result[key])

        for key in ("url", "image", "image_url", "imageUrl", "file_url", "fileUrl", "output"):
            if key in result:
                candidates.append(result[key])

        data = result.get("data")
        if isinstance(data, dict):
            for key in ("urls", "images", "image_urls", "url", "image", "image_url", "file_url"):
                if key in data:
                    candidates.append(data[key])
        elif isinstance(data, list):
            candidates.append(data)

        urls: list[str] = []

        def walk(value: Any) -> None:
            if isinstance(value, str):
                text = value.strip()
                if text.startswith("http://") or text.startswith("https://"):
                    urls.append(text)
                return
            if isinstance(value, dict):
                for nested_key in ("url", "image", "image_url", "imageUrl", "file_url", "fileUrl"):
                    if nested_key in value:
                        walk(value[nested_key])
                for nested_value in value.values():
                    if isinstance(nested_value, (list, dict)):
                        walk(nested_value)
                return
            if isinstance(value, list):
                for item in value:
                    walk(item)

        for item in candidates:
            walk(item)

        seen: set[str] = set()
        unique: list[str] = []
        for url in urls:
            if url not in seen:
                seen.add(url)
                unique.append(url)
        return unique

    @staticmethod
    def extract_status(result: dict[str, Any]) -> str:
        for key in ("status", "state", "task_status", "taskStatus"):
            value = result.get(key)
            if value is not None:
                return str(value).lower()
        data = result.get("data")
        if isinstance(data, dict):
            for key in ("status", "state", "task_status", "taskStatus"):
                value = data.get(key)
                if value is not None:
                    return str(value).lower()
        return "processing"

    @staticmethod
    def is_success_status(status: str) -> bool:
        return status in {
            "success",
            "succeeded",
            "completed",
            "complete",
            "done",
            "finished",
            "ok",
        }

    @staticmethod
    def is_failed_status(status: str) -> bool:
        return status in {"failed", "error", "cancelled", "canceled"}
