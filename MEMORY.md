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

## Project Sequoia (Hedge Fund with AI Agents)
- **Started:** March 1, 2026
- **Role:** COO
- **Initial Planning Doc:** Project_Sequoia/01_Initial_Planning.md
- **Team Doc:** Project_Sequoia/TEAM.md
- **Investment Vehicles:** Equity stocks, index ETFs, commodity ETFs, international ETFs, options (covered calls, puts)
- **Restrictions:** No margin, no futures, no short selling, no naked options
- **Target Return:** 10-20% annualized
- **Max Drawdown:** single digit %
- **Time Constraint:** Max 1 hour/day

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
- On 2026-03-31, Released Terminal Alpha v1.3: Implemented 1D high-res intraday charting with 09:30-16:00 axis, volume overlay, and last-close reference line. Validated with unit and regression tests.