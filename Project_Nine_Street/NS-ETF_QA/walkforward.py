"""NS-ETF walk-forward harness — three-policy sleeve gate (spec §5).

Policies over identical windows, rebalance cadence, and costs:
  P0  baseline: no ETF diversifier sleeve (SPY-only equity anchor)
  P1  single merged diversifier sleeve (defensive + real-asset as one book)
  P2  split sleeves (defensive + real-asset sized separately) — proposed design

Deterministic; yfinance data cached to disk so reruns are offline.
Gate (spec §5): excess vs held universe, DD ratio vs SPY, turnover bounds.
Output: walkforward_results.json + this stdout table. Findings go to the PM.

Runtime: env -u PYTHONPATH CLT-py3.9 python3 walkforward.py
"""
import datetime as dt
import json
import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402

WF_DIR = config.DATA_DIR
CACHE = WF_DIR / "wf_closes.json"
START = "2016-01-01"
REBALANCE_MONTHS = 3
COST_BPS = 10.0            # per side, per rebalance trade
TOP_N_MERGED = 5           # P1 book size
DD_GATE = 0.75             # ≤ 0.75× SPY drawdown (full-stack mandate proxy)


def fetch_all(tickers, force=False):
    """{ticker: [(date, close), ...]} — disk-cached."""
    import yfinance as yf
    if CACHE.exists() and not force:
        return json.loads(CACHE.read_text())
    out = {}
    for t in tickers:
        try:
            df = yf.download(t, start=START, progress=False, auto_adjust=True)
            close = df["Close"]
            if hasattr(close, "columns"):
                close = close.iloc[:, 0]
            out[t] = [(str(idx)[:10],
                       float(v.item()) if hasattr(v, "item") else float(v))
                      for idx, v in close.dropna().items()]
        except Exception:
            out[t] = []
        print(f"  fetched {t}: {len(out[t])} bars", file=sys.stderr)
    WF_DIR.mkdir(exist_ok=True)
    CACHE.write_text(json.dumps(out))
    return out


def to_maps(raw):
    return {t: dict(rows) for t, rows in raw.items() if rows}


def month_ends(dates):
    """Last trading day of each month from a sorted date list."""
    seen = {}
    for d in dates:
        ym = d[:7]
        seen[ym] = d
    return sorted(seen.values())


def momentum(closes_map, t, d, skip=21, look=126):
    m = closes_map[t]
    ds = [x for x in m if x <= d]
    if len(ds) < look + 1:
        return None
    p_now, p_past = m[ds[-1 - skip]], m[ds[-1 - look]]
    return p_now / p_past - 1.0


def rank_universe(closes_map, d, tickers):
    scored = []
    for t in tickers:
        mom = momentum(closes_map, t, d)
        if mom is not None:
            scored.append((t, mom))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def sleeve_weights(closes_map, picks, d):
    """Inverse-vol over 63d within picks."""
    inv = {}
    for t in picks:
        m = closes_map[t]
        ds = [x for x in m if x <= d][-64:]
        if len(ds) < 30:
            continue
        rets = [m[ds[i]] / m[ds[i - 1]] - 1 for i in range(1, len(ds))]
        var = sum(r * r for r in rets) / len(rets)
        if var > 0:
            inv[t] = 1.0 / math.sqrt(var)
    if not inv:
        return {}
    tot = sum(inv.values())
    return {t: v / tot for t, v in inv.items()}


def true_design_target(defensive, real_asset, sector_share=0.60,
                       defensive_share=0.25):
    """NS-ETF's actual design: top-3 sector momentum core (sector_share),
    split-sleeve diversifier (rest, 2:1 defensive:real-asset tilt).
    Regime tilt via defensive sleeve weight scaling by 52w SPY trend —
    SPY below its 200d → shift 10pp from sectors to defensive (soft)."""
    def target(d, scored, cm):
        sec_picks = [t for t, _ in scored if t not in defensive + real_asset][:3]
        def_picks = [t for t, _ in scored if t in defensive][:config.TOP_N_PER_SLEEVE]
        ra_picks = [t for t, _ in scored if t in real_asset][:config.TOP_N_PER_SLEEVE]
        sw = sleeve_weights(cm, sec_picks, d)
        dw = sleeve_weights(cm, def_picks, d)
        rw = sleeve_weights(cm, ra_picks, d)
        s_share, d_share = sector_share, defensive_share
        spy = cm.get("SPY", {})
        ds = [x for x in spy if x <= d]
        if len(ds) >= 200 and spy[ds[-1]] < sum(spy[x] for x in ds[-200:]) / 200:
            s_share -= 0.10
            d_share += 0.10
        r_share = 1.0 - s_share - d_share
        out = {t: w * s_share for t, w in sw.items()}
        out.update({t: out.get(t, 0) + w * d_share for t, w in dw.items()})
        out.update({t: out.get(t, 0) + w * r_share for t, w in rw.items()})
        return out
    return target


