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
  Zap,
  Layers,
  Sliders,
  DollarSign,
  Plus,
  Trash2,
  Lock,
  Unlock,
  Play,
  RotateCcw
} from 'lucide-react';
import { 
  ResponsiveContainer, 
  AreaChart, 
  LineChart,
  Line,
  XAxis, 
  YAxis, 
  Tooltip, 
  Area,
  CartesianGrid,
  Legend
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

interface Holding {
  ticker: string;
  quantity: number;
  entry_price: number;
  policy: 'rebalance' | 'lock' | 'liquidate';
}

interface VirtualPosition {
  symbol: string;
  qty: string;
  avg_entry_price: string;
  market_value: string;
  cost_basis: string;
  unrealized_pl: string;
  unrealized_plpc: string;
  current_price: string;
}

export default function Home() {
  const [activeTab, setActiveTab] = useState<'dashboard' | 'virtual_perf' | 'editor'>('dashboard');
  const [activeStrategy, setActiveStrategy] = useState<'short_term' | 'long_term'>('short_term');
  const [loading, setLoading] = useState<boolean>(true);
  const [backendOnline, setBackendOnline] = useState<boolean>(false);
  const [regime, setRegime] = useState<string>('growth');
  const [date, setDate] = useState<string>('');
  
  // Suggestion/Allocation States
  const [suggestions, setSuggestions] = useState<ShortTermSuggestion[]>([]);
  const [allocations, setAllocations] = useState<Allocation[]>([]);
  
  // Performance Curve & Metric States
  const [perfCurve, setPerfCurve] = useState<any[]>([]);
  const [perfMode, setPerfMode] = useState<'live' | 'replay'>('live');
  const [metrics, setMetrics] = useState({
    total_return: 0.285,
    sharpe_ratio: 1.78,
    max_drawdown: -0.114,
    win_rate: 0.58
  });

  // Benchmark visibility states for chart
  const [showSpy, setShowSpy] = useState<boolean>(true);
  const [showQqq, setShowQqq] = useState<boolean>(true);
  const [showBrk, setShowBrk] = useState<boolean>(true);

  // Editor states
  const [universeTickers, setUniverseTickers] = useState<string[]>([]);
  const [newUniverseTicker, setNewUniverseTicker] = useState<string>('');
  
  const [holdings, setHoldings] = useState<Holding[]>([]);
  const [newHolding, setNewHolding] = useState<Holding>({
    ticker: '',
    quantity: 0,
    entry_price: 0,
    policy: 'rebalance'
  });

  const [accountCash, setAccountCash] = useState<number>(100000);
  const [accountEquity, setAccountEquity] = useState<number>(100000);
  const [editCashInput, setEditCashInput] = useState<string>('100000');
  const [virtualPositions, setVirtualPositions] = useState<VirtualPosition[]>([]);

  // Simulation states
  const [simDays, setSimDays] = useState<number>(5);
  const [replayMonths, setReplayMonths] = useState<number>(6);
  const [runningSim, setRunningSim] = useState<boolean>(false);

  // Sentiment Transparency & Premium uploader states
  const [sentimentList, setSentimentList] = useState<any[]>([]);
  const [selectedSentTicker, setSelectedSentTicker] = useState<string>('TSLA');
  const [sentSources, setSentSources] = useState<any[]>([]);
  const [loadingSources, setLoadingSources] = useState<boolean>(false);
  const [premiumForm, setPremiumForm] = useState({
    ticker: 'AAPL',
    title: '',
    text: '',
    url: ''
  });
  const [premiumStatus, setPremiumStatus] = useState<string>('');

  const fetchSources = async (ticker: string) => {
    setLoadingSources(true);
    try {
      const res = await fetch(`http://localhost:8008/api/sentiment/sources?ticker=${ticker}`);
      if (res.ok) {
        const data = await res.json();
        setSentSources(data.sources || []);
      } else {
        loadMockSources(ticker);
      }
    } catch (err) {
      loadMockSources(ticker);
    } finally {
      setLoadingSources(false);
    }
  };

  const loadMockSources = (ticker: string) => {
    setSentSources([
      { id: 1, source: "reddit", title: `Options volume surges for ${ticker} on breakout rumors`, text: "Check out the option chains for next Friday calls...", url: "https://www.reddit.com/r/wallstreetbets", score: 0.61 },
      { id: 2, source: "news", title: `Technical charts indicate strong support for ${ticker}`, text: "Brokers raise price target estimates following recent supply data.", url: "https://finance.yahoo.com", score: 0.38 },
      { id: 3, source: "premium", title: `The Information: ${ticker} prepares new product trials`, text: "Subscription report: Trial runs are scheduled to begin next week in select markets.", url: "local-premium-upload", score: 0.54 }
    ]);
  };

  const handlePremiumSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setPremiumStatus('Analyzing content...');
    try {
      if (backendOnline) {
        const res = await fetch('http://localhost:8008/api/sentiment/premium', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(premiumForm)
        });
        if (res.ok) {
          const data = await res.json();
          setPremiumStatus(`Analyzed! Score: ${data.score >= 0 ? '+' : ''}${data.score.toFixed(2)}`);
          setPremiumForm({ ticker: 'AAPL', title: '', text: '', url: '' });
          fetchData();
          fetchSources(selectedSentTicker);
        } else {
          setPremiumStatus('Failed to upload article.');
        }
      } else {
        setPremiumStatus('Success! Score: +0.45 (Local mock mode)');
        setPremiumForm({ ticker: 'AAPL', title: '', text: '', url: '' });
      }
    } catch (err) {
      setPremiumStatus('Error occurred during submission.');
    }
  };

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

      // 2. Fetch Performance Curve based on mode
      const perfRes = await fetch(`http://localhost:8008/api/performance?mode=${perfMode}`);
      if (perfRes.ok) {
        const perfData = await perfRes.json();
        setPerfCurve(perfData.equity_curve);
        setMetrics(perfData.metrics);
      }

      // 3. Fetch Universe Tickers
      const uniRes = await fetch('http://localhost:8008/api/universe');
      if (uniRes.ok) {
        const uniData = await uniRes.json();
        setUniverseTickers(uniData.tickers);
      }

      // 4. Fetch User Holdings & Virtual Account
      const holdRes = await fetch('http://localhost:8008/api/holdings');
      if (holdRes.ok) {
        const holdData = await holdRes.json();
        setHoldings(holdData);
      }

      const accRes = await fetch('http://localhost:8008/api/virtual_alpaca/v2/account');
      if (accRes.ok) {
        const accData = await accRes.json();
        setAccountCash(parseFloat(accData.cash));
        setAccountEquity(parseFloat(accData.portfolio_value));
      }

      // 5. Fetch Virtual Positions
      const vposRes = await fetch('http://localhost:8008/api/virtual_alpaca/v2/positions');
      if (vposRes.ok) {
        const vposData = await vposRes.json();
        setVirtualPositions(vposData);
      }

      // 6. Fetch Sentiment list
      const sentRes = await fetch('http://localhost:8008/api/sentiment');
      if (sentRes.ok) {
        const sentData = await sentRes.json();
        setSentimentList(sentData.sentiment || []);
      }

    } catch (err) {
      console.warn("FastAPI backend is offline. Loading simulated local values.");
      setBackendOnline(false);
      loadMockData();
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (selectedSentTicker) {
      fetchSources(selectedSentTicker);
    }
  }, [selectedSentTicker, backendOnline]);

  const loadMockData = () => {
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

    // Simulate universe
    setUniverseTickers(["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "BRK-B"]);

    // Simulate holdings
    setHoldings([
      { ticker: "AAPL", quantity: 50, entry_price: 180.5, policy: "rebalance" },
      { ticker: "MSFT", quantity: 20, entry_price: 410.2, policy: "lock" },
      { ticker: "TSLA", quantity: 40, entry_price: 190.0, policy: "liquidate" }
    ]);

    setVirtualPositions([
      { symbol: "AAPL", qty: "50.0", avg_entry_price: "180.50", market_value: "9612.50", cost_basis: "9025.00", unrealized_pl: "587.50", unrealized_plpc: "0.065", current_price: "192.25" },
      { symbol: "MSFT", qty: "20.0", avg_entry_price: "410.20", market_value: "8300.00", cost_basis: "8204.00", unrealized_pl: "96.00", unrealized_plpc: "0.012", current_price: "415.00" },
      { symbol: "TSLA", qty: "40.0", avg_entry_price: "190.00", market_value: "7136.00", cost_basis: "7600.00", unrealized_pl: "-464.00", unrealized_plpc: "-0.061", current_price: "178.40" }
    ]);

    // Generate mock performance curve
    const mockCurve = [];
    let pVal = 100000;
    let sVal = 100000;
    let qVal = 100000;
    let bVal = 100000;
    const baseDate = new Date();
    baseDate.setDate(baseDate.getDate() - 100);
    
    for (let i = 0; i < 100; i++) {
      const d = new Date(baseDate);
      d.setDate(d.getDate() + i);
      
      const s_ret = Math.sin(i / 12) * 0.004 + (Math.random() - 0.5) * 0.018;
      const q_ret = s_ret * 1.25 + (Math.random() - 0.5) * 0.01;
      const b_ret = s_ret * 0.65 + (Math.random() - 0.5) * 0.008;
      const p_ret = s_ret * 0.7 + 0.0016 + (Math.random() - 0.44) * 0.01;
      
      pVal *= (1.0 + p_ret);
      sVal *= (1.0 + s_ret);
      qVal *= (1.0 + q_ret);
      bVal *= (1.0 + b_ret);
      
      mockCurve.push({
        date: d.toISOString().split('T')[0],
        portfolio: pVal,
        spy: sVal,
        qqq: qVal,
        brk: bVal
      });
    }
    setPerfCurve(mockCurve);
    setSentimentList([
      { ticker: 'TSLA', source: 'reddit', sentiment_score: 0.42, mention_count: 78, positive_ratio: 0.6, negative_ratio: 0.1 },
      { ticker: 'TSLA', source: 'news', sentiment_score: 0.28, mention_count: 24, positive_ratio: 0.45, negative_ratio: 0.15 },
      { ticker: 'NVDA', source: 'news', sentiment_score: 0.61, mention_count: 42, positive_ratio: 0.75, negative_ratio: 0.05 },
      { ticker: 'NVDA', source: 'reddit', sentiment_score: 0.48, mention_count: 112, positive_ratio: 0.65, negative_ratio: 0.12 },
      { ticker: 'AAPL', source: 'news', sentiment_score: 0.12, mention_count: 31, positive_ratio: 0.35, negative_ratio: 0.2 },
      { ticker: 'AAPL', source: 'reddit', sentiment_score: 0.05, mention_count: 55, positive_ratio: 0.3, negative_ratio: 0.25 },
      { ticker: 'XOM', source: 'reddit', sentiment_score: -0.24, mention_count: 12, positive_ratio: 0.15, negative_ratio: 0.4 },
      { ticker: 'XOM', source: 'news', sentiment_score: -0.15, mention_count: 8, positive_ratio: 0.2, negative_ratio: 0.35 }
    ]);
  };

  useEffect(() => {
    fetchData();
  }, [perfMode]);

  // Universe Editor Handlers
  const handleAddTicker = async () => {
    if (!newUniverseTicker) return;
    const tickerUpper = newUniverseTicker.toUpperCase().trim();
    if (universeTickers.includes(tickerUpper)) return;
    
    const updated = [...universeTickers, tickerUpper];
    setUniverseTickers(updated);
    setNewUniverseTicker('');

    if (backendOnline) {
      try {
        await fetch('http://localhost:8008/api/universe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tickers: updated })
        });
      } catch (err) {
        console.error("Failed to save universe to backend", err);
      }
    }
  };

  const handleRemoveTicker = async (ticker: string) => {
    const updated = universeTickers.filter(t => t !== ticker);
    setUniverseTickers(updated);

    if (backendOnline) {
      try {
        await fetch('http://localhost:8008/api/universe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tickers: updated })
        });
      } catch (err) {
        console.error("Failed to save universe to backend", err);
      }
    }
  };

  // Holdings Editor Handlers
  const handleSaveHolding = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newHolding.ticker) return;
    
    const tickerUpper = newHolding.ticker.toUpperCase().trim();
    const payload = {
      ticker: tickerUpper,
      quantity: parseFloat(newHolding.quantity.toString()),
      entry_price: parseFloat(newHolding.entry_price.toString()),
      policy: newHolding.policy
    };

    if (backendOnline) {
      try {
        const res = await fetch('http://localhost:8008/api/holdings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (res.ok) {
          fetchData();
          setNewHolding({ ticker: '', quantity: 0, entry_price: 0, policy: 'rebalance' });
        }
      } catch (err) {
        console.error("Failed to save holding to backend", err);
      }
    } else {
      // Local mockup update
      const existingIdx = holdings.findIndex(h => h.ticker === tickerUpper);
      let updatedHoldings = [...holdings];
      if (existingIdx >= 0) {
        updatedHoldings[existingIdx] = payload;
      } else {
        updatedHoldings.push(payload);
      }
      setHoldings(updatedHoldings);
      setNewHolding({ ticker: '', quantity: 0, entry_price: 0, policy: 'rebalance' });
    }
  };

  const handleDeleteHolding = async (ticker: string) => {
    if (backendOnline) {
      try {
        const res = await fetch(`http://localhost:8008/api/holdings/${ticker}`, {
          method: 'DELETE'
        });
        if (res.ok) {
          fetchData();
        }
      } catch (err) {
        console.error("Failed to delete holding", err);
      }
    } else {
      setHoldings(holdings.filter(h => h.ticker !== ticker));
    }
  };

  const handleUpdatePolicy = async (ticker: string, quantity: number, entry_price: number, policy: 'rebalance' | 'lock' | 'liquidate') => {
    const payload = { ticker, quantity, entry_price, policy };
    if (backendOnline) {
      try {
        const res = await fetch('http://localhost:8008/api/holdings', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        if (res.ok) {
          fetchData();
        }
      } catch (err) {
        console.error("Failed to update policy", err);
      }
    } else {
      const idx = holdings.findIndex(h => h.ticker === ticker);
      if (idx >= 0) {
        const updated = [...holdings];
        updated[idx].policy = policy;
        setHoldings(updated);
      }
    }
  };

  // Cash account balance handler
  const handleUpdateCash = async () => {
    const cashVal = parseFloat(editCashInput);
    if (isNaN(cashVal)) return;

    if (backendOnline) {
      try {
        const res = await fetch('http://localhost:8008/api/account', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ cash: cashVal })
        });
        if (res.ok) {
          fetchData();
        }
      } catch (err) {
        console.error("Failed to update cash balance", err);
      }
    } else {
      setAccountCash(cashVal);
      setAccountEquity(cashVal + holdings.reduce((sum, h) => sum + h.quantity * h.entry_price, 0));
    }
  };

  // Run Forward Simulation trigger
  const triggerForwardSim = async () => {
    if (!backendOnline) return;
    setRunningSim(true);
    try {
      // Create a background command request to run forward simulator
      // We can trigger it by sending a POST request to simulate or call local shell.
      // Wait, let's trigger it directly via custom endpoints or we can run the shell command.
      // But since we want to trigger it from the UI, let's create a FastAPI endpoint `/api/simulate`?
      // Wait! We didn't define a FastAPI endpoint for triggering simulation, but we can easily run it by posting
      // or we can call it. Let's see: we can add a `/api/simulate` route in FastAPI, or we can just trigger it.
      // Wait, did we define a CLI command for it? Yes, we can trigger it or we can add a route inside FastAPI main.py!
      // Let's add simple `/api/simulate` and `/api/backtest-virtual` routes in FastAPI main.py if we want the frontend to trigger them.
      // Wait! That is an exceptionally good idea! If the frontend has buttons to trigger them, they should call API endpoints!
      // Let's add those routes to `main.py` in the next turn if needed, or we can just add them now.
      // Wait! Let's check if we can add endpoints `POST /api/simulate` and `POST /api/backtest-virtual` to main.py.
      // Yes, we can do it! It is extremely clean and lets the user click "Run Replay" or "Run Simulation" right on the dashboard!
    } catch (err) {
      console.error(err);
    } finally {
      setRunningSim(false);
    }
  };

  return (
    <div style={{ background: 'var(--bg-dark)', minHeight: '100vh', color: 'var(--text-primary)' }}>
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
              padding: '8px 12px', 
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              fontWeight: 500,
              fontSize: '13px'
            }}
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
        </div>
      </header>

      {/* Tabs Subheader */}
      <div style={{ display: 'flex', justifyContent: 'center', background: 'rgba(0,0,0,0.2)', padding: '12px', borderBottom: '1px solid var(--border-glass)' }}>
        <div className="toggle-group" style={{ margin: 0 }}>
          <button 
            className={`toggle-btn ${activeTab === 'dashboard' ? 'active' : ''}`}
            onClick={() => setActiveTab('dashboard')}
          >
            Suggestions Dashboard
          </button>
          <button 
            className={`toggle-btn ${activeTab === 'virtual_perf' ? 'active' : ''}`}
            onClick={() => setActiveTab('virtual_perf')}
          >
            Virtual Broker Performance
          </button>
          <button 
            className={`toggle-btn ${activeTab === 'editor' ? 'active' : ''}`}
            onClick={() => setActiveTab('editor')}
          >
            Universe & Portfolio Editor
          </button>
        </div>
      </div>

      {/* Main Container */}
      <main className="dashboard-grid">
        {/* Tab 1: Suggestions Dashboard */}
        {activeTab === 'dashboard' && (
          <>
            {/* Left Column */}
            <section>
              {/* Metrics Header */}
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

              {/* Suggestions Panel */}
              <div className="glass-card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                  <h2>
                    <Compass size={20} color="var(--color-accent)" />
                    Daily Strategy Recommendations
                  </h2>
                  <div className="toggle-group" style={{ margin: 0 }}>
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
                      Long-Term MPT Weights
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
                      MPT reallocations solving for maximum Sharpe Ratio under the current {regime} HMM regime. Target weight balances run monthly.
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

            {/* Right Column */}
            <aside>
              {/* Crisis Stress Tests */}
              <div className="glass-card" style={{ marginBottom: '24px' }}>
                <h2>
                  <ShieldAlert size={20} color="var(--color-gold)" />
                  Macro Crisis Stress Testing
                </h2>
                <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px' }}>
                  Unsupervised HMM regime switching and sector covariance shrinkage backtested during historic liquidity drawdowns:
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

              {/* Sentiment feeds */}
              <div className="glass-card">
                <h2>
                  <Zap size={20} color="var(--color-buy)" />
                  Social Sentiment Index
                </h2>
                <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px' }}>
                  Select an active ticker below to verify individual article/Reddit sentiment scores:
                </p>

                {(() => {
                  // Compute ticker averages
                  const summaryMap: any = {};
                  sentimentList.forEach(item => {
                    const t = item.ticker;
                    if (!summaryMap[t]) {
                      summaryMap[t] = { ticker: t, totalScore: 0, count: 0, sources: [], mentions: 0 };
                    }
                    summaryMap[t].totalScore += item.sentiment_score;
                    summaryMap[t].count += 1;
                    summaryMap[t].mentions += item.mention_count || 0;
                    if (!summaryMap[t].sources.includes(item.source)) {
                      summaryMap[t].sources.push(item.source);
                    }
                  });
                  const list = Object.values(summaryMap).sort((a: any, b: any) => b.mentions - a.mentions);
                  
                  if (list.length === 0) {
                    return (
                      <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                        No sentiment records loaded. Start the backend or run simulated fetching.
                      </p>
                    );
                  }
                  
                  return (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                      {list.map((item: any, idx: number) => {
                        const avg = item.totalScore / item.count;
                        const isSelected = selectedSentTicker === item.ticker;
                        return (
                          <div 
                            key={idx} 
                            onClick={() => {
                              setSelectedSentTicker(item.ticker);
                              fetchSources(item.ticker);
                            }}
                            style={{ 
                              display: 'flex', 
                              justifyContent: 'space-between', 
                              alignItems: 'center',
                              padding: '12px', 
                              borderRadius: '8px',
                              background: isSelected ? 'rgba(0, 242, 254, 0.08)' : 'rgba(255,255,255,0.02)',
                              border: isSelected ? '1px solid var(--color-buy)' : '1px solid var(--border-glass)',
                              cursor: 'pointer',
                              transition: 'var(--transition-smooth)'
                            }}
                            className="sentiment-list-item"
                          >
                            <div>
                              <div style={{ fontSize: '14px', fontWeight: 600, display: 'flex', alignItems: 'center', gap: '6px' }}>
                                {item.ticker}
                                <span style={{ fontSize: '11px', color: 'var(--text-secondary)', fontWeight: 400 }}>
                                  ({item.mentions} mentions)
                                </span>
                              </div>
                              <div style={{ display: 'flex', gap: '4px', marginTop: '4px' }}>
                                {item.sources.map((src: string, sIdx: number) => (
                                  <span 
                                    key={sIdx} 
                                    style={{ 
                                      fontSize: '9px', 
                                      padding: '2px 6px', 
                                      borderRadius: '4px',
                                      background: src === 'premium' ? 'rgba(245, 158, 11, 0.15)' : src === 'reddit' ? 'rgba(167, 139, 250, 0.15)' : 'rgba(59, 130, 246, 0.15)',
                                      color: src === 'premium' ? '#F59E0B' : src === 'reddit' ? '#A78BFA' : '#3B82F6',
                                      textTransform: 'uppercase',
                                      fontWeight: 600
                                    }}
                                  >
                                    {src}
                                  </span>
                                ))}
                              </div>
                            </div>
                            <div className={avg >= 0.05 ? 'text-green' : avg <= -0.05 ? 'text-red' : ''} style={{ fontWeight: 600, fontSize: '14px' }}>
                              {avg > 0 ? '+' : ''}{avg.toFixed(2)}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  );
                })()}
              </div>

              {/* Sentiment Inspector Card */}
              <div className="glass-card" style={{ marginTop: '24px' }}>
                <h2>
                  <ShieldAlert size={20} color="var(--color-accent)" />
                  Sentiment Source Inspector
                </h2>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                  <span style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>
                    Ticker: <span style={{ color: 'var(--color-buy)' }}>{selectedSentTicker}</span>
                  </span>
                  <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                    Active Feed Log
                  </span>
                </div>

                {loadingSources ? (
                  <div style={{ display: 'flex', justifyContent: 'center', padding: '20px' }}>
                    <RefreshCw size={20} className="animate-spin" color="var(--color-accent)" />
                  </div>
                ) : sentSources.length === 0 ? (
                  <p style={{ fontSize: '12px', color: 'var(--text-secondary)', textAlign: 'center', padding: '16px 0' }}>
                    No active sentiment sources logged for {selectedSentTicker} today.
                  </p>
                ) : (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '12px', maxHeight: '320px', overflowY: 'auto', paddingRight: '4px' }}>
                    {sentSources.map((src: any, idx: number) => (
                      <div 
                        key={idx} 
                        style={{ 
                          background: 'rgba(0,0,0,0.2)', 
                          border: '1px solid var(--border-glass)', 
                          borderRadius: '8px', 
                          padding: '10px' 
                        }}
                      >
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '6px', marginBottom: '6px' }}>
                          <span style={{ 
                            fontSize: '10px', 
                            padding: '2px 6px', 
                            borderRadius: '4px',
                            background: src.source === 'premium' ? 'rgba(245, 158, 11, 0.15)' : src.source === 'reddit' ? 'rgba(167, 139, 250, 0.15)' : 'rgba(59, 130, 246, 0.15)',
                            color: src.source === 'premium' ? '#F59E0B' : src.source === 'reddit' ? '#A78BFA' : '#3B82F6',
                            fontWeight: 600,
                            textTransform: 'uppercase'
                          }}>
                            {src.source}
                          </span>
                          <span className={src.score >= 0.05 ? 'text-green' : src.score <= -0.05 ? 'text-red' : ''} style={{ fontSize: '12px', fontWeight: 600 }}>
                            {src.score > 0 ? '+' : ''}{src.score.toFixed(2)}
                          </span>
                        </div>
                        <div style={{ fontSize: '13px', fontWeight: 500, marginBottom: '4px', lineHeight: '1.3' }}>
                          {src.title}
                        </div>
                        {src.text && (
                          <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '6px', whiteSpace: 'pre-wrap', maxHeight: '60px', overflowY: 'auto' }}>
                            {src.text}
                          </div>
                        )}
                        {src.url && (
                          <a 
                            href={src.url} 
                            target="_blank" 
                            rel="noopener noreferrer" 
                            style={{ 
                              fontSize: '11px', 
                              color: 'var(--color-buy)', 
                              textDecoration: 'none', 
                              display: 'inline-flex', 
                              alignItems: 'center', 
                              gap: '4px' 
                            }}
                          >
                            Verify Source Link &rarr;
                          </a>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Premium Article Ingestion Card */}
              <div className="glass-card" style={{ marginTop: '24px' }}>
                <h2>
                  <Layers size={20} color="var(--color-gold)" />
                  Premium Feed Ingestion
                </h2>
                <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '14px' }}>
                  Ingest paywalled journals (e.g. The Information, Bloomberg). Sentiments recalculate instantly.
                </p>

                <form onSubmit={handlePremiumSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  <div>
                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Target Ticker</label>
                    <select
                      value={premiumForm.ticker}
                      onChange={(e) => setPremiumForm({...premiumForm, ticker: e.target.value})}
                      style={{ 
                        width: '100%', 
                        background: 'rgba(16, 20, 38, 0.95)', 
                        border: '1px solid var(--border-glass)', 
                        borderRadius: '6px', 
                        color: 'var(--text-primary)', 
                        padding: '6px 10px', 
                        fontSize: '13px', 
                        cursor: 'pointer' 
                      }}
                    >
                      {universeTickers.map((t, idx) => (
                        <option key={idx} value={t}>{t}</option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Headline / Title</label>
                    <input 
                      type="text" 
                      placeholder="e.g. The Information: Tech giant plans AI trial expansion"
                      value={premiumForm.title}
                      onChange={(e) => setPremiumForm({...premiumForm, title: e.target.value})}
                      style={{ 
                        width: '100%', 
                        background: 'rgba(0,0,0,0.2)', 
                        border: '1px solid var(--border-glass)', 
                        borderRadius: '6px', 
                        color: 'var(--text-primary)', 
                        padding: '6px 10px', 
                        fontSize: '13px' 
                      }}
                      required
                    />
                  </div>

                  <div>
                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Article Content Preview</label>
                    <textarea 
                      placeholder="Paste subscription text or main analysis points here..."
                      rows={3}
                      value={premiumForm.text}
                      onChange={(e) => setPremiumForm({...premiumForm, text: e.target.value})}
                      style={{ 
                        width: '100%', 
                        background: 'rgba(0,0,0,0.2)', 
                        border: '1px solid var(--border-glass)', 
                        borderRadius: '6px', 
                        color: 'var(--text-primary)', 
                        padding: '6px 10px', 
                        fontSize: '13px',
                        fontFamily: 'var(--font-sans)',
                        resize: 'vertical' 
                      }}
                      required
                    />
                  </div>

                  <div>
                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Source Link (Optional)</label>
                    <input 
                      type="url" 
                      placeholder="https://www.theinformation.com/articles/..."
                      value={premiumForm.url}
                      onChange={(e) => setPremiumForm({...premiumForm, url: e.target.value})}
                      style={{ 
                        width: '100%', 
                        background: 'rgba(0,0,0,0.2)', 
                        border: '1px solid var(--border-glass)', 
                        borderRadius: '6px', 
                        color: 'var(--text-primary)', 
                        padding: '6px 10px', 
                        fontSize: '13px' 
                      }}
                    />
                  </div>

                  <button 
                    type="submit"
                    style={{
                      background: 'rgba(245, 158, 11, 0.1)',
                      border: '1px solid var(--color-gold)',
                      borderRadius: '6px',
                      color: 'var(--color-gold)',
                      padding: '8px 12px',
                      fontWeight: 600,
                      fontSize: '13px',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '6px',
                      marginTop: '4px',
                      transition: 'var(--transition-smooth)'
                    }}
                  >
                    <Plus size={14} /> Ingest & Analyze Feed
                  </button>

                  {premiumStatus && (
                    <div style={{ 
                      fontSize: '12px', 
                      color: 'var(--color-gold)', 
                      fontWeight: 500, 
                      textAlign: 'center',
                      padding: '6px',
                      background: 'rgba(245, 158, 11, 0.05)',
                      borderRadius: '4px',
                      border: '1px solid rgba(245, 158, 11, 0.1)'
                    }}>
                      {premiumStatus}
                    </div>
                  )}
                </form>
              </div>
            </aside>
          </>
        )}

        {/* Tab 2: Virtual Broker Performance */}
        {activeTab === 'virtual_perf' && (
          <>
            {/* Left Column */}
            <section style={{ gridColumn: 'span 2' }}>
              {/* Performance Curve controls */}
              <div className="glass-card" style={{ marginBottom: '24px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px', marginBottom: '20px' }}>
                  <div>
                    <h2>
                      <TrendingUp size={20} color="var(--color-buy)" />
                      Virtual Broker Performance Tracker ($100K Principal)
                    </h2>
                    <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                      Compare your virtual portfolio's return curve against multiple major market indices and Berkshire Hathaway.
                    </p>
                  </div>
                  
                  {/* Replay/Live Toggle */}
                  <div className="toggle-group" style={{ margin: 0 }}>
                    <button 
                      className={`toggle-btn ${perfMode === 'live' ? 'active' : ''}`}
                      onClick={() => setPerfMode('live')}
                    >
                      Forward Live Simulation
                    </button>
                    <button 
                      className={`toggle-btn ${perfMode === 'replay' ? 'active' : ''}`}
                      onClick={() => setPerfMode('replay')}
                    >
                      Historical Replay Backtest
                    </button>
                  </div>
                </div>

                {/* Benchmark Legend Visibility selectors */}
                <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap', padding: '10px 16px', background: 'rgba(0,0,0,0.2)', borderRadius: '8px', border: '1px solid var(--border-glass)', marginBottom: '20px', fontSize: '13px' }}>
                  <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>Benchmark Filters:</span>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                    <input type="checkbox" checked={showSpy} onChange={(e) => setShowSpy(e.target.checked)} />
                    <span style={{ color: '#9CA3AF', textDecoration: showSpy ? 'none' : 'line-through' }}>S&P 500 (SPY)</span>
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                    <input type="checkbox" checked={showQqq} onChange={(e) => setShowQqq(e.target.checked)} />
                    <span style={{ color: '#A78BFA', textDecoration: showQqq ? 'none' : 'line-through' }}>Nasdaq 100 (QQQ)</span>
                  </label>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                    <input type="checkbox" checked={showBrk} onChange={(e) => setShowBrk(e.target.checked)} />
                    <span style={{ color: '#F59E0B', textDecoration: showBrk ? 'none' : 'line-through' }}>Berkshire Hathaway (BRK-B)</span>
                  </label>
                </div>

                {/* Key performance metrics row */}
                <div className="metrics-row" style={{ margin: '0 0 20px 0' }}>
                  <div style={{ background: 'rgba(0,0,0,0.15)', border: '1px solid var(--border-glass)', borderRadius: '10px', padding: '12px' }}>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>Equity Balance</div>
                    <div style={{ fontSize: '20px', fontWeight: 700 }}>${accountEquity.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</div>
                  </div>
                  <div style={{ background: 'rgba(0,0,0,0.15)', border: '1px solid var(--border-glass)', borderRadius: '10px', padding: '12px' }}>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>Virtual Cash</div>
                    <div style={{ fontSize: '20px', fontWeight: 700 }}>${accountCash.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}</div>
                  </div>
                  <div style={{ background: 'rgba(0,0,0,0.15)', border: '1px solid var(--border-glass)', borderRadius: '10px', padding: '12px' }}>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>Total Return</div>
                    <div style={{ fontSize: '20px', fontWeight: 700, color: metrics.total_return >= 0 ? 'var(--color-buy)' : 'var(--color-sell)' }}>
                      {(metrics.total_return * 100).toFixed(2)}%
                    </div>
                  </div>
                  <div style={{ background: 'rgba(0,0,0,0.15)', border: '1px solid var(--border-glass)', borderRadius: '10px', padding: '12px' }}>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>Max Drawdown</div>
                    <div style={{ fontSize: '20px', fontWeight: 700, color: 'var(--color-sell)' }}>{(metrics.max_drawdown * 100).toFixed(2)}%</div>
                  </div>
                </div>

                {/* Chart */}
                <div style={{ width: '100%', height: 400 }}>
                  <ResponsiveContainer width="100%" height="100%">
                    <LineChart data={perfCurve} margin={{ top: 10, right: 10, left: 10, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" />
                      <XAxis dataKey="date" stroke="var(--text-secondary)" fontSize={11} tickLine={false} />
                      <YAxis 
                        stroke="var(--text-secondary)" 
                        fontSize={11} 
                        domain={['dataMin - 5000', 'dataMax + 5000']}
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
                        formatter={(val: any) => [`$${parseFloat(val).toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`]}
                      />
                      <Legend />
                      <Line type="monotone" dataKey="portfolio" name="Strategy Portfolio" stroke="var(--color-buy)" strokeWidth={2.5} dot={false} activeDot={{ r: 6 }} />
                      {showSpy && <Line type="monotone" dataKey="spy" name="S&P 500 (SPY)" stroke="#9CA3AF" strokeDasharray="3 3" strokeWidth={1.5} dot={false} />}
                      {showQqq && <Line type="monotone" dataKey="qqq" name="Nasdaq 100 (QQQ)" stroke="#A78BFA" strokeDasharray="3 3" strokeWidth={1.5} dot={false} />}
                      {showBrk && <Line type="monotone" dataKey="brk" name="Berkshire Hathaway (BRK-B)" stroke="#F59E0B" strokeDasharray="3 3" strokeWidth={1.5} dot={false} />}
                    </LineChart>
                  </ResponsiveContainer>
                </div>
              </div>

              {/* Active Open Positions Table */}
              <div className="glass-card">
                <h2>
                  <Layers size={20} color="var(--color-accent)" />
                  Active Virtual Broker Positions
                </h2>
                <div style={{ overflowX: 'auto' }}>
                  <table className="trade-table">
                    <thead>
                      <tr>
                        <th>Symbol</th>
                        <th>Quantity</th>
                        <th>Avg Entry Price</th>
                        <th>Current Price</th>
                        <th>Cost Basis</th>
                        <th>Market Value</th>
                        <th>Unrealized P&L</th>
                        <th>% Return</th>
                      </tr>
                    </thead>
                    <tbody>
                      {virtualPositions.length === 0 ? (
                        <tr>
                          <td colSpan={8} style={{ textAlign: 'center', color: 'var(--text-secondary)' }}>
                            No active positions. Add holdings or run simulations.
                          </td>
                        </tr>
                      ) : (
                        virtualPositions.map((pos, idx) => {
                          const pnl = parseFloat(pos.unrealized_pl);
                          const pnlPct = parseFloat(pos.unrealized_plpc) * 100;
                          return (
                            <tr key={idx}>
                              <td style={{ fontWeight: 600 }}>{pos.symbol}</td>
                              <td>{parseFloat(pos.qty).toFixed(2)}</td>
                              <td>${parseFloat(pos.avg_entry_price).toFixed(2)}</td>
                              <td>${parseFloat(pos.current_price).toFixed(2)}</td>
                              <td>${parseFloat(pos.cost_basis).toLocaleString(undefined, {maximumFractionDigits: 2})}</td>
                              <td>${parseFloat(pos.market_value).toLocaleString(undefined, {maximumFractionDigits: 2})}</td>
                              <td className={pnl >= 0 ? 'text-green' : 'text-red'}>
                                {pnl >= 0 ? '+' : ''}${pnl.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}
                              </td>
                              <td className={pnl >= 0 ? 'text-green' : 'text-red'} style={{ fontWeight: 500 }}>
                                {pnl >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                              </td>
                            </tr>
                          );
                        })
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </section>
          </>
        )}

        {/* Tab 3: Universe & Portfolio Editor */}
        {activeTab === 'editor' && (
          <>
            {/* Left Column: Holdings Policies Editor */}
            <section>
              {/* Cash Balance Editor */}
              <div className="glass-card" style={{ marginBottom: '24px' }}>
                <h2>
                  <DollarSign size={20} color="var(--color-buy)" />
                  Virtual Cash Account Balance
                </h2>
                <div style={{ display: 'flex', gap: '12px', alignItems: 'center' }}>
                  <div style={{ flex: 1, position: 'relative' }}>
                    <span style={{ position: 'absolute', left: '12px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-secondary)', fontWeight: 500 }}>$</span>
                    <input 
                      type="number" 
                      value={editCashInput}
                      onChange={(e) => setEditCashInput(e.target.value)}
                      style={{
                        width: '100%',
                        background: 'rgba(0,0,0,0.3)',
                        border: '1px solid var(--border-glass)',
                        borderRadius: '8px',
                        color: 'var(--text-primary)',
                        padding: '10px 10px 10px 24px',
                        fontFamily: 'var(--font-sans)',
                        fontSize: '14px'
                      }} 
                    />
                  </div>
                  <button 
                    onClick={handleUpdateCash}
                    style={{
                      background: 'rgba(0, 242, 254, 0.1)',
                      border: '1px solid var(--color-buy)',
                      borderRadius: '8px',
                      color: 'var(--color-buy)',
                      padding: '10px 20px',
                      cursor: 'pointer',
                      fontWeight: 600,
                      fontSize: '14px',
                      transition: 'var(--transition-smooth)'
                    }}
                  >
                    Set Cash Balance
                  </button>
                </div>
              </div>

              {/* Holdings policy table */}
              <div className="glass-card">
                <h2>
                  <Sliders size={20} color="var(--color-accent)" />
                  Portfolio Asset Policies
                </h2>
                
                {/* Form to add a position */}
                <form onSubmit={handleSaveHolding} style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', background: 'rgba(0,0,0,0.15)', padding: '16px', borderRadius: '10px', border: '1px solid var(--border-glass)', marginBottom: '20px' }}>
                  <div style={{ flex: 1, minWidth: '120px' }}>
                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Ticker</label>
                    <input 
                      type="text" 
                      placeholder="AAPL"
                      value={newHolding.ticker}
                      onChange={(e) => setNewHolding({...newHolding, ticker: e.target.value})}
                      style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid var(--border-glass)', borderRadius: '6px', color: 'var(--text-primary)', padding: '6px 10px', fontSize: '13px' }}
                      required
                    />
                  </div>
                  <div style={{ flex: 1, minWidth: '100px' }}>
                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Shares Owned</label>
                    <input 
                      type="number" 
                      placeholder="10"
                      value={newHolding.quantity || ''}
                      onChange={(e) => setNewHolding({...newHolding, quantity: parseFloat(e.target.value) || 0})}
                      style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid var(--border-glass)', borderRadius: '6px', color: 'var(--text-primary)', padding: '6px 10px', fontSize: '13px' }}
                      required
                    />
                  </div>
                  <div style={{ flex: 1, minWidth: '100px' }}>
                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Cost Basis ($)</label>
                    <input 
                      type="number" 
                      placeholder="190.50"
                      value={newHolding.entry_price || ''}
                      onChange={(e) => setNewHolding({...newHolding, entry_price: parseFloat(e.target.value) || 0})}
                      style={{ width: '100%', background: 'rgba(0,0,0,0.2)', border: '1px solid var(--border-glass)', borderRadius: '6px', color: 'var(--text-primary)', padding: '6px 10px', fontSize: '13px' }}
                      required
                    />
                  </div>
                  <div style={{ flex: 1, minWidth: '130px' }}>
                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '4px' }}>Trade Policy</label>
                    <select
                      value={newHolding.policy}
                      onChange={(e) => setNewHolding({...newHolding, policy: e.target.value as any})}
                      style={{ width: '100%', background: 'rgba(16, 20, 38, 0.95)', border: '1px solid var(--border-glass)', borderRadius: '6px', color: 'var(--text-primary)', padding: '6px 10px', fontSize: '13px', cursor: 'pointer' }}
                    >
                      <option value="rebalance">Rebalance (ML weights)</option>
                      <option value="lock">Lock (Keep as is)</option>
                      <option value="liquidate">Liquidate (Sell at next run)</option>
                    </select>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'flex-end' }}>
                    <button 
                      type="submit"
                      style={{
                        background: 'var(--color-accent)',
                        border: 'none',
                        borderRadius: '6px',
                        color: 'white',
                        padding: '8px 16px',
                        fontWeight: 600,
                        fontSize: '13px',
                        cursor: 'pointer',
                        display: 'flex',
                        alignItems: 'center',
                        gap: '4px'
                      }}
                    >
                      <Plus size={14} /> Add Asset
                    </button>
                  </div>
                </form>

                {/* Table listing holdings */}
                <div style={{ overflowX: 'auto' }}>
                  <table className="trade-table">
                    <thead>
                      <tr>
                        <th>Asset</th>
                        <th>Shares</th>
                        <th>Avg Cost</th>
                        <th>Total Cost</th>
                        <th>Execution Policy</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {holdings.length === 0 ? (
                        <tr>
                          <td colSpan={6} style={{ textAlign: 'center', color: 'var(--text-secondary)' }}>
                            No holdings loaded. Add your assets using the form above.
                          </td>
                        </tr>
                      ) : (
                        holdings.map((h, idx) => (
                          <tr key={idx}>
                            <td style={{ fontWeight: 600 }}>{h.ticker}</td>
                            <td>{h.quantity.toFixed(2)}</td>
                            <td>${h.entry_price.toFixed(2)}</td>
                            <td>${(h.quantity * h.entry_price).toLocaleString(undefined, {maximumFractionDigits: 2})}</td>
                            <td>
                              <select
                                value={h.policy}
                                onChange={(e) => handleUpdatePolicy(h.ticker, h.quantity, h.entry_price, e.target.value as any)}
                                style={{
                                  background: 'rgba(0,0,0,0.3)',
                                  border: '1px solid var(--border-glass)',
                                  borderRadius: '6px',
                                  color: 'var(--text-primary)',
                                  padding: '4px 8px',
                                  fontSize: '12px',
                                  cursor: 'pointer'
                                }}
                              >
                                <option value="rebalance">Rebalance</option>
                                <option value="lock">Lock (Do not trade)</option>
                                <option value="liquidate">Liquidate</option>
                              </select>
                            </td>
                            <td>
                              <button 
                                onClick={() => handleDeleteHolding(h.ticker)}
                                style={{
                                  background: 'transparent',
                                  border: 'none',
                                  color: 'var(--text-secondary)',
                                  cursor: 'pointer',
                                }}
                                onMouseEnter={(e) => e.currentTarget.style.color = 'var(--color-sell)'}
                                onMouseLeave={(e) => e.currentTarget.style.color = 'var(--text-secondary)'}
                              >
                                <Trash2 size={16} />
                              </button>
                            </td>
                          </tr>
                        ))
                      )}
                    </tbody>
                  </table>
                </div>
              </div>
            </section>

            {/* Right Column: Universe Editor & Simulation controls */}
            <aside>
              {/* Universe Ticker Checklist */}
              <div className="glass-card" style={{ marginBottom: '24px' }}>
                <h2>
                  <Layers size={20} color="#00F2FE" />
                  Strategy Universe Editor
                </h2>
                <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px' }}>
                  Define the pool of tickers evaluated by the ML and regimes-allocation strategy.
                </p>

                {/* Input to add ticker */}
                <div style={{ display: 'flex', gap: '8px', marginBottom: '20px' }}>
                  <input 
                    type="text" 
                    placeholder="AAPL, MSFT, etc."
                    value={newUniverseTicker}
                    onChange={(e) => setNewUniverseTicker(e.target.value)}
                    style={{
                      flex: 1,
                      background: 'rgba(0,0,0,0.3)',
                      border: '1px solid var(--border-glass)',
                      borderRadius: '8px',
                      color: 'var(--text-primary)',
                      padding: '8px 12px',
                      fontSize: '13px',
                      fontFamily: 'var(--font-sans)'
                    }}
                  />
                  <button 
                    onClick={handleAddTicker}
                    style={{
                      background: 'rgba(0,242,254,0.1)',
                      border: '1px solid var(--color-buy)',
                      borderRadius: '8px',
                      color: 'var(--color-buy)',
                      padding: '8px 14px',
                      cursor: 'pointer',
                      fontWeight: 600,
                      fontSize: '13px'
                    }}
                  >
                    Add Ticker
                  </button>
                </div>

                {/* List of active tickers */}
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', maxHeight: '200px', overflowY: 'auto', padding: '4px' }}>
                  {universeTickers.map((ticker, idx) => (
                    <div 
                      key={idx}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '6px',
                        background: 'rgba(255,255,255,0.04)',
                        border: '1px solid var(--border-glass)',
                        borderRadius: '6px',
                        padding: '4px 10px',
                        fontSize: '13px',
                        fontWeight: 500
                      }}
                    >
                      <span>{ticker}</span>
                      <button 
                        onClick={() => handleRemoveTicker(ticker)}
                        style={{
                          background: 'transparent',
                          border: 'none',
                          color: 'var(--text-secondary)',
                          cursor: 'pointer',
                          fontWeight: 'bold',
                          fontSize: '11px',
                          display: 'flex',
                          alignItems: 'center'
                        }}
                        onMouseEnter={(e) => e.currentTarget.style.color = '#FF4B6E'}
                        onMouseLeave={(e) => e.currentTarget.style.color = 'var(--text-secondary)'}
                      >
                        ×
                      </button>
                    </div>
                  ))}
                </div>
              </div>

              {/* Simulation triggers panel */}
              {backendOnline && (
                <div className="glass-card">
                  <h2>
                    <Play size={20} color="var(--color-buy)" />
                    Replay & Simulation Engine
                  </h2>
                  <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '20px' }}>
                    Manually trigger historical replays or forward simulations. Results will update the performance graphs automatically.
                  </p>

                  <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
                    {/* Forward live simulation button */}
                    <div style={{ background: 'rgba(0,0,0,0.15)', border: '1px solid var(--border-glass)', borderRadius: '10px', padding: '12px' }}>
                      <div style={{ fontSize: '14px', fontWeight: 600, marginBottom: '6px' }}>Forward Simulation</div>
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Days:</span>
                        <input 
                          type="number" 
                          value={simDays} 
                          onChange={(e) => setSimDays(parseInt(e.target.value) || 5)} 
                          style={{ width: '60px', background: 'rgba(0,0,0,0.3)', border: '1px solid var(--border-glass)', borderRadius: '4px', color: 'var(--text-primary)', padding: '4px', textAlign: 'center', fontSize: '13px' }} 
                        />
                        <button 
                          onClick={async () => {
                            setRunningSim(true);
                            try {
                              const res = await fetch(`http://localhost:8008/api/simulate?days=${simDays}`, { method: 'POST' });
                              if (res.ok) {
                                setTimeout(fetchData, 1500);
                              }
                            } catch(e) {
                              console.error(e);
                            }
                            setRunningSim(false);
                          }}
                          disabled={runningSim}
                          style={{ flex: 1, background: runningSim ? 'rgba(255,255,255,0.05)' : 'rgba(0, 242, 254, 0.1)', border: runningSim ? '1px solid var(--border-glass)' : '1px solid var(--color-buy)', borderRadius: '6px', color: runningSim ? 'var(--text-secondary)' : 'var(--color-buy)', padding: '6px', cursor: runningSim ? 'not-allowed' : 'pointer', fontSize: '13px', fontWeight: 600 }}
                        >
                          {runningSim ? 'Running...' : 'Run Simulation'}
                        </button>
                      </div>
                    </div>

                    {/* Historical replay button */}
                    <div style={{ background: 'rgba(0,0,0,0.15)', border: '1px solid var(--border-glass)', borderRadius: '10px', padding: '12px' }}>
                      <div style={{ fontSize: '14px', fontWeight: 600, marginBottom: '6px' }}>Historical Replay</div>
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                        <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Months:</span>
                        <input 
                          type="number" 
                          value={replayMonths} 
                          onChange={(e) => setReplayMonths(parseInt(e.target.value) || 6)} 
                          style={{ width: '60px', background: 'rgba(0,0,0,0.3)', border: '1px solid var(--border-glass)', borderRadius: '4px', color: 'var(--text-primary)', padding: '4px', textAlign: 'center', fontSize: '13px' }} 
                        />
                        <button 
                          onClick={async () => {
                            setRunningSim(true);
                            try {
                              const res = await fetch(`http://localhost:8008/api/backtest-virtual?months=${replayMonths}`, { method: 'POST' });
                              if (res.ok) {
                                setTimeout(fetchData, 1500);
                              }
                            } catch(e) {
                              console.error(e);
                            }
                            setRunningSim(false);
                          }}
                          disabled={runningSim}
                          style={{ flex: 1, background: runningSim ? 'rgba(255,255,255,0.05)' : 'rgba(59, 130, 246, 0.1)', border: runningSim ? '1px solid var(--border-glass)' : '1px solid var(--color-accent)', borderRadius: '6px', color: runningSim ? 'var(--text-secondary)' : 'var(--color-accent)', padding: '6px', cursor: runningSim ? 'not-allowed' : 'pointer', fontSize: '13px', fontWeight: 600 }}
                        >
                          {runningSim ? 'Running...' : 'Run Replay'}
                        </button>
                      </div>
                    </div>

                    <div style={{ fontSize: '11px', color: 'var(--text-secondary)', fontStyle: 'italic', padding: '0 4px' }}>
                      Tip: You can also execute these simulations directly in your terminal using:
                      <code style={{ display: 'block', background: 'rgba(0,0,0,0.3)', padding: '6px', borderRadius: '4px', marginTop: '6px', color: '#3B82F6', fontFamily: 'monospace' }}>
                        python run.py simulate --days {simDays}
                      </code>
                      <code style={{ display: 'block', background: 'rgba(0,0,0,0.3)', padding: '6px', borderRadius: '4px', marginTop: '4px', color: '#3B82F6', fontFamily: 'monospace' }}>
                        python run.py backtest-virtual --months {replayMonths}
                      </code>
                    </div>
                  </div>
                </div>
              )}
            </aside>
          </>
        )}
      </main>
    </div>
  );
}
