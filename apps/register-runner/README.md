# register-runner

注册执行器模块。

控制台新建任务时，会把本目录文件复制到独立任务目录执行：

- `DrissionPage_example.py`
- `email_register.py`

任务运行时还会一并复制：

- `apps/cpa-worker/cpa_export.py`
- `apps/cpa-worker/cpa_xai/`
- `turnstilePatch/`

## 职责

- 启动 Chromium / DrissionPage
- 访问 xAI 注册页
- 创建临时邮箱并获取验证码
- 填写注册资料
- 获取 `sso`
- 写入 `sso/task_<id>.txt`
- 写入 `accounts/task_<id>.jsonl`
- 注册成功后按配置调用 CPA worker 生成授权文件

根目录同名脚本保留为兼容入口；控制台和 Docker 镜像使用本目录。
