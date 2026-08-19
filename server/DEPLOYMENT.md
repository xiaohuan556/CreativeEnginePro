# 公司部署申请单

## 首期建议配置

- Ubuntu 24.04 LTS，8 vCPU，32 GB 内存。
- 500 GB NVMe 数据盘起步；生成视频多时建议 1 TB，并配置对象存储归档。
- PostgreSQL 16，建议独立托管实例或同机每日备份。
- 一个正式域名和 HTTPS 证书；网页与 API 必须走同一站点，API 挂在 `/control`。
- 出站网络允许访问已配置的 OpenAI/ModelHub、火山方舟、Seedream、Seedance 和语音服务。
- FFmpeg/ffprobe；本地 AI 拉片还需要 OpenCV。若要本地 Whisper，再单独申请 NVIDIA GPU。

首期生成主要调用云端模型，GPU 不是必需。CPU 和内存主要用于上传、抽帧、拉片、合成和并发任务管理。

## 推荐的一键容器部署

仓库已经包含 `deploy/compose.yml`、Caddy HTTPS 配置以及 Web/API/Worker 镜像。服务器安装 Docker Engine 与 Compose 插件后：

```bash
cp deploy/.env.production.example deploy/.env.production
# 编辑域名、数据库密码和模型密钥；数据库密码建议：openssl rand -hex 32
docker compose --env-file deploy/.env.production -f deploy/compose.yml up -d --build
docker compose --env-file deploy/.env.production -f deploy/compose.yml exec api python scripts/create_admin.py
```

DNS 的 A/AAAA 记录必须先指向服务器，80/443 端口放行后 Caddy 会自动申请和续期证书。PostgreSQL 和媒体卷不会暴露到公网；浏览器只通过同域 `/control` 访问 API。

更新版本时先备份，再拉取代码并重复 `up -d --build`。API 与 Worker 同时启动时会使用 PostgreSQL 事务锁串行执行兼容迁移，避免重复 DDL。

## 生产进程

- Web：1–2 个实例。
- API：2 个实例，均无状态；会话、项目、任务和审计写入 PostgreSQL。
- Worker：先 2 个实例；管理员的每人并发上限仍在数据库层强制执行。
- 媒体：首期可用服务器持久卷；多人规模上升后切换 S3 兼容对象存储。

## 账号上线步骤

1. 命令行创建唯一首个管理员。
2. 管理员在网页中逐个创建账号、设置初始密码、角色、状态、模型白名单和额度；新建表单默认不批准、0 额度、0 并发。
3. 员工不能自行注册；白名单留空会禁止外部模型。停用、改角色、重置密码或点击“下线”都会撤销该账号已有会话。
4. 模型密钥只配置在 API/Worker 的环境变量中，绝不写入网页或项目文件。
5. 上线前先用测试模型和低额度完成一轮图片、视频、音频、续拍、拉片和七阶段制片验收。
6. 管理员页面确认在线会话数与“最近 24 小时使用与安全记录”可读取，并实际测试停用账号、强制下线、空白模型白名单、超额、超并发、重复提交和伪造媒体均被拒绝。
7. 创建三类验收成员：编辑、仅审片、只读；确认只有项目所有者可调整成员，审片人不能改画布，只读成员不能提交任务。

## 网页接入

- 构建网页时设置 `NEXT_PUBLIC_CONTROL_PLANE_URL=/control`。
- Nginx/Caddy 将 `/control/*` 反向代理到 FastAPI 的 `/*`；同域方案最简单，Cookie 保持 `Secure + HttpOnly + SameSite=Strict`。
- 若网页和 API 使用不同子域名，必须同属一个 HTTPS 主域，且 `CEP_PUBLIC_ORIGIN` 精确填写网页 Origin；不要使用通配 CORS。
- 私有 Sites 地址只用于预览。正式公司域名和控制层接通后，登录页才使用管理员创建的账号密码。

## 正式端到端验收

正式域名可访问且管理员创建完成后，在可信终端设置环境变量并运行：

```bash
export CEP_SMOKE_BASE_URL="https://studio.example.com"
export CEP_SMOKE_ADMIN_USERNAME="admin"
export CEP_SMOKE_ADMIN_PASSWORD="在终端中设置，不要写进仓库"
python server/scripts/production_smoke.py
```

脚本会验证：HTTPS API、数据库与媒体存储、Worker 心跳、管理员登录、待批准账号不可登录、项目成员与审片只读权限、媒体真实类型校验、资产库显式复制、工作流模板、两段持久顺序任务、结果写回画布以及强制会话下线。它会保留一个带时间戳的验收项目作为审计证据，并在结束时停用临时审片账号。只检查控制层而暂不调用 Edge TTS 时可设置 `CEP_SMOKE_SKIP_GENERATION=1`。

## 备份与告警

- PostgreSQL 每日全量备份，保留 30 天；每小时增量或 WAL 归档。
- 媒体目录每日增量备份；数据库和媒体必须使用同一时间点恢复策略。
- 告警至少覆盖：登录爆破、连续生成失败、队列积压、磁盘 80%、日费用异常和供应商 429/5xx。
- 管理员页面“生产服务状态”应持续显示数据库、媒体存储正常且至少一个 Worker 在线；45 秒内没有心跳即视为异常。
