#!/usr/bin/env python3
"""
NS-4 QA Runner - Serves dashboard HTML + API on QA port 9211
"""
import os, sys
import warnings
warnings.filterwarnings("ignore")

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

dashboard_dir = os.path.dirname(os.path.abspath(__file__))
ns4_dir = os.path.join(dashboard_dir, '..', 'NS-4_PROD', 'backend')
sys.path.insert(0, ns4_dir)

import main as prod_main

app = FastAPI(title="NS-4 QA", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

# Copy all routes from prod app
for route in prod_main.app.routes:
    app.routes.append(route)

# Serve dashboard
dash_path = os.path.join(dashboard_dir, 'ns4_dashboard.html')
if os.path.exists(dash_path):
    @app.get("/")
    async def root():
        return FileResponse(dash_path)
    @app.get("/ns4_dashboard.html")
    async def dashboard():
        return FileResponse(dash_path)

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 9211))
    print(f"NS-4 QA running on port {PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)