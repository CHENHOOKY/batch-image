@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   批量出图 - 免安装版打包
echo ============================================
echo.

REM ---------- 1. 找 Python（跳过 Windows Store 的占位程序）----------
set "PY="
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
  echo [错误] 未找到 Python，请先安装 Python 3.10+
  pause & exit /b 1
)
echo [1/6] 使用解释器: %PY%

REM ---------- 2. 装依赖 ----------
echo [2/6] 安装运行依赖与 PyInstaller...
"%PY%" -m pip install -r requirements.txt pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet
if errorlevel 1 (
  echo [错误] 依赖安装失败，请检查网络或 Python 环境
  pause & exit /b 1
)

REM ---------- 3. 清理上一次的产物 ----------
echo [3/6] 清理旧的 build / dist 产物...
if exist "build\batch_image" rmdir /s /q "build\batch_image"
if exist "dist\batch_image_dist" rmdir /s /q "dist\batch_image_dist"

REM ---------- 4. 打包 ----------
echo [4/6] 正在打包（第一次会比较慢，请耐心等待）...
"%PY%" -m PyInstaller --noconfirm --clean batch_image.spec
if errorlevel 1 (
  echo [错误] 打包失败，请把上面的报错内容发出来
  pause & exit /b 1
)
if not exist "dist\batch_image_dist\batch_image.exe" (
  echo [错误] 没有生成 batch_image.exe，打包未成功
  pause & exit /b 1
)

REM ---------- 5. 安全检查：分发目录里绝不能有配置文件 ----------
echo [5/6] 检查分发目录是否混入了配置 / 密钥...
set "LEAK="
if exist "dist\batch_image_dist\_internal\data" set "LEAK=1"
if exist "dist\batch_image_dist\data" set "LEAK=1"
if defined LEAK (
  echo         发现 data 目录，正在移除（里面可能含 API Key）...
  if exist "dist\batch_image_dist\_internal\data" rmdir /s /q "dist\batch_image_dist\_internal\data"
  if exist "dist\batch_image_dist\data" rmdir /s /q "dist\batch_image_dist\data"
)
echo         干净，分发目录内无配置文件

REM ---------- 6. 打成 zip ----------
for /f %%d in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set "TODAY=%%d"
set "ZIPNAME=dist\批量出图-免安装版-%TODAY%.zip"
echo [6/6] 生成压缩包 %ZIPNAME% ...
if exist "%ZIPNAME%" del /q "%ZIPNAME%"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\batch_image_dist' -DestinationPath '%ZIPNAME%' -CompressionLevel Optimal"
if errorlevel 1 (
  echo [警告] 压缩失败，但 dist\batch_image_dist 已经可用，可以手动压缩
) else (
  powershell -NoProfile -Command "$z=[IO.Compression.ZipFile]::OpenRead((Resolve-Path '%ZIPNAME%')); $bad=@($z.Entries | Where-Object { $_.FullName -match 'settings\.json|jobs\.json|schedules\.json' }); $z.Dispose(); if($bad.Count -gt 0){ Write-Host '        [警告] 压缩包内仍含配置文件：' ($bad.FullName -join ', ') } else { Write-Host '        已确认压缩包内不含 settings.json / jobs.json / schedules.json' }"
)

echo.
echo ============================================
echo   打包完成
echo ============================================
echo   免安装目录: dist\batch_image_dist\
echo   压缩包:     %ZIPNAME%
echo.
echo   分发时 batch_image.exe 必须和 _internal 文件夹放在一起。
echo   打包版的配置会写到 %%LOCALAPPDATA%%\BatchImage\data，
echo   不再落在分发目录里，因此转发压缩包不会带出 API Key。
echo ============================================
pause
