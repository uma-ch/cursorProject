#!/usr/bin/env bash
set -euo pipefail

NUM_WORKERS="${1:-5}"
SERVER_PORT=8080
MANAGER_PORT=9090
BASE_WORKER_PORT=8081
HUB_WS_URL="ws://localhost:${SERVER_PORT}/ws/worker"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# shellcheck source=.env
if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

SERVER_PID=""
MANAGER_PID=""

cleanup() {
    echo ""
    echo "Shutting down..."
    python worker_manager.py stop-all 2>/dev/null || true
    [ -n "$MANAGER_PID" ] && kill "$MANAGER_PID" 2>/dev/null || true
    [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true
    wait 2>/dev/null || true
    echo "All processes stopped."
}
trap cleanup EXIT

echo "Starting server on port ${SERVER_PORT}..."
PORT=$SERVER_PORT python server.py &
SERVER_PID=$!

echo "Waiting for server to be ready..."
for i in $(seq 1 20); do
    if curl -so /dev/null "http://localhost:${SERVER_PORT}/healthz" 2>/dev/null; then
        echo "Server is ready."
        break
    fi
    if [ "$i" -eq 20 ]; then
        echo "Server failed to start within 10s."
        exit 1
    fi
    sleep 0.5
done

echo "Configuring worker pool..."
python worker_manager.py init --hub-url "$HUB_WS_URL" --base-port "$BASE_WORKER_PORT"

echo "Spawning ${NUM_WORKERS} worker(s)..."
python worker_manager.py add --count "$NUM_WORKERS"

echo "Starting worker manager UI on port ${MANAGER_PORT}..."
python worker_manager.py serve --port "$MANAGER_PORT" &
MANAGER_PID=$!

echo ""
echo "========================================="
echo "  All services running"
echo "========================================="
echo "  Server dashboard:  http://localhost:${SERVER_PORT}/static/index.html"
echo "  Worker manager UI: http://localhost:${MANAGER_PORT}/static/manager.html"
echo "  Workers: ${NUM_WORKERS}"
echo "========================================="
echo ""
echo "Press Ctrl-C to stop everything."

wait
