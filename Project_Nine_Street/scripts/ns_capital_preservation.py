#!/usr/bin/env python3
"""
NS-1 Capital Preservation Backtest Engine
Multi-factor ETF rotation with VIX smile, crisis safe-haven rotation, BIL cash eq.
Profiles: capital_preservation (defensive), aggressive (higher exposure).
"""

import os, sys, json, warnings
import numpy as np
import pandas as pd
import yfinance as yf
from ta.trend import ADXIndicator, SMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
from ta.volume import OnBalanceVolumeIndicator
from datetime import datetime

warnings.filterwarnings("ignore")

# ── Constants ──
UNIVERSE = ["SPY", "QQQ", "XLK", "XLE", "XLV", "XLF", "XLI", "XLB",
            "XLY", "XLP", "XLU", "XLRE", "XLC",
            "EFA", "EEM", "AGG", "TLT", "IEI", "SHY", "BIL", "DBC", "GLD"]

VIX_SMILE = [(12, 0.95), (15, 1.00), (20, 0.90), (25, 0.80),
             (30, 0.65), (35, 0.50), (40, 0.55), (50, 0.70), (60, 0.85)]

CRISIS_VIX_IN = 28
CRISIS_VIX_OUT = 23
CRISIS_SAFE = {'SHY', 'BIL', 'AGG', 'TLT', 'IEI', 'GLD'}

FACTOR_WEIGHTS = {'risk_adj_momentum': 0.20, 'ts_momentum_blend': 0.20,
                  'rsi_score': 0.10, 'bb_score': 0.10, 'carry_signal': 0.15,
                  'spx_rel_strength': 0.10, 'adx_norm': 0.10, 'vol_ratio_inv': 0.05}

TOP_N = 3
MAX_SINGLE_ASSET = 0.50
MIN_CASH_BASE = 0.10
TRAILING_STOP_ATR_MULT = 2.0
REBALANCE_FREQ = 'MS'
START_DATE = "2010-01-01"
INITIAL_CAPITAL = 500_000

# ── Profiles ──
PROFILES = {
    'capital_preservation': {'vix_smile': VIX_SMILE, 'crisis_in': 28, 'crisis_out': 23, 'cash_floor': True},
    'aggressive': {
        'vix_smile': [(12, 1.00), (20, 1.00), (25, 0.95), (28, 0.85), (32, 0.70), (38, 0.50), (45, 0.60), (55, 0.80)],
        'crisis_in': 38, 'crisis_out': 32, 'cash_floor': False, 'top_n': 5, 'max_single': 0.40, 'trailing_stop': 2.5,
    },
}


