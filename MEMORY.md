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
| Strategist | Stan | Big picture, portfolio allocation |
| Equity Analyst | Esso | Stocks (SPX500, ADRs, IPOs) |
| Macro Economist | Marc | US/Global economy, Fed policy |
| FICC Analyst | Finn | Bonds, FX, commodities, crypto |
| Quantitative Analyst | Qi | Options, Greeks, portfolio analytics |
| Risk Management | Ray | VAR, drawdown control, exposure |

## AI Agents (Proposed):
- sequoia-strategist (MiniMax-M2.5) - Workspace: Project_Sequoia/agents/strategist
- sequoia-equity-analyst (Gemini 3 Flash) - Workspace: Project_Sequoia/agents/equity_analyst
- Task Board: Project_Sequoia/TASKS.md

## System Events:
- On 2026-02-22, Hong reported a power failure and subsequently set up auto-start for the system.
- On 2026-02-22, Hong requested to defer the data presentation discussion and resume "project alpha poc" tomorrow.
- On 2026-03-18, Completed Sprint 1 (KAN-2, KAN-3, KAN-4, KAN-7 → Done), Created Sprint 2 (KAN-8), Broke down EPICs KAN-5 and KAN-6 into 10 subtasks.
- On 2026-03-18 (PM), Created comprehensive documentation for all 10 subtasks (KAN-9 through KAN-18) in Project_Sequoia/docs/
- On 2026-03-27, Implemented instant followup workflow for sequoia-strategy channel - after Hong posts in strategy/equity/trade-ideas, team automatically responds within minutes
- On 2026-03-31, Released Terminal Alpha v1.3.1: Unified and refactored backend server, added comprehensive full-tab regression testing suite covering Dashboard, OMON, Ratio Analysis, and Financials.
## Project Nine Street - Quantitative Trading System
**Status:** ACTIVE PRIMARY PROJECT
**Folder:** `/Users/chuck/.openclaw/workspace/Project_Nine_Street/`

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
**Root:** `/Users/chuck/.openclaw/workspace/Project_Nine_Street/`
**Projects:**
- `NS-1_PROD/` - NS-1 (Python HTTP server, standalone)
- `NS-3_PROD/` - NS-3 3-Tier Sector Rotation (Next.js + FastAPI)
- `NS_QA/` - QA environment (shared codebase with NS-1, runs on QA ports)
- `NS-regime-2/` - NS-Regime-2 (Next.js + FastAPI, separate)
- `NS_PROD/` - NS-PROD (Next.js frontend + backend)

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
| NS-1 | 9199 | 9199 |
| NS-2 Backend | 9099 | 9098 |
| NS-3 | 9206 | 9206 |
| NS-4 | 9210 | 9210 |

## Running Services (Project Nine Street)
| Service | Port | PID | Status |
|---------|-----|-----|--------|
| NS-1 (PROD) | 9199 | Running | ✅ |
| NS-3 Backend | 9206 | Running | ✅ |
| NS-3 Frontend (DEV) | 3000 | Running | ✅ |
| NS-2 Alpha Terminal | 9098 | 8422 | ✅ (in Project_Sequoia/terminal/) |

## Project Nine Street - NS-3 (3-Tier Sector Rotation)
**Status:** ACTIVE - QA only
**Folder:** `/Users/chuck/.openclaw/workspace/Project_Nine_Street/NS-3_PROD/`
**Ports:** 9206 (backend API - PROD) | 3000 (frontend - DEV)
**Started:** April 27, 2026
**Architecture:**
- Tier 1: Sector Rotation (ratio momentum vs SPY)
- Tier 2: ETF Signal Engine (HMM + MACD + ADX + RSI + OBV scoring)
- Tier 3: Stock Selection (RS + Piotroski F-Score + TA composite)
**Source:** Adapted from `~/Downloads/Claude_3/` scripts
**Note:** Frontend at port 3000 showing fallback sample data. API fetch issue still unresolved (proxy route `/api/all` not reaching backend).
- **QA details**: QA frontend is on port 3005.

### General Directives
- **Memory Retention**: I must be extremely diligent about saving any context, port configurations, architectural changes, and task progression into `MEMORY.md` and daily memory files before yielding/finishing my turn, as I will lose transient conversational memory between long gaps or session restarts. This prevents wasting time/tokens.

## Nine Street Portal
**File:** `Project_Nine_Street/portal.py`
**Port:** 8000
**Git:** Committed
**Links:** Alpha Terminal, NS-1, NS-2, NS-3 (PROD + QA)
**Toggle:** PROD / QA switcher updates all links dynamically (JS-side, no reload)
