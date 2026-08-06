#!/bin/sh
# 停止本地部署服务（Linux / macOS）：停止 API / Worker / Crawler / Web
set -u

PROJECT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PID_DIR="$PROJECT_DIR/output/local-dev/pids"

color() { printf '\033[%sm%s\033[0m' "$1" "$2"; }
ok()    { printf '%s %s\n' "$(color '1;32' '✓')" "$*"; }
warn()  { printf '%s %s\n' "$(color '1;33' '!')" "$*" >&2; }

[ -d "$PID_DIR" ] || { ok "没有正在运行的服务（无 PID 目录）"; exit 0; }

for name in crawler api scheduler web; do
  pidfile="$PID_DIR/$name.pid"
  if [ ! -f "$pidfile" ]; then
    continue
  fi
  pid=$(cat "$pidfile" 2>/dev/null || echo "")
  if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$pidfile"
    continue
  fi
  # 优先优雅终止，超时强杀；一并终止子进程（如 npm/vite 派生的进程）
  kill "$pid" 2>/dev/null
  i=0
  while [ "$i" -lt 10 ] && kill -0 "$pid" 2>/dev/null; do
    sleep 1
    i=$((i + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    pkill -TERM -P "$pid" 2>/dev/null
    kill -9 "$pid" 2>/dev/null
    warn "$name（PID $pid）强制终止"
  else
    ok "$name（PID $pid）已停止"
  fi
  rm -f "$pidfile"
done

rm -f "$PROJECT_DIR/output/local-dev/scheduler.heartbeat"
ok "全部服务已停止"
