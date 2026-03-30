# Project Sequoia - Task Board

**Last Updated:** 2026-03-26  
**Target Launch:** April 1, 2026

---

## To Do (Before April 1)

### Infrastructure
- [ ] Set up trading execution method
- [ ] Test pre-market report workflow

### project_alpha (Terminal)
- [x] Sprint 3: Ratio Analysis (KAN-27→KAN-30) — Done ✅
- [x] KAN-32: Ratio Watchlist Management — Done ✅
- [x] KAN-31: Tidy up and refactor Alpha Terminal codebase — Done ✅

---

## ✅ Sprint 3 Complete (Mar 29, 2026)

### Post-Release
- [ ] Define entry zones / price targets for watchlist securities

---

## Done ✅

### Discord Setup
- [x] Create #sequoia-strategy
- [x] Create #sequoia-equity
- [x] Create #sequoia-market-updates
- [x] Create #sequoia-trade-ideas
- [x] Verified team can respond in channels

### Agent Setup
- [x] Create sequoia-strategist (MiniMax-M2.5)
- [x] Create sequoia-equity-analyst (Gemini 3 Flash)
- [x] Create sequoia-ficc-analyst (Gemini 3 Flash)
- [x] Create sequoia-macro-economist (MiniMax-M2.5)
- [x] Create sequoia-quant-analyst (MiniMax-M2.5)
- [x] Create sequoia-risk-manager (MiniMax-M2.5)
- [x] Test subagent spawning - all operational

### Documentation
- [x] TEAM.md
- [x] 6 strategy documents (docs/)
- [x] Agent prompts for all 6 roles

---

## Sprints

- **Sprint 1** (KAN-19 to KAN-22): Simplified Bloomberg Terminal — Core Dashboard
- **Sprint 2** (KAN-23 to KAN-26): Option Monitor (OMON) — Option Chains & Greeks

---

## Notes

- **Models:** All agents now using Gemini 3 Flash (per user request Mar 29, 2026)
- **Time budget:** Max 1 hour/day
- **Trading style:** Long-term, no day trading
- **Discord:** Team actively responding to questions (tested with TLT/TLTW/TOTL)
---

## 🚧 Sprint 4: Financial Analyzer (Mar 29, 2026)

### Completed
- [x] KAN-34: Graham Metrics Scoring (Intelligent Investor)
- [x] KAN-35: Add More Tickers (AAPL, TSLA, MSFT, GOOGL, AMZN, NVDA)

### In Progress
- [ ] KAN-33: SEC EDGAR Integration

### To Do
- [ ] KAN-36: SEC First Run Caching

### Watchlist (Testing)
| Ticker | Score | Rating |
|--------|-------|---------|
| AAPL | 4 | ⭐ Hold |
| TSLA | 4 | ⭐ Hold |
| MSFT | 4 | ⭐ Hold |
| GOOGL | 6 | ⭐⭐ Buy |
| AMZN | 2 | ⚠️ Speculative |
| NVDA | 6 | ⭐⭐ Buy |
