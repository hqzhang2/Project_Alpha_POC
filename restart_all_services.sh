#!/bin/bash
#
# restart_all_services.sh
# Restarts all Nine Street and Alpha Terminal services
# Run after system restart or to sync ports
#
set -e

echo "============================================="
echo "Restarting all services..."
echo "============================================="

# ── Kill all targets first ──────────────────────────────────────────────
for PORT in 8000 9098 9099 9199 9100 9105 9206 9210 3000 3005; do
    lsof -ti:$PORT | xargs kill -9 2>/dev/null || true
done
sleep 1

# ── 1. Portal (port 8000) ──────────────────────────────────────
echo "[1/6] Portal (8000)..."
cd /Users/chuck/.openclaw/workspace/Project_Nine_Street
nohup python3 portal.py > portal.log 2>&1 &

# ── 2. Alpha Terminal PROD (9098) ────────────────────────────────
echo "[2/6] Alpha Terminal PROD (9098)..."
cd /Users/chuck/.openclaw/workspace/Project_Sequoia/terminal
PORT=9098 ENV=PROD nohup python3 server.py > server_9098.log 2>&1 &

# ── 3. Alpha Terminal QA (9099) ────────────────────────────────
echo "[3/6] Alpha Terminal QA (9099)..."
cd /Users/chuck/.openclaw/workspace/Project_Sequoia/QA_terminal
PORT=9099 ENV=QA nohup python3 server.py > qa.log 2>&1 &

# ── 4. NS-1 PROD (9199) ────────────────────────────────────────
echo "[4/6] NS-1 PROD (9199)..."
cd /Users/chuck/.openclaw/workspace/Project_Nine_Street/NS-1_PROD
PORT=9199 ENV=PROD nohup python3 server.py > ns1.log 2>&1 &

# ── 5. NS-3 Backend (9206) ────────────────────────────────────
echo "[5/7] NS-3 Backend (9206)..."
cd /Users/chuck/.openclaw/workspace/Project_Nine_Street/NS-3_PROD/backend
nohup python3 -m uvicorn main:app --host 0.0.0.0 --port 9206 > backend.log 2>&1 &

# ── 6. NS-4 Backend (9210) ────────────────────────────────────
echo "[6/7] NS-4 Ratio Trading (9210)..."
cd /Users/chuck/.openclaw/workspace/Project_Nine_Street/NS-4_PROD/backend
nohup python3 -m uvicorn main:app --host 0.0.0.0 --port 9210 > backend.log 2>&1 &

# ── 7. NS-3 Frontend DEV (3000) ─────────────────────────────
echo "[7/7] NS-3 Frontend DEV (3000)..."
cd /Users/chuck/.openclaw/workspace/Project_Nine_Street/NS-3_PROD/frontend
nohup npm run dev -- -p 3000 > frontend.log 2>&1 &

sleep 3

# ── Verify ────────────────────────────────────────────────────────
echo ""
echo "============================================="
echo "Service Status:"
echo "============================================="
for PORT in 8000 9098 9099 9199 9206 3000; do
    if lsof -i :$PORT | grep -q LISTEN; then
        echo "  ✅ $PORT - UP"
    else
        echo "  ❌ $PORT - DOWN"
    fi
done

echo ""
echo "Portal:       http://localhost:8000"
echo "Alpha PROD:   http://localhost:9098"
echo "Alpha QA:     http://localhost:9099"
echo "NS-1 PROD:   http://localhost:9199"
echo "NS-3 API:    http://localhost:9206"
echo "NS-4 API:    http://localhost:9210"
echo "NS-3 UI:     http://localhost:3000"
echo "============================================="