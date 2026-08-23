"""vs_badge_refresh.py — daily value-screen + HMM badge snapshot for NS-7.

Once-a-day batch (launchd com.ninestreet.ns7.vsbadge-refresh, weekdays ~17:40 ET,
after close and after the A_T daily update). Population: every scored Major name
in the latest selection doc that outperforms BOTH SPY & QQQ over the identical
126/21 window — a SMALLER population than the Major league; everyone else is
skipped (PM-approved scope).

Per ticker two INDEPENDENT computations:
  1. HMM signal — reuses NS-2_QA's methodology functions directly (single source
     of truth; no reimplementation drift) INCLUDING the walk-forward acceptance
     gate (apply_acceptance_gate vs ns2_walkforward_results.json; missing file
     fails open = ungated). NOTE: we deliberately do NOT call run_ticker() — it
     has the side effect of writing NS-2's shared signal cache, which this job
     does not own. We replicate its exact call chain minus the cache write.
     The NS-2 import happens INSIDE the compute function so importing this
     module stays light (hermetic tests, no pandas/hmmlearn dependency).
  2. Value screen — read-only GET on A_T's /api/fundamentals/screen; keep ALL
     FOUR framework verdicts (pass AND fail) + metrics + agreement.

Output: data/vs_pass_badges.json:
    {"as_of", "generated_at", "population", "tickers": {TKR: {
        "hmm": {"signal", "color", "gate"},
        "value_screen": {"agreement", "frameworks": {
            "graham": {"pass": bool, "metric": str}, ...}}}}}

Fail-open contract: any per-ticker failure leaves that ticker out of (or marked
errored inside) the snapshot; the dashboard renders nothing extra. A missing or
stale snapshot file never blocks the page.

Run: CLT py3.9 clean env (same as run_refresh.sh).
"""
import json
import importlib.util
import os
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

FRAMEWORKS = ("graham", "greenblatt", "lynch", "buffett")

# Metric formatting per framework from the screener payload fields.
def _fmt_pct(v):
    return f"{v * 100:.1f}%" if isinstance(v, (int, float)) else "—"


def framework_metric(name, fw):
    """Human-readable key metric line for one framework verdict."""
    if name == "graham":
        gn = fw.get("graham_number")
        parts = []
        if fw.get("pe") is not None:
            parts.append(f"P/E {fw['pe']:.1f}")
        if gn is not None:
            parts.append(f"Graham number ${gn:.0f}")
        return " · ".join(parts) or "—"
    if name == "greenblatt":
        return f"EY {_fmt_pct(fw.get('ey'))} / ROC {_fmt_pct(fw.get('roc'))}"
    if name == "lynch":
        growth = fw.get("growth")
        g = f"{growth * 100:.0f}%" if isinstance(growth, (int, float)) else "—"
        return f"PEG {fw.get('peg', '—')} (growth {g})"
    if name == "buffett":
        return (f"ROE {_fmt_pct(fw.get('roe'))} · FCF conv "
                f"{fw.get('fcf_conv', '—')}× · D/E {fw.get('de', '—')}")
    return "—"


def screen_value_screener(ticker, base_url=None, timeout=15):
    """All-four-framework verdict dict from A_T's screener (read-only).
    Raises on outage — caller decides fail-open policy."""
    base = base_url or getattr(config, "AT_SCREENER_URL", "http://127.0.0.1:9099")
    url = f"{base}/api/fundamentals/screen?ticker={ticker}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode())
    results = payload.get("results") or []
    if not results:
        raise ValueError(f"empty screener result for {ticker}")
    r = results[0]
    frameworks = {}
    for name in FRAMEWORKS:
        fw = r.get(name) or {}
        passed = bool(fw.get("pass"))
        frameworks[name] = {"pass": passed, "metric": framework_metric(name, fw)}
    return {
        "agreement": int(r.get("agreement") or 0),
        "price": r.get("price"),
        "frameworks": frameworks,
    }


