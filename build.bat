@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM This wrapper stays ASCII-only on purpose: cmd.exe cannot reliably parse
REM batch files that contain multi-byte characters (it decodes them with the
REM active code page and may resume reading in the middle of a line). All
REM localized messages and the real build steps live in build.py.

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

set "PYTHONUTF8=1"
"%PY%" build.py
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" echo [batch-image] build failed with exit code %RC%
pause
exit /b %RC%
