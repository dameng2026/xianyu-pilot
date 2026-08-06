#!/bin/sh
# 一键本地部署启动脚本（Linux / macOS，无需 Docker）
# 自动完成：初始化（首次）→ 数据库迁移 → 构建 Crawler → 启动 API/Worker/Crawler/Web → 健康检查
# 用法：
#   sh ./start-local.sh              # 首次自动初始化并启动（推荐）
#   sh ./start-local.sh --force-init # 强制重新运行初始化向导
#   sh ./start-local.sh --no-init    # 跳过初始化检查，直接启动
# 停止：sh ./stop-local.sh   状态：sh ./status-local.sh
#
# 前置要求（首次运行脚本会自动完成初始化，见 scripts/setup-local.sh）：
#   - Python 3.10+ / Node.js 18+ / npm
#   - 本机已安装并运行 MySQL 8 与 Redis 7（Redis 无密码即可）

set -u

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$PROJECT_DIR"

VENV_PY="$PROJECT_DIR/.venv/bin/python"
API_DIR="$PROJECT_DIR/apps/api"
CRAWLER_DIR="$PROJECT_DIR/apps/crawler"
WEB_DIR="$PROJECT_DIR/apps/web"
OUTPUT_DIR="$PROJECT_DIR/output/local-dev"
PID_DIR="$OUTPUT_DIR/pids"
LOG_DIR="$OUTPUT_DIR"

API_PORT=15177
CRAWLER_PORT=15178
WEB_PORT=15176

color() { printf '\033[%sm%s\033[0m' "$1" "$2"; }
info()  { printf '%s %s\n' "$(color '1;36' '•')" "$*"; }
ok()    { printf '%s %s\n' "$(color '1;32' '✓')" "$*"; }
warn()  { printf '%s %s\n' "$(color '1;33' '!')" "$*" >&2; }
die()   { printf '%s %s\n' "$(color '1;31' '✗')" "$*" >&2; exit 1; }

FORCE_INIT=0
SKIP_INIT=0
for arg in "$@"; do
  case "$arg" in
    --force-init) FORCE_INIT=1 ;;
    --no-init)    SKIP_INIT=1 ;;
    *) echo "未知参数：$arg（支持：--force-init / --no-init）" >&2; exit 1 ;;
  esac
done

check_port_free() {
  port=$1
  if command -v ss >/dev/null 2>&1; then
    ss -tlnp 2>/dev/null | grep -q ":${port} " && return 1
  elif command -v netstat >/dev/null 2>&1; then
    netstat -tlnp 2>/dev/null | grep -q ":${port} " && return 1
  fi
  return 0
}

wait_http() {
  url=$1
  timeout_sec=$2
  name=$3
  i=0
  while [ "$i" -lt "$timeout_sec" ]; do
    if command -v curl >/dev/null 2>&1; then
      if curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then return 0; fi
    elif command -v wget >/dev/null 2>&1; then
      if wget -qO- --timeout=3 "$url" >/dev/null 2>&1; then return 0; fi
    else
      sleep 1; i=$((i + 1)); continue
    fi
    sleep 1
    i=$((i + 1))
  done
  warn "$name 在 ${timeout_sec}s 内未就绪（$url）"
  return 1
}

# ---------- 1. 初始化检查 ----------
if [ "$SKIP_INIT" = "0" ]; then
  need_init=0
  [ ! -f .env ] && need_init=1
  [ ! -x "$VENV_PY" ] && need_init=1
  [ ! -d "$CRAWLER_DIR/node_modules" ] && need_init=1
  [ ! -d "$WEB_DIR/node_modules" ] && need_init=1
  if [ "$FORCE_INIT" = "1" ] || [ "$need_init" = "1" ]; then
    info "首次部署，先运行本地初始化向导（自动生成配置、建库、安装依赖）..."
    sh ./scripts/setup-local.sh || die "本地初始化向导执行失败"
  else
    ok "环境已就绪（.env / .venv / node_modules 均存在）"
  fi
fi

# ---------- 2. 前置检查 ----------
[ -f .env ] || die "缺少 .env，请先运行：sh ./scripts/setup-local.sh"
[ -x "$VENV_PY" ] || die "缺少 Python venv，请先运行：sh ./scripts/setup-local.sh"

# 检查 Redis 是否可连接（无密码，127.0.0.1:6379）
if command -v redis-cli >/dev/null 2>&1; then
  if ! redis-cli -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1; then
    warn "Redis 未连接（127.0.0.1:6379）。请先启动 Redis；如需密码请编辑 .env 的 REDIS_PASSWORD"
  fi
fi

# 端口预检
for p in $WEB_PORT $API_PORT $CRAWLER_PORT; do
  if ! check_port_free "$p"; then
    die "端口 $p 已被占用。请先停止占用进程，或修改 .env 中的端口（SERVER_PORT/CRAWLER_PORT/XYA_WEB_PORT）"
  fi
done
ok "端口检查通过（Web=$WEB_PORT, API=$API_PORT, Crawler=$CRAWLER_PORT）"

mkdir -p "$PID_DIR"

