@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY="

REM Prefer the real Python install over the Windows Store stub
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
  echo 未找到 Python，请先安装 Python 3.10+
  pause
  exit /b 1
)

echo [批量出图] 使用解释器: %PY%
echo [批量出图] 安装依赖...
"%PY%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet
if errorlevel 1 (
  echo 依赖安装失败，请检查 Python / 网络
  pause
  exit /b 1
)

echo [批量出图] 启动服务...
"%PY%" run.py
pause
