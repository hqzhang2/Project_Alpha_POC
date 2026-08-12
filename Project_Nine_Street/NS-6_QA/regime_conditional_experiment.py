"""
Regime-conditional selection experiment.

Tests the user's hypothesis: selection style should follow the macro regime.
  - Growth regime (R1 expansion / R2 overheating)  → lean GROWTH (QQQ + growth stocks)
  - Defensive regime (R3 recession / R4 stagflation) → value/quality + NON-EQUITY ETFs

Regime is classified POINT-IN-TIME (no lookahead) from FRED GDP + CPI + UNRATE,
using the NS-5 primary rule (common/regime_model.py Step 1):
  growth   = GDP_QoQ_annualized >= 2%  OR  unemployment stable/falling
  inflation = CPI_YoY >= 2% AND trend not falling >0.05pp/3mo
  R1 = growth & not-inflation | R2 = growth & inflation
  R3 = not-growth & not-inflation | R4 = not-growth & inflation

Selectors:
  reg_growth    — QQQ (the growth story, beats SPY in growth regimes)
  reg_defensive — TLT/GLD/IEF/BIL (the non-equity sleeve), no equities

Comparison baselines: SPY, QQQ buy&hold, value screener, momentum.

Usage: python3 regime_conditional_experiment.py
"""

import os
import sys
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ns6_backtest as nb
import selection_construction_experiment as sc

FRED_KEY = "54d63b68fb1e686f81d53a022f9a1b91"


# ── FRED fetch ─────────────────────────────────────────────────────────────
def _fred(sid, start="2015-01-01"):
    import urllib.request
    import json
    url = (f"https://api.stlouisfed.org/fred/series/observations?series_id={sid}"
           f"&api_key={FRED_KEY}&file_type=json&observation_start={start}")
    d = json.load(urllib.request.urlopen(url, timeout=30))
    return [(o["date"], float(o["value"]))
            for o in d["observations"] if o["value"] != "."]


def _build_regime_series():
    """Point-in-time quarterly regime classification (R1-R4) for 2015-2026.

    GDP is quarterly and released with a ~1-month lag; CPI/UNRATE monthly.
    To avoid lookahead, as-of date d uses the LATEST GDP/CPI/UNRATE whose
    period_end + release_lag <= d. Simplified: use the observation date
    itself (FRED date = period end), shifted by a conservative release lag.
    """
    gdp = pd.DataFrame(_fred("GDPC1"), columns=["date", "gdp"])
    cpi = pd.DataFrame(_fred("CPIAUCSL"), columns=["date", "cpi"])
    unr = pd.DataFrame(_fred("UNRATE"), columns=["date", "unrate"])
    for df in (gdp, cpi, unr):
        df["date"] = pd.to_datetime(df["date"])

    # GDP QoQ annualized
    gdp = gdp.sort_values("date").set_index("date")
    gdp["gdp_qoq_ann"] = gdp["gdp"].pct_change() * 4 * 100
    # CPI YoY (12-month)
    cpi = cpi.sort_values("date").set_index("date")
    cpi["cpi_yoy"] = cpi["cpi"].pct_change(12) * 100
    cpi["cpi_trend_3m"] = cpi["cpi_yoy"].diff(3)
    # UNRATE 3-month change
    unr = unr.sort_values("date").set_index("date")
    unr["unrate_3m"] = unr["unrate"].diff(3)

    # Release lag (approx): GDP +45d, CPI +15d, UNRATE +10d.
    # Build an as-of daily frame of the latest available value.
    idx = pd.date_range("2016-01-01", "2026-08-01", freq="D")
    frame = pd.DataFrame(index=idx)
    for col, df, lag in (("gdp_qoq_ann", gdp["gdp_qoq_ann"], pd.Timedelta(days=45)),
                         ("cpi_yoy", cpi["cpi_yoy"], pd.Timedelta(days=15)),
                         ("cpi_trend_3m", cpi["cpi_trend_3m"], pd.Timedelta(days=15)),
                         ("unrate_3m", unr["unrate_3m"], pd.Timedelta(days=10))):
        s = df.copy()
        s.index = s.index + lag  # available only after release lag
        s = s[~s.index.duplicated(keep="last")]
        frame[col] = s.reindex(idx, method="ffill")

    # Primary classification
    gdp_thresh = 2.0
    cpi_thresh = 2.0
    growth = (frame["gdp_qoq_ann"] >= gdp_thresh) | (frame["unrate_3m"] <= 0.2)
    inflation = (frame["cpi_yoy"] >= cpi_thresh) & (frame["cpi_trend_3m"] >= -0.05)
    reg = pd.Series("R3", index=idx)
    reg[growth & ~inflation] = "R1"
    reg[growth & inflation] = "R2"
    reg[~growth & inflation] = "R4"
    return reg


# ── Regime-conditional selectors ──────────────────────────────────────────
_REG = None


def _regime_at(day):
    global _REG
    if _REG is None:
        _REG = _build_regime_series()
    d = _REG.index[_REG.index <= day]
    return str(_REG.loc[d[-1]]) if len(d) else "R1"


