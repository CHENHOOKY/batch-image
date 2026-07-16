@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
if exist "%LocalAppData%\Python\bin\python.exe" set "PY=%LocalAppData%\Python\bin\python.exe"
if not defined PY (
  where python >nul 2>nul && for /f "delims=" %%i in ('where python') do (
    if not defined PY set "PY=%%i"
  )
)
if not defined PY (
  where py >nul 2>nul && set "PY=py"
)
if not defined PY (
  echo 未找到 Python，请先安装 Python 3.10+
  pause
  exit /b 1
)

echo [批量出图] 使用解释器: %PY%
echo [批量出图] 安装依赖...
"%PY%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
  echo 依赖安装失败，请检查 Python / 网络
  pause
  exit /b 1
)

echo [批量出图] 启动服务...
"%PY%" run.py
pause
