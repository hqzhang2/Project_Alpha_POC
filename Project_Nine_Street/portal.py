"""
Portal Server - Nine Street Strategy Dashboard
=======================================
Main entry point for all trading strategies.
Default env: PROD | Toggle to QA.
"""
import os, warnings
warnings.filterwarnings("ignore")
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 8000

class PortalHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            import datetime
            ts = datetime.datetime.now().strftime('%Y-%m-%d %H:%M')
            self.wfile.write(self._html(ts).encode())
            return
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b'Not Found')

    def _html(self, ts):
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Nine Street | Strategy Portal</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0a0a0f;
    color: #e8e8f0;
    min-height: 100vh;
    padding: 24px 32px;
  }}
  .header {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 32px;
    padding-bottom: 16px;
    border-bottom: 1px solid #1e1e2e;
  }}
  .header h1 {{ font-size: 20px; font-weight: 600; letter-spacing: -0.5px; }}
  .header h1 span {{ color: #4ade80; }}
  .env-toggle {{ display: flex; gap: 8px; align-items: center; }}
  .env-badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .env-badge.prod {{ background: #14532d; color: #4ade80; }}
  .env-badge.qa {{ background: #1e3a5f; color: #60a5fa; }}
  .env-btn {{
    padding: 6px 16px;
    border-radius: 6px;
    border: 1px solid #2a2a3e;
    background: #12121a;
    color: #8888a0;
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    transition: all 0.15s;
  }}
  .env-btn:hover {{ border-color: #4ade80; color: #4ade80; }}
  .env-btn.active {{ background: #14532d; border-color: #4ade80; color: #4ade80; }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
    gap: 12px;
    max-width: 1200px;
  }}
  .card {{
    background: #111118;
    border: 1px solid #1a1a28;
    border-radius: 10px;
    padding: 20px 24px;
    transition: border-color 0.2s;
  }}
  .card:hover {{ border-color: #2d2d48; }}
  .card.alpha {{ border-left: 3px solid #f59e0b; }}
  .card.ns1 {{ border-left: 3px solid #06b6d4; }}
  .card.ns2 {{ border-left: 3px solid #8b5cf6; }}
  .card.ns3 {{ border-left: 3px solid #ec4899; }}
  .card h2 {{ font-size: 15px; font-weight: 600; margin-bottom: 6px; }}
  .card p {{ color: #6b7280; font-size: 12px; margin-bottom: 14px; line-height: 1.5; }}
  .card .tag {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 12px;
  }}
  .tag-alpha {{ background: #451a03; color: #f59e0b; }}
  .tag-ns1 {{ background: #083344; color: #06b6d4; }}
  .tag-ns2 {{ background: #2e1065; color: #a78bfa; }}
  .tag-ns3 {{ background: #500724; color: #f472b6; }}
  .card .port {{ font-size: 11px; color: #4b5563; font-family: monospace; margin-bottom: 12px; }}
  .card a {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    border-radius: 6px;
    border: 1px solid #2a2a3e;
    background: transparent;
    color: #d1d5e0;
    text-decoration: none;
    font-size: 13px;
    font-weight: 500;
    transition: all 0.15s;
    margin-right: 8px;
    margin-bottom: 6px;
  }}
  .card a:hover {{ background: #1a1a28; border-color: #4ade80; color: #4ade80; }}
  .card a.primary {{ background: #14532d; border-color: #4ade80; color: #4ade80; }}
  .card a.primary:hover {{ background: #166534; }}
  .card a.qa-link {{ background: #0c2444; border-color: #60a5fa; color: #60a5fa; }}
  .card a.qa-link:hover {{ background: #1e3a5f; }}
  .footer {{ margin-top: 48px; color: #374151; font-size: 11px; }}
</style>
</head>
<body>
<div class="header">
  <h1>🛠️ <span>Nine Street</span> Strategy Portal</h1>
  <div class="env-toggle">
    <span class="env-badge prod" id="envBadge">PROD</span>
    <button class="env-btn active" id="btn-prod" onclick="setEnv('PROD')">PROD</button>
    <button class="env-btn" id="btn-qa" onclick="setEnv('QA')">QA</button>
  </div>
</div>

<div class="grid">
  <div class="card alpha">
    <span class="tag tag-alpha">Alpha Terminal</span>
    <p>Project Sequoia. Real-time quotes, options, heatmap, financials, SEC filings, earnings estimates.</p>
    <div class="port">PROD: <span id="alpha-prod-port">9098</span> &nbsp;|&nbsp; QA: <span id="alpha-qa-port">9099</span></div>
    <a href="http://localhost:9098" class="primary" target="_blank" id="alpha-prod">&#x2192; Alpha PROD</a>
    <a href="http://localhost:9099" class="qa-link" target="_blank" id="alpha-qa">&#x2192; Alpha QA</a>
  </div>

  <div class="card ns1">
    <span class="tag tag-ns1">NS-1</span>
    <p>Original Nine Street system. Sector momentum, live feed, portfolio tracker.</p>
    <div class="port">PROD: <span id="ns1-prod-port">9199</span> &nbsp;|&nbsp; QA: <span id="ns1-qa-port">9199</span></div>
    <a href="http://localhost:9199" class="primary" target="_blank" id="ns1-prod">&#x2192; NS-1 PROD</a>
    <a href="http://localhost:9199" class="qa-link" target="_blank" id="ns1-qa">&#x2192; NS-1 QA</a>
  </div>

  <div class="card ns2">
    <span class="tag tag-ns2">NS-2</span>
    <p>Regime-based trading. HMM detection, EMAs, MACD, RSI, signal generation.</p>
    <div class="port">PROD: <span id="ns2-prod-port">9098</span> &nbsp;|&nbsp; QA: <span id="ns2-qa-port">9099</span></div>
    <a href="http://localhost:9098" class="primary" target="_blank" id="ns2-prod">&#x2192; NS-2 PROD</a>
    <a href="http://localhost:9099" class="qa-link" target="_blank" id="ns2-qa">&#x2192; NS-2 QA</a>
  </div>

  <div class="card ns3">
    <span class="tag tag-ns3">NS-3</span>
    <p>3-Tier sector rotation. Sector &rarr; ETF signals &rarr; Stock selection pipeline.</p>
    <div class="port">PROD: <span id="ns3-prod-port">9206</span> &nbsp;|&nbsp; QA: <span id="ns3-qa-port">9206</span></div>
    <a href="http://localhost:3000" class="primary" target="_blank" id="ns3-prod">&#x2192; NS-3 PROD</a>
    <a href="http://localhost:3000" class="qa-link" target="_blank" id="ns3-qa">&#x2192; NS-3 QA</a>
  </div>

  <div class="card ns4">
    <span class="tag tag-ns4">NS-4</span>
    <p>Ratio trading system. DBC/SPY, TLT/IEF, EEM/SPY, and sector pair momentum.</p>
    <div class="port">PROD: <span id="ns4-prod-port">9210</span> &nbsp;|&nbsp; QA: <span id="ns4-qa-port">9210</span></div>
    <a href="http://localhost:9210/api/v1/rankings" class="primary" target="_blank" id="ns4-prod">&#x2192; NS-4 PROD</a>
    <a href="http://localhost:9210/api/v1/rankings" class="qa-link" target="_blank" id="ns4-qa">&#x2192; NS-4 QA</a>
  </div>
</div>

<div class="footer">
  Portal v1.0 | Nine Street &middot; {ts} &middot; ENV=PROD default
</div>

<script>
const PORTS = {{
  alpha: {{PROD: 9098, QA: 9099}},
  ns1:   {{PROD: 9199, QA: 9199}},
  ns2:   {{PROD: 9098, QA: 9099}},
  ns3:   {{PROD: 9206, QA: 9206}},
  ns4:   {{PROD: 9210, QA: 9210}},
}};

let currentEnv = 'PROD';

function setEnv(env) {{
  currentEnv = env;
  document.querySelectorAll('.env-btn').forEach(function(b) {{ b.classList.remove('active'); }});
  document.getElementById('btn-' + env.toLowerCase()).classList.add('active');
  document.getElementById('envBadge').textContent = env;
  document.getElementById('envBadge').className = 'env-badge ' + env.toLowerCase();
  for (const [key, ports] of Object.entries(PORTS)) {{
    var el = document.getElementById(key + '-' + env.toLowerCase());
    if (el) el.href = 'http://localhost:' + ports[env];
    var portEl = document.getElementById(key + '-' + env.toLowerCase() + '-port');
    if (portEl) portEl.textContent = ports[env];
  }}
}}

setEnv('PROD');
</script>
</body>
</html>
"""

if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', PORT), PortalHandler)
    import datetime
    print(f'Portal: http://localhost:{PORT} ({datetime.datetime.now().strftime("%Y-%m-%d %H:%M")})')
    server.serve_forever()