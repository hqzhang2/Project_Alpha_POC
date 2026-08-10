#!/usr/bin/env python3
"""
NS-5 regime data refresh (launchd cron, no LLM in the loop).

Runs common.regime_pipeline.run_regime_pipeline() to refresh the shared
regime_history store (FRED + Yahoo → classify → upsert). Idempotent —
the store upsert is INSERT OR REPLACE keyed on date.

Why (2026-08-10, Hong): the regime axis grades the CURRENT macro
environment, but _get_regime_history only runs the pipeline when the
store is EMPTY — after the first fill the classification went stale
(was frozen at 2026-08-07 for days). This job keeps it fresh.

Daily cadence (Mon-Fri ~16:50, after the A_T news collect at 16:45):
  ProgramArguments: /bin/bash <repo>/Project_Nine_Street/NS-5_QA/run_regime_refresh.sh
  StartCalendarInterval: {Weekday: 1-5, Hour: 16, Minute: 50}

Requires FRED_API_KEY in the environment (the job env must carry it —
launchd does NOT inherit the shell's env; see the plist).
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent  # repo root (common/ lives there)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.regime_pipeline import run_regime_pipeline  # noqa: E402


def main() -> None:
    df = run_regime_pipeline(days_back=750)
    if df.empty:
        # fail-open: log to stderr; the launchd err log captures it
        print("regime refresh: pipeline returned empty (FRED key? network?)",
              file=sys.stderr)
        sys.exit(1)
    latest = df.index[-1]
    print(f"regime refresh OK: {len(df)} rows, latest {latest.date()}, "
          f"regime {df.iloc[-1]['regime']}")
    # surface the last stored row so the watchdog/ops can see freshness
    from common.regime_store import latest as store_latest
    row = store_latest()
    if row is not None:
        print(f"stored latest: {row.get('date')} regime={row.get('regime')}")


if __name__ == "__main__":
    main()
