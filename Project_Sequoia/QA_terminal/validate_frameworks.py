"""
validate_frameworks.py — Walk-forward OOS study of 4 fundamental frameworks
(Graham / Greenblatt / Lynch / Buffett) + ensemble-agreement variant.

Phase 2.1. Uses the point-in-time store built by fundamentals_history.py:
at each annual rebalance date R, only annual facts FILED <= R are visible,
and forward returns are measured to the NEXT rebalance date's close.

Method definitions (v1, as-originally-reported annual data):
  graham     : production scorecard (fundamentals.calculate_graham_metrics,
               0-12) on the annual row; PASS = score >= 6
  greenblatt : EBIT/EV rank + ROC rank (EBIT/(NWC+PPE)), cross-sectional
               percentile ranks, combined; PASS = top 20% (EBIT > 0 only)
  lynch      : PEG = P/E / 5y EPS CAGR; PASS = 0 < PEG < 1
  buffett    : ROE >= 15% AND FCF/NetInc >= 0.8 AND D/E < 0.5 AND NI > 0
  ensemble   : count of the 4 PASS verdicts (0-4); tests whether
               multi-method agreement beats single methods

Output: research_<date>_frameworks_study.md (gitignored) with pooled +
fold-level stats. Deterministic — reads only the local store.

Usage:
  python3 validate_frameworks.py                      # run the study
  python3 validate_frameworks.py --start 2018 --rebalances 6
  python3 validate_frameworks.py --no-costs           # gross returns
"""
import argparse
import json
import os
from datetime import datetime

import fundamentals as f
import fundamentals_history as fh

COST_RT = 0.001          # 10bp per side
REBALANCE_MONTH_DAY = "04-01"
DEFAULT_START = 2016
DEFAULT_END = 2027       # last rebalance has no forward return (excluded)
REPORT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "..", "research_2026-08_frameworks_study.md")


# --------------------------------------------------------------------------- #
# Method scorers (point-in-time inputs only)
# --------------------------------------------------------------------------- #
def _pv(ticker, as_of):
    """(annual row, price) visible at as_of, or (None, None)."""
    row = fh.get_snapshot(ticker, as_of)
    if not row:
        return None, None
    price = fh.price_on(ticker, as_of)
    if not price:
        return None, None
    return row, price


def score_graham(row, price):
    inc = [{"period": row["period_end"], "type": "FY",
            "revenue": row["revenue"], "gross_profit": row["gross_profit"],
            "net_income": row["net_income"], "eps_diluted": row["eps_diluted"]}]
    bs = [{"period": row["period_end"], "type": "FY",
           "current_assets": row["current_assets"],
           "current_liabilities": row["current_liabilities"],
           "short_term_debt": row["short_term_debt"],
           "long_term_debt": row["long_term_debt"],
           "total_equity": row["total_equity"],
           "shares_outstanding": row["shares_outstanding"],
           "cash": row["cash"],
           "net_receivables": None, "total_liabilities": row["total_liabilities"]}]
    m = f.calculate_graham_metrics(inc, bs, [], {
        "price": price,
        "shares_outstanding": row["shares_outstanding"]}, None)
    return m.get("valuation_score", 0) >= 6


def score_greenblatt(row, price, peers):
    """EBIT/EV + ROC percentile ranks vs this year's cross-section."""
    ebit = row["operating_income"]
    if not ebit or ebit <= 0:
        return False
    shares = row["shares_outstanding"]
    mcap = price * shares if (shares and price) else None
    cash = row["cash"] or 0
    msec = row["marketable_securities"] or 0
    ev = (mcap + (row["short_term_debt"] or 0) + (row["long_term_debt"] or 0)
          - cash - msec) if mcap else None
    nwc = (row["current_assets"] or 0) - (row["current_liabilities"] or 0)
    ic = nwc + (row["ppe"] or 0)
    if not ev or ev <= 0 or not ic or ic <= 0:
        return False
    ey = ebit / ev
    roc = ebit / ic
    rank_ey = _pct_rank(ey, peers["ey"])
    rank_roc = _pct_rank(roc, peers["roc"])
    combined = (rank_ey + rank_roc) / 2
    return combined >= 0.80


def _pct_rank(v, series):
    if not series:
        return 0.5
    return sum(1 for x in series if x < v) / len(series)


