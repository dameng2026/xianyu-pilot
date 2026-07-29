#!/bin/sh
# 一键启动脚本：自动初始化 secrets、拉取镜像、启动服务、等待健康。
# 用法：
#   sh ./start.sh              # 拉取预构建镜像并启动（推荐）
#   sh ./start.sh --build      # 本地源码构建并启动
#   sh ./start.sh --no-pull    # 跳过镜像拉取（用本地已有的镜像）
#
# 错误兜底策略：
#   - Docker 未安装 → 自动安装（get.docker.com，国内网络用阿里云镜像）
#   - secrets 生成失败 → setup-wizard.sh 只需 openssl/head
#   - bcrypt hash 生成 → 统一由 api 镜像生成（零额外下载，必定成功）
#   - 镜像拉取失败 → 自动回退到本地源码构建
#   - 端口被占用 → 自动查找可用端口并更新 .env
#   - 磁盘空间不足 → 提前警告并给出清理建议
#   - 容器启动失败 → 自动诊断并显示异常服务日志
#   - 健康检查超时 → 分阶段显示进度，失败时自动诊断
#
# 注意：不使用 `set -e`，因为部署流程中某些步骤失败时有兜底方案，不应立即终止。

set -u

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"

DO_BUILD=0
DO_PULL=1
for arg in "$@"; do
  case "$arg" in
    --build)   DO_BUILD=1; DO_PULL=0 ;;
    --no-pull) DO_PULL=0 ;;
    *) echo "未知参数：$arg（支持：--build 本地构建 / --no-pull 跳过拉取）" >&2; exit 1 ;;
  esac
done

color() { printf '\033[%sm%s\033[0m' "$1" "$2"; }
info()  { printf '%s %s\n' "$(color '1;36' '•')" "$*"; }
ok()    { printf '%s %s\n' "$(color '1;32' '✓')" "$*"; }
warn()  { printf '%s %s\n' "$(color '1;33' '!')" "$*" >&2; }
die()   { printf '%s %s\n' "$(color '1;31' '✗')" "$*" >&2; exit 1; }

# 当前时间戳（用于阶段计时）
now_ms() { date +%s; }

# 格式化耗时秒数
elapsed_str() {
  start=$1
  end=$2
  printf '%ds' $((end - start))
}

# 自动安装 Docker（Ubuntu/Debian/CentOS/RHEL/Fedora 等，用官方 get.docker.com 脚本）
ensure_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return 0
  fi

  # 检测发行版
  os_id="unknown"
  if [ -f /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    os_id="${ID:-unknown}"
  fi

  # 非 root 用户检查
  if [ "$(id -u)" != "0" ]; then
    if command -v sudo >/dev/null 2>&1; then
      SUDO=sudo
    else
      die "Docker 未安装且当前用户非 root，也未安装 sudo。请让管理员安装 Docker：https://docs.docker.com/get-docker/"
    fi
  else
    SUDO=""
  fi

  warn "未检测到 Docker，尝试自动安装（发行版：$os_id）..."
  info "使用 Docker 官方安装脚本 get.docker.com（约 1-2 分钟）..."

  # 下载官方安装脚本
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com -o /tmp/get-docker.sh || die "下载 get.docker.sh 失败，请检查网络或手动安装：https://docs.docker.com/get-docker/"
  elif command -v wget >/dev/null 2>&1; then
    wget -qO /tmp/get-docker.sh https://get.docker.com || die "下载 get.docker.sh 失败，请检查网络或手动安装：https://docs.docker.com/get-docker/"
  else
    die "未检测到 curl/wget，请手动安装 Docker：https://docs.docker.com/get-docker/"
  fi

  # 执行安装
  sh /tmp/get-docker.sh || die "Docker 安装失败，请查看上方输出或手动安装"
  rm -f /tmp/get-docker.sh

  # 启动 Docker 服务（systemd 系统）
  if command -v systemctl >/dev/null 2>&1; then
    $SUDO systemctl enable docker 2>/dev/null || true
    $SUDO systemctl start docker 2>/dev/null || true
  fi

  # 非 root 用户加入 docker 组（需重新登录生效，本次先用 sudo）
  if [ "$(id -u)" != "0" ]; then
    $SUDO usermod -aG docker "$(whoami)" 2>/dev/null || true
    info "已将当前用户加入 docker 组，重新登录后可直接使用 docker 命令"
    # 本次会话用 sudo 调用 docker
    if [ -z "$SUDO" ]; then
      die "无法配置 docker 命令访问权限"
    fi
  fi

  # 等待 Docker daemon 就绪
  info "等待 Docker daemon 就绪..."
  docker_ok=0
  i=0
  while [ "$i" -lt 30 ]; do
    if $SUDO docker info >/dev/null 2>&1; then
      docker_ok=1
      break
    fi
    sleep 1
    i=$((i + 1))
  done

  if [ "$docker_ok" = "0" ]; then
    die "Docker 已安装但 daemon 未就绪，请检查：systemctl status docker"
  fi

  ok "Docker 安装完成"
}

