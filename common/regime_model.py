"""
NS-5 Regime Model — shared by NS-5 governance engine + Alpha Terminal.

Phase 4: Regime awareness axis.
One model → two consumers. Reads FRED macro data; no dependency on NS-1..4.

FRONTIER (methodology, this module):
  - REGIME_THETA: validated thresholds (Phase 0 walk-forward)
  - RegimeClassifier: 5-step rule-based now-cast, t-1 data only
  - filter_regime_returns(): returns-filtering utility for NS-5 frontier builder

JUNIOR (cheap model, not here):
  - FRED/Yahoo fetch seam (TTL cache, fail-open)
  - SQLite regime history store
  - Tests (synthetic + offline)

Author: Frontier LLM (deepseek-v4-pro), 2026-08-09
Module: Project_Alpha_POC/common/regime_model.py
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# ── Validated Θ (Phase 0 walk-forward, Hong-approved 2026-08-09) ────────
# CPI line: Federal Reserve target (2.0%). Calibration confirmed 60/40
# split over 2010-2026 — honest, not cosmetic. PMI excluded by Hong.

REGIME_THETA: dict = {
    "gdp_growth_threshold": 2.0,           # 2% QoQ annualized
    "unemployment_rising_threshold": 0.3,  # pp Δ over 3 months
    "inflation_target_cpi": 2.0,           # CPI 2% — Federal Reserve target
    "cpi_trend_months": 3,
    "yield_curve_inversion_bp": -10,       # 2s10s < -10 bp
    "baa_aaa_spread_stress": 200,          # bp
    "nfci_tight_threshold": 0.0,
    "wti_stagflation_threshold": 100.0,    # $/bbl
    "usd_squeeze_pct_monthly": 5.0,        # %
    "vix_crisis_threshold": 28.0,
    "stock_bond_corr_window": 60,
    "corr_inflation_threshold": 0.1,
    "regime_history_days": 750,
    "min_regime_days": 60,
}


@dataclass
class RegimeResult:
    """Single-day classification output."""
    regime: str          # R1 | R2 | R3 | R4
    confidence: float    # 0.0–1.0
    flags: list[str]     # e.g. ['late cycle', 'credit stress']
    growth_above: bool
    inflation_above: bool
    diagnostics: dict = field(default_factory=dict)


class RegimeClassifier:
    """5-step rule-based macro regime now-cast.

    Step 1 — PRIMARY: GROWTH (GDP ≥ 2% OR UNRATE stable/falling)
                      × INFLATION (CPI ≥ 2% AND trend flat/rising)
    Step 2 — MONETARY OVERLAY (modifies confidence)
    Step 3 — CREDIT CONFIRMATION (BAA−AAA spread, NFCI)
    Step 4 — EXTERNAL (USD squeeze, WTI stagflation risk)
    Step 5 — MARKET (VIX, stock-bond correlation — confirmation only)

    All data consumed at t-1 — no lookahead. Fail-open: missing fields
    → omit from classification, don't crash.

    Usage:
        c = RegimeClassifier()
        result = c.classify({
            'GDP_QOQ_ANN': 2.4, 'CPI_YOY': 3.2, 'UNRATE_3M_CHG': -0.1,
            '2S10S': 0.44, 'BAA_AAA': 0.44, 'NFCI': -0.15,
            'DCOILWTICO': 75, 'USD_MOM_PCT': 0.3,
            'VIX': 15.1, 'STOCK_BOND_CORR': -0.25, 'FEDFUNDS': 5.25,
        })
        # RegimeResult(regime='R2', confidence=0.90, ...)
    """

    def __init__(self, theta: dict | None = None):
        self.t = theta or REGIME_THETA

    # ── Step 1: primary classification ──────────────────────────────
    def _growth_above(self, data: dict) -> bool:
        """GDP ≥ threshold OR unemployment stable/falling (no PMI)."""
        gdp = data.get("GDP_QOQ_ANN")
        if gdp is not None and not np.isnan(gdp):
            if gdp >= self.t["gdp_growth_threshold"]:
                return True
        unrate_chg = data.get("UNRATE_3M_CHG")
        if unrate_chg is not None and not np.isnan(unrate_chg):
            # "Stable/falling" = not rising more than threshold
            if unrate_chg <= self.t["unemployment_rising_threshold"]:
                return True
        return False

    def _inflation_above(self, data: dict) -> bool:
        """CPI ≥ threshold AND trend flat/rising."""
        cpi = data.get("CPI_YOY")
        if cpi is None or np.isnan(cpi):
            return False
        if cpi < self.t["inflation_target_cpi"]:
            return False
        trend = data.get("CPI_TREND_3M", 0)
        if trend is None or np.isnan(trend):
            trend = 0
        # "Flat/rising" = not falling more than 0.05pp in 3 months
        return trend >= -0.05

    def _primary(self, ga: bool, ia: bool) -> tuple[str, float]:
        if ga and not ia:
            return "R1", 1.0
        elif ga and ia:
            return "R2", 0.9
        elif not ga and not ia:
            return "R3", 0.9
        else:
            return "R4", 0.8

    # ── Step 2: monetary overlay ────────────────────────────────────
    def _monetary_overlay(self, regime: str, confidence: float,
                          data: dict) -> tuple[float, list[str]]:
        flags = []
        curve = data.get("2S10S")
        if curve is None or np.isnan(curve):
            return confidence, flags
        curve_bp = curve * 100  # decimal → bp

        if regime == "R1" and curve_bp < self.t["yield_curve_inversion_bp"]:
            flags.append("late cycle")
            confidence = min(confidence, 0.7)
        if regime == "R3" and curve_bp > 0:
            # De-inversion check: was curve inverted recently?
            curve_60d_ago = data.get("2S10S_60D_AGO")
            if curve_60d_ago is not None and not np.isnan(curve_60d_ago):
                if curve_60d_ago * 100 < self.t["yield_curve_inversion_bp"]:
                    flags.append("de-inverting")
                    confidence = min(confidence, 0.95)
        return confidence, flags

    # ── Step 3: credit confirmation ─────────────────────────────────
    def _credit_check(self, regime: str, data: dict) -> list[str]:
        flags = []
        baa_aaa = data.get("BAA_AAA")
        if baa_aaa is not None and not np.isnan(baa_aaa):
            if baa_aaa * 100 > self.t["baa_aaa_spread_stress"]:
                flags.append("credit stress")
        nfci = data.get("NFCI")
        if nfci is not None and not np.isnan(nfci):
            if nfci > self.t["nfci_tight_threshold"]:
                tag = "NFCI tight (confirm)" if regime in ("R3", "R4") \
                      else "NFCI tight (contradict)"
                flags.append(tag)
        return flags

    # ── Step 4: external ────────────────────────────────────────────
    def _external_check(self, regime: str, data: dict) -> list[str]:
        flags = []
        wti = data.get("DCOILWTICO")
        if wti is not None and not np.isnan(wti):
            if wti > self.t["wti_stagflation_threshold"]:
                if regime in ("R2", "R4"):
                    flags.append("stagflation risk (WTI)")
        usd = data.get("USD_MOM_PCT")
        if usd is not None and not np.isnan(usd):
            if abs(usd) > self.t["usd_squeeze_pct_monthly"]:
                flags.append("USD squeeze")
        return flags

    # ── Step 5: market confirmation (never reclassifies) ────────────
    def _market_check(self, regime: str, data: dict) -> list[str]:
        flags = []
        vix = data.get("VIX")
        if vix is not None and not np.isnan(vix):
            if vix > self.t["vix_crisis_threshold"]:
                flags.append("acute (VIX)")
        corr = data.get("STOCK_BOND_CORR")
        if corr is not None and not np.isnan(corr):
            if regime in ("R1", "R3") and corr > 0.1:
                flags.append("anomalous corr (+in R1/R3)")
            elif regime in ("R2", "R4") and corr < -0.1:
                flags.append("anomalous corr (-in R2/R4)")
        return flags

    # ── Public API ──────────────────────────────────────────────────
    def classify(self, data: dict) -> RegimeResult:
        """Classify a single day's macro data. t-1 data only.

        Args:
            data: dict with keys {GDP_QOQ_ANN, CPI_YOY, CPI_TREND_3M,
                  UNRATE_3M_CHG, 2S10S, 2S10S_60D_AGO, BAA_AAA, NFCI,
                  DCOILWTICO, USD_MOM_PCT, VIX, STOCK_BOND_CORR}
                  All optional — missing → omitted from that step.

        Returns:
            RegimeResult(regime, confidence, flags, growth_above,
                         inflation_above, diagnostics)
        """
        # Step 1
        ga = self._growth_above(data)
        ia = self._inflation_above(data)
        regime, confidence = self._primary(ga, ia)
        all_flags: list[str] = []

        # Step 2
        confidence, mf = self._monetary_overlay(regime, confidence, data)
        all_flags.extend(mf)

        # Step 3
        all_flags.extend(self._credit_check(regime, data))

        # Step 4
        all_flags.extend(self._external_check(regime, data))

        # Step 5
        all_flags.extend(self._market_check(regime, data))

        return RegimeResult(
            regime=regime,
            confidence=round(confidence, 2),
            flags=all_flags,
            growth_above=ga,
            inflation_above=ia,
            diagnostics={
                "cpi_yoy": data.get("CPI_YOY"),
                "gdp_qoq": data.get("GDP_QOQ_ANN"),
                "unrate_3m_chg": data.get("UNRATE_3M_CHG"),
                "curve_bp": round(data["2S10S"] * 100, 1)
                    if data.get("2S10S") is not None
                    and not np.isnan(data["2S10S"]) else None,
            },
        )

    def classify_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Batch-classify a daily panel. Adds columns: regime, confidence,
        flags, growth_above, inflation_above.

        Expects each row to have the columns this classifier reads.
        The caller is responsible for preparing derived series
        (CPI_YOY, 2S10S, etc.) — this method only runs classification.
        """
        results = []
        for _, row in df.iterrows():
            data = row.to_dict()
            r = self.classify(data)
            results.append({
                "regime": r.regime,
                "confidence": r.confidence,
                "flags": ",".join(r.flags) if r.flags else "",
                "growth_above": r.growth_above,
                "inflation_above": r.inflation_above,
            })
        result_df = pd.DataFrame(results, index=df.index)
        return pd.concat([df, result_df], axis=1)


