# console

仓库内置 Web 控制台。

它的目标是把注册任务做成“可创建、可观察、可停止、可删除”的批处理系统，同时保持和现有生产运行目录隔离。每个任务都会复制一套运行文件到独立工作目录，再按任务配置启动，不直接复用正在运行的生产目录。

## 功能

- 系统默认配置
- 新建任务
- 高级参数按任务覆盖
- 实时状态
- 实时控制台日志
- 停止任务
- 删除任务
- 本地账号管理
- 已有账号 CPA 授权并推送
- 环境检查

## 默认目录

- 控制台代码：[apps/console](.)
- 运行数据：`apps/console/runtime/`
- 任务目录：`apps/console/runtime/tasks/task_<id>/`
- CPA 授权目录：默认 `cpa_auth_dir`，Docker 中建议使用 `/workspace/cpa_auths`

## 启动

推荐直接用仓库里的启动脚本：

```bash
cd /home/codex/grok-register
./deploy/start-console.sh
```

Windows 本地启动：

```bat
cd /d E:\XXL\WorkSpace\AI\github\grok-register
deploy\start-console.bat
```

默认环境变量：

- `GROK_REGISTER_SOURCE_DIR=/home/codex/grok-register`
- `GROK_REGISTER_PYTHON=/home/codex/grok-register/.venv/bin/python`
- `GROK_REGISTER_CONSOLE_HOST=0.0.0.0`
- `GROK_REGISTER_CONSOLE_PORT=18600`
- `GROK_REGISTER_CONSOLE_MAX_CONCURRENT_TASKS=1`???????????? `max_concurrent_tasks` ?????????? 1-20?

## 手工启动

```bash
cd /home/codex/grok-register/apps/console
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
GROK_REGISTER_SOURCE_DIR=/home/codex/grok-register \
GROK_REGISTER_PYTHON=/home/codex/grok-register/.venv/bin/python \
python app.py
```

如果你是 Windows，本地注册任务默认会使用仓库根目录下的 `.venv\Scripts\python.exe` 作为运行 Python。

## Docker 启动

根目录 `docker-compose.yml` 默认使用 GitHub 构建镜像：

```bash
docker compose pull
docker compose up -d --force-recreate
```

Docker 镜像构建和部署说明见 [docs/docker-github-image.md](../../docs/docker-github-image.md)。

## systemd 示例

参考 [grok-register-console.service.example](grok-register-console.service.example)。