# 用 api 镜像兜底生成 admin bcrypt hash（api 镜像内一定有 bcrypt，是最可靠的方案）
generate_admin_hash_with_api_image() {
  if [ -s "./secrets/admin-password-hash" ]; then
    # 文件非空，检查是否是有效的 bcrypt hash
    if grep -qE '^\$2[aby]\$' "./secrets/admin-password-hash" 2>/dev/null; then
      return 0
    fi
  fi

  info "用 api 镜像生成 admin bcrypt hash（零额外下载）..."
  ADMIN_PASSWORD="${ADMIN_PASSWORD:-admin123}"
  export ADMIN_PASSWORD

  # 获取 api 镜像名（从 docker-compose.yml 或默认值）
  api_image=$($DOCKER_COMPOSE config --images 2>/dev/null | grep -i 'api' | head -1) || api_image=""
  if [ -z "$api_image" ]; then
    api_image="xianyu-assistant-api"
  fi

  # 用 api 镜像生成 hash
  hash_value=$(docker run --rm -e ADMIN_PASSWORD "$api_image" python -c '
import os, bcrypt
pw = os.environ["ADMIN_PASSWORD"].encode("utf-8")
print(bcrypt.hashpw(pw, bcrypt.gensalt(rounds=12)).decode())
' 2>/dev/null) || hash_value=""

  if [ -n "$hash_value" ] && echo "$hash_value" | grep -qE '^\$2[aby]\$'; then
    printf '%s' "$hash_value" > "./secrets/admin-password-hash"
    chmod 644 "./secrets/admin-password-hash" 2>/dev/null || true
    ok "admin bcrypt hash 生成成功（用 api 镜像）"
    unset ADMIN_PASSWORD
    return 0
  else
    warn "api 镜像生成 hash 失败"
    unset ADMIN_PASSWORD
    return 1
  fi
}

# 检查端口是否被占用
check_port_available() {
  port=$1
  if command -v ss >/dev/null 2>&1; then
    if ss -tlnp 2>/dev/null | grep -q ":${port} "; then
      return 1
    fi
  elif command -v netstat >/dev/null 2>&1; then
    if netstat -tlnp 2>/dev/null | grep -q ":${port} "; then
      return 1
    fi
  fi
  return 0
}

# 查找可用端口：从 base_port 开始，依次尝试到 base_port+9
# 找到可用端口后写入 .env 的 WEB_PORT，并返回该端口
find_available_port() {
  base_port=$1
  for p in $(seq "$base_port" $((base_port + 9))); do
    if check_port_available "$p"; then
      echo "$p"
      return 0
    fi
  done
  echo ""
  return 1
}

# 更新 .env 中的 WEB_PORT
update_env_web_port() {
  new_port=$1
  if [ -f .env ]; then
    # sed -i 在 macOS 和 Linux 上行为不同，用临时文件兼容
    if grep -q '^WEB_PORT=' .env 2>/dev/null; then
      sed "s/^WEB_PORT=.*/WEB_PORT=$new_port/" .env > .env.tmp && mv .env.tmp .env
    else
      printf 'WEB_PORT=%s\n' "$new_port" >> .env
    fi
  fi
}

# 磁盘空间预检查（建议 ≥10GB）
check_disk_space() {
  # 获取项目所在分区的可用空间（KB）
  avail_kb=0
  if command -v df >/dev/null 2>&1; then
    # BSD 和 GNU 兼容的写法
    avail_kb=$(df -Pk . 2>/dev/null | awk 'NR==2{print $4}') || avail_kb=0
  fi
  avail_gb=$((avail_kb / 1024 / 1024))

  if [ "$avail_kb" -gt 0 ]; then
    if [ "$avail_gb" -lt 5 ]; then
      warn "磁盘可用空间不足：${avail_gb}GB（建议 ≥10GB）"
      info "镜像构建 + 数据可能需要 5-10GB，空间不足会导致构建中途失败"
      info "清理建议：docker system prune -a --volumes（清理未使用的 Docker 资源）"
      # 不终止，让用户自己决定
    elif [ "$avail_gb" -lt 10 ]; then
      info "磁盘可用空间：${avail_gb}GB（建议 ≥10GB，首次构建可能紧张）"
    else
      ok "磁盘可用空间：${avail_gb}GB"
    fi
  fi
}

# 检测镜像仓库连通性（5秒超时）
# registry /v2/ 端点返回 401/403 表示需认证但 registry 可达；000 表示连接失败
# 参数 $1：registry 的 /v2/ 探测 URL，默认阿里云 ACR
check_registry_reachable() {
  local url="${1:-https://registry.cn-hangzhou.aliyuncs.com/v2/}"
  local code=""
  if command -v curl >/dev/null 2>&1; then
    code=$(curl -sS --max-time 5 -o /dev/null -w "%{http_code}" "$url" 2>/dev/null)
  elif command -v wget >/dev/null 2>&1; then
    code=$(wget --timeout=5 --server-response -qO /dev/null "$url" 2>&1 \
      | grep -oE 'HTTP/[0-9.]+ [0-9]+' | tail -1 | grep -oE '[0-9]+$')
  fi
  case "$code" in
    200|401|403) return 0 ;;
    *) return 1 ;;
  esac
}