def score_lynch(ticker, row, price, as_of):
    eps = row["eps_diluted"]
    if not eps or eps <= 0 or not price:
        return False
    # 5y EPS CAGR from the same point-in-time store: base = the annual ~5
    # years before the current row (hist is oldest-first, so hist[-5]).
    hist = [h for h in fh.history(ticker)
            if h["period_end"] < row["period_end"] and h["filed"] <= as_of
            and h["eps_diluted"]]
    if len(hist) < 5:
        return False
    eps0 = hist[-5]["eps_diluted"]
    if not eps0 or eps0 <= 0:
        return False
    try:
        growth = (eps / eps0) ** (1 / 5) - 1
    except Exception:
        return False
    if growth <= 0:
        return False
    peg = (price / eps) / (growth * 100)
    return 0 < peg < 1.0


def score_buffett(row):
    ni, eq = row["net_income"], row["total_equity"]
    if not ni or ni <= 0 or not eq or eq <= 0:
        return False
    roe = ni / eq
    if roe < 0.15:
        return False
    ocf, capex = row["operating_cf"], row["capex"]
    if not ocf or capex is None:
        return False
    fcf = ocf + capex          # capex stored negative
    if fcf / ni < 0.8:
        return False
    debt = (row["short_term_debt"] or 0) + (row["long_term_debt"] or 0)
    return debt / eq < 0.5


# --------------------------------------------------------------------------- #
# Study
# --------------------------------------------------------------------------- #
def rebalance_dates(start, end):
    return [f"{y}-04-01" for y in range(start, end)]


def run_study(start, end, costs=True):
    dates = rebalance_dates(start, end)
    import sp500_history
    sp_hist = sp500_history.fetch_and_cache()   # survivorship-aware universe
    methods = ["graham", "greenblatt", "lynch", "buffett"]
    # per-fold results: method -> {rets, base, n}
    folds = []
    for i, r in enumerate(dates[:-1]):
        nxt = dates[i + 1]
        # point-in-time universe: store tickers that were S&P members at r
        tickers = sorted({row[0] for row in fh._conn().execute(
            "SELECT DISTINCT ticker FROM annual")}
            & sp500_history.members_on(r, sp_hist))
        verdicts = {m: [] for m in methods}     # ticker -> bool
        base_rets = []
        peers = {"ey": [], "roc": []}
        rows = {}
        for t in tickers:
            row, price = _pv(t, r)
            if not row:
                continue
            rows[t] = (row, price)
            if row["operating_income"] and row["operating_income"] > 0:
                shares = row["shares_outstanding"]
                mcap = price * shares if (shares and price) else None
                ev = (mcap + (row["short_term_debt"] or 0)
                      + (row["long_term_debt"] or 0) - (row["cash"] or 0)
                      - (row["marketable_securities"] or 0)) if mcap else None
                if mcap and ev and ev > 0:
                    peers["ey"].append(row["operating_income"] / ev)
                nwc = (row["current_assets"] or 0) - (row["current_liabilities"] or 0)
                ic = nwc + (row["ppe"] or 0)
                if ic and ic > 0:
                    peers["roc"].append(row["operating_income"] / ic)
        # labels (annual forward return)
        fwd = {}
        for t, (row, price) in rows.items():
            p_next = fh.price_on(t, nxt)
            if p_next:
                fwd[t] = p_next / price - 1
                base_rets.append(fwd[t])
        base = sum(base_rets) / len(base_rets) if base_rets else None
        for t, (row, price) in rows.items():
            if t not in fwd:
                continue
            verdicts["graham"].append((t, score_graham(row, price)))
            verdicts["greenblatt"].append((t, score_greenblatt(row, price, peers)))
            verdicts["lynch"].append((t, score_lynch(t, row, price, r)))
            verdicts["buffett"].append((t, score_buffett(row)))
        fold = {"date": r, "base": base, "n": len(base_rets),
                "fwd": fwd, "verdicts": verdicts}
        folds.append(fold)
    return folds


