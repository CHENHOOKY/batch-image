# 批量出图工具

当前版本：`v1.9.0`

## v1.9.0 更新内容

- 移除旧模型 `gpt-image-2-G`，改用新模型集合。
- 模型列表更新为 6 个：`GPT Image 2.5 sunburst`、`gpt-image-2.5-flare-1K`、
  `GPT-Image-2-L`、`nano-banana-2`、`Nano Banana 2.1`、`nano-banana-pro`。
- 按新文档校准每个模型的比例集合与 quality 支持：
  - 三个 GPT Image 模型：支持 `quality`，比例含 `3:1` / `1:3`。
  - `nano-banana-2` / `Nano Banana 2.1`：比例含 `1:4` / `4:1` / `1:8` / `8:1`，无 `quality`。
  - `nano-banana-pro`：无 `quality`。
- 修正创建任务的请求字段：上游要求蛇形 `aspect_ratio`（写 `aspectRatio` 会报
  「缺少必需参数: 比例」）。GPT Image 系列必须传比例对应的像素尺寸
  （如 `9:16` → `720x1280`），nano 系列直接传比例本身；界面始终展示比例。
- `imageSize` 按模型区分：GPT Image 系列不发送（上游不需要），nano 系列必须传
  `1K` / `2K` / `4K`；图像尺寸选择器只在 nano 系列可用。
- 默认模型改为 `GPT Image 2.5 sunburst`；旧的 `gpt-image-2` 迁移到
  `GPT Image 2.5 sunburst`，旧的 `gpt-image-2-G` 迁移到 `gpt-image-2.5-flare-1K`。
- 结果解析兼容 `results` / `result` / `imageUrl`，任务 ID 兼容 `id` / `task_id`；
  上游以 HTTP 200 + `error` 返回的校验失败会透出真实原因。
- 启动时单实例化：探测 8787-8800，若端口上确实是本工具的旧实例就自动结束，
  避免新旧两个服务同时存在、页面一直停留在旧版本。
- 新增模型价格展示：通过 `GET /api/model-prices` 代理上游 `GET /api/models`，
  把积分价格标注在模型下拉选项中（如 `GPT-Image-2-L（6积分/次）`），并在选择框
  下方显示当前模型的实时价格；价格结果缓存 5 分钟，取不到时不影响模型选择。

## v1.8.4 更新内容

- 修复积分余额字段使用驼峰命名 `remainingTotal` 时页面不显示的问题。

## v1.8.3 更新内容

- 积分余额改为请求上游 `GET /api/v1/credit`，兼容直接或嵌套余额响应。
- 手动刷新失败时在页面显示具体错误，不再静默无反应。

## v1.8.2 更新内容

- 默认 API Base URL 改为 `https://noova.vip`，已有旧默认地址会自动迁移。

## v1.8.1 更新内容

- 修正两个模型的统一创建入口为 `/api/v1/invoke`；异步结果仍通过 `/v1/content` 查询。

## v1.8.0 更新内容

- 模型列表仅保留 `gpt-image-2-G` 和 `GPT-Image-2-L`。
- 两个模型统一使用 `/api/v1/gateway/invoke`、`referenceImages`、`aspectRatio`、`quality` 和 `/v1/content` 轮询协议。
- 旧的 `gpt-image-2` 设置会自动迁移到 `GPT-Image-2-L`。

## v1.7.5 更新内容

- `gpt-image-2-G` 与 `GPT-Image-2-L` 使用统一的创建、参考图、比例、质量和异步轮询参数。
- 异步查询改为 `/v1/content`，支持 `results` 直接返回图片 URL。

## v1.7.4 更新内容

- 增强图片读取兼容性，处理截断/轻微损坏的图片，避免 Pillow 解码异常直接中断任务。

## v1.7.3 更新内容

- 统一参考图字段为 `referenceImages`。

## v1.7.2 更新内容

- 两个 GPT Image 模型均发送 `prompt`、`model`、`aspectRatio`、`referenceImages`、`quality`，不再发送 `imageSize`。

## v1.7.1 更新内容

- 修复升级免安装包后首次打开仍可能显示旧界面的问题：前端资源按内容生成指纹并禁止缓存。
- 启动器在本地服务可访问后才打开浏览器，并且就绪检测不受系统代理影响。
- 清理未被界面调用的模型接口与辅助函数，保留全部现有交互和任务功能。

## v1.7.0 更新内容

- 修复开启系统代理后连接测试和积分请求异常的问题，应用的上游请求不再误用系统代理环境变量。
- API Base URL 和图床上传地址增加固定默认值，设置页面始终展示可用地址。
- 新增精确到秒的定时运行任务，支持保存、取消、持久化，并在服务重启后继续等待执行。
- 新增失败任务自动重试设置：首轮任务全部提交完成后，只重新提交失败的子任务，不改变成功任务；支持设置最大重试轮数。
- 优化定时运行入口位置，打开页面即可看到定时任务设置。
- 打包安全检查增加 `schedules.json`，发布包不会携带本地设置、任务记录或定时计划。