# 失败自动诊断：收集关键信息供排查
diagnose_failure() {
  echo ""
  warn "========== 自动诊断 =========="
  # 1. 容器状态
  info "[1/4] 容器状态："
  $DOCKER_COMPOSE ps --format 'table {{.Service}}\t{{.Status}}' 2>/dev/null || $DOCKER_COMPOSE ps 2>/dev/null || echo "    无法获取容器状态"
  echo ""
  # 2. 异常服务最近日志
  info "[2/4] 异常服务最近日志（各 50 行）："
  for svc in mysql redis migrate api worker crawler web; do
    status=$($DOCKER_COMPOSE ps --format '{{.Status}}' "$svc" 2>/dev/null | head -1) || status=""
    if echo "$status" | grep -qiE 'exited|failed|unhealthy|restarting'; then
      echo "    --- $svc ($status) ---"
      $DOCKER_COMPOSE logs --tail 50 "$svc" 2>/dev/null | tail -30 | sed 's/^/    /'
      echo ""
    fi
  done
  # 3. 磁盘空间
  info "[3/4] 磁盘空间："
  df -h . 2>/dev/null | sed 's/^/    /' || echo "    无法获取磁盘信息"
  echo ""
  # 4. 端口占用
  info "[4/4] 端口占用（WEB_PORT=${WEB_PORT}）："
  if command -v ss >/dev/null 2>&1; then
    ss -tlnp 2>/dev/null | grep ":${WEB_PORT} " | sed 's/^/    /' || echo "    端口 ${WEB_PORT} 未被监听"
  elif command -v netstat >/dev/null 2>&1; then
    netstat -tlnp 2>/dev/null | grep ":${WEB_PORT} " | sed 's/^/    /' || echo "    端口 ${WEB_PORT} 未被监听"
  fi
  echo ""
  info "完整诊断命令："
  info "  全部日志：$DOCKER_COMPOSE logs --tail 200"
  info "  单服务：  $DOCKER_COMPOSE logs --tail 200 <服务名>"
  info "  完全重置：$DOCKER_COMPOSE down -v && rm -rf secrets .env && sh ./start.sh"
  warn "=============================="
}