def sel_regime(closes, day, top_n):
    """Growth regime → QQQ; defensive regime → non-equity ETFs only."""
    r = _regime_at(day)
    if r in ("R1", "R2"):
        return ["QQQ"]
    return []  # defensive: no equities, sleeve carries the portfolio


def sel_growth_stocks(closes, day, top_n):
    """Growth regime → QQQ + top momentum growth names; defensive → [].

    In a growth regime, hold QQQ (the growth story) plus a few high-momentum
    names. In defensive regime, rotate out of equities entirely.
    """
    r = _regime_at(day)
    if r in ("R1", "R2"):
        # QQQ plus a couple of momentum leaders as a growth tilt
        rows = sc._screen_rows(day.strftime("%Y-%m-%d"))
        universe = [t["ticker"] for t in rows if t["ticker"] in closes.columns]
        mom = sc._mom_scores(closes, universe, day)
        leaders = sorted(mom, key=lambda t: -mom[t])[: max(2, top_n - 1)]
        return ["QQQ"] + leaders
    return []


def main():
    start, end = "2017-01-01", "2026-08-01"
    top_n = 12
    print("# Regime-Conditional Selection Experiment\n")
    base = nb.fetch_prices([nb.SPY, nb.VIX, "QQQ"] + nb.CANDIDATES, 10)
    universe = nb.build_universe(base, start, end, top_n=top_n)
    closes = nb.fetch_prices([nb.SPY, nb.VIX, "QQQ"] + universe, 10)
    # ensure QQQ is in the universe
    print(f"window {start}..{end} | {len(closes.columns)} series\n")

    reg = _build_regime_series()
    print("### Regime distribution (2017-2026, quarterly rebalance days)")
    reb = [d for d in pd.date_range(start=start, end=end, freq="QE")]
    from collections import Counter
    cnt = Counter(_regime_at(d) for d in reb)
    print(f"  {dict(cnt)}\n")

    print("### Configs (Phase 3, equal weight)")
    configs = [
        ("SPY buy&hold (benchmark)", None),
        ("QQQ buy&hold", None),
        ("regime: QQQ / defensive ETFs", sel_regime),
        ("regime: QQQ+momentum / defensive ETFs", sel_growth_stocks),
        ("value screener (baseline)", sc.sel_value),
        ("momentum (baseline)", sc.sel_momentum),
    ]

    results = {}
    for name, sel in configs:
        if sel is None:
            continue
        print(f"  running {name}...", flush=True)
        results[name] = nb.simulate(closes, start, end, top_n=top_n, phase=3,
                                    weighting="equal", selector=sel)

    # SPY and QQQ buy&hold from price series
    def buyhold(ticker):
        s = closes[ticker].dropna()
        s = s[(s.index >= start) & (s.index <= end)]
        r = s.pct_change().dropna()
        cum = (1 + r).cumprod()
        mdd = ((cum - cum.cummax()) / cum.cummax()).min()
        return (float(cum.iloc[-1] - 1), float(mdd), r)

    spy_ret, spy_dd, spy_r = buyhold("SPY")
    qqq_ret, qqq_dd, qqq_r = buyhold("QQQ")

    print("\n| Config | Ret% | Excess vs SPY pp | Max DD% | DD ratio |")
    print("|--------|------|------------------|---------|----------|")
    print(f"| SPY buy&hold | {spy_ret*100:.1f} | — | {spy_dd*100:.1f} | 1.00 |")
    print(f"| QQQ buy&hold | {qqq_ret*100:.1f} | {(qqq_ret-spy_ret)*100:+.1f} | {qqq_dd*100:.1f} | {qqq_dd/spy_dd:.2f} |")
    for name, r in results.items():
        print(f"| {name} | {r['total_port_ret']*100:.1f} | "
              f"{r['excess_total']*100:+.1f} | {r['port_max_dd']*100:.1f} | "
              f"{r['dd_ratio']:.2f} |")

    # risk-adjusted for the interesting ones
    def mets(r):
        r = r.dropna()
        ann = (1 + r).prod() ** (252 / len(r)) - 1
        vol = r.std() * np.sqrt(252)
        dd = ((1 + r).cumprod().cummax() - (1 + r).cumprod()).div((1 + r).cumprod().cummax()).min()
        return ann, vol, ann / vol if vol else 0, dd

    print("\n### Risk-adjusted (Sharpe)")
    a, v, sh, d = mets(spy_r)
    print(f"  SPY: ann={a*100:.1f}% vol={v*100:.1f}% Sharpe={sh:.2f} maxDD={d*100:.1f}%")
    a, v, sh, d = mets(qqq_r)
    print(f"  QQQ: ann={a*100:.1f}% vol={v*100:.1f}% Sharpe={sh:.2f} maxDD={d*100:.1f}%")
    for name, r in results.items():
        a, v, sh, d = mets(r["daily_port_ret"])
        print(f"  {name}: ann={a*100:.1f}% vol={v*100:.1f}% Sharpe={sh:.2f} maxDD={d*100:.1f}%")


if __name__ == "__main__":
    main()
