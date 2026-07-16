from __future__ import annotations

import socket
import webbrowser
from pathlib import Path

import uvicorn


def find_free_port(start: int = 8787, end: int = 8800) -> int:
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("8787-8800 端口都被占用，请手动指定端口")


def main() -> None:
    port = find_free_port()
    url = f"http://127.0.0.1:{port}"
    print(f"批量出图服务启动: {url}")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
