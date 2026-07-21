#!/usr/bin/env python3
"""
Common Risk Models
Shared risk management utilities.
"""
import warnings
from typing import Optional

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=RuntimeWarning)


def sharpe_ratio(returns: pd.Series, risk_free: float = 0.0) -> float:
    """Annualized Sharpe ratio."""
    excess = returns - risk_free / 252
    return float(np.sqrt(252) * excess.mean() / excess.std().replace(0, np.nan))


def sortino_ratio(returns: pd.Series, risk_free: float = 0.0) -> float:
    """Annualized Sortino ratio (downside deviation)."""
    excess = returns - risk_free / 252
    downside = returns[returns < 0]
    downside_std = downside.std()
    if downside_std == 0:
        return np.nan
    return float(np.sqrt(252) * excess.mean() / downside_std)


def max_drawdown(prices: pd.Series) -> float:
    """Maximum drawdown as a percentage."""
    cummax = prices.cummax()
    drawdown = (prices - cummax) / cummax
    return float(drawdown.min() * 100)


def volatility(returns: pd.Series, annualize: bool = True) -> float:
    """Return volatility (std dev of returns)."""
    vol = returns.std()
    if annualize:
        vol *= np.sqrt(252)
    return float(vol)


def beta(asset_returns: pd.Series, market_returns: pd.Series) -> float:
    """Beta vs market."""
    cov = asset_returns.cov(market_returns)
    var = market_returns.var()
    return float(cov / var) if var != 0 else 0.0


def alpha(asset_returns: pd.Series, market_returns: pd.Series, risk_free: float = 0.0) -> float:
    """Jensen's alpha (annualized)."""
    b = beta(asset_returns, market_returns)
    market_excess = market_returns.mean() * 252 - risk_free
    asset_excess = asset_returns.mean() * 252 - risk_free
    return asset_excess - b * market_excess


def var(returns: pd.Series, confidence: float = 0.95) -> float:
    """Value at Risk (parametric)."""
    from scipy import stats
    mean = returns.mean()
    std = returns.std()
    z = stats.norm.ppf(1 - confidence)
    return float(mean + z * std)


def cvar(returns: pd.Series, confidence: float = 0.95) -> float:
    """Conditional Value at Risk (Expected Shortfall)."""
    var_val = var(returns, confidence)
    tail = returns[returns <= var_val]
    return float(tail.mean()) if len(tail) > 0 else float(var_val)


def risk_parity_weights(cov_matrix: pd.DataFrame) -> pd.Series:
    """Risk parity weights (inverse volatility)."""
    inv_vol = 1 / np.sqrt(np.diag(cov_matrix))
    weights = inv_vol / inv_vol.sum()
    return pd.Series(weights, index=cov_matrix.index)


def equal_weight_returns(returns_dict: dict[str, pd.Series]) -> pd.Series:
    """Combine multiple return series with equal weights."""
    df = pd.DataFrame(returns_dict).dropna()
    return df.mean(axis=1)


# ── Position Sizing ──

def kelly_fraction(win_rate: float, win_loss_ratio: float) -> float:
    """Kelly criterion fraction."""
    if win_loss_ratio <= 0:
        return 0.0
    return max(0.0, win_rate - (1 - win_rate) / win_loss_ratio)


def position_size_kelly(capital: float, win_rate: float, win_loss_ratio: float,
                        max_fraction: float = 0.25) -> float:
    """Position size using Kelly with cap."""
    f = kelly_fraction(win_rate, win_loss_ratio)
    return capital * min(f, max_fraction)


def position_size_vol_target(capital: float, target_vol: float,
                              asset_vol: float, max_leverage: float = 1.0) -> float:
    """Position size to target portfolio volatility."""
    if asset_vol == 0:
        return 0.0
    leverage = target_vol / asset_vol
    leverage = min(leverage, max_leverage)
    return capital * leverage


if __name__ == "__main__":
    # Quick test
    np.random.seed(42)
    returns = pd.Series(np.random.randn(252) * 0.01 + 0.0005)
    prices = (1 + returns).cumprod() * 100

    print(f"Sharpe: {sharpe_ratio(returns):.2f}")
    print(f"Sortino: {sortino_ratio(returns):.2f}")
    print(f"Max DD: {max_drawdown(prices):.2f}%")
    print(f"Vol: {volatility(returns):.4f}")
    print(f"VaR(95%): {var(returns):.4f}")
    print(f"CVaR(95%): {cvar(returns):.4f}")