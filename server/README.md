# Creative Engine 公司服务端

该服务端是网页画布的生产控制层。它不开放自助注册：首个管理员通过命令行创建，后续账号、密码、角色、状态、模型白名单和额度都由管理员确认。

## 安全边界

- 模型密钥只保存在服务端，永远不下发浏览器。
- 密码使用 Argon2id；登录失败按账号和 IP 限流并临时锁定。
- 登录使用随机不可预测的 HttpOnly 会话 cookie，写操作额外验证 CSRF token。
- 角色、项目成员、模型白名单、每日任务/费用和并发数均在服务端强制执行。
- 任务提交必须带幂等键，避免重复点击或网络重试造成重复扣费。
- 登录、账号修改和任务提交进入审计日志。

## 启动

1. 复制 `.env.example` 为 `.env`，配置 PostgreSQL、媒体目录和正式 HTTPS 域名。
2. API 安装 `requirements.txt`；生成工作进程再安装 `requirements-worker.txt`。
3. 在 `server` 目录执行 `python scripts/create_admin.py` 创建首个管理员。
4. 执行 `uvicorn creative_server.main:app --host 127.0.0.1 --port 8000`，由 Nginx/Caddy 通过 HTTPS 反向代理。
5. 另启一个工作进程：`python -m creative_server.worker`。可按额度和 API 并发增加工作进程，但不要超过供应商限制。

开发环境可暂用默认 SQLite；正式多人环境必须使用 PostgreSQL。任务队列直接持久化在 PostgreSQL，API 只负责校验和入队，工作进程崩溃不会丢失排队任务。
