"""
Tests for the Earnings Estimates module (estimates.py).

Network-free: yfinance is swapped for a fake via `estimates._yf`. Covers the
JSON-safety NaN regression (GME), per-section fail-open, the revenue-period
mismatch guard, cache TTL semantics, derived stats, and the /api/estimates
route (in-process server fixture, same pattern as test_year_highs.py).
"""
import json
import threading
import urllib.error
import urllib.request

import estimates as est
import pandas as pd
import pytest


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class FakeTicker:
    """Attribute-dict ticker; `fail` names raise (simulates dead endpoints)."""

    def __init__(self, attrs=None, fail=()):
        self._attrs = attrs or {}
        self._fail = set(fail)

    def __getattr__(self, name):
        if name in self._fail:
            raise RuntimeError(f"attr {name} exploded")
        return self._attrs.get(name)


class FakeYF:
    def __init__(self, factory):
        self._factory = factory

    def Ticker(self, symbol):
        return self._factory(symbol)


def _ee_frame(**overrides):
    df = pd.DataFrame({
        'avg': [1.97549, 2.90908, 8.79979, 9.54902],
        'low': [1.93, 2.51, 8.28, 8.24],
        'high': [2.04, 3.42, 8.92, 10.67],
        'yearAgoEps': [1.85, 2.84, 7.46, 8.79979],
        'growth': [0.0678, 0.0243, 0.1796, 0.0851],
        'numberOfAnalysts': [28, 23, 38, 41],
    }, index=['0q', '+1q', '0y', '+1y'])
    for k, v in overrides.items():
        df[k] = v
    return df


def _hist_frame():
    idx = pd.to_datetime(['2025-09-30', '2025-12-31', '2026-03-31', '2026-06-30'])
    return pd.DataFrame({
        'epsActual': [1.85, 2.84, 2.11, 2.64],
        'epsEstimate': [1.76993, 2.6708, 2.05, 2.48],
        'surprisePercent': [0.0452, 0.0634, 0.0293, 0.0645],
    }, index=idx)


def _trend_frame():
    return pd.DataFrame({
        'current': [1.97549, 2.90908, 8.79979, 9.54902],
        '7daysAgo': [1.99, 2.92, 8.82, 9.58],
        '30daysAgo': [2.01, 2.95, 8.90, 9.66],
        '90daysAgo': [2.05, 2.99, 8.98, 9.74],
    }, index=['0q', '+1q', '0y', '+1y'])


def _rev_frame():
    return pd.DataFrame({
        'upLast7days': [1, 2, 0, 2],
        'upLast30days': [4, 5, 5, 7],
        'downLast30days': [2, 0, 2, 1],
        'downLast7Days': [1, 0, 1, 0],
    }, index=['0q', '+1q', '0y', '+1y'])


def _recs_frame():
    return pd.DataFrame({
        'period': ['0m', '-1m', '-2m', '-3m'],
        'strongBuy': [6, 6, 6, 7],
        'buy': [21, 22, 22, 23],
        'hold': [15, 14, 16, 15],
        'sell': [2, 2, 1, 1],
        'strongSell': [2, 2, 2, 2],
    }, index=[0, 1, 2, 3])


def _growth_frame():
    return pd.DataFrame({
        'stockTrend': [0.0683, 0.0161, 0.1787, 0.0818, float('nan')],
        'indexTrend': [0.4743, 0.2317, 0.3042, 0.1372, 0.1220],
    }, index=['0q', '+1q', '0y', '+1y', 'LTG'])


def _ed_frame():
    idx = pd.DatetimeIndex(
        [pd.Timestamp('2026-10-29 16:00:00-0400', tz='America/New_York')])
    return pd.DataFrame({
        'EPS Estimate': [1.98],
        'Reported EPS': [float('nan')],
        'Surprise(%)': [float('nan')],
    }, index=idx)


def _ticker(attrs=None, fail=()):
    return FakeTicker(attrs, fail)


def _default_attrs(**overrides):
    attrs = {
        'earnings_estimate': _ee_frame(),
        'revenue_estimate': _ee_frame(),
        'earnings_history': _hist_frame(),
        'eps_trend': _trend_frame(),
        'eps_revisions': _rev_frame(),
        'growth_estimates': _growth_frame(),
        'recommendations_summary': _recs_frame(),
        'analyst_price_targets': {'current': 210.0, 'mean': 238.0, 'median': 240.0,
                                  'high': 260.0, 'low': 180.0},
        'earnings_dates': _ed_frame(),
    }
    attrs.update(overrides)
    return attrs


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    """Isolate the module cache per test."""
    monkeypatch.setattr(est, "_cache", {})