def load_selection_population(selection_path=None):
    """Tickers outperforming both SPY & QQQ from the latest selection doc."""
    p = Path(selection_path or str(config.SELECTION_PATH))
    if not p.exists():
        return [], None
    try:
        doc = json.loads(p.read_text())
    except Exception:
        return [], None
    tickers = [s["ticker"] for s in doc.get("scores", [])
               if s.get("outperforms_benchmarks")]
    return tickers, doc.get("as_of")


def compute_hmm_signal(ticker):
    """NS-2 methodology inline (functions reused, NOT NS-2's run_ticker —
    that writes NS-2's shared signal cache which this job does not own).
    Returns {"signal","color","gate"}; raises on failure."""
    here = os.path.dirname(os.path.abspath(__file__))
    ns2_path = str(getattr(config, "NS2_MODULE_PATH",
                           Path(here).parent / "NS-2_QA" / "qa_server.py"))
    spec = importlib.util.spec_from_file_location(f"ns2_module_{abs(hash(ns2_path))}",
                                                  ns2_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load NS-2 module from {ns2_path}")
    ns2 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ns2)

    profile = ns2.get_profile(ticker)
    df = ns2.fetch_ohlcv(ticker)
    if len(df) < 30:
        raise ValueError(f"only {len(df)} bars — need >=30")
    df = ns2.add_rich_features(df)
    regimes, agreement, ref_model, model_data = ns2.get_regimes(
        df, use_hmm=True, profile=profile)
    df["regime"] = regimes
    df["regime_confidence"] = agreement
    macro = ns2.get_macro_filter()
    df = ns2.generate_signals_v2(df, regimes, agreement, ref_model, model_data,
                                 macro, profile=profile)
    df = ns2.apply_stops(df)
    df = ns2.backtest(df)
    df, dd_fired = ns2.apply_dd_breaker(df)
    if dd_fired:
        df = ns2.backtest(df)
    df = ns2.add_signal_labels_v2(df)
    signal = df["signal_label"].iloc[-1]
    # Walk-forward acceptance gate — NO-EDGE verdict forces the gray label;
    # missing results file fails open (ungated), identical to NS-2's dashboard.
    signal, gate_info = ns2.apply_acceptance_gate(ticker, signal)
    return {
        "signal": signal,
        "color": ns2.SIGNAL_COLORS.get(signal, "#888"),
        "gate": gate_info,
    }


def build_snapshot(selection_path=None, screener_base=None, hmm_fn=compute_hmm_signal,
                   screen_fn=screen_value_screener):
    """Full snapshot dict. Per-ticker errors are recorded, never fatal."""
    tickers, as_of = load_selection_population(selection_path)
    snap = {
        "as_of": as_of,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "service": "NS-7",
        "methodology": "benchmark-outperformer subset · HMM (NS-2 fn reuse + WF gate)"
                       " + 4-framework value screen",
        "population": len(tickers),
        "tickers": {},
    }
    for tkr in tickers:
        entry = {}
        try:
            entry["hmm"] = hmm_fn(tkr)
        except Exception as exc:  # noqa: BLE001 — fail-open per ticker
            entry["hmm"] = {"signal": None, "error": str(exc)}
        try:
            entry["value_screen"] = screen_fn(tkr, screener_base)
        except Exception as exc:  # noqa: BLE001
            entry["value_screen"] = {"agreement": None, "error": str(exc)}
        snap["tickers"][tkr] = entry
    return snap


def main():
    snap = build_snapshot()
    out = Path(str(getattr(config, "VS_BADGES_PATH",
                           Path(str(config.SELECTION_PATH)).parent / "vs_pass_badges.json")))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, indent=2))
    ok_hmm = sum(1 for v in snap["tickers"].values() if v["hmm"].get("signal"))
    ok_vs = sum(1 for v in snap["tickers"].values()
                if v["value_screen"].get("agreement") is not None)
    print(f"[vs-badges] wrote {out} · population {snap['population']} · "
          f"hmm ok {ok_hmm} · screen ok {ok_vs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
