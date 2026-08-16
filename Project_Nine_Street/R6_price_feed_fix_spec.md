# R6 — NS-6 PROD Price Feed Fix Spec

**Status:** Spec for implementation (not implemented)
**Date:** August 2026
**Source:** `full_stack_review_v3.md` §13 R6 · Phase 0 in `v3_execution_plan.md`
**Owner:** Junior (fix) · Frontier (review — the output feeds enforcement)
**Urgency:** 🔴 Highest — a blind drawdown floor violates the DD-first mandate.

---

## 1. Problem

NS-6's PROD price feed is failing. The drawdown engine in production cannot see
a real `current_drawdown`, which is the exact failure v2 flagged as the core
deliverable and the one condition the DD-first mandate cannot tolerate.

**Symptom** (`logs/ns6_pricefeed_prod.err.log`):

```
File ".../pandas/core/indexes/base.py", line 566, in __new__
    arr = sanitize_array(data, None, dtype=dtype, copy=copy)
  ...
File "pandas/_libs/lib.pyx", line 2555, in pandas._libs.lib.maybe_convert_objects
TypeError: Cannot convert numpy.ndarray to numpy.ndarray
```

## 2. Root Cause (confirmed, not a code bug)

`price_feed.py` is **byte-identical** in QA and PROD (only the NS-5 portfolio
path differs). Both `run_daily_price_feed.sh` wrappers and both launchd plists
are identical in structure. The failure is **environmental**:

- The launchd jobs invoke
  `/Library/Developer/CommandLineTools/.../Versions/3.9/bin/python3` → **Python 3.9.6**.
- That interpreter's `numpy` now resolves to a **Python 3.11 build**:
  `/Users/chuck/.hermes/hermes-agent/venv/lib/python3.11/site-packages/numpy/.../_multiarray_umath.cpython-311-darwin.so`.
- The ABI mismatch (numpy 2.4.6 built for CPython 3.11, loaded under 3.9)
  surfaces as the above `TypeError` when pandas constructs an Index/Series —
  i.e. on the first real `pd.DataFrame(...)` call, deep in `price_feed`'s
  `_align()` / `_portfolio_nav_series()`.

**Evidence:**
```
$ /Library/.../3.9/bin/python3 -c "import numpy"
Traceback ... File "/Users/chuck/.hermes/hermes-agent/venv/lib/python3.11/site-packages/numpy/_core/__init__.py"
ModuleNotFoundError: No module named 'numpy._core._multiarray_umath'
```

**QA is also at risk.** QA last succeeded 2026-08-13 / 08-14 (out log shows real
`dd/spy/budget/vix` rows), but it uses the *same* interpreter — its next run
will fail the same way. This is not a QA-vs-PROD divergence; it is a shared
environment regression that PROD happened to hit first.

## 3. The Fix

### 3.1 Repoint the price-feed jobs at a clean, consistent interpreter

The environment must have a **mutually ABI-compatible** `numpy` + `pandas` +
`yfinance`. Options:

| Option | Pros | Cons |
|---|---|---|
| **(a) Dedicated NS-6 venv** (`python3.9 -m venv` or 3.11, pinned `requirements-ns6.txt`) | Isolated from the agent's own venv; reproducible; the robust answer for a prod service | One-time setup; must be recreated on host changes |
| (b) Reuse the hermes venv (3.11, numpy 2.4.6 + pandas 3.0.5 currently import cleanly) | Zero setup | It is the *agent's* venv — fragile for a production service; a future `pip install` by the agent breaks it again |
| (c) Repair the 3.9 user site-packages (remove the stray `.pth`/path leak, reinstall numpy<2 + matching pandas for 3.9) | Keeps the existing 3.9 convention | The path leak must be found first; `numpy<2` diverges from the rest of the stack |

