#!/bin/bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_ROOT"
FRONTEND_DIR="$PROJECT_ROOT/dlcs-ui"

BACKEND_PORT=8000
FRONTEND_PORT=5173
BACKEND_HEALTH_RETRIES="${DL_BACKEND_HEALTH_RETRIES:-60}"
BACKEND_HEALTH_SLEEP_SEC="${DL_BACKEND_HEALTH_SLEEP_SEC:-1}"
BACKEND_PROBE_TIMEOUT_SEC="${DL_BACKEND_PROBE_TIMEOUT_SEC:-8}"
BACKEND_VENV_DIR="${DL_BACKEND_VENV_DIR:-$PROJECT_ROOT/.venv}"
BACKEND_RUNTIME_REQUIREMENTS="${DL_BACKEND_REQUIREMENTS_FILE:-$PROJECT_ROOT/requirements.backend-runtime.txt}"
BACKEND_MIN_PYTHON="${DL_BACKEND_MIN_PYTHON:-3.10}"
BACKEND_MAX_PYTHON="${DL_BACKEND_MAX_PYTHON:-3.12}"

if [ -x "/opt/homebrew/opt/node@20/bin/node" ]; then
  export PATH="/opt/homebrew/opt/node@20/bin:$PATH"
fi

echo "📂 Project root: $PROJECT_ROOT"
echo "🧠 Backend dir : $BACKEND_DIR"
echo "🎨 Frontend dir: $FRONTEND_DIR"
echo "----------------------------------------"
echo "🧩 Node version: $(node -v)"

hash_file() {
  local target=$1
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$target" | awk '{print $1}'
  else
    openssl dgst -sha256 "$target" | awk '{print $NF}'
  fi
}

python_is_supported() {
  local py_cmd=$1
  "$py_cmd" - <<'PY' >/dev/null 2>&1
import sys
import os
def parse(v):
    major, minor = (v.strip().split(".") + ["0"])[:2]
    return int(major), int(minor)
min_v = parse(os.environ.get("BACKEND_MIN_PYTHON", "3.10"))
max_v = parse(os.environ.get("BACKEND_MAX_PYTHON", "3.12"))
cur = (sys.version_info.major, sys.version_info.minor)
raise SystemExit(0 if (cur >= min_v and cur <= max_v) else 1)
PY
}

python_bootstrap_probe() {
  local py_cmd=$1
  "$py_cmd" - <<'PY' >/dev/null 2>&1 &
import importlib
import sys
import os
def parse(v):
    major, minor = (v.strip().split(".") + ["0"])[:2]
    return int(major), int(minor)
min_v = parse(os.environ.get("BACKEND_MIN_PYTHON", "3.10"))
max_v = parse(os.environ.get("BACKEND_MAX_PYTHON", "3.12"))
cur = (sys.version_info.major, sys.version_info.minor)
if not (cur >= min_v and cur <= max_v):
    raise SystemExit(1)
for mod in ("subprocess", "venv"):
    importlib.import_module(mod)
print("ok")
PY
  local pid=$!
  local waited=0
  while kill -0 "$pid" >/dev/null 2>&1; do
    if [ "$waited" -ge "$BACKEND_PROBE_TIMEOUT_SEC" ]; then
      kill -9 "$pid" >/dev/null 2>&1 || true
      wait "$pid" >/dev/null 2>&1 || true
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
  wait "$pid" >/dev/null 2>&1
}

