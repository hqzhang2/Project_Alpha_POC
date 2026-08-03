"""
Hermetic tests for option_screener.py (features, score, scan). No live network —
the provider is faked via options_data.get_provider monkeypatch.
"""
import sys
import pytest

import config
import option_screener as osmod


# ---------------------------------------------------------------------------
# pure features
# ---------------------------------------------------------------------------
def test_zscore_guard():
    assert osmod._zscore([5, 5, 5]) == [0.0, 0.0, 0.0]
    assert osmod._zscore([1]) == [0.0]
    z = osmod._zscore([1, 2, 3, 4, 5])
    assert abs(sum(z)) < 1e-9
    assert abs(max(z)) > 1.0


def test_moneyness_mult():
    assert osmod.moneyness_mult(110, 100, "Call") == 1.6      # 10% OTM (>=0.10 bucket)
    assert osmod.moneyness_mult(108, 100, "Call") == 1.3      # 8% OTM (>=0.05 bucket)
    assert osmod.moneyness_mult(90, 100, "Put") == 1.6        # 10% OTM put
    assert osmod.moneyness_mult(95, 100, "Call") == 1.0       # ITM call
    assert osmod.moneyness_mult(75, 100, "Put") == 2.0        # >20% OTM put (K<spot for puts!)
    assert osmod.moneyness_mult(104, 100, "Call") == 1.0      # 4% OTM -> bucket 0-5
    assert osmod.moneyness_mult(None, 100, "Call") == 1.0
    assert osmod.moneyness_mult(100, None, "Call") == 1.0


def test_dte_of():
    assert osmod.dte_of("1999-01-01") < 0
    assert osmod.dte_of("garbage") == 999


def test_catalyst_bonus():
    assert osmod.catalyst_bonus(None) == 0.0
    assert osmod.catalyst_bonus(3) == 1.0
    assert osmod.catalyst_bonus(10) == 0.5
    assert osmod.catalyst_bonus(20) == 0.0


def test_iv_cheap_flag():
    assert osmod.iv_cheap_flag({"iv": 0.2}, [0.3, 0.4, 0.5]) == 1.0
    assert osmod.iv_cheap_flag({"iv": 0.6}, [0.3, 0.4, 0.5]) == 0.0
    assert osmod.iv_cheap_flag({"iv": None}, [0.3]) == 0.0
    assert osmod.iv_cheap_flag({"iv": 0.3}, []) == 0.0


def test_score_and_tier():
    r = {"vol_oi_z": 4.0, "notional_z": 4.0, "moneyness_mult": 2.0,
         "iv_cheap": 1.0, "catalyst_bonus": 1.0, "dte": 10}
    # 0.3*4 + 0.25*4 + 0.2*1.0 + 0.15*1 + 0.10*1 = 2.65
    s = osmod.score_contract(r)
    assert s == 2.65
    assert s > config.SCORE_TIER_HIGH
    assert osmod.tier_of(s) == "HIGH"
    r2 = dict(r, dte=1)
    # float-noise tolerance: 2.65 * 0.3 = 0.795 (rounds 0.79 or 0.80 depending on repr)
    assert abs(osmod.score_contract(r2) - 2.65 * 0.3) <= 0.006   # 0DTE dampening
    assert osmod.tier_of(0.5) == "LOW"
    assert osmod.tier_of(config.SCORE_TIER_MED) == "MED"


def test_enrich_adds_features():
    from datetime import date, timedelta
    earnings = (date.today() + timedelta(days=3)).isoformat()  # within 7d -> bonus 1.0
    recs = [{"expiry": "2099-01-01", "strike": 110.0, "type": "Call",
             "vol": 1000, "oi": 100, "bid": 1.0, "ask": 1.2, "last": 1.0, "iv": 0.3}]
    out = osmod.enrich_ticker_contracts(recs, 100.0, earnings)
    r = out[0]
    assert r["notional"] == 110000          # 1000 x 1.10 mid x 100
    assert r["vol_oi"] == 10.0
    assert r["moneyness_mult"] == 1.6       # 10% OTM -> >=0.10 bucket
    assert r["otm_pct"] == 10.0             # actual OTM % (not the multiplier!)
    assert r["dte"] > 0
    assert r["catalyst_bonus"] == 1.0       # earnings 3 days out
    assert "score" in r and r["tier"] in ("HIGH", "MED", "LOW")


def test_enrich_zero_oi_no_crash():
    recs = [{"expiry": "2099-01-01", "strike": 100.0, "type": "Put",
             "vol": 500, "oi": 0, "bid": 0, "ask": 0, "last": 2.0, "iv": None}]
    out = osmod.enrich_ticker_contracts(recs, 100.0, None)
    assert out[0]["vol_oi"] == 500.0        # oi=0 -> max(0,1) guard
    assert out[0]["catalyst_bonus"] == 0.0
    assert "score" in out[0]


# ---------------------------------------------------------------------------
# scan integration (fake provider, hermetic)
# ---------------------------------------------------------------------------
class FakeProvider:
    def get_expirations(self, ticker):
        return ["2099-01-01"]

    def get_chain(self, ticker, expiry=None):
        # call: wins notional_z; put: wins iv_cheap + moneyness (25% OTM) so BOTH score > 0
        return {
            "ticker": ticker, "expiry": expiry, "spot": 100.0,
            "calls": [{"strike": 110.0, "vol": 1000, "oi": 100, "bid": 1.0,
                       "ask": 1.2, "last": 1.0, "iv": 0.3}],
            "puts": [{"strike": 75.0, "vol": 500, "oi": 50, "bid": 0.5,
                      "ask": 0.6, "last": 0.5, "iv": 0.2}],
        }

    def get_next_earnings(self, ticker):
        return "2099-01-10" if ticker == "FAKE" else None


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    osmod._scan_cache["data"] = None
    osmod._scan_cache["ts"] = 0.0
    osmod._universe_cache["names"] = None
    osmod._universe_cache["ts"] = 0.0
    osmod._earnings_cache["data"] = {}
    osmod._earnings_cache["ts"] = 0.0
    # fake provider at the seam option_screener actually uses
    import options_data
    options_data._PROVIDER = FakeProvider()
    monkeypatch.setattr(options_data, "_PROVIDER", FakeProvider())
    yield
    osmod._scan_cache["data"] = None
    osmod._scan_cache["ts"] = 0.0


def test_scan_ticker_integration():
    res = osmod.scan_ticker("FAKE")
    assert res is not None
    assert res["ticker"] == "FAKE"
    assert res["spot"] == 100.0
    assert res["total_premium"] > 0
    assert res["pc_ratio"] > 0
    # 2-contract cross-section: z-scores are +/-1, so at least one contract
    # must score > 0 (the top one survives the score>0 filter by design)
    assert len(res["contracts"]) >= 1
    c = res["contracts"][0]
    for k in ("expiry", "strike", "type", "vol", "oi", "notional", "dte", "score", "tier"):
        assert k in c
    assert res["catalyst"] == "2099-01-10"


def test_scan_universe_cached_and_force():
    r1 = osmod.scan_universe()
    assert r1["count"] >= 1
    assert isinstance(r1["cached_at"], str)
    # second call served from cache (same object)
    r2 = osmod.scan_universe()
    assert r2["cached_at"] == r1["cached_at"]
    # force rebuilds
    r3 = osmod.scan_universe(force=True)
    assert r3["count"] == r1["count"]


def test_routes_declared():
    assert osmod.ROUTES["/api/screen/v2"] == "handle_screen_v2"
    assert osmod.ROUTES["/api/screen/ticker"] == "handle_screen_ticker"
