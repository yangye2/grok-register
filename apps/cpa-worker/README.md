# cpa-worker

CPA 授权模块，负责 xAI OAuth 授权、授权文件生成、本地导入和远程 CPA 推送。

## 调用方式

- 注册成功后：`apps/register-runner/DrissionPage_example.py` 在任务目录内导入 `cpa_export.py`。
- 已有账号：控制台账号管理页后台线程直接加载本目录的 `cpa_export.py`。

## 输出

- 默认生成到 `cpa_auth_dir`。
- 开启 `cpa_copy_to_hotload` 后复制到 `cpa_hotload_dir`。
- 开启 `cpa_cloud_upload_enabled` 后上传到 `<CPA_CLOUD_API_BASE>/v0/management/auth-files`。
- 默认强制有头模式；只有设置 `CPA_ALLOW_HEADLESS=true` 时才会真正使用无头浏览器。

远程密钥优先从 `CPA_CLOUD_MANAGEMENT_KEY` 或 `CLI_PROXY_MANAGEMENT_KEY` 环境变量读取。控制台不会把密钥写入任务配置文件。