def aggregate(folds, method_names, costs=True):
    """{method: {mean, hit, n, vol, sharpe, max_dd, fold_stats[]}}."""
    out = {}
    for m in method_names:
        rets, hits, n = [], 0, 0
        fold_stats = []
        for fold in folds:
            m_rets, m_hits = [], 0
            for t, passed in fold["verdicts"].get(m, []):
                if not passed or t not in fold["fwd"]:
                    continue
                r = fold["fwd"][t]
                if costs:
                    r -= COST_RT * 2        # full annual turnover
                m_rets.append(r)
                m_hits += 1 if r > 0 else 0
            base = fold["base"]
            fold_stats.append({"date": fold["date"], "n": len(m_rets),
                               "mean": (sum(m_rets) / len(m_rets)) if m_rets else None,
                               "base": base,
                               "excess": ((sum(m_rets) / len(m_rets) - base)
                                          if m_rets and base is not None else None),
                               "hit": (m_hits / len(m_rets)) if m_rets else None})
            rets.extend(m_rets)
            hits += m_hits
            n += len(m_rets)
        # risk from the annual strategy series (fold-level means)
        ann = [fs["mean"] for fs in fold_stats if fs["mean"] is not None]
        vol = _std(ann) if len(ann) > 1 else None
        sharpe = (sum(ann) / len(ann) / vol) if (ann and vol) else None
        max_dd = _max_dd(ann) if ann else None
        out[m] = {"mean": (sum(rets) / n) if n else None, "hit": (hits / n) if n else None,
                  "n": n, "vol": vol, "sharpe": sharpe, "max_dd": max_dd,
                  "folds": fold_stats}
    return out


def _std(xs):
    import statistics
    return statistics.pstdev(xs)


def _max_dd(annual_rets):
    """Max drawdown of the cumulative equity curve from annual returns."""
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in annual_rets:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    return mdd


