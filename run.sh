#!/usr/bin/env bash
# Start the FX Sentiment Dashboard (API + built frontend) on http://127.0.0.1:8000
#
#   ./run.sh              build what is missing/stale, then serve on 127.0.0.1:8000
#   PORT=8010 ./run.sh    use another port (5000/7000/8765/8766 are taken on this Mac)
#   ./run.sh dev          API with auto-reload + Vite dev server (hot reload) on :5173,
#                         which proxies /api to the API. Open http://127.0.0.1:5173
#   ./run.sh build        only (re)build the frontend
#
# Database settings come from .env (MySQL). For a throwaway demo database:
#   export DATABASE_URL=sqlite:///$PWD/backend/demo.db
#   (cd backend && .venv/bin/python scripts/seed_demo.py) && ./run.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND="$ROOT/backend"
FRONTEND="$ROOT/frontend"
VENV="$BACKEND/.venv"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
MODE="${1:-serve}"

ensure_venv() {
  if [ ! -x "$VENV/bin/python" ]; then
    echo "==> Creating Python virtualenv in backend/.venv"
    "${PYTHON:-python3}" -m venv "$VENV"
    "$VENV/bin/pip" install --quiet --upgrade pip
    "$VENV/bin/pip" install --quiet -r "$BACKEND/requirements.txt"
  elif ! "$VENV/bin/python" -c "import fastapi, uvicorn, sqlalchemy, apscheduler" 2>/dev/null; then
    echo "==> Installing Python requirements"
    "$VENV/bin/pip" install --quiet -r "$BACKEND/requirements.txt"
  fi
}

ensure_node_modules() {
  if [ ! -d "$FRONTEND/node_modules" ] || [ "$FRONTEND/package.json" -nt "$FRONTEND/node_modules" ]; then
    echo "==> Installing frontend dependencies"
    (cd "$FRONTEND" && npm install --no-audit --no-fund)
  fi
}

frontend_is_stale() {
  [ ! -f "$FRONTEND/dist/index.html" ] && return 0
  # any source/config file newer than the last build?
  [ -n "$(find "$FRONTEND/src" "$FRONTEND/index.html" "$FRONTEND/package.json" \
            "$FRONTEND/vite.config.ts" "$FRONTEND/tsconfig.json" \
            -newer "$FRONTEND/dist/index.html" -print -quit 2>/dev/null)" ]
}

build_frontend() {
  if ! command -v npm >/dev/null 2>&1; then
    if [ -f "$FRONTEND/dist/index.html" ]; then
      echo "!! npm not found; serving the existing frontend build" >&2
      return 0
    fi
    echo "!! npm not found and frontend/dist is missing. Install Node.js, then re-run." >&2
    exit 1
  fi
  ensure_node_modules
  echo "==> Building frontend"
  (cd "$FRONTEND" && npm run build)
}

case "$MODE" in
  serve)
    ensure_venv
    if frontend_is_stale; then build_frontend; fi
    echo "==> Dashboard: http://$HOST:$PORT   (API docs: http://$HOST:$PORT/api/docs)"
    cd "$BACKEND"
    exec "$VENV/bin/uvicorn" app.main:app --host "$HOST" --port "$PORT"
    ;;
  build)
    build_frontend
    ;;
  dev)
    ensure_venv
    ensure_node_modules
    echo "==> API (auto-reload): http://$HOST:$PORT    UI (hot reload): http://127.0.0.1:5173"
    (cd "$BACKEND" && exec "$VENV/bin/uvicorn" app.main:app --host "$HOST" --port "$PORT" --reload --reload-dir app) &
    API_PID=$!
    trap 'kill "$API_PID" 2>/dev/null || true' EXIT INT TERM
    cd "$FRONTEND"
    API_URL="http://$HOST:$PORT" npm run dev
    ;;
  *)
    sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
