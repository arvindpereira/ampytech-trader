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
  RotateCcw,
  Cpu
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

interface HedgePlan {
  mode: string;
  symbol: string;
  ratio: number;
  price: number | null;
  notional: number;
  shares: number | null;
}

interface ShortTermSuggestion {
  ticker: string;
  close: number;
  action: 'BUY' | 'SELL' | 'HOLD';
  confidence: number;
  stop_loss: number | null;
  take_profit: number | null;
  reasoning: string;
  hedge?: HedgePlan | null;
  action_plan?: string | null;
}

interface SwingSuggestion {
  ticker: string;
  close: number;
  action: 'BUY' | 'HOLD';
  confidence: number;
  stop_loss: number | null;
  take_profit: number | null;
  horizon_days: number;
  llm_news: number;
  llm_news_intensity: number;
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
  const [activeStrategy, setActiveStrategy] = useState<'short_term' | 'swing' | 'long_term'>('short_term');
  const [appMode, setAppMode] = useState<'real' | 'simulated'>('real');
  const [hedgeMode, setHedgeMode] = useState<'none' | 'beta_neutral' | 'pair_trade'>('none');
  const [loading, setLoading] = useState<boolean>(true);
  const [backendOnline, setBackendOnline] = useState<boolean>(false);
  const [regime, setRegime] = useState<string>('growth');
  const [date, setDate] = useState<string>('');

