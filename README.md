<div align="center">

# 🐟 闲鱼助手（开源版）

**一套面向闲鱼卖家的自动化运营管理后台 — 账号、商品、订单、消息、自动发货、卡密、AI 自动回复，一站搞定。**

![Python](https://img.shields.io/badge/Python-3.11-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688?logo=fastapi&logoColor=white)
![Vue](https://img.shields.io/badge/Vue-3-42b883?logo=vuedotjs&logoColor=white)
![Vite](https://img.shields.io/badge/Vite-5-646CFF?logo=vite&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-8.0-4479A1?logo=mysql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![Node](https://img.shields.io/badge/Node.js-22-339933?logo=nodedotjs&logoColor=white)

</div>

> ## ⚠️ 本项目已停止维护 · 商业版已全面开源
>
> **本项目（旧版 xianyu-pilot）已停止维护**。商业版 **xianyu-pilot-pro** 现已**全面开源**，完整继承本项目能力并进一步增强。请前往商业版项目获取全部功能、更新与支持。
>
> 商业版在继承本项目能力的基础上，进一步提供：
>
> - 🧩 **商业级滑块求解** — 三种模拟轨迹方案轮换（最小急动度物理模型 / 容器内多策略 / 超出容器偏移），完全本地算法驱动、无需外部 API，实测本地成功率 70%+，求解能力与线上商业版完全一致
> - 🔍 **商机挖掘** — 关键词 / 类目 / 对标店铺维度挖掘热门商品，一键 AI 改写标题文案 + AI 生图 + 直接发布，快速复制成熟店铺的选品思路
> - ⚙️ **工作流能力** — 工作流可视化编排、条件化自动选品、多账号批量铺货、定时发布，实现无人值守的自动上架
>
> 👉 **商业版 git 地址**：[github.com/dameng2026/xianyu-pilot-pro](https://github.com/dameng2026/xianyu-pilot-pro)

---

一套面向闲鱼卖家的**单用户开源版本**自动化运营后台，覆盖账号、商品、订单、消息、自动发货、卡密、AI 自动回复等核心场景。开箱即用，支持 Docker Compose 一键部署，也支持本地裸机开发。

遇到行为差异时，以当前仓库的代码、测试和文档为准。

## ⭐ 项目特色

### 🚀 长期维护 · 持续进化
本项目由团队**长期维护、持续迭代**，始终保持最新状态，力求做到更好：

- 🤖 **更智能** — 持续引入 AI 能力：AI 自动回复、RAG 知识库增强、AI 商品推荐、智能文案改写，让店铺运营越来越自动化、越来越省心
- 🎨 **更美观** — 精心打磨的 UI 设计与统一的设计语言，桌面端 + 移动端双端适配，交互细节处处用心
- ✨ **更简洁** — 开箱即用：一键脚本部署、符合直觉的操作流程、贴心的引导与提示，让每一位卖家都能快速上手

### 🧩 商业级滑块求解（领先所有开源项目）
- ✅ **实测本地成功率 70%+**，**无需借助任何外部 API**，完全本地浏览器 + 智能轨迹算法驱动，秒级完成、零额外成本
- ✅ 三种模拟轨迹方案轮换（最小急动度物理模型 / 容器内多策略 / 超出容器偏移），深度模拟真人操作，抗风控能力强
- ✅ 支持 Windows / Linux / Ubuntu / Docker 全平台，求解能力与线上商业版完全一致

### 💪 功能强大 · 极其健壮
- 📦 功能密度行业领先：账号、商品、订单、消息、自动发货、卡密、AI 自动回复、定时任务、数据看板……一站搞定
- 🛡️ 工程化程度极高：异常恢复、幂等保护、降级兜底、审计留痕、安全加固，经得起长时间稳定运行
- 🔄 **保持长期更新**：内置版本更新检测与一键升级，功能持续新增、体验持续打磨

---

## 📸 项目截图

| 控制台仪表盘 | 账号管理 |
| :---: | :---: |
| ![仪表盘](screenshots/dashboard.png) | ![账号管理](screenshots/accounts.png) |

| 商品管理 | 订单管理 |
| :---: | :---: |
| ![商品管理](screenshots/products.png) | ![订单管理](screenshots/orders.png) |

| 在线消息 | 自动发货 |
| :---: | :---: |
| ![在线消息](screenshots/messages.png) | ![自动发货](screenshots/auto-delivery.png) |

| AI 自动回复 | 商品发布 |
| :---: | :---: |
| ![AI 自动回复](screenshots/auto-reply.png) | ![商品发布](screenshots/product-publish.png) |

| 数据看板 | 系统配置 |
| :---: | :---: |
| ![数据看板](screenshots/data-panel.png) | ![系统配置](screenshots/settings.png) |

| 移动端 - 仪表盘 | 移动端 - 消息 |
| :---: | :---: |
| ![移动端仪表盘](screenshots/mobile-dashboard.png) | ![移动端消息](screenshots/mobile-messages.png) |

## ✨ 功能亮点

- 🧑‍💼 **闲鱼账号管理** — 多账号接入、二维码登录、Cookie 保活、一键滑块求解、状态监控
- 📦 **商品管理与发布** — 上下架、编辑、批量同步、分类、商品发布（位置数据本地行政区划内置，开箱即用无需 API Key）
- 🧾 **订单管理** — 同步、跟踪、状态流转、会话侧客户订单卡片、小刀订单免拼发货
- 💬 **在线消息** — 实时会话、WebSocket 长连接、分页回溯、会话级自动回复开关
- 🚚 **自动发货** — 卡密自动交付、实时与手动双通道、补发兜底循环、发货确认语句会话（买家回复确认后才触发）
- 🎫 **卡密仓库** — 库存管理、去重、交付记录
- 🤖 **AI 自动回复** — AI 驱动、RAG 知识库增强、人设与聊天规则可配
- ⏰ **定时任务** — 调度执行、心跳与租约保护
- 📝 **操作日志** — 审计留痕、保留期管理
- 🔔 **通知渠道** — 持久化防重复测试发送，未知结果只能人工确认关闭
- 📚 **RAG 知识库** — 向量检索增强回复
- ⚙️ **系统配置** — 通用模型、向量模型、RAG、商业版桥接状态
- 🧩 **滑块求解（商业级 · 领先所有开源项目）** — 三种模拟轨迹方案轮换（最小急动度物理模型 / 容器内多策略 / 超出容器偏移），**完全本地算法驱动、无需借助任何外部 API**，实测本地成功率高达 **70%+**，支持 Windows / Linux / Ubuntu / Docker 全平台，求解能力与线上商业版完全一致；本地浏览器 + 远程 API 双通道，同账号 10 分钟内跨模块去重防重复扣费
- 🏠 **首页运营** — 轮播、公告、文字广告、广告申请（含在线支付）、关于我们、更新日志
- 🔗 **反馈建议** — 商业版桥接优先 + 本地兜底
- 🔄 **版本更新检测** — 关于我们页面一键检查 GitHub Release 并生成更新脚本

## 🖼️ 商业版预览
| 商业版 - 截图 1 | 商业版 - 截图 2 |
| :---: | :---: |
| ![商业版1](screenshots/commercial-1.png) | ![商业版2](screenshots/commercial-2.png) |

## 🏆 本项目已停止维护 · 商业版已全面开源

本项目（旧版 xianyu-pilot）**已停止维护**，其全部能力已迁移至商业版 **xianyu-pilot-pro** 并**全面开源**、持续迭代。商业版在保留基础能力的同时进一步增强，请前往商业版项目使用最新功能。

👉 **商业版 git 地址**：[github.com/dameng2026/xianyu-pilot-pro](https://github.com/dameng2026/xianyu-pilot-pro)

## 🚀 3 分钟快速上手

> 适合第一次使用的用户。一键脚本会自动生成所有 secrets、bcrypt 密码 hash、`.env` 配置，无需手动创建任何密码文件。
>
> 📖 **完整部署教程**（含 Windows/Ubuntu 详细步骤、常见问题、运维命令、进阶配置）：见 [docs/deployment-guide.md](docs/deployment-guide.md)

### 前置要求
- Docker 24+ 与 Docker Compose v2（[安装 Docker](https://docs.docker.com/get-docker/)；Ubuntu 上若未安装，`start.sh` 会自动安装）
- 一台 Linux / macOS / Windows 机器（建议 2GB+ 内存）

### 步骤

1. **克隆仓库**
   ```bash
   git clone https://github.com/dameng2026/XianYuPilo.git
   cd XianYuPilo
   ```

2. **一键启动**
   ```bash
   sh ./start.sh          # Linux/macOS
   # 或 .\start.bat       # Windows（PowerShell/CMD 均可）
   ```

   首次启动时脚本会自动：
   - 检测 Docker 与 Docker Compose v2
   - 在 `./secrets/` 目录生成所有随机密钥文件（MySQL/Redis/JWT/Cookie/Token 共 7 组，权限 0600）
   - 用 bcrypt cost 12 生成 admin 密码 hash（默认密码 `admin123`，可改）
   - 从 `.env.example` 复制生成 `.env`
   - 拉取预构建镜像并启动 7 个服务（mysql/redis/migrate/crawler/api/worker/web）
   - 等待 Web 健康检查通过（最长 3 分钟）

3. **访问服务**

   浏览器打开 http://localhost:8080

   - 默认账号：`admin`
   - 默认密码：`admin123`（终端会打印，请尽快在系统设置中修改）

### 本地裸机部署（无需 Docker）

不想安装 Docker 的用户，可直接在本机运行，体验与 Docker 一键部署一致：

```bash
# Linux / macOS：首次运行自动初始化并启动
sh ./start-local.sh

# Windows
.\start-local.bat
```

首次运行脚本会自动完成：生成 `.env` 与随机密钥 → 生成 admin bcrypt hash → 创建 MySQL 数据库与用户 → 安装 Python/Node 依赖与 Playwright Chromium → 数据库迁移 → 启动 API/Worker/Crawler/Web 四个服务 → 健康检查。

- 前置要求：Python 3.10+、Node.js 18+、本机已运行 MySQL 8 与 Redis 7（Redis 无密码即可）
- 访问地址：`http://localhost:15176`（默认账号 `admin` / 密码 `admin123`）
- 管理命令：`sh ./status-local.sh` / `sh ./stop-local.sh`（Windows 用 `status-local.bat` / `stop-local.bat`）
- MySQL root 有密码时：`MYSQL_ROOT_PASSWORD="你的密码" sh ./start-local.sh`
- 国内网络加速：`PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple" PLAYWRIGHT_DOWNLOAD_HOST="https://npmmirror.com/mirrors/playwright" sh ./start-local.sh`

> 本地模式仅供单机使用；公网多用户部署请使用 Docker 方式（`sh ./start.sh`）。

### 自定义 admin 密码

```bash
# Linux/macOS：在启动前设置环境变量
ADMIN_PASSWORD="your-strong-password" sh ./start.sh

# Windows PowerShell
$env:ADMIN_PASSWORD="your-strong-password"; .\start.bat
```

### Windows 用户提示

`start.bat` 会调用 `scripts/setup-wizard.ps1`。如果 PowerShell 执行策略受限，脚本会自动用 `-ExecutionPolicy Bypass` 启动，无需手动修改系统策略。

### 常见问题

| 问题 | 解决方法 |
|---|---|
| 端口 8080 被占用 | 脚本会自动尝试 8081-8089 并更新 .env；或手动修改 `WEB_PORT` 后运行 `sh ./start.sh --no-pull` |
| GHCR 镜像拉取慢/失败 | 脚本会自动检测 GHCR 连通性并回退本地构建；国内网络见 [部署教程-国内网络优化](docs/deployment-guide.md#国内网络优化镜像加速) |
| bcrypt 生成失败 | 本版本已彻底解决——不再在主机生成，改由 api 镜像自动生成（零额外下载） |
| 忘记 admin 密码 | 删除 `./secrets/admin-password-hash` 后重跑 `sh ./start.sh`，或手动用 bcrypt 生成 hash 写入该文件 |
| 想更新到最新版 | 进入"关于我们"页 → 点击"检查更新" → 复制脚本执行；或 `git pull && sh ./start.sh` |
| 局域网其他机器访问不了 | 确认 `.env` 中 `WEB_BIND_ADDRESS=0.0.0.0`（默认即是），防火墙放行 8080 端口 |
| Docker Desktop 未运行（Windows） | 脚本会自动启动 Docker Desktop 并等待就绪（最长 90 秒） |
| 磁盘空间不足 | 脚本会预检并警告；清理命令：`docker system prune -a --volumes` |
| 服务启动失败 | 脚本会自动诊断：显示异常容器状态、各服务最近日志、磁盘空间、端口占用 |
| 想完全重新初始化 | 删除 `.env` 和 `./secrets/` 目录后重跑 `sh ./start.sh`（注意：会丢失已有数据，请先备份） |

---

## 🧱 技术架构

| 层级 | 技术 |
|------|------|
| 后端 API | Python 3.11 + FastAPI + SQLAlchemy 2.0 |
| 前端 Web | Vue 3 + Vite |
| 爬虫服务 | Node.js 22 + TypeScript + Playwright |
| 数据库 | MySQL 8.0 |
| 缓存 | Redis 7 |
| 反向代理 | Nginx |
| 部署方式 | Docker Compose |

## 📁 目录结构

```text
xianyu-assistant-opensource/
├── apps/
│   ├── api/        # FastAPI 后端、SQL migration、上传目录挂载点
│   ├── crawler/    # Playwright 子服务，仅负责滑块求解
│   └── web/        # 合并后的 Vue 前端
├── deploy/         # MySQL 初始化脚本与 Nginx TLS 配置示例
├── docs/           # 本地运行与备份恢复文档
├── scripts/        # 生产运维与本地开发脚本
├── screenshots/    # README 截图
├── docker-compose.yml
├── start.bat / start.sh
└── .env.example
```

## 🚀 快速开始（生产候选部署）

### 前置要求

- 受支持的 Linux 与 Docker Engine
- Docker Compose v2（已包含在 Docker Desktop 和 `docker-compose-plugin` 中）
- 推荐同机 TLS 反向代理（如需对公网服务）

### 启动方式

`start.sh` / `start.bat` 已经覆盖所有部署场景：

```bash
sh ./start.sh              # 拉取预构建镜像（推荐）
sh ./start.sh --build      # 本地源码构建（适用于自定义修改或离线场景）
sh ./start.sh --no-pull    # 跳过镜像拉取，使用本地已有镜像
```

Windows 使用 `.\start.bat`，参数相同。

脚本工作流程：
1. 首次运行调用 `scripts/setup-wizard.sh`（或 `.ps1`）：生成 7 组随机 secrets、4 个空的可选 secrets、bcrypt admin 密码 hash、`.env` 文件
2. 拉取多架构预构建镜像（`linux/amd64` + `linux/arm64`，Intel Mac、Apple Silicon 与 x86 服务器原生派发，无需 QEMU）
3. 启动 7 个服务并等待 Web 健康检查通过

镜像源与命名空间可在 `.env` 的 `Prebuilt Docker images` 段覆盖；要切回本地源码构建用 `--build` 参数。

> Compose 会先运行一次性 `migrate` 服务，再允许 API 和 Worker 启动；新库与旧库都走同一个带历史记录和 MySQL 单实例锁的版本化 runner。维护窗口、备份、状态检查和回滚兼容流程见 [`apps/api/migrations/README.md`](apps/api/migrations/README.md)。

### 暴露到公网

默认绑定 `0.0.0.0:8080` 便于局域网访问。暴露到公网前请：

1. 在 `.env` 中将 `TRUSTED_HOSTS` 改为实际域名（例如 `assistant.example.com,localhost,127.0.0.1,api`）
2. 在宿主机部署 TLS 反向代理（Nginx/Caddy），将 443 流量转发到 `127.0.0.1:8080`
3. 将 `WEB_BIND_ADDRESS` 改回 `127.0.0.1`，仅让 TLS 代理访问

API、Crawler、MySQL、Redis 没有宿主机发布端口，只有容器内部地址。

> API 错误同时使用真实 HTTP 状态码和顶层 `code/msg/data` 信封：参数错误、冲突、资源不存在、依赖不可用和内部错误不会再以 HTTP 200 伪装成功。调用方应以 HTTP 状态为主、信封错误码为业务细节；已退役兼容面统一返回 HTTP 410 和迁移指引。

## 🖥️ 本地开发

本地开发与生产部署是两条独立路径。生产 `docker-compose.yml` 会强制要求全部生产秘密，不应再被当作"只启动 MySQL/Redis"的无密码开发捷径。完整步骤、端口和本地裸机启动方式见 [`docs/local-dev-runbook.md`](docs/local-dev-runbook.md)。

Crawler 的所有 `/api/*` 路由都应使用 `X-Internal-Token`；生产仅允许容器内 API 访问。健康检查是 `/health`，就绪检查是 `/ready`。


### 代码中还支持的补充变量

以下变量在 `apps/api/app/core/config.py` 中也已支持；其中商业版桥接相关变量和 `INTERNAL_API_TOKEN_FILE` 已经写进 `.env.example`，这里只列仍需按需手动追加的部分：

- `APP_ENV`
- `AI_PROVIDER_ENABLED`
- `AI_PROVIDER_BASE_URL`
- `AI_PROVIDER_API_KEY`
- `AI_PROVIDER_MODEL`
- `AI_PROVIDER_TIMEOUT_SECONDS`

## 🌐 生产网络边界

| 服务 | 容器端口 | 宿主发布 |
|------|------|------|
| Web | `8080` | 默认 `0.0.0.0:8080`，便于局域网访问；公网部署建议改回 `127.0.0.1` 并由 TLS 入口代理 |
| API | `12401` | 不发布，仅 Web/内部网络 |
| Crawler | `3001` | Compose 容器内私有端口，不映射宿主机，仅 API/内部网络 |
| MySQL | `3306` | 不发布，仅内部网络 |
| Redis | `6379` | 不发布，仅内部网络 |

## 🛠️ 常用命令

启动、重建和上线验收始终走 `start.sh` / `start.bat`；状态、日志和停止使用跨平台的固定命令包装器：

```bash
# 启动、构建、等待健康
sh ./start.sh
# Windows: .\start.bat

# 查看全部服务状态（包含已退出的一次性迁移服务）
python scripts/production_ops.py --env-file .env status

# 查看最近 200 行 API/Web 日志
python scripts/production_ops.py --env-file .env logs --tail 200 api web

# 持续跟随允许列表中的服务日志
python scripts/production_ops.py --env-file .env logs --follow --tail 200 api worker

# 停止并移除本项目容器和网络；不会删除命名卷或镜像
python scripts/production_ops.py --env-file .env stop

# 重启特定服务
python scripts/production_ops.py --env-file .env restart api web
```

日志服务名仅允许 `mysql`、`redis`、`migrate`、`api`、`worker`、`crawler`、`web`；`--tail` 范围为 1–10000。需要再次启动时重新运行 `start.sh` / `start.bat` 即可。


## 📚 参考文档

| 文档 | 说明 |
|------|------|
| [`docs/local-dev-runbook.md`](docs/local-dev-runbook.md) | 本地开发运行手册 |
| [`docs/backup-restore.md`](docs/backup-restore.md) | 备份与恢复 |
| [`apps/api/migrations/README.md`](apps/api/migrations/README.md) | 数据库迁移说明 |
| [`SECURITY.md`](SECURITY.md) | 安全策略 |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | 贡献指南 |

## 🤝 贡献

欢迎提交 Issue 与 Pull Request。提交前请先阅读 [`CONTRIBUTING.md`](CONTRIBUTING.md)，并确保本地 `verify-local.bat`（或对应脚本）通过。

## 🧸 特别鸣谢

本项目在开发过程中参考了以下开源项目，特此致谢：

- [xianyuapis](https://github.com/IAMLZY2018/xianyuapis) — 闲鱼相关 API 的实现思路与协议参考。

## 📄 许可证：Apache-2.0 许可证

本项目采用 [Apache License 2.0](LICENSE) 授权。任何人可在遵守该许可证条款的前提下自由使用、修改和分发本仓库代码。

---

## 💖 赞助

如果这个项目帮助到了你，或者你通过它赚到了钱，并且你愿意的话，希望你能赞助支持我的持续开发与维护工作。你的支持是我继续迭代、修复问题、添加新功能的动力。

<div align="center">

![赞助码](screenshots/qr.png)

</div>

## 💬 交流群

欢迎加入交流群，与更多闲鱼卖家和开发者一起交流使用心得、反馈问题、获取最新动态。以下提供微信群与 QQ 群两种加入方式，按你的习惯选择即可。

### 微信交流群

<div align="center">

![微信群](screenshots/wechat-group.png)

</div>

### QQ 交流群

<div align="center">

![QQ群](screenshots/qq-group.png)

</div>

> 📌 **二维码失效？** 若上方二维码已过期或无法扫码，请添加微信 **`JiShu0724`**（备注「闲鱼助手」），我们会邀请你进群。

---

## 📞 微信客服

如需一对一咨询、技术支持或商务合作，可扫描下方微信客服二维码添加好友：

<div align="center">

![微信客服](screenshots/wechat-kefu.png)

</div>

> 📌 **二维码失效？** 若上方二维码已过期或无法扫码，请直接添加微信 **`JiShu0724`**（备注「闲鱼助手」）。

---

## 🛠️ 定制需求与技术服务

除开源版外，团队也承接各类定制开发与技术服务：

- 🎯 **功能定制** — 基于本项目定制专属功能、对接第三方系统、适配你的业务流程
- 💻 **程序设计 / 外包开发** — 接外活，涵盖 Web 前后端、爬虫、自动化、AI 应用、小程序等
- 🏆 **商业版服务** — 更稳定的多账号管理、商机发掘、工作流自动化、专属客服与运维托管

如有需要，欢迎联系：

- 微信：**`JiShu0724`**（备注「定制 / 外包」）
- 商业版源码：[xianyu-pilot-pro](https://github.com/dameng2026/xianyu-pilot-pro)

<div align="center">

<sub>Made with care for the 闲鱼 seller community.</sub>

</div>