**Recommendation: (a) — a dedicated, pinned venv.** It is the only option that
cannot be broken by the agent's own environment churn. This is an **ops
decision** (RULE #1: ask the PM before changing the prod interpreter) — see §7.

### 3.2 Pre-flight environment self-check (the regression test)

Add a guard at the top of `run_daily_price_feed.py` that fails **loudly and
clearly** if the environment is broken, instead of a cryptic deep-pandas
traceback. This is the "PROD-path regression test" — it must run under the
*same interpreter the launchd job uses*, which is what makes it catch this exact
failure:

```python
# run_daily_price_feed.py — before importing price_feed
def _env_selfcheck() -> None:
    import sys
    try:
        import numpy as np
        import pandas as pd
        # Force the code path that historically exploded (Index construction).
        _ = pd.Series([1.0, 2.0], index=pd.DatetimeIndex(["2026-01-01", "2026-01-02"]))
        _ = pd.DataFrame({"a": [1.0, 2.0]}).dropna()
        _ = np.asarray([1.0, 2.0]).astype(float)
    except Exception as exc:
        sys.stderr.write(
            "price feed env self-check FAILED: numpy/pandas ABI mismatch "
            f"(python {sys.version.split()[0]}). Fix the interpreter in "
            f"run_daily_price_feed.sh. Detail: {exc}\n"
        )
        sys.exit(2)   # non-zero → launchd flags the failure loudly
    print(f"env ok: numpy {np.__version__} pandas {pd.__version__}")

if __name__ == "__main__":
    _env_selfcheck()
    # ... existing run_once() path ...
```

This converts "silent ABI corruption → opaque TypeError" into "clear exit-2
message at 09:00 ET with a pointer to the fix."

### 3.3 Verify the numbers the feed actually produces

While fixing, confirm the *values* are sane — the QA out log shows
`spy=0.0000` on two consecutive days and a constant `budget=-0.0500`, which is
suspicious (SPY is rarely at its exact 2-year running peak two days running).
Flag any secondary issue for a follow-up; do **not** conflate it with the R6
crash fix.

## 4. Acceptance

| Criterion | Check |
|---|---|
| PROD price feed exits 0 | `launchctl list | grep pricefeed.prod` shows status 0 after next run |
| Real drawdown persisted | `/api/enforcement/status` shows a non-zero `current_drawdown` and `as_of` = today |
| Self-check present | A broken env now exits 2 with a clear message, not a deep-pandas `TypeError` |
| Both QA + PROD fixed | QA was also at risk (same interpreter); apply the same venv to both |
| No code change to `price_feed.py` logic | The fix is environment + guard only; the math is correct and unchanged |

## 5. Files Touched

- `NS-6_PROD/run_daily_price_feed.sh` — repoint to the clean interpreter.
- `NS-6_QA/run_daily_price_feed.sh` — same (QA at risk).
- `NS-6_PROD/run_daily_price_feed.py` + QA twin — add `_env_selfcheck()`.
- New `requirements-ns6.txt` (if option (a)) — pinned numpy/pandas/yfinance.
- Both launchd plists — `bootout` + `bootstrap` after the ENV change (per house rule: ENV changes need bootout+bootstrap, not `kickstart -k`).

## 6. Tests

```python
# tests/test_env_selfcheck.py (run under the SAME interpreter as launchd)
def test_selfcheck_passes_on_clean_env():
    # import and call _env_selfcheck in-process; assert it does not raise
    from run_daily_price_feed import _env_selfcheck
    _env_selfcheck()   # should print "env ok" and return None
```

> The real regression test is `_env_selfcheck()` itself — it must be exercised
> under the launchd interpreter, which is precisely the environment that broke.

## 7. Ops Decision Required (RULE #1)

Before changing the prod interpreter, confirm with the PM:

1. **Which environment is canonical** for NS services — the CommandLineTools 3.9
   (repaired) or a new dedicated venv (recommended)?
2. **Is the hermes venv (3.11) acceptable** for prod, or must NS-6 be isolated
   from the agent's own environment?

Do **not** proceed to `bootout`/`bootstrap` the prod plist without this sign-off.

---

## 8. Why This Is Phase 0 (not deferred)

The drawdown engine's *entire purpose* is to know how much budget remains. A
blind floor means the DD-first mandate is not being enforced in production
today. R6 is independent of every other recommendation (no dependencies), is a
fix rather than new methodology, and is the only *live-risk* item in the plan —
hence first.
