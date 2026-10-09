#!/usr/bin/env bash
set -uo pipefail

STATE_DIR="/tmp/sentinel-ops"

port_in_use() {
  (echo > "/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

stop_by_pidfile() {
  local name="$1"
  local pidfile="$STATE_DIR/$name.pid"
  if [ ! -f "$pidfile" ]; then
    echo "$name: no pid file at $pidfile"
    return
  fi
  local pid
  pid="$(cat "$pidfile")"
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null
    local i
    for i in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.3
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null
    fi
    echo "$name: stopped (pid $pid)"
  else
    echo "$name: pid $pid not running"
  fi
  rm -f "$pidfile"
}

stop_by_pidfile person-api
stop_by_pidfile sentinel
stop_by_pidfile openwindow

if port_in_use 8002; then
  pkill -f "uvicorn app\.main:app .*--port 8002" 2>/dev/null || true
fi
if port_in_use 8001; then
  pkill -f "uvicorn app\.service:app .*--port 8001" 2>/dev/null || true
fi
if port_in_use 8003; then
  pkill -f "manage\.py runserver 8003" 2>/dev/null || true
fi

sleep 1
for port in 8001 8002 8003; do
  if port_in_use "$port"; then
    echo "port $port still in use — check manually" >&2
    exit 1
  fi
done
echo "sentinel-ops stopped: ports 8001 8002 8003 free"
