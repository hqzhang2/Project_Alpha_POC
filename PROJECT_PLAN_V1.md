# PROJECT_PLAN_V1.md - Project Alpha Startup Cycle

## 🗓️ Release Cadence: 2-Week Sprints
**Current Cycle:** Sprint 1 (Mar 1, 2026 - Mar 14, 2026)
**Owner:** Hong (Lead) / Chuck (AI Execution)

---

## 🏗️ Epics (Jira KAN Board)

### 1. [EPIC] Infrastructure & Operations Foundation (KAN-5)
- **Goal:** Robust, production-ready environment.
- **Backlog:**
    - [x] Local PostgreSQL 18.3 Migration (Milestone 5)
    - [x] Automated Daily Snapshot via Email (Milestone 2)
    - [ ] GitHub Repository CI Integration
    - [ ] Persistent Logging for Drift Analysis

### 2. [EPIC] Alpha Strategy & Drift Mitigation (KAN-6)
- **Goal:** Maintain defensive profile with low SPY correlation.
- **Backlog:**
    - [x] XLV -> XLP Swap (Strategy V1.1)
    - [ ] XLK / XLY Exposure Reduction Planning
    - [ ] Real-time correlation alerting system

---

## 🏃 Sprint 1 Goals (KAN-7)
1. **Infrastructure:** Complete full application refactor to use PostgreSQL (Risk Manager, Backtester).
2. **Strategy:** Analyze and implement the reduction of XLK (0.89 correlation) to lower portfolio-wide drift.
3. **Operations:** Enable 9:00 AM Daily Snapshot automated run.

---

## 📊 Sprint 1 Capacity & Velocity (Estimated)
- **Current Load:** 12 Active Tickers
- **Drift Target Avg:** < 0.20
- **System Uptime:** 24/7 (Local Host)
