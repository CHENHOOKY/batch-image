# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for batch-image."""

block_cipher = None

# 注意：datas 里绝不要加 data/ 目录，那里面是用户的 API Key。
# 打包版运行时会把配置写到 %LOCALAPPDATA%\BatchImage\data（见 run.py），
# 分发目录本身不应包含任何配置文件。
frontend_files = [
    ("frontend/index.html", "frontend"),
    ("frontend/app.js", "frontend"),
    ("frontend/styles.css", "frontend"),
    ("frontend/lucide.min.js", "frontend"),
    ("frontend/favicon.svg", "frontend"),
]

hidden = [
    "anyio._backends._asyncio", "anyio.lowlevel", "asyncio",
    "multipart", "multipart.multipart", "aiofiles", "aiofiles.os",
    "httpcore", "httpx", "httpx._transports.default", "starlette",
    "starlette.middleware", "starlette.routing", "uvicorn",
    "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.logging", "uvicorn.lifespan.on",
    "http", "http.server", "http.client",
    "PIL", "PIL.Image",
]

excl = ["unittest", "xml", "xmlrpc",
        "pdb", "doctest", "sqlite3", "distutils", "setuptools"]

a = Analysis(["run.py"], pathex=[], binaries=[], datas=frontend_files,
             hiddenimports=hidden, hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=excl, cipher=block_cipher)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="batch_image",
          debug=False, bootloader_ignore_signals=False, strip=False,
          upx=True, console=True)

coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas,
               strip=False, upx=True, name="batch_image_dist")
