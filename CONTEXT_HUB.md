# CONTEXT_HUB.md - Operational Continuity

## 🎯 Current Focus (Session: 2026-02-28 08:45 AM)
- **Primary Task:** Implementing Drift Monitor Analysis and project persistence improvements.
- **Goal:** Analyzing Style Drift and individual ticker correlations for Project Alpha.
- **Model:** Switched back to `gemini-flash` (google/gemini-3-flash-preview) as requested by Hong.

## ✅ Accomplishments (Today)
- [x] Switched model to `gemini-flash`.
- [x] Reviewed February 27th Drift Monitor Design document.
- [x] Created `visualize_drift_ascii.py` for correlation trend analysis.
- [x] Identified current avg correlation of **0.1645** (vs. 0.1000 threshold).
- [x] Implemented `PROJECT_PROFILE.md` and `CONTEXT_HUB.md` for operational continuity.

## 🔜 Next Steps & Open Tasks
1. **Ticker Drift Breakdown:** Run a correlation review on individual assets (XLU, XLV, GLD) to find the primary driver of the 0.1645 correlation.
2. **Threshold Sensitivity:** Review if the 0.10 threshold needs adjustment based on current regime data.
3. **Database Migration Plan:** Prepare steps for the SQLite -> PostgreSQL 16 transition.
4. **Context Loop:** Chuck must check `PROJECT_PROFILE.md` and `CONTEXT_HUB.md` at the start of every session.

## ⚠️ Active Blockers / Notes
- **Matplotlib Absence:** Using ASCII-based plotting as `matplotlib` is not in the `project_alpha_env`.
- **Power Failure History:** System set up with auto-start; monitor for any missed daily logs.