def run_policy(name, closes_map, dates, rebalance_dates, policy, vix_map=None):
    """Simulate daily mark-to-market, rebalance on schedule.
    policy(d, scored) -> {ticker: weight} target book.
    vix_map enables the NS-1 heritage overlay: VIX >= CRISIS → whole book
    rotates to CRISIS_SAFE (equal-vol), back on the next rebalance when
    VIX < CRISIS_OUT (hysteresis, matching NS-1's crisis_in/out)."""
    equity = 1.0
    last_weights = {}
    curve, turnover_total, n_rebal = [], 0.0, 0
    in_crisis = False
    crisis_just_flipped = False
    for d in dates:
        # Daily VIX check, decoupled from cadence (iteration-3 fix): crisis
        # entry/exit is evaluated EVERY day; rotation trades fire the same
        # day (crisis entry → CRISIS_SAFE, exit → restore strategic book).
        if vix_map:
            v = vix_map.get(d)
            if v is not None:
                if not in_crisis and v >= config.VIX_CRISIS_LEVEL:
                    in_crisis = True
                    crisis_just_flipped = True
                elif in_crisis and v < config.VIX_CRISIS_LEVEL - 5:
                    in_crisis = False
                    crisis_just_flipped = True
        needs_trade = (d in rebalance_dates) or (
            vix_map and in_crisis is not None and
            ((in_crisis and vix_map.get(d) is not None) or crisis_just_flipped))
        if needs_trade:
            scored = rank_universe(closes_map, d, policy["universe"])
            target = policy["target"](d, scored, closes_map)
            if vix_map and in_crisis:
                safe = sorted(t for t in config.CRISIS_SAFE if t in closes_map)
                target = sleeve_weights(closes_map, safe, d)
            if target:
                traded = sum(abs(target.get(t, 0) - last_weights.get(t, 0))
                             for t in set(target) | set(last_weights))
                if traded > 1e-9:
                    turnover_total += traded
                    cost = equity * traded * COST_BPS / 10000.0
                    equity -= cost
                    last_weights = target
                    n_rebal += 1
        crisis_just_flipped = False
        # daily pnl
        if last_weights:
            day = 0.0
            for t, w in last_weights.items():
                m = closes_map[t]
                ds = [x for x in m if x <= d]
                if len(ds) >= 2 and ds[-1] == d:
                    r = m[d] / m[ds[-2]] - 1
                    day += w * r
            equity *= (1 + day)
        curve.append((d, equity))
    return {"policy": name, "curve": curve,
            "total_return": equity,
            "turnover_per_year": turnover_total / max(1, (len(dates) / 252)),
            "n_rebalances": n_rebal}


def drawdown(curve):
    peak, mdd = -1e9, 0.0
    for _, v in curve:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return mdd


def cagr(curve):
    if len(curve) < 2:
        return 0.0
    years = (dt.date.fromisoformat(curve[-1][0]) -
             dt.date.fromisoformat(curve[0][0])).days / 365.25
    return (curve[-1][1] / curve[0][1]) ** (1 / years) - 1


def excess_years(policy_curve, spy_curve):
    """Calendar-year return minus SPY same-year, per year. Returns
    (wins, total, detail)."""
    def yearly(curve):
        out = {}
        for d, v in curve:
            y = d[:4]
            out.setdefault(y, [v, v])[1] = v
        return {y: (a[1] / a[0] - 1) for y, a in out.items()}
    py, sy = yearly(policy_curve), yearly(spy_curve)
    detail = {}
    wins = 0
    for y in sorted(set(py) & set(sy)):
        ex = py[y] - sy[y]
        detail[y] = round(ex, 4)
        if ex > 0:
            wins += 1
    return wins, len(detail), detail