print_access_info() {
  # 推断可访问地址
  if [ "$WEB_BIND" = "0.0.0.0" ]; then
    # 尝试获取本机 IP
    lan_ip=""
    if command -v hostname >/dev/null 2>&1; then
      lan_ip=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
    fi
    if command -v ip >/dev/null 2>&1; then
      lan_ip=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}' || true)
    fi

    echo "访问地址："
    echo "  本机：    http://localhost:${WEB_PORT}"
    [ -n "$lan_ip" ] && echo "  局域网：  http://${lan_ip}:${WEB_PORT}"
  else
    echo "访问地址：http://localhost:${WEB_PORT}"
  fi
  echo ""
  echo "默认账号：admin"
  echo "默认密码：admin123（首次启动时由脚本生成，请尽快修改）"
  echo ""
  echo "常用命令："
  echo "  查看状态：python scripts/production_ops.py --env-file .env status"
  echo "  查看日志：python scripts/production_ops.py --env-file .env logs --tail 200"
  echo "  停止服务：python scripts/production_ops.py --env-file .env stop"
  echo ""
  warn "默认绑定 0.0.0.0:${WEB_PORT}，暴露到公网前请在 .env 中配置 TRUSTED_HOSTS 和反向代理 TLS"
}

# ---------- 1. 前置依赖检查 + 自动安装 Docker ----------
SUDO=""
ensure_docker

# 如果是非 root 用户，后续 docker 命令需要 sudo
if [ "$(id -u)" != "0" ] && ! docker info >/dev/null 2>&1; then
  SUDO="sudo"
fi

DOCKER_COMPOSE="${SUDO} docker compose"

# ---------- 1.5 磁盘空间预检查 ----------
check_disk_space

# ---------- 2. 首次启动初始化 ----------
need_init=0
[ ! -f .env ] && need_init=1
[ ! -d ./secrets ] && need_init=1
[ -d ./secrets ] && [ -z "$(ls -A ./secrets 2>/dev/null)" ] && need_init=1
# 检查关键 secrets 文件是否齐全
for f in admin-password-hash mysql-root-password mysql-app-password mysql-migration-password \
         redis-password jwt-secret cookie-crypto-secret internal-api-token; do
  [ ! -s "./secrets/$f" ] && [ "$f" != "admin-password-hash" ] && need_init=1
done
# admin-password-hash 允许为空（由 api 镜像生成），但文件必须存在
[ ! -f "./secrets/admin-password-hash" ] && need_init=1

if [ "$need_init" = "1" ]; then
  info "首次启动，运行初始化向导..."
  sh ./scripts/setup-wizard.sh
fi

# ---------- 3. 加载 .env（用于读取 WEB_PORT 等变量） ----------
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

WEB_PORT="${WEB_PORT:-8080}"
WEB_BIND="${WEB_BIND_ADDRESS:-0.0.0.0}"

# ---------- 3.5 端口占用检查 + 自动选择可用端口 ----------
if ! check_port_available "$WEB_PORT"; then
  warn "端口 $WEB_PORT 已被占用，自动查找可用端口..."
  new_port=$(find_available_port $((WEB_PORT + 1))) || true
  if [ -n "$new_port" ]; then
    info "已自动切换到可用端口：$new_port"
    update_env_web_port "$new_port"
    WEB_PORT="$new_port"
    ok "已将 .env 中的 WEB_PORT 更新为 $WEB_PORT"
  else
    warn "端口 $WEB_PORT ~ $((WEB_PORT + 9)) 均被占用"
    info "解决方法：修改 .env 中的 WEB_PORT=其他端口（如 8090），然后重新运行 sh ./start.sh"
    info "或检查占用进程：ss -tlnp | grep :${WEB_PORT}"
    die "无可用端口"
  fi
fi

# ---------- 4. 拉取镜像或本地构建 ----------
# 先构建/拉取镜像（不启动服务），以便用 api 镜像兜底生成 bcrypt hash
build_ok=0
if [ "$DO_BUILD" = "1" ]; then
  info "本地源码构建镜像（首次约 5-10 分钟）..."
  if $DOCKER_COMPOSE build 2>&1; then
    build_ok=1
  else
    die "镜像构建失败。查看错误信息上方输出，或检查网络和磁盘空间"
  fi
