"""NS-ETF data pipeline — refresh prices into sqlite, emit signals.json.

Runs under launchd (after A_T/NS-7 feeds). Deterministic; fail-open on
per-ticker outages (surfaced as events, never silent defaults).
Runtime: CLT py3.9, `env -i HOME=$HOME python3 pipeline.py`.
"""
import datetime as dt
import json

import config
import indicators
import overlay
import regime
import selector
import store


def fetch_prices(tickers, period="1y"):
    """yfinance daily OHLC → {ticker: [(date, close, high, low), ...]}.
    Per-ticker failure returns [] for that ticker (never raises)."""
    import yfinance as yf  # deferred: tests run hermetic without network
    out = {}
    for t in tickers:
        try:
            df = yf.download(t, period=period, progress=False, auto_adjust=True)
            if df is None or len(df) == 0:
                out[t] = []
                continue
            if hasattr(df.columns, "get_level_values") and df.columns.nlevels > 1:
                df.columns = df.columns.get_level_values(0)
            rows = []
            for date_idx, row in df.iterrows():
                try:
                    d = getattr(date_idx, "date", None)
                    ds = str(d()) if callable(d) else str(date_idx)[:10]
                except Exception:
                    ds = str(date_idx)[:10]

                def _f(x):
                    try:
                        return float(x.item())
                    except Exception:
                        return float(x)
                close = _f(row["Close"])
                high = _f(row["High"]) if "High" in row else close
                low = _f(row["Low"]) if "Low" in row else close
                rows.append((ds, close, high, low))
            out[t] = rows
        except Exception:
            out[t] = []
    return out


def vix_snapshot():
    """(spot, avg) from yfinance ^VIX. (None, None) on outage."""
    try:
        import yfinance as yf
        df = yf.download(config.VIX_SPOT_SERIES,
                         period=f"{config.VIX_AVG_WINDOW + 10}d",
                         progress=False, auto_adjust=True)
        if df is None or df.empty or "Close" not in df:
            return None, None
        close_col = df["Close"]
        if hasattr(close_col, "columns"):   # multi-ticker download → DataFrame
            close_col = close_col.iloc[:, 0]
        closes = []
        for c in close_col.dropna():
            closes.append(float(c.item()) if hasattr(c, "item") else float(c))
        if not closes:
            return None, None
        spot = closes[-1]
        avg = sum(closes[-config.VIX_AVG_WINDOW:]) / min(config.VIX_AVG_WINDOW, len(closes))
        return spot, avg
    except Exception:
        return None, None


def build_advisory_panel(price_map):
    """Sector-ratio trend/composite panel (NS-4 PROD methodology).
    Display-only metadata — never enters weights."""
    panel = []
    for ratio, name in config.ADVISORY_RATIOS:
        num, den = ratio.split("/")
        pn, pd_ = price_map.get(num), price_map.get(den)
        if not pn or not pd_:
            panel.append({"ratio": ratio, "name": name, "error": "no data"})
            continue
        n = min(len(pn), len(pd_))
        series = [pn[-n + i][1] / pd_[-n + i][1] for i in range(n)]
        smas = {}
        for w in (10, 20, 50):
            m = indicators.sma(series, w)
            if m is not None:
                smas[w] = round(m, 4)
        rsi = indicators.wilder_rsi(series)
        macd_info = indicators.macd(series)
        adx = indicators.wilder_adx(
            [r[2] / pd_[-n + i][1] for i, r in enumerate(pn[-n:])],
            [r[3] / pd_[-n + i][1] for i, r in enumerate(pn[-n:])],
            series) if n > 30 else None
        # Composite alignment score: price vs SMAs + MACD sign + RSI mid + ADX level
        align = 0
        last = series[-1]
        for w, m in smas.items():
            align += 1 if last > m else -1
        if macd_info:
            align += 1 if macd_info["hist"] > 0 else -1
        if isinstance(rsi, float) and 45 <= rsi <= 65:
            align += 0  # neutral zone adds nothing
        trend = "UP" if align >= 2 else ("DOWN" if align <= -2 else "MIXED")
        panel.append({
            "ratio": ratio, "name": name, "last": round(last, 4),
            "sma": smas,
            "rsi": round(rsi, 1) if isinstance(rsi, float) else None,
            "adx": round(adx["adx"], 1) if isinstance(adx, dict) else None,
            "alignment": align, "trend": trend, "advisory_only": True,
        })
    return panel