def main(force=False):
    universe = sorted(set(config.FEED_FED_TICKERS) - {"SPY"} | {"SPY"})
    print(f"NS-ETF walk-forward {START}→today · rebalance every {REBALANCE_MONTHS}m · {COST_BPS}bps")
    raw = fetch_all(universe + ["^VIX"], force=force)
    closes = to_maps(raw)
    if "SPY" not in closes:
        print("FATAL: no SPY data"); return 1

    spy_dates = sorted(closes["SPY"])
    rebal = set(month_ends(spy_dates)[::REBALANCE_MONTHS])
    spy_curve = [(d, closes["SPY"][d]) for d in spy_dates]

    defensive = [t for t in config.DEFENSIVE_ETFS if t in closes]
    real_asset = [t for t in config.REAL_ASSET_ETFS if t in closes]
    sectors = [t for t in config.SECTOR_ETFS if t in closes]
    print(f"universe ok: {len(sectors)} sectors, {len(defensive)} defensive, {len(real_asset)} real-asset")

    policies = [
        {"name": "P0_baseline_no_sleeve",
         "universe": sectors,
         "target": lambda d, scored, cm: dict(
             (t, w) for (t, _), w in zip(
                 scored[:3],
                 list(sleeve_weights(cm, [t for t, _ in scored[:3]], d).values())))},
        {"name": "P0V_baseline_plus_vix",
         "universe": sectors,
         "vix": True,
         "target": lambda d, scored, cm: dict(
             (t, w) for (t, _), w in zip(
                 scored[:3],
                 list(sleeve_weights(cm, [t for t, _ in scored[:3]], d).values())))},
        {"name": "P1_merged_diversifier",
         "universe": sectors + defensive + real_asset,
         "vix": True,
         "target": lambda d, scored, cm: dict(
             (t, w) for (t, _), w in zip(
                 scored[:TOP_N_MERGED],
                 list(sleeve_weights(cm, [t for t, _ in scored[:TOP_N_MERGED]], d).values())))},
        # True NS-ETF design: sector-momentum core + defensive/real-asset
        # sleeves sized by regime tilt, VIX crisis overlay on top.
        {"name": "P2R_true_design",
         "universe": sectors + defensive + real_asset,
         "vix": True,
         "target": true_design_target(defensive, real_asset)},
        # Iteration 3: PM dial (defensive share grid) + monthly cadence.
        {"name": "P2R_def15",
         "universe": sectors + defensive + real_asset,
         "vix": True, "rebal_months": REBALANCE_MONTHS,
         "target": true_design_target(defensive, real_asset,
                                      sector_share=config.SECTOR_CORE_SHARE,
                                      defensive_share=config.DEFENSIVE_SHARE)},
        {"name": "P2R_def35",
         "universe": sectors + defensive + real_asset,
         "vix": True,
         "target": true_design_target(defensive, real_asset, defensive_share=0.35)},
        {"name": "P2R_monthly",
         "universe": sectors + defensive + real_asset,
         "vix": True, "rebal_months": 1,
         "target": true_design_target(defensive, real_asset)},
    ]

    vix_map = {d: v for d, v in to_maps(raw).get("^VIX", {}).items()}
    results = {}
    print(f"\n{'policy':26s} {'CAGR':>7s} {'MaxDD':>8s} {'DD/SPY':>7s} {'tn/yr':>6s} {'excess yrs':>11s}")
    spy_mdd = abs(drawdown(spy_curve))
    for p in policies:
        months = p.get("rebal_months", REBALANCE_MONTHS)
        reb = set(month_ends(spy_dates)[::months]) if months != REBALANCE_MONTHS else rebal
        r = run_policy(p["name"], closes, spy_dates, reb, p,
                       vix_map=vix_map if p.get("vix") else None)
        mdd = abs(drawdown(r["curve"]))
        wins, tot, detail = excess_years(r["curve"], spy_curve)
        results[p["name"]] = {
            "cagr": round(cagr(r["curve"]), 4),
            "max_dd": round(-mdd, 4),
            "dd_ratio_vs_spy": round(mdd / spy_mdd, 3) if spy_mdd else None,
            "turnover_per_year": round(r["turnover_per_year"], 2),
            "excess_wins": wins, "excess_years_total": tot,
            "excess_detail": detail,
            "dd_gate_pass": bool(mdd / spy_mdd <= DD_GATE) if spy_mdd else None,
        }
        print(f"{p['name']:26s} {results[p['name']]['cagr']:7.2%} "
              f"{-mdd:8.2%} {results[p['name']]['dd_ratio_vs_spy']:7.2f} "
              f"{results[p['name']]['turnover_per_year']:6.2f} "
              f"{wins}/{tot:>d}")

    out = {"as_of": str(dt.date.today()), "start": START,
           "rebalance_months": REBALANCE_MONTHS, "cost_bps": COST_BPS,
           "dd_gate": DD_GATE, "results": results}
    path = WF_DIR / "walkforward_results.json"
    path.write_text(json.dumps(out, indent=2))
    print(f"\nwritten: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(force="--fetch" in sys.argv))
