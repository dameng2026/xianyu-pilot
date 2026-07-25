# 部署教程（Windows / Ubuntu）

本教程基于实战测试，覆盖从空白系统到服务上线的完整流程。两种系统的部署方式高度一致，核心都是一条命令：

```bash
sh ./start.sh          # Linux / macOS
.\start.bat            # Windows
```

脚本会自动处理：Docker 安装检测（缺失时自动安装）、secrets 生成、`.env` 配置、镜像构建/拉取、服务启动、健康检查。

---

## 目录

- [系统要求](#系统要求)
- [Ubuntu 部署](#ubuntu-部署)
  - [方式一：一键脚本（推荐）](#方式一一键脚本推荐)
  - [方式二：手动分步](#方式二手动分步)
- [Windows 部署](#windows-部署)
  - [方式一：一键脚本（推荐）](#方式一一键脚本推荐-1)
  - [方式二：手动分步](#方式二手动分步-1)
- [首次登录与配置](#首次登录与配置)
- [运维命令](#运维命令)
- [常见问题](#常见问题)
- [进阶配置](#进阶配置)
- [卸载与清理](#卸载与清理)

---

## 系统要求

| 项目 | 最低 | 推荐 |
|---|---|---|
| **操作系统** | Ubuntu 22.04 / Windows 10 1809 | Ubuntu 24.04 / Windows 11 |
| **内存** | 2 GB | 4 GB |
| **磁盘** | 10 GB 可用空间 | 20 GB |
| **CPU** | 1 核 | 2 核 |
| **网络** | 能访问 GitHub 和 Docker Hub | 同左 |
| **端口** | 8080 可用 | 同左 |

**无需预装任何软件**：Docker、Docker Compose、Python、bcrypt 等都由脚本自动处理。

### 部署时长参考

基于 Ubuntu 24.04 / 3.8GB 内存服务器的实测数据：

| 场景 | 耗时 |
|---|---|
| 首次部署（含 Docker 安装 + 镜像构建） | 约 5-8 分钟 |
| 首次部署（已装 Docker + 镜像构建） | 约 4-6 分钟 |
| 首次部署（已装 Docker + 拉取预构建镜像） | 约 2-4 分钟 |
| 二次部署（镜像已缓存） | 约 1-2 分钟 |

> 注：本版本已移除主机 bcrypt 生成环节（原 5-15 分钟），改为由 api 镜像在启动前自动生成（零额外下载），部署时长显著缩短。

---

## Ubuntu 部署

### 方式一：一键脚本（推荐）

适用于 Ubuntu 20.04 / 22.04 / 24.04 等所有受支持版本，包括桌面版和服务器版。

#### 步骤 1：克隆仓库

```bash
git clone https://github.com/dameng2026/xianyupilot.git
cd xianyupilot
```

如果未安装 git：

```bash
sudo apt update && sudo apt install -y git
```

#### 步骤 2：运行启动脚本

```bash
sh ./start.sh
```

脚本会自动完成以下所有工作：

1. **检测 Docker**：若未安装，自动调用 Docker 官方 `get.docker.com` 脚本安装（约 1 分钟），并启动 Docker 服务
2. **磁盘空间预检查**：检查当前分区可用空间，不足 5GB 时警告并给出清理建议
3. **首次初始化**：调用 `scripts/setup-wizard.sh`
   - 在 `./secrets/` 目录生成 7 组随机密钥（MySQL root/app/migration、Redis、JWT、Cookie、Internal Token，均 Base64URL 编码，长度 ≥32 字符）
   - 生成 4 个空的可选密钥文件（商业版桥接、AI、地图等，未启用时为空）
   - 创建空的 `admin-password-hash` 文件（由 api 镜像在启动前自动生成 bcrypt hash，无需主机 Python）
   - 从 `.env.example` 复制生成 `.env`
4. **端口冲突自动处理**：若 8080 被占用，自动尝试 8081-8089，找到可用端口后自动更新 `.env`
5. **镜像源连通性检测**：拉取前检测阿里云 ACR 是否可达，不可达时直接本地构建（避免拉取超时）
6. **拉取镜像或本地构建**：优先尝试拉取 ACR 预构建镜像（国内拉取快）；若拉取失败或镜像源不可达，自动回退到本地源码构建
7. **bcrypt hash 自动生成**：用已就绪的 api 镜像生成 admin 密码 hash（零额外下载，必定成功）
8. **启动 7 个服务**：mysql、redis、migrate（一次性）、crawler、api、worker、web
9. **分阶段健康检查**：按依赖顺序检查 MySQL → migrate → Redis → API → Web，每阶段显示进度和耗时
10. **失败自动诊断**：任一阶段失败时自动收集容器状态、异常服务日志、磁盘空间、端口占用，生成诊断报告

#### 步骤 3：访问服务

脚本完成后会输出访问地址：

```
✓ 服务已就绪

访问地址：
  本机：    http://localhost:8080
  局域网：  http://<服务器IP>:8080

默认账号：admin
默认密码：admin123（首次启动时由脚本生成，请尽快修改）
```

浏览器打开 http://服务器IP:8080 即可访问。

#### 自定义 admin 密码（可选）

```bash
ADMIN_PASSWORD="你的强密码" sh ./start.sh
```

#### 启动参数（可选）

| 参数 | 作用 |
|---|---|
| `sh ./start.sh` | 默认：拉取镜像并启动（推荐） |
| `sh ./start.sh --build` | 强制本地源码构建（适用于自定义修改或离线场景） |
| `sh ./start.sh --no-pull` | 跳过镜像拉取，使用本地已有镜像 |

### 方式二：手动分步

适合想了解每个步骤细节的用户。

#### 步骤 1：安装 Docker

```bash
# 方法 A：使用官方脚本（推荐）
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker

# 方法 B：使用 apt
sudo apt update
sudo apt install -y docker.io docker-compose-plugin
sudo systemctl enable --now docker
```

验证安装：

```bash
docker --version
docker compose version
```

#### 步骤 2：克隆仓库

```bash
git clone https://github.com/dameng2026/xianyupilot.git
cd xianyupilot
```

#### 步骤 3：运行初始化向导

```bash
sh ./scripts/setup-wizard.sh
```

向导会生成所有 secrets 文件和 `.env` 配置。

#### 步骤 4：启动服务

```bash
docker compose up -d
```

首次启动会构建镜像（约 5-7 分钟）。如需查看构建进度：

```bash
docker compose up -d --build 2>&1 | tail -20
```

#### 步骤 5：等待服务就绪

```bash
# 方法 A：使用 curl 检查健康端点
curl -fsS http://127.0.0.1:8080/readyz

# 方法 B：查看容器状态
docker compose ps
```

当所有服务显示 `healthy` 时即部署成功。

---

## Windows 部署

### 方式一：一键脚本（推荐）

适用于 Windows 10 1809+ / Windows 11 / Windows Server 2019+。

#### 步骤 1：安装 Docker Desktop

下载并安装 [Docker Desktop for Windows](https://docs.docker.com/desktop/install/windows-install/)。

安装完成后启动 Docker Desktop，等待右下角 Docker 图标变为绿色（表示 Docker 引擎已运行）。

> **注意**：Windows 上必须使用 WSL 2 后端（Docker Desktop 默认启用），不要使用 Hyper-V 后端。

#### 步骤 2：克隆仓库

打开 PowerShell 或 CMD：

```powershell
git clone https://github.com/dameng2026/xianyupilot.git
cd xianyupilot
```

如果未安装 git，从 [git-scm.com](https://git-scm.com/download/win) 下载安装。

#### 步骤 3：运行启动脚本

```powershell
.\start.bat
```

或在 CMD 中：

```cmd
start.bat
```

脚本会自动完成以下工作：

1. **检测 Docker**：若未安装会提示安装；若已安装但未运行，**自动启动 Docker Desktop** 并等待就绪（最长 90 秒）
2. **磁盘空间预检查**：检查当前分区可用空间，不足 5GB 时警告并给出清理建议
3. **首次初始化**：调用 `scripts/setup-wizard.ps1`（PowerShell 脚本，自动用 `-ExecutionPolicy Bypass` 启动，无需修改系统策略）
   - 生成 7 组随机 secrets（使用系统内置 RNG，无需 Python）
   - 创建空的 `admin-password-hash` 文件（由 api 镜像在启动前自动生成 bcrypt hash）
   - 从 `.env.example` 复制生成 `.env`
4. **端口冲突自动处理**：若 8080 被占用，自动尝试 8081-8089，找到可用端口后自动更新 `.env`
5. **镜像源连通性检测**：拉取前检测阿里云 ACR 是否可达，不可达时直接本地构建
6. **拉取镜像或本地构建**：与 Linux 版本一致
7. **bcrypt hash 自动生成**：用已就绪的 api 镜像生成（零额外下载，必定成功）
8. **启动 7 个服务**
9. **分阶段健康检查**：MySQL → migrate → Redis → API → Web，每阶段显示进度
10. **失败自动诊断**：任一阶段失败时自动收集容器状态、异常服务日志、磁盘空间、端口占用

#### 步骤 4：访问服务

浏览器打开 http://localhost:8080

```
默认账号：admin
默认密码：admin123
```

#### 自定义 admin 密码（可选）

```powershell
$env:ADMIN_PASSWORD="你的强密码"; .\start.bat
```

#### 启动参数

| 参数 | 作用 |
|---|---|
| `.\start.bat` | 默认：拉取镜像并启动 |
| `.\start.bat --build` | 强制本地源码构建 |
| `.\start.bat --no-pull` | 跳过镜像拉取 |

### 方式二：手动分步

#### 步骤 1：安装 Docker Desktop

参考[方式一 步骤 1](#步骤-1安装-docker-desktop)。

#### 步骤 2：克隆仓库

```powershell
git clone https://github.com/dameng2026/xianyupilot.git
cd xianyupilot
```

#### 步骤 3：运行初始化向导

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-wizard.ps1
```

#### 步骤 4：启动服务

```powershell
docker compose up -d
```

首次启动会构建镜像（约 5-10 分钟，取决于网络和磁盘速度）。

#### 步骤 5：等待服务就绪

```powershell
# 检查健康端点
curl http://127.0.0.1:8080/readyz

# 查看容器状态
docker compose ps
```

---

## 首次登录与配置

### 1. 登录管理后台

浏览器打开 http://服务器IP:8080，使用默认账号登录：

- 用户名：`admin`
- 密码：`admin123`

### 2. 修改默认密码

登录后立即修改密码：

1. 进入"系统设置" → "账户管理"
2. 修改 admin 密码为强密码

### 3. 配置闲鱼账号

进入"账号管理"，添加闲鱼账号的 Cookie 即可开始使用。

---

## 运维命令

所有运维操作通过 `scripts/production_ops.py` 包装器执行，避免直接调用复杂的 docker compose 参数。

### 查看服务状态

```bash
# Linux / macOS
python3 scripts/production_ops.py --env-file .env status

# Windows
python scripts/production_ops.py --env-file .env status
```

输出示例：

```
NAME                         IMAGE                      STATUS
xianyu-assistant-api-1       xianyu-assistant-api        Up 2 minutes (healthy)
xianyu-assistant-crawler-1   xianyu-assistant-crawler    Up 2 minutes (healthy)
xianyu-assistant-mysql-1     mysql:8.0                   Up 2 minutes (healthy)
xianyu-assistant-redis-1     redis:7.4-alpine            Up 2 minutes (healthy)
xianyu-assistant-web-1       xianyu-assistant-web        Up 2 minutes (healthy)
xianyu-assistant-worker-1    xianyu-assistant-api        Up 2 minutes (healthy)
xianyu-assistant-migrate-1   xianyu-assistant-api        Exited (0) 2 minutes ago
```

### 查看日志

```bash
# 查看最近 200 行 API 和 Web 日志
python3 scripts/production_ops.py --env-file .env logs --tail 200 api web

# 持续跟随 API 日志（类似 tail -f）
python3 scripts/production_ops.py --env-file .env logs --follow --tail 200 api

# 查看所有服务最近 100 行日志
python3 scripts/production_ops.py --env-file .env logs --tail 100
```

允许的服务名：`mysql`、`redis`、`migrate`、`api`、`worker`、`crawler`、`web`

### 重启服务

```bash
# 重启所有服务
python3 scripts/production_ops.py --env-file .env restart

# 只重启 API 和 Worker
python3 scripts/production_ops.py --env-file .env restart api worker
```

### 停止服务

```bash
# 停止并移除容器与网络（保留数据卷和镜像）
python3 scripts/production_ops.py --env-file .env stop

# 同时删除数据卷（MySQL/Redis 数据将永久丢失，慎用！）
python3 scripts/production_ops.py --env-file .env stop --volumes
```

### 更新到最新版

```bash
# Linux / macOS
git pull
sh ./start.sh

# Windows
git pull
.\start.bat
```

---

## 常见问题

### Q1：端口 8080 被占用

**现象**：启动时提示端口被占用。

**解决**：本版本已自动处理——脚本会自动尝试 8081-8089，找到可用端口后自动更新 `.env` 并继续启动。如需手动指定：

```bash
# Linux / macOS
sed -i 's/^WEB_PORT=8080/WEB_PORT=8090/' .env
sh ./start.sh --no-pull

# Windows：用文本编辑器打开 .env，将 WEB_PORT=8080 改为 WEB_PORT=8090
.\start.bat --no-pull
```

### Q2：Docker 安装失败

**现象**：`get.docker.com` 脚本执行失败。

**解决**：手动安装 Docker：

- **Ubuntu**：参考 [Docker 官方文档](https://docs.docker.com/engine/install/ubuntu/)
- **Windows**：下载 [Docker Desktop](https://docs.docker.com/desktop/install/windows-install/)

### Q3：bcrypt hash 生成失败（NAS / 离线环境）

**现象**：setup-wizard 提示"admin-password-hash 将由 api 镜像自动生成"，或启动时 bcrypt 相关错误。

**原因**：旧版本在主机生成 bcrypt hash，需要 Python + bcrypt 包，NAS/离线环境经常失败。

**解决**：本版本已彻底解决——**不再在主机生成 bcrypt hash**。setup-wizard 只创建空文件，`start.sh` / `start.bat` 会在 api 镜像就绪后自动用它生成 hash（api 镜像内一定有 bcrypt，零额外下载，必定成功）。正常情况下无需任何手动操作。

若 api 镜像生成也失败（极罕见，通常是镜像损坏），按以下顺序尝试：

**方法 A：重新构建 api 镜像**

```bash
# Linux / macOS
sh ./start.sh --build    # 强制重新构建，构建后自动生成 hash

# Windows
.\start.bat --build
```

**方法 B：手动用 api 镜像生成 hash 写入文件**

```bash
# 用已构建的 api 镜像生成（最可靠）
docker run --rm xianyu-assistant-api python -c \
  "import bcrypt; print(bcrypt.hashpw(b'admin123', bcrypt.gensalt(rounds=12)).decode())" \
  > ./secrets/admin-password-hash

# 验证文件内容（应以 $2b$ 开头）
cat ./secrets/admin-password-hash

# 重新启动
sh ./start.sh --no-pull
```

**方法 C：完全重新初始化**

```bash
# Linux / macOS
rm -rf secrets .env
sh ./start.sh

# Windows
rmdir /s /q secrets
del .env
.\start.bat
```

### Q4：镜像拉取失败（denied / 超时）

**现象**：`Error response from daemon: error from registry: denied` 或拉取超时。

**原因**：镜像未发布、命名空间不匹配，或镜像源网络不可达。本项目默认使用**阿里云 ACR**（国内拉取快），GHCR 作为海外备用源。ACR 仓库已设为公开，开源用户无需 `docker login` 即可直接 `docker pull`。

**解决**：`start.sh` / `start.bat` 已自动处理——拉取前会检测镜像源连通性，不可达时直接本地构建。如仍失败：

```bash
sh ./start.sh --build          # Linux / macOS
.\start.bat --build            # Windows
```

#### 切换镜像源（ACR ↔ GHCR）

默认拉取阿里云 ACR。如需切换到 GHCR（海外部署）或 ACR 拉取失败，编辑 `.env`：

```bash
# 取消注释并改为 GHCR（海外部署推荐）
API_IMAGE=ghcr.io/xianyu-assistant-opensource/xianyu-assistant-api:latest
WEB_IMAGE=ghcr.io/xianyu-assistant-opensource/xianyu-assistant-web:latest
CRAWLER_IMAGE=ghcr.io/xianyu-assistant-opensource/xianyu-assistant-crawler:latest
```

切换后重新拉取：

```bash
sh ./start.sh --no-pull    # 用已拉取/构建的镜像启动
# 或
sh ./start.sh              # 重新尝试拉取
```

#### 国内网络优化（镜像加速）

默认 ACR 镜像源已是国内节点，通常无需额外加速。若本地构建慢（npm/pip 下载慢），可配置 Docker registry mirror：

**1. 配置 Docker registry mirror（加速基础镜像拉取）**

```bash
# Linux：编辑 /etc/docker/daemon.json
sudo tee /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://dockerproxy.com",
    "https://docker.nju.edu.cn",
    "https://docker.1panel.live"
  ]
}
EOF
sudo systemctl restart docker
```

Windows 用户在 Docker Desktop → Settings → Docker Engine 中添加上述 `registry-mirrors` 配置。

**2. 使用代理拉取预构建镜像**

```bash
# 设置 Docker 代理（Linux）
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/http-proxy.conf <<'EOF'
[Service]
Environment="HTTPS_PROXY=http://127.0.0.1:7890"
Environment="NO_PROXY=localhost,127.0.0.1"
EOF
sudo systemctl daemon-reload
sudo systemctl restart docker
```

### Q5：忘记 admin 密码

**解决**：重新生成 bcrypt hash：

```bash
# Linux / macOS（主机已装 Python bcrypt）
python3 -c "import bcrypt; print(bcrypt.hashpw(b'你的新密码', bcrypt.gensalt(rounds=12)).decode())" > ./secrets/admin-password-hash

# 或删除文件后重跑向导（会重新生成默认密码 admin123）
rm ./secrets/admin-password-hash
sh ./scripts/setup-wizard.sh

# 重启服务
python3 scripts/production_ops.py --env-file .env restart api
```

### Q6：局域网其他机器无法访问

**检查项**：

1. `.env` 中 `WEB_BIND_ADDRESS=0.0.0.0`（默认即是）
2. 防火墙放行 8080 端口：
   ```bash
   # Ubuntu
   sudo ufw allow 8080/tcp

   # Windows：在 Windows Defender 防火墙中添加入站规则
   ```
3. 服务器安全组放行 8080 端口（云服务器）

### Q7：公网访问返回 400 错误

**原因**：`TRUSTED_HOSTS` 默认只允许 `localhost,127.0.0.1,api`，公网 IP 不在白名单中。

**解决**：编辑 `.env`，将公网 IP 或域名加入 `TRUSTED_HOSTS`：

```bash
TRUSTED_HOSTS=your-domain.com,154.x.x.x,localhost,127.0.0.1,api
```

重启服务：

```bash
python3 scripts/production_ops.py --env-file .env restart api web
```

### Q8：MySQL 启动失败（Permission denied）

**现象**：MySQL 容器日志显示 `/run/secrets/mysql_root_password: Permission denied`

**原因**：secrets 文件权限过严。

**解决**：本版本已修复此问题（secrets 文件权限为 0644）。若仍遇到，手动修复：

```bash
chmod 644 ./secrets/*
python3 scripts/production_ops.py --env-file .env restart mysql
```

### Q9：想完全重新初始化

```bash
# 备份当前数据（可选）
docker compose exec -T mysql mysqldump -uroot -p"$(cat ./secrets/mysql-root-password)" xianyu_opensource > backup.sql

# 停止并删除所有数据
python3 scripts/production_ops.py --env-file .env stop --volumes

# 删除配置和 secrets
rm -f .env
rm -rf secrets

# 重新启动
sh ./start.sh
```

### Q10：Windows 上 PowerShell 执行策略受限

**现象**：运行 `start.bat` 时提示 PowerShell 脚本无法执行。

**解决**：`start.bat` 已自动用 `-ExecutionPolicy Bypass` 参数启动 `setup-wizard.ps1`，无需手动修改系统策略。若仍失败，手动执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\setup-wizard.ps1
```

### Q11：磁盘空间不足

**现象**：构建或启动时提示 `no space left on device`

**解决**：

```bash
# 清理未使用的 Docker 镜像和缓存
docker system prune -a --volumes

# 查看磁盘占用
docker system df
df -h
```

---

## 进阶配置

### 配置反向代理（HTTPS）

生产环境建议在前面加一层 Nginx/Caddy 提供 HTTPS：

#### Nginx 配置示例

```nginx
server {
    listen 443 ssl http2;
    server_name your-domain.com;

    ssl_certificate     /etc/ssl/certs/your-cert.pem;
    ssl_certificate_key /etc/ssl/private/your-key.pem;

    client_max_body_size 12m;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # WebSocket 支持（闲鱼消息推送需要）
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 86400;
    }
}

# HTTP 强制跳转 HTTPS
server {
    listen 80;
    server_name your-domain.com;
    return 301 https://$server_name$request_uri;
}
```

配置完成后，修改 `.env`：

```bash
WEB_BIND_ADDRESS=127.0.0.1          # 仅监听本机，由 Nginx 转发
TRUSTED_HOSTS=your-domain.com,localhost,127.0.0.1,api
```

重启服务：

```bash
python3 scripts/production_ops.py --env-file .env restart api web
```

### 启用 AI 客服

编辑 `.env`：

```bash
AI_PROVIDER_ENABLED=true
AI_PROVIDER_BASE_URL=https://api.openai.com/v1
AI_PROVIDER_MODEL=gpt-4o-mini
```

将 API Key 写入 secrets 文件：

```bash
echo -n "sk-your-api-key" > ./secrets/ai-provider-api-key
```

重启 API 和 Worker：

```bash
python3 scripts/production_ops.py --env-file .env restart api worker
```

### 数据备份

```bash
# 备份 MySQL 数据
docker compose exec -T mysql sh -c 'MYSQL_PWD="$(cat /run/secrets/mysql_root_password)" mysqldump -uroot xianyu_opensource' > backup_$(date +%Y%m%d).sql

# 备份 secrets 目录（包含所有密钥）
tar -czf secrets_backup_$(date +%Y%m%d).tar.gz secrets/ .env

# 恢复：将备份文件解压到新服务器，运行 sh ./start.sh --no-pull 即可
```

建议设置定时任务（cron）每日备份：

```bash
# 编辑 crontab
crontab -e

# 每天凌晨 3 点备份
0 3 * * * cd /path/to/xianyupilot && docker compose exec -T mysql sh -c 'MYSQL_PWD="$(cat /run/secrets/mysql_root_password)" mysqldump -uroot xianyu_opensource' > /backup/xianyu_$(date +\%Y\%m\%d).sql
```

---

## 卸载与清理

### 完全卸载（保留数据备份）

```bash
# 1. 停止并移除容器
python3 scripts/production_ops.py --env-file .env stop

# 2. 删除项目目录
cd ..
rm -rf xianyupilot

# 3. 可选：卸载 Docker（Ubuntu）
sudo apt-get purge -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo rm -rf /var/lib/docker /var/lib/containerd /etc/docker
sudo apt-get autoremove -y
```

### 完全卸载（删除所有数据）

```bash
# 1. 停止并删除容器和数据卷
python3 scripts/production_ops.py --env-file .env stop --volumes

# 2. 删除项目目录
cd ..
rm -rf xianyupilot

# 3. 清理 Docker 镜像
docker rmi $(docker images --format '{{.Repository}}:{{.Tag}}' | grep xianyu-assistant) 2>/dev/null || true

# 4. 可选：卸载 Docker
sudo apt-get purge -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo rm -rf /var/lib/docker /var/lib/containerd /etc/docker
sudo apt-get autoremove -y
```

---

## 附录

### 服务架构

| 服务 | 端口 | 作用 | 是否暴露 |
|---|---|---|---|
| **web** | 8080 | 前端 Web 界面 | 是（默认 0.0.0.0:8080） |
| **api** | 12401 | 后端 API 服务 | 否（仅容器内） |
| **worker** | - | 后台任务调度 | 否 |
| **crawler** | 3001 | 闲鱼页面爬取 | 否（仅容器内） |
| **mysql** | 3306 | 数据库 | 否（仅容器内） |
| **redis** | 6379 | 缓存与队列 | 否（仅容器内） |
| **migrate** | - | 一次性数据库迁移 | 启动后退出 |

### Secrets 文件清单

首次启动时由 `setup-wizard` 自动生成在 `./secrets/` 目录：

| 文件 | 用途 | 是否必需 |
|---|---|---|
| `admin-password-hash` | admin 用户 bcrypt hash | ✅ |
| `mysql-root-password` | MySQL root 密码 | ✅ |
| `mysql-app-password` | MySQL 应用账号密码 | ✅ |
| `mysql-migration-password` | MySQL 迁移账号密码 | ✅ |
| `redis-password` | Redis 密码 | ✅ |
| `jwt-secret` | JWT 签名密钥 | ✅ |
| `cookie-crypto-secret` | Cookie 加密密钥 | ✅ |
| `internal-api-token` | 内部服务间通信令牌 | ✅ |
| `commercial-backend-access-token` | 商业版桥接令牌 | 可选（留空禁用） |
| `embedding-api-key` | 嵌入模型 API Key | 可选（留空禁用） |
| `ai-provider-api-key` | AI 大模型 API Key | 可选（留空禁用） |

### 技术支持

- **GitHub Issues**：[提交问题](https://github.com/dameng2026/xianyupilot/issues)
- **查看日志**：`python3 scripts/production_ops.py --env-file .env logs --tail 500`
- **查看版本**：进入"关于我们"页面查看当前版本和更新信息