一个基于 FastAPI + 原生前端的批量图像生成工具，支持从本地文件夹读取图片，按提示词批量提交到 Noova API，并实时查看进度、结果和错误信息。

## 功能特性

- 批量读取本地文件夹中的图片并上传
- 可配置并发任务数量、轮询间隔和超时时间
- 支持多个提示词批量生成
- 实时显示任务进度、成功数、失败数和输出结果
- 支持结果预览、下载和任务历史恢复
- API Key 仅保存在本地设置文件中，接口返回时自动掩码
- 外部 URL 校验和下载大小限制，避免访问本机或内网地址
- 选择图片文件夹后自动统计图片数量
- 支持模型专属的尺寸、比例、质量和轮询参数
- 浅色 / 深色双主题，一键切换并自动记住选择（默认跟随系统）
- 输出结果支持按状态筛选（全部 / 进行中 / 成功 / 失败 / 取消）
- 结果图片点击可放大预览（Esc 或点击空白处关闭）
- 任务结束后可一键重试失败或已取消的子任务，成功的结果不受影响
- 任务记录只保留最近 30 条，避免 `jobs.json` 无限增长
- 进度轮询带状态指纹，状态无变化时只返回极小响应

## 安全说明

本工具是一个只监听 `127.0.0.1` 的本地服务。但"只监听本机"对浏览器来说并不是
安全边界——任何网页都能向 `127.0.0.1` 发请求。因此加了两层防护：

- **访问令牌**：每次启动随机生成、不落盘，由后端注入首页。所有 `/api/` 接口都要求
  携带该令牌。跨域网页受同源策略限制读不到页面内容，因此拿不到令牌，也就无法伪造
  请求（包括那些不带请求体、原本能被任意网页触发的接口）。
- **Host 校验**：只接受 Host 为 `127.0.0.1` / `localhost` / `::1` 的请求，用于阻断
  DNS rebinding —— 否则恶意域名解析到本机后就能取得同源权限、改掉 API 地址并把
  你的 API Key 转发出去。

由此带来的两点行为变化：

- 服务重启后令牌会更新，旧页面的请求会返回 401，**刷新页面**即可。
- 如果你改成监听 `0.0.0.0` 想从局域网访问，Host 校验会拒绝（这是有意为之：
  本服务没有多用户鉴权，不应暴露到局域网）。

需要说明的是，这两层防护针对的是**浏览器侧**的攻击（CSRF、DNS rebinding）。
以你自己身份运行的本机程序仍然可以读到令牌并调用接口——对桌面工具而言这是
固有前提，任何本地程序本来也能直接读取配置文件。

### 关于 API Key 的存放位置

- 源码运行：`data/settings.json`（项目目录下）
- **打包版：`%LOCALAPPDATA%\BatchImage\data\`**

打包版之所以不放在程序目录里，是因为冻结运行时配置会落在
`batch_image_dist/_internal/data/`，一旦把用过的整个分发目录压缩转发，API Key
就跟着一起发出去了。首次启动会自动把旧位置的配置迁移过去，并清掉原文件里的密钥。
**打包分发前请确认压缩包内不含 `data/` 目录。**

## 支持模型

当前仅保留以下六个模型：

- `GPT Image 2.5 sunburst`（仅 1K，支持 quality）
- `gpt-image-2.5-flare-1K`（仅 1K，支持 quality）
- `GPT-Image-2-L`（仅 1K，支持 quality）
- `nano-banana-2`（支持 1K/2K/4K）
- `Nano Banana 2.1`（支持 1K/2K/4K）
- `nano-banana-pro`（支持 1K/2K/4K）

## 快速开始（源码运行）

需要 Python 3.10 或更高版本。

```bash
python -m pip install -r requirements.txt
python run.py
```

Windows 也可以直接双击 `start.bat`：依赖已装好时不会再跑 pip，启动时会自动结束
残存的本工具旧实例，保证页面加载的是最新代码。

启动后浏览器访问控制台显示的地址，通常为 `http://127.0.0.1:8787`。首次使用请在“设置”中填写 Noova API Key 和 Base URL。

## Windows 免安装版

**双击 `build.bat` 即可**，它会自动完成：找 Python、安装依赖和 PyInstaller、清理旧产物、
执行打包、检查分发目录里没有混入配置文件、最后压缩成 `dist/批量出图-免安装版-<日期>.zip`
并复查压缩包内不含 `settings.json` / `jobs.json`。

也可以手动执行：

```bash
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean batch_image.spec
```

构建产物为 `dist/batch_image_dist/`。分发时必须保持 `batch_image.exe` 与 `_internal` 文件夹在同一目录。双击 `batch_image.exe` 即可启动，无需安装 Python。

打包版的配置写在 `%LOCALAPPDATA%\BatchImage\data`，生成结果默认放在 exe 旁边的
`outputs\`（若程序被放在受保护目录则自动改用 `文档\BatchImage\outputs`）。

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
