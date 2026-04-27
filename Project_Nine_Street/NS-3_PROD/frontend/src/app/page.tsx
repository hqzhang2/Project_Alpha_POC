"use client";

import React, { useState, useEffect } from 'react';
import { 
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer,
  BarChart, Bar, Cell
} from 'recharts';
import { TrendingUp, Activity, Target, ArrowRight, ChevronDown, ChevronUp } from 'lucide-react';

// API Base URL
const API_BASE = "http://127.0.0.1:9206/api/v1";

// ── Helper Components ────────────────────────────────────────────────────────────

const DecisionBadge = ({ decision, color }: { decision: string; color: string }) => (
  <span className={`px-2 py-1 rounded text-xs font-bold ${color}`}>
    {decision}
  </span>
);

const ScoreBar = ({ score, max = 5 }: { score: number; max?: number }) => {
  const pct = (score / max) * 100;
  return (
    <div className="w-20 h-2 bg-gray-700 rounded-full overflow-hidden">
      <div 
        className="h-full bg-emerald-400 transition-all"
        style={{ width: `${pct}%` }}
      />
    </div>
  );
};

// ── Tier 1: Sector Rankings ────────────────────────────────────────────────

function Tier1Panel({ data }: { data: any }) {
  const sectors = data?.sectors || [];
  
  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <h3 className="text-lg font-bold mb-4 flex items-center gap-2">
        <TrendingUp className="w-5 h-5 text-emerald-400" />
        Tier 1: Sector Rotation
      </h3>
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-gray-400 border-b border-gray-700">
              <th className="text-left py-2">Rank</th>
              <th className="text-left py-2">Symbol</th>
              <th className="text-left py-2">Name</th>
              <th className="text-right py-2">Momentum</th>
              <th className="text-right py-2">YTD</th>
              <th className="text-center py-2">Tier 2</th>
            </tr>
          </thead>
          <tbody>
            {sectors.map((s: any) => (
              <tr 
                key={s.symbol} 
                className={`border-b border-gray-700 ${s.passToTier2 ? 'bg-emerald-900/20' : ''}`}
              >
                <td className="py-2">#{s.rank}</td>
                <td className="py-2 font-mono font-bold">{s.symbol}</td>
                <td className="py-2 text-gray-300">{s.name}</td>
                <td className={`py-2 text-right ${s.momentum > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {s.momentum > 0 ? '+' : ''}{s.momentum?.toFixed(2)}%
                </td>
                <td className={`py-2 text-right ${s.ytd > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {s.ytd > 0 ? '+' : ''}{s.ytd?.toFixed(2)}%
                </td>
                <td className="py-2 text-center">
                  {s.passToTier2 && <span className="text-emerald-400">▲ PASS</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Tier 2: ETF Signals ───────────────────────────────────────────────────

function Tier2Panel({ data }: { data: any }) {
  const etfs = data?.etfs || [];
  
  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <h3 className="text-lg font-bold mb-4 flex items-center gap-2">
        <Activity className="w-5 h-5 text-amber-400" />
        Tier 2: ETF Signal Engine
      </h3>
      <div className="grid gap-4">
        {etfs.map((etf: any) => {
          const decisionColor = etf.decision === 'ENTER LONG' 
            ? 'bg-emerald-900 text-emerald-400' 
            : etf.decision === 'WATCH'
            ? 'bg-amber-900 text-amber-400'
            : 'bg-gray-700 text-gray-400';
          
          return (
            <div key={etf.symbol} className="bg-gray-700 rounded-lg p-3">
              <div className="flex justify-between items-center mb-2">
                <span className="font-mono font-bold text-xl">{etf.symbol}</span>
                <DecisionBadge decision={etf.decision} color={decisionColor} />
              </div>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <div>
                  <span className="text-gray-400">Score:</span> {etf.score}/5
                  <ScoreBar score={etf.score} />
                </div>
                <div>
                  <span className="text-gray-400">HMM Bull:</span> {(etf.hmm?.bullProb * 100).toFixed(1)}%
                </div>
                <div>
                  <span className="text-gray-400">MACD:</span> {etf.macd?.isBull ? 'Bull ▲' : 'Bear ▼'}
                </div>
                <div>
                  <span className="text-gray-400">ADX:</span> {etf.adx?.value?.toFixed(1)} ({etf.adx?.isStrong ? 'Strong' : 'Weak'})
                </div>
                <div>
                  <span className="text-gray-400">RSI:</span> {etf.rsi?.value?.toFixed(1)} ({etf.rsi?.isOk ? 'OK' : 'Extreme'})
                </div>
                <div>
                  <span className="text-gray-400">OBV:</span> {etf.obv?.isRising ? 'Rising ↗' : 'Falling ↘'}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Tier 3: Stock Selection ────────────────────────────────────────────

function Tier3Panel({ data }: { data: any }) {
  const sectors = data?.sectors || [];
  
  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <h3 className="text-lg font-bold mb-4 flex items-center gap-2">
        <Target className="w-5 h-5 text-blue-400" />
        Tier 3: Stock Selection
      </h3>
      {sectors.length === 0 ? (
        <p className="text-gray-400">No qualifying ETFs from Tier 2</p>
      ) : (
        <div className="grid gap-6">
          {sectors.map((sec: any) => (
            <div key={sec.etf} className="bg-gray-700 rounded-lg p-3">
              <h4 className="font-mono font-bold text-lg mb-2">{sec.etf} → Stocks</h4>
              <div className="grid gap-2">
                {sec.stocks?.map((stock: any) => {
                  const decisionColor = stock.decision === 'BUY'
                    ? 'bg-emerald-900 text-emerald-400'
                    : stock.decision === 'WATCH'
                    ? 'bg-amber-900 text-amber-400'
                    : 'bg-gray-700 text-gray-400';
                  
                  return (
                    <div key={stock.symbol} className="bg-gray-600 rounded p-2 flex justify-between items-center">
                      <div>
                        <span className="font-bold">{stock.symbol}</span>
                        <span className="text-gray-400 text-sm ml-2">
                          F={stock.fscore} TA={stock.taScore}/5 RS={stock.rs26w}%
                        </span>
                      </div>
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-gray-400">
                          conf={stock.confidence?.toFixed(2)}
                        </span>
                        <DecisionBadge decision={stock.decision} color={decisionColor} />
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main Dashboard ────────────────────────────────────────────────────────────────

export default function Dashboard() {
  const [tier, setTier] = useState<1 | 2 | 3>(1);
  const [data, setData] = useState<any>(null);
  const [allData, setAllData] = useState<any>({
    tier1: {
      generatedAt: new Date().toISOString(),
      sectors: [
        {symbol: 'XLE', name: 'Energy', momentum: 12.03, ytd: 16.37, currentPrice: 56.87, rank: 1, passToTier2: true},
        {symbol: 'XLK', name: 'Technology', momentum: 6.45, ytd: 10.57, currentPrice: 160.22, rank: 2, passToTier2: true},
        {symbol: 'XLU', name: 'Utilities', momentum: 5.20, ytd: 9.26, currentPrice: 46.18, rank: 3, passToTier2: true},
      ]
    },
    tier2: {
      etfs: [
        {symbol: 'XLE', decision: 'ENTER LONG', score: 5, hmm: {bullProb: 1.0}}
      ]
    },
    tier3: {
      sectors: []
    }
  });
  const [loading, setLoading] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<string>("");

  useEffect(() => {
    // Load all tiers on mount
    const fetchAll = async () => {
      setLoading(true);
      try {
        console.log('Fetching from API...');
        const res = await fetch("/api/all");
        console.log('Response status:', res.status);
        const json = await res.json();
        console.log('Data received:', json);
        setAllData(json);
        setLastUpdated(new Date().toLocaleTimeString());
      } catch (err) {
        console.error("Failed to fetch data:", err);
      } finally {
        setLoading(false);
      }
    };
    fetchAll();
  }, []);

  // Auto-refresh every 5 minutes
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch("/api/all");
        const json = await res.json();
        setAllData(json);
        setLastUpdated(new Date().toLocaleTimeString());
      } catch (err) {
        console.error("Refresh failed:", err);
      }
    }, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, []);

  const currentData = tier === 1 ? allData?.tier1 : tier === 2 ? allData?.tier2 : allData?.tier3;

  return (
    <div className="min-h-screen bg-gray-900 text-white p-4">
      <div className="max-w-6xl mx-auto">
        {/* Header */}
        <div className="flex justify-between items-center mb-6">
          <div>
            <h1 className="text-2xl font-bold">NS-3: Sector Rotation Algo</h1>
            <p className="text-gray-400 text-sm">
              Port {9206} | 3-Tier System | Last updated: {lastUpdated || '—'}
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => setTier(1)}
              className={`px-4 py-2 rounded ${tier === 1 ? 'bg-emerald-600' : 'bg-gray-700'}`}
            >
              Tier 1
            </button>
            <button
              onClick={() => setTier(2)}
              className={`px-4 py-2 rounded ${tier === 2 ? 'bg-amber-600' : 'bg-gray-700'}`}
            >
              Tier 2
            </button>
            <button
              onClick={() => setTier(3)}
              className={`px-4 py-2 rounded ${tier === 3 ? 'bg-blue-600' : 'bg-gray-700'}`}
            >
              Tier 3
            </button>
          </div>
        </div>

        {/* Loading State */}
        {loading && (
          <div className="flex justify-center py-12">
            <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-emerald-500" />
          </div>
        )}

        {/* Content */}
        {!loading && (
          <>
            {tier === 1 && <Tier1Panel data={currentData} />}
            {tier === 2 && <Tier2Panel data={currentData} />}
            {tier === 3 && <Tier3Panel data={currentData} />}
          </>
        )}

        {/* Footer */}
        <div className="mt-8 text-center text-gray-500 text-xs">
          <p>Data from yfinance | Not financial advice | For research purposes only</p>
        </div>
      </div>
    </div>
  );
}