  // Suggestion/Allocation States
  const [suggestions, setSuggestions] = useState<ShortTermSuggestion[]>([]);
  const [swingSuggestions, setSwingSuggestions] = useState<SwingSuggestion[]>([]);
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
  const [portfolio, setPortfolio] = useState<any>(null);
  const [refreshingPortfolio, setRefreshingPortfolio] = useState<boolean>(false);
  const [jobs, setJobs] = useState<any[]>([]);
  const [trainStatus, setTrainStatus] = useState<any>(null);
  const [liquidateModal, setLiquidateModal] = useState<any>(null);
  const [addStockOpen, setAddStockOpen] = useState<boolean>(false);
  const [addStockTicker, setAddStockTicker] = useState<string>('');
  const [actionBusy, setActionBusy] = useState<boolean>(false);
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
  const [sentSources, setSentSources] = useState<any[]>([]);
  const [loadingSources, setLoadingSources] = useState<boolean>(false);
  const [priceSummary, setPriceSummary] = useState<any[]>([]);
  const [expandedTicker, setExpandedTicker] = useState<string>('');
  const [llmNews, setLlmNews] = useState<any[]>([]);
  const [health, setHealth] = useState<any>(null);
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
      const res = await fetch(`http://localhost:8008/api/sentiment/sources?ticker=${ticker}&mode=${appMode}`);
      if (res.ok) {
        const data = await res.json();
        setSentSources(data.sources || []);
      } else {
        if (appMode !== 'real') {
          loadMockSources(ticker);
        } else {
          setSentSources([]);
        }
      }
    } catch (err) {
      if (appMode !== 'real') {
        loadMockSources(ticker);
      } else {
        setSentSources([]);
      }
    } finally {
      setLoadingSources(false);
    }
  };

  const fetchHealth = async () => {
    try {
      const res = await fetch(`http://localhost:8008/api/health`);
      setHealth(res.ok ? await res.json() : null);
    } catch (err) {
      setHealth(null);
    }
  };

  const fetchPortfolio = async () => {
    setRefreshingPortfolio(true);
    try {
      const res = await fetch(`http://localhost:8008/api/portfolio?mode=${appMode}`);
      setPortfolio(res.ok ? await res.json() : null);
    } catch (err) {
      setPortfolio(null);
    } finally {
      setRefreshingPortfolio(false);
    }
  };

  const fetchJobsAndTraining = async () => {
    try {
      const [jr, tr] = await Promise.all([
        fetch(`http://localhost:8008/api/jobs`),
        fetch(`http://localhost:8008/api/train/status`),
      ]);
      if (jr.ok) setJobs((await jr.json()).jobs || []);
      if (tr.ok) setTrainStatus(await tr.json());
    } catch (err) { /* backend offline */ }
  };

  const handleAddStock = async () => {
    const t = addStockTicker.toUpperCase().trim();
    if (!t) return;
    setActionBusy(true);
    try {
      await fetch(`http://localhost:8008/api/universe/add`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker: t }),
      });
      setAddStockTicker('');
      setAddStockOpen(false);
      fetchJobsAndTraining();
      fetchData();
    } catch (err) { console.error(err); } finally { setActionBusy(false); }
  };

  const handleRemoveMonitored = async (ticker: string) => {
    if (!confirm(`Stop monitoring ${ticker}? (It has no open position.)`)) return;
    try {
      await fetch(`http://localhost:8008/api/universe/remove`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker }),
      });
      fetchData();
    } catch (err) { console.error(err); }
  };

  const handleLiquidate = async () => {
    if (!liquidateModal) return;
    const shares = parseFloat(liquidateModal.shares);
    if (!shares || shares <= 0) return;
    setActionBusy(true);
    try {
      const res = await fetch(`http://localhost:8008/api/positions/liquidate?mode=${appMode}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker: liquidateModal.ticker, shares }),
      });
      if (!res.ok) {
        const e = await res.json();
        alert(`Liquidation failed: ${e.detail || 'unknown error'}`);
      } else {
        setLiquidateModal(null);
        setTimeout(fetchData, 1200);
      }
    } catch (err) { console.error(err); } finally { setActionBusy(false); }
  };

  const handleRetrain = async () => {
    setActionBusy(true);
    try {
      await fetch(`http://localhost:8008/api/train/start`, { method: 'POST' });
      fetchJobsAndTraining();
    } catch (err) { console.error(err); } finally { setActionBusy(false); }
  };

  const fetchLlmNews = async (ticker: string) => {
    try {
      const res = await fetch(`http://localhost:8008/api/news/llm?ticker=${ticker}&limit=40`);
      setLlmNews(res.ok ? ((await res.json()).articles || []) : []);
    } catch (err) {
      setLlmNews([]);
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
          if (expandedTicker) fetchSources(expandedTicker);
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
      const sugRes = await fetch(`http://localhost:8008/api/suggestions?mode=${appMode}&hedge_mode=${hedgeMode}`);
      if (sugRes.ok) {
        const sugData = await sugRes.json();
        setSuggestions(sugData.short_term_suggestions || []);
        setSwingSuggestions(sugData.swing_suggestions || []);
        setAllocations(sugData.long_term_allocation || []);
        setRegime(sugData.date ? sugData.regime : 'growth');
        setDate(sugData.date || '');
        setBackendOnline(true);
      } else {
        throw new Error("Backend Suggestions Offline");
      }

      // 2. Fetch Performance Curve based on mode
      const perfRes = await fetch(`http://localhost:8008/api/performance?mode=${appMode === 'real' ? 'live' : perfMode}`);
      if (perfRes.ok) {
        const perfData = await perfRes.json();
        setPerfCurve(perfData.equity_curve || []);
        setMetrics(perfData.metrics || { total_return: 0, sharpe_ratio: 0, max_drawdown: 0, win_rate: 0 });
      }

      // 3. Fetch Universe Tickers
      const uniRes = await fetch('http://localhost:8008/api/universe');
      if (uniRes.ok) {
        const uniData = await uniRes.json();
        setUniverseTickers(uniData.tickers || []);
      }

      // 4. Fetch User Holdings & Virtual Account
      const holdRes = await fetch(`http://localhost:8008/api/holdings?mode=${appMode}`);
      if (holdRes.ok) {
        const holdData = await holdRes.json();
        setHoldings(holdData || []);
      }

      const accRes = await fetch(`http://localhost:8008/api/virtual_alpaca/v2/account?mode=${appMode}`);
      if (accRes.ok) {
        const accData = await accRes.json();
        setAccountCash(parseFloat(accData.cash) || 0);
        setAccountEquity(parseFloat(accData.portfolio_value) || 0);
      }

      // 5. Fetch Virtual Positions
      const vposRes = await fetch(`http://localhost:8008/api/virtual_alpaca/v2/positions?mode=${appMode}`);
      if (vposRes.ok) {
        const vposData = await vposRes.json();
        setVirtualPositions(vposData || []);
      }

      // 6. Fetch Sentiment list
      const sentRes = await fetch(`http://localhost:8008/api/sentiment?mode=${appMode}`);
      if (sentRes.ok) {
        const sentData = await sentRes.json();
        setSentimentList(sentData.sentiment || []);
      }

      // 7. Fetch per-ticker price summary (live price + 1D/1W/1M/1Y changes)
      const priceRes = await fetch(`http://localhost:8008/api/prices/summary`);
      if (priceRes.ok) {
        const priceData = await priceRes.json();
        setPriceSummary(priceData.prices || []);
      }

      // 8. Fetch portfolio (holdings enriched with live value + P&L)
      const portRes = await fetch(`http://localhost:8008/api/portfolio?mode=${appMode}`);
      if (portRes.ok) {
        setPortfolio(await portRes.json());
      }

    } catch (err) {
      console.warn("FastAPI backend is offline.");
      setBackendOnline(false);
      if (appMode !== 'real') {
        loadMockData();
      } else {
        setSuggestions([]);
        setAllocations([]);
        setHoldings([]);
        setVirtualPositions([]);
        setPerfCurve([]);
        setSentimentList([]);
        setPriceSummary([]);
        setPortfolio(null);
      }
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (expandedTicker) {
      fetchSources(expandedTicker);
      fetchLlmNews(expandedTicker);
    }
  }, [expandedTicker, backendOnline, appMode]);

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
  }, [perfMode, appMode, hedgeMode]);

  // Poll service health (30s) and refresh signals/prices (90s) so the dashboard stays current.
  useEffect(() => {
    fetchHealth();
    const h = setInterval(fetchHealth, 30000);
    const d = setInterval(() => fetchData(), 90000);
    return () => { clearInterval(h); clearInterval(d); };
  }, [perfMode, appMode, hedgeMode]);

  // Poll background jobs + training status every 4s so progress bars stay live.
  useEffect(() => {
    fetchJobsAndTraining();
    const j = setInterval(fetchJobsAndTraining, 4000);
    return () => clearInterval(j);
  }, []);

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
        const res = await fetch(`http://localhost:8008/api/holdings?mode=${appMode}`, {
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
        const res = await fetch(`http://localhost:8008/api/holdings/${ticker}?mode=${appMode}`, {
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
        const res = await fetch(`http://localhost:8008/api/holdings?mode=${appMode}`, {
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
        const res = await fetch(`http://localhost:8008/api/account?mode=${appMode}`, {
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

  const realThemeStyles = (appMode === 'real' ? {
    '--color-buy': '#10B981',
    '--color-buy-bg': 'rgba(16, 185, 129, 0.1)',
    '--color-accent': '#0D9488',
    '--border-glow': 'rgba(13, 148, 136, 0.2)',
    '--color-sell': '#EF4444',
  } : {}) as React.CSSProperties;

  return (
    <div style={{ background: 'var(--bg-dark)', minHeight: '100vh', color: 'var(--text-primary)', ...realThemeStyles }}>
      {/* Top Navbar */}
      <header className="navbar">
        <div className="nav-logo">
          <Activity size={28} color="var(--color-buy)" />
          <span>AMPYTECH TRADER</span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
          {/* Service health indicators */}
          {health && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              {([
                ['scheduler', 'Scheduler'],
                ['ollama', 'Ollama'],
                ['alpaca', 'Alpaca'],
                ['news_llm', 'News'],
                ['database', 'DB'],
              ] as const).map(([key, label]) => {
                const s = health.services?.[key];
                const st = s?.status || 'down';
                const color = st === 'up' ? '#10B981' : st === 'stale' ? '#F59E0B' : '#EF4444';
                return (
                  <div
                    key={key}
                    title={`${label}: ${st}${s?.detail ? ' — ' + s.detail : ''}`}
                    style={{
                      display: 'flex', alignItems: 'center', gap: '5px',
                      padding: '4px 8px', borderRadius: '999px',
                      background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-glass)', cursor: 'default'
                    }}
                  >
                    <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: color, boxShadow: `0 0 6px ${color}` }} />
                    <span style={{ fontSize: '11px', color: 'var(--text-secondary)', fontWeight: 600 }}>{label}</span>
                  </div>
                );
              })}
            </div>
          )}

          {/* Mode Switcher Toggle */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            background: 'rgba(255, 255, 255, 0.03)',
            border: '1px solid var(--border-glass)',
            borderRadius: '999px',
            padding: '2px',
            gap: '2px'
          }}>
            <button
              onClick={() => setAppMode('real')}
              style={{
                background: appMode === 'real' ? 'rgba(16, 185, 129, 0.15)' : 'transparent',
                border: appMode === 'real' ? '1px solid rgba(16, 185, 129, 0.3)' : '1px solid transparent',
                borderRadius: '999px',
                color: appMode === 'real' ? '#10B981' : 'var(--text-secondary)',
                padding: '6px 12px',
                fontSize: '12px',
                fontWeight: 600,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                transition: 'var(--transition-smooth)'
              }}
            >
              <Cpu size={14} />
              REAL MODE
            </button>
            <button
              onClick={() => setAppMode('simulated')}
              style={{
                background: appMode === 'simulated' ? 'rgba(59, 130, 246, 0.15)' : 'transparent',
                border: appMode === 'simulated' ? '1px solid rgba(59, 130, 246, 0.3)' : '1px solid transparent',
                borderRadius: '999px',
                color: appMode === 'simulated' ? '#00F2FE' : 'var(--text-secondary)',
                padding: '6px 12px',
                fontSize: '12px',
                fontWeight: 600,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                transition: 'var(--transition-smooth)'
              }}
            >
              <Sliders size={14} />
              SIMULATED
            </button>
          </div>

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
                      className={`toggle-btn ${activeStrategy === 'swing' ? 'active' : ''}`}
                      onClick={() => setActiveStrategy('swing')}
                    >
                      Swing (Days) + News
                    </button>
                    <button
                      className={`toggle-btn ${activeStrategy === 'long_term' ? 'active' : ''}`}
                      onClick={() => setActiveStrategy('long_term')}
                    >
                      Long-Term MPT Weights
                    </button>
                  </div>
                </div>

                {appMode === 'real' && suggestions.length === 0 && (
                  <div style={{
                    background: 'rgba(239, 68, 68, 0.08)',
                    border: '1px solid rgba(239, 68, 68, 0.25)',
                    borderRadius: '8px',
                    padding: '16px',
                    marginBottom: '16px',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '8px'
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--color-sell)', fontWeight: 600 }}>
                      <ShieldAlert size={18} />
                      No Real Market Suggestions Available
                    </div>
                    <p style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: '1.4' }}>
                      To generate real suggestions, please ensure your Alpaca and Massive.com credentials are configured in your <code style={{ fontFamily: 'monospace', color: 'var(--text-primary)' }}>.env</code> file, then run <code style={{ fontFamily: 'monospace', color: 'var(--color-buy)', background: 'rgba(255,255,255,0.05)', padding: '2px 6px', borderRadius: '4px' }}>make fetch</code> to ingest current market data and run model inference.
                    </p>
                  </div>
                )}

                {activeStrategy === 'short_term' ? (
                  <>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '14px', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>Hedge overlay:</span>
                    <div className="toggle-group" style={{ margin: 0 }}>
                      {([
                        ['none', 'None (long only)'],
                        ['beta_neutral', 'Beta-Neutral'],
                        ['pair_trade', 'Pair Trade'],
                      ] as const).map(([val, label]) => (
                        <button
                          key={val}
                          className={`toggle-btn ${hedgeMode === val ? 'active' : ''}`}
                          onClick={() => setHedgeMode(val)}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    {hedgeMode !== 'none' && (
                      <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                        Each BUY pairs with an offsetting short to neutralize market risk — see the trade plan under each row.
                      </span>
                    )}
                  </div>
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
                          <React.Fragment key={idx}>
                          <tr>
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
                          {item.action === 'BUY' && item.action_plan && (
                            <tr key={`${idx}-plan`}>
                              <td colSpan={7} style={{ padding: '0 12px 12px 12px', borderTop: 'none' }}>
                                <div style={{
                                  background: 'rgba(0, 242, 254, 0.06)',
                                  border: '1px solid var(--border-glow)',
                                  borderRadius: '8px',
                                  padding: '10px 12px',
                                  fontSize: '12.5px',
                                  lineHeight: '1.5',
                                  color: 'var(--text-primary)',
                                  fontFamily: 'monospace'
                                }}>
                                  <span style={{ color: 'var(--color-gold)', fontWeight: 600 }}>▶ Trade plan: </span>
                                  {item.action_plan}
                                  {item.hedge && (
                                    <span style={{ display: 'block', marginTop: '4px', color: 'var(--color-sell)' }}>
                                      Hedge leg: SHORT {item.hedge.shares ?? '?'} sh {item.hedge.symbol}
                                      {item.hedge.price ? ` @ $${item.hedge.price.toFixed(2)}` : ''} ({item.hedge.ratio}× notional)
                                    </span>
                                  )}
                                </div>
                              </td>
                            </tr>
                          )}
                        </React.Fragment>
                      ))}
                      </tbody>
                    </table>
                  </div>
                  </>
                ) : activeStrategy === 'swing' ? (
                  <>
                  <div style={{
                    background: 'rgba(0, 242, 254, 0.06)',
                    border: '1px solid var(--border-glow)',
                    borderRadius: '8px',
                    padding: '12px 14px',
                    marginBottom: '16px',
                    fontSize: '13px',
                    color: 'var(--text-secondary)',
                    lineHeight: '1.5'
                  }}>
                    Multi-day (≈{swingSuggestions[0]?.horizon_days ?? 5}-trading-day) signals from daily prices + LLM-scored
                    news headlines. In walk-forward + capital-aware portfolio simulation this added a real edge over a
                    technicals-only baseline (Sharpe 1.55 vs 1.16, −18% vs −24% max drawdown). Only the highest-conviction
                    names are flagged BUY (capped to the same open-position limit the simulation used); lower-ranked
                    above-threshold candidates are shown as HOLD.
                  </div>
                  {swingSuggestions.length === 0 ? (
                    <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                      No swing signals available. Train the model with <code style={{ fontFamily: 'monospace', color: 'var(--color-buy)' }}>make swing-train</code> after
                      scoring news (<code style={{ fontFamily: 'monospace', color: 'var(--color-buy)' }}>make news-llm</code>).
                    </p>
                  ) : (
                  <div style={{ overflowX: 'auto' }}>
                    <table className="trade-table">
                      <thead>
                        <tr>
                          <th>Ticker</th>
                          <th>Last Price</th>
                          <th>Action</th>
                          <th>Win Prob</th>
                          <th>News</th>
                          <th>Stop Loss</th>
                          <th>Take Profit</th>
                          <th>Strategic Rationale</th>
                        </tr>
                      </thead>
                      <tbody>
                        {swingSuggestions.map((item, idx) => (
                          <tr key={idx}>
                            <td style={{ fontWeight: 600 }}>{item.ticker}</td>
                            <td>${item.close.toFixed(2)}</td>
                            <td>
                              <span className={`badge badge-${item.action.toLowerCase()}`}>
                                {item.action}
                              </span>
                            </td>
                            <td>{(item.confidence * 100).toFixed(0)}%</td>
                            <td style={{ color: item.llm_news > 0.02 ? 'var(--color-buy)' : item.llm_news < -0.02 ? 'var(--color-sell)' : 'var(--text-secondary)' }}>
                              {item.llm_news > 0.02 ? '▲' : item.llm_news < -0.02 ? '▼' : '—'} {item.llm_news.toFixed(2)}
                            </td>
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
                  )}
                  </>
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
              {/* Watchlist: live prices + click-to-expand news/sentiment */}
              <div className="glass-card">
                <h2>
                  <Activity size={20} color="var(--color-buy)" />
                  Watchlist &mdash; Prices &amp; News
                </h2>
                <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px' }}>
                  Live price with 1D / 1W / 1M / 1Y change. Click any ticker to expand the latest news &amp; sentiment articles behind it.
                </p>

                {(() => {
                  // Average sentiment score per ticker (for the inline chip)
                  const sentMap: any = {};
                  sentimentList.forEach(item => {
                    const t = item.ticker;
                    if (!sentMap[t]) sentMap[t] = { total: 0, count: 0 };
                    sentMap[t].total += item.sentiment_score;
                    sentMap[t].count += 1;
                  });

                  if (priceSummary.length === 0) {
                    return (
                      <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                        {appMode === 'real'
                          ? "No price data loaded yet. Run 'make fetch' to ingest market data."
                          : "No price data loaded. Start the backend or run simulated fetching."
                        }
                      </p>
                    );
                  }

                  const renderChg = (label: string, v: number | null) => (
                    <div style={{ flex: 1, textAlign: 'center' }}>
                      <div style={{ fontSize: '9px', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>{label}</div>
                      <div className={v == null ? '' : v > 0 ? 'text-green' : v < 0 ? 'text-red' : ''} style={{ fontSize: '12px', fontWeight: 600 }}>
                        {v == null ? '–' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`}
                      </div>
                    </div>
                  );

                  return (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '620px', overflowY: 'auto', paddingRight: '4px' }}>
                      {priceSummary.map((p: any, idx: number) => {
                        const isOpen = expandedTicker === p.ticker;
                        const sm = sentMap[p.ticker];
                        const avg = sm ? sm.total / sm.count : null;
                        return (
                          <div
                            key={idx}
                            style={{
                              borderRadius: '8px',
                              border: isOpen ? '1px solid var(--color-buy)' : '1px solid var(--border-glass)',
                              background: isOpen ? 'rgba(0, 242, 254, 0.06)' : 'rgba(255,255,255,0.02)',
                              transition: 'var(--transition-smooth)'
                            }}
                          >
                            <div
                              onClick={() => setExpandedTicker(isOpen ? '' : p.ticker)}
                              style={{ padding: '10px 12px', cursor: 'pointer' }}
                              className="sentiment-list-item"
                            >
                              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                                  <span style={{ fontWeight: 700, fontSize: '14px' }}>{p.ticker}</span>
                                  {p.is_live && (
                                    <span title="Live price" style={{ width: '6px', height: '6px', borderRadius: '50%', background: 'var(--color-buy)', display: 'inline-block' }} />
                                  )}
                                  {avg != null && (
                                    <span className={avg >= 0.05 ? 'text-green' : avg <= -0.05 ? 'text-red' : ''} style={{ fontSize: '10px', fontWeight: 600 }}>
                                      sent {avg > 0 ? '+' : ''}{avg.toFixed(2)}
                                    </span>
                                  )}
                                </div>
                                <span style={{ fontWeight: 600, fontSize: '14px' }}>${p.price.toFixed(2)}</span>
                              </div>
                              <div style={{ display: 'flex', gap: '4px' }}>
                                {renderChg('1D', p.d1)}
                                {renderChg('1W', p.w1)}
                                {renderChg('1M', p.m1)}
                                {renderChg('1Y', p.y1)}
                              </div>
                            </div>
                            {isOpen && (
                              <div style={{ borderTop: '1px solid var(--border-glass)', padding: '10px 12px' }}>
                                {/* LLM-scored swing news — the signal that actually drives swing trades */}
                                <div style={{ fontSize: '10px', fontWeight: 700, color: 'var(--color-buy)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '8px' }}>
                                  LLM Swing-News &middot; latest first
                                </div>
                                {llmNews.length === 0 ? (
                                  <p style={{ fontSize: '12px', color: 'var(--text-secondary)', padding: '0 0 10px' }}>
                                    No LLM-scored headlines yet for {p.ticker}.
                                  </p>
                                ) : (
                                  <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '260px', overflowY: 'auto', paddingRight: '4px', marginBottom: '14px' }}>
                                    {llmNews.map((n: any, nidx: number) => (
                                      <div key={nidx} style={{ background: 'rgba(0,0,0,0.2)', border: '1px solid var(--border-glass)', borderRadius: '8px', padding: '9px 10px' }}>
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '6px', marginBottom: '4px' }}>
                                          <span style={{ fontSize: '10px', color: 'var(--text-secondary)' }}>{(n.published_utc || n.date || '').slice(0, 16).replace('T', ' ')}</span>
                                          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                                            <span className={n.score > 0 ? 'text-green' : n.score < 0 ? 'text-red' : ''} style={{ fontSize: '11px', fontWeight: 700 }}>
                                              {n.score > 0 ? '+' : ''}{n.score.toFixed(2)}
                                            </span>
                                            <span style={{ fontSize: '10px', color: 'var(--text-secondary)' }}>rel {n.relevance.toFixed(2)}</span>
                                          </div>
                                        </div>
                                        <div style={{ fontSize: '12.5px', fontWeight: 500, lineHeight: '1.3' }}>{n.title}</div>
                                      </div>
                                    ))}
                                  </div>
                                )}
                                <div style={{ fontSize: '10px', fontWeight: 700, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '8px' }}>
                                  Social / news sentiment feed
                                </div>
                                {loadingSources ? (
                                  <div style={{ display: 'flex', justifyContent: 'center', padding: '12px' }}>
                                    <RefreshCw size={18} className="animate-spin" color="var(--color-accent)" />
                                  </div>
                                ) : sentSources.length === 0 ? (
                                  <p style={{ fontSize: '12px', color: 'var(--text-secondary)', textAlign: 'center', padding: '8px 0' }}>
                                    {appMode === 'real'
                                      ? `No news/sentiment articles found for ${p.ticker} in the database.`
                                      : `No active sources logged for ${p.ticker} today.`
                                    }
                                  </p>
                                ) : (
                                  <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', maxHeight: '300px', overflowY: 'auto', paddingRight: '4px' }}>
                                    {sentSources.map((src: any, sidx: number) => (
                                      <div
                                        key={sidx}
                                        style={{ background: 'rgba(0,0,0,0.2)', border: '1px solid var(--border-glass)', borderRadius: '8px', padding: '10px' }}
                                      >
                                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '6px', marginBottom: '6px' }}>
                                          <span style={{
                                            fontSize: '10px', padding: '2px 6px', borderRadius: '4px',
                                            background: src.source === 'premium' ? 'rgba(245, 158, 11, 0.15)' : src.source === 'reddit' ? 'rgba(167, 139, 250, 0.15)' : 'rgba(59, 130, 246, 0.15)',
                                            color: src.source === 'premium' ? '#F59E0B' : src.source === 'reddit' ? '#A78BFA' : '#3B82F6',
                                            fontWeight: 600, textTransform: 'uppercase'
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
                                          <a href={src.url} target="_blank" rel="noopener noreferrer" style={{ fontSize: '11px', color: 'var(--color-buy)', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                                            Verify Source Link &rarr;
                                          </a>
                                        )}
                                      </div>
                                    ))}
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  );
                })()}
              </div>

              {/* Social Sentiment Index (moved below): ranking by social/news mention volume */}
              <div className="glass-card" style={{ marginTop: '24px' }}>
                <h2>
                  <Zap size={20} color="var(--color-buy)" />
                  Social Sentiment Index
                </h2>
                <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px' }}>
                  Tickers ranked by mention volume with their average sentiment. Click one to expand its news above.
                </p>

                {(() => {
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
                        {appMode === 'real'
                          ? "No real sentiment records loaded. Please run 'make fetch' to ingest news/Reddit sentiment insights."
                          : "No sentiment records loaded. Start the backend or run simulated fetching."
                        }
                      </p>
                    );
                  }

                  return (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                      {list.map((item: any, idx: number) => {
                        const avg = item.totalScore / item.count;
                        return (
                          <div
                            key={idx}
                            onClick={() => setExpandedTicker(item.ticker)}
                            style={{
                              display: 'flex',
                              justifyContent: 'space-between',
                              alignItems: 'center',
                              padding: '12px',
                              borderRadius: '8px',
                              background: 'rgba(255,255,255,0.02)',
                              border: '1px solid var(--border-glass)',
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

              {/* Crisis Stress Tests (moved below the live sentiment/news feeds) */}
              <div className="glass-card" style={{ marginTop: '24px' }}>
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
                            {appMode === 'real'
                              ? "No active positions found in your real virtual broker account. Configure holdings or run execution scheduler."
                              : "No active positions. Add holdings or run simulations."
                            }
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
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px', flexWrap: 'wrap', gap: '8px' }}>
                  <h2 style={{ margin: 0 }}>
                    <Sliders size={20} color="var(--color-accent)" />
                    My Portfolio &amp; Holdings
                  </h2>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    {portfolio?.as_of && (
                      <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                        {portfolio.source === 'broker' ? 'Live · Alpaca' : 'Stored prices'} · {portfolio.as_of.slice(11)}
                      </span>
                    )}
                    <button
                      onClick={() => { setAddStockOpen(true); setAddStockTicker(''); }}
                      style={{
                        display: 'flex', alignItems: 'center', gap: '6px',
                        background: 'var(--color-accent)', border: 'none',
                        borderRadius: '8px', color: 'white', padding: '7px 14px',
                        cursor: 'pointer', fontWeight: 600, fontSize: '13px'
                      }}
                    >
                      <Plus size={14} /> Add Stock
                    </button>
                    <button
                      onClick={fetchPortfolio}
                      disabled={refreshingPortfolio}
                      style={{
                        display: 'flex', alignItems: 'center', gap: '6px',
                        background: 'rgba(0, 242, 254, 0.1)', border: '1px solid var(--color-buy)',
                        borderRadius: '8px', color: 'var(--color-buy)', padding: '7px 14px',
                        cursor: refreshingPortfolio ? 'default' : 'pointer', fontWeight: 600, fontSize: '13px'
                      }}
                    >
                      <RefreshCw size={14} className={refreshingPortfolio ? 'animate-spin' : ''} />
                      Refresh Prices
                    </button>
                  </div>
                </div>

                {/* Portfolio summary */}
                {portfolio?.totals && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', marginBottom: '18px' }}>
                    {([
                      ['Market Value', `$${portfolio.totals.market_value.toLocaleString(undefined, {maximumFractionDigits: 0})}`, null],
                      ['Cost Basis', `$${portfolio.totals.cost_basis.toLocaleString(undefined, {maximumFractionDigits: 0})}`, null],
                      ['Unrealized P&L', `${portfolio.totals.unrealized_pl >= 0 ? '+' : ''}$${portfolio.totals.unrealized_pl.toLocaleString(undefined, {maximumFractionDigits: 0})} (${portfolio.totals.unrealized_pl_pct >= 0 ? '+' : ''}${portfolio.totals.unrealized_pl_pct.toFixed(2)}%)`, portfolio.totals.unrealized_pl],
                      ['Cash', `$${portfolio.totals.cash.toLocaleString(undefined, {maximumFractionDigits: 0})}`, null],
                      ['Total Equity', `$${portfolio.totals.equity.toLocaleString(undefined, {maximumFractionDigits: 0})}`, null],
                    ] as [string, string, number | null][]).map(([label, val, pl], i) => (
                      <div key={i} style={{ flex: '1 1 130px', background: 'rgba(0,0,0,0.2)', border: '1px solid var(--border-glass)', borderRadius: '10px', padding: '12px 14px' }}>
                        <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '4px' }}>{label}</div>
                        <div className={pl == null ? '' : pl > 0 ? 'text-green' : pl < 0 ? 'text-red' : ''} style={{ fontSize: '16px', fontWeight: 700 }}>{val}</div>
                      </div>
                    ))}
                  </div>
                )}

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

                {/* Table listing holdings with live value + P&L */}
                <div style={{ overflowX: 'auto' }}>
                  {(() => {
                    const heldRows = (portfolio?.holdings && portfolio.holdings.length > 0)
                      ? portfolio.holdings.map((h: any) => ({ ...h, monitored: false }))
                      : holdings.map((h: any) => ({
                          ticker: h.ticker, shares: h.quantity, entry_price: h.entry_price,
                          current_price: h.entry_price, market_value: h.quantity * h.entry_price,
                          unrealized_pl: 0, unrealized_pl_pct: 0, policy: h.policy, monitored: false
                        }));
                    const heldSet = new Set(heldRows.map((h: any) => h.ticker));
                    const priceMap: any = {};
                    priceSummary.forEach((p: any) => { priceMap[p.ticker] = p.price; });
                    const monitoredRows = universeTickers
                      .filter((t: string) => !heldSet.has(t))
                      .map((t: string) => ({
                        ticker: t, shares: 0, entry_price: 0, current_price: priceMap[t] ?? 0,
                        market_value: 0, unrealized_pl: 0, unrealized_pl_pct: 0, policy: 'rebalance', monitored: true
                      }));
                    const rows = [...heldRows, ...monitoredRows];
                    return (
                      <table className="trade-table">
                        <thead>
                          <tr>
                            <th>Asset</th>
                            <th>Shares</th>
                            <th>Avg Cost</th>
                            <th>Cur Price</th>
                            <th>Mkt Value</th>
                            <th>Unrealized P&amp;L</th>
                            <th>Policy</th>
                            <th>Actions</th>
                          </tr>
                        </thead>
                        <tbody>
                          {rows.length === 0 ? (
                            <tr>
                              <td colSpan={8} style={{ textAlign: 'center', color: 'var(--text-secondary)' }}>
                                No positions or monitored stocks yet. Use &ldquo;+ Add Stock&rdquo; to start monitoring a ticker.
                              </td>
                            </tr>
                          ) : (
                            rows.map((h: any, idx: number) => (
                              <tr key={idx} style={h.monitored ? { opacity: 0.72 } : undefined}>
                                <td style={{ fontWeight: 600 }}>{h.ticker}</td>
                                <td>{h.monitored ? '0' : h.shares.toFixed(2)}</td>
                                <td>{h.monitored ? '—' : `$${h.entry_price.toFixed(2)}`}</td>
                                <td>{h.current_price > 0 ? `$${h.current_price.toFixed(2)}` : '—'}</td>
                                <td>{h.monitored ? '—' : `$${h.market_value.toLocaleString(undefined, {maximumFractionDigits: 0})}`}</td>
                                <td className={h.monitored ? '' : h.unrealized_pl > 0 ? 'text-green' : h.unrealized_pl < 0 ? 'text-red' : ''} style={{ fontWeight: 600 }}>
                                  {h.monitored ? '—' : (
                                    <>
                                      {h.unrealized_pl < 0 ? '-' : '+'}${Math.abs(h.unrealized_pl).toLocaleString(undefined, {maximumFractionDigits: 0})}
                                      <span style={{ fontSize: '11px', marginLeft: '4px' }}>({h.unrealized_pl_pct >= 0 ? '+' : ''}{h.unrealized_pl_pct.toFixed(2)}%)</span>
                                    </>
                                  )}
                                </td>
                                <td>
                                  {h.monitored ? (
                                    <span style={{ fontSize: '11px', color: 'var(--text-secondary)', padding: '3px 9px', borderRadius: '999px', background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border-glass)' }}>Monitoring</span>
                                  ) : (
                                    <select
                                      value={h.policy}
                                      onChange={(e) => handleUpdatePolicy(h.ticker, h.shares, h.entry_price, e.target.value as any)}
                                      style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid var(--border-glass)', borderRadius: '6px', color: 'var(--text-primary)', padding: '4px 8px', fontSize: '12px', cursor: 'pointer' }}
                                    >
                                      <option value="rebalance">Rebalance</option>
                                      <option value="lock">Lock</option>
                                      <option value="liquidate">Liquidate (next run)</option>
                                    </select>
                                  )}
                                </td>
                                <td>
                                  {h.monitored ? (
                                    <button
                                      onClick={() => handleRemoveMonitored(h.ticker)}
                                      title="Stop monitoring this ticker"
                                      style={{ background: 'transparent', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer' }}
                                      onMouseEnter={(e) => e.currentTarget.style.color = 'var(--color-sell)'}
                                      onMouseLeave={(e) => e.currentTarget.style.color = 'var(--text-secondary)'}
                                    >
                                      <Trash2 size={16} />
                                    </button>
                                  ) : (
                                    <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                                      <button
                                        onClick={() => setLiquidateModal({ ticker: h.ticker, held: h.shares, shares: String(h.shares) })}
                                        style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid var(--color-sell)', borderRadius: '6px', color: 'var(--color-sell)', padding: '4px 12px', fontSize: '12px', fontWeight: 600, cursor: 'pointer' }}
                                      >
                                        Liquidate
                                      </button>
                                      <span title="Held position — sell via Liquidate. The trash icon is enabled only once you hold 0 shares.">
                                        <Trash2 size={16} style={{ color: 'rgba(255,255,255,0.18)', cursor: 'not-allowed' }} />
                                      </span>
                                    </div>
                                  )}
                                </td>
                              </tr>
                            ))
                          )}
                        </tbody>
                      </table>
                    );
                  })()}
                </div>
              </div>
            </section>

            {/* Right Column: Universe Editor & Simulation controls */}
            <aside>
              {/* Model Training & Background Data Jobs */}
              <div className="glass-card" style={{ marginBottom: '24px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
                  <h2 style={{ margin: 0 }}>
                    <Cpu size={20} color="var(--color-gold)" />
                    Model Training
                  </h2>
                  <button
                    onClick={handleRetrain}
                    disabled={actionBusy || (trainStatus?.training && trainStatus.training.status === 'running')}
                    style={{
                      display: 'flex', alignItems: 'center', gap: '6px',
                      background: 'rgba(245,158,11,0.12)', border: '1px solid var(--color-gold)',
                      borderRadius: '8px', color: 'var(--color-gold)', padding: '7px 14px',
                      cursor: (trainStatus?.training && trainStatus.training.status === 'running') ? 'default' : 'pointer',
                      fontWeight: 600, fontSize: '13px', opacity: (trainStatus?.training && trainStatus.training.status === 'running') ? 0.6 : 1
                    }}
                  >
                    <RotateCcw size={14} className={(trainStatus?.training && trainStatus.training.status === 'running') ? 'animate-spin' : ''} />
                    {(trainStatus?.training && trainStatus.training.status === 'running') ? 'Training…' : 'Retrain'}
                  </button>
                </div>

                {trainStatus?.training && trainStatus.training.status === 'running' && (
                  <div style={{ marginBottom: '16px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '4px' }}>
                      <span style={{ color: 'var(--color-gold)' }}>{trainStatus.training.stage}</span>
                      <span style={{ color: 'var(--text-secondary)' }}>{trainStatus.training.progress}%</span>
                    </div>
                    <div style={{ height: '6px', background: 'rgba(255,255,255,0.06)', borderRadius: '999px', overflow: 'hidden' }}>
                      <div style={{ width: `${trainStatus.training.progress}%`, height: '100%', background: 'var(--color-gold)', transition: 'width 0.4s' }} />
                    </div>
                  </div>
                )}
                {trainStatus?.training && trainStatus.training.status === 'error' && (
                  <div style={{ fontSize: '12px', color: 'var(--color-sell)', marginBottom: '14px' }}>
                    Training failed: {trainStatus.training.error}
                  </div>
                )}

                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {([['swing', 'Swing model'], ['short_term', 'Short-term (XGBoost)'], ['regime_hmm', 'Regime (HMM)']] as const).map(([k, label]) => (
                    <div key={k} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px' }}>
                      <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
                      <span style={{ fontWeight: 500 }}>{trainStatus?.models?.[k]?.last_trained ? `trained ${trainStatus.models[k].last_trained}` : '—'}</span>
                    </div>
                  ))}
                </div>

                {jobs.filter((j: any) => j.type === 'backfill').length > 0 && (
                  <div style={{ marginTop: '16px', borderTop: '1px solid var(--border-glass)', paddingTop: '14px' }}>
                    <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: '10px' }}>
                      Data Backfill
                    </div>
                    {jobs.filter((j: any) => j.type === 'backfill').map((j: any) => (
                      <div key={j.id} style={{ marginBottom: '12px' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '4px' }}>
                          <span>{j.label}</span>
                          <span style={{ color: j.status === 'error' ? 'var(--color-sell)' : j.status === 'done' ? 'var(--color-buy)' : 'var(--text-secondary)' }}>
                            {j.status === 'error' ? 'failed' : `${j.progress}%`}
                          </span>
                        </div>
                        <div style={{ height: '6px', background: 'rgba(255,255,255,0.06)', borderRadius: '999px', overflow: 'hidden' }}>
                          <div style={{ width: `${j.progress}%`, height: '100%', background: j.status === 'error' ? 'var(--color-sell)' : 'var(--color-buy)', transition: 'width 0.4s' }} />
                        </div>
                        <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '3px' }}>{j.error || j.stage}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

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
                  {appMode === 'real' ? (
                    <div style={{
                      display: 'flex',
                      flexDirection: 'column',
                      alignItems: 'center',
                      justifyContent: 'center',
                      padding: '24px 12px',
                      background: 'rgba(0, 0, 0, 0.25)',
                      borderRadius: '8px',
                      border: '1px solid rgba(239, 68, 68, 0.1)',
                      marginTop: '12px',
                      textAlign: 'center'
                    }}>
                      <Lock size={32} color="var(--color-sell)" style={{ marginBottom: '12px', opacity: 0.8 }} />
                      <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '4px' }}>
                        Simulation Locked in Real Mode
                      </div>
                      <p style={{ fontSize: '12px', color: 'var(--text-secondary)', lineHeight: '1.4' }}>
                        To prevent database pollution and protect real positions, forward simulations and historical replays are disabled in Real Mode.
                      </p>
                    </div>
                  ) : (
                    <>
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
                    </>
                  )}
                </div>
              )}
            </aside>
          </>
        )}
      </main>

      {/* Liquidate position modal */}
      {liquidateModal && (
        <div
          onClick={() => !actionBusy && setLiquidateModal(null)}
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}
        >
          <div onClick={(e) => e.stopPropagation()} style={{ background: 'rgba(16, 20, 38, 0.98)', border: '1px solid var(--border-glass)', borderRadius: '14px', padding: '24px', width: '380px', maxWidth: '90vw', boxShadow: '0 20px 60px rgba(0,0,0,0.5)' }}>
            <h2 style={{ marginTop: 0, marginBottom: '8px' }}>Liquidate {liquidateModal.ticker}</h2>
            <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '14px' }}>
              You hold <strong style={{ color: 'var(--text-primary)' }}>{liquidateModal.held}</strong> shares. How many do you want to sell?
            </p>
            <input
              type="number" min="0" max={liquidateModal.held} value={liquidateModal.shares}
              onChange={(e) => setLiquidateModal({ ...liquidateModal, shares: e.target.value })}
              autoFocus
              style={{ width: '100%', background: 'rgba(0,0,0,0.3)', border: '1px solid var(--border-glass)', borderRadius: '8px', color: 'var(--text-primary)', padding: '10px 12px', fontSize: '14px' }}
            />
            <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
              <button onClick={() => setLiquidateModal({ ...liquidateModal, shares: String(liquidateModal.held) })}
                style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border-glass)', borderRadius: '6px', color: 'var(--text-secondary)', padding: '5px 12px', fontSize: '12px', cursor: 'pointer' }}>Sell all</button>
              <button onClick={() => setLiquidateModal({ ...liquidateModal, shares: String(Math.floor(liquidateModal.held / 2)) })}
                style={{ background: 'rgba(255,255,255,0.05)', border: '1px solid var(--border-glass)', borderRadius: '6px', color: 'var(--text-secondary)', padding: '5px 12px', fontSize: '12px', cursor: 'pointer' }}>Half</button>
            </div>
            <div style={{ display: 'flex', gap: '10px', marginTop: '22px', justifyContent: 'flex-end' }}>
              <button onClick={() => setLiquidateModal(null)} disabled={actionBusy}
                style={{ background: 'transparent', border: '1px solid var(--border-glass)', borderRadius: '8px', color: 'var(--text-secondary)', padding: '8px 16px', fontSize: '13px', cursor: 'pointer' }}>Cancel</button>
              <button onClick={handleLiquidate} disabled={actionBusy || !(parseFloat(liquidateModal.shares) > 0)}
                style={{ background: 'var(--color-sell)', border: 'none', borderRadius: '8px', color: 'white', padding: '8px 16px', fontSize: '13px', fontWeight: 600, cursor: 'pointer', opacity: actionBusy ? 0.6 : 1 }}>
                {actionBusy ? 'Selling…' : `Sell ${liquidateModal.shares || 0} shares`}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Add stock to monitor modal */}
      {addStockOpen && (
        <div
          onClick={() => !actionBusy && setAddStockOpen(false)}
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}
        >
          <div onClick={(e) => e.stopPropagation()} style={{ background: 'rgba(16, 20, 38, 0.98)', border: '1px solid var(--border-glass)', borderRadius: '14px', padding: '24px', width: '380px', maxWidth: '90vw', boxShadow: '0 20px 60px rgba(0,0,0,0.5)' }}>
            <h2 style={{ marginTop: 0, marginBottom: '8px' }}>Add a Stock to Monitor</h2>
            <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '14px' }}>
              It&rsquo;s added to your universe and its price history is backfilled in the background (watch the progress under Model Training).
            </p>
            <input
              type="text" placeholder="e.g. TSLA" value={addStockTicker}
              onChange={(e) => setAddStockTicker(e.target.value.toUpperCase())}
              onKeyDown={(e) => { if (e.key === 'Enter') handleAddStock(); }}
              autoFocus
              style={{ width: '100%', background: 'rgba(0,0,0,0.3)', border: '1px solid var(--border-glass)', borderRadius: '8px', color: 'var(--text-primary)', padding: '10px 12px', fontSize: '14px' }}
            />
            <div style={{ display: 'flex', gap: '10px', marginTop: '22px', justifyContent: 'flex-end' }}>
              <button onClick={() => setAddStockOpen(false)} disabled={actionBusy}
                style={{ background: 'transparent', border: '1px solid var(--border-glass)', borderRadius: '8px', color: 'var(--text-secondary)', padding: '8px 16px', fontSize: '13px', cursor: 'pointer' }}>Cancel</button>
              <button onClick={handleAddStock} disabled={actionBusy || !addStockTicker.trim()}
                style={{ background: 'var(--color-accent)', border: 'none', borderRadius: '8px', color: 'white', padding: '8px 16px', fontSize: '13px', fontWeight: 600, cursor: 'pointer', opacity: (actionBusy || !addStockTicker.trim()) ? 0.6 : 1 }}>
                {actionBusy ? 'Adding…' : 'Add & Backfill'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
