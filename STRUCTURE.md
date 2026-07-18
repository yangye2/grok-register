# 目录结构（精简）

```
grok-register/
├── apps/
│   ├── console/           # Web 控制台 + SQLite 账号库 + 队列
│   ├── register-runner/   # 注册浏览器流程 / 邮箱 / outmail
│   ├── cpa-worker/        # SSO OAuth、测活续期、CPA/Sub2 推送、cpa_xai
│   ├── worker-runtime/    # Dockerfile
│   └── network-gateway/   # 可选网关相关
├── turnstilePatch/        # 浏览器扩展补丁
├── docs/                  # 文档
├── deploy/                # 启动脚本 / 备选 compose
├── config.example.json
├── docker-compose.yml     # 推荐编排入口
├── requirements.txt
└── readme.md
```

## 怎么改代码

| 需求 | 改哪里 |
| --- | --- |
| 页面 / 账号管理 API | `apps/console/` |
| 注册 DOM / 验证码 | `apps/register-runner/DrissionPage_example.py` |
| 邮箱 / outmail | `apps/register-runner/email_register.py` `outmail_client.py` |
| OAuth / 测活 / 续期核心 | `apps/cpa-worker/cpa_xai/` |
| CPA 导出与推送 | `apps/cpa-worker/cpa_export.py` |
| Sub2API 推送格式 | `apps/cpa-worker/cpa_to_sub2api.py` |

不要再在仓库根目录新增业务 `.py` 副本。
