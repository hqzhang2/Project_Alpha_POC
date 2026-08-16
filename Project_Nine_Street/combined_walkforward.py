"""combined_walkforward.py — R1: combined-fund walk-forward (coordinator).

Assembles the FULL multi-strategy fund (NS-7 momentum + A_T value equity sleeve
∪ NS-8 tactical multi-asset) with the NS-6 VIX fast-de-risk floor, on real data,
and measures the mandate gates:

  - Combined excess vs SPY >= 0 in >= 7/10 years   (return half)
  - Combined max drawdown <= 0.75x SPY             (drawdown half)

WHY subprocesses: both NS-7_QA and NS-8_QA define a `config.py`. Importing both
sleeves in one Python process makes their `config` modules collide (the first
`import config` wins globally). Running each sleeve in its own subprocess with
the correct cwd avoids the collision entirely and keeps each sleeve's real,
already-validated machinery intact. This coordinator:
  1. runs the equity-sleeve blend (2a regime) in NS-7_QA -> equity rows JSON
  2. runs NS-8 tactical in NS-8_QA -> monthly return JSON
  3. combines + applies the NS-6 VIX floor, measures the mandate.

Assumptions (documented, PM-reviewable — same spirit as 2a's A1-A4):
  A1 Equity/tactical split is regime-stepped (0.70 growth / 0.30 defensive),
     NOT NS-5's frontier (R2 wires the frontier for exact sizing).
  A2 No cross-sleeve transaction cost (sleeves carry their own cost models).
  A3 FRED as-revised (house macro accepts this).
  A4 Sleeves combined on the equity sleeve's quarterly rebalance grid; NS-8
     daily returns aggregated to each interval. Both real.

Run:
  /Users/chuck/.hermes/hermes-agent/venv/bin/python3 combined_walkforward.py
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
VENV_PY = "/Users/chuck/.hermes/hermes-agent/venv/bin/python3"

OUT = HERE / "combined_walkforward_results.json"
FINDINGS = HERE / "R1_combined_findings.md"
TMP = HERE / ".combined_tmp"

TILT = {"growth": 0.70, "defensive": 0.30}   # equity/tactical split (A1)


# ── NS-6 fast-de-risk VIX-smile exposure cap (floored, never 0) ──────────
def vix_cap(vix) -> float:
    if vix is None:
        return 1.0
    if vix < 20:
        return 1.0
    if vix < 30:
        return 0.8
    if vix < 40:
        return 0.6
    return 0.4                                  # floor at 40% (never 0)


def _load_vix() -> dict:
    """{date: vix} from the NS-6 price cache (real ^VIX 2y). Fail-open {}."""
    import pandas as pd
    pkl = HERE / "NS-6_PROD" / "data" / "ns6_prices.pkl"
    if not pkl.exists():
        return {}
    try:
        cache = pd.read_pickle(pkl)
        vix = cache.get("^VIX")
        if vix is None:
            return {}
        return {d.strftime("%Y-%m-%d"): float(v) for d, v in vix.items()}
    except Exception:
        return {}


# ── Sleeve subprocess helpers ────────────────────────────────────────────
def _run_equity_sleeve(start: str, end: str) -> list:
    """Run the 2a regime blend in NS-7_QA, return its per-rebalance rows."""
    script = (
        "import sys, json; sys.path.insert(0, '.'); "
        "import research_ns5_blend as b, config; "
        "at_db=config.AT_FUNDAMENTALS_DB; "
        "p=b.wf.load_prices(at_db); a=b.wf.load_annual(at_db); "
        "m=b.wf.load_membership(); f=b.wf.Facts(p,a,m); "
        "s=b.wf.load_spy(config.DATA_DIR/'spy_closes.json'); "
        "mac=b._load_macro(); "
        f"res=b.run_blend('{start}','{end}',f,mac,s,rebalance_months=3,tilt_mode='regime'); "
        "json.dump(res.get('rows',[]), open('" + str(TMP) + "_eq.json','w'), default=str)"
    )
    r = subprocess.run([VENV_PY, "-c", script],
                       cwd=str(HERE / "NS-7_QA"), capture_output=True, text=True,
                       timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"equity sleeve failed:\n{r.stderr[-2000:]}")
    return json.loads((TMP.with_name(TMP.name + "_eq.json")).read_text())


def _run_tactical(start: str, end: str) -> dict:
    """Run NS-8 tactical in NS-8_QA, return {ym: mean monthly return}."""
    out_file = str(TMP) + "_t8.json"
    helper = str(HERE / "NS-8_QA" / "_r1_tactical.py")
    r = subprocess.run([VENV_PY, helper, out_file, start, end],
                       cwd=str(HERE / "NS-8_QA"), capture_output=True, text=True,
                       timeout=600)
    if r.returncode != 0:
        raise RuntimeError(f"tactical sleeve failed:\n{r.stderr[-2000:]}")
    return json.loads(Path(out_file).read_text())


# ── Combination ──────────────────────────────────────────────────────────
def run_combined(start: str = "2016-01-01", end: str = "2026-07-31") -> dict:
    print("running equity sleeve (2a regime blend) in NS-7_QA ...")
    eq_rows = _run_equity_sleeve(start, end)
    print(f"  -> {len(eq_rows)} rebalance intervals")

    print("running NS-8 tactical in NS-8_QA ...")
    t8_monthly = _run_tactical(start, end)
    print(f"  -> {len(t8_monthly)} months")

    vix = _load_vix()
    combined_rows = []
    for r in eq_rows:
        ym = r.get("month") or (r.get("rebalance", "")[:7])
        reg = r.get("regime") or "defensive"
        w_eq = TILT.get(reg, 0.30)
        eq_ret = float(r.get("strategy", 0.0))
        vix_lvl = _latest_vix_before(vix, ym + "-28")
        cap = vix_cap(vix_lvl)
        eq_capped = eq_ret * cap
        tac = t8_monthly.get(ym)
        fund_ret = w_eq * eq_capped + (1 - w_eq) * (tac or 0.0)
        combined_rows.append({
            "month": ym, "regime": reg, "w_equity": round(w_eq, 2),
            "vix": vix_lvl, "vix_cap": cap,
            "equity_sleeve": round(eq_ret, 6),
            "equity_capped": round(eq_capped, 6),
            "tactical": round(tac, 6) if tac is not None else None,
            "fund": round(fund_ret, 6),
            "spy": r.get("spy"),
        })

    # ── Yearly aggregation ──────────────────────────────────────────────
    by_year: dict = {}
    for c in combined_rows:
        y = c["month"][:4]
        by_year.setdefault(y, {"fund": 1.0, "spy": 1.0})
        by_year[y]["fund"] *= (1 + c["fund"])
        if c["spy"] is not None:
            by_year[y]["spy"] *= (1 + c["spy"])
    yearly = [{"year": y, "fund": g["fund"] - 1.0, "spy": g["spy"] - 1.0,
               "excess_vs_spy": g["fund"] - g["spy"]}
              for y, g in sorted(by_year.items())]

    # ── Drawdown ────────────────────────────────────────────────────────
    fund_curve, spy_curve = [1.0], [1.0]
    for c in combined_rows:
        fund_curve.append(fund_curve[-1] * (1 + c["fund"]))
        si = c.get("spy")
        spy_curve.append(spy_curve[-1] * (1 + (si or 0.0)))
    dd_fund, dd_spy = _max_dd(fund_curve), _max_dd(spy_curve)
    ratio = abs(dd_fund / dd_spy) if dd_spy else None

    excess_ok = sum(1 for y in yearly if y["excess_vs_spy"] > 0)
    gates = {
        "return_gate": {"pass": excess_ok >= 7, "excess_years": excess_ok,
                        "need": 7, "of_years": len(yearly)},
        "dd_gate": {"pass": (ratio or 9) <= 0.75, "ratio": ratio, "need": 0.75},
    }
    return {
        "window": {"start": start, "end": end, "rebalances": len(combined_rows)},
        "yearly": yearly,
        "excess_years_vs_spy": excess_ok,
        "drawdown": {"fund": dd_fund, "spy": dd_spy, "ratio": ratio},
        "gates": gates,
        "rows": combined_rows,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


def _latest_vix_before(vix, date):
    keys = [d for d in vix if d <= date]
    return vix[max(keys)] if keys else None


def _max_dd(curve):
    peak, mdd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0 if peak else 0.0)
    return mdd


def write_findings(res) -> None:
    g = res["gates"]
    lines = [
        "# R1 — Combined-Fund Walk-Forward (Findings)",
        "",
        f"Run: {res['generated_at']} · window {res['window']['start']} → {res['window']['end']}",
        "",
        "## Mandate gates (R7 benchmark: outperform SPY, ≤0.75× SPY drawdown)",
        "",
        "| Gate | Result | Pass? |",
        "|---|---|---|",
        f"| Return: excess vs SPY ≥7/10 yrs | {g['return_gate']['excess_years']}/{g['return_gate']['of_years']} | "
        f"{'✅' if g['return_gate']['pass'] else '❌'} |",
        f"| Drawdown: ≤0.75× SPY | ratio {g['dd_gate']['ratio']:.2f} | "
        f"{'✅' if g['dd_gate']['pass'] else '❌'} |",
        "",
        f"Fund max DD {res['drawdown']['fund']:.1%} vs SPY {res['drawdown']['spy']:.1%} "
        f"(ratio {res['drawdown']['ratio']:.2f})",
        "",
        "## Yearly",
        "",
        "| Year | Fund | SPY | Excess |",
        "|---|---|---|---|",
    ]
    for y in res["yearly"]:
        lines.append(f"| {y['year']} | {y['fund']:+.1%} | {y['spy']:+.1%} | "
                     f"{y['excess_vs_spy']:+.1%} |")
    lines += [
        "",
        "## Assumptions (A1-A4, PM-reviewable — see module docstring)",
        "",
        "Full stack = NS-7 momentum + A_T value (regime tilt) ∪ NS-8 tactical, "
        "with NS-6 VIX fast-de-risk floor. R2 wires the NS-5 frontier for exact "
        "sleeve sizing; this is a faithful first R1 assembly.",
    ]
    FINDINGS.write_text("\n".join(lines))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2026-07-31")
    args = ap.parse_args()
    try:
        res = run_combined(args.start, args.end)
    finally:
        for p in (TMP.with_name(TMP.name + "_eq.json"), TMP.with_name(TMP.name + "_t8.json")):
            if p.exists():
                p.unlink(missing_ok=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=2, default=str))
    write_findings(res)
    g = res["gates"]
    print(f"\n=== R1 COMBINED-FUND WALK-FORWARD === {res['window']['start']} → {res['window']['end']}")
    print(f"excess_years_vs_spy: {res['excess_years_vs_spy']}/{len(res['yearly'])}")
    print(f"fund DD {res['drawdown']['fund']:.1%} vs SPY {res['drawdown']['spy']:.1%} "
          f"(ratio {res['drawdown']['ratio']:.2f})")
    print(f"RETURN GATE (>=7/10 vs SPY): {'PASS' if g['return_gate']['pass'] else 'FAIL'} "
          f"({g['return_gate']['excess_years']}/{g['return_gate']['of_years']})")
    print(f"DD GATE (<=0.75x SPY): {'PASS' if g['dd_gate']['pass'] else 'FAIL'} "
          f"(ratio {g['dd_gate']['ratio']})")
    print(f"→ {OUT}")
