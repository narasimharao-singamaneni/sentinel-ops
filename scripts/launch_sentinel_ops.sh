#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="/tmp/sentinel-ops"
PY="$ROOT/.venv/bin/python"
READY_TIMEOUT=30

if [ ! -x "$PY" ]; then
  echo "error: $PY not found — run 'uv sync --all-packages' from $ROOT first" >&2
  exit 1
fi
mkdir -p "$STATE_DIR"

port_in_use() {
  (echo > "/dev/tcp/127.0.0.1/$1") 2>/dev/null
}

wait_ready() {
  local name="$1" url="$2"
  local deadline=$(( $(date +%s) + READY_TIMEOUT ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -sf -m 2 "$url" > /dev/null 2>&1; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

start_service() {
  local name="$1" port="$2" health="$3" dir="$4"
  local -a cmd=("${@:5}")

  if port_in_use "$port"; then
    echo "$name: port $port already in use — skipping start"
    return 0
  fi

  rm -f "$STATE_DIR/$name.pid"
  (
    cd "$ROOT/$dir"
    nohup "${cmd[@]}" > "$STATE_DIR/$name.log" 2>&1 &
    echo $! > "$STATE_DIR/$name.pid"
  )

  if wait_ready "$name" "$health"; then
    echo "$name: ready at $health (pid $(cat "$STATE_DIR/$name.pid"))"
  else
    echo "$name: FAILED to become ready at $health — last log lines:" >&2
    tail -n 20 "$STATE_DIR/$name.log" >&2
    return 1
  fi
}

start_service person-api 8002 "http://127.0.0.1:8002/health" "services/person-api" \
  env DATABASE_URL="postgresql+asyncpg://localhost:5432/person_api" \
      SENTINEL_URL="http://127.0.0.1:8001" \
      "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8002

start_service sentinel 8001 "http://127.0.0.1:8001/health" "services/aiops-sentinel" \
  env QWEN_BASE_URL="http://127.0.0.1:8000/v1" \
      QWEN_API_KEY="1234" \
      QWEN_MODEL="Qwen3.6-27B-oQ4e-mtp" \
      QWEN_TIMEOUT="90" \
      QWEN_DISABLE_THINKING="1" \
      QWEN_TEMPERATURE="0" \
      OPENWINDOW_INGEST_URL="http://127.0.0.1:8003/api/v1/incidents" \
      "$PY" -m uvicorn app.service:app --host 127.0.0.1 --port 8001

start_service openwindow 8003 "http://127.0.0.1:8003/health/" "apps/openwindow" \
  env DJANGO_SETTINGS_MODULE="openwindow.config.django_settings" \
      DJANGO_DEBUG="true" \
      DJANGO_ALLOWED_HOSTS="127.0.0.1,localhost" \
      SENTINEL_CALLBACK_URL="http://127.0.0.1:8001/api/v1/execute" \
      "$PY" manage.py runserver 8003 --noreload

echo "sentinel-ops stack running: person-api :8002, sentinel :8001, openwindow :8003"
echo "logs and pids: $STATE_DIR/   stop with: scripts/kill_sentinel_ops.sh"
