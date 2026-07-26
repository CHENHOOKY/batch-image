from __future__ import annotations

"""本地服务的安全边界。

这个应用是一个只监听 127.0.0.1 的本地 Web 服务，没有多用户概念，
但"只监听本机"对浏览器来说并不是安全边界：任何网页都能向 127.0.0.1
发请求，配合 DNS rebinding 甚至能取得同源权限。因此这里做两层防护：

1. HostGuardMiddleware —— 校验 Host 头，掐断 DNS rebinding。
   rebinding 的本质是攻击者域名解析到 127.0.0.1，此时浏览器发出的
   Host 头仍然是攻击者域名，与本机名字对不上，直接拒绝。

2. TokenAuthMiddleware —— /api/* 全部需要携带每次启动随机生成的 token。
   token 只注入到由本服务渲染的首页里，跨域网页读不到（同源策略），
   因此无法伪造请求。这同时封堵了无 body 端点的 CSRF。
"""

import hmac
import ipaddress
import secrets
from typing import Iterable

from starlette.responses import JSONResponse

#: 每次启动重新生成，不落盘。进程结束即失效。
APP_TOKEN: str = secrets.token_urlsafe(32)

TOKEN_HEADER = "x-auth-token"

#: 允许的 Host（不含端口）。只认本机名字。
ALLOWED_HOSTNAMES: frozenset[str] = frozenset(
    {"127.0.0.1", "localhost", "::1", "[::1]"}
)

#: 不需要 token 的路径。仅限静态资源与首页本身。
_PUBLIC_PREFIXES: tuple[str, ...] = (
    "/index.html",
    "/styles.css",
    "/app.js",
    "/lucide.min.js",
    "/favicon.svg",
)


def _split_host(raw: str) -> str:
    """从 Host 头里取出主机名，去掉端口。兼容 IPv6 的 [::1]:8787 写法。"""
    value = raw.strip()
    if not value:
        return ""
    if value.startswith("["):
        end = value.find("]")
        if end != -1:
            return value[: end + 1].lower()
        return value.lower()
    return value.rsplit(":", 1)[0].lower() if value.count(":") == 1 else value.lower()


def is_allowed_host(raw_host: str) -> bool:
    host = _split_host(raw_host)
    if not host:
        # 没有 Host 头的只可能是 HTTP/1.0 或本地工具，按拒绝处理更安全
        return False
    if host in ALLOWED_HOSTNAMES:
        return True
    # 兜底：任何解析成回环地址的字面量 IP 也放行（例如 127.0.0.2）
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def _json(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


class HostGuardMiddleware:
    """拒绝 Host 头不是本机名字的请求，用于阻断 DNS rebinding。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        host = headers.get(b"host", b"").decode("latin-1")
        if not is_allowed_host(host):
            await _json(403, "非法的 Host 头，本服务只接受来自本机的访问")(scope, receive, send)
            return
        await self.app(scope, receive, send)


class TokenAuthMiddleware:
    """/api/* 需要携带正确的 X-Auth-Token。"""

    def __init__(self, app, token: str, public_prefixes: Iterable[str] = _PUBLIC_PREFIXES):
        self.app = app
        self.token = token
        self._token_bytes = token.encode("utf-8")
        self.public_prefixes = tuple(public_prefixes)

    def _needs_auth(self, path: str) -> bool:
        if path == "/" or path in self.public_prefixes:
            return False
        return path.startswith("/api/")

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if not self._needs_auth(path):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        supplied = headers.get(TOKEN_HEADER.encode("latin-1"), b"")
        # 按字节做常量时间比较：compare_digest 不接受非 ASCII 的 str，
        # 直接传解码后的字符串会在收到非 ASCII 头时抛 TypeError（500 而非 401）。
        if not supplied or not hmac.compare_digest(supplied, self._token_bytes):
            await _json(401, "缺少或无效的访问令牌，请刷新页面重新打开本工具")(scope, receive, send)
            return
        await self.app(scope, receive, send)
