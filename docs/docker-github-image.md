# GitHub 构建 Docker 镜像

## 镜像来源

GitHub Actions workflow：

```text
.github/workflows/docker-image.yml
```

默认镜像：

```text
ghcr.io/yangye2/grok-register:latest
```

触发方式：

- 推送到 `main`
- 推送到 `master`
- 推送 `v*` 标签
- 在 GitHub Actions 页面手动运行 `Build Docker Image`

## 镜像内容

镜像由 `apps/worker-runtime/Dockerfile` 构建，包含：

- Python 3.12
- Chromium / Xvfb
- 根目录 `requirements.txt` + Console 依赖
- `apps/console`（控制台、任务调度、账号管理、设置库）
- `apps/register-runner`（注册流程）
- `apps/cpa-worker`（CPA 授权/推送、`cpa_xai`、**`health_check` 测活**）
- `turnstilePatch`
- `config.example.json`

说明：

- 根目录另有一份开发用 `health_check/` / `cpa_export.py` 等，**不会**打进镜像；运行时只使用 `apps/*` 副本。
- 注册任务启动时会把 `cpa_export.py`、`cpa_xai/`、`health_check/` 等复制到隔离 `task_dir`，保证「注册成功 → 测活 → 推送 CPA」链路可用。
- 账号批量授权走 Console 直接加载 `apps/cpa-worker/cpa_export.py`，同目录已带 `health_check`。

近期功能（测活 headers 可配置、多次授权失败自动移除邮箱域名）主要落在 `apps/console` + `apps/cpa-worker`，**无需改 GitHub workflow / Dockerfile 结构**；拉新镜像并重启即可。

域名失败统计写在 Console SQLite（`runtime/console` volume），阈值与自动移除在控制台「设置」中配置。

## 服务器部署

首次部署：

```bash
cp .env.example .env
docker compose pull
docker compose up -d --force-recreate
```

代码更新并推送 GitHub 后：

```bash
docker compose pull
docker compose up -d --force-recreate
```

如果只执行 `docker compose up -d`，服务器可能继续使用旧镜像。

## `.env` 必填项

至少确认这些值：

```env
GROK_REGISTER_DEFAULT_PROXY=socks5://warp:1080
GROK_REGISTER_DEFAULT_BROWSER_PROXY=socks5://warp:1080
GROK_REGISTER_DEFAULT_TEMP_MAIL_API_BASE=
GROK_REGISTER_DEFAULT_TEMP_MAIL_ADMIN_PASSWORD=
GROK_REGISTER_DEFAULT_TEMP_MAIL_DOMAIN=
```

远程 CPA 推送：

```env
CPA_CLOUD_UPLOAD_ENABLED=true
CPA_CLOUD_API_BASE=https://your-cpa-host
CPA_CLOUD_MANAGEMENT_KEY=your-management-key
```

xAI 授权默认禁止无头模式，Docker 内会用 Xvfb 跑有头 Chromium。只有明确接受 Cloudflare 拦截风险时才设置：

```env
CPA_ALLOW_HEADLESS=true
```

如果控制台部署在远程服务器，并且你需要从其他机器访问，把端口绑定改为：

```env
GROK_STACK_CONSOLE_BIND=0.0.0.0
```

默认是 `127.0.0.1`，只允许服务器本机访问。

测活 headers、域名失败阈值等进阶项优先在控制台 Web 设置里改（写入 SQLite），不强制写进 `.env`。

## 持久化目录

`docker-compose.yml` 默认挂载：

```text
./runtime/console -> /workspace/apps/console/runtime
./runtime/cpa_auths -> /workspace/cpa_auths
```

这意味着容器重建后：

- 控制台任务记录、账号库、域名失败统计不会丢。
- CPA 授权文件不会丢。

## 环境检查

Docker 内代理地址必须使用容器服务名：

```text
socks5://warp:1080
```

不要在容器配置里写宿主机 `127.0.0.1:1080`，那会指向 console 容器自身，不是 WARP 容器。

环境检查里常见异常：

- `WARP / Proxy` 异常：检查 `warp` 容器是否启动，代理是否是 `socks5://warp:1080`。
- `Temp Mail API` 异常：检查临时邮箱 API 地址、口令和域名。
- `x.ai Sign-up` 异常：通常是出口 IP 风控、代理不可达或 xAI 当前访问限制。