# ── Frontier-builder utility ───────────────────────────────────────────
def filter_regime_returns(
    returns: pd.DataFrame,
    regime_history: pd.DataFrame,
    current_regime: str | None = None,
    min_days: int = 60,
) -> pd.DataFrame:
    """Filter daily returns to the current regime's trading days.

    Used by NS-5 to compute regime-conditional frontiers:
        current_returns = filter_regime_returns(returns, history)
        frontier = compute_frontier(closes_from(current_returns), tickers)

    Args:
        returns:        DataFrame of daily returns (index=date, cols=tickers)
        regime_history: DataFrame with 'date' index + 'regime' column
        current_regime: if None, uses the most recent regime in history
        min_days:       minimum trading days required (fail-open)

    Returns:
        Filtered returns DataFrame, or empty DataFrame if insufficient data.
    """
    if current_regime is None:
        if regime_history.empty:
            return pd.DataFrame()
        current_regime = regime_history["regime"].iloc[-1]

    regime_dates = regime_history[
        regime_history["regime"] == current_regime
    ].index

    filtered = returns[returns.index.isin(regime_dates)]
    if len(filtered) < min_days:
        return pd.DataFrame()

    return filtered.dropna(how="all")


def current_regime_from_history(
    regime_history: pd.DataFrame,
) -> str | None:
    """Return the most recent regime label from history, or None if empty."""
    if regime_history.empty:
        return None
    return str(regime_history["regime"].iloc[-1])
