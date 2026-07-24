# MEMORY.md - Chuck's Long-Term Memory

## Email Accounts:
- munger6c@gmail.com (password: Chuck108d#, App Password: vvqwcmdgjyhfpjhp)

## Discord Information:
- Hong's User ID: 1467629773663768830
- Primary Channel ID: 1467635682619953164
- Server (Guild) ID: 1467635681948995739
- Strategy Channel ID: 1486569927086313623 (#sequoia-strategy)
- Equity Channel ID: 1486570257312256010 (#sequoia-equity)
- Market Updates Channel ID: 1486570382617215046 (#sequoia-market-updates)
- Trade Ideas Channel ID: 1486570528318951435 (#sequoia-trade-ideas)

## Project Sequoia (Hedge Fund with AI Agents) - DEPRIORITIZED
- **Status:** Deprioritized (as of April 2026). Primary focus shifted to Project Nine Street.
- **Started:** March 1, 2026
- **Role:** COO
- **Initial Planning Doc:** Project_Sequoia/01_Initial_Planning.md
- **Team Doc:** Project_Sequoia/TEAM.md
- **Investment Vehicles:** Equity stocks, index ETFs, commodity ETFs, international ETFs, options (covered calls, puts)
- **Restrictions:** No margin, no futures, no short selling, no naked options
- **Target Return:** 10-20% annualized
- **Max Drawdown:** single digit %
- **Time Constraint:** Max 1 hour/day

## ⚠️ Critical Development Rules (Alpha Terminal):
1. **No changes in Production environment (`Project_Sequoia/terminal/`)** until a formal release branch is cut.
2. **All development work MUST go to the QA environment (`Project_Sequoia/QA_terminal/`).**
3. **Ports are STRICT:** Production is ALWAYS `9098`. QA is ALWAYS `9099`.
4. **Never bypass `deploy.sh`:** Do not manually start servers. The `deploy.sh` scripts set critical `PORT` and `ENV` variables. Missing these will cause QA files to overlap onto the Production port.

## Human Team Members:
| Role | Name | Focus |
|------|------|-------|
| Lead Investor | Hong | Overall direction |
| Co-investor | TBD | |

## System Events:
- On 2026-02-22, Hong reported a power failure and subsequently set up auto-start for the system.
- On 2026-02-22, Hong requested to defer the data presentation discussion and resume "project alpha poc" tomorrow.
- On 2026-03-18, Completed Sprint 1 (KAN-2, KAN-3, KAN-4, KAN-7 → Done), Created Sprint 2 (KAN-8), Broke down EPICs KAN-5 and KAN-6 into 10 subtasks.
- On 2026-03-18 (PM), Created comprehensive documentation for all 10 subtasks (KAN-9 through KAN-18) in Project_Sequoia/docs/
- On 2026-03-27, Implemented instant followup workflow for sequoia-strategy channel - after Hong posts in strategy/equity/trade-ideas, team automatically responds within minutes
- On 2026-03-31, Released Terminal Alpha v1.3.1: Unified and refactored backend server, added comprehensive full-tab regression testing suite covering Dashboard, OMON, Ratio Analysis, and Financials.
- **2026-07-21:** Restored NS-1/NS-3/NS-4 QA servers with full dashboard APIs. Created release/v1.6 and feature/v1.7 branches.
- **2026-07-23:** 52-Week Highs feature completed in Alpha Terminal QA (feature/v1.8): Market Cap (B) column, finviz ticker cleanup (AADM→ADM etc), OMON-style toolbar + container panel with gradient accent bar, sticky header, sort arrows (▲=asc/▼=desc), zebra striping, removed $1B filter & Reload button. Launchd daily update job at 5pm ET Mon-Fri. All changes in feature/v1.8 branch.

## Project Nine Street - Quantitative Trading System
**Status:** ACTIVE PRIMARY PROJECT
**Folder:** `/Users/chuck/Project_Alpha_POC/Project_Nine_Street/`

### Core Tenets:
1. **Persona:** Quantitative Trader. Software is strictly for internal alpha generation and monetization, not for external distribution.
2. **Methodology:** Institutional-grade. Combine PhD-level quant libraries with existing tools to filter false signals and optimize the success ratio.
3. **Objective:** Continuous model improvement focused on maximizing risk-adjusted returns (better returns, less risk).
4. **Strategy:** Creative and adaptive. Emphasize regime change detection, volatility shifts, and balancing predictive modeling with reactive detection.

### 4-Layer Architecture (via claude.md):
- **Layer 1 (Feature Engineering):** Derive TA indicators (ADX, RSI, ATR, BB) and cross-asset signals (credit spreads, VIX) to feed into state models, compressing price/volume history into regime-relevant features.
- **Layer 2 (State Estimation):** Use GMM (Gaussian Mixture Models) for clustering and HMM (Hidden Markov Models) for latent regime detection and transition probabilities.
- **Layer 3 (Signal Generation):** Generate momentum/mean-reversion signals scaled by Layer 2 state confidence (e.g. suppress momentum signals in choppy regimes).
- **Layer 4 (Position Sizing & Execution):** Volatility-target sizing adjusted by regime uncertainty and transaction costs.

## Project Nine Street - Directory Structure & Ports
**Root:** `/Users/chuck/Project_Alpha_POC/Project_Nine_Street/`

### QA Environment (Active):
- `NS_1_QA/` - NS-1 Alpha Engine (server_qa.py + index.html) - Port 9219
- `NS-3_QA/` - NS-3 3-Tier Sector Rotation (qa_server.py) - Port 9237
- `NS-4_QA/` - NS-4 Ratio Trading (qa_server.py) - Port 9241
- `portal_qa.py` - Portal - Port 8000

### PROD Environment:
- `NS_PROD/` - NS-PROD (Next.js frontend + backend)
- `NS-3_PROD/` - NS-3 PROD backend - Port 9236
- `NS-4_PROD/` - NS-4 PROD backend - Port 9240

### Alpha Terminal:
- `Project_Sequoia/QA_terminal/` - QA - Port 9099
- `Project_Sequoia/terminal/` - PROD - Port 9098

**⚠️ RULE #1: Every page/code created MUST be documented in MEMORY.md and committed to GitHub. No memory loss.**

**⚠️ RULE #2: SDLC - Never skip steps.**

## SDLC Process (MUST FOLLOW)
### Development Workflow:
1. Develop in workspace/feature branch
2. Deploy to QA (NOT PROD)
3. User tests/verifies in QA
4. Add unit tests (>60% coverage required)
5. Run regression testing
6. Create release branch
7. Deploy to PROD

### NEVER:
- Deploy directly to PROD without QA
- Skip unit tests
- Skip regression testing
- Skip user verification in QA
- Skip release branch

### QA vs PROD Ports:
| Service | QA | PROD |
|--------|-----|------|
| Alpha Terminal | 9099 | 9098 |
| NS-1 | 9219 | 9218 |
| NS-3 | 9237 | 9236 |
| NS-4 | 9241 | 9240 |
| Portal | 8000 | 8000 |

## Running QA Services (Project Nine Street) - Verified 2026-07-21
| Service | Port | Status | Endpoints |
|---------|-----|--------|-----------|
| Portal | 8000 | ✅ | / |
| Alpha Terminal QA | 9099 | ✅ | /dashboard.html |
| NS-1 QA | 9219 | ✅ | /, /api/nsae, /api/nsoe, /api/backtest, /api/chart, /api/live_feed |
| NS-3 QA | 9237 | ✅ | /, /api/v1/tier1, /api/v1/tier2, /api/v1/tier3, /api/v1/regime, /api/v1/holdings |
| NS-4 QA | 9241 | ✅ | /, /api/v1/all, /api/v1/pairs, /health |

## Project Nine Street - NS-1 (Alpha Engine)
**Status:** ACTIVE - QA restored 2026-07-21
**Folder:** `/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS_1_QA/`
**Port:** 9219 (QA) / 9218 (PROD)
**Server:** `server_qa.py` (stdlib HTTP)
**Endpoints:**
- `/` - Dashboard (index.html)
- `/api/nsae` - NSAE Feature Engineer signals (20 tickers)
- `/api/nsoe` - NSOE Option Engine (option chains, Greeks)
- `/api/backtest` - Backtest engine (HMM regime, NSAE, SMA)
- `/api/chart` - Chart data with regime overlay
- `/api/live_feed` - Live prices + system events
- `/api/portfolio` - Paper portfolio

## Project Nine Street - NS-3 (3-Tier Sector Rotation)
**Status:** ACTIVE - QA rewritten 2026-07-21
**Folder:** `/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-3_QA/`
**Port:** 9237 (QA) / 9236 (PROD)
**Server:** `qa_server.py` (stdlib HTTP, no FastAPI deps)
**Endpoints matching dashboard:**
- `/`ns3_dashboard.html`:
  - `/api/v1/tier1` - 11 sectors ranked by momentum vs SPY
  - `/api/v1/tier2` - Top 3 ETF signals (MACD, RSI, ADX, HMM, OBV)
  - `/api/v1/tier3` - Top 10 stocks from qualifying ETFs (RS + Piotroski + TA)
  - `/api/v1/regime` - Market regime (Bull/Neutral/Bear)
  - `/api/v1/holdings?symbol=XLK` - ETF holdings

## Project Nine Street - NS-4 (Ratio Trading)
**Status:** ACTIVE - QA rewritten 2026-07-21
**Folder:** `/Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-4_QA/`
**Port:** 9241 (QA) / 9240 (PROD)
**Server:** `qa_server.py` (stdlib HTTP, no FastAPI deps)
**Endpoints matching `ns4_dashboard.html`:**
- `/api/v1/all` - All 6 pair ratios with indicators + signals
- `/api/v1/pairs` - Same as /all (alias)
- `/health` - Health check
- Pairs: XLK/XLF, XLV/XLY, XLE/XLU, XLI/XLB, XLRE/XLC, SPY/QQQ

## Project Nine Street - Portal
**File:** `Project_Nine_Street/portal_qa.py`
**Port:** 8000
**Tabs:** Alpha Terminal, NS-1, NS-3, NS-4
**Toggle:** PROD/QA switcher updates all iframe URLs dynamically

## Git Branches (as of 2026-07-23)
| Branch | Base | Status |
|--------|------|--------|
| `feature/v1.6` | main | ✅ Merged (commit 0832717) |
| `release/v1.6` | feature/v1.6 | ✅ Created & pushed |
| `feature/v1.7` | feature/v1.6 | ✅ Created & pushed (current) |
| `feature/v1.8` | feature/v1.7 | ✅ Created & pushed (52-Week Highs feature) |

### Files Changed in v1.6:
- `Project_Nine_Street/NS_1_QA/server_qa.py` (new)
- `Project_Nine_Street/NS_1_QA/index.html` (new)
- `Project_Nine_Street/NS-3_QA/qa_server.py` (rewritten)
- `Project_Nine_Street/NS-4_QA/qa_server.py` (rewritten)
- `Project_Nine_Street/portal_qa.py` (tabs + ports updated)
- `Project_Nine_Street/scripts/nsoe_pricing.py` (vollib import fix)
- `package.json` (new - npm scripts)

## Git Push Policy
**IMPORTANT:** Git pushes should be **postponed until explicitly requested** by the user. Do not auto-push commits unless instructed.

## Session Management
This session has accumulated significant context. For new projects or major feature work, consider starting a fresh Hermes session to avoid context window saturation and ensure clean state. Current session is focused on Project Alpha POC / Nine Street v1.7.

## Portal Health Monitoring (Feature v1.7)
**Location:** `portal_qa.py` — built into the portal frontend
**How it works:**
- Each tab has a **status indicator dot**: ⚪ gray (unknown) → 🟢 green (up) → 🔴 red (down)
- Tabs also get a **green/red left border** for quick visual scan
- Status is checked every **30 seconds** via `fetch()` to each service's `/health` endpoint
- Portal also exposes `/api/health` endpoint returning JSON statuses

**Status check endpoints:**
| Service | Health Check |
|---------|-------------|
| Alpha Terminal | `http://localhost:9099/dashboard.html` |
| NS-1 | `http://localhost:9219/health` |
| NS-3 | `http://localhost:9237/health` |
| NS-4 | `http://localhost:9241/health` |

## Morning Check Routine
**Purpose:** Verify all services are up before trading day starts.

### Step 1: Open Portal
```bash
open http://localhost:8000/
```
Check all tabs have green dots. Red dots = service down.

### Step 2: Quick API Health Check
```bash
for port in 9099 9219 9237 9241 8000; do
  echo -n "Port $port: "
  curl -s -o /dev/null -w "%{http_code}" http://localhost:$port/ 2>/dev/null || echo "DOWN"
done
```
Expected: 5 × 200 responses.

### Step 3: Restart Any Down Services
```bash
# Alpha Terminal
cd /Users/chuck/Project_Alpha_POC/Project_Sequoia/QA_terminal && PORT=9099 /Library/Developer/CommandLineTools/usr/bin/python3 server.py &

# NS-1
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS_1_QA && PORT=9219 PYTHONPATH="/Users/chuck/Project_Alpha_POC/Project_Nine_Street/scripts" /Library/Developer/CommandLineTools/usr/bin/python3 server_qa.py &

# NS-3
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-3_QA && PORT=9237 /Library/Developer/CommandLineTools/usr/bin/python3 qa_server.py &

# NS-4
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street/NS-4_QA && PORT=9241 /Library/Developer/CommandLineTools/usr/bin/python3 qa_server.py &

# Portal
cd /Users/chuck/Project_Alpha_POC/Project_Nine_Street && PORT=8000 /Library/Developer/CommandLineTools/usr/bin/python3 portal_qa.py &
```

### Step 4: Regenerate GitHub Token (if pushing)
```bash
PYTHONPATH="" /Library/Developer/CommandLineTools/usr/bin/python3 \
  /Users/chuck/.zeroclaw/agents/chuck/workspace/gen_github_token.py
```

### Fault Recovery
- **NS-3/NS-4 down (FastAPI/pydantic error):** The QA servers use stdlib http.server, no FastAPI needed. Re-run the server script directly.
- **NS-1 engines unavailable:** Engines require numba/vectorbt (numpy<2). Dashboard still loads; engine endpoints return 503 gracefully.
- **Portal blank:** Check iframe URL construction. Portal uses `STRATS[key][env.toLowerCase()]` for port.