def run(fetcher=None, vix_fn=None, db_path=None):
    """Full refresh: prices → scores → sleeves → overlay → signals.json.
    Returns the emitted dict."""
    fetcher = fetcher or fetch_prices
    vix_fn = vix_fn or vix_snapshot
    conn = store._connect(db_path)
    store.init_db(db_path)

    tickers = sorted(set(config.UNIVERSE) | {"HYG"})   # HYG for macro context later
    prices = fetcher(sorted(set(tickers) |
                            {r.split("/")[0] for r, _ in config.ADVISORY_RATIOS} |
                            {r.split("/")[1] for r, _ in config.ADVISORY_RATIOS}))
    events = []
    for t, rows in prices.items():
        if rows:
            store.upsert_prices(conn, t, rows)
        else:
            events.append({"type": "data_gap", "ticker": t})

    spy_closes = selector.store_series(conn, "SPY")
    if not spy_closes:
        # Fail-open: no SPY → cannot score relative strength; still rank absolute.
        spy_closes = []

    # Rank each allocation-bearing sleeve
    sleeve_picks, sleeve_weights, signal_rows = {}, {}, []
    for sleeve, members in (
            ("defensive", config.DEFENSIVE_ETFS),
            ("real_asset", config.REAL_ASSET_ETFS)):
        ranked = selector.rank_sleeve(conn, members, spy_closes)
        top = [r for r in ranked if "score" in r][:config.TOP_N_PER_SLEEVE]
        picks = [r["ticker"] for r in top]
        sleeve_picks[sleeve] = ranked
        wts = selector.inverse_vol_weights(conn, picks) if picks else {}
        sleeve_weights[sleeve] = wts
        for r in top:
            signal_rows.append((r["ticker"], sleeve, r["score"], 1, wts.get(r["ticker"], 0.0)))

    # VIX overlay across the blended book
    blended = dict(sleeve_weights.get("defensive", {}))
    for t, w in sleeve_weights.get("real_asset", {}).items():
        blended[t] = blended.get(t, 0.0) + w
    spot, avg = vix_fn()
    vix_info = overlay.vix_state(spot, avg)
    final_w, o_events = overlay.apply_overlay(conn, sleeve_picks, blended, vix_info)
    events.extend(o_events)

    # Regime scaling is advisory metadata (soft); scores already computed.
    mom_series = []
    if spy_closes and len(spy_closes) > config.WEEKLY_RANK_WINDOW // 2:
        step = 5
        mom_series = [spy_closes[i] / spy_closes[i - 21] - 1.0
                      for i in range(step, len(spy_closes), step)]
    reg = regime.classify(momentum_series=mom_series)

    as_of = str(dt.date.today())
    if signal_rows:
        store.save_signals(conn, as_of, [(t, s, sc, sg, w)
                                         for t, s, sc, sg, w in signal_rows])
    store.set_meta(conn, "last_run",
                   {"as_of": as_of, "events": events, "vix": vix_info})
    conn.close()

    flat_signals = {t: (1 if w > 0 else 0) for t, w in final_w.items()}
    feed = {
        "as_of": as_of,
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "service": "ns-etf",
        "version": config.FEED_VERSION,
        "sleeves": {
            "defensive": {
                "signals": {t: 1 for t in sleeve_weights.get("defensive", {})},
                "weights": {t: round(w, 4) for t, w in
                            sleeve_weights.get("defensive", {}).items()},
            },
            "real_asset": {
                "signals": {t: 1 for t in sleeve_weights.get("real_asset", {})},
                "weights": {t: round(w, 4) for t, w in
                            sleeve_weights.get("real_asset", {}).items()},
            },
        },
        "signals": flat_signals,
        "weights": {t: round(w, 6) for t, w in final_w.items()},
        "regime": reg,
        "crisis_mode": vix_info.get("crisis", False),
        "vix": {"spot": vix_info.get("spot"), "avg": vix_info.get("avg"),
                "state": vix_info.get("state"),
                "exposure_cap": vix_info.get("exposure_cap")},
        "advisory_sector_ratios": build_advisory_panel(prices),
        "events": events,
    }
    with open(config.SIGNALS_PATH, "w") as fh:
        json.dump(feed, fh, indent=2)
    return feed


if __name__ == "__main__":
    result = run()
    print(json.dumps({"as_of": result["as_of"],
                      "crisis": result["crisis_mode"],
                      "weights": result["weights"],
                      "events": len(result["events"])}, indent=2))