def build_prices(tickers):
    """Fetch + cache daily closes for the universe (resumable, ~1-2s each)."""
    import time
    n = 0
    for i, t in enumerate(tickers, 1):
        try:
            got = fh.ensure_prices(t)
            if got:
                n += 1
            print(f"[{i}/{len(tickers)}] {t}: {got or 'cached'}")
        except Exception as e:
            print(f"[{i}/{len(tickers)}] {t}: FAILED {e}")
        time.sleep(0.1)
    print(f"prices fetched: {n}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=DEFAULT_START)
    ap.add_argument("--end", type=int, default=DEFAULT_END)
    ap.add_argument("--no-costs", action="store_true")
    ap.add_argument("--build-prices", action="store_true")
    args = ap.parse_args()

    tickers = sorted({r[0] for r in fh._conn().execute(
        "SELECT DISTINCT ticker FROM annual")})
    if args.build_prices:
        build_prices(tickers)
        raise SystemExit(0)

    folds = run_study(args.start, args.end, costs=not args.no_costs)
    methods = ["graham", "greenblatt", "lynch", "buffett"]
    # ensemble verdicts per (ticker, fold): agreement count among the 4
    for fold in folds:
        ens = {t: sum(1 for m in methods if (t, True) in fold["verdicts"][m])
               for t in fold["fwd"]}
        fold["verdicts"]["ensemble"] = [(t, c >= 3) for t, c in ens.items()]
        fold["verdicts"]["ensemble2"] = [(t, c >= 2) for t, c in ens.items()]
    all_methods = methods + ["ensemble", "ensemble2"]
    agg = aggregate(folds, all_methods, costs=not args.no_costs)

    # SPY benchmark (survivorship sanity check: equal-weight base vs index)
    fh.ensure_prices("^GSPC")
    dates = rebalance_dates(args.start, args.end)
    spy = []
    for i, r in enumerate(dates[:-1]):
        nxt = dates[i + 1]
        p0, p1 = fh.price_on("^GSPC", r), fh.price_on("^GSPC", nxt)
        spy.append((p1 / p0 - 1) if (p0 and p1) else None)

    # Agreement buckets: pooled mean/hit by # methods passing (0-4) — the
    # direct test of "cross-referencing adds value". Also track the per-fold
    # bucket means so risk metrics can be computed from the annual series.
    buckets = {k: {"rets": [], "hits": 0, "fold_mean": []} for k in range(5)}
    for fold in folds:
        ens_count = {t: sum(1 for m in methods
                            if (t, True) in fold["verdicts"][m])
                     for t in fold["fwd"]}
        per_fold = {k: [] for k in range(5)}
        for t, n_pass in ens_count.items():
            r = fold["fwd"][t]
            if not args.no_costs:
                r -= COST_RT * 2
            buckets[n_pass]["rets"].append(r)
            buckets[n_pass]["hits"] += 1 if r > 0 else 0
            per_fold[n_pass].append(r)
        for k in range(5):
            if per_fold[k]:
                buckets[k]["fold_mean"].append(sum(per_fold[k]) / len(per_fold[k]))

    lines = ["# Framework Cross-Reference Study v2 (walk-forward OOS, survivorship-aware)",
             "",
             f"- Window: {args.start}-04-01 .. {args.end}-04-01, annual rebalance",
             f"- Universe: point-in-time S&P 500 membership (Wikipedia changes "
             f"table) ∩ fundamentals store — {len(tickers)} tickers in store",
             f"- Costs: {'10bp/side (20bp/yr)' if not args.no_costs else 'none (gross)'}",
             f"- Data: SEC XBRL as-originally-reported annual facts, filed-date point-in-time",
             f"- Risk: annual strategy series = fold-level mean returns "
             f"({len(folds)} points); MaxDD from cumulative product",
             "", "## Pooled", "",
             "| Method | N | Mean | Hit% | vs Base | Vol | Sharpe | MaxDD |",
             "|---|---|---|---|---|---|---|---|"]

    def _pct(v, digits=2):
        return f"{v:.{digits}%}" if v is not None else "-"

    for m in all_methods:
        a = agg[m]
        base_mean = sum((fs["base"] or 0) for fs in a["folds"]) / max(len(a["folds"]), 1)
        excess = (a["mean"] - base_mean) if a["mean"] is not None else None
        lines.append(f"| {m} | {a['n']} | {_pct(a['mean'])} | {_pct(a['hit'], 0)} "
                     f"| {_pct(excess)} | {_pct(a['vol'])} | "
                     f"{a['sharpe']:.2f} | {_pct(a['max_dd'])} |")
    lines += ["", "## Fold-level (excess over base, hit%)", "",
              "| Date | Base | SPY | " + " | ".join(methods) + " | ensemble |",
              "|---|---|---|" + "---|" * (len(methods) + 1)]
    for i, fold in enumerate(folds):
        cells = [f"{fold['base']:.2%}",
                 f"{spy[i]:.2%}" if spy[i] is not None else "-"]
        for m in methods:
            fs = agg[m]["folds"][i]
            cells.append(f"{fs['mean']:.2%} ({fs['n']})" if fs["mean"] is not None else "-")
        ens = agg["ensemble"]["folds"][i]
        cells.append(f"{ens['mean']:.2%} ({ens['n']})" if ens["mean"] is not None else "-")
        lines.append(f"| {fold['date']} | " + " | ".join(cells) + " |")

    lines += ["", "## Agreement buckets (pooled, # methods passing)", "",
              "| # Pass | N | Mean | Hit% | Vol | Sharpe | MaxDD |",
              "|---|---|---|---|---|---|---|"]
    for k in range(5):
        b = buckets[k]
        n = len(b["rets"])
        mean = (sum(b["rets"]) / n) if n else None
        hit = (b["hits"] / n) if n else None
        vol = _std(b["fold_mean"]) if len(b["fold_mean"]) > 1 else None
        sharpe = (mean / vol) if (mean and vol) else None
        lines.append(f"| {k} | {n} | {_pct(mean)} | {_pct(hit, 0)} | "
                     f"{_pct(vol)} | {sharpe:.2f} | {_pct(_max_dd(b['fold_mean']))} |")
    lines += ["", "## Caveats", "",
              "- Survivorship: universe = point-in-time S&P membership "
              "reconstructed from Wikipedia's selected-changes table (current "
              "tickers only — renames not retro-mapped; non-index delistings "
              "under-represented). SPY column is the index return for calibration.",
              "- Equal-weight baskets, annual rebalance at Apr-01 (captures "
              "Dec-FYE 10-Ks). As-originally-reported facts (no restatements).",
              "- Costs: flat 20bp/yr (full annual turnover approximation).",
              "- Risk series = 10 annual points (fold means); Sharpe/MaxDD are "
              "coarse. 4-pass bucket n small — not statistically significant.",
              "- Graham PASS = score >= 6; Greenblatt = top-20% combined EBIT/EV "
              "+ ROC rank (EBIT > 0 only); Lynch = 0 < PEG < 1 (5y EPS CAGR); "
              "Buffett = ROE>=15% + FCF/NI>=0.8 + D/E<0.5."]
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w") as fh_:
        fh_.write("\n".join(lines))
    print("\n".join(lines))
    print(f"\nreport: {REPORT}")
