#!/usr/bin/env python3
"""
run_daily_price_feed.py — NS-6 daily EOD price feed (launchd cron, R2a).

Fetches daily closes for the cockpit's selected portfolio (+ SPY, + VIX),
computes drawdown/budget/multiplier, and persists one row to the drawdown_log
so /api/enforcement/status surfaces live numbers instead of current_dd=0.0.

Idempotent (upsert on date). No LLM in the compute path. Mirrors NS-5's
run_weekly_refresh.py pattern: repo-root bootstrap, env -u PYTHONPATH.

launchd (QA):
  /bin/bash -lc "exec env -u PYTHONPATH PORT=9261 \
    /Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3 \
    .../NS-6_QA/run_daily_price_feed.py"
  Schedule: Weekdays 09:00 ET (before the enforcement loop review).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import price_feed  # noqa: E402


if __name__ == "__main__":
    snap = price_feed.run_once()
    if snap is None:
        print("price feed: no live prices — no row written (enforcement will flag stale)")
        sys.exit(0)
    print("price feed ok:", snap["as_of"],
          "dd=%.4f spy=%.4f budget=%.4f remaining=%.2f vix=%s"
          % (snap["current_drawdown_pct"], snap["spy_drawdown_pct"],
             snap["budget_pct"], snap["budget_remaining_pct"], snap["latest_vix"]))
