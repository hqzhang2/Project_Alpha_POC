"""
Trading Strategy Engine Portal
=============================
Main entry point for all trading strategies.
Default: PROD environment, Alpha Terminal dashboard.
"""
import os, warnings
warnings.filterwarnings("ignore")
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 8000

# ── Strategy Configuration ─────────────────────────────────────────────────────
STRATEGIES = {
    'alpha': {'name': 'Alpha Terminal', 'path': 'dashboard.html', 'prod': 9098, 'qa': 9099},
    'ns1':   {'name': 'NS-1',            'path': '',                'prod': 9199, 'qa': 9199},
    'ns2':   {'name': 'NS-2',            'path': '',                'prod': 3001, 'qa': 3001},
    'ns3':   {'name': 'NS-3',            'path': '',                'prod': 3000, 'qa': 3000},
}

def build_html():
    ts = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')
    strategies_json = str(STRATEGIES).replace("'", '"')
    
    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trading Strategy Engine</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{ height: 100%; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0a0a0f;
    color: #e8e8f0;
    display: flex;
    flex-direction: column;
    height: 100vh;
    overflow: hidden;
  }}
  .topbar {{
    background: #161b22;
    border-bottom: 1px solid #1e1e2e;
    padding: 0 20px;
    height: 48px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-shrink: 0;
  }}
  .topbar-left {{
    display: flex;
    align-items: center;
    gap: 24px;
  }}
  .logo {{
    font-size: 16px;
    font-weight: 700;
    color: #4ade80;
  }}
  .logo span {{ color: #e8e8f0; margin-left: 8px; }}
  .nav-tabs {{
    display: flex;
    gap: 4px;
  }}
  .nav-tab {{
    padding: 8px 16px;
    border-radius: 6px;
    background: transparent;
    border: none;
    color: #8888a0;
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    transition: all 0.15s;
  }}
  .nav-tab:hover {{ color: #e8e8f0; background: #21262d; }}
  .nav-tab.active {{ background: #21262d; color: #4ade80; }}
  .env-toggle {{
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .env-label {{ font-size: 12px; color: #6b7280; }}
  .env-btn {{
    padding: 4px 12px;
    border-radius: 4px;
    border: 1px solid #30363d;
    background: #12121a;
    color: #8888a0;
    cursor: pointer;
    font-size: 12px;
    font-weight: 600;
    transition: all 0.15s;
  }}
  .env-btn:hover {{ border-color: #4ade80; color: #4ade80; }}
  .env-btn.active {{ background: #14532d; border-color: #4ade80; color: #4ade80; }}
  .main-frame {{
    flex: 1;
    background: #0d1117;
    overflow: hidden;
    position: relative;
  }}
  .main-frame iframe {{
    width: 100%;
    height: 100%;
    border: none;
    background: #0d1117;
  }}
  .loading {{
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    color: #6b7280;
    font-size: 14px;
  }}
  .footer {{
    background: #161b22;
    border-top: 1px solid #1e1e2e;
    padding: 8px 20px;
    font-size: 11px;
    color: #484f58;
    display: flex;
    justify-content: space-between;
    flex-shrink: 0;
  }}
  .status-dot {{
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #4ade80;
    margin-right: 4px;
  }}
</style>
</head>
<body>
  <div class="topbar">
    <div class="topbar-left">
      <div class="logo"><span>Trading Strategy Engine</span></div>
      <div class="nav-tabs">
        <button class="nav-tab active" data-strategy="alpha" onclick="switchStrategy('alpha')">Alpha Terminal</button>
        <button class="nav-tab" data-strategy="ns1" onclick="switchStrategy('ns1')">NS-1</button>
        <button class="nav-tab" data-strategy="ns2" onclick="switchStrategy('ns2')">NS-2</button>
        <button class="nav-tab" data-strategy="ns3" onclick="switchStrategy('ns3')">NS-3</button>
      </div>
    </div>
    <div class="env-toggle">
      <span class="env-label">ENV:</span>
      <button class="env-btn active" id="btn-prod" onclick="setEnv('PROD')">PROD</button>
      <button class="env-btn" id="btn-qa" onclick="setEnv('QA')">QA</button>
    </div>
  </div>
  
  <div class="main-frame">
    <div class="loading" id="loading">Loading Alpha Terminal...</div>
    <iframe id="frame" style="display:none" onload="onFrameLoad()"></iframe>
  </div>
  
  <div class="footer">
    <div><span class="status-dot"></span><span id="status-text">Connected</span></div>
    <div><span id="env-display">PROD</span> | Port: <span id="port-display">9098</span> | {ts}</div>
  </div>

<script>
const STRATS = {strategies_json};

let currentEnv = 'PROD';
let currentStrategy = 'alpha';

function setEnv(env) {{
  currentEnv = env;
  document.querySelectorAll('.env-btn').forEach(function(b) {{ b.classList.remove('active'); }});
  document.getElementById('btn-' + env.toLowerCase()).classList.add('active');
  document.getElementById('env-display').textContent = env;
  switchStrategy(currentStrategy);
}}

function switchStrategy(strategy) {{
  currentStrategy = strategy;
  document.querySelectorAll('.nav-tab').forEach(function(b) {{ b.classList.remove('active'); }});
  document.querySelector('[data-strategy="' + strategy + '"]').classList.add('active');
  
  var s = STRATS[strategy];
  var port = s[currentEnv.toLowerCase()];
  var path = s.path || '';
  
  document.getElementById('port-display').textContent = port;
  document.getElementById('loading').style.display = 'flex';
  document.getElementById('frame').style.display = 'none';
  document.getElementById('frame').src = 'http://localhost:' + port + '/' + path;
}}

function onFrameLoad() {{
  document.getElementById('loading').style.display = 'none';
  document.getElementById('frame').style.display = 'block';
}}

// Initial load - Alpha Terminal PROD dashboard
document.getElementById('frame').src = 'http://localhost:9098/dashboard.html';
</script>
</body>
</html>'''
    return html

class PortalHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        super().end_headers()

    def do_GET(self):
        if self.path == '/' or self.path == '/index.html':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(self._html().encode())
            return
        self.send_response(404)
        self.end_headers()
        self.wfile.write(b'Not Found')
    
    def _html(self):
        return build_html()

if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', PORT), PortalHandler)
    print(f'Trading Strategy Engine: http://localhost:{PORT}')
    server.serve_forever()