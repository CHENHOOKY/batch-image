@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM ASCII-only on purpose: cmd.exe cannot reliably parse batch files that
REM contain multi-byte characters. Chinese output comes from run.py instead.

set "PY="

REM Prefer a real Python install over the Windows Store stub.
if exist "%LocalAppData%\Programs\Python\Python313\python.exe" set "PY=%LocalAppData%\Programs\Python\Python313\python.exe"
if not defined PY if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"
if not defined PY if exist "%LocalAppData%\Programs\Python\Python311\python.exe" set "PY=%LocalAppData%\Programs\Python\Python311\python.exe"
if not defined PY if exist "%LocalAppData%\Programs\Python\Python310\python.exe" set "PY=%LocalAppData%\Programs\Python\Python310\python.exe"
if not defined PY (
  for /f "delims=" %%i in ('where.exe python 2^>nul') do (
    echo %%i | findstr /i "WindowsApps" >nul || if not defined PY set "PY=%%i"
  )
)
if not defined PY if exist "C:\Windows\py.exe" set "PY=C:\Windows\py.exe"
if not defined PY (
  echo [batch-image] Python 3.10+ not found. Please install Python first.
  pause
  exit /b 1
)

echo [batch-image] interpreter: %PY%

REM Skip pip when the dependencies are already importable: re-installing on
REM every start is slow and, when it fails, used to abort the script and leave
REM the previous instance holding port 8787.
"%PY%" -c "import fastapi, uvicorn, httpx, pydantic, multipart, aiofiles, PIL" 2>nul
if errorlevel 1 (
  echo [batch-image] installing dependencies...
  "%PY%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet
  if errorlevel 1 (
    echo [batch-image] dependency install failed, check Python / network.
    pause
    exit /b 1
  )
)

REM run.py probes 8787-8800 and stops a leftover instance of this tool first,
REM so an old server can never keep serving on port 8787.
set "PYTHONUTF8=1"
echo [batch-image] starting server, a leftover instance is stopped automatically...
"%PY%" run.py
pause