def vix_exposure_cap(vix_level):
    if np.isnan(vix_level): return VIX_SMILE[len(VIX_SMILE)//2][1]
    levels = [p[0] for p in VIX_SMILE]; caps = [p[1] for p in VIX_SMILE]
    if vix_level <= levels[0]: return caps[0]
    if vix_level >= levels[-1]: return caps[-1]
    for i in range(len(levels)-1):
        if levels[i] <= vix_level < levels[i+1]:
            f = (vix_level - levels[i]) / (levels[i+1] - levels[i])
            return caps[i] + f * (caps[i+1] - caps[i])
    return VIX_SMILE[len(VIX_SMILE)//2][1]


# ═══════════════════════════════════════════════════
# Feature Engineer
# ═══════════════════════════════════════════════════

class FeatureEngineer:
    def __init__(self, tickers, start_date, end_date=None):
        self.tickers = tickers; self.start_date = start_date; self.end_date = end_date
        self.prices = None; self.returns = None; self.features = {}
        self.vix_data = None; self.macro_data = None; self.spy_vol = None
        self.vix_backwardation = None; self.credit_stress = None

    def fetch_all(self):
        tickers = list(self.tickers)
        if 'SPY' not in tickers: tickers.append('SPY')
        raw = yf.download(tickers, start=self.start_date, end=self.end_date, progress=False, auto_adjust=True)
        if isinstance(raw.columns, pd.MultiIndex):
            self.prices = raw['Close'].copy()
            self.highs = raw['High'].copy()
            self.lows = raw['Low'].copy()
            self.volumes = raw['Volume'].copy()
        keep = [c for c in self.tickers if c in self.prices.columns]
        self.prices = self.prices[keep]
        self.highs = self.highs[[c for c in keep if c in self.highs.columns]]
        self.lows = self.lows[[c for c in keep if c in self.lows.columns]]
        self.volumes = self.volumes[[c for c in keep if c in self.volumes.columns]]
        self.returns = self.prices.pct_change().fillna(0)
        if 'SPY' in self.prices.columns:
            self.spy_vol = self.returns['SPY'].rolling(20).std() * np.sqrt(252)
        vix = yf.download('^VIX', start=self.start_date, end=self.end_date, progress=False, auto_adjust=True)
        if isinstance(vix, pd.DataFrame):
            self.vix_data = vix['Close'].iloc[:,0] if vix['Close'].shape[1] > 0 else vix['Close']
        else:
            self.vix_data = vix
        if isinstance(self.vix_data, pd.DataFrame): self.vix_data = self.vix_data.iloc[:,0]
        self.vix_data.name = 'VIX'
        macro = yf.download(['HYG', 'TLT'], start=self.start_date, end=self.end_date, progress=False, auto_adjust=True)
        self.macro_data = macro['Close'].copy() if isinstance(macro.columns, pd.MultiIndex) else pd.DataFrame()
        try:
            dxy = yf.download('DX-Y.NYB', start=self.start_date, end=self.end_date, progress=False, auto_adjust=True)
            if not dxy.empty:
                dxy_close = dxy['Close'].iloc[:,0] if isinstance(dxy.columns, pd.MultiIndex) else dxy['Close']
                self.macro_data['DXY'] = dxy_close
        except: pass
        # Compute macro features
        if self.macro_data is not None and not self.macro_data.empty:
            if 'HYG' in self.macro_data and 'TLT' in self.macro_data:
                cr = self.macro_data['HYG'] / self.macro_data['TLT']
                self.credit_stress = -cr.pct_change(20)
                self.credit_stress.name = 'credit_stress'
        self.vix_backwardation = (self.vix_data - self.vix_data.rolling(20).mean()) / self.vix_data.rolling(20).mean().replace(0, np.nan)
        self.vix_backwardation.name = 'vix_backwardation'
        return self

    def compute_features(self):
        for ticker in self.prices.columns:
            close = self.prices[ticker].dropna()
            high = self.highs.get(ticker, close); low = self.lows.get(ticker, close)
            vol = self.volumes.get(ticker, pd.Series(1, index=close.index))
            df = pd.DataFrame(index=close.index)
            for col, val in [('close', close), ('high', high), ('low', low), ('volume', vol)]:
                df[col] = val
            try: df['ADX'] = ADXIndicator(high=high, low=low, close=close, window=14).adx()
            except: df['ADX'] = 25
            try: df['RSI'] = RSIIndicator(close=close, window=14).rsi()
            except: df['RSI'] = 50
            try:
                bb = BollingerBands(close=close, window=20, window_dev=2)
                df['BB_width'] = bb.bollinger_wband()
                df['BB_position'] = (close - bb.bollinger_lband()) / (bb.bollinger_hband() - bb.bollinger_lband() + 1e-10)
            except: df['BB_width'] = 0.02; df['BB_position'] = 0.5
            df['vol_ratio'] = vol / (vol.rolling(20).mean() + 1)
            try:
                obv = OnBalanceVolumeIndicator(close=close, volume=vol)
                df['OBV_slope'] = obv.on_balance_volume().diff(5)
            except: df['OBV_slope'] = 0
            df['SMA_50'] = SMAIndicator(close=close, window=50).sma_indicator()
            df['SMA_200'] = SMAIndicator(close=close, window=200).sma_indicator() if len(close) >= 200 else close.expanding().mean()
            df['ret_1d'] = close.pct_change()
            df['mom_21'] = close.pct_change(21)
            df['mom_63'] = close.pct_change(63)
            df['mom_126'] = close.pct_change(126)
            df['realized_vol'] = df['ret_1d'].rolling(20).std() * np.sqrt(252)
            if self.macro_data is not None:
                df = df.join(self.macro_data, how='left').ffill()
            if self.vix_backwardation is not None:
                df['vix_backwardation'] = self.vix_backwardation
            if self.credit_stress is not None:
                df['credit_stress'] = self.credit_stress
            self.features[ticker] = df
        return self


# ═══════════════════════════════════════════════════
# Composite Scoring
# ═══════════════════════════════════════════════════

def compute_composite_scores(features_dict, prices, returns, spy_vol, date_idx):
    scores = pd.DataFrame(0.0, index=date_idx, columns=list(features_dict.keys()))
    for ticker, feat in features_dict.items():
        if feat is None or feat.empty: continue
        a = feat.reindex(date_idx, method='ffill')
        s63 = (a['ret_1d'].rolling(63).mean() * 252 - 0.04) / (a['ret_1d'].rolling(63).std() * np.sqrt(252) + 0.01)
        s63 = s63.clip(-3, 3)
        mom = (0.5 * a['mom_21'].fillna(0) + 0.3 * a['mom_63'].fillna(0) + 0.2 * a['mom_126'].fillna(0)).clip(-0.5,0.5)*2
        rsi_s = (-(a['RSI'].fillna(50) - 50) / 30).clip(-1, 1)
        bb_s = (-(a['BB_position'].fillna(0.5) - 0.5) * 2).clip(-1, 1)
        if ticker in ('AGG','TLT','IEI','SHY','BIL'):
            carry = -mom * 0.3
        elif ticker in ('DBC','GLD'):
            carry = a['mom_21'] * 2
        else:
            carry = pd.Series(0, index=date_idx)
        carry = carry.clip(-1,1)
        if 'SPY' in prices.columns and ticker != 'SPY':
            spy_63 = returns['SPY'].reindex(date_idx).rolling(63).sum()
            tk_63 = returns[ticker].reindex(date_idx).rolling(63).sum()
            rel_str = (tk_63 - spy_63).clip(-0.5, 0.5) * 2
        else:
            rel_str = pd.Series(0, index=date_idx)
        vratio = a['vol_ratio'].fillna(1)
        vri = ((1/(vratio+0.5) - 0.5) * 2).clip(-1, 1)

        raw = (FACTOR_WEIGHTS['risk_adj_momentum'] * s63.fillna(0) +
               FACTOR_WEIGHTS['ts_momentum_blend'] * mom.fillna(0) +
               FACTOR_WEIGHTS['rsi_score'] * rsi_s.fillna(0) +
               FACTOR_WEIGHTS['bb_score'] * bb_s.fillna(0) +
               FACTOR_WEIGHTS['carry_signal'] * carry.fillna(0) +
               FACTOR_WEIGHTS['spx_rel_strength'] * rel_str.fillna(0) +
               FACTOR_WEIGHTS['vol_ratio_inv'] * vri.fillna(0))
        scores[ticker] = raw

    # ADX cross-sectional normalization
    adx_df = pd.DataFrame({t: features_dict[t]['ADX'].reindex(date_idx, method='ffill').fillna(20)
                           for t in features_dict if features_dict[t] is not None})
    if len(adx_df.columns) > 0:
        adx_z = adx_df.sub(adx_df.mean(1), axis=0).div(adx_df.std(1).replace(0,1), axis=0).clip(-2,2)/2
        scores += FACTOR_WEIGHTS['adx_norm'] * adx_z

    # Filters
    for ticker, feat in features_dict.items():
        if feat is None: continue
        rsi = feat['RSI'].reindex(date_idx, method='ffill').fillna(50)
        adx = feat['ADX'].reindex(date_idx, method='ffill').fillna(20)
        scores.loc[adx < 20, ticker] *= 0.5
        scores.loc[rsi > 75, ticker] *= 0.5
        if spy_vol is not None:
            vol = feat['realized_vol'].reindex(date_idx, method='ffill').fillna(0.15)
            spy_v = spy_vol.reindex(date_idx).fillna(0.15)
            scores.loc[vol > 2 * spy_v, ticker] *= 0.3
    return scores


# ═══════════════════════════════════════════════════
# Portfolio Simulator
# ═══════════════════════════════════════════════════

def simulate_portfolio(prices, returns, scores, vix_data, vix_backwardation, credit_stress, features_dict,
                       profile=None):
    p = PROFILES.get(profile, PROFILES['capital_preservation']) if profile else PROFILES['capital_preservation']
    smile = p.get('vix_smile', VIX_SMILE); crisis_in = p.get('crisis_in', CRISIS_VIX_IN)
    crisis_out = p.get('crisis_out', CRISIS_VIX_OUT); cash_floor = p.get('cash_floor', True)
    top_n = p.get('top_n', TOP_N); max_single = p.get('max_single', MAX_SINGLE_ASSET)
    stop_mult = p.get('trailing_stop', TRAILING_STOP_ATR_MULT)

    def _vix_cap(vl):
        if np.isnan(vl): return smile[len(smile)//2][1]
        lv = [s[0] for s in smile]; cv = [s[1] for s in smile]
        if vl <= lv[0]: return cv[0]
        if vl >= lv[-1]: return cv[-1]
        for i in range(len(lv)-1):
            if lv[i] <= vl < lv[i+1]:
                return cv[i] + (vl-lv[i])/(lv[i+1]-lv[i]) * (cv[i+1]-cv[i])
        return smile[len(smile)//2][1]

    daily_idx = prices.index; tickers = list(prices.columns)
    nav = INITIAL_CAPITAL; cash = INITIAL_CAPITAL
    positions = {}; nav_history = pd.Series(INITIAL_CAPITAL, index=daily_idx, dtype=float)
    weight_history = pd.DataFrame(0.0, index=daily_idx, columns=tickers)
    trailing_stops = {}; peak_prices = {}; trades = []
    crisis_mode = False

    for date in daily_idx:
        cp = {}
        for t in tickers:
            p = prices[t].get(date, np.nan)
            if not np.isnan(p) and p > 0: cp[t] = p

        # Trailing stops
        for ticker in list(positions.keys()):
            price = cp.get(ticker, np.nan)
            if np.isnan(price) or price <= 0: continue
            feat = features_dict.get(ticker)
            atr = max(feat.loc[date, 'realized_vol'] / np.sqrt(252) * price if feat is not None and date in feat.index and 'realized_vol' in feat.columns else price*0.02, price*0.005)
            if ticker not in peak_prices or price > peak_prices[ticker]:
                peak_prices[ticker] = price
                trailing_stops[ticker] = price - stop_mult * atr
            if price < trailing_stops[ticker] and positions[ticker] > 0:
                cash += positions[ticker] * price
                trades.append({'date':date, 'ticker':ticker, 'action':'STOP', 'price':price, 'shares':positions[ticker], 'reason':'trailing_stop'})
                positions[ticker] = 0; del peak_prices[ticker]; del trailing_stops[ticker]

        # Rebalance
        if date in scores.index:
            vix_level = float(vix_data.get(date, 20))
            if np.isnan(vix_level): vix_level = 20
            max_deployed = _vix_cap(vix_level)
            if vix_level >= crisis_in: crisis_mode = True
            elif vix_level <= crisis_out: crisis_mode = False
            if cash_floor:
                min_c = 0.35 if vix_level > 35 else (0.25 if vix_level > 30 else (0.15 if vix_level > 25 else 0.05))
                max_deployed = min(max_deployed, 1.0 - min_c)

            today_scores = scores.loc[date].copy()
            tradeable = [t for t in tickers if t in cp and not np.isnan(today_scores.get(t, np.nan))]
            if not tradeable: continue
            today_scores = today_scores[tradeable].sort_values(ascending=False)
            top_n_tickers = today_scores.head(top_n).index.tolist()
            if crisis_mode:
                safe = today_scores[today_scores.index.isin(CRISIS_SAFE)]
                if not safe.empty: top_n_tickers = safe.head(top_n).index.tolist()

            vols = {t: max(features_dict[t].loc[date, 'realized_vol'] if features_dict.get(t) is not None and date in features_dict[t].index and 'realized_vol' in features_dict[t].columns else 0.20, 0.05) for t in top_n_tickers}
            rw = {t: 1.0/vols[t] for t in top_n_tickers}
            tw = sum(rw.values())
            if tw == 0: continue
            rw = {t: w/tw for t,w in rw.items()}
            for t in top_n_tickers: rw[t] = min(rw[t], max_single)
            tw = sum(rw.values()); scale = max_deployed / tw
            target_weights = {t: w*scale for t,w in rw.items()}

            # BIL as cash eq
            if 'BIL' in today_scores.index:
                bil_w = 1.0 - sum(target_weights.values())
                if bil_w > 0.01:
                    target_weights['BIL'] = bil_w
                    if 'BIL' not in top_n_tickers: top_n_tickers.append('BIL')

            # Compute NAV
            eq_val = sum(positions.get(t,0) * cp.get(t,np.nan) for t in tickers if not np.isnan(cp.get(t,np.nan)) and cp.get(t,np.nan)>0)
            nav = cash + eq_val

            # Execute trades
            for ticker in top_n_tickers:
                td = nav * target_weights.get(ticker, 0)
                if np.isnan(td) or td <= 0: continue
                price = cp.get(ticker, np.nan)
                if np.isnan(price) or price <= 0: continue
                ts = int(td / price); delta = ts - positions.get(ticker, 0)
                if delta > 0 and delta * price <= cash:
                    cash -= delta * price
                    positions[ticker] = ts
                    trades.append({'date':date, 'ticker':ticker, 'action':'BUY', 'price':price, 'shares':delta, 'reason':f'score={today_scores.get(ticker,0):.3f}'})
                    atr = max(features_dict[ticker].loc[date, 'realized_vol'] / np.sqrt(252) * price if features_dict.get(ticker) is not None and date in features_dict[ticker].index and 'realized_vol' in features_dict[ticker].columns else price*0.02, price*0.005)
                    peak_prices[ticker] = price; trailing_stops[ticker] = price - stop_mult * atr
                elif delta < 0:
                    cash += -delta * price
                    positions[ticker] = ts
                    trades.append({'date':date, 'ticker':ticker, 'action':'SELL', 'price':price, 'shares':-delta, 'reason':'rebalance'})
                    if ts == 0 and ticker in peak_prices: del peak_prices[ticker]; del trailing_stops[ticker]

            # Sell rotated out
            for ticker in list(positions.keys()):
                if ticker not in top_n_tickers and positions.get(ticker,0) > 0:
                    price = cp.get(ticker, np.nan)
                    if not np.isnan(price) and price > 0:
                        cash += positions[ticker] * price
                        trades.append({'date':date, 'ticker':ticker, 'action':'SELL', 'price':price, 'shares':positions[ticker], 'reason':'rotated_out'})
                        positions[ticker] = 0
                        if ticker in peak_prices: del peak_prices[ticker]; del trailing_stops[ticker]

        # Mark to market
        eq_val = sum(positions[t] * cp.get(t,np.nan) for t in positions if not np.isnan(cp.get(t,np.nan)) and cp.get(t,np.nan)>0)
        nav_history[date] = cash + eq_val
        total_eq = max(cash + eq_val, 1)
        for t in tickers:
            p = cp.get(t, np.nan)
            if not np.isnan(p) and positions.get(t,0) > 0:
                weight_history.loc[date, t] = (positions[t] * p) / total_eq

    return nav_history, weight_history, trades


# ═══════════════════════════════════════════════════
# Metrics
# ═══════════════════════════════════════════════════

def compute_metrics(nav_series, benchmark_series=None, vix_series=None, risk_free=0.04):
    returns = nav_series.pct_change().fillna(0)
    days = (nav_series.index[-1] - nav_series.index[0]).days
    years = days / 365.25
    cagr = ((nav_series.iloc[-1] / nav_series.iloc[0]) ** (1/years) - 1) * 100 if years > 0 else 0
    running_max = nav_series.cummax()
    max_dd = ((nav_series - running_max) / running_max).min() * 100
    excess = returns - risk_free/252
    sharpe = np.sqrt(252) * excess.mean() / returns.std() if returns.std() > 0 else 0
    downside = returns[returns < 0]
    sortino = np.sqrt(252) * excess.mean() / downside.std() if len(downside) > 0 and downside.std() > 0 else 0
    win_rate = (returns > 0).mean() * 100
    monthly = nav_series.resample('ME').apply(lambda x: x.iloc[-1]/x.iloc[0]-1)
    pos_months = (monthly > 0).mean() * 100
    worst_m = monthly.min() * 100
    best_m = monthly.max() * 100

    m = {'total_return_pct': round((nav_series.iloc[-1]/nav_series.iloc[0]-1)*100,2),
         'cagr_pct': round(cagr,2), 'max_drawdown_pct': round(max_dd,2),
         'sharpe_ratio': round(sharpe,2), 'sortino_ratio': round(sortino,2),
         'win_rate_pct': round(win_rate,2), 'pct_positive_months': round(pos_months,2),
         'worst_month_pct': round(worst_m,2), 'best_month_pct': round(best_m,2), 'years': round(years,1)}

    if vix_series is not None:
        bins = {'VIX < 15': (0,15), 'VIX 15-20': (15,20), 'VIX 20-25': (20,25),
                'VIX 25-30': (25,30), 'VIX 30-35': (30,35), 'VIX 35-40': (35,40), 'VIX 40+': (40,999)}
        ci = returns.index.intersection(vix_series.index)
        cv = vix_series.reindex(ci); cr = returns.reindex(ci)
        rm = {}
        for l, (lo, hi) in bins.items():
            mask = (cv >= lo) & (cv < hi)
            if mask.sum() > 10:
                rr = cr[mask]; re = rr - risk_free/252
                max_cum = nav_series.reindex(ci)[mask].cummax()
                mdd = ((nav_series.reindex(ci)[mask] - max_cum) / max_cum).min() * 100 if len(max_cum) > 1 else 0
                rm[l] = {'days':mask.sum(), 'return_pct':round(rr.sum()*100,1), 'sharpe':round(np.sqrt(252)*re.mean()/rr.std() if rr.std() > 0 else 0,2), 'max_dd_pct':round(mdd,1)}
                if l == 'VIX 40+':
                    m['vix40plus_return'] = round(rr.sum()*100, 2)
                    m['vix40plus_sharpe'] = round(np.sqrt(252)*re.mean()/rr.std() if rr.std() > 0 else 0, 2)
        m['regime_breakdown'] = rm

    if benchmark_series is not None:
        bm = benchmark_series.pct_change().fillna(0)
        bm_cagr = ((benchmark_series.iloc[-1]/benchmark_series.iloc[0])**(1/years)-1)*100 if years > 0 else 0
        bm_max = benchmark_series.cummax()
        bm_dd = ((benchmark_series - bm_max)/bm_max).min()*100
        bm_sharpe = np.sqrt(252)*(bm-risk_free/252).mean()/bm.std() if bm.std() > 0 else 0
        m['benchmark'] = {'cagr_pct':round(bm_cagr,2), 'max_drawdown_pct':round(bm_dd,2), 'sharpe_ratio':round(bm_sharpe,2),
                          'total_return_pct':round((benchmark_series.iloc[-1]/benchmark_series.iloc[0]-1)*100,2)}
    return m


# ═══════════════════════════════════════════════════
# Run Backtest
# ═══════════════════════════════════════════════════

def run_capital_preservation_backtest(start_date=START_DATE, end_date=None, profile=None):
    eng = FeatureEngineer(tickers=UNIVERSE, start_date=start_date, end_date=end_date)
    eng.fetch_all(); eng.compute_features()
    daily_idx = eng.prices.index
    scores = compute_composite_scores(eng.features, eng.prices, eng.returns, eng.spy_vol, daily_idx)
    nav, weights, trades = simulate_portfolio(eng.prices, eng.returns, scores, eng.vix_data,
                                               eng.vix_backwardation, eng.credit_stress, eng.features, profile=profile)
    spy_bm = eng.prices['SPY'] / eng.prices['SPY'].iloc[0] * INITIAL_CAPITAL if 'SPY' in eng.prices.columns else None
    metrics = compute_metrics(nav, spy_bm, eng.vix_data)
    return metrics, nav, spy_bm, trades, weights


# ═══════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default=START_DATE)
    parser.add_argument('--end', default=None)
    parser.add_argument('--profile', default=None, choices=list(PROFILES.keys()), help='Strategy profile')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    m, nav, spy, trades, w = run_capital_preservation_backtest(args.start, args.end, profile=args.profile)
    if args.json:
        print(json.dumps(m, indent=2, default=str))
    else:
        s = m; b = m.get('benchmark', {})
        print(f"Profile: {args.profile or 'capital_preservation'}")
        print(f"CAGR: {s['cagr_pct']}% vs SPY {b.get('cagr_pct','N/A')}%")
        print(f"Max DD: {s['max_drawdown_pct']}% vs SPY {b.get('max_drawdown_pct','N/A')}%")
        print(f"Sharpe: {s['sharpe_ratio']} vs SPY {b.get('sharpe_ratio','N/A')}")
        print(f"Win Rate: {s['win_rate_pct']}% | Trades: {len(trades)}")
        for lb, st in m.get('regime_breakdown', {}).items():
            print(f"  {lb:30s}  r={st['return_pct']:>7.1f}%  sh={st['sharpe']:>5.2f}  dd={st['max_dd_pct']:>5.1f}%")