pick_bootstrap_python() {
  local candidates=()
  if [ -n "${DL_BACKEND_BOOTSTRAP_PYTHON:-}" ]; then
    candidates+=("${DL_BACKEND_BOOTSTRAP_PYTHON}")
  fi
  candidates+=(
    "/opt/homebrew/bin/python3.12"
    "/opt/homebrew/bin/python3.11"
    "python3.12"
    "python3.11"
    "python3.10"
    "python3"
    "python"
  )

  local c
  for c in "${candidates[@]}"; do
    if ! command -v "$c" >/dev/null 2>&1; then
      continue
    fi
    if python_bootstrap_probe "$c"; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

ensure_backend_runtime() {
  if [[ "${DL_BACKEND_AUTO_BOOTSTRAP:-1}" != "1" ]]; then
    return 0
  fi
  if [ ! -f "$BACKEND_RUNTIME_REQUIREMENTS" ]; then
    echo "❌ Missing backend runtime requirements file: $BACKEND_RUNTIME_REQUIREMENTS"
    return 1
  fi

  local bootstrap_python=""
  bootstrap_python="$(pick_bootstrap_python || true)"
  if [ -z "${bootstrap_python:-}" ] && [[ "${DL_BACKEND_AUTO_INSTALL_PYTHON:-1}" == "1" ]] && command -v brew >/dev/null 2>&1; then
    echo "📦 Installing python@3.11 via Homebrew (one-time) ..."
    brew install python@3.11
    bootstrap_python="$(pick_bootstrap_python || true)"
  fi
  if [ -z "${bootstrap_python:-}" ]; then
    echo "❌ No Python >=3.10 found. Set DL_BACKEND_BOOTSTRAP_PYTHON to a 3.10+ interpreter."
    return 1
  fi

  if [ -x "$BACKEND_VENV_DIR/bin/python" ] && ! python_bootstrap_probe "$BACKEND_VENV_DIR/bin/python"; then
    echo "⚠️  Existing backend venv runtime probe failed. Recreating $BACKEND_VENV_DIR ..."
    rm -rf "$BACKEND_VENV_DIR"
  fi

  if [ ! -x "$BACKEND_VENV_DIR/bin/python" ]; then
    echo "🛠  Creating backend venv at $BACKEND_VENV_DIR ..."
    "$bootstrap_python" -m venv "$BACKEND_VENV_DIR"
  fi

  local venv_python="$BACKEND_VENV_DIR/bin/python"
  local stamp_file="$BACKEND_VENV_DIR/.backend_runtime_stamp"
  local req_hash
  req_hash="$(hash_file "$BACKEND_RUNTIME_REQUIREMENTS")"
  local stamp_hash=""
  if [ -f "$stamp_file" ]; then
    stamp_hash="$(cat "$stamp_file" || true)"
  fi

  if [ "$req_hash" != "$stamp_hash" ]; then
    echo "📦 Installing backend runtime dependencies ..."
    "$venv_python" -m pip install --upgrade pip setuptools wheel >/dev/null
    "$venv_python" -m pip install -r "$BACKEND_RUNTIME_REQUIREMENTS"
    echo "$req_hash" > "$stamp_file"
    echo "✅ Backend runtime dependencies installed."
  else
    echo "✅ Backend runtime dependencies already up to date."
  fi
}

backend_python_probe() {
  local py_cmd=$1
  "$py_cmd" - <<'PY' >/dev/null 2>&1 &
import importlib
import sys
import os
def parse(v):
    major, minor = (v.strip().split(".") + ["0"])[:2]
    return int(major), int(minor)
min_v = parse(os.environ.get("BACKEND_MIN_PYTHON", "3.10"))
max_v = parse(os.environ.get("BACKEND_MAX_PYTHON", "3.12"))
cur = (sys.version_info.major, sys.version_info.minor)
if not (cur >= min_v and cur <= max_v):
    raise SystemExit(1)
for mod in ("subprocess", "uvicorn", "fastapi"):
    importlib.import_module(mod)
print("ok")
PY
  local pid=$!
  local waited=0
  while kill -0 "$pid" >/dev/null 2>&1; do
    if [ "$waited" -ge "$BACKEND_PROBE_TIMEOUT_SEC" ]; then
      kill -9 "$pid" >/dev/null 2>&1 || true
      wait "$pid" >/dev/null 2>&1 || true
      return 1
    fi
    sleep 1
    waited=$((waited + 1))
  done
  wait "$pid" >/dev/null 2>&1
}

resolve_backend_python() {
  local candidates=()
  if [ -x "$BACKEND_VENV_DIR/bin/python" ]; then
    candidates+=("$BACKEND_VENV_DIR/bin/python")
  fi
  if [ -n "${DL_BACKEND_PYTHON:-}" ]; then
    candidates+=("${DL_BACKEND_PYTHON}")
  fi
  candidates+=(
    "/opt/homebrew/bin/python3.12"
    "/opt/homebrew/bin/python3.11"
    "python3.12"
    "python3.11"
    "python3.10"
    "python3"
    "python"
  )

  local c
  for c in "${candidates[@]}"; do
    if ! command -v "$c" >/dev/null 2>&1; then
      continue
    fi
    if backend_python_probe "$c"; then
      echo "$c"
      return 0
    fi
    echo "⚠️  Backend python probe failed for '$c' (requires Python $BACKEND_MIN_PYTHON-$BACKEND_MAX_PYTHON and imports: subprocess/uvicorn/fastapi)." >&2
  done
  return 1
}

kill_port() {
  local port=$1
  if lsof -ti:"$port" >/dev/null 2>&1; then
    echo "⚠️  Port $port in use, killing existing process..."
    lsof -ti:"$port" | xargs kill -9 || true
    echo "✅ Port $port cleared."
  else
    echo "✅ Port $port is free."
  fi
}

start_detached() {
  local log_file=$1
  shift
  if command -v setsid >/dev/null 2>&1; then
    setsid "$@" > "$log_file" 2>&1 < /dev/null &
  else
    nohup "$@" > "$log_file" 2>&1 < /dev/null &
  fi
}

health_check() {
  local name=$1
  local url=$2
  local retries=${3:-30}
  local sleep_sec=${4:-1}

  echo "🔎 Checking $name at $url ..."
  for ((i=1; i<=retries; i++)); do
    if curl -sS --max-time 3 --connect-timeout 1 "$url" >/dev/null 2>&1; then
      echo "✅ $name is UP (attempt $i/$retries)"
      return 0
    fi
    echo "⏳ $name not ready yet (attempt $i/$retries)..."
    sleep "$sleep_sec"
  done
  echo "❌ $name failed health check after $retries attempts."
  return 1
}

health_check_html() {
  local name=$1
  local url=$2
  local must_contain=$3
  local retries=${4:-30}
  local sleep_sec=${5:-1}

  echo "🔎 Checking $name content at $url ..."
  for ((i=1; i<=retries; i++)); do
    local body=""
    body="$(curl -sS --max-time 3 --connect-timeout 1 "$url" || true)"
    if [[ -n "$body" && "$body" == *"$must_contain"* ]]; then
      echo "✅ $name content is valid (attempt $i/$retries)"
      return 0
    fi
    echo "⏳ $name content not ready yet (attempt $i/$retries)..."
    sleep "$sleep_sec"
  done
  echo "❌ $name content check failed after $retries attempts."
  return 1
}

health_check_js_contains() {
  local name=$1
  local js_url=$2
  local must_contain=$3
  local retries=${4:-20}
  local sleep_sec=${5:-1}

  echo "🔎 Checking $name bundle marker at $js_url ..."
  for ((i=1; i<=retries; i++)); do
    local tmp_file
    tmp_file="$(mktemp /tmp/dlens-jscheck.XXXXXX)"
    if curl -sS --max-time 6 --connect-timeout 2 "$js_url" -o "$tmp_file" >/dev/null 2>&1 && LC_ALL=C grep -Fq -- "$must_contain" "$tmp_file"; then
      rm -f "$tmp_file"
      echo "✅ $name bundle marker found (attempt $i/$retries)"
      return 0
    fi
    rm -f "$tmp_file"
    echo "⏳ $name marker not ready yet (attempt $i/$retries)..."
    sleep "$sleep_sec"
  done
  echo "❌ $name marker check failed after $retries attempts."
  return 1
}

frontend_needs_build() {
  local build_mode="${DL_FRONTEND_BUILD_ON_START:-auto}"
  if [[ "$build_mode" == "1" ]]; then
    return 0
  fi
  if [[ "$build_mode" == "0" ]]; then
    return 1
  fi

  local dist_index="$FRONTEND_DIR/dist/index.html"
  local fingerprint_file="$FRONTEND_DIR/dist/.source-fingerprint"
  if [ ! -f "$dist_index" ]; then
    return 0
  fi

  if [ ! -f "$fingerprint_file" ]; then
    return 0
  fi

  local current_fp
  current_fp="$(frontend_source_fingerprint || true)"
  if [ -z "${current_fp:-}" ]; then
    return 0
  fi
  local built_fp
  built_fp="$(cat "$fingerprint_file" 2>/dev/null || true)"
  if [ "$current_fp" != "$built_fp" ]; then
    return 0
  fi
  return 1
}

frontend_source_fingerprint() {
  (
    cd "$FRONTEND_DIR" || exit 1
    local include=(
      "index.html"
      "package.json"
      "package-lock.json"
      "vite.config.ts"
      "tsconfig.json"
      "tsconfig.app.json"
      "tsconfig.node.json"
      "src"
    )
    local target
    local files=()
    for target in "${include[@]}"; do
      if [ -f "$target" ]; then
        files+=("$target")
      elif [ -d "$target" ]; then
        while IFS= read -r file; do
          files+=("$file")
        done < <(find "$target" -type f | sort)
      fi
    done
    if [ "${#files[@]}" -eq 0 ]; then
      echo ""
      exit 0
    fi
    local file
    local tmp
    tmp="$(mktemp /tmp/dlens-fp.XXXXXX)"
    for file in "${files[@]}"; do
      shasum -a 256 "$file" >> "$tmp"
    done
    shasum -a 256 "$tmp" | awk '{print $1}'
    rm -f "$tmp"
  )
}

echo "🧹 Step 1: Cleaning old processes on $BACKEND_PORT / $FRONTEND_PORT..."
kill_port "$BACKEND_PORT"
kill_port "$FRONTEND_PORT"
pkill -f "$FRONTEND_DIR/node_modules/.bin/vite" >/dev/null 2>&1 || true
pkill -f "uvicorn webapp.main:app" >/dev/null 2>&1 || true
pkill -f "python.*-m uvicorn webapp.main:app" >/dev/null 2>&1 || true
echo "----------------------------------------"

echo "🔧 Step 2: Resolving backend Python runtime..."
ensure_backend_runtime
BACKEND_PYTHON="$(resolve_backend_python || true)"
if [ -z "${BACKEND_PYTHON:-}" ]; then
  echo "❌ No usable Python runtime found for backend."
  echo "   The current runtime cannot import subprocess/uvicorn/fastapi reliably."
  echo "   Fix options:"
  echo "   1) Install a stable Python and deps, then run:"
  echo "      DL_BACKEND_PYTHON=/path/to/python ./start.sh"
  echo "   2) Or create a virtualenv and install backend deps there."
  exit 1
fi
echo "✅ Backend python: $BACKEND_PYTHON"
echo "----------------------------------------"

echo "🧠 Step 3: Starting backend (uvicorn webapp.main:app --port $BACKEND_PORT)..."
cd "$BACKEND_DIR"
mkdir -p logs
# Default to stable mode (no --reload) to avoid random drops from reloader/watchers.
# If you want hot reload: DL_BACKEND_RELOAD=1 ./start.sh
BACKEND_ARGS=(-m uvicorn webapp.main:app --port "$BACKEND_PORT")
if [[ "${DL_BACKEND_RELOAD:-0}" == "1" ]]; then
  BACKEND_ARGS=(-m uvicorn webapp.main:app --reload --reload-dir webapp --reload-dir analysis --reload-dir database --port "$BACKEND_PORT")
fi
start_detached "logs/backend.log" "$BACKEND_PYTHON" "${BACKEND_ARGS[@]}"
BACKEND_PID=$!
echo "✅ Backend started with PID $BACKEND_PID"
echo "📜 Backend logs: $PROJECT_ROOT/logs/backend.log"
echo "----------------------------------------"

echo "🎨 Step 4: Ensuring frontend dependencies..."
cd "$FRONTEND_DIR"
if [ ! -d "node_modules" ]; then
  npm install
fi
mkdir -p logs
VITE_BIN="$FRONTEND_DIR/node_modules/.bin/vite"
if [ ! -x "$VITE_BIN" ]; then
  npm install
fi

# Default to preview mode for stability (avoids intermittent dev-server exits/white screen).
# Use DL_FRONTEND_MODE=dev for HMR while actively editing UI.
FRONTEND_MODE="${DL_FRONTEND_MODE:-preview}"
if [[ "$FRONTEND_MODE" == "dev" ]]; then
  echo "🛠  Frontend mode: dev (HMR)"
  start_detached "logs/frontend.log" "$VITE_BIN" --host 127.0.0.1 --strictPort --port "$FRONTEND_PORT"
else
  echo "🛡  Frontend mode: preview (stable)"
  if frontend_needs_build; then
    echo "🏗  Building frontend bundle..."
    npm run build >/dev/null 2>&1
    frontend_source_fingerprint > "$FRONTEND_DIR/dist/.source-fingerprint" || true
  else
    echo "⏭  Reusing existing dist bundle (set DL_FRONTEND_BUILD_ON_START=1 to force rebuild)."
  fi
  start_detached "logs/frontend.log" "$VITE_BIN" preview --host 127.0.0.1 --strictPort --port "$FRONTEND_PORT"
fi
FRONTEND_PID=$!
echo "✅ Frontend started with PID $FRONTEND_PID"
echo "📜 Frontend logs: $FRONTEND_DIR/logs/frontend.log"
echo "----------------------------------------"

echo "🩺 Step 5: Health checks..."
cd "$PROJECT_ROOT"
health_check "Backend API" "http://127.0.0.1:${BACKEND_PORT}/api/health" "$BACKEND_HEALTH_RETRIES" "$BACKEND_HEALTH_SLEEP_SEC" || {
  echo "💥 Backend health check failed. Last backend log lines:"
  tail -n 80 "$PROJECT_ROOT/logs/backend.log" || true
  exit 1
}
# Optional warm-up check for data endpoint. Do not fail startup if DB-dependent route is still warming.
if ! health_check "Backend posts endpoint" "http://127.0.0.1:${BACKEND_PORT}/api/posts" 15 1; then
  echo "⚠️  /api/posts still warming up; backend core is healthy at /api/health."
fi
health_check "Frontend UI" "http://127.0.0.1:${FRONTEND_PORT}" 60 1 || {
  echo "💥 Frontend health check failed. Check dlcs-ui/logs/frontend.log"
  exit 1
}
if [[ "$FRONTEND_MODE" == "dev" ]]; then
  health_check_html "Frontend Vite Client" "http://127.0.0.1:${FRONTEND_PORT}/@vite/client" "createHotContext" 30 1 || {
    echo "💥 Frontend runtime check failed. Check dlcs-ui/logs/frontend.log"
    exit 1
  }
else
  health_check_html "Frontend Index" "http://127.0.0.1:${FRONTEND_PORT}/" "id=\"root\"" 30 1 || {
    echo "💥 Frontend runtime check failed. Check dlcs-ui/logs/frontend.log"
    exit 1
  }
  FRONTEND_ASSET_PATH="$(curl -sS --max-time 6 --connect-timeout 2 "http://127.0.0.1:${FRONTEND_PORT}/" | sed -n 's/.*src="\([^"]*index-[^"]*\.js\)".*/\1/p' | head -n 1)"
  if [[ -n "${FRONTEND_ASSET_PATH:-}" ]]; then
    health_check_js_contains "Stitch UI" "http://127.0.0.1:${FRONTEND_PORT}${FRONTEND_ASSET_PATH}" "Glass Command" 20 1 || {
      echo "💥 Frontend bundle does not include expected Stitch UI marker. Check dlcs-ui/logs/frontend.log"
      exit 1
    }
  fi
fi

echo "✅✅ All systems go."
echo ""
echo "🌐 Backend API : http://127.0.0.1:${BACKEND_PORT}"
echo "🌐 Frontend UI : http://127.0.0.1:${FRONTEND_PORT}"
echo ""
echo "📌 查看 logs"
echo "  - tail -f $PROJECT_ROOT/logs/backend.log"
echo "  - tail -f $FRONTEND_DIR/logs/frontend.log"
