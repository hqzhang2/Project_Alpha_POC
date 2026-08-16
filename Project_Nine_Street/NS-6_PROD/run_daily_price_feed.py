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

R6 hardening (2026-08-16): a polluted PYTHONPATH that leaks the agent's
3.11 hermes venv numpy into this 3.9 interpreter caused an ABI mismatch
(`TypeError: Cannot convert numpy.ndarray to numpy.ndarray`) deep inside pandas
Index construction — the drawdown engine went blind with a cryptic traceback.
_env_selfcheck() fails LOUDLY (exit 2) if numpy/pandas are ABI-incompatible,
before any price_feed import, so a recurrence surfaces a clear message at
09:00 ET instead of an opaque crash or a silently-stale enforcement row.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _env_selfcheck() -> None:
    """Verify numpy/pandas are importable and ABI-compatible under THIS python.

    Exits 2 with a clear message on failure (see R6 spec). Must run before
    `import price_feed`, which lazily imports pandas deep inside its math.
    """
    try:
        import numpy as np
        import pandas as pd
        # Exercise the exact path that historically exploded (Index/Series
        # construction + DataFrame.dropna), so a mismatch is caught here.
        _ = pd.Series([1.0, 2.0],
                      index=pd.DatetimeIndex(["2026-01-01", "2026-01-02"]))
        _ = pd.DataFrame({"a": [1.0, 2.0]}).dropna()
        _ = np.asarray([1.0, 2.0]).astype(float)
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(
            "price feed env self-check FAILED: numpy/pandas ABI mismatch "
            f"(python {sys.version.split()[0]}). "
            "Fix the interpreter in run_daily_price_feed.sh. "
            f"Detail: {exc}\n"
        )
        sys.exit(2)
    print(f"env ok: numpy {np.__version__} pandas {pd.__version__}")


if __name__ == "__main__":
    _env_selfcheck()

    import price_feed  # noqa: E402  (after self-check, intentional)

    snap = price_feed.run_once()
    if snap is None:
        print("price feed: no live prices — no row written (enforcement will flag stale)")
        sys.exit(0)
    print("price feed ok:", snap["as_of"],
          "dd=%.4f spy=%.4f budget=%.4f remaining=%.2f vix=%s"
          % (snap["current_drawdown_pct"], snap["spy_drawdown_pct"],
             snap["budget_pct"], snap["budget_remaining_pct"], snap["latest_vix"]))
