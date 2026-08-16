"""tests/test_signals.py — Unit Tests for NS-8 Signal Generation."""
import pytest
from signals import compute_sma, generate_signals, compute_weights, build_signal_document


def test_compute_sma_basic():
    """SMA of 1..200 with window=200 should be 100.5."""
    closes = [float(i) for i in range(1, 201)]
    sma = compute_sma(closes, 200)
    assert sma == 100.5


def test_compute_sma_insufficient_history():
    """SMA returns None when history < window."""
    closes = [100.0] * 100
    sma = compute_sma(closes, 200)
    assert sma is None


def test_compute_sma_exact_window():
    """SMA works with exactly window length."""
    closes = [100.0] * 200
    sma = compute_sma(closes, 200)
    assert sma == 100.0


def test_generate_signals_trending_up():
    """Uptrending price → signal 1."""
    prices = {"SPY": [float(i) for i in range(100, 300)]}  # 200 days, trending up
    signals = generate_signals(prices)
    assert signals["SPY"] == 1


def test_generate_signals_trending_down():
    """Downtrending price → signal 0."""
    prices = {"EFA": [float(i) for i in range(300, 100, -1)]}  # 200 days, trending down
    signals = generate_signals(prices)
    assert signals["EFA"] == 0


def test_generate_signals_flat():
    """Flat price at SMA boundary → signal 0 (close <= SMA)."""
    prices = {"IEF": [100.0] * 200}
    signals = generate_signals(prices)
    assert signals["IEF"] == 0


def test_generate_signals_insufficient_history():
    """Insufficient history → signal 0 (cash)."""
    prices = {"VNQ": [100.0] * 100}  # only 100 days
    signals = generate_signals(prices)
    assert signals["VNQ"] == 0


def test_compute_weights_all_on():
    """All signals 1 → each 20%, SHV 0%."""
    signals = {"SPY": 1, "EFA": 1, "IEF": 1, "VNQ": 1, "DBC": 1}
    weights = compute_weights(signals)
    assert weights["SPY"] == 0.20
    assert weights["EFA"] == 0.20
    assert weights["IEF"] == 0.20
    assert weights["VNQ"] == 0.20
    assert weights["DBC"] == 0.20
    assert weights["SHV"] == 0.0
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_compute_weights_all_off():
    """All signals 0 → all 0%, SHV 100%."""
    signals = {"SPY": 0, "EFA": 0, "IEF": 0, "VNQ": 0, "DBC": 0}
    weights = compute_weights(signals)
    assert weights["SPY"] == 0.0
    assert weights["SHV"] == 1.0
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_compute_weights_mixed():
    """Mixed signals → correct allocation."""
    signals = {"SPY": 1, "EFA": 0, "IEF": 1, "VNQ": 0, "DBC": 1}
    weights = compute_weights(signals)
    assert weights["SPY"] == 0.20
    assert weights["EFA"] == 0.0
    assert weights["IEF"] == 0.20
    assert weights["VNQ"] == 0.0
    assert weights["DBC"] == 0.20
    assert abs(weights["SHV"] - 0.40) < 1e-9  # 3 off = 60% to SHV
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_build_signal_document():
    """Signal document has required fields."""
    doc = build_signal_document(
        as_of="2026-07-31",
        signals={"SPY": 1, "EFA": 0},
        weights={"SPY": 0.20, "EFA": 0.0, "SHV": 0.80},
        version=1
    )
    assert doc["as_of"] == "2026-07-31"
    assert doc["signals"]["SPY"] == 1
    assert doc["weights"]["SPY"] == 0.20
    assert doc["version"] == 1
    assert "generated_at" in doc


if __name__ == "__main__":
    pytest.main([__file__, "-v"])