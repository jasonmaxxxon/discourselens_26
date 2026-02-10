#!/usr/bin/env bash
set -e

###########################################################
# DiscourseLens V6 One-Shot Dev Launcher (Backend + UI)
# - Kills old processes on :8000 / :5173
# - Starts FastAPI (uvicorn) + Vite dev server
# - Health-checks both and prints friendly status
###########################################################

# 🔍 定位專案根目錄（確保從任何位置執行都OK）
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_ROOT"
FRONTEND_DIR="$PROJECT_ROOT/dlcs-ui"

BACKEND_PORT=8000
FRONTEND_PORT=5173

echo "📂 Project root: $PROJECT_ROOT"
echo "🧠 Backend dir : $BACKEND_DIR"
echo "🎨 Frontend dir: $FRONTEND_DIR"
echo "----------------------------------------"

kill_port() {
  local PORT=$1
  if lsof -ti:"$PORT" >/dev/null 2>&1; then
    echo "⚠️  Port $PORT in use, killing existing process..."
    lsof -ti:"$PORT" | xargs kill -9 || true
    echo "✅ Port $PORT cleared."
  else
    echo "✅ Port $PORT is free."
  fi
}

health_check() {
  local NAME=$1
  local URL=$2
  local RETRIES=${3:-20}
  local SLEEP_SEC=${4:-1}

  echo "🔎 Checking $NAME at $URL ..."
  for ((i=1; i<=RETRIES; i++)); do
    if curl -sSf "$URL" >/dev/null 2>&1; then
      echo "✅ $NAME is UP (attempt $i/$RETRIES)"
      return 0
    fi
    echo "⏳ $NAME not ready yet (attempt $i/$RETRIES)..."
    sleep "$SLEEP_SEC"
  done
  echo "❌ $NAME failed health check after $RETRIES attempts."
  return 1
}

echo "🧹 Step 1: Cleaning old processes on $BACKEND_PORT / $FRONTEND_PORT..."
kill_port "$BACKEND_PORT"
kill_port "$FRONTEND_PORT"
echo "----------------------------------------"

########################################
# 🚀 啟動 Backend (FastAPI + Uvicorn)
########################################
echo "🧠 Step 2: Starting backend (uvicorn webapp.main:app --reload --port $BACKEND_PORT)..."
cd "$BACKEND_DIR"

# 把 log 存到 logs/backend.log（避免 terminal 太亂）
mkdir -p logs
uvicorn webapp.main:app --reload --port "$BACKEND_PORT" > logs/backend.log 2>&1 &
BACKEND_PID=$!
echo "✅ Backend started with PID $BACKEND_PID"
echo "📜 Backend logs: $PROJECT_ROOT/logs/backend.log"
echo "----------------------------------------"

########################################
# 🎨 啟動 Frontend (Vite dev server)
########################################
echo "🎨 Step 3: Starting frontend (npm run dev -- --port $FRONTEND_PORT)..."
cd "$FRONTEND_DIR"

# 同樣把 log 存到 logs/frontend.log
mkdir -p logs
npm run dev -- --port "$FRONTEND_PORT" > logs/frontend.log 2>&1 &
FRONTEND_PID=$!
echo "✅ Frontend started with PID $FRONTEND_PID"
echo "📜 Frontend logs: $FRONTEND_DIR/logs/frontend.log"
echo "----------------------------------------"

########################################
# ✅ 健康檢查
########################################
cd "$PROJECT_ROOT"

echo "🩺 Step 4: Health checks..."

# Backend: 用 /api/posts 確認 API
health_check "Backend API" "http://127.0.0.1:${BACKEND_PORT}/api/posts" 25 1 || {
  echo "💥 Backend health check failed. Check logs/backend.log"
  exit 1
}

# Frontend: 根目錄 GET
health_check "Frontend UI" "http://127.0.0.1:${FRONTEND_PORT}" 25 1 || {
  echo "💥 Frontend health check failed. Check dlcs-ui/logs/frontend.log"
  exit 1
}

echo "✅✅ All systems go."
echo ""
echo "🌐 Backend API : http://127.0.0.1:${BACKEND_PORT}"
echo "🌐 Frontend UI : http://localhost:${FRONTEND_PORT}"
echo ""
echo "📌 提示："
echo "  - 查看 backend log: tail -f logs/backend.log"
echo "  - 查看 frontend log: cd dlcs-ui && tail -f logs/frontend.log"
echo ""
echo "🛑 要關閉全部服務，可在 terminal 按 Ctrl + C，"
echo "   或手動執行： kill ${BACKEND_PID} ${FRONTEND_PID}"
echo "----------------------------------------"