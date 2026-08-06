#!/bin/sh
# 本地裸机部署初始化向导（Linux / macOS）
# 自动完成：预检环境 → 生成 .env 与随机密钥 → 生成 admin bcrypt hash
#          → 创建 MySQL 数据库与用户 → 安装 Python/Node 依赖 → 安装 Playwright Chromium
# 用法：
#   sh ./scripts/setup-local.sh                                        # 默认
#   MYSQL_ROOT_PASSWORD="xxx" sh ./scripts/setup-local.sh              # MySQL root 有密码
#   ADMIN_PASSWORD="your-pass" sh ./scripts/setup-local.sh             # 自定义 admin 密码（默认 admin123）
#   PIP_INDEX_URL="https://pypi.tuna.tsinghua.edu.cn/simple" sh ./scripts/setup-local.sh   # 国内 pip 镜像
#   PLAYWRIGHT_DOWNLOAD_HOST="https://npmmirror.com/mirrors/playwright" sh ./scripts/setup-local.sh  # 国内 playwright 镜像
# 说明：已存在的 .env / venv / node_modules 不会被重复生成或覆盖。

set -u

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"

VENV_DIR=".venv"
ENV_FILE=".env"
ENV_TEMPLATE=".env.development.example"
DEFAULT_ADMIN_PASSWORD="admin123"

color() { printf '\033[%sm%s\033[0m' "$1" "$2"; }
info()  { printf '%s %s\n' "$(color '1;36' '•')" "$*"; }
ok()    { printf '%s %s\n' "$(color '1;32' '✓')" "$*"; }
warn()  { printf '%s %s\n' "$(color '1;33' '!')" "$*" >&2; }
die()   { printf '%s %s\n' "$(color '1;31' '✗')" "$*" >&2; exit 1; }

# 生成随机 hex 字符串（用于 JWT/COOKIE/INTERNAL/MYSQL 等 secret）
gen_secret() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "${1:-32}"
  elif command -v head >/dev/null 2>&1; then
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n'
  else
    date +%s%N | sha256sum | cut -d' ' -f1
  fi
}

