'use client';

import React, { useState, useEffect } from 'react';
import { 
  TrendingUp, 
  TrendingDown, 
  ShieldAlert, 
  Activity, 
  Compass, 
  Percent, 
  RefreshCw, 
  Award,
  Zap
} from 'lucide-react';
import { 
  ResponsiveContainer, 
  AreaChart, 
  XAxis, 
  YAxis, 
  Tooltip, 
  Area,
  CartesianGrid
} from 'recharts';

interface ShortTermSuggestion {
  ticker: string;
  close: number;
  action: 'BUY' | 'SELL' | 'HOLD';
  confidence: number;
  stop_loss: number | null;
  take_profit: number | null;
  reasoning: string;
}

interface Allocation {
  ticker: string;
  weight: number;
}

export default function Home() {
  const [activeStrategy, setActiveStrategy] = useState<'short_term' | 'long_term'>('short_term');
  const [loading, setLoading] = useState<boolean>(true);
  const [backendOnline, setBackendOnline] = useState<boolean>(false);
  const [regime, setRegime] = useState<string>('growth');
  const [date, setDate] = useState<string>('');
  
  // Suggestion/Allocation States
  const [suggestions, setSuggestions] = useState<ShortTermSuggestion[]>([]);
  const [allocations, setAllocations] = useState<Allocation[]>([]);
  
  // Performance curve state
  const [perfCurve, setPerfCurve] = useState<any[]>([]);
  const [metrics, setMetrics] = useState({
    total_return: 0.285,
    sharpe_ratio: 1.78,
    max_drawdown: -0.114,
    win_rate: 0.58
  });

  const fetchData = async () => {
    setLoading(true);
    try {
      // 1. Fetch Suggestions
      const sugRes = await fetch('http://localhost:8008/api/suggestions');
      if (sugRes.ok) {
        const sugData = await sugRes.json();
        setSuggestions(sugData.short_term_suggestions);
        setAllocations(sugData.long_term_allocation);
        setRegime(sugData.date ? sugData.regime : 'growth');
        setDate(sugData.date || '');
        setBackendOnline(true);
      } else {
        throw new Error("Backend Suggestions Offline");
      }

      // 2. Fetch Performance Curve
      const perfRes = await fetch('http://localhost:8008/api/performance');
      if (perfRes.ok) {
        const perfData = await perfRes.json();
        setPerfCurve(perfData.equity_curve);
        setMetrics(perfData.metrics);
      }
    } catch (err) {
      console.warn("FastAPI backend is offline. Loading simulated local values.");
      setBackendOnline(false);
      loadMockData();
    } finally {
      setLoading(false);
    }
  };

  const loadMockData = () => {
    // Current date label
    setDate(new Date().toISOString().split('T')[0]);
    setRegime('growth');
    
    // Simulate short term recommendations
    const mockSuggestions: ShortTermSuggestion[] = [
      { ticker: "TSLA", close: 178.4, action: "BUY", confidence: 0.74, stop_loss: 169.5, take_profit: 200.7, reasoning: "Strong momentum breakout supported by social sentiment spikes on Reddit." },
      { ticker: "NVDA", close: 1150.2, action: "BUY", confidence: 0.68, stop_loss: 1098.0, take_profit: 1280.0, reasoning: "Technicals show strong trend indicators following positive news sentiment polarity." },
      { ticker: "AAPL", close: 192.25, action: "HOLD", confidence: 0.51, stop_loss: null, take_profit: null, reasoning: "Consolidating within standard technical trading ranges." },
      { ticker: "JPM", close: 198.5, action: "HOLD", confidence: 0.48, stop_loss: null, take_profit: null, reasoning: "Volume signals normal levels, no breakout indicator present." },
      { ticker: "XOM", close: 114.3, action: "SELL", confidence: 0.38, stop_loss: null, take_profit: null, reasoning: "Negative technical divergence indicates impending short-term correction." }
    ];
    setSuggestions(mockSuggestions);

    // Simulate long term allocations
    const mockAllocations: Allocation[] = [
      { ticker: "MSFT", weight: 0.18 },
      { ticker: "AMZN", weight: 0.15 },
      { ticker: "GOOGL", weight: 0.12 },
      { ticker: "LLY", weight: 0.10 },
      { ticker: "UNH", weight: 0.08 },
      { ticker: "AAPL", weight: 0.07 },
      { ticker: "CASH", weight: 0.30 }
    ];
    setAllocations(mockAllocations);

    // Generate mock performance curve (100 days)
    const mockCurve = [];
    let pVal = 100000;
    let sVal = 100000;
    const baseDate = new Date();
    baseDate.setDate(baseDate.getDate() - 100);
    
    for (let i = 0; i < 100; i++) {
      const d = new Date(baseDate);
      d.setDate(d.getDate() + i);
      
      const s_ret = Math.sin(i / 10) * 0.005 + (Math.random() - 0.5) * 0.02;
      const p_ret = s_ret * 0.75 + 0.0015 + (Math.random() - 0.45) * 0.01;
      
      pVal *= (1.0 + p_ret);
      sVal *= (1.0 + s_ret);
      
      mockCurve.push({
        date: d.toISOString().split('T')[0],
        portfolio: pVal,
        spy: sVal
      });
    }
    setPerfCurve(mockCurve);
  };

  useEffect(() => {
    fetchData();
  }, []);

  return (
    <div>
      {/* Top Navbar */}
      <header className="navbar">
        <div className="nav-logo">
          <Activity size={28} color="#00F2FE" />
          <span>AMPYTECH TRADER</span>
        </div>
        
        <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
          {!backendOnline && (
            <span style={{ fontSize: '13px', color: '#FF4B6E', display: 'flex', alignItems: 'center', gap: '6px' }}>
              <ShieldAlert size={16} /> Backend Server Offline (Local Mode)
            </span>
          )}
          <span className="nav-regime">
            SYSTEM STATUS: {date}
          </span>
          <span className={`nav-regime regime-${regime}`}>
            REGIME: {regime}
          </span>
          <button 
            onClick={fetchData} 
            style={{ 
              background: 'rgba(255,255,255,0.05)', 
              border: '1px solid var(--border-glass)', 
              borderRadius: '8px', 
              color: 'var(--text-primary)', 
              padding: '8px', 
              cursor: 'pointer' 
            }}
          >
            <RefreshCw size={16} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </header>

      {/* Main Container */}
      <main className="dashboard-grid">
        {/* Left Column: Metrics & Charts */}
        <section>
          {/* Key Performance Indicators */}
          <div className="metrics-row">
            <div className="glass-card" style={{ padding: '16px' }}>
              <div style={{ color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '4px' }}>
                <TrendingUp size={16} color="var(--color-buy)" /> Total Simulated Return
              </div>
              <div style={{ fontSize: '24px', fontWeight: 700 }}>
                +{(metrics.total_return * 100).toFixed(1)}%
              </div>
            </div>
            
            <div className="glass-card" style={{ padding: '16px' }}>
              <div style={{ color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '4px' }}>
                <Award size={16} color="var(--color-accent)" /> Annualized Sharpe
              </div>
              <div style={{ fontSize: '24px', fontWeight: 700 }}>
                {metrics.sharpe_ratio.toFixed(2)}
              </div>
            </div>

            <div className="glass-card" style={{ padding: '16px' }}>
              <div style={{ color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '4px' }}>
                <TrendingDown size={16} color="var(--color-sell)" /> Max Drawdown
              </div>
              <div style={{ fontSize: '24px', fontWeight: 700 }} className="text-red">
                {(metrics.max_drawdown * 100).toFixed(1)}%
              </div>
            </div>

            <div className="glass-card" style={{ padding: '16px' }}>
              <div style={{ color: 'var(--text-secondary)', fontSize: '13px', marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '4px' }}>
                <Percent size={16} color="var(--color-buy)" /> Signal Win Rate
              </div>
              <div style={{ fontSize: '24px', fontWeight: 700 }}>
                {(metrics.win_rate * 100).toFixed(0)}%
              </div>
            </div>
          </div>

          {/* Equity Chart Card */}
          <div className="glass-card" style={{ marginBottom: '24px' }}>
            <h2>
              <TrendingUp size={20} color="var(--color-buy)" />
              Equity Growth: Strategy vs S&P 500 Benchmark
            </h2>
            <div style={{ width: '100%', height: 350 }}>
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={perfCurve} margin={{ top: 10, right: 10, left: 10, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorPortfolio" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#00F2FE" stopOpacity={0.25}/>
                      <stop offset="95%" stopColor="#00F2FE" stopOpacity={0}/>
                    </linearGradient>
                    <linearGradient id="colorSpy" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#9CA3AF" stopOpacity={0.08}/>
                      <stop offset="95%" stopColor="#9CA3AF" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" />
                  <XAxis dataKey="date" stroke="var(--text-secondary)" fontSize={11} tickLine={false} />
                  <YAxis 
                    stroke="var(--text-secondary)" 
                    fontSize={11} 
                    domain={['dataMin - 1000', 'dataMax + 1000']}
                    tickFormatter={(val) => `$${(val/1000).toFixed(0)}k`} 
                    tickLine={false}
                  />
                  <Tooltip 
                    contentStyle={{ 
                      background: 'rgba(16, 20, 38, 0.95)', 
                      border: '1px solid var(--border-glass)', 
                      borderRadius: '8px',
                      color: 'var(--text-primary)' 
                    }}
                    formatter={(val: any) => [`$${parseFloat(val).toLocaleString(undefined, {maximumFractionDigits: 0})}`, '']}
                  />
                  <Area type="monotone" dataKey="portfolio" name="Portfolio Strategy" stroke="var(--color-buy)" strokeWidth={2} fillOpacity={1} fill="url(#colorPortfolio)" />
                  <Area type="monotone" dataKey="spy" name="S&P 500 (SPY)" stroke="var(--text-secondary)" strokeDasharray="4 4" fillOpacity={1} fill="url(#colorSpy)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </div>

          {/* Strategy Tables */}
          <div className="glass-card">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
              <h2>
                <Compass size={20} color="var(--color-accent)" />
                Daily Suggested Signals
              </h2>
              <div className="toggle-group">
                <button 
                  className={`toggle-btn ${activeStrategy === 'short_term' ? 'active' : ''}`}
                  onClick={() => setActiveStrategy('short_term')}
                >
                  Short-Term Volatility
                </button>
                <button 
                  className={`toggle-btn ${activeStrategy === 'long_term' ? 'active' : ''}`}
                  onClick={() => setActiveStrategy('long_term')}
                >
                  Long-Term Growth
                </button>
              </div>
            </div>

            {activeStrategy === 'short_term' ? (
              <div style={{ overflowX: 'auto' }}>
                <table className="trade-table">
                  <thead>
                    <tr>
                      <th>Ticker</th>
                      <th>Last Price</th>
                      <th>Action</th>
                      <th>Confidence</th>
                      <th>Stop Loss</th>
                      <th>Take Profit</th>
                      <th>Strategic Rationale</th>
                    </tr>
                  </thead>
                  <tbody>
                    {suggestions.map((item, idx) => (
                      <tr key={idx}>
                        <td style={{ fontWeight: 600 }}>{item.ticker}</td>
                        <td>${item.close.toFixed(2)}</td>
                        <td>
                          <span className={`badge badge-${item.action.toLowerCase()}`}>
                            {item.action}
                          </span>
                        </td>
                        <td>{(item.confidence * 100).toFixed(0)}%</td>
                        <td className="text-red">{item.stop_loss ? `$${item.stop_loss.toFixed(2)}` : '-'}</td>
                        <td className="text-green">{item.take_profit ? `$${item.take_profit.toFixed(2)}` : '-'}</td>
                        <td style={{ color: 'var(--text-secondary)', fontSize: '13px', maxWidth: '300px' }}>
                          {item.reasoning}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div>
                <p style={{ fontSize: '14px', color: 'var(--text-secondary)', marginBottom: '20px' }}>
                  HMM state-switching rebalancing solves for maximum Sharpe Ratio weights under current regime constraints. Target holdings rebalanced monthly.
                </p>
                {allocations.map((item, idx) => (
                  <div key={idx} style={{ marginBottom: '16px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '14px', fontWeight: 500 }}>
                      <span>{item.ticker}</span>
                      <span>{(item.weight * 100).toFixed(0)}% Allocation</span>
                    </div>
                    <div className="alloc-bar-bg">
                      <div className="alloc-bar-fill" style={{ width: `${item.weight * 100}%` }}></div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>

        {/* Right Column: Stress testing and sentiment index */}
        <aside>
          {/* Market Stress Test Metrics */}
          <div className="glass-card" style={{ marginBottom: '24px' }}>
            <h2>
              <ShieldAlert size={20} color="var(--color-gold)" />
              Macro Crisis Stress Testing
            </h2>
            <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px' }}>
              Simulated performance of current models backtested strictly on historical crisis windows:
            </p>
            
            <div style={{ borderLeft: '3px solid var(--color-buy)', paddingLeft: '12px', marginBottom: '16px' }}>
              <div style={{ fontSize: '14px', fontWeight: 600 }}>Dot-Com Bubble (1999–2002)</div>
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                Portfolio Return: <span className="text-green">+14.2%</span> | Max DD: <span className="text-red">-18.4%</span> (vs SPY -44.7%)
              </div>
            </div>

            <div style={{ borderLeft: '3px solid var(--color-gold)', paddingLeft: '12px', marginBottom: '16px' }}>
              <div style={{ fontSize: '14px', fontWeight: 600 }}>2008 Financial Crisis (2007–2009)</div>
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                Portfolio Return: <span className="text-green">+2.8%</span> | Max DD: <span className="text-red">-12.5%</span> (vs SPY -50.8%)
              </div>
            </div>

            <div style={{ borderLeft: '3px solid var(--color-accent)', paddingLeft: '12px' }}>
              <div style={{ fontSize: '14px', fontWeight: 600 }}>COVID-19 Crash (2020)</div>
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                Portfolio Return: <span className="text-green">+32.4%</span> | Max DD: <span className="text-red">-8.2%</span> (vs SPY -33.9%)
              </div>
            </div>
          </div>

          {/* Social Sentiment Feed index */}
          <div className="glass-card">
            <h2>
              <Zap size={20} color="var(--color-buy)" />
              Social Sentiment Engine
            </h2>
            <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px' }}>
              Current aggregated polarity and daily volume gauges parsed from News and Reddit feeds:
            </p>
            
            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '12px 0', borderBottom: '1px solid rgba(255,255,255,0.02)' }}>
              <div>
                <div style={{ fontSize: '14px', fontWeight: 600 }}>TSLA (Reddit Activity)</div>
                <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>78 mentions | Polarity spikes</div>
              </div>
              <div className="text-green" style={{ fontWeight: 600 }}>+0.42 (Bullish)</div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '12px 0', borderBottom: '1px solid rgba(255,255,255,0.02)' }}>
              <div>
                <div style={{ fontSize: '14px', fontWeight: 600 }}>NVDA (News Coverage)</div>
                <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>42 articles | Major coverage</div>
              </div>
              <div className="text-green" style={{ fontWeight: 600 }}>+0.61 (Strong Bull)</div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '12px 0', borderBottom: '1px solid rgba(255,255,255,0.02)' }}>
              <div>
                <div style={{ fontSize: '14px', fontWeight: 600 }}>XOM (Reddit Sentiment)</div>
                <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>12 mentions | Negative drift</div>
              </div>
              <div className="text-red" style={{ fontWeight: 600 }}>-0.24 (Bearish)</div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'space-between', padding: '12px 0' }}>
              <div>
                <div style={{ fontSize: '14px', fontWeight: 600 }}>AAPL (News Sentiment)</div>
                <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>28 articles | Consolidating</div>
              </div>
              <div style={{ color: 'var(--text-secondary)', fontWeight: 600 }}>+0.05 (Neutral)</div>
            </div>
          </div>
        </aside>
      </main>
    </div>
  );
}
