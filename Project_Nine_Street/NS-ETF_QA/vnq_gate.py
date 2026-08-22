#!/usr/bin/env python3
"""VNQ evidence gate (v4.4) — real_asset ± VNQ, per research spec §4.

Runs the P2R_def15 design twice: DBC/GLD vs DBC/GLD/VNQ. VNQ earns a
universe slot only if it improves the frontier (CAGR up at flat/better DD).
Deterministic; uses the disk-cached closes. CLT py3.9, env -u PYTHONPATH.
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import config

CACHE = config.DATA_DIR / "wf_closes.json"
START = "2016-01-01"


def load():
    raw = json.loads(CACHE.read_text())
    return {t: dict(rows) for t, rows in raw.items() if rows}


def month_ends(dates):
    seen = {}
    for d in dates:
        seen[d[:7]] = d
    return sorted(seen.values())


def momentum(m, d, skip=21, look=126):
    ds = [x for x in m if x <= d]
    if len(ds) < look + 1:
        return None
    return m[ds[-1 - skip]] / m[ds[-1 - look]] - 1.0


def sleeve_weights(closes, picks, d):
    inv = {}
    for t in picks:
        m = closes[t]
        ds = [x for x in m if x <= d][-64:]
        if len(ds) < 30:
            continue
        rets = [m[ds[i]] / m[ds[i - 1]] - 1 for i in range(1, len(ds))]
        var = sum(r * r for r in rets) / len(rets)
        if var > 0:
            inv[t] = 1 / math.sqrt(var)
    tot = sum(inv.values())
    return {t: v / tot for t, v in inv.items()} if inv else {}


def run(closes, spy_dates, rebal, ra, with_vix, vix_map):
    sectors = ["XLK", "XLV", "XLF", "XLY", "XLP", "XLE",
               "XLI", "XLB", "XLU", "XLRE"]
    defensive = ["TLT", "IEF", "IEI", "AGG", "SHY", "BIL"]
    universe = sectors + defensive + ra
    equity, last_w, in_crisis = 1.0, {}, False
    peak, mdd = -1e9, 0.0
    for d in spy_dates:
        if vix_map:
            v = vix_map.get(d)
            if v is not None:
                if not in_crisis and v >= config.VIX_CRISIS_LEVEL:
                    in_crisis = True
                elif in_crisis and v < config.VIX_CRISIS_LEVEL - 5:
                    in_crisis = False
        needs = d in rebal or (in_crisis and vix_map and vix_map.get(d) is not None)
        if needs:
            scored = sorted([(t, momentum(closes[t], d)) for t in universe
                             if momentum(closes[t], d) is not None],
                            key=lambda x: x[1], reverse=True)
            sec = [t for t, _ in scored if t in sectors][:3]
            dfn = [t for t, _ in scored if t in defensive][:config.TOP_N_PER_SLEEVE]
            raa = [t for t, _ in scored if t in ra][:config.TOP_N_PER_SLEEVE]
            s, dv = 0.60, 0.15
            spy = closes["SPY"]
            ds = [x for x in spy if x <= d]
            if len(ds) >= 200 and spy[ds[-1]] < sum(spy[x] for x in ds[-200:]) / 200:
                s -= 0.10
                dv += 0.10
            r_share = 1 - s - dv
            tgt = {**{t: w * s for t, w in sleeve_weights(closes, sec, d).items()},
                   **{t: w * dv for t, w in sleeve_weights(closes, dfn, d).items()},
                   **{t: w * r_share for t, w in sleeve_weights(closes, raa, d).items()}}
            if in_crisis:
                safe = sorted(t for t in config.CRISIS_SAFE if t in closes)
                tgt = sleeve_weights(closes, safe, d)
            if tgt:
                traded = sum(abs(tgt.get(t, 0) - last_w.get(t, 0))
                             for t in set(tgt) | set(last_w))
                if traded > 1e-9:
                    equity -= equity * traded * 10 / 10000
                    last_w = tgt
                    # count later via n_rebal placeholder
        if last_w:
            day = 0.0
            for t, w in last_w.items():
                m = closes[t]
                ds = [x for x in m if x <= d]
                if len(ds) >= 2 and ds[-1] == d:
                    day += w * (m[d] / m[ds[-2]] - 1)
            equity *= (1 + day)
        peak = max(peak, equity)
        mdd = min(mdd, equity / peak - 1)
    years = len(spy_dates) / 252
    return {"cagr": equity ** (1 / years) - 1, "max_dd": mdd}


def main():
    raw = json.loads(CACHE.read_text())
    closes = {t: dict(rows) for t, rows in raw.items() if rows}
    spy_dates = sorted(closes["SPY"])
    rebal = set(month_ends(spy_dates)[::3])
    vix_map = {d: v for d, v in closes.get("^VIX", {}).items()}

    variants = {
        "base (DBC,GLD)": ["DBC", "GLD"],
        "+VNQ": ["DBC", "GLD", "VNQ"],
    }
    print("VNQ evidence gate — P2R_def15 design, daily VIX overlay")
    results = {}
    for label, ra in variants.items():
        r = run(closes, spy_dates, rebal, ra, True, vix_map)
        results[label] = {"cagr": round(r["cagr"], 4),
                          "max_dd": round(r["max_dd"], 4)}
        print(f"  {label:18s} CAGR {r['cagr']:.2%}  MaxDD {r['max_dd']:.2%}")

    base, vnq = results["base (DBC,GLD)"], results["+VNQ"]
    verdict = ("ADD VNQ" if vnq["cagr"] > base["cagr"]
               and vnq["max_dd"] <= base["max_dd"] + 0.005 else
               "DEFER VNQ") if vnq["cagr"] > base["cagr"] or vnq["max_dd"] <= base["max_dd"] else "DEFER VNQ"
    print(f"\nVerdict: {verdict} "
          f"(ΔCAGR {vnq['cagr']-base['cagr']:+.2%}, "
          f"ΔDD {vnq['max_dd']-base['max_dd']:+.2%})")
    out = {"as_of_doc": "v4.4", "results": results,
           "verdict": verdict}
    (config.DATA_DIR / "vnq_evidence.json").write_text(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
