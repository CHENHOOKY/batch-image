# 批量出图工具

一个基于 FastAPI + 原生前端的批量图像生成工具，支持从本地文件夹读取图片，按提示词批量提交到 Noova API，并实时查看进度、结果和错误信息。

## 功能特性

- 批量读取本地文件夹中的图片并上传
- 可配置并发任务数量、轮询间隔和超时时间
- 支持多个提示词批量生成
- 实时显示任务进度、成功数、失败数和输出结果
- 支持结果预览、下载和任务历史恢复
- API Key 仅保存在本地 `data/settings.json`，接口返回时自动掩码
- 外部 URL 校验和下载大小限制，避免访问本机或内网地址
- 选择图片文件夹后自动统计图片数量
- 支持模型专属的尺寸、比例、质量和轮询参数

## 支持模型

当前仅保留以下四个模型：

- `gpt-image-2`
- `gpt-image-2-G`
- `nano-banana-2`
- `nano-banana-pro`

## 快速开始（源码运行）

需要 Python 3.10 或更高版本。

```bash
python -m pip install -r requirements.txt
python run.py
```

启动后浏览器访问控制台显示的地址，通常为 `http://127.0.0.1:8787`。首次使用请在“设置”中填写 Noova API Key 和 Base URL。

## Windows 免安装版

使用 PyInstaller 构建：

```bash
python -m PyInstaller --noconfirm --clean batch_image.spec
```

构建产物为 `dist/batch_image_dist/`。分发时必须保持 `batch_image.exe` 与 `_internal` 文件夹在同一目录，也可以直接发送项目提供的 ZIP 压缩包。双击 `batch_image.exe` 即可启动，无需安装 Python。

## 目录说明

```text
backend/       FastAPI 后端、模型配置和任务管理
frontend/      页面、脚本和样式
data/          本地设置和任务记录（运行时生成，不提交到 Git）
outputs/       生成结果（运行时生成，不提交到 Git）
run.py         开发模式和打包后的启动入口
batch_image.spec  PyInstaller 打包配置
```

## 安全提示

- 不要把 `data/settings.json`、API Key 或个人输出目录提交到公开仓库。
- API Key 只在本机保存，分享项目或压缩包前请确认没有包含 `data/` 目录。
- 生成任务需要有效的 Noova API Key，并可能产生 API 用量。

## 许可证

本项目按仓库所有者的授权方式使用。提交修改前请确认符合目标仓库的许可证要求。
