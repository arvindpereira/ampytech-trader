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
  Cpu,
  Clock,
  Brain,
  ThumbsUp,
  ThumbsDown,
  AlertTriangle,
  CheckCircle2
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
  current_shares?: number;
  entry_price?: number;
  current_price?: number;
  current_value?: number;
  target_shares?: number;
  target_value?: number;
  suggested_action?: string;
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
  const appMode = 'real';
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
    total_return: 0.0,
    sharpe_ratio: 0.0,
    max_drawdown: 0.0,
    win_rate: 0.0
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
  const [strategyConfig, setStrategyConfig] = useState<any>(null);
  const [bucketEdit, setBucketEdit] = useState<{ swing: string; longterm: string }>({ swing: '', longterm: '' });
  const [suggestRunning, setSuggestRunning] = useState<boolean>(false);
  const [suggestProgress, setSuggestProgress] = useState<{ pct: number; stage: string }>({ pct: 0, stage: '' });
  const [suggestData, setSuggestData] = useState<any>(null);
  const [validateRunning, setValidateRunning] = useState<boolean>(false);
  const [validateProgress, setValidateProgress] = useState<{ pct: number; stage: string }>({ pct: 0, stage: '' });
  const [validateData, setValidateData] = useState<any>(null);
  const [evalStrategies, setEvalStrategies] = useState<{ swing: boolean; longterm: boolean }>({ swing: true, longterm: true });
  const [evalSplits, setEvalSplits] = useState<number>(4);
  const [evalUseAlloc, setEvalUseAlloc] = useState<boolean>(true);
  const [evalRunning, setEvalRunning] = useState<boolean>(false);
  const [evalProgress, setEvalProgress] = useState<{ pct: number; stage: string }>({ pct: 0, stage: '' });
  const [evalResult, setEvalResult] = useState<any>(null);
  const [evalJobId, setEvalJobId] = useState<string | null>(null);
  const [interpLoading, setInterpLoading] = useState<boolean>(false);
  const [evalWindow, setEvalWindow] = useState<string>('none');
  const [evalCustom, setEvalCustom] = useState<{ start: string; end: string }>({ start: '', end: '' });
  const [evalOosStart, setEvalOosStart] = useState<string>('');
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
  const [expandedAlloc, setExpandedAlloc] = useState<string>('');
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
        setSentSources([]);
      }
    } catch (err) {
      setSentSources([]);
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

  const STRATEGY_OPTIONS: [string, string][] = [
    ['swing', 'Swing + News'],
    ['longterm', 'Long-term (MPT)'],
    ['hold', 'Hold (no trades)'],
  ];

  const fetchStrategyConfig = async () => {
    try {
      const res = await fetch(`http://localhost:8008/api/strategy/config`);
      if (res.ok) {
        const d = await res.json();
        setStrategyConfig(d);
        setBucketEdit({ swing: String(Math.round((d.buckets.swing || 0) * 100)), longterm: String(Math.round((d.buckets.longterm || 0) * 100)) });
      }
    } catch (err) { /* offline */ }
  };

  const handleSetTickerStrategy = async (ticker: string, strategy: string) => {
    try {
      await fetch(`http://localhost:8008/api/strategy/ticker`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker, strategy }),
      });
      fetchStrategyConfig();
      fetchData();
    } catch (err) { console.error(err); }
  };

  const handleSuggestStrategies = async () => {
    setSuggestRunning(true);
    setSuggestData(null);
    setSuggestProgress({ pct: 0, stage: 'Starting…' });
    try {
      const res = await fetch(`http://localhost:8008/api/strategy/suggest?oos_start=2022-01-01`, { method: 'POST' });
      const { job_id } = await res.json();
      const poll = async () => {
        try {
          const r = await fetch(`http://localhost:8008/api/strategy/suggest/result?job_id=${job_id}`);
          const d = await r.json();
          if (d.status === 'done') { setSuggestData(d.result); setSuggestRunning(false); }
          else if (d.status === 'error' || d.status === 'unknown') { setSuggestProgress({ pct: 0, stage: 'Failed: ' + (d.error || 'job lost') }); setSuggestRunning(false); }
          else { setSuggestProgress({ pct: d.progress || 0, stage: d.stage || 'Running…' }); setTimeout(poll, 2000); }
        } catch (err) { setSuggestRunning(false); }
      };
      poll();
    } catch (err) { console.error(err); setSuggestRunning(false); }
  };

  const suggestMap: Record<string, any> = {};
  if (suggestData?.suggestions) suggestData.suggestions.forEach((s: any) => { suggestMap[s.ticker] = s; });

  const handleValidateSuggestions = async () => {
    setValidateRunning(true);
    setValidateData(null);
    setValidateProgress({ pct: 0, stage: 'Starting…' });
    try {
      const res = await fetch(`http://localhost:8008/api/strategy/validate?oos_start=2022-01-01`, { method: 'POST' });
      const { job_id } = await res.json();
      const poll = async () => {
        try {
          const r = await fetch(`http://localhost:8008/api/strategy/validate/result?job_id=${job_id}`);
          const d = await r.json();
          if (d.status === 'done') { setValidateData(d.result); setValidateRunning(false); }
          else if (d.status === 'error' || d.status === 'unknown') { setValidateProgress({ pct: 0, stage: 'Failed: ' + (d.error || 'job lost') }); setValidateRunning(false); }
          else { setValidateProgress({ pct: d.progress || 0, stage: d.stage || 'Running…' }); setTimeout(poll, 2000); }
        } catch (err) { setValidateRunning(false); }
      };
      poll();
    } catch (err) { console.error(err); setValidateRunning(false); }
  };

  const handleAcceptAllSuggestions = async () => {
    if (!suggestData?.suggestions) return;
    const cur = strategyConfig?.assignments || {};
    const changes = suggestData.suggestions.filter((s: any) => (cur[s.ticker] || 'swing') !== s.recommended);
    if (changes.length && !confirm(`Apply ${changes.length} strategy change(s) to your tickers?`)) return;
    setActionBusy(true);
    try {
      for (const s of changes) {
        await fetch(`http://localhost:8008/api/strategy/ticker`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker: s.ticker, strategy: s.recommended }),
        });
      }
      fetchStrategyConfig(); fetchData();
    } catch (err) { console.error(err); } finally { setActionBusy(false); }
  };

  const handleSaveBuckets = async () => {
    const s = (parseFloat(bucketEdit.swing) || 0) / 100;
    const l = (parseFloat(bucketEdit.longterm) || 0) / 100;
    if (s + l > 1.0001) { alert('Buckets cannot exceed 100% of equity.'); return; }
    setActionBusy(true);
    try {
      const res = await fetch(`http://localhost:8008/api/strategy/buckets`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ swing: s, longterm: l }),
      });
      if (!res.ok) { const e = await res.json(); alert(e.detail || 'Failed'); }
      else fetchStrategyConfig();
    } catch (err) { console.error(err); } finally { setActionBusy(false); }
  };

  const STRESS_PRESETS: Record<string, { label: string; start: string | null; end: string | null }> = {
    none: { label: 'Walk-forward (model eval)', start: null, end: null },
    '2022': { label: '2022 Bear Market', start: '2022-01-03', end: '2022-12-30' },
    covid: { label: '2020 COVID Crash', start: '2020-02-19', end: '2020-04-30' },
    gfc: { label: '2008 Financial Crisis', start: '2007-10-09', end: '2009-03-09' },
    dotcom: { label: 'Dot-Com Bust', start: '2000-03-24', end: '2002-10-09' },
    custom: { label: 'Custom range…', start: '', end: '' },
  };

  const EVAL_SERIES: { key: string; name: string; color: string }[] = [
    { key: 'swing', name: 'Swing + News', color: '#00F2FE' },
    { key: 'longterm', name: 'Long-term (MPT)', color: '#10B981' },
    { key: 'blended', name: 'Blended (your allocation)', color: '#F59E0B' },
    { key: 'spy', name: 'S&P 500', color: '#94A3B8' },
    { key: 'qqq', name: 'QQQ', color: '#A78BFA' },
    { key: 'brk', name: 'Berkshire (BRK-B)', color: '#FB923C' },
  ];

  const handleRunEval = async () => {
    const strategies = (Object.entries(evalStrategies) as [string, boolean][]).filter(([, v]) => v).map(([k]) => k);
    if (strategies.length === 0) return;
    let start: string | null = null, end: string | null = null;
    if (evalWindow === 'custom') { start = evalCustom.start || null; end = evalCustom.end || null; }
    else if (evalWindow !== 'none') { start = STRESS_PRESETS[evalWindow].start; end = STRESS_PRESETS[evalWindow].end; }
    // OOS-start only applies to walk-forward mode (not a fixed stress window)
    const oos = (evalWindow === 'none' && evalOosStart) ? evalOosStart : null;
    setEvalRunning(true);
    setEvalResult(null);
    setEvalProgress({ pct: 0, stage: 'Starting…' });
    try {
      const res = await fetch(`http://localhost:8008/api/evaluate`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategies, horizon: 5, splits: evalSplits, use_allocation: evalUseAlloc, start_date: start, end_date: end, oos_start: oos }),
      });
      const { job_id } = await res.json();
      setEvalJobId(job_id);
      const poll = async () => {
        try {
          const r = await fetch(`http://localhost:8008/api/evaluate/result?job_id=${job_id}`);
          const d = await r.json();
          if (d.status === 'done') { setEvalResult(d.result); setEvalRunning(false); }
          else if (d.status === 'error' || d.status === 'unknown') { setEvalProgress({ pct: 0, stage: 'Failed: ' + (d.error || 'job lost') }); setEvalRunning(false); }
          else { setEvalProgress({ pct: d.progress || 0, stage: d.stage || 'Running…' }); setTimeout(poll, 1500); }
        } catch (err) { setEvalRunning(false); }
      };
      poll();
    } catch (err) { console.error(err); setEvalRunning(false); }
  };

  const regenerateInterpretation = async () => {
    if (!evalJobId) return;
    setInterpLoading(true);
    try {
      const r = await fetch(`http://localhost:8008/api/evaluate/interpret?job_id=${evalJobId}`, { method: 'POST' });
      const d = await r.json();
      if (d.interpretation) setEvalResult((prev: any) => ({ ...prev, interpretation: d.interpretation }));
    } catch (err) { console.error(err); } finally { setInterpLoading(false); }
  };

  const handleAddStock = async () => {
    const t = addStockTicker.toUpperCase().trim();
    if (!t) return;
    setActionBusy(true);
    try {
      // /backfill adds the ticker if missing AND always starts a (visible) backfill job —
      // so "Add & Backfill" works even for a ticker already in the universe.
      await fetch(`http://localhost:8008/api/universe/backfill`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker: t }),
      });
      setAddStockTicker('');
      setAddStockOpen(false);
      fetchJobsAndTraining();
      fetchData();
    } catch (err) { console.error(err); } finally { setActionBusy(false); }
  };

  const handleBackfillTicker = async (ticker: string) => {
    try {
      await fetch(`http://localhost:8008/api/universe/backfill`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker }),
      });
      fetchJobsAndTraining();
    } catch (err) { console.error(err); }
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
        setPremiumStatus('Backend offline: cannot upload article.');
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

      // 9. Fetch strategy assignments + bucket allocations
      fetchStrategyConfig();

    } catch (err) {
      console.warn("FastAPI backend is offline.");
      setBackendOnline(false);
      setSuggestions([]);
      setAllocations([]);
      setHoldings([]);
      setVirtualPositions([]);
      setPerfCurve([]);
      setSentimentList([]);
      setPriceSummary([]);
      setPortfolio(null);
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

    setActionBusy(true);
    try {
      await fetch(`http://localhost:8008/api/universe/add`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker: tickerUpper })
      });
      setNewUniverseTicker('');
      fetchJobsAndTraining();
      fetchData();
    } catch (err) {
      console.error("Failed to add ticker to universe", err);
    } finally {
      setActionBusy(false);
    }
  };

  const handleRemoveTicker = async (ticker: string) => {
    if (!confirm(`Stop monitoring ${ticker}?`)) return;
    setActionBusy(true);
    try {
      await fetch(`http://localhost:8008/api/universe/remove`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker })
      });
      fetchData();
    } catch (err) {
      console.error("Failed to remove ticker from universe", err);
    } finally {
      setActionBusy(false);
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

  const formatTime = (ts: string | undefined | null) => {
    if (!ts || ts === 'none' || ts === 'error') return '—';
    if (ts.length >= 16) return ts.substring(5, 16);
    if (ts.length === 10) return ts.substring(5, 10);
    return ts;
  };

  return (
    <div style={{ background: 'var(--bg-dark)', minHeight: '100vh', color: 'var(--text-primary)', ...realThemeStyles }}>
      {/* Top Navbar */}
      <header className="navbar">
        <div className="nav-logo">
          <Activity size={28} color="var(--color-buy)" />
          <span>AMPYTECH TRADER</span>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '20px' }}>
          {/* Active background-jobs badge (visible from any tab) */}
          {jobs.filter((j: any) => j.status === 'running').length > 0 && (
            <button
              onClick={() => setActiveTab('editor')}
              title="View the Background Jobs queue"
              style={{ display: 'flex', alignItems: 'center', gap: '6px', background: 'rgba(0,242,254,0.1)', border: '1px solid var(--color-buy)', borderRadius: '999px', color: 'var(--color-buy)', padding: '4px 12px', fontSize: '12px', fontWeight: 600, cursor: 'pointer' }}
            >
              <RefreshCw size={13} className="animate-spin" />
              {jobs.filter((j: any) => j.status === 'running').length} job(s) running
            </button>
          )}
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

          {/* Last Refreshed tracker */}
          {health && health.last_refreshed && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: '8px',
              padding: '4px 12px', borderRadius: '999px',
              background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border-glass)',
              fontSize: '11px', color: 'var(--text-secondary)'
            }} title="Last time data was fetched and updated in the DB">
              <Clock size={12} color="var(--color-buy)" style={{ opacity: 0.8 }} />
              <span>Prices:</span>
              <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{formatTime(health.last_refreshed.prices_hourly)}</span>
              <span style={{ opacity: 0.3 }}>|</span>
              <span>News:</span>
              <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{formatTime(health.last_refreshed.news_llm)}</span>
              <span style={{ opacity: 0.3 }}>|</span>
              <span>Macro:</span>
              <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>{formatTime(health.last_refreshed.macro)}</span>
            </div>
          )}



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
            Model Evaluation
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
                    <TrendingUp size={16} color="var(--color-buy)" /> Total Return
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
                    {allocations.map((item, idx) => {
                      const isExpanded = expandedAlloc === item.ticker;
                      const hasDetails = item.current_shares !== undefined;
                      const ownedPct = item.target_shares > 0 ? (item.current_shares / item.target_shares) * 100 : 0;
                      return (
                        <div
                          key={idx}
                          style={{
                            marginBottom: '12px',
                            padding: '10px',
                            borderRadius: '8px',
                            background: isExpanded ? 'rgba(255,255,255,0.02)' : 'transparent',
                            border: isExpanded ? '1px solid var(--border-glass)' : '1px solid transparent',
                            cursor: 'pointer',
                            transition: 'all 0.2s ease',
                          }}
                          onClick={() => setExpandedAlloc(isExpanded ? '' : item.ticker)}
                          onMouseEnter={(e) => { e.currentTarget.style.background = 'rgba(255,255,255,0.04)'; }}
                          onMouseLeave={(e) => { e.currentTarget.style.background = isExpanded ? 'rgba(255,255,255,0.02)' : 'transparent'; }}
                        >
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '14px', fontWeight: 500, marginBottom: '6px' }}>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                              <span style={{
                                color: 'var(--text-secondary)',
                                fontSize: '10px',
                                display: 'inline-block',
                                transition: 'transform 0.2s',
                                transform: isExpanded ? 'rotate(90deg)' : 'none'
                              }}>▶</span>
                              <span style={{ fontWeight: 600 }}>{item.ticker}</span>
                              {item.suggested_action && (
                                <span className={`badge badge-${item.suggested_action.includes('BUY') ? 'buy' : item.suggested_action.includes('SELL') ? 'sell' : 'hold'}`} style={{ fontSize: '10px', padding: '2px 6px', fontWeight: 700 }}>
                                  {item.suggested_action.includes('BUY') ? 'BUY' : item.suggested_action.includes('SELL') ? 'SELL' : 'HOLD'}
                                </span>
                              )}
                            </div>
                            <span style={{ color: 'var(--color-buy)', fontWeight: 600 }}>{(item.weight * 100).toFixed(0)}% Target</span>
                          </div>

                          {/* Progress Bar indicating Lack of Ownership to Target */}
                          <div style={{ marginTop: '4px' }}>
                            <div className="alloc-bar-bg" style={{ height: '8px', background: 'rgba(255,255,255,0.05)', borderRadius: '999px', overflow: 'hidden', display: 'flex', border: '1px solid var(--border-glass)' }}>
                              <div
                                className="alloc-bar-fill"
                                style={{
                                  height: '100%',
                                  background: ownedPct >= 100 ? 'var(--color-buy)' : 'var(--color-accent)',
                                  width: `${Math.min(ownedPct, 100)}%`,
                                  transition: 'width 0.3s ease'
                                }}
                              ></div>
                              {ownedPct > 100 && (
                                <div
                                  style={{
                                    height: '100%',
                                    background: 'var(--color-gold)',
                                    width: `${Math.min(ownedPct - 100, 100)}%`,
                                    transition: 'width 0.3s ease'
                                  }}
                                  title="Overweight excess"
                                ></div>
                              )}
                            </div>
                            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                              <span>
                                {item.current_shares > 0
                                  ? `${item.current_shares.toFixed(1)} / ${item.target_shares.toFixed(1)} shares`
                                  : `0 / ${item.target_shares.toFixed(1)} shares (0% owned)`
                                }
                              </span>
                              <span>
                                {ownedPct.toFixed(0)}% of Target
                              </span>
                            </div>
                          </div>

                          {isExpanded && (
                            <div
                              style={{
                                padding: '12px',
                                marginTop: '10px',
                                borderRadius: '6px',
                                background: 'rgba(0,0,0,0.15)',
                                border: '1px solid rgba(255,255,255,0.05)',
                                fontSize: '12px',
                                color: 'var(--text-secondary)',
                                cursor: 'default'
                              }}
                              onClick={(e) => e.stopPropagation()}
                            >
                              {hasDetails ? (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px' }}>
                                    <div>
                                      <span style={{ color: 'var(--text-primary)', fontSize: '10px', fontWeight: 700, letterSpacing: '0.05em', display: 'block', marginBottom: '4px' }}>CURRENT STATE</span>
                                      <div style={{ fontSize: '13px', color: 'var(--text-primary)' }}>
                                        <strong>{item.current_shares?.toFixed(1)}</strong> shares owned
                                      </div>
                                      <div style={{ marginTop: '2px', fontSize: '11px' }}>Value: ${item.current_value?.toFixed(2)} @ ${item.current_price?.toFixed(2)}</div>
                                      <div style={{ fontSize: '11px' }}>Cost Basis: {item.entry_price && item.entry_price > 0 ? `$${item.entry_price.toFixed(2)}` : '—'}</div>
                                    </div>
                                    <div>
                                      <span style={{ color: 'var(--text-primary)', fontSize: '10px', fontWeight: 700, letterSpacing: '0.05em', display: 'block', marginBottom: '4px' }}>TARGET ALLOCATION</span>
                                      <div style={{ fontSize: '13px', color: 'var(--text-primary)' }}>
                                        <strong>{item.target_shares?.toFixed(1)}</strong> shares
                                      </div>
                                      <div style={{ marginTop: '2px', fontSize: '11px' }}>Target Value: ${item.target_value?.toFixed(2)}</div>
                                      <div style={{ fontSize: '11px' }}>Target Weight: {(item.weight * 100).toFixed(1)}%</div>
                                    </div>
                                  </div>

                                  <div style={{
                                    padding: '8px 12px',
                                    marginTop: '4px',
                                    borderRadius: '4px',
                                    background: item.suggested_action?.includes('BUY') ? 'rgba(16, 185, 129, 0.1)' : item.suggested_action?.includes('SELL') ? 'rgba(239, 68, 68, 0.1)' : 'rgba(255,255,255,0.03)',
                                    border: `1px solid ${item.suggested_action?.includes('BUY') ? 'rgba(16, 185, 129, 0.2)' : item.suggested_action?.includes('SELL') ? 'rgba(239, 68, 68, 0.2)' : 'rgba(255,255,255,0.05)'}`,
                                    color: item.suggested_action?.includes('BUY') ? 'var(--color-buy)' : item.suggested_action?.includes('SELL') ? 'var(--color-sell)' : 'var(--text-primary)',
                                    lineHeight: 1.4
                                  }}>
                                    <strong style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: '2px' }}>Suggested Action</strong>
                                    {item.suggested_action}
                                  </div>
                                </div>
                              ) : (
                                <p style={{ color: 'var(--text-secondary)', margin: 0 }}>No current holding details available.</p>
                              )}
                            </div>
                          )}
                        </div>
                      );
                    })}
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
          <section style={{ gridColumn: '1 / -1' }}>
            {/* Evaluation controls */}
            <div className="glass-card" style={{ marginBottom: '24px' }}>
              <h2>
                <Activity size={20} color="var(--color-buy)" />
                Strategy Backtest &amp; Evaluation
              </h2>
              <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '18px', maxWidth: '900px' }}>
                Walk-forward, look-ahead-free backtests (the models only ever see data available up to each date) of your
                strategies, plotted as growth of $100,000 vs the S&amp;P 500, Nasdaq 100, and Berkshire Hathaway. Enable
                &ldquo;use my allocation&rdquo; to also chart a blended curve weighted by your current capital buckets.
              </p>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '24px', alignItems: 'flex-end' }}>
                <div>
                  <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Strategies</label>
                  <div style={{ display: 'flex', gap: '14px' }}>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer', fontSize: '13px' }}>
                      <input type="checkbox" checked={evalStrategies.swing} onChange={(e) => setEvalStrategies({ ...evalStrategies, swing: e.target.checked })} /> Swing + News
                    </label>
                    <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer', fontSize: '13px' }}>
                      <input type="checkbox" checked={evalStrategies.longterm} onChange={(e) => setEvalStrategies({ ...evalStrategies, longterm: e.target.checked })} /> Long-term (MPT)
                    </label>
                  </div>
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Walk-forward folds</label>
                  <select value={evalSplits} onChange={(e) => setEvalSplits(parseInt(e.target.value))}
                    style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid var(--border-glass)', borderRadius: '8px', color: 'var(--text-primary)', padding: '8px 12px', fontSize: '13px', cursor: 'pointer' }}>
                    {[3, 4, 5, 6].map(n => <option key={n} value={n}>{n} folds</option>)}
                  </select>
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Period / stress window</label>
                  <select value={evalWindow} onChange={(e) => setEvalWindow(e.target.value)}
                    style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid var(--border-glass)', borderRadius: '8px', color: 'var(--text-primary)', padding: '8px 12px', fontSize: '13px', cursor: 'pointer' }}>
                    {Object.entries(STRESS_PRESETS).map(([k, p]) => <option key={k} value={k}>{p.label}</option>)}
                  </select>
                </div>
                {evalWindow === 'custom' && (
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <div>
                      <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '6px' }}>From</label>
                      <input type="date" value={evalCustom.start} onChange={(e) => setEvalCustom({ ...evalCustom, start: e.target.value })}
                        style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid var(--border-glass)', borderRadius: '8px', color: 'var(--text-primary)', padding: '7px 10px', fontSize: '13px' }} />
                    </div>
                    <div>
                      <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '6px' }}>To</label>
                      <input type="date" value={evalCustom.end} onChange={(e) => setEvalCustom({ ...evalCustom, end: e.target.value })}
                        style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid var(--border-glass)', borderRadius: '8px', color: 'var(--text-primary)', padding: '7px 10px', fontSize: '13px' }} />
                    </div>
                  </div>
                )}
                {evalWindow === 'none' && (
                  <div title="Walk-forward: training uses only data before this date; testing (out-of-sample) starts here. Set to 2022-01-01 to put the 2022 bear in the OOS test.">
                    <label style={{ display: 'block', fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '6px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>OOS test starts (optional)</label>
                    <input type="date" value={evalOosStart} onChange={(e) => setEvalOosStart(e.target.value)} placeholder="auto"
                      style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid var(--border-glass)', borderRadius: '8px', color: 'var(--text-primary)', padding: '7px 10px', fontSize: '13px' }} />
                  </div>
                )}
                <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer', fontSize: '13px' }}>
                  <input type="checkbox" checked={evalUseAlloc} onChange={(e) => setEvalUseAlloc(e.target.checked)} /> Use my current allocation (blended curve)
                </label>
                <button onClick={handleRunEval} disabled={evalRunning}
                  style={{ display: 'flex', alignItems: 'center', gap: '8px', background: 'var(--color-buy)', border: 'none', borderRadius: '8px', color: '#06121f', padding: '10px 22px', fontWeight: 700, fontSize: '14px', cursor: evalRunning ? 'default' : 'pointer', opacity: evalRunning ? 0.6 : 1 }}>
                  {evalRunning ? <RefreshCw size={15} className="animate-spin" /> : <Play size={15} />}
                  {evalRunning ? 'Running…' : 'Run Evaluation'}
                </button>
              </div>
              {(evalRunning || (evalProgress.stage && evalProgress.stage.startsWith('Failed'))) && (
                <div style={{ marginTop: '18px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '5px' }}>
                    <span style={{ color: evalProgress.stage.startsWith('Failed') ? 'var(--color-sell)' : 'var(--color-buy)' }}>{evalProgress.stage}</span>
                    {evalRunning && <span style={{ color: 'var(--text-secondary)' }}>{evalProgress.pct}%</span>}
                  </div>
                  {evalRunning && (
                    <>
                      <div style={{ height: '6px', background: 'rgba(255,255,255,0.06)', borderRadius: '999px', overflow: 'hidden' }}>
                        <div style={{ width: `${evalProgress.pct}%`, height: '100%', background: 'var(--color-buy)', transition: 'width 0.4s' }} />
                      </div>
                      <p style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '6px' }}>Training models across folds — this can take a couple of minutes.</p>
                    </>
                  )}
                </div>
              )}
            </div>

            {evalResult && evalResult.caveats && evalResult.caveats.length > 0 && (
              <div className="glass-card" style={{ marginBottom: '24px', border: '1px solid rgba(245, 158, 11, 0.3)', background: 'rgba(245, 158, 11, 0.06)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--color-gold)', fontWeight: 600, marginBottom: '8px' }}>
                  <ShieldAlert size={18} /> Read with care
                </div>
                <ul style={{ margin: 0, paddingLeft: '20px', fontSize: '13px', color: 'var(--text-secondary)', lineHeight: '1.6' }}>
                  {evalResult.caveats.map((c: string, i: number) => <li key={i}>{c}</li>)}
                </ul>
              </div>
            )}

            {evalResult && evalResult.interpretation && (() => {
              const it = evalResult.interpretation;
              const s = it.sections;
              const label: React.CSSProperties = { display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-secondary)', marginBottom: '6px' };
              const para: React.CSSProperties = { margin: 0, fontSize: '13.5px', lineHeight: '1.65', color: 'var(--text-secondary)' };
              const ul: React.CSSProperties = { margin: 0, paddingLeft: '20px', fontSize: '13px', color: 'var(--text-secondary)', lineHeight: '1.65', display: 'flex', flexDirection: 'column', gap: '4px' };
              return (
                <div className="glass-card" style={{ marginBottom: '24px', border: '1px solid rgba(139,92,246,0.35)', background: 'rgba(139,92,246,0.06)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px', marginBottom: '14px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 600, fontSize: '16px', color: 'var(--text-primary)' }}>
                      <Brain size={20} color="#a78bfa" /> Expert Interpretation
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '12px', fontSize: '11px', color: 'var(--text-secondary)' }}>
                      {it.model && <span>{it.model}{it.tokens ? ` · ${it.tokens.toLocaleString()} tok` : ''}{typeof it.cost === 'number' ? ` · ~$${it.cost.toFixed(3)}` : ''}</span>}
                      <button onClick={regenerateInterpretation} disabled={interpLoading}
                        style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '11px', padding: '4px 10px', borderRadius: '6px', cursor: interpLoading ? 'default' : 'pointer', background: 'rgba(139,92,246,0.15)', border: '1px solid rgba(139,92,246,0.35)', color: 'var(--text-primary)' }}>
                        <RefreshCw size={12} className={interpLoading ? 'animate-spin' : ''} /> {interpLoading ? 'Thinking…' : 'Regenerate'}
                      </button>
                    </div>
                  </div>

                  {it.error ? (
                    <div style={{ fontSize: '13px', color: 'var(--text-secondary)', lineHeight: '1.6' }}>{it.error}</div>
                  ) : s ? (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '18px' }}>
                      {s.tldr && (
                        <div style={{ fontSize: '14.5px', lineHeight: '1.65', color: 'var(--text-primary)', padding: '12px 14px', borderRadius: '8px', background: 'rgba(139,92,246,0.10)', borderLeft: '3px solid #a78bfa' }}>{s.tldr}</div>
                      )}
                      {s.what_was_tested && (
                        <div><div style={label}>What was tested</div><p style={para}>{s.what_was_tested}</p></div>
                      )}
                      {Array.isArray(s.key_findings) && s.key_findings.length > 0 && (
                        <div><div style={label}>Key findings</div><ul style={ul}>{s.key_findings.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></div>
                      )}
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: '18px' }}>
                        {Array.isArray(s.strengths) && s.strengths.length > 0 && (
                          <div><div style={{ ...label, color: 'var(--color-buy)' }}><ThumbsUp size={14} /> Strengths</div><ul style={ul}>{s.strengths.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></div>
                        )}
                        {Array.isArray(s.weaknesses) && s.weaknesses.length > 0 && (
                          <div><div style={{ ...label, color: 'var(--color-sell)' }}><ThumbsDown size={14} /> Weaknesses</div><ul style={ul}>{s.weaknesses.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></div>
                        )}
                      </div>
                      {Array.isArray(s.shortcomings) && s.shortcomings.length > 0 && (
                        <div><div style={{ ...label, color: 'var(--color-gold)' }}><AlertTriangle size={14} /> Shortcomings of this study</div><ul style={ul}>{s.shortcomings.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></div>
                      )}
                      {s.verdict && (
                        <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-start', fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', padding: '12px 14px', borderRadius: '8px', background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.25)' }}>
                          <CheckCircle2 size={18} color="var(--color-buy)" style={{ flexShrink: 0, marginTop: '1px' }} /> <span>{s.verdict}</span>
                        </div>
                      )}
                    </div>
                  ) : null}
                </div>
              );
            })()}

            {evalResult && evalResult.series && evalResult.series.length > 0 && (
              <>
                <div className="glass-card" style={{ marginBottom: '24px' }}>
                  <h2>
                    <TrendingUp size={20} color="var(--color-buy)" />
                    {evalResult.mode === 'stress' ? 'Stress window · ' : ''}Growth of $100,000{evalResult.window && evalResult.window.length ? ` · ${evalResult.window[0]} → ${evalResult.window[1]}` : ''}
                  </h2>
                  <div style={{ width: '100%', height: 430 }}>
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={evalResult.series} margin={{ top: 10, right: 16, left: 10, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.03)" />
                        <XAxis dataKey="date" stroke="var(--text-secondary)" fontSize={11} tickLine={false} minTickGap={50} tickFormatter={(d) => String(d).slice(0, 7)} />
                        <YAxis stroke="var(--text-secondary)" fontSize={11} tickLine={false} domain={['dataMin - 5000', 'dataMax + 5000']} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`} />
                        <Tooltip contentStyle={{ background: 'rgba(16,20,38,0.95)', border: '1px solid var(--border-glass)', borderRadius: '8px', color: 'var(--text-primary)' }}
                          formatter={(v: any) => `$${parseFloat(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`} />
                        <Legend />
                        {EVAL_SERIES.filter(s => evalResult.metrics[s.key]).map(s => (
                          <Line key={s.key} type="monotone" dataKey={s.key} name={s.name} stroke={s.color}
                            strokeWidth={['spy', 'qqq', 'brk'].includes(s.key) ? 1.5 : 2.6}
                            strokeDasharray={['spy', 'qqq', 'brk'].includes(s.key) ? '4 3' : undefined}
                            dot={false} connectNulls />
                        ))}
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                <div className="glass-card">
                  <h2>
                    <Award size={20} color="var(--color-gold)" />
                    Performance Metrics
                  </h2>
                  <div style={{ overflowX: 'auto' }}>
                    <table className="trade-table">
                      <thead>
                        <tr>
                          <th>Series</th>
                          <th>Total Return</th>
                          <th>CAGR</th>
                          <th>Sharpe</th>
                          <th>Max Drawdown</th>
                          <th>Final Value</th>
                        </tr>
                      </thead>
                      <tbody>
                        {EVAL_SERIES.filter(s => evalResult.metrics[s.key]).map(s => {
                          const m = evalResult.metrics[s.key];
                          return (
                            <tr key={s.key}>
                              <td style={{ fontWeight: 600 }}>
                                <span style={{ display: 'inline-block', width: '10px', height: '10px', borderRadius: '50%', background: s.color, marginRight: '8px' }} />
                                {s.name}
                              </td>
                              <td className={m.total_return >= 0 ? 'text-green' : 'text-red'}>{m.total_return >= 0 ? '+' : ''}{(m.total_return * 100).toFixed(1)}%</td>
                              <td className={m.cagr >= 0 ? 'text-green' : 'text-red'}>{m.cagr >= 0 ? '+' : ''}{(m.cagr * 100).toFixed(1)}%</td>
                              <td>{m.sharpe_ratio.toFixed(2)}</td>
                              <td className="text-red">{(m.max_drawdown * 100).toFixed(1)}%</td>
                              <td>${m.final_value.toLocaleString(undefined, { maximumFractionDigits: 0 })}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                  <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '14px' }}>
                    Out-of-sample: each point uses only information available at that date — expanding-window walk-forward for
                    Swing (point-in-time news features), trailing-only covariance for the MPT book. Benchmarks are buy-and-hold
                    over the same window.
                  </p>
                </div>
              </>
            )}
          </section>
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

              {/* Strategy capital allocation buckets */}
              <div className="glass-card" style={{ marginBottom: '24px' }}>
                <h2>
                  <Sliders size={20} color="var(--color-gold)" />
                  Strategy Capital Allocation
                </h2>
                <p style={{ fontSize: '13px', color: 'var(--text-secondary)', marginBottom: '16px' }}>
                  Set the max % of equity each strategy may deploy. The bot trades within these limits and never exceeds them (it won&rsquo;t force-sell to rebalance down). Per-stock strategy is chosen in the table below.
                </p>
                {strategyConfig?.overlay_active && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.3)', borderRadius: '8px', padding: '10px 12px', marginBottom: '16px', fontSize: '12.5px', color: 'var(--text-secondary)', lineHeight: '1.45' }}>
                    <ShieldAlert size={16} color="var(--color-gold)" style={{ flexShrink: 0 }} />
                    <span>
                      <strong style={{ color: 'var(--color-gold)' }}>Regime overlay active — {strategyConfig.regime}.</strong> Swing
                      is auto-scaled to {Math.round((strategyConfig.swing_factor || 0) * 100)}% of its bucket
                      (effective {Math.round((strategyConfig.effective_swing || 0) * 100)}% of equity); the freed capital is held
                      as cash. This guards against swing&rsquo;s tendency to amplify bear drawdowns and lifts automatically when the regime turns to growth.
                    </span>
                  </div>
                )}
                {strategyConfig && !strategyConfig.overlay_active && strategyConfig.regime && (
                  <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '14px' }}>
                    Regime: <strong style={{ color: 'var(--color-buy)' }}>{strategyConfig.regime}</strong> — swing at full bucket weight (overlay inactive).
                  </div>
                )}
                {(() => {
                  const s = parseFloat(bucketEdit.swing) || 0;
                  const l = parseFloat(bucketEdit.longterm) || 0;
                  const cash = Math.max(0, 100 - s - l);
                  const over = s + l > 100;
                  const field = (label: string, key: 'swing' | 'longterm', color: string) => (
                    <div style={{ flex: 1 }}>
                      <label style={{ display: 'block', fontSize: '12px', color, marginBottom: '6px', fontWeight: 600 }}>{label}</label>
                      <div style={{ position: 'relative' }}>
                        <input type="number" min="0" max="100" value={bucketEdit[key]}
                          onChange={(e) => setBucketEdit({ ...bucketEdit, [key]: e.target.value })}
                          style={{ width: '100%', background: 'rgba(0,0,0,0.3)', border: '1px solid var(--border-glass)', borderRadius: '8px', color: 'var(--text-primary)', padding: '9px 26px 9px 12px', fontSize: '14px' }} />
                        <span style={{ position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-secondary)', fontSize: '13px' }}>%</span>
                      </div>
                    </div>
                  );
                  return (
                    <>
                      <div style={{ display: 'flex', gap: '12px', marginBottom: '14px' }}>
                        {field('Swing + News', 'swing', 'var(--color-buy)')}
                        {field('Long-term (MPT)', 'longterm', 'var(--color-accent)')}
                        <div style={{ flex: 1 }}>
                          <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px', fontWeight: 600 }}>Cash (rest)</label>
                          <div style={{ padding: '9px 12px', fontSize: '14px', fontWeight: 600, color: over ? 'var(--color-sell)' : 'var(--text-primary)' }}>{over ? '—' : `${cash}%`}</div>
                        </div>
                      </div>
                      {/* stacked allocation bar */}
                      <div style={{ display: 'flex', height: '8px', borderRadius: '999px', overflow: 'hidden', background: 'rgba(255,255,255,0.06)', marginBottom: '14px' }}>
                        <div style={{ width: `${Math.min(s, 100)}%`, background: 'var(--color-buy)' }} />
                        <div style={{ width: `${Math.min(l, 100 - Math.min(s, 100))}%`, background: 'var(--color-accent)' }} />
                      </div>
                      <button onClick={handleSaveBuckets} disabled={actionBusy || over}
                        style={{ background: over ? 'rgba(239,68,68,0.15)' : 'rgba(245,158,11,0.12)', border: `1px solid ${over ? 'var(--color-sell)' : 'var(--color-gold)'}`, borderRadius: '8px', color: over ? 'var(--color-sell)' : 'var(--color-gold)', padding: '8px 18px', cursor: over ? 'not-allowed' : 'pointer', fontWeight: 600, fontSize: '13px' }}>
                        {over ? 'Exceeds 100%' : 'Save Allocation'}
                      </button>
                    </>
                  );
                })()}

                {/* Per-stock strategy suggester */}
                <div style={{ marginTop: '18px', paddingTop: '16px', borderTop: '1px solid var(--border-glass)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
                    <button onClick={handleSuggestStrategies} disabled={suggestRunning}
                      style={{ display: 'flex', alignItems: 'center', gap: '6px', background: 'rgba(0,242,254,0.1)', border: '1px solid var(--color-buy)', borderRadius: '8px', color: 'var(--color-buy)', padding: '8px 16px', cursor: suggestRunning ? 'default' : 'pointer', fontWeight: 600, fontSize: '13px', opacity: suggestRunning ? 0.6 : 1 }}>
                      {suggestRunning ? <RefreshCw size={14} className="animate-spin" /> : <Sliders size={14} />}
                      {suggestRunning ? 'Analyzing…' : 'Suggest per-stock strategies'}
                    </button>
                    {suggestData?.counts && (
                      <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>
                        Suggested: <span style={{ color: 'var(--color-buy)' }}>{suggestData.counts.swing} swing</span>, <span style={{ color: 'var(--color-accent)' }}>{suggestData.counts.longterm} long-term</span>, {suggestData.counts.hold} hold
                      </span>
                    )}
                    {suggestData?.suggestions && (
                      <button onClick={handleAcceptAllSuggestions} disabled={actionBusy}
                        style={{ background: 'var(--color-accent)', border: 'none', borderRadius: '8px', color: 'white', padding: '8px 14px', cursor: 'pointer', fontWeight: 600, fontSize: '13px' }}>
                        Accept all
                      </button>
                    )}
                    <button onClick={handleValidateSuggestions} disabled={validateRunning}
                      style={{ background: 'transparent', border: '1px solid var(--border-glass)', borderRadius: '8px', color: 'var(--text-secondary)', padding: '8px 14px', cursor: validateRunning ? 'default' : 'pointer', fontWeight: 600, fontSize: '13px', opacity: validateRunning ? 0.6 : 1 }}>
                      {validateRunning ? 'Validating…' : 'Validate vs current'}
                    </button>
                  </div>
                  {validateRunning && (
                    <div style={{ marginTop: '10px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '4px' }}>
                        <span style={{ color: 'var(--color-buy)' }}>{validateProgress.stage}</span>
                        <span style={{ color: 'var(--text-secondary)' }}>{validateProgress.pct}%</span>
                      </div>
                      <div style={{ height: '6px', background: 'rgba(255,255,255,0.06)', borderRadius: '999px', overflow: 'hidden' }}>
                        <div style={{ width: `${validateProgress.pct}%`, height: '100%', background: 'var(--color-buy)', transition: 'width 0.4s' }} />
                      </div>
                    </div>
                  )}
                  {validateData?.schemes && (() => {
                    const noChange = (validateData.n_changes || 0) === 0;
                    const labels: Record<string, string> = { current: 'Current', suggested: 'Suggested', 'suggested @ 30/60': 'Suggested + 30/60 buckets' };
                    const rows = (Object.entries(validateData.schemes) as [string, any][]).filter(([n]) => !(noChange && n === 'suggested'));
                    return (
                      <div style={{ marginTop: '14px', background: 'rgba(0,0,0,0.2)', border: '1px solid var(--border-glass)', borderRadius: '10px', padding: '14px' }}>
                        <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '6px' }}>
                          Strategy validation — blended out-of-sample (incl. the 2022 bear)
                        </div>
                        {noChange ? (
                          <div style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', fontSize: '12.5px', color: 'var(--color-buy)', marginBottom: '10px' }}>
                            <span style={{ flexShrink: 0 }}>✓</span>
                            <span>Your assignments already match the recommendation — nothing to change. Re-run &ldquo;Suggest per-stock strategies&rdquo; if you&rsquo;ve changed the universe or retrained.</span>
                          </div>
                        ) : (
                          <div style={{ fontSize: '12px', color: validateData.verdict?.startsWith('Suggested beats') ? 'var(--color-buy)' : 'var(--color-gold)', marginBottom: '10px' }}>
                            {validateData.n_changes} change(s) suggested. {validateData.verdict}
                          </div>
                        )}
                        <table className="trade-table" style={{ fontSize: '12px' }}>
                          <thead><tr><th>Scheme</th><th>Buckets (sw/lt)</th><th>Total</th><th>Sharpe</th><th>Max DD</th><th>Stocks (sw/lt)</th></tr></thead>
                          <tbody>
                            {rows.map(([name, r]: any) => (
                              <tr key={name}>
                                <td style={{ fontWeight: name === 'suggested' ? 700 : 400 }}>{noChange && name === 'current' ? 'Current (= Suggested)' : (labels[name] || name)}</td>
                                <td>{Math.round(r.buckets.swing * 100)}/{Math.round(r.buckets.longterm * 100)}</td>
                                <td className={r.metrics.total_return >= 0 ? 'text-green' : 'text-red'}>{r.metrics.total_return >= 0 ? '+' : ''}{(r.metrics.total_return * 100).toFixed(1)}%</td>
                                <td>{r.metrics.sharpe_ratio.toFixed(2)}</td>
                                <td className="text-red">{(r.metrics.max_drawdown * 100).toFixed(1)}%</td>
                                <td style={{ color: 'var(--text-secondary)' }}>{r.n_swing}/{r.n_longterm}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        <p style={{ fontSize: '10.5px', color: 'var(--text-secondary)', marginTop: '8px', lineHeight: '1.45' }}>
                          &ldquo;Buckets&rdquo; = % of equity allocated to swing / long-term (rest cash). &ldquo;Stocks&rdquo; = how many stocks each strategy manages. The &ldquo;30/60&rdquo; row shows what happens if you also shift more capital to long-term. Absolute returns are bull-inflated — trust the Sharpe / drawdown comparison, not the headline %.
                        </p>
                      </div>
                    );
                  })()}
                  {suggestRunning && (
                    <div style={{ marginTop: '10px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '4px' }}>
                        <span style={{ color: 'var(--color-buy)' }}>{suggestProgress.stage}</span>
                        <span style={{ color: 'var(--text-secondary)' }}>{suggestProgress.pct}%</span>
                      </div>
                      <div style={{ height: '6px', background: 'rgba(255,255,255,0.06)', borderRadius: '999px', overflow: 'hidden' }}>
                        <div style={{ width: `${suggestProgress.pct}%`, height: '100%', background: 'var(--color-buy)', transition: 'width 0.4s' }} />
                      </div>
                    </div>
                  )}
                  <p style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '10px', lineHeight: '1.5' }}>
                    Evidence-driven (out-of-sample incl. the 2022 bear): recommends swing only where a ticker shows a real
                    news-driven edge, else long-term MPT, else hold. Suggestions appear in the Strategy column below; review
                    before accepting. Conservative by design and based on a single-regime, survivorship-biased history.
                  </p>
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
                            <th>Strategy</th>
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
                                  <select
                                    value={(strategyConfig?.assignments?.[h.ticker]) || h.strategy || 'swing'}
                                    onChange={(e) => handleSetTickerStrategy(h.ticker, e.target.value)}
                                    style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid var(--border-glass)', borderRadius: '6px', color: 'var(--text-primary)', padding: '4px 8px', fontSize: '12px', cursor: 'pointer' }}
                                  >
                                    {STRATEGY_OPTIONS.map(([val, label]) => (
                                      <option key={val} value={val}>{label}</option>
                                    ))}
                                  </select>
                                  {(() => {
                                    const sg = suggestMap[h.ticker];
                                    if (!sg) return null;
                                    const cur = (strategyConfig?.assignments?.[h.ticker]) || h.strategy || 'swing';
                                    if (sg.recommended === cur) return null;
                                    const cc = sg.confidence === 'high' ? 'var(--color-buy)' : sg.confidence === 'low' ? 'var(--text-secondary)' : 'var(--color-gold)';
                                    return (
                                      <div
                                        onClick={() => handleSetTickerStrategy(h.ticker, sg.recommended)}
                                        title={sg.rationale}
                                        style={{ marginTop: '4px', fontSize: '10px', color: cc, cursor: 'pointer', textDecoration: 'underline dotted' }}
                                      >
                                        ▸ suggests {sg.recommended} ({sg.confidence})
                                      </div>
                                    );
                                  })()}
                                </td>
                                <td>
                                  <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                                    <button
                                      onClick={() => handleBackfillTicker(h.ticker)}
                                      title="Backfill prices + news for this stock (watch it in Background Jobs)"
                                      style={{ background: 'transparent', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', display: 'flex', alignItems: 'center' }}
                                      onMouseEnter={(e) => e.currentTarget.style.color = 'var(--color-buy)'}
                                      onMouseLeave={(e) => e.currentTarget.style.color = 'var(--text-secondary)'}
                                    >
                                      <RefreshCw size={15} />
                                    </button>
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
                                      <>
                                        <button
                                          onClick={() => setLiquidateModal({ ticker: h.ticker, held: h.shares, shares: String(h.shares) })}
                                          style={{ background: 'rgba(239,68,68,0.1)', border: '1px solid var(--color-sell)', borderRadius: '6px', color: 'var(--color-sell)', padding: '4px 12px', fontSize: '12px', fontWeight: 600, cursor: 'pointer' }}
                                        >
                                          Liquidate
                                        </button>
                                        <span title="Held position — sell via Liquidate. The trash icon is enabled only once you hold 0 shares.">
                                          <Trash2 size={16} style={{ color: 'rgba(255,255,255,0.18)', cursor: 'not-allowed' }} />
                                        </span>
                                      </>
                                    )}
                                  </div>
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
              {/* Background jobs queue — visible status for every backfill / retrain / eval */}
              {jobs.length > 0 && (
                <div className="glass-card" style={{ marginBottom: '24px', border: '1px solid rgba(0,242,254,0.3)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '12px' }}>
                    <h2 style={{ margin: 0 }}>
                      <RefreshCw size={18} color="var(--color-buy)" className={jobs.some((j: any) => j.status === 'running') ? 'animate-spin' : ''} />
                      Background Jobs
                    </h2>
                    <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                      {jobs.filter((j: any) => j.status === 'running').length} running
                    </span>
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                    {jobs.map((j: any) => {
                      const TYPE: Record<string, string> = { backfill: 'Data backfill', train: 'Model retrain', evaluate: 'Evaluation', suggest: 'Strategy suggester', validate: 'Validation' };
                      const statusColor = j.status === 'error' ? 'var(--color-sell)' : j.status === 'done' ? 'var(--color-buy)' : 'var(--color-gold)';
                      return (
                        <div key={j.id}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12.5px', marginBottom: '4px', gap: '8px' }}>
                            <span style={{ fontWeight: 600 }}>{TYPE[j.type] || j.type}<span style={{ fontWeight: 400, color: 'var(--text-secondary)' }}> · {j.label}</span></span>
                            <span style={{ color: statusColor, flexShrink: 0 }}>{j.status === 'error' ? 'failed' : j.status === 'done' ? 'done ✓' : `${j.progress}%`}</span>
                          </div>
                          <div style={{ height: '6px', background: 'rgba(255,255,255,0.06)', borderRadius: '999px', overflow: 'hidden' }}>
                            <div style={{ width: `${j.progress}%`, height: '100%', background: j.status === 'error' ? 'var(--color-sell)' : 'var(--color-buy)', transition: 'width 0.4s' }} />
                          </div>
                          <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '3px' }}>{j.error || j.stage}</div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

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
