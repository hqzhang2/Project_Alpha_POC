#!/usr/bin/env python3
"""
Trading Strategy Engine Portal - QA Environment
==============================================
Aggregates Alpha Terminal + Nine Street projects.
Default: QA environment.
"""
import os
import json
import warnings
warnings.filterwarnings("ignore")
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 8000

def _last_updated():
    """Date of the last commit touching portal.py (repo-relative), with
    file-mtime fallback — the label never needs a manual bump again."""
    import datetime as _dt
    import subprocess as _sp
    here = os.path.dirname(os.path.abspath(__file__))
    try:
        r = _sp.run(["git", "-C", here, "log", "-1", "--format=%cs", "--",
                     "Project_Nine_Street/portal.py"],
                    capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception:
        pass
    try:
        return _dt.date.fromtimestamp(os.path.getmtime(__file__)).isoformat()
    except Exception:
        return "2026-08-06"

LAST_UPDATED = _last_updated()

# Strategy Configuration
STRATEGIES = {
    'alpha': {'name': 'Alpha Terminal', 'path': 'dashboard.html', 'prod': 9098, 'qa': 9099},
    'ns1':   {'name': 'NS-1', 'path': 'index.html', 'prod': 9218, 'qa': 9219},
    'ns2':   {'name': 'NS-2 (MAG7 HMM)', 'path': '', 'prod': 9228, 'qa': 9229},
    'ns3':   {'name': 'NS-3 (Sector Rotation)', 'path': 'ns3_dashboard.html', 'prod': 9236, 'qa': 9237},
    'ns4':   {'name': 'NS-4 (Ratio Trading)', 'path': 'ns4_dashboard.html', 'prod': 9240, 'qa': 9241},
    'ns5':   {'name': 'NS-5 (Portfolio Grading)', 'path': 'ns5_dashboard.html', 'prod': 9250, 'qa': 9251},
    'ns6':   {'name': 'NS-6 (Drawdown Engine)', 'path': 'ns6_dashboard.html', 'prod': 9260, 'qa': 9261},
    'ns7':   {'name': 'NS-7 (Growth/Momentum)', 'path': 'ns7_dashboard.html', 'prod': 9270, 'qa': 9271},
    'ns8':   {'name': 'NS-8 (Tactical Alloc)', 'path': 'dashboard', 'prod': 9280, 'qa': 9281},
    'nsx':   {'name': 'NS-X (Strategy Alloc)', 'path': 'nsx_dashboard.html', 'prod': 9290, 'qa': 9291},
}

# HTML Template with all braces escaped for Python .format()
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trading Strategy Engine - QA</title>
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
    overflow-x: auto;
    scrollbar-width: none;
    -ms-overflow-style: none;
  }}
  .nav-tabs::-webkit-scrollbar {{ display: none; }}
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
  .status-indicator {{
    display: inline-block;
    width: 8px;
    height: 8px;
    border-radius: 50%;
    margin-right: 6px;
    background: #6b7280; /* gray - unknown */
    border: 1px solid #333;
  }}
  .status-indicator.up { background: #3fb950; border-color: #2ea043; }
  .status-indicator.down { background: #f85149; border-color: #da3633; }

</style>
</head>
<body>
  <div class="topbar">
    <div class="topbar-left">
      <div class="logo"><span>Trading Strategy Engine</span><div style="font-size: 9px; color: #8b949e; margin-left: 8px; white-space: nowrap;">v3.9.0 | Last Updated: {last_updated}</div></div>
      <div class="nav-tabs">
              <button class="nav-tab active" data-strategy="alpha" onclick="switchStrategy('alpha')"><span class="status-indicator" id="status-alpha"></span>Alpha Terminal</button>
              <button class="nav-tab" data-strategy="ns1" onclick="switchStrategy('ns1')"><span class="status-indicator" id="status-ns1"></span>NS-1</button>
              <button class="nav-tab" data-strategy="ns2" onclick="switchStrategy('ns2')"><span class="status-indicator" id="status-ns2"></span>NS-2</button>
              <button class="nav-tab" data-strategy="ns3" onclick="switchStrategy('ns3')"><span class="status-indicator" id="status-ns3"></span>NS-3</button>
              <button class="nav-tab" data-strategy="ns4" onclick="switchStrategy('ns4')"><span class="status-indicator" id="status-ns4"></span>NS-4</button>
              <button class="nav-tab" data-strategy="ns5" onclick="switchStrategy('ns5')"><span class="status-indicator" id="status-ns5"></span>NS-5</button>
              <button class="nav-tab" data-strategy="ns6" onclick="switchStrategy('ns6')"><span class="status-indicator" id="status-ns6"></span>NS-6</button>
              <button class="nav-tab" data-strategy="ns7" onclick="switchStrategy('ns7')"><span class="status-indicator" id="status-ns7"></span>NS-7</button>
              <button class="nav-tab" data-strategy="ns8" onclick="switchStrategy('ns8')"><span class="status-indicator" id="status-ns8"></span>NS-8</button>
              <button class="nav-tab" data-strategy="nsx" onclick="switchStrategy('nsx')"><span class="status-indicator" id="status-nsx"></span>NS-X</button>
            </div>
    </div>
    <div class="env-toggle">
      <span class="env-label">ENV:</span>
      <button class="env-btn" id="btn-prod" onclick="setEnv('PROD')">PROD</button>
      <button class="env-btn active" id="btn-qa" onclick="setEnv('QA')">QA</button>
    </div>
  </div>

  <div class="main-frame">
    <div class="loading" id="loading">Loading Alpha Terminal (QA)...</div>
    <iframe id="frame" style="display:none" onload="onFrameLoad()"></iframe>
  </div>

  <div class="footer">
    <div><span class="status-dot"></span><span id="status-text">Connected</span></div>
    <div><span id="env-display">QA</span> | Port: <span id="port-display">9099</span> | {ts}</div>
  </div>

<script>
const STRATS = {strategies_json};

let currentEnv = 'QA';
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
  var path = s.path || s.dashboard || '';

  document.getElementById('port-display').textContent = port;
  document.getElementById('loading').style.display = 'flex';
  document.getElementById('frame').style.display = 'none';
  document.getElementById('frame').src = 'http://localhost:' + port + '/' + path;
}}

