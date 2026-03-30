"""
Earnings Estimates Module (Sprint 4 - Bloomberg EE style)
"""
import yfinance as yf
import pandas as pd
import numpy as np

def get_estimates(ticker):
    """Fetch consensus estimates, history, and trends from Yahoo Finance."""
    try:
        t = yf.Ticker(ticker)
        
        # 1. Consensus Summary (EE Top)
        ee = t.earnings_estimate
        rev = t.revenue_estimate
        
        summary = []
        if ee is not None and not ee.empty:
            for period in ee.index:
                summary.append({
                    "period": period,
                    "eps_avg": ee.loc[period, 'avg'],
                    "eps_low": ee.loc[period, 'low'],
                    "eps_high": ee.loc[period, 'high'],
                    "eps_year_ago": ee.loc[period, 'yearAgoEps'],
                    "eps_growth": ee.loc[period, 'growth'],
                    "analysts": ee.loc[period, 'numberOfAnalysts'],
                    "rev_avg": rev.loc[period, 'avg'] if rev is not None else None,
                    "rev_growth": rev.loc[period, 'growth'] if rev is not None else None
                })

        # 2. Earnings History (Surprise Table)
        hist_df = t.earnings_history
        history = []
        if hist_df is not None and not hist_df.empty:
            for quarter in hist_df.index:
                history.append({
                    "quarter": quarter,
                    "actual": hist_df.loc[quarter, 'epsActual'],
                    "estimate": hist_df.loc[quarter, 'epsEstimate'],
                    "surprise": hist_df.loc[quarter, 'surprisePercent']
                })

        # 3. EPS Trend (Revision History)
        trend_df = t.eps_trend
        trends = []
        if trend_df is not None and not trend_df.empty:
            for period in trend_df.index:
                trends.append({
                    "period": period,
                    "current": trend_df.loc[period, 'current'],
                    "7days": trend_df.loc[period, '7daysAgo'],
                    "30days": trend_df.loc[period, '30daysAgo'],
                    "90days": trend_df.loc[period, '90daysAgo']
                })

        return {
            "ticker": ticker.upper(),
            "summary": summary,
            "history": history,
            "trends": trends
        }
    except Exception as e:
        return {"error": str(e)}

