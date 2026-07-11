# 架构说明

项目按“控制台编排、注册执行、CPA 授权、容器运行时”拆分。根目录保留部分旧入口用于兼容，当前控制台和 Docker 镜像使用 `apps/` 下的新结构。

## 目录职责

| 目录 | 职责 |
| --- | --- |
| `apps/console` | FastAPI Web 控制台、SQLite 本地账号库、任务编排、日志和环境检查 |
| `apps/register-runner` | xAI 注册执行器，负责邮箱注册、验证码、资料提交、提取 `sso` |
| `apps/cpa-worker` | xAI OAuth 授权、CPA 授权文件生成、本地导入和远程 CPA 推送 |
| `apps/worker-runtime` | Docker 镜像定义，固化 Python、Chromium、Xvfb 和运行依赖 |
| `turnstilePatch` | 浏览器扩展补丁，随任务一起复制到独立运行目录 |
| `runtime/console` | Docker 挂载的控制台运行数据，保存 SQLite、任务目录和日志 |
| `runtime/cpa_auths` | Docker 挂载的 CPA 授权文件目录 |

## 控制台如何启动任务

1. 控制台读取 `config.example.json`、环境变量和页面保存的系统配置。
2. 新建任务时，控制台生成任务级 `config.json`，但不会把远程 CPA 管理密钥写入任务文件。
3. 控制台把下面文件复制进独立任务目录：
   - `apps/register-runner/DrissionPage_example.py`
   - `apps/register-runner/email_register.py`
   - `apps/cpa-worker/cpa_export.py`
   - `apps/cpa-worker/cpa_xai/`
   - `turnstilePatch/`
4. 任务目录在 `apps/console/runtime/tasks/task_<id>` 下独立执行，避免多个任务互相污染浏览器 profile、日志和输出文件。
5. 任务进程由控制台 supervisor 拉起。Python 路径优先使用 `GROK_REGISTER_PYTHON`，找不到时回退到当前控制台解释器。

## 数据流

注册成功后，执行器会写两类结果：

- `sso/task_<id>.txt`：一行一个 `sso`。
- `accounts/task_<id>.jsonl`：账号完整记录，包含邮箱、密码、姓名、`sso` 和 CPA 结果。

控制台会持续同步 JSONL 到 SQLite 的 `accounts` 表，账号管理页直接读取 SQLite。

## CPA 授权边界

CPA 功能由 `apps/cpa-worker` 负责，不写在控制台页面自动化里。

- 注册成功后：注册执行器调用任务目录里的 `cpa_export.py` 自动生成授权文件。
- 已有账号：账号管理页调用 `POST /api/accounts/{id}/cpa`，控制台后台线程直接加载 `apps/cpa-worker/cpa_export.py` 处理。
- 本地文件：默认写入 `cpa_auth_dir`。
- 本地 CPA 导入：开启 `cpa_copy_to_hotload` 后复制到 `cpa_hotload_dir`。
- 远程 CPA 推送：开启 `cpa_cloud_upload_enabled` 后上传到 `<CPA_CLOUD_API_BASE>/v0/management/auth-files`。

远程管理密钥只从控制台保存配置或 `CPA_CLOUD_MANAGEMENT_KEY` 环境变量读取，不返回给前端，也不写入任务配置。

## Docker 镜像

GitHub Actions 使用 `.github/workflows/docker-image.yml` 构建 `apps/worker-runtime/Dockerfile`，镜像内包含：

- 控制台：`apps/console`
- 注册执行器：`apps/register-runner`
- CPA 授权模块：`apps/cpa-worker`
- 浏览器补丁：`turnstilePatch`
- 示例配置：`config.example.json`

根目录 `docker-compose.yml` 默认使用 GitHub Container Registry 镜像：

```yaml
image: ghcr.io/yangye2/grok-register:latest
```

推送到 `main` / `master`、推送 `v*` 标签，或手动运行 workflow 后，GitHub 会重新构建并推送镜像。服务器需要执行 `docker compose pull` 和 `docker compose up -d --force-recreate` 才会使用新镜像。
