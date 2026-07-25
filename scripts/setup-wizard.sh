#!/bin/sh
# 首次启动初始化向导：自动生成所有 secrets 文件和 .env。
# admin bcrypt hash 不在此处生成（避免主机 Python/pip/Docker 临时容器的多轮耗时下载），
# 而是由 start.sh 在 api 镜像就绪后用 api 容器统一兜底生成（api 镜像内一定有 bcrypt）。
# 安全设计：所有随机值都从 /dev/urandom 读取；secrets 目录权限 0700，
# 文件权限 0644（容器内非 root 用户需读取）。
# 已存在的文件不会被覆盖。
#
# 注意：不使用 `set -e`，保持与 start.sh 一致的容错策略。
# 使用 `set -u` 捕获未定义变量引用。

set -u

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$PROJECT_DIR"

SECRETS_DIR="./secrets"
DEFAULT_ADMIN_PASSWORD="admin123"

color() { printf '\033[%sm%s\033[0m' "$1" "$2"; }
info()  { printf '%s %s\n' "$(color '1;36' '•')" "$*"; }
ok()    { printf '%s %s\n' "$(color '1;32' '✓')" "$*"; }
warn()  { printf '%s %s\n' "$(color '1;33' '!')" "$*" >&2; }
die()   { printf '%s %s\n' "$(color '1;31' '✗')" "$*" >&2; exit 1; }

# ---------- 1. 前置依赖检查 ----------
# secrets 生成只需 openssl 或 head + base64，不依赖 Python/Docker
if ! command -v openssl >/dev/null 2>&1 && ! command -v head >/dev/null 2>&1; then
  die "未检测到 openssl 或 head，无法生成随机密钥"
fi

# ---------- 2. 创建 secrets 目录 ----------
mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR" 2>/dev/null || true

# 生成 base64url 随机字符串（A-Za-z0-9_-，符合 Redis 密码正则要求）
gen_random() {
  bytes=${1:-32}
  # 优先用 openssl（更可靠），退到 /dev/urandom + base64
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -base64 "$bytes" 2>/dev/null | tr -d '\n=' | tr '+/' '-_'
  else
    head -c "$bytes" /dev/urandom | base64 | tr -d '\n=' | tr '+/' '-_'
  fi
}

# 写入 secret 文件（已存在且非空则跳过）
# 权限 0644：docker-compose secrets file 模式下，容器内进程（如 mysql 用户）需要读取权限
write_secret() {
  file=$1
  content=$2
  if [ -s "$file" ]; then
    return 0
  fi
  printf '%s' "$content" > "$file"
  chmod 644 "$file" 2>/dev/null || true
}

# 生成空的可选 secret 文件（已存在则跳过）
touch_optional() {
  file=$1
  if [ ! -f "$file" ]; then
    : > "$file"
    chmod 644 "$file" 2>/dev/null || true
  fi
}

# ---------- 3. 生成必需的随机 secrets ----------
info "生成随机 secrets（如已存在则跳过）..."

# 3 组 MySQL 密码必须互不相同且 >=16 字符；gen_random 32 bytes 输出 ~43 字符，远超要求
write_secret "$SECRETS_DIR/mysql-root-password"       "$(gen_random 32)"
write_secret "$SECRETS_DIR/mysql-app-password"        "$(gen_random 32)"
write_secret "$SECRETS_DIR/mysql-migration-password"  "$(gen_random 32)"
# Redis 密码必须 Base64URL 字符
write_secret "$SECRETS_DIR/redis-password"            "$(gen_random 32)"
# JWT/Cookie/Token 必须 >=32 字符
write_secret "$SECRETS_DIR/jwt-secret"                "$(gen_random 64)"
write_secret "$SECRETS_DIR/cookie-crypto-secret"      "$(gen_random 64)"
write_secret "$SECRETS_DIR/internal-api-token"        "$(gen_random 64)"

# ---------- 4. 生成空的可选 secrets（功能未启用时也需要文件存在） ----------
touch_optional "$SECRETS_DIR/commercial-backend-access-token"
touch_optional "$SECRETS_DIR/embedding-api-key"
touch_optional "$SECRETS_DIR/ai-provider-api-key"

# ---------- 5. admin bcrypt 密码 hash（由 start.sh 用 api 镜像统一生成） ----------
# 不在主机生成：避免主机无 Python、pip install 超时、Docker 临时镜像拉取慢等耗时环节。
# 此处只创建空文件作为标记，start.sh 检测到为空时会在 api 镜像就绪后自动生成。
if [ ! -s "$SECRETS_DIR/admin-password-hash" ]; then
  : > "$SECRETS_DIR/admin-password-hash"
  chmod 644 "$SECRETS_DIR/admin-password-hash" 2>/dev/null || true
  info "admin-password-hash 将由 api 镜像在启动前自动生成（默认密码：$DEFAULT_ADMIN_PASSWORD）"
else
  ok "admin-password-hash 已存在（跳过生成）"
fi

# ---------- 6. 创建 .env ----------
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
    ok "已从 .env.example 创建 .env"
  else
    die ".env.example 不存在，无法创建 .env"
  fi
else
  ok ".env 已存在（跳过创建）"
fi

# ---------- 7. 完成 ----------
cat <<EOF

$(ok "初始化完成")

默认管理员账号：
  用户名：admin
  密码：${DEFAULT_ADMIN_PASSWORD}

$(warn "请尽快登录后修改默认密码！")

下一步：
  启动服务：sh ./start.sh
  或手动：docker compose pull && docker compose up -d

EOF
