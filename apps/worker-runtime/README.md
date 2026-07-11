# worker-runtime

Docker 运行时环境定义。

GitHub Actions 使用本目录的 [Dockerfile](Dockerfile) 构建 GHCR 镜像。镜像内包含控制台、注册执行器、CPA worker 和浏览器补丁，不依赖宿主机再挂载源码。

## 镜像内置

- Python 3.12
- Chromium
- Xvfb
- 根目录 [requirements.txt](../../requirements.txt)
- 控制台依赖 [apps/console/requirements.txt](../console/requirements.txt)
- `apps/console`
- `apps/register-runner`
- `apps/cpa-worker`
- `turnstilePatch`

## 默认入口

```bash
python /workspace/apps/console/app.py
```

配套部署文件见根目录 [docker-compose.yml](../../docker-compose.yml)，GitHub 镜像说明见 [docs/docker-github-image.md](../../docs/docker-github-image.md)。