def _install(monkeypatch, attrs=None, fail=()):
    monkeypatch.setattr(est, "_yf",
                        FakeYF(lambda s: _ticker(_default_attrs(**(attrs or {})), fail)))


# --------------------------------------------------------------------------- #
# P1 regression: NaN must never reach JSON (GME yearAgoEps)
# --------------------------------------------------------------------------- #
def test_nan_scrubbed_to_none(monkeypatch):
    # GME-like: yearAgoEps NaN for 0y/+1y; np.float64 (pandas dtype)
    ee = _ee_frame(yearAgoEps=[1.85, 2.84, float('nan'), float('nan')])
    _install(monkeypatch, {'earnings_estimate': ee})
    payload = est.get_estimates('GME')
    rows = {s['period']: s for s in payload['summary']}
    assert rows['0y']['eps_year_ago'] is None
    assert rows['+1y']['eps_year_ago'] is None
    assert rows['0q']['eps_year_ago'] == 1.85
    # the server path (clean_dict + SafeJSONEncoder) must never emit bare NaN
    raw = json.dumps(payload)
    assert 'NaN' not in raw


def test_nan_scrubbed_through_server_encoder(monkeypatch):
    """End-to-end: clean_dict + encoder on an np.float64 NaN payload."""
    from server import SafeJSONEncoder, clean_dict
    ee = _ee_frame(yearAgoEps=[1.85, 2.84, float('nan'), float('nan')])
    _install(monkeypatch, {'earnings_estimate': ee})
    payload = est.get_estimates('GME')
    raw = json.dumps(clean_dict(payload), cls=SafeJSONEncoder)
    assert 'NaN' not in raw


# --------------------------------------------------------------------------- #
# Robustness
# --------------------------------------------------------------------------- #
def test_rev_period_mismatch_no_crash(monkeypatch):
    """revenue_estimate missing a period -> rev_avg None, not KeyError."""
    rev = _ee_frame().drop('+1q')
    _install(monkeypatch, {'revenue_estimate': rev})
    payload = est.get_estimates('AAPL')
    rows = {s['period']: s for s in payload['summary']}
    assert rows['+1q']['rev_avg'] is None
    assert rows['0q']['rev_avg'] == pytest.approx(1.97549)
    assert 'error' not in payload


def test_missing_column_no_crash(monkeypatch):
    """yfinance drops all-NaN columns -> missing yearAgoEps must not KeyError."""
    ee = _ee_frame().drop(columns=['yearAgoEps'])
    _install(monkeypatch, {'earnings_estimate': ee})
    payload = est.get_estimates('AAPL')
    assert all(s['eps_year_ago'] is None for s in payload['summary'])
    assert 'error' not in payload


def test_attr_failure_degrades_section_only(monkeypatch):
    """A dead eps_revisions endpoint blanks that section, not the page."""
    _install(monkeypatch, fail=('eps_revisions',))
    payload = est.get_estimates('AAPL')
    assert payload['revisions'] == []
    assert len(payload['summary']) == 4
    assert 'error' not in payload


def test_ticker_normalized_upper(monkeypatch):
    _install(monkeypatch)
    payload = est.get_estimates('  aapl ')
    assert payload['ticker'] == 'AAPL'


def test_empty_ticker_error():
    assert est.get_estimates('')['error'] == 'ticker required'
    assert est.get_estimates(None)['error'] == 'ticker required'


# --------------------------------------------------------------------------- #
# Cache semantics
# --------------------------------------------------------------------------- #
def test_cache_hit_skips_fetch(monkeypatch):
    calls = {'n': 0}

    def factory(symbol):
        calls['n'] += 1
        return _ticker(_default_attrs())

    monkeypatch.setattr(est, "_yf", FakeYF(factory))
    est.get_estimates('AAPL')
    est.get_estimates('AAPL')
    est.get_estimates('aapl')  # same cache key
    assert calls['n'] == 1


