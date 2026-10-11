"""批量出图 - 免安装版打包脚本。

这些逻辑原先写在 build.bat 里，但 cmd 对含中文的批处理解析并不可靠：
UTF-8 无 BOM 会按系统代码页乱码，加 BOM 又会让首行 @echo off 失效，多字节
字符还会让 cmd 按字节偏移续读时错位、把半行当命令执行。因此把逻辑挪到
Python（源码固定按 UTF-8 解析），build.bat 只负责找解释器并调用本文件。

用法：
    python build.py          # 等价于双击 build.bat
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
APP_NAME = "batch_image"
DIST_DIR = DIST / f"{APP_NAME}_dist"
ZIP_PATH = DIST / f"批量出图-免安装版-{date.today():%Y%m%d}.zip"

#: 分发目录 / 压缩包里绝不允许出现的文件（可能包含 API Key）
FORBIDDEN_NAMES = {"settings.json", "jobs.json", "schedules.json"}


def _run(cmd: list[str]) -> int:
    print(">", " ".join(str(part) for part in cmd))
    return subprocess.run(cmd, cwd=ROOT).returncode


def _strip_config_dirs() -> list[str]:
    """删除分发目录里混入的 data 目录，返回被删掉的相对路径。"""
    removed: list[str] = []
    for path in sorted(DIST_DIR.rglob("data")):
        if not path.is_dir():
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed.append(str(path.relative_to(DIST_DIR)))
    return removed


def _make_zip() -> None:
    ZIP_PATH.unlink(missing_ok=True)
    archive_root = DIST.parent
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(DIST_DIR.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(archive_root))


def _zip_leaks() -> list[str]:
    with zipfile.ZipFile(ZIP_PATH) as archive:
        return [name for name in archive.namelist() if Path(name).name in FORBIDDEN_NAMES]


def main() -> int:
    print("=" * 44)
    print("  批量出图 - 免安装版打包")
    print("=" * 44)

    print("[1/6] 检查解释器...")
    if sys.version_info < (3, 10):
        print(f"[错误] 需要 Python 3.10 或更高版本，当前 {sys.version}")
        return 1
    print(f"      解释器: {sys.executable}")

    print("[2/6] 安装运行依赖与 PyInstaller...")
    code = _run([
        sys.executable, "-m", "pip", "install",
        "-r", "requirements.txt", "pyinstaller",
        "-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "--quiet",
    ])
    if code != 0:
        print("[错误] 依赖安装失败，请检查网络或 Python 环境")
        return 1

    print("[3/6] 清理旧的 build / dist 产物...")
    for path in (ROOT / "build" / APP_NAME, DIST_DIR):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    print("[4/6] 正在打包（第一次会比较慢，请耐心等待）...")
    if _run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "batch_image.spec"]) != 0:
        print("[错误] 打包失败，请把上面的报错内容发出来")
        return 1
    if not (DIST_DIR / f"{APP_NAME}.exe").is_file():
        print("[错误] 没有生成 batch_image.exe，打包未成功")
        return 1

    print("[5/6] 检查分发目录是否混入了配置 / 密钥...")
    removed = _strip_config_dirs()
    if removed:
        print(f"      发现 data 目录，已移除: {', '.join(removed)}")
    print("      干净，分发目录内无配置文件")

    print(f"[6/6] 生成压缩包 {ZIP_PATH.name} ...")
    _make_zip()
    leaks = _zip_leaks()
    if leaks:
        print(f"      [警告] 压缩包内仍含配置文件: {', '.join(leaks)}")
        return 1
    print("      已确认压缩包内不含 settings.json / jobs.json / schedules.json")

    size_mb = ZIP_PATH.stat().st_size / 1024 / 1024
    print()
    print("=" * 44)
    print("  打包完成")
    print("=" * 44)
    print(f"  免安装目录: dist\\{DIST_DIR.name}\\")
    print(f"  压缩包:     dist\\{ZIP_PATH.name}  ({size_mb:.1f} MB)")
    print()
    print("  分发时 batch_image.exe 必须和 _internal 文件夹放在一起。")
    print("  打包版的配置会写到 %LOCALAPPDATA%\\BatchImage\\data，")
    print("  不再落在分发目录里，因此转发压缩包不会带出 API Key。")
    print("=" * 44)
    return 0


if __name__ == "__main__":
    sys.exit(main())
