#!/usr/bin/env python3
"""
NS-3 QA Server
Serves dashboard HTML + proxies to PROD API (or runs local copy).
"""
import os
import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

dashboard_dir = Path(__file__).parent
dashboard_path = dashboard_dir / "ns3_dashboard.html"

app = FastAPI(title="NS-3 QA", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Serve dashboard
if dashboard_path.exists():
    @app.get("/")
    async def root():
        return FileResponse(dashboard_path)

    @app.get("/ns3_dashboard.html")
    async def dashboard():
        return FileResponse(dashboard_path)

# Proxy API to PROD (or run local copy)
# For now, we'll run a local copy of the API
ns3_prod_path = dashboard_dir.parent / "NS-3_PROD" / "backend"
sys.path.insert(0, str(ns3_prod_path))

import main as prod_main

# Copy all routes from prod app
for route in prod_main.app.routes:
    app.routes.append(route)

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 9237))
    print(f"NS-3 QA running on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)