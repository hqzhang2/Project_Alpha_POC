"""tests/test_strategy_data.py — NS-DS strategy-data store."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import strategy_data


def test_builds_all_streams():
    s = strategy_data.build_streams()
    assert set(s.keys()) == {"ns7", "at_val", "ns8", "cash"}


def test_ns8_stream_daily_real():
    s = strategy_data.build_streams()
    assert len(s["ns8"]["returns"]) > 1000          # real daily closes
    assert s["ns8"]["label"] == "live"


def test_streams_differentiated():
    s = strategy_data.build_streams()
    streams = [s[k]["returns"] for k in ("ns7", "at_val", "ns8")]
    # at least two of them differ (they must NOT all be the SPY proxy)
    assert len({tuple(x[:100]) for x in streams}) >= 2


def test_momentum_friendly_length():
    import config
    s = strategy_data.build_streams()
    # each stream must exceed the momentum lookback so NS-X can score it
    for k in ("ns7", "at_val", "ns8"):
        assert len(s[k]["returns"]) >= config.MOM_LOOKBACK_DAYS + config.MOM_SKIP_DAYS


def test_get_stream_fail_open():
    assert strategy_data.get_stream("nonexistent") == []


def test_sources_labels():
    src = strategy_data.sources()
    assert "ns8" in src and "ns7" in src and "at_val" in src and "cash" in src