# ---------- 3. 数据库迁移 ----------
info "执行数据库迁移（python -m app.migrations upgrade）..."
(cd "$API_DIR" && "$VENV_PY" -m app.migrations upgrade) || die "数据库迁移失败，请检查 MySQL 连接配置（.env 中 MYSQL_*）"
ok "数据库迁移完成"

# ---------- 4. 构建 Crawler ----------
if [ ! -f "$CRAWLER_DIR/dist/server.js" ]; then
  info "构建 Crawler（npm run build）..."
  (cd "$CRAWLER_DIR" && npm run build) || die "Crawler 构建失败"
  ok "Crawler 构建完成"
else
  ok "Crawler 已构建（dist/server.js 存在）"
fi

# ---------- 5. 启动服务（后台） ----------
# 与 scripts/local-dev.ps1 保持一致的环境变量与端口约定
export SERVER_HOST=127.0.0.1
export SERVER_PORT=$API_PORT
export API_RELOAD=false
export CRAWLER_PORT=$CRAWLER_PORT
export PORT=$CRAWLER_PORT
export HOST=127.0.0.1
export CRAWLER_BASE_URL="http://127.0.0.1:$CRAWLER_PORT"
export CRAWLER_SERVICE_URL="http://127.0.0.1:$CRAWLER_PORT"
export XYA_WEB_PORT=$WEB_PORT
export XYA_WEB_HOST=127.0.0.1
export VITE_API_PROXY_TARGET="http://127.0.0.1:$API_PORT"
export VITE_UPLOAD_PROXY_TARGET="http://127.0.0.1:$API_PORT"
export CORS_ALLOWED_ORIGINS="http://127.0.0.1:$WEB_PORT,http://localhost:$WEB_PORT"
export CRAWLER_ALLOWED_ORIGINS="$CORS_ALLOWED_ORIGINS"
export SCHEDULER_HEARTBEAT_PATH="$OUTPUT_DIR/scheduler.heartbeat"
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8

start_service() {
  name=$1
  workdir=$2
  cmd=$3
  shift 3
  pidfile="$PID_DIR/$name.pid"
  stdout="$LOG_DIR/$name.out.log"
  stderr="$LOG_DIR/$name.err.log"
  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    ok "$name 已在运行（PID $(cat "$pidfile")）"
    return 0
  fi
  info "启动 $name ..."
  : > "$stdout"
  : > "$stderr"
  (
    cd "$workdir" || exit 1
    nohup $cmd "$@" >> "$stdout" 2>> "$stderr" &
    echo $! > "$pidfile"
  )
  sleep 1
  if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
    ok "$name 已启动（PID $(cat "$pidfile")，日志：$stdout）"
  else
    warn "$name 启动失败，最近日志："
    tail -20 "$stderr" 2>/dev/null | sed 's/^/    /'
  fi
}

start_service "crawler" "$CRAWLER_DIR" node --env-file=../../.env dist/server.js
start_service "api"     "$API_DIR"     "$VENV_PY" run.py
start_service "scheduler" "$API_DIR"   "$VENV_PY" -m app.worker
start_service "web"     "$WEB_DIR"     npm run dev

# ---------- 6. 健康检查 ----------
echo ""
info "等待服务就绪（最长 90 秒）..."
ok_final=1

printf '  [1/4] Crawler...'
if wait_http "http://127.0.0.1:$CRAWLER_PORT/ready" 30 "Crawler"; then printf ' ✓\n'; else printf ' ✗\n'; ok_final=0; fi

printf '  [2/4] API...'
if wait_http "http://127.0.0.1:$API_PORT/health/ready" 60 "API"; then printf ' ✓\n'; else printf ' ✗\n'; ok_final=0; fi

printf '  [3/4] Scheduler Worker...'
sched_ok=0
i=0
while [ "$i" -lt 30 ]; do
  if (cd "$API_DIR" && "$VENV_PY" -m app.worker --check) >/dev/null 2>&1; then sched_ok=1; break; fi
  sleep 2
  i=$((i + 1))
done
if [ "$sched_ok" = "1" ]; then printf ' ✓\n'; else printf ' ✗\n'; ok_final=0; fi

printf '  [4/4] Web...'
if wait_http "http://127.0.0.1:$WEB_PORT/" 30 "Web"; then printf ' ✓\n'; else printf ' ✗\n'; ok_final=0; fi

if [ "$ok_final" = "0" ]; then
  warn "部分服务未就绪，请查看日志："
  warn "  API：       tail -100 $LOG_DIR/api.out.log"
  warn "  Crawler：   tail -100 $LOG_DIR/crawler.out.log"
  warn "  Scheduler： tail -100 $LOG_DIR/scheduler.out.log"
  warn "  Web：       tail -100 $LOG_DIR/web.out.log"
  exit 1
fi

echo ""
ok "本地服务已就绪"
echo ""
echo "访问地址："
echo "  本机：   http://localhost:$WEB_PORT"
echo ""
echo "默认账号：admin"
echo "默认密码：admin123（首次启动时生成，请登录后尽快修改）"
echo ""
echo "管理命令："
echo "  查看状态：sh ./status-local.sh"
echo "  停止服务：sh ./stop-local.sh"
echo "  查看日志：tail -f $LOG_DIR/api.out.log"
echo ""
warn "本地模式仅供单机使用；公网多用户请使用 Docker 部署（sh ./start.sh）"
