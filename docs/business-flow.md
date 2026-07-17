# 注册、授权、推送 CPA 流程

## 注册任务流程

1. 在控制台配置网络、临时邮箱和 CPA 参数。
2. 创建注册任务。
3. 控制台为任务生成独立目录和 `config.json`。
4. 控制台复制注册执行器、CPA worker 和 `turnstilePatch` 到任务目录。
5. 注册执行器启动 Chromium。Docker 内默认通过 Xvfb 或 headless Chromium 运行。
6. 浏览器打开 `https://accounts.x.ai/sign-up?redirect=grok-com`。
7. 执行器通过临时邮箱 API 创建邮箱。
8. 浏览器提交邮箱，执行器轮询邮箱验证码。
9. 浏览器提交验证码，进入资料填写页。
10. 执行器生成姓名和密码并完成注册。
11. 注册完成后从浏览器 cookie 中读取 `sso`。
12. `sso` 写入 `sso/task_<id>.txt`。
13. 邮箱、密码、姓名、`sso` 写入 `accounts/task_<id>.jsonl`。
14. 控制台同步 JSONL 到本地 SQLite，账号管理页出现新账号。

## 注册后自动 CPA 授权

如果 `cpa_export_enabled=true`，每轮注册成功后会继续执行 CPA 授权：

1. 注册执行器把邮箱、密码、当前浏览器 cookie 和 `sso` 传给 `cpa_export.py`。
2. CPA worker 使用 `cpa_xai` 走 xAI OAuth 授权流程。
3. 授权文件写入 `cpa_auth_dir`，默认 Docker 路径是 `/workspace/cpa_auths`。
4. 如果开启 `cpa_copy_to_hotload`，授权文件会复制到 `cpa_hotload_dir`，供本机 CPA 热加载。
5. 如果开启 `cpa_cloud_upload_enabled`，授权文件会上传到远程 CPA 管理接口。
6. CPA 结果写回账号 JSONL，控制台同步为 `generated` 或 `uploaded`。

注意：注册后的自动 CPA 授权目前是同步执行。xAI 授权超时会拖慢后续注册轮次，但不会丢失已经注册成功的 `sso` 和账号记录。

## 已有账号授权并推送 CPA

账号管理页的“授权并推送”用于已经存在的账号：

1. 控制台读取账号表里的邮箱、密码和 `sso`。
2. 后台线程加载 `apps/cpa-worker/cpa_export.py`。
3. 生成 CPA 授权文件。
4. 按配置决定是否复制到本地 CPA 导入目录。
5. 按配置决定是否上传到远程 CPA。
6. 账号表状态更新为：
   - `running`：授权中
   - `generated`：已生成本地授权文件
   - `uploaded`：已推送远程 CPA
   - `failed`：生成或推送失败

## 关键配置

| 配置 | 作用 |
| --- | --- |
| `browser_proxy` | 注册浏览器访问 xAI 使用的代理 |
| `proxy` | 临时邮箱 API、普通 HTTP 请求使用的代理 |
| `cpa_proxy` | xAI 授权专用代理；留空时使用 `proxy` |
| `cpa_headless` | xAI 授权浏览器是否无头运行 |
| `cpa_auth_dir` | CPA 授权文件生成目录 |
| `cpa_copy_to_hotload` | 是否复制到本机 CPA 热加载目录 |
| `cpa_hotload_dir` | 本机 CPA auth 热加载目录 |
| `cpa_cloud_upload_enabled` | 是否推送到远程 CPA |
| `cpa_cloud_api_base` / `CPA_CLOUD_API_BASE` | 远程 CPA 管理地址 |
| `CPA_CLOUD_MANAGEMENT_KEY` | 远程 CPA 管理密钥 |

Docker 内默认代理是 `socks5://warp:1080`。如果环境检查显示代理不可达，优先确认 `warp` 服务已启动、`browser_proxy` 和 `proxy` 都使用容器内地址，而不是宿主机 `127.0.0.1`。


## ???? / Token ?? / SSO OAuth ??

???????????????? grokcli-2api ? SSO OAuth + refresh ????

1. **??**??? SSO cookie ????? `accounts.x.ai`????? CPA ?????? `cli-chat-proxy` chat/completions?
2. **Token ??**????? `refresh_token` ? `auth.x.ai/oauth2/token`?? RT ????????? SSO????? SSO ? HTTP Device Flow ??? token?
3. **OAuth ????**???????? SSO cookie?? HTTP ?? device/code ? verify ? approve ? token??? `xai-<email>.json`????????????

?? API?
- `POST /api/accounts/{id}/probe`
- `POST /api/accounts/{id}/refresh`
- `POST /api/accounts/{id}/oauth`
- `POST /api/accounts/maintain/batch`  body: `{account_ids, mode: probe_only|refresh_only|oauth_only}`


## Outmail

Set `email_provider=outmail` for Outlook pool or anonymous temp mailbox. See `docs/temp-mail-api.md`.
