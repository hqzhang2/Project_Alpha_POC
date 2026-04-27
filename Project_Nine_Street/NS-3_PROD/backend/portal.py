"""
Portal Backend - Strategy Dashboard Hub
=====================================
Serves as the main entry point for all trading strategies.
"""

import warnings
warnings.filterwarnings("ignore")

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Trading Portal", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

PORT = 8000

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🛠️ Trading Strategies Portal</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0d1117;
            color: #e6edf3;
            min-height: 100vh;
            padding: 24px;
        }
        h1 {
            font-size: 28px;
            margin-bottom: 8px;
        }
        .subtitle {
            color: #8b949e;
            margin-bottom: 32px;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 16px;
            max-width: 1200px;
        }
        .card {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 20px;
            transition: all 0.2s;
        }
        .card:hover {
            border-color: #58a6ff;
            transform: translateY(-2px);
        }
        .card h2 {
            font-size: 18px;
            margin-bottom: 8px;
        }
        .card p {
            color: #8b949e;
            font-size: 14px;
            margin-bottom: 16px;
        }
        .card .badge {
            display: inline-block;
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 12px;
            font-weight: 600;
        }
        .badge-active { background: #238636; color: white; }
        .badge-beta { background: #1f6feb; color: white; }
        .badge-prod { background: #238636; color: white; }
        .card a {
            display: inline-block;
            margin-top: 12px;
            color: #58a6ff;
            text-decoration: none;
            font-weight: 500;
        }
        .card a:hover {
            text-decoration: underline;
        }
        .footer {
            margin-top: 48px;
            color: #484f58;
            font-size: 12px;
        }
    </style>
</head>
<body>
    <h1>🛠️ Trading Strategies Portal</h1>
    <p class="subtitle">Central hub for all quantitative trading systems</p>
    
    <div class="grid">
        <!-- NS-2 Alpha Terminal -->
        <div class="card">
            <h2>Alpha Terminal (NS-2)</h2>
            <p>Regime-based trading with HMM detection. Features: Dashboard, OMON, Option Screener, Heatmap, Earnings Estimates.</p>
            <span class="badge badge-prod">PROD</span>
            <br>
            <a href="http://localhost:9098">Open Terminal →</a>
        </div>
        
        <!-- NS-3 Sector Rotation -->
        <div class="card">
            <h2>NS-3: Sector Rotation</h2>
            <p>3-Tier system: Sector rotation → ETF signal engine → Stock selection. Based on Claudealgo methodology.</p>
            <span class="badge badge-active">ACTIVE</span>
            <br>
            <a href="http://localhost:3000">Open Dashboard →</a>
        </div>
        
        <!-- NS-2 Backend API -->
        <div class="card">
            <h2>NS-2 API</h2>
            <p>Backend API for regime detection and signal generation. Returns ticker data with HMM regimes.</p>
            <span class="badge badge-prod">PROD</span>
            <br>
            <a href="http://localhost:9098/api/v1/health">API Health →</a>
        </div>
        
        <!-- NS-3 Backend API -->
        <div class="card">
            <h2>NS-3 API</h2>
            <p>Backend API for 3-tier sector rotation. Endpoints: /tier1, /tier2, /tier3, /all.</p>
            <span class="badge badge-active">ACTIVE</span>
            <br>
            <a href="http://localhost:9206/api/v1/health">API Health →</a>
        </div>
    </div>
    
    <div class="footer">
        <p>Last Updated: """ + str(__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')) + """</p>
    </div>
</body>
</html>
"""

@app.get("/")
def portal():
    return HTMLResponse(HTML)

@app.get("/api/strategies")
def strategies():
    return {
        "strategies": [
            {
                "name": "Alpha Terminal (NS-2)",
                "path": "NS-2",
                "port": 9098,
                "status": "prod",
                "description": "Regime-based trading with HMM"
            },
            {
                "name": "NS-3: Sector Rotation",
                "path": "NS-3",
                "port": 9206,
                "status": "active",
                "description": "3-Tier sector rotation system"
            }
        ]
    }

@app.get("/api/v1/health")
def health():
    return {"status": "ok", "service": "Trading Portal", "port": PORT}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)