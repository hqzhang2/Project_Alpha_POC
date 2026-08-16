"""tests/test_allocator.py — NS-X allocator + walk-forward HARD GATE."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import allocator
import nsx_walkforward as wf
import registry


def test_allocator_builds_document():
    doc = allocator.build_allocation("2026-08-16")
    assert doc["as_of"] == "2026-08-16"
    assert "strategies" in doc and "momentum_scores" in doc
    assert abs(doc["weights_sum"] - 1.0) < 1e-6
    assert all(v >= 0 for v in doc["strategies"].values())
    assert "cash" in doc["strategies"]


def test_registry_has_cash_and_enabled_universe():
    reg = registry.build_registry()
    ids = {s.id for s in reg}
    assert "cash" in ids
    enabled = {s.id for s in registry.enabled_registry()}
    assert enabled == {"ns7", "at_val", "ns8", "cash"}   # ns1/ns3 disabled (§4.4)


def test_walkforward_mechanics_valid():
    """Mechanics gate: rotation finds the winning strategy on differentiated synth."""
    res = wf.run_validation(seed=42)
    assert res["gate"]["mechanics_validated"] is True
    assert res["rotation_sharpe"] > res["static_sharpe"]


def test_walkforward_evidence_status_honest():
    """Evidence status must be 'evidence_pending' (real streams not wired)."""
    res = wf.run_validation(seed=42)
    assert res["gate"]["evidence_status"] == "evidence_pending"


def test_walkforward_turnover_reported():
    """Turnover gate is measured and reported."""
    res = wf.run_validation(seed=42)
    assert "annual_turnover" in res
    assert "turnover_ok" in res["gate"]
    assert res["gate"]["turnover_ok"] is True   # synth streams are low-turnover


def test_walkforward_json_serializable():
    res = wf.run_validation(seed=1)
    json.dumps(res)   # must not raise (no numpy.bool_/float64)


def test_allocator_writes_file(tmp_path):
    path = tmp_path / "alloc.json"
    doc = allocator.build_allocation("2026-08-16")
    allocator.write_allocation(doc, path)
    assert path.exists()
    loaded = json.loads(path.read_text())
    assert loaded["as_of"] == "2026-08-16"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
