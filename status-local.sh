#!/bin/sh
# 查看本地部署服务状态（Linux / macOS）
set -u

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PID_DIR="$PROJECT_DIR/output/local-dev/pids"
API_DIR="$PROJECT_DIR/apps/api"
VENV_PY="$PROJECT_DIR/.venv/bin/python"
HEARTBEAT="$PROJECT_DIR/output/local-dev/scheduler.heartbeat"

API_PORT=15177
CRAWLER_PORT=15178
WEB_PORT=15176

check_port() {
  port=$1
  if command -v ss >/dev/null 2>&1; then
    ss -tlnp 2>/dev/null | grep -q ":${port} " && return 0
  elif command -v netstat >/dev/null 2>&1; then
    netstat -tlnp 2>/dev/null | grep -q ":${port} " && return 0
  fi
  return 1
}

printf '%-10s %-8s %-10s %s\n' "服务" "PID" "端口监听" "健康检查"
echo "--------------------------------------------------------------"

for entry in "crawler:$CRAWLER_PORT:http://127.0.0.1:${CRAWLER_PORT}/ready" "api:$API_PORT:http://127.0.0.1:${API_PORT}/health" "web:$WEB_PORT:http://127.0.0.1:${WEB_PORT}/"; do
  name=${entry%%:*}
  rest=${entry#*:}
  port=${rest%%:*}
  url=${rest#*:}

  pid="-"
  if [ -f "$PID_DIR/$name.pid" ]; then
    pid=$(cat "$PID_DIR/$name.pid" 2>/dev/null || echo "-")
    kill -0 "$pid" 2>/dev/null || pid="-"
  fi

  listening="否"
  check_port "$port" && listening="是"

  health="-"
  if command -v curl >/dev/null 2>&1 && curl -fsS --max-time 3 "$url" >/dev/null 2>&1; then
    health="正常"
  fi
  printf '%-10s %-8s %-10s %s\n' "$name" "$pid" "$listening" "$health"
done

# Scheduler Worker：无端口，用心跳文件判断
pid="-"
if [ -f "$PID_DIR/scheduler.pid" ]; then
  pid=$(cat "$PID_DIR/scheduler.pid" 2>/dev/null || echo "-")
  kill -0 "$pid" 2>/dev/null || pid="-"
fi
health="未知"
if [ -f "$HEARTBEAT" ]; then
  age=$(( $(date +%s) - $(stat -c %Y "$HEARTBEAT" 2>/dev/null || stat -f %m "$HEARTBEAT" 2>/dev/null || echo 0) ))
  if [ "${age:-999}" -lt 60 ]; then
    health="正常（心跳 ${age}s 前）"
  else
    health="心跳过期（${age}s 前）"
  fi
fi
printf '%-10s %-8s %-10s %s\n' "scheduler" "$pid" "-" "$health"