def test_cache_expiry_refetches(monkeypatch):
    calls = {'n': 0}

    def factory(symbol):
        calls['n'] += 1
        return _ticker(_default_attrs())

    monkeypatch.setattr(est, "_yf", FakeYF(factory))
    est.get_estimates('AAPL')
    # age the entry past TTL
    est._cache['AAPL'] = (0.0, est._cache['AAPL'][1])
    est.get_estimates('AAPL')
    assert calls['n'] == 2


def test_error_not_cached(monkeypatch):
    def boom(symbol):
        raise RuntimeError("network down")

    monkeypatch.setattr(est, "_yf", FakeYF(boom))
    payload = est.get_estimates('AAPL')
    assert payload['error']
    assert 'AAPL' not in est._cache
    # next call retries (does not serve a stale error)
    payload2 = est.get_estimates('AAPL')
    assert payload2['error']


# --------------------------------------------------------------------------- #
# Derived data
# --------------------------------------------------------------------------- #
def test_surprise_stats(monkeypatch):
    idx = pd.to_datetime(['2025-09-30', '2025-12-31', '2026-03-31', '2026-06-30'])
    hist = pd.DataFrame({
        'epsActual': [1.0, 2.0, 3.0, 4.0],
        'epsEstimate': [1.0, 2.0, 3.0, 4.0],
        'surprisePercent': [0.05, -0.02, 0.03, 0.01],
    }, index=idx)
    _install(monkeypatch, {'earnings_history': hist})
    ss = est.get_estimates('AAPL')['surprise_stats']
    assert ss['n'] == 4
    assert ss['beat_rate'] == pytest.approx(0.75)
    assert ss['avg_surprise_pct'] == pytest.approx(1.75)
    assert ss['beat_streak'] == 2  # most recent two quarters both beat
    assert ss['best_pct'] == pytest.approx(5.0)


def test_next_earnings(monkeypatch):
    _install(monkeypatch)
    ne = est.get_estimates('AAPL')['next_earnings']
    assert ne['date'].startswith('2026-10-29')
    assert ne['eps_estimate'] == pytest.approx(1.98)
    assert ne['reported_eps'] is None  # upcoming report: no actual yet


def test_revisions_net_and_column_case(monkeypatch):
    _install(monkeypatch)
    rev = est.get_estimates('AAPL')['revisions']
    by_p = {r['period']: r for r in rev}
    assert by_p['0q']['up_30d'] == 4
    assert by_p['0q']['down_7d'] == 1      # 'downLast7Days' (capital D) resolved
    assert by_p['0q']['net_30d'] == 2      # 4 up - 2 down


def test_recommendations_and_growth_mapping(monkeypatch):
    _install(monkeypatch)
    payload = est.get_estimates('AAPL')
    assert payload['recommendations'][0]['strong_buy'] == 6
    assert payload['recommendations'][0]['period'] == '0m'
    ltg = [g for g in payload['growth'] if g['period'] == 'LTG'][0]
    assert ltg['stock'] is None            # NaN scrubbed
    assert ltg['index'] == pytest.approx(0.1220)


def test_price_targets(monkeypatch):
    _install(monkeypatch)
    apt = est.get_estimates('AAPL')['price_targets']
    assert apt['mean'] == 238.0
    assert apt['current'] == 210.0


# --------------------------------------------------------------------------- #
# Route (in-process server, same fixture shape as test_year_highs.py)
# --------------------------------------------------------------------------- #
@pytest.fixture
def server(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init_db()
    import importlib

    import server as srv
    importlib.reload(srv)
    srv.Handler._discover_module_routes()
    httpd = srv.HTTPServer(("127.0.0.1", 0), srv.Handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    yield httpd
    httpd.shutdown()


def _get_json(server, path):
    port = server.server_address[1]
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return json.loads(r.read().decode())


def test_route_estimates_ok(server, monkeypatch):
    monkeypatch.setattr(est, "_fetch_estimates",
                        lambda t: {"ticker": t, "summary": [], "history": [],
                                   "trends": [], "revisions": [], "growth": [],
                                   "recommendations": [], "price_targets": None,
                                   "next_earnings": None, "surprise_stats": None})
    data = _get_json(server, "/api/estimates?ticker=aapl")
    assert data["ticker"] == "AAPL"  # handler normalizes case


def test_route_estimates_missing_ticker_400(server, monkeypatch):
    monkeypatch.setattr(est, "_fetch_estimates", lambda t: {"error": "unused"})
    port = server.server_address[1]
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/estimates", timeout=10)
    assert exc.value.code == 400