else
  pull_ok=0
  if [ "$DO_PULL" = "1" ]; then
    # 检测镜像源连通性（默认阿里云 ACR），不可达则直接本地构建
    info "检测镜像源连通性（阿里云 ACR）..."
    if check_registry_reachable; then
      ok "镜像源可达"
      info "拉取最新镜像（首次约 3-5 分钟）..."
      if $DOCKER_COMPOSE pull 2>&1; then
        pull_ok=1
      else
        warn "镜像拉取失败（可能是镜像未发布或命名空间不匹配）"
        info "自动回退到本地源码构建（首次约 5-10 分钟）..."
        DO_BUILD=1
      fi
    else
      warn "镜像源不可达（网络或防火墙限制）"
      info "建议直接本地构建（避免拉取超时）..."
      info "如需使用预构建镜像，可在 .env 中切换 GHCR 镜像源或配置代理后重试"
      info "正在尝试本地构建..."
      DO_BUILD=1
    fi
  fi
  if [ "$pull_ok" = "1" ]; then
    build_ok=1
  else
    if $DOCKER_COMPOSE build 2>&1; then
      build_ok=1
    else
      die "镜像构建失败。查看错误信息上方输出，或检查网络和磁盘空间"
    fi
  fi
fi

# ---------- 4.5 兜底生成 admin bcrypt hash（用 api 镜像） ----------
# setup-wizard.sh 不再在主机生成 bcrypt hash（避免 pip install / Docker 临时镜像的耗时）
# api 镜像已经构建/拉取好，用它来生成 hash（api 镜像内一定有 bcrypt，零额外下载）
if [ ! -s "./secrets/admin-password-hash" ]; then
  generate_admin_hash_with_api_image || {
    warn "无法生成 admin bcrypt hash，服务可能无法启动"
    warn "手动生成方法："
    warn "  1) 用 api 镜像：docker run --rm xianyu-assistant-api python -c \"import bcrypt; print(bcrypt.hashpw(b'admin123', bcrypt.gensalt(rounds=12)).decode())\" > ./secrets/admin-password-hash"
    warn "  2) 重新运行: sh ./start.sh --no-pull"
    die "admin bcrypt hash 生成失败"
  }
fi

# ---------- 5. 启动服务 ----------
info "启动服务..."
$DOCKER_COMPOSE up -d || die "服务启动失败。查看日志：$DOCKER_COMPOSE logs"

# ---------- 6. 分阶段等待健康检查 ----------
info "等待服务就绪（分阶段，最长 5 分钟）..."

# curl 或 wget 二选一
HEALTH_URL="http://127.0.0.1:${WEB_PORT}/readyz"
HEALTH_CHECK=""
if command -v curl >/dev/null 2>&1; then
  HEALTH_CHECK="curl -fsS --max-time 3 '$HEALTH_URL' >/dev/null 2>&1"
elif command -v wget >/dev/null 2>&1; then
  HEALTH_CHECK="wget -qO- --timeout=3 '$HEALTH_URL' >/dev/null 2>&1"
else
  HEALTH_CHECK=""
fi

phase_start=$(now_ms)

# 阶段 1：等待 MySQL healthy
printf '  [1/5] MySQL...'
stage_start=$(now_ms)
mysql_ok=0
i=0
while [ "$i" -lt 60 ]; do
  status=$($DOCKER_COMPOSE ps --format '{{.Health}}' mysql 2>/dev/null | head -1) || status=""
  if [ "$status" = "healthy" ]; then
    mysql_ok=1
    break
  fi
  # 检查是否 exited
  s=$($DOCKER_COMPOSE ps --format '{{.Status}}' mysql 2>/dev/null | head -1) || s=""
  if echo "$s" | grep -qiE 'exited|failed'; then
    break
  fi
  printf '.'
  sleep 2
  i=$((i + 1))
done
stage_end=$(now_ms)
if [ "$mysql_ok" = "1" ]; then
  printf ' ✓ (%s)\n' "$(elapsed_str "$stage_start" "$stage_end")"
else
  printf ' ✗ 失败\n'
  warn "MySQL 启动失败，可能是磁盘空间不足或 secrets 权限问题"
  diagnose_failure
  exit 1
fi