# 更新 .env 中的键值（不存在则追加）
set_env() {
  key=$1
  value=$2
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed "s|^${key}=.*|${key}=${value}|" "$ENV_FILE" > "$ENV_FILE.tmp" && mv "$ENV_FILE.tmp" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

# ---------- 1. 前置依赖检查 ----------
info "检查本地环境依赖..."
PYTHON_BIN=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
    PYTHON_BIN=$(command -v "$c")
    break
  fi
done
[ -z "$PYTHON_BIN" ] && die "未检测到 Python 3.10+，请先安装：https://www.python.org/downloads/ （apt: sudo apt install -y python3 python3-venv python3-pip）"
ok "Python：$($PYTHON_BIN --version 2>&1)"

for c in node npm; do
  command -v "$c" >/dev/null 2>&1 || die "未检测到 $c，请先安装 Node.js 22+：https://nodejs.org/ （apt: sudo apt install -y nodejs npm）"
done
NODE_MAJOR=$(node -e 'console.log(process.versions.node.split(".")[0])' 2>/dev/null)
[ "${NODE_MAJOR:-0}" -lt 18 ] && warn "检测到 Node.js v${NODE_MAJOR}，推荐 22+（最低 18）"
ok "Node.js：$(node --version) / npm：$(npm --version)"

command -v mysql >/dev/null 2>&1 || warn "未检测到 mysql 客户端，将无法自动创建数据库（可手动创建后重跑本脚本）"

# ---------- 2. 生成 .env ----------
if [ -f "$ENV_FILE" ]; then
  ok ".env 已存在（跳过生成）"
else
  [ -f "$ENV_TEMPLATE" ] || die "缺少 $ENV_TEMPLATE 模板文件"
  cp "$ENV_TEMPLATE" "$ENV_FILE"
  ok "已从 $ENV_TEMPLATE 创建 .env"
  set_env "JWT_SECRET"           "$(gen_secret 48)"
  set_env "COOKIE_CRYPTO_SECRET" "$(gen_secret 48)"
  set_env "INTERNAL_API_TOKEN"   "$(gen_secret 48)"
  set_env "MYSQL_PASSWORD"       "$(gen_secret 24)"
  info "已自动生成 JWT/COOKIE/INTERNAL/MYSQL 随机密钥"
fi

# ---------- 3. 创建 venv 并安装 API 依赖 ----------
if [ -x "$VENV_DIR/bin/python" ]; then
  ok "Python venv 已存在（.venv）"
else
  info "创建 Python 虚拟环境（.venv）..."
  "$PYTHON_BIN" -m venv "$VENV_DIR" || die "创建 venv 失败，请确认已安装 python3-venv（Ubuntu: sudo apt install -y python3-venv）"
  ok "venv 创建完成"
fi

if "$VENV_DIR/bin/python" -c 'import bcrypt, fastapi' >/dev/null 2>&1; then
  ok "API 依赖已安装（跳过）"
else
  info "安装 API 依赖（首次约 2-5 分钟，国内可设 PIP_INDEX_URL 加速）..."
  "$VENV_DIR/bin/pip" install -r apps/api/requirements.txt || die "API 依赖安装失败，请检查网络后重试"
  ok "API 依赖安装完成"
fi

# ---------- 4. 生成 admin bcrypt hash ----------
ADMIN_PASSWORD="${ADMIN_PASSWORD:-$DEFAULT_ADMIN_PASSWORD}"
CURRENT_HASH=$(grep -E '^ADMIN_PASSWORD_HASH=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2-)
if [ -n "$CURRENT_HASH" ] && echo "$CURRENT_HASH" | grep -qE '^\$2[aby]\$'; then
  ok "admin 密码 hash 已存在（跳过生成）"
else
  info "生成 admin 密码 hash（默认密码：$DEFAULT_ADMIN_PASSWORD，可用 ADMIN_PASSWORD 覆盖）..."
  HASH_VALUE=$(ADMIN_PASSWORD="$ADMIN_PASSWORD" "$VENV_DIR/bin/python" -c '
import os, bcrypt
pw = os.environ["ADMIN_PASSWORD"].encode("utf-8")
print(bcrypt.hashpw(pw, bcrypt.gensalt(rounds=12)).decode())
' 2>/dev/null) || HASH_VALUE=""
  if [ -n "$HASH_VALUE" ] && echo "$HASH_VALUE" | grep -qE '^\$2[aby]\$'; then
    set_env "ADMIN_PASSWORD_HASH" "$HASH_VALUE"
    ok "admin 密码 hash 已写入 .env（登录账号：admin）"
  else
    die "bcrypt hash 生成失败，请检查 .venv 中 bcrypt 是否安装"
  fi
fi

# ---------- 5. 创建 MySQL 数据库与用户 ----------
MYSQL_DATABASE=$(grep -E '^MYSQL_DATABASE=' "$ENV_FILE" | head -1 | cut -d= -f2-)
MYSQL_USER=$(grep -E '^MYSQL_USER=' "$ENV_FILE" | head -1 | cut -d= -f2-)
MYSQL_PASSWORD=$(grep -E '^MYSQL_PASSWORD=' "$ENV_FILE" | head -1 | cut -d= -f2-)
[ -z "$MYSQL_DATABASE" ] && MYSQL_DATABASE="xianyu_opensource"
[ -z "$MYSQL_USER" ] && MYSQL_USER="xianyu"

if command -v mysql >/dev/null 2>&1; then
  # 尝试 root 连接：免密 → 环境变量 → 交互输入
  ROOT_ARGS=""
  if mysql -uroot --skip-password -e "SELECT 1" >/dev/null 2>&1; then
    ROOT_ARGS="-uroot"
  elif [ -n "${MYSQL_ROOT_PASSWORD:-}" ] && mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SELECT 1" >/dev/null 2>&1; then
    ROOT_ARGS="-uroot -p${MYSQL_ROOT_PASSWORD}"
  else
    printf '请输入 MySQL root 密码（本机 root 无密码则直接回车）: '
    stty -echo 2>/dev/null
    IFS= read -r ROOT_INPUT
    stty echo 2>/dev/null
    echo ""
    if [ -n "$ROOT_INPUT" ]; then
      ROOT_ARGS="-uroot -p${ROOT_INPUT}"
    else
      ROOT_ARGS="-uroot"
    fi
    unset ROOT_INPUT
  fi

  if mysql $ROOT_ARGS -e "SELECT 1" >/dev/null 2>&1; then
    info "使用 MySQL root 连接创建数据库与用户..."
    mysql $ROOT_ARGS -e "
      CREATE DATABASE IF NOT EXISTS \`${MYSQL_DATABASE}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
      CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'localhost' IDENTIFIED BY '${MYSQL_PASSWORD}';
      CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'127.0.0.1' IDENTIFIED BY '${MYSQL_PASSWORD}';
      GRANT ALL PRIVILEGES ON \`${MYSQL_DATABASE}\`.* TO '${MYSQL_USER}'@'localhost';
      GRANT ALL PRIVILEGES ON \`${MYSQL_DATABASE}\`.* TO '${MYSQL_USER}'@'127.0.0.1';
      FLUSH PRIVILEGES;
    " && ok "数据库 ${MYSQL_DATABASE} 与用户 ${MYSQL_USER} 创建完成" \
      || warn "建库失败，请手动执行以下 SQL 后重跑本脚本"
  else
    warn "无法连接 MySQL root，请手动创建数据库与用户后重跑本脚本："
    echo ""
    echo "  CREATE DATABASE IF NOT EXISTS \`${MYSQL_DATABASE}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
    echo "  CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'localhost' IDENTIFIED BY '${MYSQL_PASSWORD}';"
    echo "  CREATE USER IF NOT EXISTS '${MYSQL_USER}'@'127.0.0.1' IDENTIFIED BY '${MYSQL_PASSWORD}';"
    echo "  GRANT ALL PRIVILEGES ON \`${MYSQL_DATABASE}\`.* TO '${MYSQL_USER}'@'localhost';"
    echo "  GRANT ALL PRIVILEGES ON \`${MYSQL_DATABASE}\`.* TO '${MYSQL_USER}'@'127.0.0.1';"
    echo "  FLUSH PRIVILEGES;"
    echo ""
  fi
else
  warn "未安装 mysql 客户端，请手动创建数据库与用户（SQL 见上方提示）"
fi

# ---------- 6. 安装 Node 依赖 ----------
if [ -d "apps/crawler/node_modules" ]; then
  ok "Crawler 依赖已安装（跳过）"
else
  info "安装 Crawler 依赖（npm install，首次约 1-3 分钟）..."
  (cd apps/crawler && npm install) || warn "Crawler 依赖安装失败"
  ok "Crawler 依赖安装完成"
fi

if [ -d "apps/web/node_modules" ]; then
  ok "Web 依赖已安装（跳过）"
else
  info "安装 Web 依赖（npm install，首次约 2-4 分钟）..."
  (cd apps/web && npm install) || warn "Web 依赖安装失败"
  ok "Web 依赖安装完成"
fi

# ---------- 7. 安装 Playwright Chromium ----------
if [ -d "$HOME/.cache/ms-playwright" ] || [ -d "$HOME/Library/Caches/ms-playwright" ]; then
  ok "Playwright Chromium 已安装（跳过）"
else
  info "安装 Playwright Chromium（约 150MB，国内可设 PLAYWRIGHT_DOWNLOAD_HOST 镜像加速）..."
  (cd apps/crawler && npm exec playwright install chromium) 2>/dev/null \
    || warn "Chromium 下载失败，可设置镜像后重试："
  if ! [ -d "$HOME/.cache/ms-playwright" ] && ! [ -d "$HOME/Library/Caches/ms-playwright" ]; then
    warn "  PLAYWRIGHT_DOWNLOAD_HOST=\"https://npmmirror.com/mirrors/playwright\" sh ./scripts/setup-local.sh"
  fi
fi

# ---------- 8. 完成 ----------
cat <<EOF

$(ok "本地初始化完成")

默认管理员账号：
  用户名：admin
  密码：${ADMIN_PASSWORD}（登录后请尽快修改）

下一步：
  启动服务：sh ./start-local.sh

$(warn "提示：本机需已安装并运行 MySQL 8 与 Redis 7（本机无密码 Redis 即可，有密码请编辑 .env 的 REDIS_PASSWORD）")
EOF