function onFrameLoad() {{
  document.getElementById('loading').style.display = 'none';
  document.getElementById('frame').style.display = 'block';
}}

// Initial load - Alpha Terminal QA
document.getElementById('frame').src = 'http://localhost:9099/dashboard.html';

// Health check system for status indicators
const SERVICE_ENDPOINTS = {
  'alpha': '/dashboard.html',
  'ns1': '/health',
  'ns2': '/health',
  'ns3': '/health',
  'ns4': '/health',
  'ns5': '/health',
  'ns6': '/health',
  'ns7': '/health',
  'ns8': '/health',
  'nsx': '/health'
};

async function checkServiceHealth(key, port, path) {
  try {
    const res = await fetch(`http://localhost:${port}${path}`, { 
      method: 'GET', 
      cache: 'no-cache',
      timeout: 3000 
    });
    return res.ok;
  } catch (e) {
    return false;
  }
}

async function updateStatusIndicators() {
  // Derived from STRATS — a new service tab gets its health light for free
  // (the hardcoded array missed ns7 when it was added).
  const strategies = Object.keys(STRATS);
  for (const key of strategies) {
    const s = STRATS[key];
    if (!s) continue;
    
    const port = s[currentEnv.toLowerCase()];
    const path = SERVICE_ENDPOINTS[key] || '/health';
    const isUp = await checkServiceHealth(key, port, path);
    
    const tab = document.querySelector(`[data-strategy="${{key}}"]`);
    const dot = tab ? tab.querySelector('.status-indicator') : null;
    if (dot) {{
      dot.classList.remove('up', 'down');
      dot.classList.add(isUp ? 'up' : 'down');
      dot.title = `${{key.toUpperCase()}}: ${{isUp ? 'UP' : 'DOWN'}}`;
    }}
  }
}

// Initial check
updateStatusIndicators();
// Poll every 30 seconds
setInterval(updateStatusIndicators, 30000);
</script>
</body>
</html>"""

def build_html():
    ts = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')
    strategies_json = __import__('json').dumps(STRATEGIES)
    # Template uses {{ }} (escaped for the old .format() era). Unescape the
    # CSS/JS braces FIRST, then splice in the already-valid JSON token — so the
    # JSON's own braces are never touched.
    html = HTML_TEMPLATE.replace('{{', '{').replace('}}', '}')
    html = html.replace('{ts}', ts)
    html = html.replace('{last_updated}', LAST_UPDATED)
    html = html.replace('{strategies_json}', strategies_json)
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

        if self.path == '/api/health':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            statuses = {}
            for key, cfg in STRATEGIES.items():
                port = cfg.get('qa', cfg.get('prod', 0))
                statuses[key] = 'up' if port else 'down'
            self.wfile.write(json.dumps(statuses).encode())
            return

        self.send_response(404)
        self.end_headers()
        self.wfile.write(b'Not Found')

    def _html(self):
        return build_html()

if __name__ == '__main__':
    server = HTTPServer(('0.0.0.0', PORT), PortalHandler)
    print(f'Trading Strategy Engine (QA): http://localhost:{PORT}')
    server.serve_forever()