# 阶段 2：等待 migrate 完成（一次性容器）
printf '  [2/5] 数据库迁移...'
stage_start=$(now_ms)
migrate_ok=0
i=0
while [ "$i" -lt 90 ]; do
  s=$($DOCKER_COMPOSE ps --format '{{.Status}}' migrate 2>/dev/null | head -1) || s=""
  if echo "$s" | grep -qiE 'exited.*code.*0|exited.*0'; then
    migrate_ok=1
    break
  fi
  if echo "$s" | grep -qiE 'exited.*[1-9]|failed'; then
    break
  fi
  printf '.'
  sleep 2
  i=$((i + 1))
done
stage_end=$(now_ms)
if [ "$migrate_ok" = "1" ]; then
  printf ' ✓ (%s)\n' "$(elapsed_str "$stage_start" "$stage_end")"
else
  printf ' ✗ 失败\n'
  warn "数据库迁移失败，可能是密码不匹配或迁移脚本错误"
  info "查看 migrate 日志：$DOCKER_COMPOSE logs --tail 100 migrate"
  diagnose_failure
  exit 1
fi

# 阶段 3：等待 Redis healthy
printf '  [3/5] Redis...'
stage_start=$(now_ms)
redis_ok=0
i=0
while [ "$i" -lt 30 ]; do
  status=$($DOCKER_COMPOSE ps --format '{{.Health}}' redis 2>/dev/null | head -1) || status=""
  if [ "$status" = "healthy" ]; then
    redis_ok=1
    break
  fi
  printf '.'
  sleep 2
  i=$((i + 1))
done
stage_end=$(now_ms)
if [ "$redis_ok" = "1" ]; then
  printf ' ✓ (%s)\n' "$(elapsed_str "$stage_start" "$stage_end")"
else
  printf ' ✗ 失败\n'
  warn "Redis 启动失败"
  diagnose_failure
  exit 1
fi

# 阶段 4：等待 API healthy
printf '  [4/5] API...'
stage_start=$(now_ms)
api_ok=0
i=0
while [ "$i" -lt 60 ]; do
  status=$($DOCKER_COMPOSE ps --format '{{.Health}}' api 2>/dev/null | head -1) || status=""
  if [ "$status" = "healthy" ]; then
    api_ok=1
    break
  fi
  s=$($DOCKER_COMPOSE ps --format '{{.Status}}' api 2>/dev/null | head -1) || s=""
  if echo "$s" | grep -qiE 'exited|failed'; then
    break
  fi
  printf '.'
  sleep 2
  i=$((i + 1))
done
stage_end=$(now_ms)
if [ "$api_ok" = "1" ]; then
  printf ' ✓ (%s)\n' "$(elapsed_str "$stage_start" "$stage_end")"
else
  printf ' ✗ 失败\n'
  warn "API 启动失败"
  info "查看 API 日志：$DOCKER_COMPOSE logs --tail 100 api"
  diagnose_failure
  exit 1
fi

# 阶段 5：等待 Web healthy + /readyz
printf '  [5/5] Web...'
stage_start=$(now_ms)
web_ok=0
i=0
while [ "$i" -lt 30 ]; do
  # 先检查容器健康
  status=$($DOCKER_COMPOSE ps --format '{{.Health}}' web 2>/dev/null | head -1) || status=""
  if [ "$status" = "healthy" ]; then
    # 再检查 /readyz
    if [ -n "$HEALTH_CHECK" ]; then
      if eval "$HEALTH_CHECK"; then
        web_ok=1
        break
      fi
    else
      web_ok=1
      break
    fi
  fi
  s=$($DOCKER_COMPOSE ps --format '{{.Status}}' web 2>/dev/null | head -1) || s=""
  if echo "$s" | grep -qiE 'exited|failed'; then
    break
  fi
  printf '.'
  sleep 2
  i=$((i + 1))
done
stage_end=$(now_ms)
total_end=$(now_ms)

if [ "$web_ok" = "1" ]; then
  printf ' ✓ (%s)\n' "$(elapsed_str "$stage_start" "$stage_end")"
  echo ""
  ok "服务已就绪（总耗时 $(elapsed_str "$phase_start" "$total_end")）"
  echo ""
  print_access_info
  exit 0
fi

echo ""
printf ' ✗ 失败\n'
warn "服务启动超时（Web 健康检查未通过）"
diagnose_failure
die "服务启动超时"
