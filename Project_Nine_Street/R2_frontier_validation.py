#!/usr/bin/env python3
"""R2_frontier_validation.py — validate the frontier sizer against real data.

R2 deliverable: prove the NS-5 frontier sizer works on REAL joint-universe data
and compare frontier weights vs the current equal-weight-within-sleeve stopgap.
This is the validation that lets the revamped R1 consume a tested frontier sizer
without wiring it into the live production blend yet.

Method:
  1. Run the 2a equity-sleeve blend (research_ns5_blend.run_blend, regime) to get
     the joint-universe holdings + per-ticker prices the same way R1 does.
  2. For a representative rebalance date, build the held names' daily closes
     (point-in-time, from A_T data via NS-7's Facts).
  3. Call the frontier sizer (3.9 subprocess) on that universe -> frontier weights.
  4. Report frontier weights vs equal-weight, and the realized (ret, vol) of each.

Run (house 3.9 runtime — has sklearn for Ledoit-Wolf):
  /Library/.../3.9/bin/python3 R2_frontier_validation.py
Results: printed table + data/frontier_validation.json (gitignored).

Note: this runs in NS-7_QA context for the equity data (imports research_ns5_blend),
so it must NOT import NS-5's config in the same process — the frontier sizing is
done via a 3.9 subprocess helper (NS-5_QA/_r1_frontier.py) to avoid the config
name collision and the sklearn-in-hermes-venv gap.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "NS-7_QA"))

import research_ns5_blend as blend2a  # noqa: E402

PY39 = "/Library/Developer/CommandLineTools/Library/Frameworks/Python3.framework/Versions/3.9/bin/python3"
FRONTIER_HELPER = str(HERE / "NS-5_QA" / "_r1_frontier.py")
OUT = HERE / "R2_frontier_validation.json"

MODE = "maxsharpe"
RISK_FREE = 0.0


def _held_names_on(eq_rows, target_month: str) -> list:
    """The joint-universe names held at the rebalance for target_month."""
    for r in eq_rows:
        if (r.get("month") or r.get("rebalance", ""))[:7] == target_month:
            return list(r.get("mom", [])) or list(r.keys())[:0]
    return []


def _closes_csv_for(names, facts, day, tmp_csv: Path) -> int:
    """Write daily closes for `names` up to `day` into a CSV; return # rows."""
    import pandas as pd
    frame = {}
    for t in names:
        dates, closes = facts.prices.get(t, ([], []))
        upto = [(d, c) for d, c in zip(dates, closes) if d <= day]
        if len(upto) > 60:
            frame[t] = pd.Series({d: c for d, c in upto}, dtype=float)
    if not frame:
        return 0
    df = pd.DataFrame(frame).dropna()
    if df.empty or len(df) < 60:
        return 0
    df.to_csv(tmp_csv)
    return len(df)


def main() -> int:
    print("loading A_T data + running equity-sleeve blend (2a regime) ...")
    at_db = blend2a.config.AT_FUNDAMENTALS_DB
    prices = blend2a.wf.load_prices(at_db)
    annual = blend2a.wf.load_annual(at_db)
    membership = blend2a.wf.load_membership()
    facts = blend2a.wf.Facts(prices, annual, membership)
    spy = blend2a.wf.load_spy(blend2a.config.DATA_DIR / "spy_closes.json")
    macro = blend2a._load_macro()
    eq = blend2a.run_blend("2016-01-01", "2026-07-31", facts, macro, spy,
                           rebalance_months=3, tilt_mode="regime")
    rows = eq.get("rows", [])
    if not rows:
        print("no equity rows"); return 1

    # Representative rebalance: use the last full rebalance in 2020 (a normal year)
    target = next((r["month"] for r in rows if r["month"].startswith("2020-06")), rows[10]["month"])
    names = [t for t in (list(rows[10].get("mom", [])))]
    # Build a richer universe: momentum ∪ value picks on that date
    day = target + "-15"
    names = _held_names_on(rows, target) or []
    if not names:
        print("no held names for", target); return 1

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tf:
        closes_csv = tf.name
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        out_json = tf.name
    try:
        n = _closes_csv_for(names, facts, day, Path(closes_csv))
        if not n:
            print("insufficient closes for frontier"); return 1
        r = subprocess.run(
            [PY39, FRONTIER_HELPER, closes_csv, MODE, str(RISK_FREE), out_json],
            capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            print("frontier helper failed:", r.stderr[-1500:]); return 1
        res = json.loads(Path(out_json).read_text())
        if "error" in res:
            print("frontier error:", res); return 1
    finally:
        Path(closes_csv).unlink(missing_ok=True)
        Path(out_json).unlink(missing_ok=True)

    fw = res["weights"]
    n_names = len(fw)
    ew = 1.0 / n_names if n_names else 0.0
    doc = {
        "as_of": day, "mode": MODE, "source": res.get("source"), "n": n_names,
        "frontier_weights": fw,
        "equal_weight": ew,
        "names": names,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    OUT.write_text(json.dumps(doc, indent=2, default=str))

    print(f"\n=== R2 FRONTIER SIZING VALIDATION === {day} ({MODE}, src={res.get('source')})")
    print(f"{'ticker':<8} {'frontier':>10} {'equal-wt':>10}")
    for t in sorted(fw, key=lambda x: -fw[x]):
        print(f"{t:<8} {fw[t]:>10.3f} {ew:>10.3f}")
    print(f"n={n_names}, Σw={sum(fw.values()):.4f}")
    print(f"→ {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
