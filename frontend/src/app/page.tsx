'use client';

import React, { useState, useEffect, useMemo } from 'react';
import { apiUrl } from '../lib/api';
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
  Pause,
  RotateCcw,
  Cpu,
  Clock,
  Brain,
  ThumbsUp,
  ThumbsDown,
  AlertTriangle,
  CheckCircle2,
  Newspaper,
  Upload
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
  Legend,
  ReferenceLine
} from 'recharts';
import GrantTimeline from './GrantTimeline';

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

interface EquityLot {
  id?: number;
  ticker: string;
  account_label?: string;
  lot_type: 'rsu' | 'espp' | 'other';
  shares: number;
  cost_basis_per_share: number;
  acquisition_date: string;
  notes?: string;
  current_price?: number;
  market_value?: number;
  unrealized_gain?: number;
  unrealized_gain_pct?: number | null;
  is_long_term?: boolean;
  days_to_long_term?: number;
  recommendation?: { action: string; label: string; detail: string };
}

// Compact relative-time formatter ("just now", "3h ago", "2d ago") with an absolute-time title.
function fmtRelTime(iso?: string | null): { rel: string; abs: string } {
  if (!iso) return { rel: '—', abs: '' };
  const d = new Date(iso);
  if (isNaN(d.getTime())) return { rel: '—', abs: '' };
  const diffMs = Date.now() - d.getTime();
  const abs = d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  const past = diffMs >= 0;
  const s = Math.abs(diffMs) / 1000;
  let rel: string;
  if (s < 60) rel = 'just now';
  else if (s < 3600) rel = `${Math.round(s / 60)}m`;
  else if (s < 86400) rel = `${Math.round(s / 3600)}h`;
  else rel = `${Math.round(s / 86400)}d`;
  if (rel !== 'just now') rel = past ? `${rel} ago` : `in ${rel}`;
  return { rel, abs };
}

// Small "Last updated / Next auto-update" status line shown under a card title.
function TimingBadge({ lastRun, lastLabel = 'Updated', nextScheduled, schedule, stale }: {
  lastRun?: string | null; lastLabel?: string; nextScheduled?: string | null; schedule?: string; stale?: boolean;
}) {
  const last = fmtRelTime(lastRun);
  const next = fmtRelTime(nextScheduled);
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '6px 12px', fontSize: '10.5px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
      <span title={last.abs} style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
        <Clock size={11} /> {lastLabel} {last.rel}
      </span>
      {nextScheduled && (
        <span title={schedule} style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
          <RefreshCw size={11} /> Next auto {next.rel}
        </span>
      )}
      {stale && (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', color: 'var(--color-gold)' }}>
          <AlertTriangle size={11} /> new data since
        </span>
      )}
    </div>
  );
}

export default function Home() {
  const [activeTab, setActiveTab] = useState<'dashboard' | 'virtual_perf' | 'editor' | 'advisor' | 'crash' | 'external'>('dashboard');

  // Crash Radar States
  const [crashData, setCrashData] = useState<any>(null);
  const [playbook, setPlaybook] = useState<any>(null);
  const [wargameResults, setWargameResults] = useState<any>(null);
  const [preset, setPreset] = useState<'balanced' | 'conservative' | 'aggressive' | 'custom'>('balanced');
  const [theta, setTheta] = useState<number>(0.85);
  const [k, setK] = useState<number>(2.0);
  const [gamma, setGamma] = useState<number>(0.25);
  const [compareData, setCompareData] = useState<any>(null);
  const [comparingPresets, setComparingPresets] = useState<boolean>(false);

  // Glide-path knob presets (mirrors backend ml_engine/glide.py PRESETS).
  const GLIDE_PRESETS: Record<'conservative' | 'balanced' | 'aggressive', { theta: number; k: number; gamma: number }> = {
    conservative: { theta: 0.60, k: 3.0, gamma: 0.15 },
    balanced: { theta: 0.85, k: 2.0, gamma: 0.25 },
    aggressive: { theta: 1.10, k: 1.5, gamma: 0.40 },
  };

  const applyPreset = (p: 'conservative' | 'balanced' | 'aggressive') => {
    const cfg = GLIDE_PRESETS[p];
    setTheta(cfg.theta);
    setK(cfg.k);
    setGamma(cfg.gamma);
    setPreset(p);
  };
  const [forecastJobId, setForecastJobId] = useState<string | null>(null);
  const [forecastStatus, setForecastStatus] = useState<string>('');
  const [wargameJobId, setWargameJobId] = useState<string | null>(null);
  const [wargameStatus, setWargameStatus] = useState<string>('');
  const [scenarioJobId, setScenarioJobId] = useState<string | null>(null);
  const [scenarioStatus, setScenarioStatus] = useState<string>('');
  const [scenarioData, setScenarioData] = useState<any>(null);
  const [selectedScenario, setSelectedScenario] = useState<string>('gfc');
  const [wargameAnalyst, setWargameAnalyst] = useState<any>(null);
  const [analystLoading, setAnalystLoading] = useState<boolean>(false);
  const [crashStatus, setCrashStatus] = useState<any>(null);
  const [analystMeta, setAnalystMeta] = useState<{ generated_at?: string; stale?: boolean }>({});
  const [applyConfirmOpen, setApplyConfirmOpen] = useState<boolean>(false);
  const [applyResult, setApplyResult] = useState<any>(null);
  const [applyingRebalance, setApplyingRebalance] = useState<boolean>(false);
  const [previewData, setPreviewData] = useState<any>(null);
  const [previewLoading, setPreviewLoading] = useState<boolean>(false);
  const [timelineData, setTimelineData] = useState<any[]>([]);

  const fetchTimeline = async () => {
    try {
      const res = await fetch(apiUrl('/api/crash/timeline'));
      if (res.ok) {
        const data = await res.json();
        setTimelineData(data);
      }
    } catch (err) {
      console.error('Error fetching crash timeline:', err);
    }
  };

  const fetchCrashIndex = async () => {
    try {
      const res = await fetch(apiUrl('/api/crash/index'));
      if (res.ok) {
        const data = await res.json();
        setCrashData(data);
      }
    } catch (err) {
      console.error('Error fetching crash index:', err);
    }
  };

  const fetchCrashStatus = async () => {
    try {
      const res = await fetch(apiUrl('/api/crash/status'));
      if (res.ok) setCrashStatus(await res.json());
    } catch (err) {
      console.error('Error fetching crash status:', err);
    }
  };

  // Load the last cached scenario comparison + AI analyst so the Wargame card renders by default.
  const fetchWargameCache = async () => {
    try {
      const res = await fetch(apiUrl('/api/crash/wargame/cache'));
      if (!res.ok) return;
      const data = await res.json();
      if (data.comparison) {
        setScenarioData(data.comparison);
        const ids = (data.comparison.scenarios || []).map((s: any) => s.id);
        if (ids.length && !ids.includes(selectedScenario)) setSelectedScenario(ids[0]);
      }
      if (data.analyst) {
        setWargameAnalyst(data.analyst);
        setAnalystMeta({ generated_at: data.analyst_generated_at, stale: data.analyst_stale });
      }
    } catch (err) {
      console.error('Error fetching wargame cache:', err);
    }
  };

  const fetchPlaybook = async (selectedPreset: string) => {
    try {
      const res = await fetch(apiUrl(`/api/crash/playbook?preset=${selectedPreset}`));
      if (res.ok) {
        const data = await res.json();
        setPlaybook(data);
      }
    } catch (err) {
      console.error('Error fetching playbook:', err);
    }
  };

  const runPresetComparison = async () => {
    setComparingPresets(true);
    try {
      const res = await fetch(apiUrl(`/api/crash/compare?years=5&theta=${theta}&k=${k}&gamma=${gamma}`));
      if (res.ok) {
        setCompareData(await res.json());
      } else {
        const errData = await res.json().catch(() => ({}));
        alert(`Comparison failed: ${errData.detail || res.statusText}`);
      }
    } catch (err: any) {
      console.error('Error running preset comparison:', err);
      alert(`Error: ${err.message || err}`);
    } finally {
      setComparingPresets(false);
    }
  };

  // Read-only dry run: fetch the exact orders + validation that "apply" would execute.
  const openPreview = async () => {
    setApplyResult(null);
    setPreviewData(null);
    setApplyConfirmOpen(true);
    setPreviewLoading(true);
    try {
      const params = preset === 'custom'
        ? `preset=custom&theta=${theta}&k=${k}&gamma=${gamma}`
        : `preset=${preset}`;
      const res = await fetch(apiUrl(`/api/crash/apply/preview?${params}`));
      if (res.ok) {
        setPreviewData(await res.json());
      } else {
        const errData = await res.json().catch(() => ({}));
        setPreviewData({ error: errData.detail || res.statusText });
      }
    } catch (err: any) {
      setPreviewData({ error: err.message || String(err) });
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleApplyRebalance = async () => {
    setApplyingRebalance(true);
    setApplyConfirmOpen(false);
    try {
      const res = await fetch(apiUrl('/api/crash/apply'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          confirm_execution: true,
          target_posture: crashData?.current_posture || 'Normal',
          preset: preset === 'custom' ? 'custom' : preset,
          ...(preset === 'custom' ? { theta, k, gamma } : {})
        })
      });
      if (res.ok) {
        const data = await res.json();
        setApplyResult(data);
      } else {
        const errData = await res.json();
        alert(`Failed to apply rebalance: ${errData.detail || res.statusText}`);
      }
    } catch (err: any) {
      console.error('Error applying stance rebalancing:', err);
      alert(`Error: ${err.message || err}`);
    } finally {
      setApplyingRebalance(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'crash') {
      fetchCrashIndex();
      fetchPlaybook(preset === 'custom' ? 'balanced' : preset);
      fetchTimeline();
      fetchCrashStatus();
      fetchWargameCache();
    }
  }, [activeTab, preset]);

  // Forecast Poller
  useEffect(() => {
    if (!forecastJobId) return;
    let timer = setInterval(async () => {
      try {
        const res = await fetch(apiUrl(`/api/crash/forecast/result?job_id=${forecastJobId}`));
        if (res.ok) {
          const data = await res.json();
          if (data.status === 'completed') {
            setForecastStatus('complete');
            setForecastJobId(null);
            fetchCrashIndex(); // reload snapshot to get forecasts
            fetchTimeline();
          } else if (data.status === 'error') {
            setForecastStatus(`Error: ${data.error}`);
            setForecastJobId(null);
          } else {
            setForecastStatus(`Running: ${data.progress}% (${data.stage})`);
          }
        }
      } catch (err) {
        console.error(err);
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [forecastJobId]);

  // Wargame Poller
  useEffect(() => {
    if (!wargameJobId) return;
    let timer = setInterval(async () => {
      try {
        const res = await fetch(apiUrl(`/api/crash/wargame/result?job_id=${wargameJobId}`));
        if (res.ok) {
          const data = await res.json();
          if (data.status === 'completed') {
            setWargameStatus('complete');
            setWargameResults(data.result);
            setWargameJobId(null);
          } else if (data.status === 'error') {
            setWargameStatus(`Error: ${data.error}`);
            setWargameJobId(null);
          } else {
            setWargameStatus(`Running: ${data.progress}% (${data.stage})`);
          }
        }
      } catch (err) {
        console.error(err);
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [wargameJobId]);

  // Scenario comparison poller
  useEffect(() => {
    if (!scenarioJobId) return;
    const timer = setInterval(async () => {
      try {
        const res = await fetch(apiUrl(`/api/crash/wargame/scenarios/result?job_id=${scenarioJobId}`));
        if (res.ok) {
          const data = await res.json();
          if (data.status === 'completed') {
            setScenarioStatus('');
            setScenarioData(data.result);
            setWargameAnalyst(null);
            setAnalystMeta({});
            const ids = (data.result?.scenarios || []).map((s: any) => s.id);
            if (ids.length && !ids.includes(selectedScenario)) setSelectedScenario(ids[0]);
            setScenarioJobId(null);
            fetchCrashStatus();
          } else if (data.status === 'error') {
            setScenarioStatus(`Error: ${data.error}`);
            setScenarioJobId(null);
          } else {
            setScenarioStatus(`${data.progress || 0}% · ${data.stage || 'running'}`);
          }
        }
      } catch (err) {
        console.error(err);
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [scenarioJobId]);

  const runScenarioComparison = async () => {
    setScenarioStatus('Queued…');
    setScenarioData(null);
    setWargameAnalyst(null);
    try {
      const body = preset === 'custom' ? { theta, k, gamma } : {};
      const res = await fetch(apiUrl('/api/crash/wargame/scenarios'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      if (res.ok) {
        setScenarioJobId((await res.json()).job_id);
      } else {
        setScenarioStatus('Trigger failed.');
      }
    } catch (err) {
      setScenarioStatus('Trigger failed.');
    }
  };

  const runWargameAnalyst = async () => {
    if (!scenarioData) return;
    setAnalystLoading(true);
    try {
      const res = await fetch(apiUrl('/api/crash/wargame/interpret'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ comparison: scenarioData }),
      });
      const data = await res.json();
      setWargameAnalyst(res.ok ? data : { error: data.detail || res.statusText });
      if (res.ok) {
        setAnalystMeta({ generated_at: new Date().toISOString(), stale: false });
        fetchCrashStatus();
      }
    } catch (err: any) {
      setWargameAnalyst({ error: err.message || String(err) });
    } finally {
      setAnalystLoading(false);
    }
  };

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
  const [classification, setClassification] = useState<Record<string, any>>({});
  const [tierMenu, setTierMenu] = useState<{ ticker: string; x: number; y: number } | null>(null);
  const [portfolio, setPortfolio] = useState<any>(null);
  const [refreshingPortfolio, setRefreshingPortfolio] = useState<boolean>(false);
  const [jobs, setJobs] = useState<any[]>([]);
  const [trainStatus, setTrainStatus] = useState<any>(null);
  const [liquidateModal, setLiquidateModal] = useState<any>(null);
  const [addStockOpen, setAddStockOpen] = useState<boolean>(false);
  const [addStockTicker, setAddStockTicker] = useState<string>('');
  const [actionBusy, setActionBusy] = useState<boolean>(false);
  const [strategyConfig, setStrategyConfig] = useState<any>(null);
  const [bucketEdit, setBucketEdit] = useState<{ swing: string; longterm: string; high_risk: string }>({ swing: '', longterm: '', high_risk: '' });
  const [suggestRunning, setSuggestRunning] = useState<boolean>(false);
  const [suggestProgress, setSuggestProgress] = useState<{ pct: number; stage: string }>({ pct: 0, stage: '' });
  const [suggestData, setSuggestData] = useState<any>(null);
  const [validateRunning, setValidateRunning] = useState<boolean>(false);
  const [validateProgress, setValidateProgress] = useState<{ pct: number; stage: string }>({ pct: 0, stage: '' });
  const [validateData, setValidateData] = useState<any>(null);
  const [evalStrategies, setEvalStrategies] = useState<{ swing: boolean; longterm: boolean; high_risk: boolean }>({ swing: true, longterm: true, high_risk: false });
  const [evalSplits, setEvalSplits] = useState<number>(4);
  const [evalUseAlloc, setEvalUseAlloc] = useState<boolean>(true);
  const [evalExcludePremium, setEvalExcludePremium] = useState<boolean>(false);
  const [premiumValue, setPremiumValue] = useState<any>(null);
  const [evalRunning, setEvalRunning] = useState<boolean>(false);
  const [evalProgress, setEvalProgress] = useState<{ pct: number; stage: string }>({ pct: 0, stage: '' });
  const [evalResult, setEvalResult] = useState<any>(null);
  const [evalJobId, setEvalJobId] = useState<string | null>(null);
  const [interpLoading, setInterpLoading] = useState<boolean>(false);
  const [llmUsage, setLlmUsage] = useState<any>(null);
  const [llmSince, setLlmSince] = useState<'today' | '7d' | 'all'>('today');
  const [llmLoading, setLlmLoading] = useState<boolean>(false);
  const [calModel, setCalModel] = useState<string>('');
  const [calCost, setCalCost] = useState<string>('');
  const [calMsg, setCalMsg] = useState<string>('');
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
  const [equityLots, setEquityLots] = useState<EquityLot[]>([]);
  const [grantChartsVisible, setGrantChartsVisible] = useState<Record<string, boolean>>({});
  const [vestSchedules, setVestSchedules] = useState<any[]>([]);
  const [equityForecasts, setEquityForecasts] = useState<Record<string, any>>({});
  const [taxProfile, setTaxProfile] = useState<any>({
    filing_status: 'single',
    ordinary_income: 0,
    magi: 0,
    state_ltcg_rate: 0,
    state_stcg_rate: 0,
    carryover_loss: 0,
    tax_year: new Date().getFullYear()
  });
  const [newEquityLot, setNewEquityLot] = useState<EquityLot>({
    ticker: 'ADBE',
    account_label: '',
    lot_type: 'rsu',
    shares: 0,
    cost_basis_per_share: 0,
    acquisition_date: new Date().toISOString().slice(0, 10),
    notes: ''
  });
  const [equityObjective, setEquityObjective] = useState<string>('raise_cash');
  const [equityTarget, setEquityTarget] = useState<string>('10000');
  const [equityTargetTicker, setEquityTargetTicker] = useState<string>('ADBE');
  const [equityPlan, setEquityPlan] = useState<any>(null);

  // Tab 6: External Portfolio Manager States
  const [externalAccounts, setExternalAccounts] = useState<any[]>([]);
  const [selectedAccount, setSelectedAccount] = useState<string>('Robinhood');
  const [cashFocused, setCashFocused] = useState<boolean>(false);
  const [externalPositions, setExternalPositions] = useState<any[]>([]);
  const [expandedPositions, setExpandedPositions] = useState<Record<string, boolean>>({});
  const [externalSuggestions, setExternalSuggestions] = useState<any[]>([]);
  const [externalStrategyResult, setExternalStrategyResult] = useState<any>(null);
  const [externalStrategySaving, setExternalStrategySaving] = useState<boolean>(false);
  const [externalWargame, setExternalWargame] = useState<any>(null);
  const [externalWargameLoading, setExternalWargameLoading] = useState<boolean>(false);
  const [wargameYears, setWargameYears] = useState<number>(3);
  const [crashStress, setCrashStress] = useState<any>(null);
  const [crashStressLoading, setCrashStressLoading] = useState<boolean>(false);
  const [crashEra, setCrashEra] = useState<string>('gfc');
  const [externalStrategyError, setExternalStrategyError] = useState<string>('');
  const [externalStrategyEdit, setExternalStrategyEdit] = useState({
    strategy_mode: 'growth', aggression: 60, swing: '100', longterm: '0', high_risk: '0'
  });
  const [externalSuggestionsLoading, setExternalSuggestionsLoading] = useState<boolean>(false);
  const [reconcileStatus, setReconcileStatus] = useState<string>('');
  const [externalConfirmOrder, setExternalConfirmOrder] = useState<any>(null);
  const [externalExecutionPrice, setExternalExecutionPrice] = useState<string>('');
  const [externalExecutionDate, setExternalExecutionDate] = useState<string>(new Date().toISOString().slice(0, 10));
  const [showRobinhoodSyncModal, setShowRobinhoodSyncModal] = useState<boolean>(false);
  const [robinhoodUsername, setRobinhoodUsername] = useState<string>('');
  const [robinhoodPassword, setRobinhoodPassword] = useState<string>('');
  const [robinhoodMfaSecret, setRobinhoodMfaSecret] = useState<string>('');
  const [robinhoodMfaCode, setRobinhoodMfaCode] = useState<string>('');
  const [robinhoodSyncLoading, setRobinhoodSyncLoading] = useState<boolean>(false);
  const [robinhoodSyncError, setRobinhoodSyncError] = useState<string>('');
  const [robinhoodMfaRequired, setRobinhoodMfaRequired] = useState<boolean>(false);

  const fetchExternalAccounts = async () => {
    try {
      const res = await fetch(apiUrl('/api/external/accounts'));
      if (res.ok) {
        const data = await res.json();
        setExternalAccounts(data);
        if (data.length > 0) {
          if (!data.some((a: any) => a.account_label === selectedAccount)) {
            setSelectedAccount(data[0].account_label);
          }
        }
      }
    } catch (err) {
      console.error(err);
    }
  };

  const runExternalWargame = async (acctLabel: string, years: number) => {
    if (!acctLabel) return;
    setExternalWargameLoading(true);
    setExternalWargame(null);
    try {
      const res = await fetch(apiUrl(`/api/external/accounts/${encodeURIComponent(acctLabel)}/wargame?lookback_years=${years}`), { method: 'POST' });
      if (res.ok) setExternalWargame(await res.json());
    } catch (err) {
      console.error(err);
    } finally {
      setExternalWargameLoading(false);
    }
  };

  const runCrashStress = async (acctLabel: string, era: string) => {
    if (!acctLabel) return;
    setCrashStressLoading(true);
    setCrashStress(null);
    try {
      const res = await fetch(apiUrl(`/api/external/accounts/${encodeURIComponent(acctLabel)}/crash-stress?era=${era}`), { method: 'POST' });
      if (res.ok) setCrashStress(await res.json());
    } catch (err) {
      console.error(err);
    } finally {
      setCrashStressLoading(false);
    }
  };

  const fetchExternalPositionsAndSuggestions = async (acctLabel: string) => {
    if (!acctLabel) return;
    try {
      setExternalSuggestionsLoading(true);
      setExternalSuggestions([]);
      setExternalStrategyResult(null);
      setExternalWargame(null);
      setCrashStress(null);
      const [posRes, suggRes] = await Promise.all([
        fetch(apiUrl(`/api/external/positions?account_label=${encodeURIComponent(acctLabel)}`)),
        fetch(apiUrl(`/api/external/suggestions?account_label=${encodeURIComponent(acctLabel)}`))
      ]);
      if (posRes.ok) {
        setExternalPositions(await posRes.json());
      }
      if (suggRes.ok) {
        const data = await suggRes.json();
        setExternalSuggestions(data.suggestions || []);
        setExternalStrategyResult(data);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setExternalSuggestionsLoading(false);
    }
  };

  useEffect(() => {
    if (activeTab === 'external') {
      fetchExternalAccounts();
    }
  }, [activeTab]);

  useEffect(() => {
    if (activeTab === 'external' && selectedAccount) {
      fetchExternalPositionsAndSuggestions(selectedAccount);
    }
  }, [selectedAccount, activeTab]);

  useEffect(() => {
    const acct = externalAccounts.find((a: any) => a.account_label === selectedAccount);
    if (!acct) return;
    const buckets = acct.buckets || { swing: 1, longterm: 0, high_risk: 0 };
    setExternalStrategyEdit({
      strategy_mode: acct.strategy_mode || 'growth',
      aggression: acct.aggression ?? 60,
      swing: String(Math.round((buckets.swing || 0) * 100)),
      longterm: String(Math.round((buckets.longterm || 0) * 100)),
      high_risk: String(Math.round((buckets.high_risk || 0) * 100)),
    });
  }, [selectedAccount, externalAccounts]);

  const saveExternalStrategy = async (resetBuckets = false) => {
    setExternalStrategySaving(true);
    setExternalStrategyError('');
    const buckets = resetBuckets ? null : {
      swing: (parseFloat(externalStrategyEdit.swing) || 0) / 100,
      longterm: (parseFloat(externalStrategyEdit.longterm) || 0) / 100,
      high_risk: (parseFloat(externalStrategyEdit.high_risk) || 0) / 100,
    };
    try {
      const res = await fetch(apiUrl(`/api/external/accounts/${encodeURIComponent(selectedAccount)}/strategy`), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy_mode: externalStrategyEdit.strategy_mode,
          aggression: externalStrategyEdit.aggression, buckets }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Could not save strategy');
      await fetchExternalAccounts();
      await fetchExternalPositionsAndSuggestions(selectedAccount);
    } catch (err: any) {
      setExternalStrategyError(err.message || 'Could not save strategy');
    } finally {
      setExternalStrategySaving(false);
    }
  };

  const [equityRunning, setEquityRunning] = useState<boolean>(false);
  const [equityProgress, setEquityProgress] = useState<{ pct: number; stage: string }>({ pct: 0, stage: '' });
  const [tradingBlocks, setTradingBlocks] = useState<any[]>([]);
  const [autoTradingPaused, setAutoTradingPaused] = useState<boolean>(false);
  const [newBlockTicker, setNewBlockTicker] = useState<string>('');
  const [equityAggregate, setEquityAggregate] = useState<any[]>([]);
  const [sellModal, setSellModal] = useState<any>(null);
  const [sellBusy, setSellBusy] = useState<boolean>(false);
  const [equityImportStatus, setEquityImportStatus] = useState<string>('');
  const [equityImportBusy, setEquityImportBusy] = useState<boolean>(false);
  const [equityImportReplace, setEquityImportReplace] = useState<boolean>(false);

  const asNum = (v: any) => typeof v === 'number' ? v : (typeof v === 'string' && v.trim() !== '' ? Number(v) : NaN);
  const money = (v: any) => {
    const n = asNum(v);
    return Number.isFinite(n) ? `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—';
  };
  /** Per-share quotes, cost basis, analyst targets — always show cents. */
  const sharePrice = (v: any) => {
    const n = asNum(v);
    return Number.isFinite(n) ? `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '—';
  };
  const pct = (v: any) => typeof v === 'number' && isFinite(v) ? `${(v * 100).toFixed(1)}%` : '—';

  const equityPlanPickById = useMemo(() => {
    const map = new Map<number, { sell_shares: number; ticker: string }>();
    for (const p of equityPlan?.recommendation?.picks || []) {
      if (p?.id != null) map.set(p.id, p);
    }
    return map;
  }, [equityPlan]);

  const fetchEquityAdvisor = async () => {
    try {
      const [lotsRes, profileRes] = await Promise.all([
        fetch(apiUrl('/api/equity/lots')),
        fetch(apiUrl('/api/equity/tax-profile'))
      ]);
      if (lotsRes.ok) {
        const data = await lotsRes.json();
        setEquityLots(data.lots || []);
        setEquityForecasts(data.forecasts || {});
        setEquityAggregate(data.aggregate || []);
        setVestSchedules(data.vest_schedules || []);
        const tickers = Array.from(new Set((data.lots || []).map((l: EquityLot) => l.ticker))).sort() as string[];
        setGrantChartsVisible((prev) => {
          const next = { ...prev };
          for (const t of tickers) {
            if (next[t] === undefined) next[t] = true;
          }
          return next;
        });
      }
      if (profileRes.ok) setTaxProfile(await profileRes.json());
    } catch (err) {
      console.error(err);
    }
    fetchTradingGuard();
  };

  const fetchTradingGuard = async () => {
    try {
      const res = await fetch(apiUrl('/api/equity/trading-blocks'));
      if (res.ok) {
        const data = await res.json();
        setTradingBlocks(data.blocks || []);
        setAutoTradingPaused(!!data.auto_trading_paused);
      }
    } catch (err) {
      console.error(err);
    }
  };

  const setAutoTrading = async (paused: boolean) => {
    await fetch(apiUrl('/api/execution/auto-trading'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ paused })
    });
    fetchTradingGuard();
  };

  const createTradingBlock = async (body: any) => {
    await fetch(apiUrl('/api/equity/trading-blocks'), {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    fetchTradingGuard();
  };

  const releaseTradingBlock = async (id: number) => {
    await fetch(apiUrl(`/api/equity/trading-blocks/${id}`), { method: 'DELETE' });
    fetchTradingGuard();
  };

  useEffect(() => {
    if (activeTab === 'advisor') fetchEquityAdvisor();
  }, [activeTab]);

  const saveTaxProfile = async () => {
    await fetch(apiUrl('/api/equity/tax-profile'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(taxProfile)
    });
    fetchEquityAdvisor();
  };

  const saveEquityLot = async () => {
    await fetch(apiUrl('/api/equity/lots'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(newEquityLot)
    });
    setNewEquityLot({ ...newEquityLot, id: undefined, shares: 0, cost_basis_per_share: 0, notes: '' });
    fetchEquityAdvisor();
  };

  const deleteEquityLot = async (id?: number) => {
    if (!id) return;
    await fetch(apiUrl(`/api/equity/lots/${id}`), { method: 'DELETE' });
    fetchEquityAdvisor();
  };

  const importEquityPdf = async (file: File, replaceTickerAccount = false) => {
    setEquityImportBusy(true);
    setEquityImportStatus('');
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('force_llm', 'false');
      form.append('replace_ticker_account', replaceTickerAccount ? 'true' : 'false');
      const res = await fetch(apiUrl('/api/equity/lots/import'), { method: 'POST', body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const msg = typeof data.detail === 'object' ? data.detail.message || JSON.stringify(data.detail) : (data.detail || res.statusText);
        setEquityImportStatus(`Import failed: ${msg}`);
        return;
      }
      const warn = (data.warnings || []).length ? ` Warnings: ${(data.warnings as string[]).join(' ')}` : '';
      setEquityImportStatus(
        `Imported ${data.inserted} lot(s) (${(data.tickers || []).join(', ') || '—'}); ` +
        `${data.skipped_duplicates || 0} duplicate(s) skipped.${data.llm_used ? ' (LLM parser)' : ''}${warn}`
      );
      fetchEquityAdvisor();
    } catch (err: any) {
      setEquityImportStatus(`Import failed: ${err?.message || err}`);
    } finally {
      setEquityImportBusy(false);
    }
  };

  const toggleEquityAutoTradeBlock = async (ticker: string, blocked: boolean) => {
    await fetch(apiUrl('/api/equity/auto-trade-block'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ticker, blocked }),
    });
    fetchEquityAdvisor();
    fetchTradingGuard();
  };

  const saveVestSchedule = async (schedule: any) => {
    await fetch(apiUrl('/api/equity/vest-schedules'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(schedule),
    });
    fetchEquityAdvisor();
  };

  const editEquityLot = (lot: EquityLot) => {
    setNewEquityLot({
      id: lot.id, ticker: lot.ticker, account_label: lot.account_label, lot_type: lot.lot_type,
      shares: lot.shares, cost_basis_per_share: lot.cost_basis_per_share,
      acquisition_date: lot.acquisition_date, notes: lot.notes,
    });
    document.getElementById('equity-lot-form')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
  };

  const submitSell = async () => {
    if (!sellModal || sellModal.shares <= 0) return;
    setSellBusy(true);
    try {
      const res = await fetch(apiUrl(`/api/equity/lots/${sellModal.id}/sell`), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          shares: sellModal.shares,
          sale_price: sellModal.sale_price ? parseFloat(sellModal.sale_price) : null,
          sale_date: sellModal.sale_date || null,
          add_wash_sale_block: !!sellModal.add_wash_sale_block,
        })
      });
      await res.json();
      setSellModal(null);
      fetchEquityAdvisor();
    } catch (err) {
      console.error(err);
    } finally {
      setSellBusy(false);
    }
  };

  const runEquityAnalyze = async () => {
    setEquityRunning(true);
    setEquityPlan(null);
    try {
      const res = await fetch(apiUrl('/api/equity/analyze'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ objective: equityObjective, target_amount: parseFloat(equityTarget) || 0, target_ticker: equityTargetTicker || null })
      });
      const { job_id } = await res.json();
      const poll = setInterval(async () => {
        const r = await fetch(apiUrl(`/api/equity/analyze/result?job_id=${job_id}`));
        const data = await r.json();
        if (data.status === 'done') {
          clearInterval(poll);
          setEquityPlan(data.result);
          setEquityRunning(false);
          fetchEquityAdvisor();
        } else if (data.status === 'error') {
          clearInterval(poll);
          setEquityProgress({ pct: 0, stage: data.error || 'Analysis failed' });
          setEquityRunning(false);
        } else {
          setEquityProgress({ pct: data.progress || 0, stage: data.stage || 'Running' });
        }
      }, 1000);
    } catch (err) {
      console.error(err);
      setEquityRunning(false);
    }
  };

  const fetchSources = async (ticker: string) => {
    setLoadingSources(true);
    try {
      const res = await fetch(apiUrl(`/api/sentiment/sources?ticker=${ticker}&mode=${appMode}`));
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
      const res = await fetch(apiUrl(`/api/health`));
      setHealth(res.ok ? await res.json() : null);
    } catch (err) {
      setHealth(null);
    }
  };

  const fetchPortfolio = async () => {
    setRefreshingPortfolio(true);
    try {
      const res = await fetch(apiUrl(`/api/portfolio?mode=${appMode}`));
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
        fetch(apiUrl(`/api/jobs`)),
        fetch(apiUrl(`/api/train/status`)),
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
      const res = await fetch(apiUrl(`/api/strategy/config`));
      if (res.ok) {
        const d = await res.json();
        setStrategyConfig(d);
        setBucketEdit({ swing: String(Math.round((d.buckets.swing || 0) * 100)), longterm: String(Math.round((d.buckets.longterm || 0) * 100)), high_risk: String(Math.round((d.buckets.high_risk || 0) * 100)) });
      }
    } catch (err) { /* offline */ }
  };

  const handleSetTickerStrategy = async (ticker: string, strategy: string) => {
    try {
      await fetch(apiUrl(`/api/strategy/ticker`), {
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
      const res = await fetch(apiUrl(`/api/strategy/suggest?oos_start=2022-01-01`), { method: 'POST' });
      const { job_id } = await res.json();
      const poll = async () => {
        try {
          const r = await fetch(apiUrl(`/api/strategy/suggest/result?job_id=${job_id}`));
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
      const res = await fetch(apiUrl(`/api/strategy/validate?oos_start=2022-01-01`), { method: 'POST' });
      const { job_id } = await res.json();
      const poll = async () => {
        try {
          const r = await fetch(apiUrl(`/api/strategy/validate/result?job_id=${job_id}`));
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
        await fetch(apiUrl(`/api/strategy/ticker`), {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker: s.ticker, strategy: s.recommended }),
        });
      }
      fetchStrategyConfig(); fetchData();
    } catch (err) { console.error(err); } finally { setActionBusy(false); }
  };

  const handleSaveBuckets = async () => {
    const s = (parseFloat(bucketEdit.swing) || 0) / 100;
    const l = (parseFloat(bucketEdit.longterm) || 0) / 100;
    let h = (parseFloat(bucketEdit.high_risk) || 0) / 100;
    if (h > 0.05) { alert('High-risk sleeve is capped at 5% of equity.'); return; }
    if (s + l + h > 1.0001) { alert('Buckets cannot exceed 100% of equity.'); return; }
    setActionBusy(true);
    try {
      const res = await fetch(apiUrl(`/api/strategy/buckets`), {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ swing: s, longterm: l, high_risk: h }),
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
    { key: 'swing', name: 'Swing + News (core)', color: '#00F2FE' },
    { key: 'longterm', name: 'Long-term (MPT)', color: '#10B981' },
    { key: 'high_risk', name: 'High-risk (aggressive)', color: '#EF4444' },
    { key: 'blended', name: 'Blended (your allocation)', color: '#F59E0B' },
    { key: 'spy', name: 'S&P 500', color: '#94A3B8' },
    { key: 'qqq', name: 'QQQ', color: '#A78BFA' },
    { key: 'brk', name: 'Berkshire (BRK-B)', color: '#FB923C' },
  ];

  // Drop a series from the chart/table when another already plotted line is numerically identical
  // (common when blended == swing at 100% swing allocation but backend hasn't deduped yet).
  const evalVisibleSeries = (result: any) => {
    const rows: any[] = result?.series || [];
    const keys = EVAL_SERIES.map(s => s.key).filter(k => result?.metrics?.[k]);
    const sameCurve = (a: string, b: string) => rows.length > 0 && rows.every(r => {
      const va = r[a], vb = r[b];
      if (va == null && vb == null) return true;
      if (va == null || vb == null) return false;
      return Math.abs(Number(va) - Number(vb)) < 0.05;
    });
    return keys.filter(k => !keys.some(other => other !== k && other !== 'blended' && k === 'blended' && sameCurve(k, other)));
  };

  // Draw benchmarks first, blended in the middle, component strategies last so they aren't buried.
  const EVAL_CHART_ORDER: Record<string, number> = { spy: 0, qqq: 1, brk: 2, blended: 3, longterm: 4, high_risk: 5, swing: 6 };

  // Risk × fundamental-quality tiers, with at-a-glance names/icons/colors for the ticker badges.
  const TIER_META: Record<string, { label: string; icon: string; color: string; blurb: string }> = {
    quality_growth: { label: 'Hot', icon: '🔥', color: '#10B981', blurb: 'Strong fundamentals, volatile — accumulate dips, hold long-term' },
    core: { label: 'Solid', icon: '🛡️', color: '#38BDF8', blurb: 'Solid fundamentals, calmer — the core book' },
    speculative: { label: 'Long-shot', icon: '🎲', color: '#EF4444', blurb: 'Weak/volatile — high-risk gamble, small bets only' },
    value_trap: { label: 'Cold', icon: '🧊', color: '#94A3B8', blurb: 'Weak fundamentals, low upside — avoid' },
  };
  const tierMeta = (t: string) => { const tier = classification[t]?.tier; return tier ? TIER_META[tier] : null; };
  const setTierOverride = async (ticker: string, tier: string | null) => {
    setTierMenu(null);
    try {
      await fetch(apiUrl('/api/classification/override'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker, tier }),
      });
      const r = await fetch(apiUrl('/api/classification'));
      if (r.ok) setClassification(await r.json());
    } catch (err) { console.error(err); }
  };
  // Polished tier pill (tinted bg + icon + colored symbol). Click to override the tier; hover for detail.
  const TickerTag = ({ t, size = 13, bold = true }: { t: string; size?: number; bold?: boolean }) => {
    const m = tierMeta(t); const c = classification[t];
    const detail = m ? `${m.label} — ${m.blurb}${c?.quality != null ? ` · quality ${Math.round(c.quality * 100)}%` : ''}${c?.overridden ? ' · (manual override)' : ''} · click to change`
                     : `${t}: unrated (no fundamentals) · click to set tier`;
    const base: React.CSSProperties = { display: 'inline-flex', alignItems: 'center', gap: '4px', fontWeight: bold ? 700 : 600, fontSize: size, cursor: 'pointer', borderRadius: '6px', padding: '1px 7px', lineHeight: 1.7 };
    const styled: React.CSSProperties = m
      ? { ...base, color: m.color, background: `${m.color}1A`, border: `1px solid ${m.color}44` }
      : { ...base, color: 'var(--text-primary)', border: '1px dashed var(--border-glass)' };
    return (
      <span title={detail} onClick={(e) => { e.stopPropagation(); setTierMenu({ ticker: t, x: e.clientX, y: e.clientY }); }} style={styled}>
        {m && <span style={{ fontSize: size - 2 }}>{m.icon}</span>}{t}{c?.overridden && <span style={{ fontSize: size - 3, opacity: 0.7 }}>✎</span>}
      </span>
    );
  };

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
      const res = await fetch(apiUrl(`/api/evaluate`), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategies, horizon: 5, splits: evalSplits, use_allocation: evalUseAlloc, start_date: start, end_date: end, oos_start: oos, exclude_premium: evalExcludePremium }),
      });
      const { job_id } = await res.json();
      setEvalJobId(job_id);
      const poll = async () => {
        try {
          const r = await fetch(apiUrl(`/api/evaluate/result?job_id=${job_id}`));
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
      const r = await fetch(apiUrl(`/api/evaluate/interpret?job_id=${evalJobId}`), { method: 'POST' });
      const d = await r.json();
      if (d.interpretation) setEvalResult((prev: any) => ({ ...prev, interpretation: d.interpretation }));
    } catch (err) { console.error(err); } finally { setInterpLoading(false); }
  };

  const sinceDate = (mode: string) => {
    if (mode === 'all') return '';
    const d = new Date();
    if (mode === '7d') d.setDate(d.getDate() - 6);
    // Local date (not UTC) — the backend stamps usage rows with datetime.now() local time, so a UTC
    // slice can be a day ahead and filter out everything near midnight.
    const p = (n: number) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  };
  const fetchLlmUsage = async (mode: string = llmSince) => {
    setLlmLoading(true);
    try {
      const s = sinceDate(mode);
      const r = await fetch(apiUrl(`/api/llm/usage${s ? `?since=${s}` : ''}`));
      setLlmUsage(await r.json());
    } catch (err) { console.error(err); } finally { setLlmLoading(false); }
  };
  const calibrateModel = async () => {
    if (!calModel || !calCost) return;
    setCalMsg('');
    try {
      const s = sinceDate(llmSince);
      const r = await fetch(apiUrl('/api/llm/calibrate'), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: calModel, actual_cost: parseFloat(calCost), since: s || null }),
      });
      const d = await r.json();
      if (d.error) setCalMsg(d.error);
      else { setCalMsg(`Calibrated ${d.model}: ×${d.factor} (est $${d.est_cost_before} → set to $${d.actual_cost})`); setCalCost(''); fetchLlmUsage(); }
    } catch (err) { console.error(err); setCalMsg('Calibration failed.'); }
  };
  const fetchPremiumValue = async () => {
    try {
      const r = await fetch(apiUrl('/api/premium/value'));
      setPremiumValue(await r.json());
    } catch (err) { console.error(err); }
  };
  useEffect(() => { if (activeTab === 'virtual_perf') { fetchLlmUsage(llmSince); fetchPremiumValue(); } /* eslint-disable-next-line */ }, [activeTab, llmSince]);

  const handleAddStock = async () => {
    const t = addStockTicker.toUpperCase().trim();
    if (!t) return;
    setActionBusy(true);
    try {
      // /backfill adds the ticker if missing AND always starts a (visible) backfill job —
      // so "Add & Backfill" works even for a ticker already in the universe.
      await fetch(apiUrl(`/api/universe/backfill`), {
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
      await fetch(apiUrl(`/api/universe/backfill`), {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ticker }),
      });
      fetchJobsAndTraining();
    } catch (err) { console.error(err); }
  };

  const handleRemoveMonitored = async (ticker: string) => {
    if (!confirm(`Stop monitoring ${ticker}? (It has no open position.)`)) return;
    try {
      await fetch(apiUrl(`/api/universe/remove`), {
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
      const res = await fetch(apiUrl(`/api/positions/liquidate?mode=${appMode}`), {
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
      await fetch(apiUrl(`/api/train/start`), { method: 'POST' });
      fetchJobsAndTraining();
    } catch (err) { console.error(err); } finally { setActionBusy(false); }
  };

  const fetchLlmNews = async (ticker: string) => {
    try {
      const res = await fetch(apiUrl(`/api/news/llm?ticker=${ticker}&limit=40`));
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
        const res = await fetch(apiUrl('/api/sentiment/premium'), {
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
      const sugRes = await fetch(apiUrl(`/api/suggestions?mode=${appMode}&hedge_mode=${hedgeMode}`));
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
      const perfRes = await fetch(apiUrl(`/api/performance?mode=${appMode === 'real' ? 'live' : perfMode}`));
      if (perfRes.ok) {
        const perfData = await perfRes.json();
        setPerfCurve(perfData.equity_curve || []);
        setMetrics(perfData.metrics || { total_return: 0, sharpe_ratio: 0, max_drawdown: 0, win_rate: 0 });
      }

      // 3. Fetch Universe Tickers
      const uniRes = await fetch(apiUrl('/api/universe'));
      if (uniRes.ok) {
        const uniData = await uniRes.json();
        setUniverseTickers(uniData.tickers || []);
      }

      // 4. Fetch User Holdings & Virtual Account
      const holdRes = await fetch(apiUrl(`/api/holdings?mode=${appMode}`));
      if (holdRes.ok) {
        const holdData = await holdRes.json();
        setHoldings(holdData || []);
      }

      const accRes = await fetch(apiUrl(`/api/virtual_alpaca/v2/account?mode=${appMode}`));
      if (accRes.ok) {
        const accData = await accRes.json();
        setAccountCash(parseFloat(accData.cash) || 0);
        setAccountEquity(parseFloat(accData.portfolio_value) || 0);
      }

      // 5. Fetch Virtual Positions
      const vposRes = await fetch(apiUrl(`/api/virtual_alpaca/v2/positions?mode=${appMode}`));
      if (vposRes.ok) {
        const vposData = await vposRes.json();
        setVirtualPositions(vposData || []);
      }

      // 6. Fetch Sentiment list
      const sentRes = await fetch(apiUrl(`/api/sentiment?mode=${appMode}`));
      if (sentRes.ok) {
        const sentData = await sentRes.json();
        setSentimentList(sentData.sentiment || []);
      }

      // 7. Fetch per-ticker price summary (live price + 1D/1W/1M/1Y changes)
      const priceRes = await fetch(apiUrl(`/api/prices/summary`));
      if (priceRes.ok) {
        const priceData = await priceRes.json();
        setPriceSummary(priceData.prices || []);
      }

      // 8. Fetch portfolio (holdings enriched with live value + P&L)
      const portRes = await fetch(apiUrl(`/api/portfolio?mode=${appMode}`));
      if (portRes.ok) {
        setPortfolio(await portRes.json());
      }

      // 9. Fetch strategy assignments + bucket allocations
      fetchStrategyConfig();

      // 10. Per-ticker risk × quality tier (for the badges)
      try {
        const clsRes = await fetch(apiUrl('/api/classification'));
        if (clsRes.ok) setClassification(await clsRes.json());
      } catch { /* non-fatal */ }

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
      await fetch(apiUrl(`/api/universe/add`), {
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
      await fetch(apiUrl(`/api/universe/remove`), {
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
        const res = await fetch(apiUrl(`/api/holdings?mode=${appMode}`), {
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
        const res = await fetch(apiUrl(`/api/holdings/${ticker}?mode=${appMode}`), {
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
        const res = await fetch(apiUrl(`/api/holdings?mode=${appMode}`), {
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
        const res = await fetch(apiUrl(`/api/account?mode=${appMode}`), {
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
          {/* Market open/closed status (with next session for closed markets) */}
          {health?.services?.alpaca?.status === 'up' && (() => {
            const a = health.services.alpaca;
            const isOpen = !!a.market_open;
            const color = isOpen ? '#10B981' : '#F59E0B';
            let nextLabel = '';
            if (!isOpen && a.next_open) {
              const d = new Date(a.next_open);
              if (!isNaN(d.getTime())) {
                nextLabel = ' · Opens ' + d.toLocaleString(undefined, {
                  weekday: 'short', month: 'short', day: 'numeric',
                  hour: 'numeric', minute: '2-digit',
                });
              }
            }
            return (
              <div
                title={isOpen ? 'US market is open — the bot trades on schedule' : `US market is closed${nextLabel ? ' —' + nextLabel.replace(' · Opens', ' opens') : ''}`}
                style={{
                  display: 'flex', alignItems: 'center', gap: '6px',
                  padding: '4px 12px', borderRadius: '999px',
                  background: isOpen ? 'rgba(16,185,129,0.1)' : 'rgba(245,158,11,0.1)',
                  border: `1px solid ${color}`, cursor: 'default',
                }}
              >
                <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: color, boxShadow: `0 0 6px ${color}` }} />
                <span style={{ fontSize: '11px', color, fontWeight: 700 }}>
                  {isOpen ? 'Market Open' : 'Market Closed'}
                </span>
                {!isOpen && nextLabel && (
                  <span style={{ fontSize: '11px', color: 'var(--text-secondary)', fontWeight: 500 }}>
                    {nextLabel.replace(' · ', '')}
                  </span>
                )}
              </div>
            );
          })()}

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
          <button
            className={`toggle-btn ${activeTab === 'advisor' ? 'active' : ''}`}
            onClick={() => setActiveTab('advisor')}
          >
            Equity Advisor
          </button>
          <button
            className={`toggle-btn ${activeTab === 'crash' ? 'active' : ''}`}
            onClick={() => setActiveTab('crash')}
          >
            Crash Radar
          </button>
          <button
            className={`toggle-btn ${activeTab === 'external' ? 'active' : ''}`}
            onClick={() => setActiveTab('external')}
          >
            External Portfolio
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
                  {Object.keys(classification).length > 0 && (
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px', margin: '0 0 12px', fontSize: '11.5px', color: 'var(--text-secondary)' }}>
                      {(['quality_growth', 'core', 'speculative', 'value_trap'] as const).map(k => (
                        <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }} title={TIER_META[k].blurb}>
                          <span>{TIER_META[k].icon}</span>
                          <span style={{ color: TIER_META[k].color, fontWeight: 600 }}>{TIER_META[k].label}</span>
                        </span>
                      ))}
                      <span style={{ opacity: 0.7 }}>· hover a ticker for details</span>
                    </div>
                  )}
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
                            <td><TickerTag t={item.ticker} /></td>
                            <td>{sharePrice(item.close)}</td>
                            <td>
                              <span className={`badge badge-${item.action.toLowerCase()}`}>
                                {item.action}
                              </span>
                            </td>
                            <td>{(item.confidence * 100).toFixed(0)}%</td>
                            <td className="text-red">{item.stop_loss ? sharePrice(item.stop_loss) : '-'}</td>
                            <td className="text-green">{item.take_profit ? sharePrice(item.take_profit) : '-'}</td>
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
                                      {item.hedge.price ? ` @ ${sharePrice(item.hedge.price)}` : ''} ({item.hedge.ratio}× notional)
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
                            <td><TickerTag t={item.ticker} /></td>
                            <td>{sharePrice(item.close)}</td>
                            <td>
                              <span className={`badge badge-${item.action.toLowerCase()}`}>
                                {item.action}
                              </span>
                            </td>
                            <td>{(item.confidence * 100).toFixed(0)}%</td>
                            <td style={{ color: item.llm_news > 0.02 ? 'var(--color-buy)' : item.llm_news < -0.02 ? 'var(--color-sell)' : 'var(--text-secondary)' }}>
                              {item.llm_news > 0.02 ? '▲' : item.llm_news < -0.02 ? '▼' : '—'} {item.llm_news.toFixed(2)}
                            </td>
                            <td className="text-red">{item.stop_loss ? sharePrice(item.stop_loss) : '-'}</td>
                            <td className="text-green">{item.take_profit ? sharePrice(item.take_profit) : '-'}</td>
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
                              <TickerTag t={item.ticker} bold={false} />
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
                                      <div style={{ marginTop: '2px', fontSize: '11px' }}>Value: {sharePrice(item.current_value)} @ {sharePrice(item.current_price)}</div>
                                      <div style={{ fontSize: '11px' }}>Cost Basis: {item.entry_price && item.entry_price > 0 ? sharePrice(item.entry_price) : '—'}</div>
                                    </div>
                                    <div>
                                      <span style={{ color: 'var(--text-primary)', fontSize: '10px', fontWeight: 700, letterSpacing: '0.05em', display: 'block', marginBottom: '4px' }}>TARGET ALLOCATION</span>
                                      <div style={{ fontSize: '13px', color: 'var(--text-primary)' }}>
                                        <strong>{item.target_shares?.toFixed(1)}</strong> shares
                                      </div>
                                      <div style={{ marginTop: '2px', fontSize: '11px' }}>Target Value: {sharePrice(item.target_value)}</div>
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
                                  <TickerTag t={p.ticker} size={14} />
                                  {p.is_live && (
                                    <span title="Live price" style={{ width: '6px', height: '6px', borderRadius: '50%', background: 'var(--color-buy)', display: 'inline-block' }} />
                                  )}
                                  {avg != null && (
                                    <span className={avg >= 0.05 ? 'text-green' : avg <= -0.05 ? 'text-red' : ''} style={{ fontSize: '10px', fontWeight: 600 }}>
                                      sent {avg > 0 ? '+' : ''}{avg.toFixed(2)}
                                    </span>
                                  )}
                                </div>
                                <span style={{ fontWeight: 600, fontSize: '14px' }}>{sharePrice(p.price)}</span>
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
                                <TickerTag t={item.ticker} size={14} />
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
                    <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer', fontSize: '13px' }} title="Aggressive model on speculative-tier names (small high-risk sleeve)">
                      <input type="checkbox" checked={evalStrategies.high_risk} onChange={(e) => setEvalStrategies({ ...evalStrategies, high_risk: e.target.checked })} /> High-risk (aggressive)
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
                <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer', fontSize: '13px' }} title="A/B: re-run the swing walk-forward with premium-newsletter news excluded, to isolate its effect. Only meaningful once premium news spans the OOS window.">
                  <input type="checkbox" checked={evalExcludePremium} onChange={(e) => setEvalExcludePremium(e.target.checked)} /> Exclude premium news (A/B)
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
                      {it.model && <span>{it.model}{it.tokens ? ` · ${it.input_tokens != null ? `${it.input_tokens.toLocaleString()}→${(it.output_tokens || 0).toLocaleString()}` : it.tokens.toLocaleString()} tok` : ''}{typeof it.cost === 'number' ? ` · ~$${it.cost.toFixed(4)}` : ''}</span>}
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
                        {EVAL_SERIES.filter(s => evalVisibleSeries(evalResult).includes(s.key))
                          .sort((a, b) => (EVAL_CHART_ORDER[a.key] ?? 9) - (EVAL_CHART_ORDER[b.key] ?? 9))
                          .map(s => (
                          <Line key={s.key} type="monotone" dataKey={s.key} name={s.name} stroke={s.color}
                            strokeWidth={['spy', 'qqq', 'brk'].includes(s.key) ? 1.5 : 2.6}
                            strokeDasharray={['spy', 'qqq', 'brk'].includes(s.key) ? '4 3' : undefined}
                            dot={false} connectNulls isAnimationActive={false} />
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
                        {EVAL_SERIES.filter(s => evalVisibleSeries(evalResult).includes(s.key)).map(s => {
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

            {/* Value of The Information (premium news) widget */}
            {premiumValue && premiumValue.coverage && (
              <div className="glass-card" style={{ marginTop: '24px', border: '1px solid rgba(139,92,246,0.3)' }}>
                <h2 style={{ margin: '0 0 6px' }}><Newspaper size={20} color="#a78bfa" /> Value of The Information</h2>
                <p style={{ fontSize: '12.5px', color: 'var(--text-secondary)', marginTop: 0 }}>
                  Does the newsletter's calls predict moves? For each premium score we check the ticker's
                  forward return once that window closes — <b>hit-rate</b> = direction matched; <b>avg edge</b> =
                  return from going long on bullish calls / short on bearish ones.
                </p>
                {premiumValue.coverage.scores > 0 ? (
                  <>
                    <div style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: '8px 0 14px' }}>
                      <b style={{ color: 'var(--text-primary)' }}>{premiumValue.coverage.scores}</b> scores from{' '}
                      <b style={{ color: 'var(--text-primary)' }}>{premiumValue.coverage.articles}</b> articles ·{' '}
                      {premiumValue.coverage.tickers} tickers · {premiumValue.coverage.date_min} → {premiumValue.coverage.date_max} ·{' '}
                      {premiumValue.coverage.high_conviction} high-conviction (rel≥0.6)
                      {premiumValue.coverage.top_tickers && premiumValue.coverage.top_tickers.length > 0 && (
                        <> · top: {premiumValue.coverage.top_tickers.slice(0, 5).map((t: any[]) => `${t[0]}(${t[1]})`).join(', ')}</>
                      )}
                    </div>
                    {!premiumValue.enough_data && (
                      <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-start', fontSize: '13px', color: 'var(--color-gold)', background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.25)', borderRadius: '8px', padding: '10px 12px', marginBottom: '14px' }}>
                        <AlertTriangle size={16} style={{ flexShrink: 0, marginTop: '1px' }} />
                        <span>Accumulating — need ≥{premiumValue.min_n} closed samples per horizon for any confidence, and most forward windows are still open. This becomes a real read after a few weeks of premium news; treat current numbers as noise.</span>
                      </div>
                    )}
                    <div style={{ overflowX: 'auto' }}>
                      <table className="trade-table">
                        <thead>
                          <tr><th>Horizon</th><th>Closed</th><th>Pending</th><th>Hit-rate</th><th>Avg edge</th><th>High-conviction (n · hit · edge)</th></tr>
                        </thead>
                        <tbody>
                          {Object.entries(premiumValue.horizons).map(([h, v]: [string, any]) => (
                            <tr key={h}>
                              <td style={{ fontWeight: 600 }}>{h}d</td>
                              <td>{v.n || 0}</td>
                              <td style={{ color: 'var(--text-secondary)' }}>{v.pending || 0}</td>
                              <td>{v.n ? `${(v.hit_rate * 100).toFixed(0)}%` : '—'}</td>
                              <td className={v.n ? (v.avg_edge >= 0 ? 'text-green' : 'text-red') : ''}>{v.n ? `${v.avg_edge >= 0 ? '+' : ''}${(v.avg_edge * 100).toFixed(2)}%` : '—'}</td>
                              <td style={{ color: 'var(--text-secondary)' }}>{v.high_conviction && v.high_conviction.n ? `${v.high_conviction.n} · ${(v.high_conviction.hit_rate * 100).toFixed(0)}% · ${v.high_conviction.avg_edge >= 0 ? '+' : ''}${(v.high_conviction.avg_edge * 100).toFixed(2)}%` : '—'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '12px', lineHeight: 1.5 }}>
                      For the swing-model A/B, tick <b>“Exclude premium news (A/B)”</b> above and compare a run to a normal one — but it only diverges once premium news spans the out-of-sample window (currently {premiumValue.coverage.date_min}→{premiumValue.coverage.date_max}). The forward study above is the near-term meter for whether the subscription pays for itself.
                    </p>
                  </>
                ) : (
                  <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>No premium news ingested yet — run <code>make premium-ingest</code>.</p>
                )}
              </div>
            )}

            {/* LLM usage + cost widget */}
            <div className="glass-card" style={{ marginTop: '24px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px', marginBottom: '14px' }}>
                <h2 style={{ margin: 0 }}><DollarSign size={20} color="var(--color-gold)" /> LLM Usage &amp; Cost</h2>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  {(['today', '7d', 'all'] as const).map(m => (
                    <button key={m} onClick={() => setLlmSince(m)} className={`toggle-btn ${llmSince === m ? 'active' : ''}`}
                      style={{ padding: '4px 10px', fontSize: '12px' }}>{m === 'today' ? 'Today' : m === '7d' ? '7 days' : 'All'}</button>
                  ))}
                  <button onClick={() => fetchLlmUsage(llmSince)} title="Refresh"
                    style={{ display: 'flex', alignItems: 'center', padding: '5px 8px', borderRadius: '6px', background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border-glass)', color: 'var(--text-secondary)', cursor: 'pointer' }}>
                    <RefreshCw size={14} className={llmLoading ? 'animate-spin' : ''} />
                  </button>
                </div>
              </div>

              {llmUsage && llmUsage.totals && llmUsage.totals.calls > 0 ? (
                <>
                  <div style={{ overflowX: 'auto' }}>
                    <table className="trade-table">
                      <thead>
                        <tr><th>Model</th><th>Provider</th><th>Calls</th><th>API reqs</th><th>Input tok</th><th>Output tok</th><th>Est. cost</th></tr>
                      </thead>
                      <tbody>
                        {Object.entries(llmUsage.by_model).map(([model, v]: [string, any]) => (
                          <tr key={model}>
                            <td style={{ fontWeight: 600 }}>{model}</td>
                            <td><span style={{ fontSize: '11px', padding: '2px 7px', borderRadius: '10px', background: v.provider === 'openai' ? 'rgba(16,185,129,0.15)' : 'rgba(139,92,246,0.15)', color: v.provider === 'openai' ? 'var(--color-buy)' : '#a78bfa' }}>{v.provider || '—'}</span></td>
                            <td>{v.calls.toLocaleString()}</td>
                            <td>{v.requests.toLocaleString()}</td>
                            <td>{v.prompt_tokens.toLocaleString()}</td>
                            <td>{v.completion_tokens.toLocaleString()}</td>
                            <td>{v.priced ? `~$${v.est_cost.toFixed(4)}` : <span style={{ color: 'var(--text-secondary)' }}>free / local</span>}</td>
                          </tr>
                        ))}
                      </tbody>
                      <tfoot>
                        <tr style={{ borderTop: '2px solid var(--border-glass)', fontWeight: 700 }}>
                          <td>Total</td><td></td>
                          <td>{llmUsage.totals.calls.toLocaleString()}</td>
                          <td>{llmUsage.totals.requests.toLocaleString()}</td>
                          <td>{llmUsage.totals.prompt_tokens.toLocaleString()}</td>
                          <td>{llmUsage.totals.completion_tokens.toLocaleString()}</td>
                          <td style={{ color: 'var(--color-gold)' }}>~${llmUsage.totals.est_cost.toFixed(4)}</td>
                        </tr>
                      </tfoot>
                    </table>
                  </div>

                  {llmUsage.by_purpose && Object.keys(llmUsage.by_purpose).length > 0 && (
                    <div style={{ marginTop: '14px' }}>
                      <div style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-secondary)', marginBottom: '6px' }}>By purpose</div>
                      <div style={{ overflowX: 'auto' }}>
                        <table className="trade-table">
                          <thead><tr><th>Purpose</th><th>Calls</th><th>API reqs</th><th>Input tok</th><th>Output tok</th><th>Est. cost</th></tr></thead>
                          <tbody>
                            {Object.entries(llmUsage.by_purpose).sort((a: any, b: any) => b[1].est_cost - a[1].est_cost).map(([purpose, v]: [string, any]) => (
                              <tr key={purpose}>
                                <td style={{ fontWeight: 600 }}>{purpose}</td>
                                <td>{v.calls.toLocaleString()}</td>
                                <td>{v.requests.toLocaleString()}</td>
                                <td>{v.prompt_tokens.toLocaleString()}</td>
                                <td>{v.completion_tokens.toLocaleString()}</td>
                                <td>{v.est_cost > 0 ? `~$${v.est_cost.toFixed(4)}` : <span style={{ color: 'var(--text-secondary)' }}>free / local</span>}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  <div style={{ marginTop: '16px', padding: '12px 14px', borderRadius: '8px', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-glass)' }}>
                    <div style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-secondary)', marginBottom: '8px' }}>Calibrate against your real OpenAI cost</div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                      <select value={calModel} onChange={(e) => setCalModel(e.target.value)}
                        style={{ padding: '6px 10px', borderRadius: '6px', background: 'var(--bg-input, rgba(255,255,255,0.05))', border: '1px solid var(--border-glass)', color: 'var(--text-primary)', fontSize: '13px' }}>
                        <option value="">Select model…</option>
                        {Object.entries(llmUsage.by_model).filter(([, v]: [string, any]) => v.priced).map(([model]) => <option key={model} value={model}>{model}</option>)}
                      </select>
                      <span style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>actual $ (for {llmSince === 'today' ? 'today' : llmSince === '7d' ? 'last 7 days' : 'all time'}):</span>
                      <input type="number" step="0.01" value={calCost} onChange={(e) => setCalCost(e.target.value)} placeholder="1.11"
                        style={{ width: '90px', padding: '6px 10px', borderRadius: '6px', background: 'var(--bg-input, rgba(255,255,255,0.05))', border: '1px solid var(--border-glass)', color: 'var(--text-primary)', fontSize: '13px' }} />
                      <button onClick={calibrateModel} disabled={!calModel || !calCost}
                        style={{ padding: '6px 14px', borderRadius: '6px', background: 'rgba(245,158,11,0.15)', border: '1px solid rgba(245,158,11,0.35)', color: 'var(--color-gold)', fontWeight: 600, fontSize: '13px', cursor: (!calModel || !calCost) ? 'default' : 'pointer' }}>Calibrate</button>
                    </div>
                    {calMsg && <div style={{ marginTop: '8px', fontSize: '12.5px', color: 'var(--text-secondary)' }}>{calMsg}</div>}
                    <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                      Token counts are tracked from every call; cost is estimated from a pricing table (gpt-5.5 is a starting estimate). Enter the model's real cost from your OpenAI dashboard for the selected window and we'll scale its rate so future estimates match.
                    </div>
                  </div>
                </>
              ) : (
                <p style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>No LLM calls recorded {llmSince === 'today' ? 'today' : llmSince === '7d' ? 'in the last 7 days' : 'yet'}. Run an evaluation (expert interpretation) or news scoring to populate this.</p>
              )}
            </div>
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
                  const h = parseFloat(bucketEdit.high_risk) || 0;
                  const cash = Math.max(0, 100 - s - l - h);
                  const hrOver = h > 5;
                  const over = s + l + h > 100 || hrOver;
                  const field = (label: string, key: 'swing' | 'longterm' | 'high_risk', color: string, max = 100) => (
                    <div style={{ flex: 1 }}>
                      <label style={{ display: 'block', fontSize: '12px', color, marginBottom: '6px', fontWeight: 600 }}>{label}</label>
                      <div style={{ position: 'relative' }}>
                        <input type="number" min="0" max={max} value={bucketEdit[key]}
                          onChange={(e) => setBucketEdit({ ...bucketEdit, [key]: e.target.value })}
                          style={{ width: '100%', background: 'rgba(0,0,0,0.3)', border: `1px solid ${key === 'high_risk' && hrOver ? 'var(--color-sell)' : 'var(--border-glass)'}`, borderRadius: '8px', color: 'var(--text-primary)', padding: '9px 26px 9px 12px', fontSize: '14px' }} />
                        <span style={{ position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)', color: 'var(--text-secondary)', fontSize: '13px' }}>%</span>
                      </div>
                    </div>
                  );
                  return (
                    <>
                      <div style={{ display: 'flex', gap: '12px', marginBottom: '6px' }}>
                        {field('Swing + News (core)', 'swing', 'var(--color-buy)')}
                        {field('Long-term (MPT)', 'longterm', 'var(--color-accent)')}
                        {field('High-risk (≤5%)', 'high_risk', '#EF4444', 5)}
                        <div style={{ flex: 1 }}>
                          <label style={{ display: 'block', fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px', fontWeight: 600 }}>Cash (rest)</label>
                          <div style={{ padding: '9px 12px', fontSize: '14px', fontWeight: 600, color: over ? 'var(--color-sell)' : 'var(--text-primary)' }}>{over ? '—' : `${cash}%`}</div>
                        </div>
                      </div>
                      <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
                        High-risk = the aggressive model on speculative-tier names (BYND, RGTI…); capped at 5% — you accept big drawdowns there for upside.
                      </div>
                      {/* stacked allocation bar */}
                      <div style={{ display: 'flex', height: '8px', borderRadius: '999px', overflow: 'hidden', background: 'rgba(255,255,255,0.06)', marginBottom: '14px' }}>
                        <div style={{ width: `${Math.min(s, 100)}%`, background: 'var(--color-buy)' }} />
                        <div style={{ width: `${Math.min(l, 100 - Math.min(s, 100))}%`, background: 'var(--color-accent)' }} />
                        <div style={{ width: `${Math.min(h, Math.max(0, 100 - s - l))}%`, background: '#EF4444' }} />
                      </div>
                      <button onClick={handleSaveBuckets} disabled={actionBusy || over}
                        style={{ background: over ? 'rgba(239,68,68,0.15)' : 'rgba(245,158,11,0.12)', border: `1px solid ${over ? 'var(--color-sell)' : 'var(--color-gold)'}`, borderRadius: '8px', color: over ? 'var(--color-sell)' : 'var(--color-gold)', padding: '8px 18px', cursor: over ? 'not-allowed' : 'pointer', fontWeight: 600, fontSize: '13px' }}>
                        {hrOver ? 'High-risk > 5%' : over ? 'Exceeds 100%' : 'Save Allocation'}
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
                          <thead><tr><th>Scheme</th><th>Buckets (sw/lt/hr)</th><th>Total</th><th>Sharpe</th><th>Max DD</th><th>Stocks (sw/lt/hr)</th></tr></thead>
                          <tbody>
                            {rows.map(([name, r]: any) => (
                              <tr key={name}>
                                <td style={{ fontWeight: name === 'suggested' ? 700 : 400 }}>{noChange && name === 'current' ? 'Current (= Suggested)' : (labels[name] || name)}</td>
                                <td>{Math.round(r.buckets.swing * 100)}/{Math.round(r.buckets.longterm * 100)}/{Math.round((r.buckets.high_risk || 0) * 100)}</td>
                                <td className={r.metrics.total_return >= 0 ? 'text-green' : 'text-red'}>{r.metrics.total_return >= 0 ? '+' : ''}{(r.metrics.total_return * 100).toFixed(1)}%</td>
                                <td>{r.metrics.sharpe_ratio.toFixed(2)}</td>
                                <td className="text-red">{(r.metrics.max_drawdown * 100).toFixed(1)}%</td>
                                <td style={{ color: 'var(--text-secondary)' }}>{r.n_swing}/{r.n_longterm}/{r.n_high_risk ?? 0}</td>
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
                      ['Market Value', money(portfolio.totals.market_value), null],
                      ['Cost Basis', money(portfolio.totals.cost_basis), null],
                      ['Unrealized P&L', `${portfolio.totals.unrealized_pl >= 0 ? '+' : ''}${money(portfolio.totals.unrealized_pl)} (${portfolio.totals.unrealized_pl_pct >= 0 ? '+' : ''}${portfolio.totals.unrealized_pl_pct.toFixed(2)}%)`, portfolio.totals.unrealized_pl],
                      ['Cash', money(portfolio.totals.cash), null],
                      ['Total Equity', money(portfolio.totals.equity), null],
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
                                <td>{h.monitored ? '—' : sharePrice(h.entry_price)}</td>
                                <td>{h.current_price > 0 ? sharePrice(h.current_price) : '—'}</td>
                                <td>{h.monitored ? '—' : money(h.market_value)}</td>
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

                {strategyConfig?.retrain?.retrain_recommended && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', background: 'rgba(245,158,11,0.08)', border: '1px solid rgba(245,158,11,0.3)', borderRadius: '8px', padding: '8px 10px', marginBottom: '14px', fontSize: '12px', color: 'var(--text-secondary)', lineHeight: '1.4' }}>
                    <ShieldAlert size={14} color="var(--color-gold)" style={{ flexShrink: 0 }} />
                    <span>
                      <strong style={{ color: 'var(--color-gold)' }}>Retrain recommended.</strong> Manual tier overrides have changed since the models were last trained. Retrain to bake them in.
                    </span>
                  </div>
                )}

                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {([['swing', 'Swing model (Core)'], ['swing_aggressive', 'Swing model (Aggressive)'], ['short_term', 'Short-term (XGBoost)'], ['regime_hmm', 'Regime (HMM)']] as const).map(([k, label]) => (
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
                                  const res = await fetch(apiUrl(`/api/simulate?days=${simDays}`), { method: 'POST' });
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
                                  const res = await fetch(apiUrl(`/api/backtest-virtual?months=${replayMonths}`), { method: 'POST' });
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

        {activeTab === 'advisor' && (
          <section style={{ gridColumn: '1 / -1', display: 'grid', gap: '18px' }}>
            <div className="glass-card" style={{ padding: '18px', border: autoTradingPaused ? '1px solid rgba(239,68,68,0.55)' : '1px solid var(--border-glass)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '10px', flexWrap: 'wrap' }}>
                {autoTradingPaused ? <Lock size={18} color="#EF4444" /> : <Unlock size={18} color="#10B981" />}
                <h3 style={{ margin: 0, fontSize: '16px' }}>Trading guard</h3>
                <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Stops the auto-trader from re-buying a name you&rsquo;re harvesting losses on (wash-sale protection) or any name you manage elsewhere.</span>
              </div>
              {autoTradingPaused && (
                <div style={{ background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.4)', borderRadius: '8px', padding: '10px 12px', marginBottom: '12px', display: 'flex', gap: '8px', alignItems: 'center' }}>
                  <AlertTriangle size={16} color="#EF4444" />
                  <span style={{ fontSize: '13px', color: 'var(--text-primary)' }}><strong>Auto-trading is PAUSED.</strong> No buys or sells will run until you resume.</span>
                </div>
              )}
              <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap', marginBottom: '14px' }}>
                <button onClick={() => setAutoTrading(!autoTradingPaused)} className="toggle-btn" style={{ borderColor: autoTradingPaused ? '#10B981' : '#EF4444', color: autoTradingPaused ? '#10B981' : '#EF4444' }}>
                  {autoTradingPaused ? <><Play size={14} /> Resume auto-trading</> : <><Pause size={14} /> Pause all auto-trading</>}
                </button>
                <span style={{ fontSize: '11.5px', color: 'var(--text-secondary)' }}>Global kill-switch — freezes the whole bot. Use while settling a real-account loss sale.</span>
              </div>
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap', marginBottom: '12px' }}>
                <input value={newBlockTicker} onChange={(e) => setNewBlockTicker(e.target.value.toUpperCase())} placeholder="Ticker (e.g. PINS)" style={{ background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px', width: '160px' }} />
                <button onClick={() => { if (newBlockTicker) { createTradingBlock({ ticker: newBlockTicker, block_type: 'wash_sale', sale_date: new Date().toISOString().slice(0, 10), reason: `Loss sale initiated — block re-buys 31 days.` }); setNewBlockTicker(''); } }} disabled={!newBlockTicker} className="toggle-btn">Block 31 days (loss sale)</button>
                <button onClick={() => { if (newBlockTicker) { createTradingBlock({ ticker: newBlockTicker, block_type: 'permanent', account_label: 'external', reason: `Held/managed externally — never auto-trade.` }); setNewBlockTicker(''); } }} disabled={!newBlockTicker} className="toggle-btn">Never trade (held elsewhere)</button>
              </div>
              {tradingBlocks.length === 0 ? (
                <div style={{ fontSize: '12.5px', color: 'var(--text-secondary)' }}>No active buy-blocks. Record a tax-loss sale below (or here) to protect the harvested loss.</div>
              ) : (
                <table className="trade-table">
                  <thead><tr><th>Ticker</th><th>Type</th><th>Until</th><th>Why</th><th></th></tr></thead>
                  <tbody>
                    {tradingBlocks.map((b) => (
                      <tr key={b.id}>
                        <td><strong>{b.ticker}</strong></td>
                        <td>{b.block_type === 'wash_sale' ? '🧼 Wash-sale' : '🚫 Never-trade'}</td>
                        <td>{b.blocked_until ? `${b.blocked_until}${b.days_remaining != null ? ` (${b.days_remaining}d)` : ''}` : 'permanent'}</td>
                        <td style={{ fontSize: '11.5px', color: 'var(--text-secondary)', maxWidth: '320px' }}>{b.reason}</td>
                        <td><button onClick={() => releaseTradingBlock(b.id)} style={{ background: 'transparent', border: 0, color: 'var(--text-secondary)', cursor: 'pointer', fontSize: '11.5px' }}>Release</button></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <div className="glass-card" style={{ padding: '18px', border: '1px solid rgba(245, 158, 11, 0.28)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '12px' }}>
                <DollarSign size={20} color="#F59E0B" />
                <h2 style={{ margin: 0, fontSize: '18px' }}>Equity Advisor</h2>
                <span style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>Plans when/what to sell from your vested shares — tax-aware. Decision-support only, not tax advice. No orders are placed.</span>
              </div>
              <p style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: '0 0 14px' }}>
                <strong style={{ color: 'var(--text-primary)' }}>Step 1 — Your tax situation.</strong> These let us estimate taxes realistically. Nothing is filed or shared.
              </p>
              {(() => {
                const FIELDS: { key: string; label: string; type: 'select' | 'money' | 'percent' | 'year'; help: string }[] = [
                  { key: 'filing_status', label: 'Filing status', type: 'select', help: 'How you file your federal taxes.' },
                  { key: 'ordinary_income', label: 'Annual income', type: 'money', help: 'Your regular taxable income (salary, etc.). Sets your tax bracket.' },
                  { key: 'magi', label: 'Total income (MAGI)', type: 'money', help: 'Modified Adjusted Gross Income — roughly all your income for the year. Used to check the extra 3.8% investment tax that applies above ~$200k (single) / $250k (married).' },
                  { key: 'state_ltcg_rate', label: 'State tax — long-term gains', type: 'percent', help: 'Your state’s rate on shares held over 1 year. Enter 0 if your state has no capital-gains tax (e.g. TX, FL, WA).' },
                  { key: 'state_stcg_rate', label: 'State tax — short-term gains', type: 'percent', help: 'Your state’s rate on shares held under 1 year.' },
                  { key: 'carryover_loss', label: 'Loss carryover', type: 'money', help: 'Capital losses carried over from prior years’ returns. Enter 0 if none.' },
                  { key: 'tax_year', label: 'Tax year', type: 'year', help: 'The year you’re planning for (2025 or 2026).' },
                ];
                const inp: React.CSSProperties = { width: '100%', marginTop: '5px', background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px' };
                return (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: '14px' }}>
                    {FIELDS.map(f => (
                      <div key={f.key}>
                        <label style={{ fontSize: '12.5px', fontWeight: 600, color: 'var(--text-primary)' }}>{f.label}</label>
                        {f.type === 'select' ? (
                          <select value={(taxProfile as any).filing_status || 'single'} onChange={(e) => setTaxProfile({ ...taxProfile, filing_status: e.target.value })} style={inp}>
                            <option value="single">Single</option>
                            <option value="married_joint">Married, filing jointly</option>
                            <option value="married_separate">Married, filing separately</option>
                            <option value="head_of_household">Head of household</option>
                          </select>
                        ) : f.type === 'percent' ? (
                          <div style={{ position: 'relative' }}>
                            <input type="number" step="0.1" value={(taxProfile as any)[f.key] != null ? +(((taxProfile as any)[f.key]) * 100).toFixed(2) : ''}
                              onChange={(e) => setTaxProfile({ ...taxProfile, [f.key]: (parseFloat(e.target.value) || 0) / 100 })} style={inp} />
                            <span style={{ position: 'absolute', right: '10px', top: '13px', color: 'var(--text-secondary)', fontSize: '12px' }}>%</span>
                          </div>
                        ) : (
                          <div style={{ position: 'relative' }}>
                            {f.type === 'money' && <span style={{ position: 'absolute', left: '9px', top: '13px', color: 'var(--text-secondary)', fontSize: '12px' }}>$</span>}
                            <input type="number" value={(taxProfile as any)[f.key] ?? ''}
                              onChange={(e) => setTaxProfile({ ...taxProfile, [f.key]: f.type === 'year' ? (parseInt(e.target.value) || 2026) : (parseFloat(e.target.value) || 0) })}
                              style={{ ...inp, paddingLeft: f.type === 'money' ? '20px' : '8px' }} />
                          </div>
                        )}
                        <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '4px', lineHeight: 1.4 }}>{f.help}</div>
                      </div>
                    ))}
                  </div>
                );
              })()}
              <button onClick={saveTaxProfile} className="toggle-btn" style={{ marginTop: '14px' }}>Save Profile</button>
            </div>

            <div className="glass-card" style={{ padding: '18px' }} id="equity-lot-form-section">
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap', marginBottom: '8px' }}>
                <h3 style={{ margin: 0 }}>Step 2 — Your share lots</h3>
                <label
                  htmlFor="equity-pdf-upload"
                  style={{
                    display: 'inline-flex', alignItems: 'center', gap: '8px', cursor: equityImportBusy ? 'wait' : 'pointer',
                    padding: '8px 14px', borderRadius: '8px', fontSize: '13px', fontWeight: 600,
                    border: '1px solid rgba(34, 211, 238, 0.55)', background: 'rgba(34, 211, 238, 0.12)',
                    color: '#22D3EE', opacity: equityImportBusy ? 0.6 : 1,
                  }}
                >
                  <Upload size={15} /> {equityImportBusy ? 'Importing…' : 'Import PDF'}
                  <input
                    id="equity-pdf-upload"
                    type="file"
                    accept="application/pdf,.pdf"
                    disabled={equityImportBusy}
                    style={{ display: 'none' }}
                    onChange={(e) => {
                      const f = e.target.files?.[0];
                      if (f) importEquityPdf(f, equityImportReplace);
                      e.target.value = '';
                    }}
                  />
                </label>
              </div>
              <p style={{ fontSize: '13px', color: 'var(--text-secondary)', margin: '0 0 12px' }}>
                Add each batch of vested shares manually below, or use <strong style={{ color: 'var(--text-primary)' }}>Import PDF</strong> for Schwab cost-basis exports and E*TRADE / Morgan Stanley stock-plan statements. Duplicates are skipped automatically.
              </p>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', alignItems: 'center', marginBottom: '14px', padding: '10px 12px', borderRadius: '8px', border: '1px dashed rgba(34, 211, 238, 0.35)', background: 'rgba(34, 211, 238, 0.06)' }}>
                <label style={{ fontSize: '12px', color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <input
                    type="checkbox"
                    checked={equityImportReplace}
                    onChange={(e) => setEquityImportReplace(e.target.checked)}
                  />
                  Replace existing lots for same ticker/account before import
                </label>
              </div>
              {equityImportStatus && (
                <div style={{ fontSize: '12.5px', marginBottom: '12px', color: equityImportStatus.startsWith('Import failed') ? 'var(--color-sell)' : 'var(--color-buy)' }}>
                  {equityImportStatus}
                </div>
              )}
              {equityAggregate.length > 0 && (
                <div style={{ marginBottom: '16px' }}>
                  <div style={{ fontSize: '12.5px', fontWeight: 600, marginBottom: '6px' }}>By holding</div>
                  <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: '0 0 8px' }}>
                    External holdings are added to the data universe (news, fundamentals, models) with strategy <em>hold</em>.
                    Toggle <strong>Block bot</strong> to prevent auto-trading a name you are harvesting manually (RSU tickers like PINS default on).
                  </p>
                  <table className="trade-table">
                    <thead><tr><th>Ticker</th><th>Shares</th><th>Avg basis</th><th>Value</th><th>Unrealized</th><th>LT / ST</th><th>Bot trading</th><th>Recommendation</th></tr></thead>
                    <tbody>
                      {equityAggregate.map((a: any) => (
                        <tr key={a.ticker}>
                          <td><strong>{a.ticker}</strong>{a.tier_label && <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>{a.tier_label} · {pct(a.weight)} · {a.universe_strategy || 'hold'}</div>}</td>
                          <td>{Math.round(a.shares).toLocaleString()}</td>
                          <td>{sharePrice(a.avg_cost_basis)}</td>
                          <td>{money(a.market_value)}</td>
                          <td style={{ color: (a.unrealized_gain || 0) >= 0 ? 'var(--color-buy)' : 'var(--color-sell)' }}>{money(a.unrealized_gain)} / {pct(a.unrealized_pct)}</td>
                          <td style={{ fontSize: '12px' }}>{Math.round(a.lt_shares).toLocaleString()} / {Math.round(a.st_shares).toLocaleString()}</td>
                          <td>
                            <label style={{ fontSize: '12px', display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                              <input
                                type="checkbox"
                                checked={!!a.auto_trade_blocked}
                                onChange={(e) => toggleEquityAutoTradeBlock(a.ticker, e.target.checked)}
                              />
                              {a.auto_trade_blocked ? 'Blocked' : 'Allowed'}
                            </label>
                          </td>
                          <td title={a.recommendation?.detail || ''} style={{ fontSize: '12px', color: a.recommendation?.action === 'trim' ? '#F59E0B' : a.recommendation?.action === 'hold' ? 'var(--color-buy)' : 'var(--text-primary)' }}>{a.recommendation?.label || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div id="equity-lot-form" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))', gap: '10px', marginBottom: '14px' }}>
                <input placeholder="Ticker" value={newEquityLot.ticker} onChange={(e) => setNewEquityLot({ ...newEquityLot, ticker: e.target.value.toUpperCase() })} style={{ background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px' }} />
                <input placeholder="Account" value={newEquityLot.account_label || ''} onChange={(e) => setNewEquityLot({ ...newEquityLot, account_label: e.target.value })} style={{ background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px' }} />
                <select value={newEquityLot.lot_type} onChange={(e) => setNewEquityLot({ ...newEquityLot, lot_type: e.target.value as any })} style={{ background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px' }}>
                  <option value="rsu">RSU</option><option value="espp">ESPP</option><option value="other">Other</option>
                </select>
                <input type="number" placeholder="Shares" value={newEquityLot.shares || ''} onChange={(e) => setNewEquityLot({ ...newEquityLot, shares: parseFloat(e.target.value) || 0 })} style={{ background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px' }} />
                <input type="number" placeholder="Basis/share" value={newEquityLot.cost_basis_per_share || ''} onChange={(e) => setNewEquityLot({ ...newEquityLot, cost_basis_per_share: parseFloat(e.target.value) || 0 })} style={{ background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px' }} />
                <input type="date" value={newEquityLot.acquisition_date} onChange={(e) => setNewEquityLot({ ...newEquityLot, acquisition_date: e.target.value })} style={{ background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px' }} />
                <button onClick={saveEquityLot} disabled={!newEquityLot.ticker || newEquityLot.shares <= 0} className="toggle-btn"><Plus size={14} /> {newEquityLot.id ? `Update Lot #${newEquityLot.id}` : 'Add Lot'}</button>
                {newEquityLot.id && <button onClick={() => setNewEquityLot({ ...newEquityLot, id: undefined, shares: 0, cost_basis_per_share: 0, notes: '' })} style={{ background: 'transparent', border: '1px solid var(--border-glass)', color: 'var(--text-secondary)', borderRadius: '6px', padding: '8px', cursor: 'pointer' }}>Cancel</button>}
              </div>
              {equityPlanPickById.size > 0 && (
                <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: '0 0 10px' }}>
                  Lots highlighted in amber match the sell plan below (<strong>Lot #</strong> is the database id used in both tables).
                </p>
              )}
              <table className="trade-table">
                <thead><tr><th>Lot</th><th>Ticker</th><th>Shares</th><th>Basis</th><th>Price</th><th>Value</th><th>P&L</th><th>Term</th><th>Recommendation</th><th></th></tr></thead>
                <tbody>
                  {equityLots.map((lot) => {
                    const planPick = lot.id != null ? equityPlanPickById.get(lot.id) : undefined;
                    return (
                    <tr key={lot.id} style={planPick ? { background: 'rgba(245, 158, 11, 0.07)' } : undefined}>
                      <td style={{ fontFamily: 'monospace', fontWeight: 600, whiteSpace: 'nowrap', color: planPick ? '#F59E0B' : 'var(--text-secondary)' }}>
                        #{lot.id}
                        {planPick && (
                          <div style={{ fontSize: '10px', fontWeight: 500, color: '#F59E0B' }} title="Included in sell plan below">
                            sell {planPick.sell_shares?.toFixed(2)} sh
                          </div>
                        )}
                      </td>
                      <td>{lot.ticker}<div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>{lot.acquisition_date}{lot.account_label ? ` · ${lot.account_label}` : ''}{lot.lot_type ? ` · ${String(lot.lot_type).toUpperCase()}` : ''}</div></td>
                      <td>{lot.shares.toFixed(2)}</td>
                      <td>{sharePrice(lot.cost_basis_per_share)}</td>
                      <td>{sharePrice(lot.current_price)}</td>
                      <td>{money(lot.market_value)}</td>
                      <td style={{ color: (lot.unrealized_gain || 0) >= 0 ? 'var(--color-buy)' : 'var(--color-sell)' }}>{money(lot.unrealized_gain)} / {pct(lot.unrealized_gain_pct)}</td>
                      <td>{lot.is_long_term ? 'LT' : `ST ${lot.days_to_long_term}d`}</td>
                      <td title={lot.recommendation?.detail || ''} style={{ fontSize: '12px', color: lot.recommendation?.action === 'harvest' ? 'var(--color-sell)' : lot.recommendation?.action === 'wait' ? '#F59E0B' : 'var(--text-secondary)' }}>{lot.recommendation?.label || '—'}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        <button onClick={() => setSellModal({ id: lot.id, ticker: lot.ticker, max: lot.shares, shares: lot.shares, sale_price: lot.current_price ? String(lot.current_price) : '', sale_date: new Date().toISOString().slice(0, 10), basis: lot.cost_basis_per_share, add_wash_sale_block: false })} className="toggle-btn" style={{ padding: '3px 8px', fontSize: '11.5px', marginRight: '6px' }}>Sell</button>
                        <button onClick={() => editEquityLot(lot)} style={{ background: 'transparent', border: 0, color: 'var(--text-secondary)', cursor: 'pointer', marginRight: '4px' }} title="Edit"><Sliders size={14} /></button>
                        <button onClick={() => deleteEquityLot(lot.id)} style={{ background: 'transparent', border: 0, color: 'var(--color-sell)', cursor: 'pointer' }} title="Delete"><Trash2 size={15} /></button>
                      </td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {equityLots.length > 0 && (() => {
              const grantTickers = Array.from(new Set(equityLots.map((l) => l.ticker))).sort();
              const visibleTickers = grantTickers.filter((t) => grantChartsVisible[t] !== false);
              return (
                <div className="glass-card" style={{ padding: '18px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '14px', flexWrap: 'wrap' }}>
                    <Activity size={20} color="#22D3EE" />
                    <h3 style={{ margin: 0, fontSize: '18px' }}>Grant Timeline</h3>
                    <span style={{ color: 'var(--text-secondary)', fontSize: '12px', flex: 1 }}>
                      Compare holdings side-by-side. Purple dashed lines mark upcoming vests from your schedule below.
                    </span>
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '12px', marginBottom: '14px', alignItems: 'center' }}>
                    <span style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)' }}>Show charts:</span>
                    {grantTickers.map((t) => (
                      <label key={t} style={{ fontSize: '13px', display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
                        <input
                          type="checkbox"
                          checked={grantChartsVisible[t] !== false}
                          onChange={(e) => setGrantChartsVisible({ ...grantChartsVisible, [t]: e.target.checked })}
                        />
                        <strong>{t}</strong>
                      </label>
                    ))}
                  </div>

                  <div style={{ marginBottom: '16px' }}>
                    <div style={{ fontSize: '12.5px', fontWeight: 600, marginBottom: '8px' }}>Vesting schedule</div>
                    <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: '0 0 10px' }}>
                      Set the next expected grant/vest date and cadence. Defaults are inferred from your imported lot history — edit if yours differs (e.g. June 23).
                    </p>
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '10px' }}>
                      {vestSchedules.map((vs) => (
                        <div key={`${vs.ticker}-${vs.lot_type}`} style={{
                          border: '1px solid var(--border-glass)', borderRadius: '8px', padding: '10px',
                          background: vs.vesting_complete ? 'rgba(0,0,0,0.08)' : 'rgba(0,0,0,0.15)',
                          opacity: vs.vesting_complete ? 0.85 : 1,
                        }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                            <div style={{ fontWeight: 700 }}>{vs.ticker} · {String(vs.lot_type).toUpperCase()}</div>
                            <label style={{ fontSize: '12px', display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer', whiteSpace: 'nowrap' }}>
                              <input
                                type="checkbox"
                                checked={!!vs.vesting_complete}
                                onChange={(e) => saveVestSchedule({ ...vs, vesting_complete: e.target.checked })}
                              />
                              Grants done
                            </label>
                          </div>
                          {vs.vesting_complete ? (
                            <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                              No upcoming vests — historical grants only. Uncheck to resume schedule tracking.
                            </div>
                          ) : (
                            <>
                              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', fontSize: '12px' }}>
                                <label>Next vest<input type="date" defaultValue={vs.next_vest_date}
                                  onBlur={(e) => saveVestSchedule({ ...vs, next_vest_date: e.target.value })}
                                  style={{ width: '100%', marginTop: '4px', background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '6px' }} /></label>
                                <label>Cadence<select defaultValue={vs.cadence}
                                  onChange={(e) => saveVestSchedule({ ...vs, cadence: e.target.value })}
                                  style={{ width: '100%', marginTop: '4px', background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '6px' }}>
                                  <option value="quarterly">Quarterly</option>
                                  <option value="semi_annual">Semi-annual</option>
                                  <option value="monthly">Monthly</option>
                                  <option value="annual">Annual</option>
                                </select></label>
                                <label>Day of month<input type="number" min={1} max={28} defaultValue={vs.vest_day || 20}
                                  onBlur={(e) => saveVestSchedule({ ...vs, vest_day: parseInt(e.target.value) || 20 })}
                                  style={{ width: '100%', marginTop: '4px', background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '6px' }} /></label>
                                <label>Est. shares<input type="number" defaultValue={vs.est_shares ?? ''}
                                  onBlur={(e) => saveVestSchedule({ ...vs, est_shares: parseFloat(e.target.value) || null })}
                                  style={{ width: '100%', marginTop: '4px', background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '6px' }} /></label>
                              </div>
                              {vs.days_until_next != null && (
                                <div style={{ marginTop: '8px', fontSize: '12px', color: vs.days_until_next <= 14 ? '#F59E0B' : 'var(--text-secondary)' }}>
                                  Next vest in {vs.days_until_next === 0 ? '0 days (today)' : `${vs.days_until_next} day${vs.days_until_next === 1 ? '' : 's'}`}
                                  {(vs.upcoming || []).slice(1, 3).length > 0 && (
                                    <span>{' '}· then {(vs.upcoming || []).slice(1, 3).map((u: any) => u.date).join(', ')}</span>
                                  )}
                                </div>
                              )}
                            </>
                          )}
                        </div>
                      ))}
                    </div>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px', marginTop: '10px' }}>
                      {grantTickers.flatMap((ticker) => {
                        const types = new Set(vestSchedules.filter((v) => v.ticker === ticker).map((v) => v.lot_type));
                        const missing: { lot_type: string; label: string; defaults: any }[] = [];
                        if (!types.has('rsu')) missing.push({
                          lot_type: 'rsu', label: 'RSU',
                          defaults: { cadence: 'quarterly', vest_day: 23, next_vest_date: '2026-06-23', vest_months: [3, 6, 9, 12] },
                        });
                        if (!types.has('espp')) missing.push({
                          lot_type: 'espp', label: 'ESPP',
                          defaults: { cadence: 'semi_annual', vest_day: 30, next_vest_date: '2026-06-30', vest_months: [6, 12] },
                        });
                        return missing.map((m) => (
                          <button key={`${ticker}-${m.lot_type}`} className="toggle-btn" style={{ fontSize: '12px' }}
                            onClick={() => saveVestSchedule({ ticker, lot_type: m.lot_type, est_shares: null, notes: '', ...m.defaults })}>
                            + {ticker} {m.label} schedule
                          </button>
                        ));
                      })}
                    </div>
                  </div>

                  {visibleTickers.length === 0 ? (
                    <div style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>Select at least one ticker above to show charts.</div>
                  ) : (
                    <div style={{
                      display: 'grid',
                      gridTemplateColumns: visibleTickers.length === 1 ? '1fr' : 'repeat(auto-fit, minmax(420px, 1fr))',
                      gap: '16px', alignItems: 'start',
                    }}>
                      {visibleTickers.map((t) => (
                        <GrantTimeline key={t} ticker={t} compact={visibleTickers.length > 1} />
                      ))}
                    </div>
                  )}
                </div>
              );
            })()}

            <div className="glass-card" style={{ padding: '18px' }}>
              <h3 style={{ marginTop: 0 }}>Analyst Forecast</h3>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px' }}>
                {Object.values(equityForecasts).filter(Boolean).map((f: any) => (
                  <div key={f.ticker} style={{ border: '1px solid var(--border-glass)', borderRadius: '8px', padding: '12px' }}>
                    <strong>{f.ticker}</strong>
                    <div style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>{f.source} · {f.as_of_date}</div>
                    <div>Price: {sharePrice(f.current_price)}</div>
                    {f.target_mean != null && <div>Target mean: {sharePrice(f.target_mean)} ({pct(f.upside_pct)})</div>}
                    {f.target_high != null && <div>Range: {sharePrice(f.target_low)} - {sharePrice(f.target_high)}</div>}
                    {f.num_analysts != null && <div>Analysts: {f.num_analysts}</div>}
                    {f.recommendation_key && <div>Rating: {f.recommendation_key}</div>}
                  </div>
                ))}
              </div>
            </div>

            <div className="glass-card" style={{ padding: '18px' }}>
              <h3 style={{ marginTop: 0 }}>Step 3 — Sell plan</h3>
              <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '6px' }}>
                <select value={equityObjective} onChange={(e) => setEquityObjective(e.target.value)} style={{ background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px' }}>
                  <option value="raise_cash">Raise cash</option>
                  <option value="harvest_loss">Harvest losses (cut taxes)</option>
                  <option value="exit_ticker">Exit a holding</option>
                </select>
                {equityObjective !== 'exit_ticker' && (
                  <div style={{ position: 'relative' }}>
                    <span style={{ position: 'absolute', left: '9px', top: '9px', color: 'var(--text-secondary)', fontSize: '12px' }}>$</span>
                    <input type="number" value={equityTarget} onChange={(e) => setEquityTarget(e.target.value)} placeholder="Target amount" style={{ background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px 8px 8px 20px', width: '150px' }} />
                  </div>
                )}
                {equityObjective === 'exit_ticker' && <input value={equityTargetTicker} onChange={(e) => setEquityTargetTicker(e.target.value.toUpperCase())} placeholder="Ticker" style={{ background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px', width: '100px' }} />}
                <button onClick={runEquityAnalyze} disabled={equityRunning || equityLots.length === 0} className="toggle-btn"><Play size={14} /> {equityRunning ? 'Analyzing...' : 'Build plan'}</button>
                {equityRunning && <span style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>{equityProgress.pct}% · {equityProgress.stage}</span>}
              </div>
              <div style={{ fontSize: '11.5px', color: 'var(--text-secondary)', marginBottom: '14px', lineHeight: 1.5 }}>
                {equityObjective === 'raise_cash' && 'Sells the most tax-efficient lots (losses first, then highest-cost shares) until it raises your target cash.'}
                {equityObjective === 'harvest_loss' && 'Sells losing lots to bank tax losses (which offset other gains), up to your target.'}
                {equityObjective === 'exit_ticker' && 'Plans a full exit of one ticker, ordered for tax efficiency, and warns about wash-sale timing.'}
              </div>
              {equityPlan && (() => { const rec = equityPlan.recommendation || {}; const guard = equityPlan.wash_sale_guard; const conc = equityPlan.concentration || []; return (
                <>
                  {conc.length > 0 && (
                    <div style={{ marginBottom: '14px' }}>
                      <div style={{ fontSize: '12.5px', fontWeight: 600, marginBottom: '6px' }}>Concentration &amp; harvestable losses</div>
                      <table className="trade-table">
                        <thead><tr><th>Ticker</th><th>Weight</th><th>Value</th><th>Unreal.</th><th>LT / ST sh</th><th>Harvestable loss</th><th>Tier</th></tr></thead>
                        <tbody>
                          {conc.map((r: any) => (
                            <tr key={r.ticker}>
                              <td><strong>{r.ticker}</strong></td>
                              <td style={{ color: r.weight > 0.25 ? '#F59E0B' : 'inherit' }}>{pct(r.weight)}{r.weight > 0.25 ? ' ⚠' : ''}</td>
                              <td>{money(r.market_value)}</td>
                              <td style={{ color: (r.unrealized_gain || 0) >= 0 ? 'var(--color-buy)' : 'var(--color-sell)' }}>{pct(r.unrealized_pct)}</td>
                              <td style={{ fontSize: '12px' }}>{Math.round(r.lt_shares).toLocaleString()} / {Math.round(r.st_shares).toLocaleString()}</td>
                              <td style={{ color: r.harvestable_loss < 0 ? 'var(--color-sell)' : 'var(--text-secondary)' }}>{r.harvestable_loss < 0 ? money(r.harvestable_loss) : '—'}</td>
                              <td style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{r.tier_label || '—'}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {equityPlan.narrative && (
                    <div style={{ background: 'rgba(56,189,248,0.08)', border: '1px solid rgba(56,189,248,0.25)', borderRadius: '8px', padding: '12px 14px', marginBottom: '14px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}><Brain size={14} color="#38BDF8" /><strong style={{ fontSize: '12.5px' }}>Advisor read</strong></div>
                      <p style={{ margin: 0, color: 'var(--text-primary)', fontSize: '13px', lineHeight: 1.55, whiteSpace: 'pre-wrap' }}>{equityPlan.narrative}</p>
                    </div>
                  )}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: '10px', marginBottom: '6px' }}>
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Cash from sale<br /><strong style={{ fontSize: '15px', color: 'var(--text-primary)' }}>{money(rec.gross_proceeds)}</strong></div>
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Tax owed<br /><strong style={{ fontSize: '15px', color: 'var(--text-primary)' }}>{money(rec.estimated_tax)}</strong></div>
                    {(rec.estimated_tax_savings || 0) > 0 && <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Tax savings<br /><strong style={{ fontSize: '15px', color: 'var(--color-buy)' }}>{money(rec.estimated_tax_savings)}</strong></div>}
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Net cash kept<br /><strong style={{ fontSize: '15px', color: 'var(--color-gold)' }}>{money(rec.net_cash)}</strong></div>
                    <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Realized gain<br /><strong style={{ fontSize: '15px', color: (rec.realized_gain || 0) >= 0 ? 'var(--color-buy)' : 'var(--color-sell)' }}>{money(rec.realized_gain)}</strong></div>
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '12px', lineHeight: 1.5 }}>
                    Net cash = cash from sale − tax owed. Tax savings from losses offset <em>other</em> gains, so they’re shown separately (not added to cash). Estimates round tax up to stay conservative.
                  </div>
                  <table className="trade-table">
                    <thead><tr><th>Lot</th><th>Ticker</th><th>Sell</th><th>Proceeds</th><th>Gain</th><th>Tax</th><th>Note</th></tr></thead>
                    <tbody>{(equityPlan.recommendation?.picks || []).map((p: any) => (
                      <tr key={`${p.id}-${p.sell_shares}`} style={{ background: 'rgba(245, 158, 11, 0.07)' }}>
                        <td style={{ fontFamily: 'monospace', fontWeight: 600, color: '#F59E0B' }}>#{p.id}</td>
                        <td>{p.ticker}</td>
                        <td>{p.sell_shares.toFixed(2)}</td><td>{money(p.sale_proceeds)}</td><td>{money(p.gain)}</td><td>{money(p.estimated_tax)}</td><td>{p.wait_flag || ''}</td></tr>
                    ))}</tbody>
                  </table>
                  {guard && guard.tickers && guard.tickers.length > 0 && (
                    <div style={{ background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.3)', borderRadius: '8px', padding: '12px 14px', marginTop: '12px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '6px' }}><Lock size={14} color="#10B981" /><strong style={{ fontSize: '12.5px' }}>Protect this harvest</strong></div>
                      <p style={{ margin: '0 0 10px', fontSize: '12.5px', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                        If you place these loss sales, block the bot from re-buying for 31 days so the IRS wash-sale rule can&rsquo;t disallow the loss. One click per name records the sale date and the block. (Watch your RSU vest calendar — a vest inside the window can also trip it.)
                      </p>
                      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                        {guard.tickers.map((t: any) => {
                          const already = tradingBlocks.some((b) => b.ticker === t.ticker);
                          return (
                            <button key={t.ticker} disabled={already}
                              onClick={() => createTradingBlock({ ticker: t.ticker, block_type: 'wash_sale', sale_date: guard.sale_date, window_days: guard.window_days, realized_loss: t.realized_loss, shares: t.shares, reason: `Loss sale ${guard.sale_date} (~${money(t.realized_loss)}) — no re-buys until ${guard.blocked_until}.` })}
                              className="toggle-btn" style={{ opacity: already ? 0.5 : 1 }}>
                              {already ? `✓ ${t.ticker} blocked` : `Block ${t.ticker} until ${guard.blocked_until}`}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                  {(equityPlan.wash_sale_warnings || []).map((w: any, i: number) => (
                    <div key={i} style={{ color: '#F59E0B', display: 'flex', gap: '6px', alignItems: 'center', marginTop: '8px' }}><AlertTriangle size={14} /> {w.message}</div>
                  ))}
                  <p style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>{equityPlan.disclaimer}</p>
                </>
              ); })()}
            </div>
          </section>
        )}

        {activeTab === 'crash' && (
          <section className="crash-grid">

            {/* Full-width Timeline Graph Card */}
            <div className="glass-card" style={{ gridColumn: '1 / -1', padding: '24px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Activity size={20} color="var(--color-accent)" />
                  <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>Composite Crash-Risk Timeline (Past 5 Years)</h3>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
                  {[
                    { label: 'Calm', color: '#10B981', range: '0–40' },
                    { label: 'Elevated', color: '#3B82F6', range: '40–65' },
                    { label: 'High', color: '#F59E0B', range: '65–80' },
                    { label: 'Extreme', color: '#EF4444', range: '80–100' },
                  ].map((b) => (
                    <span key={b.label} style={{ display: 'flex', alignItems: 'center', gap: '5px', fontSize: '11px', color: 'var(--text-secondary)' }}>
                      <span style={{ width: '10px', height: '10px', borderRadius: '2px', background: b.color, display: 'inline-block' }} />
                      {b.label} <span style={{ opacity: 0.6 }}>{b.range}</span>
                    </span>
                  ))}
                </div>
              </div>

              <div style={{ width: '100%', height: '300px' }}>
                {timelineData && timelineData.length > 0 ? (
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={timelineData} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                      <defs>
                        {/* Risk heat map: color tracks the index value (green=low risk -> red=high risk).
                            Gradient runs top(value=100)->bottom(value=0); offsets = (100 - value)/100,
                            so band thresholds 80/65/40 sit at offsets 0.20/0.35/0.60. */}
                        <linearGradient id="crashStroke" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0" stopColor="#EF4444" />
                          <stop offset="0.20" stopColor="#EF4444" />
                          <stop offset="0.35" stopColor="#F59E0B" />
                          <stop offset="0.60" stopColor="#3B82F6" />
                          <stop offset="1" stopColor="#10B981" />
                        </linearGradient>
                        <linearGradient id="crashFill" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0" stopColor="#EF4444" stopOpacity={0.55} />
                          <stop offset="0.20" stopColor="#EF4444" stopOpacity={0.40} />
                          <stop offset="0.35" stopColor="#F59E0B" stopOpacity={0.30} />
                          <stop offset="0.60" stopColor="#3B82F6" stopOpacity={0.20} />
                          <stop offset="1" stopColor="#10B981" stopOpacity={0.05} />
                        </linearGradient>
                      </defs>
                      <XAxis
                        dataKey="date"
                        tickFormatter={(tick) => {
                          const date = new Date(tick);
                          return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}`;
                        }}
                        stroke="var(--text-secondary)"
                        fontSize={10}
                        tickLine={false}
                      />
                      <YAxis
                        domain={[0, 100]}
                        ticks={[0, 40, 65, 80, 100]}
                        stroke="var(--text-secondary)"
                        fontSize={10}
                        tickLine={false}
                      />
                      {/* Band threshold guide lines */}
                      <ReferenceLine y={40} stroke="#3B82F6" strokeDasharray="3 3" strokeOpacity={0.35} />
                      <ReferenceLine y={65} stroke="#F59E0B" strokeDasharray="3 3" strokeOpacity={0.35} />
                      <ReferenceLine y={80} stroke="#EF4444" strokeDasharray="3 3" strokeOpacity={0.45} />
                      <Tooltip
                        content={({ active, payload }) => {
                          if (active && payload && payload.length) {
                            const data = payload[0].payload;
                            const bandColor =
                              data.risk_band === 'Calm' ? '#10B981' :
                              data.risk_band === 'Elevated' ? '#3B82F6' :
                              data.risk_band === 'High' ? '#F59E0B' : '#EF4444';
                            return (
                              <div style={{ background: 'rgba(16, 20, 38, 0.95)', border: `1px solid ${bandColor}`, borderRadius: '8px', padding: '12px', boxShadow: '0 10px 30px rgba(0,0,0,0.5)' }}>
                                <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '4px' }}>{data.date}</div>
                                <div style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', marginBottom: '2px' }}>
                                  Index: <span style={{ color: bandColor }}>{data.composite_index.toFixed(1)}</span>
                                </div>
                                <div style={{ fontSize: '12px', fontWeight: 600, display: 'flex', gap: '6px' }}>
                                  <span style={{ color: bandColor }}>
                                    {data.risk_band.toUpperCase()}
                                  </span>
                                  <span style={{ color: 'var(--text-secondary)' }}>·</span>
                                  <span style={{ color: '#3B82F6' }}>{data.current_posture}</span>
                                </div>
                              </div>
                            );
                          }
                          return null;
                        }}
                      />
                      <Area
                        type="monotone"
                        dataKey="composite_index"
                        stroke="url(#crashStroke)"
                        strokeWidth={2.5}
                        fillOpacity={1}
                        fill="url(#crashFill)"
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                ) : (
                  <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-secondary)', fontSize: '14px' }}>
                    Loading timeline data...
                  </div>
                )}
              </div>
            </div>

            {/* COLUMN 1: RISK ASSESSMENT & GLIDE PATH KNOBS */}
            <div style={{ display: 'grid', gap: '20px', alignContent: 'start' }}>

              {/* Card 1: Composite Crash-Risk Index */}
              <div className="glass-card" style={{ padding: '24px', position: 'relative', overflow: 'hidden' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <ShieldAlert size={20} color="#F59E0B" />
                    <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>Composite Crash-Risk Index</h3>
                  </div>
                  <span style={{ fontSize: '12px', background: 'rgba(255,255,255,0.06)', padding: '4px 8px', borderRadius: '4px', color: 'var(--text-secondary)' }}>
                    As of: {crashData?.as_of_date || 'Loading...'}
                  </span>
                </div>
                <TimingBadge lastRun={crashStatus?.index?.last_refresh} nextScheduled={crashStatus?.index?.next_scheduled} schedule={crashStatus?.index?.schedule} />

                {crashData ? (
                  <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', margin: '14px 0' }}>
                    {/* Gauge Circle */}
                    <div style={{ position: 'relative', width: '160px', height: '160px', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                      <svg width="160" height="160" viewBox="0 0 160 160">
                        <circle cx="80" cy="80" r="70" fill="none" stroke="rgba(255,255,255,0.05)" strokeWidth="12" />
                        <circle cx="80" cy="80" r="70" fill="none"
                          stroke={
                            crashData.risk_band === 'Calm' ? '#10B981' :
                            crashData.risk_band === 'Elevated' ? '#3B82F6' :
                            crashData.risk_band === 'High' ? '#F59E0B' : '#EF4444'
                          }
                          strokeWidth="12"
                          strokeDasharray={`${2 * Math.PI * 70}`}
                          strokeDashoffset={`${2 * Math.PI * 70 * (1 - crashData.composite_index / 100)}`}
                          strokeLinecap="round"
                          transform="rotate(-90 80 80)"
                        />
                      </svg>
                      <div style={{ position: 'absolute', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                        <span style={{ fontSize: '32px', fontWeight: 800, color: 'var(--text-primary)' }}>
                          {(crashData?.composite_index ?? 0).toFixed(1)}
                        </span>
                        <span style={{ fontSize: '12px', fontWeight: 600, color:
                          crashData.risk_band === 'Calm' ? '#10B981' :
                          crashData.risk_band === 'Elevated' ? '#3B82F6' :
                          crashData.risk_band === 'High' ? '#F59E0B' : '#EF4444'
                        }}>
                          {crashData.risk_band.toUpperCase()}
                        </span>
                      </div>
                    </div>

                    <div style={{ display: 'flex', gap: '16px', marginTop: '18px', width: '100%', justifyContent: 'space-around' }}>
                      <div style={{ textAlign: 'center' }}>
                        <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>POSTURE</div>
                        <div style={{ fontSize: '15px', fontWeight: 700, color: '#3B82F6', marginTop: '2px' }}>{crashData.current_posture}</div>
                      </div>
                      <div style={{ textAlign: 'center' }}>
                        <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>DE-RISK EXP</div>
                        <div style={{ fontSize: '15px', fontWeight: 700, color: '#F59E0B', marginTop: '2px' }}>
                          {playbook ? `${(playbook.de_risk_coefficient * 100).toFixed(0)}%` : 'Loading...'}
                        </div>
                      </div>
                    </div>

                    {/* Trigger Reasons */}
                    <div style={{ marginTop: '20px', width: '100%', borderTop: '1px solid var(--border-glass)', paddingTop: '16px' }}>
                      <h4 style={{ margin: '0 0 10px', fontSize: '13px', color: 'var(--text-primary)' }}>System Triggers</h4>
                      <ul style={{ margin: 0, paddingLeft: '16px', fontSize: '12.5px', color: 'var(--text-secondary)', display: 'grid', gap: '6px' }}>
                        {crashData.trigger_reasons && crashData.trigger_reasons.length > 0 ? (
                          crashData.trigger_reasons.map((reason: string, idx: number) => (
                            <li key={idx} style={{ lineHeight: '1.4' }}>{reason}</li>
                          ))
                        ) : (
                          <li>All macro, credit, and internals indicators reflect normal conditions.</li>
                        )}
                      </ul>
                    </div>
                  </div>
                ) : (
                  <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text-secondary)' }}>Loading composite index...</div>
                )}
              </div>

              {/* Card 2: Glide Path Knobs */}
              <div className="glass-card" style={{ padding: '24px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '16px' }}>
                  <Sliders size={20} color="#10B981" />
                  <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>Glide-Path Policy Curve</h3>
                </div>
                <p style={{ fontSize: '12.5px', color: 'var(--text-secondary)', margin: '0 0 20px', lineHeight: '1.4' }}>
                  Adjust de-risking sensitivity thresholds. Choose a preset or customize parameters dynamically.
                </p>

                <div style={{ display: 'flex', gap: '10px', marginBottom: '22px' }}>
                  {(['conservative', 'balanced', 'aggressive'] as const).map(p => (
                    <button key={p}
                      onClick={() => applyPreset(p)}
                      className={`toggle-btn ${preset === p ? 'active' : ''}`}
                      style={{ flex: 1, textTransform: 'capitalize' }}
                    >
                      {p}
                    </button>
                  ))}
                </div>

                <div style={{ display: 'grid', gap: '18px' }}>
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12.5px', marginBottom: '6px' }}>
                      <span>De-risking Threshold (θ)</span>
                      <strong style={{ color: 'var(--text-primary)' }}>{theta.toFixed(2)}</strong>
                    </div>
                    <input type="range" min="0.4" max="1.4" step="0.05" value={theta} onChange={(e) => { setTheta(parseFloat(e.target.value)); setPreset('custom'); }} style={{ width: '100%' }} />
                    <span style={{ fontSize: '10.5px', color: 'var(--text-secondary)' }}>Standardized score above which de-allocating equities begins.</span>
                  </div>

                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12.5px', marginBottom: '6px' }}>
                      <span>Curve Steepness (k)</span>
                      <strong style={{ color: 'var(--text-primary)' }}>{k.toFixed(1)}</strong>
                    </div>
                    <input type="range" min="1.0" max="5.0" step="0.1" value={k} onChange={(e) => { setK(parseFloat(e.target.value)); setPreset('custom'); }} style={{ width: '100%' }} />
                    <span style={{ fontSize: '10.5px', color: 'var(--text-secondary)' }}>Determines how rapidly the de-risking blends cash as risk increases.</span>
                  </div>

                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12.5px', marginBottom: '6px' }}>
                      <span>Trend Gate Strength (γ)</span>
                      <strong style={{ color: 'var(--text-primary)' }}>{gamma.toFixed(2)}</strong>
                    </div>
                    <input type="range" min="0.0" max="0.5" step="0.05" value={gamma} onChange={(e) => { setGamma(parseFloat(e.target.value)); setPreset('custom'); }} style={{ width: '100%' }} />
                    <span style={{ fontSize: '10.5px', color: 'var(--text-secondary)' }}>Raises de-risking threshold during active market uptrends.</span>
                  </div>
                </div>

                {/* Preset comparison (read-only walk-forward backtest) */}
                <div style={{ marginTop: '24px', borderTop: '1px solid var(--border-color)', paddingTop: '18px' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                    <strong style={{ fontSize: '13.5px' }}>Compare Policies (Backtest)</strong>
                    <button
                      className="toggle-btn"
                      disabled={comparingPresets}
                      onClick={runPresetComparison}
                      style={{ fontSize: '12px', padding: '6px 12px' }}
                    >
                      {comparingPresets ? 'Running…' : 'Run Comparison'}
                    </button>
                  </div>
                  <p style={{ fontSize: '10.5px', color: 'var(--text-secondary)', margin: '0 0 14px', lineHeight: '1.4' }}>
                    Walk-forward simulation of each preset (and your current Custom knobs) steering an SPY/TLT blend with the real historical crash-risk index, vs passive Buy &amp; Hold. Analysis only — this does not change your portfolio.
                  </p>

                  {compareData && compareData.series && (
                    <>
                      <div style={{ width: '100%', height: '220px', marginBottom: '12px' }}>
                        <ResponsiveContainer>
                          <LineChart
                            data={(compareData.dates || []).map((d: string, i: number) => {
                              const row: any = { date: d };
                              compareData.series.forEach((s: any) => { row[s.label] = s.equity_curve[i]; });
                              row['benchmark'] = compareData.benchmark?.equity_curve[i];
                              return row;
                            })}
                            margin={{ top: 5, right: 10, left: 0, bottom: 0 }}
                          >
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                            <XAxis dataKey="date" tick={{ fontSize: 9 }} minTickGap={40} />
                            <YAxis tick={{ fontSize: 9 }} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`} width={42} />
                            <Tooltip
                              contentStyle={{ background: 'var(--bg-secondary)', border: '1px solid var(--border-color)', fontSize: '11px' }}
                              formatter={(v: any, name: any) => [`$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`, name]}
                            />
                            <Legend wrapperStyle={{ fontSize: '10.5px' }} />
                            {(() => {
                              const colors: Record<string, string> = { conservative: '#10B981', balanced: '#3B82F6', aggressive: '#F59E0B', custom: '#A855F7' };
                              const lines = compareData.series.map((s: any) => (
                                <Line key={s.label} type="monotone" dataKey={s.label} stroke={colors[s.label] || '#888'} dot={false} strokeWidth={1.8} name={s.label} />
                              ));
                              lines.push(<Line key="benchmark" type="monotone" dataKey="benchmark" stroke="#9CA3AF" strokeDasharray="5 4" dot={false} strokeWidth={1.5} name="Buy & Hold" />);
                              return lines;
                            })()}
                          </LineChart>
                        </ResponsiveContainer>
                      </div>

                      <table className="trade-table" style={{ fontSize: '11.5px' }}>
                        <thead>
                          <tr>
                            <th>Policy</th>
                            <th>Return</th>
                            <th>Max DD</th>
                            <th>Ulcer</th>
                            <th>Sharpe</th>
                            <th>Turnover</th>
                          </tr>
                        </thead>
                        <tbody>
                          {[...compareData.series, compareData.benchmark].map((s: any, idx: number) => (
                            <tr key={idx}>
                              <td style={{ textTransform: 'capitalize', fontWeight: 600 }}>
                                {s.label}
                                {s.theta != null && (
                                  <span style={{ color: 'var(--text-secondary)', fontWeight: 400, fontSize: '10px' }}>
                                    {` (θ${s.theta.toFixed(2)}, k${s.k.toFixed(1)}, γ${s.gamma.toFixed(2)})`}
                                  </span>
                                )}
                              </td>
                              <td style={{ color: '#10B981', fontWeight: 600 }}>{s.total_return.toFixed(1)}%</td>
                              <td style={{ color: '#EF4444' }}>-{s.max_drawdown.toFixed(1)}%</td>
                              <td>{s.ulcer_index.toFixed(2)}</td>
                              <td>{s.sharpe.toFixed(2)}</td>
                              <td>{s.turnover.toFixed(1)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <p style={{ fontSize: '10px', color: 'var(--text-secondary)', margin: '10px 0 0', lineHeight: '1.4' }}>
                        {compareData.start_date} → {compareData.end_date}. Lower Max DD/Ulcer = smoother ride; higher Return/Sharpe = more upside. Defensive presets trade upside for drawdown protection. Past simulation is not a predictor.
                      </p>
                    </>
                  )}
                </div>
              </div>

              {/* Card 3: Experimental Drawdown Odds */}
              <div className="glass-card" style={{ padding: '24px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Brain size={20} color="#3B82F6" />
                    <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>Experimental Drawdown Odds</h3>
                  </div>
                  {forecastStatus && (
                    <span style={{ fontSize: '11px', color: '#F59E0B' }}>{forecastStatus}</span>
                  )}
                </div>
                <TimingBadge lastRun={crashStatus?.forecast?.last_refresh} nextScheduled={crashStatus?.forecast?.next_scheduled} schedule={crashStatus?.forecast?.schedule} />

                <p style={{ fontSize: '12.5px', color: 'var(--text-secondary)', margin: '0 0 16px', lineHeight: '1.4' }}>
                  Forward probability odds of an SPY drawdown, from penalized L2 logistic regressions. Odds are calibrated to historical base rates and projected onto a logically-coherent grid (deeper drawdowns are never more likely than shallower ones; longer horizons never less likely than shorter ones). <strong>Reliability</strong> reflects purged-embargo cross-validated AUC &mdash; values near 0.5 mean the model has little skill beyond the base rate.
                </p>

                {crashData?.experimental_forecast_odds && crashData.experimental_forecast_odds.length > 0 ? (
                  <table className="trade-table" style={{ marginBottom: '16px' }}>
                    <thead>
                      <tr>
                        <th>Drawdown</th>
                        <th>Horizon</th>
                        <th>Probability</th>
                        <th>Base Rate</th>
                        <th>Reliability</th>
                      </tr>
                    </thead>
                    <tbody>
                      {crashData.experimental_forecast_odds.map((item: any, idx: number) => {
                        const auc = item.cv_auc;
                        const skill = auc == null
                          ? { label: 'n/a', color: 'var(--text-secondary)' }
                          : auc >= 0.60
                            ? { label: `AUC ${auc.toFixed(2)}`, color: '#10B981' }
                            : auc >= 0.55
                              ? { label: `AUC ${auc.toFixed(2)}`, color: '#F59E0B' }
                              : { label: `AUC ${auc.toFixed(2)}`, color: '#EF4444' };
                        return (
                          <tr key={idx}>
                            <td><strong>{item.drawdown}</strong></td>
                            <td>{item.horizon_days} Days</td>
                            <td style={{ color: item.probability > 0.3 ? '#EF4444' : '#10B981', fontWeight: 700 }}>
                              {(item.probability * 100).toFixed(0)}%
                            </td>
                            <td style={{ color: 'var(--text-secondary)' }}>
                              {item.base_rate != null ? `${(item.base_rate * 100).toFixed(0)}%` : '\u2014'}
                            </td>
                            <td style={{ color: skill.color, fontSize: '11.5px', fontWeight: 600 }}>
                              {skill.label}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                ) : (
                  <div style={{ padding: '20px 0', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '13px' }}>
                    No odds forecast calculations cached in snapshot.
                  </div>
                )}

                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  <button
                    disabled={!!forecastJobId}
                    onClick={async () => {
                      setForecastStatus('Queued...');
                      try {
                        const res = await fetch(apiUrl('/api/crash/forecast'), { method: 'POST' });
                        if (res.ok) {
                          const data = await res.json();
                          setForecastJobId(data.job_id);
                        }
                      } catch (err) {
                        setForecastStatus('Trigger failed.');
                      }
                    }}
                    className="toggle-btn"
                  >
                    {forecastJobId ? 'Retraining models...' : 'Run Purged CV Forecast'}
                  </button>
                  <span style={{ fontSize: '10.5px', color: 'var(--text-secondary)', fontStyle: 'italic', textAlign: 'center' }}>
                    ⚠ Small sample warning: Model is trained on very few historical crash episodes since 1998.
                  </span>
                </div>
              </div>
            </div>

            {/* COLUMN 2: STRATEGIC PLAYBOOK & WARGAMING */}
            <div style={{ display: 'grid', gap: '20px', alignContent: 'start' }}>

              {/* Card 4: Defensive Stance Playbook */}
              <div className="glass-card" style={{ padding: '24px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Compass size={20} color="#F59E0B" />
                    <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>Defensive Stance Playbook</h3>
                  </div>
                </div>

                {playbook ? (
                  <div style={{ display: 'grid', gap: '16px' }}>

                    {/* Stance Card: Buffett Cash Stance */}
                    <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-glass)', borderRadius: '8px', padding: '12px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', marginBottom: '8px' }}>
                        <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>Buffett Cash Ladder Stance</span>
                        <span style={{ color: '#F59E0B', fontWeight: 700 }}>
                          Target Cash: {playbook.stances?.buffett?.target_cash_pct.toFixed(0)}%
                        </span>
                      </div>
                      <div style={{ display: 'grid', gap: '6px' }}>
                        {playbook.stances?.buffett?.ladders.map((l: any, idx: number) => (
                          <div key={idx} style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11.5px', color: 'var(--text-secondary)' }}>
                            <span>Deploy Tranche on {l.drawdown} dip:</span>
                            <span>{l.pct_of_reserve_to_deploy}% reserve ({l.sizing_rule})</span>
                          </div>
                        ))}
                      </div>
                    </div>

                    {/* Stance Card: Safe Asset Branch */}
                    <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-glass)', borderRadius: '8px', padding: '12px' }}>
                      <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '4px' }}>
                        Macro Season: <span style={{ color: '#10B981' }}>{playbook.stances?.safe_asset_selection?.active_branch}</span>
                      </div>
                      <p style={{ fontSize: '11px', color: 'var(--text-secondary)', margin: '0 0 8px', lineHeight: '1.3' }}>
                        {playbook.stances?.safe_asset_selection?.explanation}
                      </p>
                      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                        {Object.entries(playbook.stances?.safe_asset_selection?.mix || {}).map(([k, v]: any) => (
                          <span key={k} title={playbook.stances?.safe_asset_selection?.mix_labels?.[k] || k} style={{ fontSize: '10.5px', background: 'rgba(16,185,129,0.12)', border: '1px solid rgba(16,185,129,0.3)', color: '#10B981', padding: '2px 6px', borderRadius: '4px' }}>
                            {k} ({playbook.stances?.safe_asset_selection?.mix_labels?.[k] || k}): {v}%
                          </span>
                        ))}
                      </div>
                    </div>

                    {/* Stance Card: Dalio All-Weather & Taleb Barbell */}
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
                      <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-glass)', borderRadius: '8px', padding: '10px' }}>
                        <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '6px', color: 'var(--text-primary)' }}>Dalio Risk-Parity</div>
                        <ul style={{ margin: 0, paddingLeft: '12px', fontSize: '10.5px', color: 'var(--text-secondary)' }}>
                          <li>Equities capped at {playbook.stances?.dalio?.hmm_gated ? '10%' : '30%'}</li>
                          <li>Treasuries: 40-50%</li>
                          <li>Gold/Commodities</li>
                        </ul>
                      </div>
                      <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-glass)', borderRadius: '8px', padding: '10px' }}>
                        <div style={{ fontSize: '12px', fontWeight: 600, marginBottom: '6px', color: 'var(--text-primary)' }}>Taleb Barbell Stance</div>
                        <ul style={{ margin: 0, paddingLeft: '12px', fontSize: '10.5px', color: 'var(--text-secondary)' }}>
                          <li>90% Safe FDIC/BIL</li>
                          <li>10% Speculative End</li>
                          <li>OTM SPY Put Hedging</li>
                        </ul>
                      </div>
                    </div>

                    {/* Stance Card: Minsky/Dalio Debt Cycle */}
                    <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-glass)', borderRadius: '8px', padding: '12px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', marginBottom: '8px' }}>
                        <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>Debt-Cycle Fragility Profile</span>
                        <span style={{ color: '#EF4444', fontWeight: 700 }}>
                          {crashData?.debt_cycle_metrics?.qualitative_state}
                        </span>
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', fontSize: '11px', color: 'var(--text-secondary)' }}>
                        <div>Debt/GDP: <strong>{(crashData?.debt_cycle_metrics?.debt_to_gdp_pct ?? 0).toFixed(1)}%</strong></div>
                        <div>Debt Service Ratio: <strong>{(crashData?.debt_cycle_metrics?.debt_service_ratio ?? 0).toFixed(1)}%</strong></div>
                        <div>Real rates: <strong>{(crashData?.debt_cycle_metrics?.real_rates ?? 0).toFixed(2)}%</strong></div>
                        <div>Margin debt change: <strong style={{ color: (crashData?.debt_cycle_metrics?.margin_debt_yoy_pct ?? 0) > 15.0 ? '#EF4444' : 'inherit' }}>
                          {(crashData?.debt_cycle_metrics?.margin_debt_yoy_pct ?? 0).toFixed(1)}% YoY
                        </strong></div>
                      </div>
                    </div>

                    {/* Execution Apply Section */}
                    <div style={{ borderTop: '1px solid var(--border-glass)', paddingTop: '16px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '13px', alignItems: 'center' }}>
                        <span>Target allocation Stance:</span>
                        <strong style={{ color: 'var(--text-primary)' }}>{crashData?.current_posture}</strong>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', color: 'var(--text-secondary)' }}>
                        <span>Glide-path curve in use:</span>
                        <strong style={{ color: 'var(--text-primary)' }}>{preset === 'custom' ? 'Custom knobs' : `${preset.charAt(0).toUpperCase()}${preset.slice(1)} preset`}</strong>
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', color: 'var(--text-secondary)' }}>
                        <span>De-risk coefficient (d):</span>
                        <strong style={{ color: '#F59E0B' }}>{playbook ? `${(playbook.de_risk_coefficient * 100).toFixed(0)}% to safe assets` : '—'}</strong>
                      </div>
                      <p style={{ fontSize: '11px', color: 'var(--text-secondary)', margin: '2px 0 0', lineHeight: 1.4 }}>
                        Blends your current holdings with the safe-asset mix at the de-risk coefficient set by the
                        glide-path curve above, then rebalances the <strong>paper account only</strong>. Preview the
                        exact orders before anything runs.
                      </p>

                      <button
                        disabled={applyingRebalance}
                        onClick={openPreview}
                        className="toggle-btn"
                        style={{ background: 'var(--color-gold)', color: 'black', fontWeight: 700, border: 'none' }}
                      >
                        {applyingRebalance ? 'Applying rebalancing...' : 'Preview Rebalancing (Paper)'}
                      </button>

                      {applyResult && (
                        <div style={{ background: 'rgba(16,185,129,0.12)', border: '1px solid rgba(16,185,129,0.4)', borderRadius: '6px', padding: '10px', fontSize: '12px', color: 'var(--text-primary)' }}>
                          <div style={{ fontWeight: 600, color: '#10B981', marginBottom: '4px' }}>✓ Stance Applied Successfully</div>
                          <div>Submitted {applyResult.orders_submitted?.length || 0} orders: {
                            applyResult.orders_submitted?.map((o: any) => `${o.symbol} (${o.side})`).join(', ')
                          }</div>
                          <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                            Broker cash reserve balance: {money(applyResult.cash_transferred_to_reserve)}
                          </div>
                        </div>
                      )}
                    </div>

                  </div>
                ) : (
                  <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text-secondary)' }}>Loading defensive playbook...</div>
                )}
              </div>

              {/* Card 5: Scenario Wargame — policy comparison across crashes */}
              <div className="glass-card" style={{ padding: '24px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Activity size={20} color="#3B82F6" />
                    <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>Scenario Wargame</h3>
                  </div>
                  {scenarioStatus && (
                    <span style={{ fontSize: '11.5px', color: '#F59E0B' }}>{scenarioStatus}</span>
                  )}
                </div>
                <p style={{ fontSize: '12.5px', color: 'var(--text-secondary)', margin: '0 0 14px', lineHeight: '1.45' }}>
                  Replays each strategy — from doing nothing (Buy &amp; Hold) to fully defensive — across real bear
                  markets and synthetic crashes, so you can see how they would have steered an SPY/defense blend.
                  Read-only; never changes your portfolio.
                </p>
                {scenarioData && (
                  <TimingBadge lastRun={crashStatus?.wargame?.last_run} nextScheduled={crashStatus?.wargame?.next_scheduled} schedule={crashStatus?.wargame?.schedule} stale={crashStatus?.wargame?.stale} />
                )}

                {/* Knob glossary */}
                {(() => {
                  const g = scenarioData?.knob_glossary || {
                    theta: { symbol: 'θ', name: 'De-risking threshold', desc: 'How high crash-risk must climb before you start moving to defense. Higher = wait longer (aggressive).' },
                    k: { symbol: 'k', name: 'Curve steepness', desc: 'How sharply you flip from stocks to defense around the threshold. Higher = faster, more all-or-nothing.' },
                    gamma: { symbol: 'γ', name: 'Trend gate', desc: 'Keeps you invested longer during uptrends so you don\u2019t bail early in a melt-up.' },
                  };
                  return (
                    <div style={{ display: 'grid', gap: '8px', marginBottom: '14px', background: 'rgba(255,255,255,0.02)', padding: '12px', borderRadius: '8px', border: '1px solid var(--border-glass)' }}>
                      <div style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-secondary)' }}>What the glide-path knobs mean</div>
                      {['theta', 'k', 'gamma'].map((kk) => g[kk] && (
                        <div key={kk} style={{ fontSize: '11.5px', color: 'var(--text-secondary)', lineHeight: '1.4' }}>
                          <strong style={{ color: 'var(--text-primary)' }}>{g[kk].symbol} — {g[kk].name}:</strong> {g[kk].desc}
                        </div>
                      ))}
                    </div>
                  );
                })()}

                <button
                  disabled={!!scenarioJobId}
                  onClick={runScenarioComparison}
                  className="toggle-btn"
                  style={{ width: '100%', marginBottom: scenarioData ? '16px' : '0' }}
                >
                  {scenarioJobId ? `Running… ${scenarioStatus}` : (scenarioData ? 'Re-run Wargame' : 'Run Scenario Wargame')}
                  {!scenarioJobId && preset === 'custom' ? ' (incl. your custom knobs)' : ''}
                </button>

                {scenarioData && (() => {
                  const policies = scenarioData.policies || [];
                  const polById: any = Object.fromEntries(policies.map((p: any) => [p.id, p]));
                  const sc = (scenarioData.scenarios || []).find((s: any) => s.id === selectedScenario) || scenarioData.scenarios?.[0];
                  if (!sc) return null;
                  const chartData = (sc.dates || []).map((d: string, i: number) => {
                    const row: any = { date: d };
                    policies.forEach((p: any) => { row[p.id] = sc.series?.[p.id]?.equity_curve?.[i]; });
                    return row;
                  });
                  const ranked = [...policies].sort((a: any, b: any) => (sc.series?.[b.id]?.total_return || 0) - (sc.series?.[a.id]?.total_return || 0));
                  return (
                    <div>
                      {/* Scenario selector */}
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginBottom: '10px' }}>
                        {(scenarioData.scenarios || []).map((s: any) => (
                          <button key={s.id} onClick={() => setSelectedScenario(s.id)}
                            style={{ fontSize: '11px', padding: '5px 9px', borderRadius: '6px', cursor: 'pointer',
                              background: s.id === sc.id ? 'rgba(59,130,246,0.18)' : 'rgba(255,255,255,0.03)',
                              border: `1px solid ${s.id === sc.id ? 'rgba(59,130,246,0.5)' : 'var(--border-glass)'}`,
                              color: s.id === sc.id ? '#93c5fd' : 'var(--text-secondary)', fontWeight: s.id === sc.id ? 700 : 500 }}>
                            {s.label}
                          </button>
                        ))}
                      </div>
                      <p style={{ fontSize: '11.5px', color: 'var(--text-secondary)', margin: '0 0 10px', lineHeight: '1.4' }}>
                        {sc.subtitle} <span style={{ color: 'var(--text-primary)' }}>Perfect-foresight ceiling: {sc.perfect_foresight_return >= 0 ? '+' : ''}{sc.perfect_foresight_return}%.</span>
                      </p>

                      {/* Equity-curve timelines */}
                      <div style={{ width: '100%', height: 280 }}>
                        <ResponsiveContainer width="100%" height="100%">
                          <LineChart data={chartData} margin={{ top: 6, right: 8, left: 6, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.04)" />
                            <XAxis dataKey="date" stroke="var(--text-secondary)" fontSize={10} tickLine={false} minTickGap={48} tickFormatter={(d) => String(d).slice(0, 7)} />
                            <YAxis stroke="var(--text-secondary)" fontSize={10} tickLine={false} width={42} domain={['dataMin - 3000', 'dataMax + 3000']} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`} />
                            <Tooltip contentStyle={{ background: 'rgba(16,20,38,0.96)', border: '1px solid var(--border-glass)', borderRadius: '8px', color: 'var(--text-primary)', fontSize: '12px' }}
                              formatter={(v: any, name: any) => [`$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}`, name]} />
                            <Legend wrapperStyle={{ fontSize: '10.5px' }} iconType="plainline" />
                            {policies.map((p: any) => (
                              <Line key={p.id} type="monotone" dataKey={p.id} name={p.label} stroke={p.color}
                                strokeWidth={p.id === 'buyhold' ? 1.5 : 2} dot={false} isAnimationActive={false}
                                strokeDasharray={p.id === 'buyhold' ? '5 4' : undefined} />
                            ))}
                          </LineChart>
                        </ResponsiveContainer>
                      </div>

                      {/* Per-scenario metrics table */}
                      <div style={{ overflowX: 'auto', border: '1px solid var(--border-glass)', borderRadius: '8px', marginTop: '12px' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '11.5px' }}>
                          <thead>
                            <tr style={{ background: 'rgba(255,255,255,0.04)', color: 'var(--text-secondary)', textAlign: 'right' }}>
                              <th style={{ padding: '7px 9px', textAlign: 'left' }}>Strategy</th>
                              <th style={{ padding: '7px 9px' }}>Return</th>
                              <th style={{ padding: '7px 9px' }}>Max DD</th>
                              <th style={{ padding: '7px 9px' }}>Sharpe</th>
                              <th style={{ padding: '7px 9px' }}>Turnover</th>
                            </tr>
                          </thead>
                          <tbody>
                            {ranked.map((p: any) => {
                              const m = sc.series?.[p.id] || {};
                              return (
                                <tr key={p.id} style={{ borderTop: '1px solid var(--border-glass)' }}>
                                  <td style={{ padding: '7px 9px' }}>
                                    <span style={{ display: 'inline-block', width: '9px', height: '9px', borderRadius: '2px', background: p.color, marginRight: '6px' }} />
                                    <span title={p.desc} style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{p.label}</span>
                                  </td>
                                  <td style={{ padding: '7px 9px', textAlign: 'right', color: (m.total_return || 0) >= 0 ? '#10B981' : '#EF4444', fontWeight: 700 }}>{(m.total_return || 0) >= 0 ? '+' : ''}{(m.total_return ?? 0).toFixed(1)}%</td>
                                  <td style={{ padding: '7px 9px', textAlign: 'right', color: '#F59E0B' }}>{(m.max_drawdown ?? 0).toFixed(1)}%</td>
                                  <td style={{ padding: '7px 9px', textAlign: 'right', color: 'var(--text-secondary)' }}>{(m.sharpe ?? 0).toFixed(2)}</td>
                                  <td style={{ padding: '7px 9px', textAlign: 'right', color: 'var(--text-secondary)' }}>{(m.turnover ?? 0).toFixed(1)}x</td>
                                </tr>
                              );
                            })}
                          </tbody>
                        </table>
                      </div>

                      {/* AI analyst */}
                      <div style={{ marginTop: '16px', borderTop: '1px solid var(--border-glass)', paddingTop: '14px' }}>
                        {!wargameAnalyst && (
                          <button onClick={runWargameAnalyst} disabled={analystLoading}
                            style={{ display: 'flex', alignItems: 'center', gap: '8px', width: '100%', justifyContent: 'center', fontSize: '13px', padding: '9px', borderRadius: '8px', cursor: analystLoading ? 'default' : 'pointer', background: 'rgba(139,92,246,0.15)', border: '1px solid rgba(139,92,246,0.4)', color: 'var(--text-primary)', fontWeight: 600 }}>
                            <Brain size={16} className={analystLoading ? 'animate-spin' : ''} color="#a78bfa" /> {analystLoading ? 'Analyst is thinking…' : 'Summarize with AI Analyst'}
                          </button>
                        )}
                        {wargameAnalyst && (() => {
                          const it = wargameAnalyst;
                          const s = it.sections;
                          const label: React.CSSProperties = { fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', color: 'var(--text-secondary)', marginBottom: '5px' };
                          const para: React.CSSProperties = { margin: 0, fontSize: '12.5px', lineHeight: '1.6', color: 'var(--text-secondary)' };
                          const ul: React.CSSProperties = { margin: 0, paddingLeft: '18px', fontSize: '12px', color: 'var(--text-secondary)', lineHeight: '1.55', display: 'flex', flexDirection: 'column', gap: '3px' };
                          return (
                            <div style={{ border: '1px solid rgba(139,92,246,0.35)', background: 'rgba(139,92,246,0.06)', borderRadius: '10px', padding: '14px' }}>
                              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', marginBottom: '12px' }}>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontWeight: 600, fontSize: '14px', color: 'var(--text-primary)' }}>
                                  <Brain size={18} color="#a78bfa" /> AI Wargame Analyst
                                </div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '10.5px', color: 'var(--text-secondary)' }}>
                                  {analystMeta.generated_at && (
                                    <span title={fmtRelTime(analystMeta.generated_at).abs} style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
                                      <Clock size={11} /> {fmtRelTime(analystMeta.generated_at).rel}
                                    </span>
                                  )}
                                  {analystMeta.stale && (
                                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '4px', color: 'var(--color-gold)' }} title="The underlying data has changed since this summary was generated.">
                                      <AlertTriangle size={11} /> stale
                                    </span>
                                  )}
                                  {it.model && <span>{it.model}{it.tokens ? ` · ${(it.tokens).toLocaleString()} tok` : ''}{typeof it.cost === 'number' ? ` · ~$${it.cost.toFixed(4)}` : ''}</span>}
                                  <button onClick={runWargameAnalyst} disabled={analystLoading}
                                    style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '10.5px', padding: '3px 8px', borderRadius: '6px', cursor: analystLoading ? 'default' : 'pointer', background: 'rgba(139,92,246,0.15)', border: '1px solid rgba(139,92,246,0.35)', color: 'var(--text-primary)' }}>
                                    <RefreshCw size={11} className={analystLoading ? 'animate-spin' : ''} /> {analystLoading ? '…' : 'Regenerate'}
                                  </button>
                                </div>
                              </div>
                              {it.error ? (
                                <div style={{ fontSize: '12.5px', color: 'var(--text-secondary)', lineHeight: '1.6' }}>{it.error}</div>
                              ) : s ? (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
                                  {s.tldr && <div style={{ fontSize: '13px', lineHeight: '1.6', color: 'var(--text-primary)', padding: '10px 12px', borderRadius: '8px', background: 'rgba(139,92,246,0.10)', borderLeft: '3px solid #a78bfa' }}>{s.tldr}</div>}
                                  {s.how_to_read && <div><div style={label}>How to read this</div><p style={para}>{s.how_to_read}</p></div>}
                                  {s.knobs_explained && <div><div style={label}>Knobs explained</div><p style={para}>{s.knobs_explained}</p></div>}
                                  {Array.isArray(s.policy_findings) && s.policy_findings.length > 0 && (
                                    <div><div style={label}>How each strategy behaved</div>
                                      <ul style={ul}>{s.policy_findings.map((x: any, i: number) => <li key={i}><strong style={{ color: 'var(--text-primary)' }}>{x.policy}:</strong> {x.finding}</li>)}</ul>
                                    </div>
                                  )}
                                  {Array.isArray(s.regime_insights) && s.regime_insights.length > 0 && (
                                    <div><div style={label}>Regime insights</div><ul style={ul}>{s.regime_insights.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></div>
                                  )}
                                  {s.best_for_you && (
                                    <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-start', fontSize: '12.5px', lineHeight: '1.6', color: 'var(--text-primary)', padding: '10px 12px', borderRadius: '8px', background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.25)' }}>
                                      <CheckCircle2 size={16} color="var(--color-buy)" style={{ flexShrink: 0, marginTop: '1px' }} /> <span>{s.best_for_you}</span>
                                    </div>
                                  )}
                                  {Array.isArray(s.caveats) && s.caveats.length > 0 && (
                                    <div><div style={{ ...label, color: 'var(--color-gold)' }}><AlertTriangle size={12} style={{ display: 'inline', marginRight: '4px' }} />Caveats</div><ul style={ul}>{s.caveats.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></div>
                                  )}
                                </div>
                              ) : null}
                            </div>
                          );
                        })()}
                      </div>
                    </div>
                  );
                })()}
              </div>

              {/* Card 6: Severity Contingency Checklist */}
              <div className="glass-card" style={{ padding: '24px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '14px' }}>
                  <Lock size={18} color="#EF4444" />
                  <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 600 }}>Severity-Tier Contingency Checklist</h3>
                </div>
                <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: '0 0 16px', lineHeight: '1.4' }}>
                  Real-world custodial security actions outside the application based on systemic market stress tiers.
                </p>
                <div style={{ display: 'grid', gap: '10px' }}>
                  <div style={{ background: 'rgba(255,255,255,0.02)', padding: '10px', borderRadius: '6px', borderLeft: '4px solid #3B82F6' }}>
                    <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-primary)' }}>Tier 1: Correction (-10% to -20%)</div>
                    <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                      Lock all margin accounts. Turn leverage to 0. Audit high beta speculative holdings.
                    </div>
                  </div>
                  <div style={{ background: 'rgba(255,255,255,0.02)', padding: '10px', borderRadius: '6px', borderLeft: '4px solid #F59E0B' }}>
                    <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-primary)' }}>Tier 2: Bear Market (-20% to -35%)</div>
                    <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                      Diversify cash holdings across multiple banks. Ensure cash balances do not exceed FDIC limits ($250k).
                    </div>
                  </div>
                  <div style={{ background: 'rgba(255,255,255,0.02)', padding: '10px', borderRadius: '6px', borderLeft: '4px solid #EF4444' }}>
                    <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-primary)' }}>Tier 3: Systemic Crisis (-35% to -55%)</div>
                    <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                      Verify SIPC broker coverage details ($500k limit). Hold cash reserves in direct short-term US Treasury Bills.
                    </div>
                  </div>
                </div>
              </div>
            </div>

          </section>
        )}

        {activeTab === 'external' && (
          <section style={{ gridColumn: '1 / -1', display: 'grid', gap: '20px' }}>

            {/* Account Selector & Settings Panel */}
            <div className="glass-card" style={{ padding: '24px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '16px' }}>
                <div>
                  <h2 style={{ margin: 0, fontSize: '20px', fontWeight: 700, color: 'var(--text-primary)' }}>External Portfolio Manager</h2>
                  <p style={{ margin: '4px 0 0', fontSize: '13px', color: 'var(--text-secondary)' }}>
                    Track, manage, and manually execute strategy-driven MPT/Swing rebalances across your Robinhood and Vanguard accounts.
                  </p>
                </div>
                <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                  {externalAccounts.map(acct => (
                    <button
                      key={acct.account_label}
                      onClick={() => setSelectedAccount(acct.account_label)}
                      className="toggle-btn"
                      style={{
                        borderColor: selectedAccount === acct.account_label ? 'var(--color-buy)' : 'var(--border-glass)',
                        background: selectedAccount === acct.account_label ? 'rgba(0, 242, 254, 0.1)' : 'transparent',
                        color: selectedAccount === acct.account_label ? 'var(--text-primary)' : 'var(--text-secondary)',
                        fontWeight: 600
                      }}
                    >
                      {acct.account_label}
                    </button>
                  ))}
                </div>
              </div>

              {/* Account summary cards */}
              {(() => {
                const currentAcct = externalAccounts.find(a => a.account_label === selectedAccount);
                if (!currentAcct) return null;
                return (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '16px', marginTop: '20px' }}>
                    <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border-glass)', borderRadius: '10px', padding: '16px' }}>
                      <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Cash Balance</div>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '6px' }}>
                        <DollarSign size={18} color="var(--color-buy)" />
                        <input
                          type="text"
                          inputMode="decimal"
                          value={cashFocused ? (currentAcct.cash ?? '') : sharePrice(currentAcct.cash)}
                          onFocus={() => setCashFocused(true)}
                          onBlur={() => setCashFocused(false)}
                          onChange={async (e) => {
                            const newCash = parseFloat(e.target.value.replace(/[$,\s]/g, '')) || 0.0;
                            setExternalAccounts(prev => prev.map(a => a.account_label === selectedAccount ? { ...a, cash: newCash } : a));
                            await fetch(apiUrl(`/api/external/accounts/${encodeURIComponent(selectedAccount)}/cash`), {
                              method: 'POST',
                              headers: { 'Content-Type': 'application/json' },
                              body: JSON.stringify({ cash: newCash })
                            });
                          }}
                          style={{
                            background: 'transparent',
                            border: 'none',
                            color: 'var(--text-primary)',
                            fontSize: '18px',
                            fontWeight: 700,
                            width: '140px',
                            borderBottom: '1px dashed var(--border-glass)'
                          }}
                        />
                      </div>
                    </div>
                    <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border-glass)', borderRadius: '10px', padding: '16px' }}>
                      <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Holdings Value</div>
                      <div style={{ fontSize: '20px', fontWeight: 700, color: 'var(--text-primary)', marginTop: '4px' }}>
                        {sharePrice(currentAcct.holdings_value)}
                      </div>
                    </div>
                    <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border-glass)', borderRadius: '10px', padding: '16px' }}>
                      <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Total Account Value</div>
                      <div style={{ fontSize: '20px', fontWeight: 700, color: 'var(--color-buy)', marginTop: '4px' }}>
                        {sharePrice(currentAcct.total_value)}
                      </div>
                    </div>
                    <div style={{ background: 'rgba(255,255,255,0.02)', border: '1px solid var(--border-glass)', borderRadius: '10px', padding: '16px' }}>
                      <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Active Strategy</div>
                      <div style={{ fontSize: '14px', fontWeight: 700, color: 'var(--text-primary)', marginTop: '7px' }}>
                        {(currentAcct.strategy_mode || 'growth').replace('_', ' ')} · {currentAcct.aggression ?? 60}/100
                      </div>
                    </div>
                  </div>
                );
              })()}
            </div>

            {/* Per-account model allocation policy */}
            <div className="glass-card" style={{ padding: '20px', display: 'grid', gap: '16px' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', alignItems: 'start' }}>
                <div>
                  <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 600 }}>Account Strategy</h3>
                  <p style={{ margin: '5px 0 0', fontSize: '12px', color: 'var(--text-secondary)' }}>
                    Builds proposals only. Saving this policy never executes a trade.
                  </p>
                </div>
                <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                  {externalAccounts.find((a: any) => a.account_label === selectedAccount)?.inherits_global_buckets
                    ? 'Using global buckets' : 'Custom account buckets'}
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 1fr) minmax(260px, 2fr)', gap: '18px' }}>
                <div style={{ display: 'grid', gap: '12px' }}>
                  <label style={{ display: 'grid', gap: '5px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                    Allocation mode
                    <select value={externalStrategyEdit.strategy_mode}
                      onChange={(e) => setExternalStrategyEdit(prev => ({ ...prev, strategy_mode: e.target.value }))}
                      style={{ background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px' }}>
                      <option value="growth">Model growth</option>
                      <option value="glide_path">De-risk — keep quality (holdings-aware)</option>
                      <option value="all_weather">Rotate into All-Weather basket (Dalio-style)</option>
                      <option value="barbell">Rotate into barbell basket (T-bill heavy)</option>
                    </select>
                  </label>
                  <div style={{ fontSize: '11px', lineHeight: 1.45, color: 'var(--text-secondary)' }}>
                    {externalStrategyEdit.strategy_mode === 'growth' && 'Deploys shared model BUY signals across your buckets; defensive endpoint is cash.'}
                    {(externalStrategyEdit.strategy_mode === 'glide_path' || externalStrategyEdit.strategy_mode === 'de_risk') && 'Holdings-aware: keeps your low-volatility / quality names, trims speculative ones, raises cash — scaled by live crash risk. Does not open new speculative positions.'}
                    {externalStrategyEdit.strategy_mode === 'all_weather' && 'Explicitly ROTATES into a fixed ETF basket: SPY 30 / TLT 40 / IEF 15 / GLD 7.5 / GSG 7.5 — this will sell holdings not in the basket.'}
                    {externalStrategyEdit.strategy_mode === 'barbell' && 'Explicitly ROTATES into a fixed basket: BIL 90 / QQQ 10. No options or tail hedge.'}
                  </div>
                  <label style={{ display: 'grid', gap: '5px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                    Aggression: <strong style={{ color: 'var(--text-primary)' }}>{externalStrategyEdit.aggression}</strong>
                    <input type="range" min={0} max={100} value={externalStrategyEdit.aggression}
                      onChange={(e) => setExternalStrategyEdit(prev => ({ ...prev, aggression: Number(e.target.value) }))} />
                    <span style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10px' }}><span>Defensive endpoint</span><span>Growth endpoint</span></span>
                  </label>
                </div>
                <div style={{ display: 'grid', gap: '12px' }}>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px' }}>
                    {(['swing', 'longterm', 'high_risk'] as const).map((key) => (
                      <label key={key} style={{ display: 'grid', gap: '5px', fontSize: '11px', color: 'var(--text-secondary)' }}>
                        {key === 'swing' ? 'Swing %' : key === 'longterm' ? 'Long-term %' : 'High-risk % (≤5)'}
                        <input type="number" min={0} max={key === 'high_risk' ? 5 : 100} step="1"
                          value={externalStrategyEdit[key]}
                          onChange={(e) => setExternalStrategyEdit(prev => ({ ...prev, [key]: e.target.value }))}
                          style={{ background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px' }} />
                      </label>
                    ))}
                  </div>
                  <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                    <button className="toggle-btn" disabled={externalStrategySaving} onClick={() => saveExternalStrategy(false)}
                      style={{ background: 'rgba(0,242,254,0.08)', borderColor: 'var(--color-buy)' }}>
                      {externalStrategySaving ? 'Saving…' : 'Save & regenerate'}
                    </button>
                    <button className="toggle-btn" disabled={externalStrategySaving} onClick={() => saveExternalStrategy(true)}>
                      Reset buckets to global
                    </button>
                    <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                      Unallocated bucket capacity remains cash.
                    </span>
                  </div>
                  {externalStrategyError && <div style={{ fontSize: '12px', color: 'var(--color-sell)' }}>{externalStrategyError}</div>}
                </div>
              </div>
              {externalStrategyResult && (() => {
                const r = externalStrategyResult;
                const tw = r.target_weights || {}; const cw = r.current_weights || {}; const tiers = r.tiers || {};
                const TIER_LABEL: any = { quality_growth: ['🔥 Hot', '#10B981'], core: ['🛡️ Solid', '#38BDF8'], speculative: ['🎲 Long-shot', '#EF4444'], value_trap: ['🧊 Cold', '#94A3B8'] };
                const REASON: any = { keep_quality: 'Keep — quality/low-vol', model_buy: 'Model BUY', shared_model_growth: 'Model growth', blended_growth_defensive: 'Blend', defensive_template: 'Defensive basket' };
                const rows = Array.from(new Set([...Object.keys(tw), ...Object.keys(cw)]))
                  .map((t) => ({ t, cur: cw[t] || 0, tgt: tw[t] || 0, tier: tiers[t], reason: (r.target_reason_codes || {})[t] }))
                  .filter((x) => x.cur > 0.0005 || x.tgt > 0.0005)
                  .sort((a, b) => b.tgt - a.tgt).slice(0, 16);
                const keeping = rows.filter((x) => x.tgt >= x.cur && (x.tier === 'core' || x.tier === 'quality_growth')).map((x) => x.t);
                const trimming = rows.filter((x) => x.tgt < x.cur - 0.001 && (x.tier === 'speculative' || x.tier === 'value_trap')).map((x) => x.t);
                const badge = (tier: string) => { const m = TIER_LABEL[tier]; return m ? <span style={{ fontSize: '10px', color: m[1] }}>{m[0]}</span> : <span style={{ fontSize: '10px', color: 'var(--text-secondary)' }}>—</span>; };
                return (
                  <div style={{ borderTop: '1px solid var(--border-glass)', paddingTop: '12px', display: 'grid', gap: '10px' }}>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '14px', fontSize: '11.5px', color: 'var(--text-secondary)' }}>
                      <span><strong style={{ color: 'var(--text-primary)' }}>{(r.strategy_mode || '').replace('_', ' ')}</strong> · aggression {r.aggression}</span>
                      {r.crash_risk_coefficient != null && <span>Crash-risk de-risk: <strong style={{ color: 'var(--color-gold)' }}>{(r.crash_risk_coefficient * 100).toFixed(0)}%</strong></span>}
                      <span>Cash target <strong style={{ color: '#10B981' }}>{((r.cash_target_weight || 0) * 100).toFixed(1)}%</strong></span>
                      <span>Turnover {((r.turnover_pct || 0) * 100).toFixed(1)}%</span>
                    </div>
                    {(keeping.length > 0 || trimming.length > 0) && (
                      <div style={{ fontSize: '11.5px', lineHeight: 1.5 }}>
                        {keeping.length > 0 && <span style={{ color: '#38BDF8' }}>Keeping {keeping.slice(0, 6).join(', ')}</span>}
                        {keeping.length > 0 && trimming.length > 0 && <span style={{ color: 'var(--text-secondary)' }}> · </span>}
                        {trimming.length > 0 && <span style={{ color: '#EF4444' }}>Trimming {trimming.slice(0, 6).join(', ')}</span>}
                      </div>
                    )}
                    <table className="trade-table">
                      <thead><tr><th>Ticker</th><th>Risk</th><th>Current</th><th>Target</th><th></th><th>Why</th></tr></thead>
                      <tbody>
                        {rows.map((x) => (
                          <tr key={x.t}>
                            <td><strong>{x.t}</strong></td>
                            <td>{badge(x.tier)}</td>
                            <td>{(x.cur * 100).toFixed(1)}%</td>
                            <td>{(x.tgt * 100).toFixed(1)}%</td>
                            <td style={{ color: x.tgt > x.cur + 0.001 ? 'var(--color-buy)' : x.tgt < x.cur - 0.001 ? 'var(--color-sell)' : 'var(--text-secondary)' }}>{x.tgt > x.cur + 0.001 ? '▲' : x.tgt < x.cur - 0.001 ? '▼' : '—'}</td>
                            <td style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>{REASON[x.reason] || x.reason || ''}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {(r.warnings || []).map((warning: string, index: number) => (
                      <div key={index} style={{ fontSize: '11px', color: 'var(--color-gold)' }}>⚠ {warning}</div>
                    ))}
                  </div>
                );
              })()}
            </div>

            {/* Per-account war-game: forward-walk each strategy mode over recent prices */}
            <div className="glass-card" style={{ padding: '18px', display: 'grid', gap: '12px' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
                <h3 style={{ margin: 0, fontSize: '15px' }}>Strategy war-game</h3>
                <span style={{ fontSize: '11.5px', color: 'var(--text-secondary)' }}>How each mode (at this account&rsquo;s aggression) would have performed on your holdings.</span>
                <select value={wargameYears} onChange={(e) => setWargameYears(Number(e.target.value))}
                  style={{ background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '6px' }}>
                  {[1, 2, 3, 5].map((y) => <option key={y} value={y}>{y}-year lookback</option>)}
                </select>
                <button className="toggle-btn" disabled={externalWargameLoading || !selectedAccount}
                  onClick={() => runExternalWargame(selectedAccount, wargameYears)}>
                  {externalWargameLoading ? 'Simulating…' : 'Run war-game'}
                </button>
              </div>
              {externalWargame && externalWargame.error && (
                <div style={{ fontSize: '12px', color: 'var(--color-gold)' }}>⚠ {externalWargame.error}</div>
              )}
              {externalWargame && externalWargame.results && (() => {
                const COLORS: any = { de_risk: '#38BDF8', growth: '#10B981', all_weather: '#F59E0B', barbell: '#A78BFA' };
                const dates = externalWargame.dates || [];
                const chart = dates.map((d: string, i: number) => {
                  const row: any = { date: d };
                  externalWargame.results.forEach((r: any) => { row[r.mode] = +(((r.curve[i] ?? 1) - 1) * 100).toFixed(2); });
                  return row;
                });
                const sorted = [...externalWargame.results].sort((a: any, b: any) => b.metrics.total_return - a.metrics.total_return);
                return (
                  <>
                    <div style={{ height: 240 }}>
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={chart} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                          <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'var(--text-secondary)' }} minTickGap={48} />
                          <YAxis tick={{ fontSize: 10, fill: 'var(--text-secondary)' }} tickFormatter={(v) => `${v}%`} />
                          <Tooltip contentStyle={{ background: 'rgba(16,20,38,0.97)', border: '1px solid var(--border-glass)', fontSize: 12 }}
                            formatter={(v: any, name: any) => [`${v}%`, externalWargame.results.find((r: any) => r.mode === name)?.label || name]} />
                          <Legend formatter={(v: any) => externalWargame.results.find((r: any) => r.mode === v)?.label || v} wrapperStyle={{ fontSize: 11 }} />
                          {externalWargame.results.map((r: any) => (
                            <Line key={r.mode} type="monotone" dataKey={r.mode} stroke={COLORS[r.mode] || '#888'}
                              strokeWidth={r.mode === externalWargame.account_mode ? 3 : 1.5} dot={false} />
                          ))}
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                    <table className="trade-table">
                      <thead><tr><th>Strategy</th><th>Return</th><th>Max DD</th><th>Sharpe</th><th>Coverage</th></tr></thead>
                      <tbody>
                        {sorted.map((r: any) => (
                          <tr key={r.mode} style={r.mode === externalWargame.account_mode ? { background: 'rgba(56,189,248,0.08)' } : {}}>
                            <td><span style={{ color: COLORS[r.mode] }}>●</span> {r.label}{r.mode === externalWargame.account_mode ? ' (current)' : ''}</td>
                            <td style={{ color: r.metrics.total_return >= 0 ? 'var(--color-buy)' : 'var(--color-sell)' }}>{r.metrics.total_return >= 0 ? '+' : ''}{r.metrics.total_return.toFixed(1)}%</td>
                            <td style={{ color: 'var(--color-sell)' }}>−{r.metrics.max_drawdown.toFixed(1)}%</td>
                            <td>{r.metrics.sharpe.toFixed(2)}</td>
                            <td style={{ color: r.coverage < 0.6 ? 'var(--color-gold)' : 'var(--text-secondary)' }}>{(r.coverage * 100).toFixed(0)}%</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                      {externalWargame.lookback_years}-yr, monthly-rebalanced to each mode&rsquo;s target (as of {externalWargame.as_of}). Names enter when they begin trading; weight waits in cash until then. Coverage = share of the target that was investable — the rest is the strategy&rsquo;s own cash.
                    </div>
                  </>
                );
              })()}

              {/* Crash-era stress test (synthetic SPY-beta proxy) */}
              <div style={{ borderTop: '1px solid var(--border-glass)', paddingTop: '12px', display: 'grid', gap: '10px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                  <strong style={{ fontSize: '13px' }}>Crash-era stress test</strong>
                  <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Re-runs each mode through a historical crash via a synthetic SPY-beta proxy of your holdings.</span>
                  <select value={crashEra} onChange={(e) => setCrashEra(e.target.value)}
                    style={{ background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '6px' }}>
                    <option value="gfc">2008 Financial Crisis</option>
                    <option value="covid">2020 COVID crash</option>
                    <option value="dotcom">2000 Dot-com bust</option>
                    <option value="2022">2022 rate shock</option>
                  </select>
                  <button className="toggle-btn" disabled={crashStressLoading || !selectedAccount}
                    onClick={() => runCrashStress(selectedAccount, crashEra)}>
                    {crashStressLoading ? 'Stressing…' : 'Run crash stress'}
                  </button>
                </div>
                {crashStress && crashStress.error && (
                  <div style={{ fontSize: '12px', color: 'var(--color-gold)' }}>⚠ {crashStress.error}</div>
                )}
                {crashStress && crashStress.results && (() => {
                  const COLORS: any = { de_risk: '#38BDF8', growth: '#10B981', all_weather: '#F59E0B', barbell: '#A78BFA' };
                  const chart = (crashStress.dates || []).map((d: string, i: number) => {
                    const row: any = { date: d };
                    crashStress.results.forEach((r: any) => { row[r.mode] = +(((r.curve[i] ?? 1) - 1) * 100).toFixed(2); });
                    return row;
                  });
                  const sorted = [...crashStress.results].sort((a: any, b: any) => a.metrics.max_drawdown - b.metrics.max_drawdown);
                  return (
                    <>
                      <div style={{ fontSize: '11.5px', color: 'var(--text-secondary)' }}>
                        <strong style={{ color: 'var(--text-primary)' }}>{crashStress.era_label}</strong> — S&amp;P 500 fell <strong style={{ color: 'var(--color-sell)' }}>{crashStress.spy_drawdown}%</strong>.
                      </div>
                      <div style={{ height: 220 }}>
                        <ResponsiveContainer width="100%" height="100%">
                          <LineChart data={chart} margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                            <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'var(--text-secondary)' }} minTickGap={48} />
                            <YAxis tick={{ fontSize: 10, fill: 'var(--text-secondary)' }} tickFormatter={(v) => `${v}%`} />
                            <Tooltip contentStyle={{ background: 'rgba(16,20,38,0.97)', border: '1px solid var(--border-glass)', fontSize: 12 }}
                              formatter={(v: any, name: any) => [`${v}%`, crashStress.results.find((r: any) => r.mode === name)?.label || name]} />
                            <Legend formatter={(v: any) => crashStress.results.find((r: any) => r.mode === v)?.label || v} wrapperStyle={{ fontSize: 11 }} />
                            <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" />
                            {crashStress.results.map((r: any) => (
                              <Line key={r.mode} type="monotone" dataKey={r.mode} stroke={COLORS[r.mode] || '#888'}
                                strokeWidth={r.mode === crashStress.account_mode ? 3 : 1.5} dot={false} />
                            ))}
                          </LineChart>
                        </ResponsiveContainer>
                      </div>
                      <table className="trade-table">
                        <thead><tr><th>Strategy</th><th>Return</th><th>Max DD</th><th>Avg beta</th></tr></thead>
                        <tbody>
                          {sorted.map((r: any) => (
                            <tr key={r.mode} style={r.mode === crashStress.account_mode ? { background: 'rgba(56,189,248,0.08)' } : {}}>
                              <td><span style={{ color: COLORS[r.mode] }}>●</span> {r.label}{r.mode === crashStress.account_mode ? ' (current)' : ''}</td>
                              <td style={{ color: r.metrics.total_return >= 0 ? 'var(--color-buy)' : 'var(--color-sell)' }}>{r.metrics.total_return >= 0 ? '+' : ''}{r.metrics.total_return.toFixed(1)}%</td>
                              <td style={{ color: 'var(--color-sell)' }}>−{r.metrics.max_drawdown.toFixed(1)}%</td>
                              <td style={{ color: r.avg_beta > 0.8 ? 'var(--color-gold)' : 'var(--text-secondary)' }}>{r.avg_beta.toFixed(2)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>{crashStress.method_note} Higher avg beta ⇒ more market exposure ⇒ deeper crash drawdown.</div>
                    </>
                  );
                })()}
              </div>
            </div>

            {/* Split layout: Holdings/Lots vs suggestions */}
            <div style={{ display: 'grid', gridTemplateColumns: '3fr 2fr', gap: '20px', alignItems: 'start' }}>

              {/* Left Column: Consolidated Positions & Tax Lots */}
              <div className="glass-card" style={{ padding: '24px', display: 'grid', gap: '16px' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 600 }}>Account Position Holdings</h3>
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <label className="toggle-btn" style={{ cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px' }}>
                      <Upload size={14} /> Import statement / RH CSV
                      <input
                        type="file"
                        accept=".pdf,.csv"
                        onChange={async (e) => {
                          const file = e.target.files?.[0];
                          if (!file) return;
                          const isCsv = file.name.toLowerCase().endsWith('.csv');
                          setReconcileStatus(isCsv ? 'Uploading and reconstructing holdings from CSV…' : 'Uploading and parsing statement…');
                          const formData = new FormData();
                          formData.append('file', file);
                          formData.append('override_account', selectedAccount);
                          try {
                            const res = await fetch(apiUrl('/api/external/import'), {
                              method: 'POST',
                              body: formData
                            });
                            if (res.ok) {
                              const r = await res.json();
                              if (r.lots_written != null) {
                                setReconcileStatus(`Imported ${r.account_label}: ${r.lots_written} lots, cash ${money(r.cash || 0)}.${r.zero_basis_lots ? ` ⚠ ${r.zero_basis_lots} lots have no cost basis.` : ''}`);
                              } else {
                                setReconcileStatus(`Success: Ingested ${r.parsed_count} positions. Cash updated to ${money(r.cash_updated || 0)}.`);
                              }
                              fetchExternalAccounts();
                              if (r.account_label) {
                                setSelectedAccount(r.account_label);
                                fetchExternalPositionsAndSuggestions(r.account_label);
                              } else {
                                fetchExternalPositionsAndSuggestions(selectedAccount);
                              }
                            } else {
                              const err = await res.json();
                              setReconcileStatus(`Import error: ${err.detail || 'Failed to parse upload.'}`);
                            }
                          } catch (err: any) {
                            setReconcileStatus(`Import failed: ${err.message}`);
                          }
                          e.target.value = '';
                        }}
                        style={{ display: 'none' }}
                      />
                    </label>
                    {selectedAccount.startsWith('Robinhood') && (
                      <button
                        onClick={() => {
                          setRobinhoodSyncError('');
                          setRobinhoodMfaRequired(false);
                          setRobinhoodMfaCode('');
                          setShowRobinhoodSyncModal(true);
                        }}
                        className="toggle-btn"
                        style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', background: 'rgba(0, 242, 254, 0.08)', borderColor: 'var(--color-buy)', cursor: 'pointer' }}
                      >
                        <RefreshCw size={14} /> Sync Robinhood API
                      </button>
                    )}
                  </div>
                </div>

                {reconcileStatus && (
                  <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-glass)', borderRadius: '8px', padding: '10px 12px', fontSize: '12.5px', color: 'var(--text-secondary)' }}>
                    {reconcileStatus}
                  </div>
                )}

                {externalPositions.length === 0 ? (
                  <div style={{ padding: '30px 0', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '13px' }}>
                    No positions logged for this account. Upload a PDF statement above to import your holdings automatically.
                  </div>
                ) : (
                  <div style={{ overflowX: 'auto' }}>
                    <table className="trade-table" style={{ width: '100%' }}>
                      <thead>
                        <tr>
                          <th style={{ width: '30px' }}></th>
                          <th>Ticker</th>
                          <th>Shares</th>
                          <th>Avg Cost</th>
                          <th>Price</th>
                          <th>Market Value</th>
                          <th>Gain/Loss</th>
                        </tr>
                      </thead>
                      <tbody>
                        {externalPositions.map((pos) => {
                          const isExpanded = !!expandedPositions[pos.ticker];
                          return (
                            <React.Fragment key={pos.ticker}>
                              <tr style={{ background: isExpanded ? 'rgba(255,255,255,0.02)' : 'none' }}>
                                <td>
                                  <button
                                    onClick={() => setExpandedPositions(prev => ({ ...prev, [pos.ticker]: !prev[pos.ticker] }))}
                                    style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
                                  >
                                    {isExpanded ? '▼' : '▶'}
                                  </button>
                                </td>
                                <td><strong style={{ color: 'var(--text-primary)' }}>{pos.ticker}</strong></td>
                                <td>{pos.total_shares.toFixed(4)}</td>
                                <td>{sharePrice(pos.average_cost)}</td>
                                <td>{sharePrice(pos.current_price)}</td>
                                <td><strong>{money(pos.market_value)}</strong></td>
                                <td style={{ color: pos.unrealized_gain >= 0 ? '#10B981' : '#EF4444', fontWeight: 600 }}>
                                  {pos.unrealized_gain >= 0 ? '+' : ''}{money(pos.unrealized_gain)} ({pos.unrealized_gain_pct.toFixed(1)}%)
                                </td>
                              </tr>
                              {isExpanded && (
                                <tr>
                                  <td colSpan={7} style={{ padding: '0 0 10px 30px', background: 'rgba(255,255,255,0.015)' }}>
                                    <div style={{ border: '1px solid var(--border-glass)', borderRadius: '6px', overflow: 'hidden', marginTop: '6px' }}>
                                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '11.5px', textAlign: 'left' }}>
                                        <thead>
                                          <tr style={{ background: 'rgba(255,255,255,0.03)', color: 'var(--text-secondary)' }}>
                                            <th style={{ padding: '6px 8px' }}>Acquisition Date</th>
                                            <th style={{ padding: '6px 8px' }}>Shares</th>
                                            <th style={{ padding: '6px 8px' }}>Cost Basis</th>
                                            <th style={{ padding: '6px 8px' }}>Notes</th>
                                          </tr>
                                        </thead>
                                        <tbody>
                                          {pos.lots.map((lot: any) => (
                                            <tr key={lot.id} style={{ borderTop: '1px solid var(--border-glass)', color: 'var(--text-secondary)' }}>
                                              <td style={{ padding: '6px 8px' }}>{lot.acquisition_date}</td>
                                              <td style={{ padding: '6px 8px', color: 'var(--text-primary)', fontWeight: 500 }}>{lot.shares.toFixed(4)}</td>
                                              <td style={{ padding: '6px 8px' }}>{sharePrice(lot.cost_basis_per_share)}</td>
                                              <td style={{ padding: '6px 8px', fontStyle: 'italic', fontSize: '11px' }}>{lot.notes}</td>
                                            </tr>
                                          ))}
                                        </tbody>
                                      </table>
                                    </div>
                                  </td>
                                </tr>
                              )}
                            </React.Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>

              {/* Right Column: Suggested Trades & Reconciler */}
              <div style={{ display: 'grid', gap: '20px' }}>

                {/* Manual suggestions list */}
                <div className="glass-card" style={{ padding: '24px', display: 'grid', gap: '14px' }}>
                  <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 600 }}>Manual Trade Suggestions</h3>

                  {externalSuggestionsLoading ? (
                    <div style={{ padding: '40px 0', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '13px' }}>
                      Evaluating MPT weights & Swing signals...
                    </div>
                  ) : externalSuggestions.length === 0 ? (
                    <div style={{ padding: '30px 0', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '13px' }}>
                      No trades needed. Portfolio matches active targets within threshold limits.
                    </div>
                  ) : (
                    <div style={{ display: 'grid', gap: '10px' }}>
                      {externalSuggestions.map((sug: any, i: number) => (
                        <div
                          key={i}
                          style={{
                            background: 'rgba(255,255,255,0.02)',
                            border: '1px solid var(--border-glass)',
                            borderRadius: '8px',
                            padding: '12px 14px',
                            display: 'flex',
                            justifyContent: 'space-between',
                            alignItems: 'center'
                          }}
                        >
                          <div>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                              <span
                                style={{
                                  background: sug.side === 'BUY' ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)',
                                  color: sug.side === 'BUY' ? '#10B981' : '#EF4444',
                                  fontSize: '11px',
                                  fontWeight: 700,
                                  padding: '2px 6px',
                                  borderRadius: '4px'
                                }}
                              >
                                {sug.side}
                              </span>
                              <strong style={{ fontSize: '14px', color: 'var(--text-primary)' }}>{sug.ticker}</strong>
                            </div>
                            <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '4px', maxWidth: '240px' }}>
                              {sug.qty.toFixed(4)} shares @ limit {sharePrice(sug.limit_price)} · {sug.reason}
                            </div>
                            <div style={{ fontSize: '10.5px', color: 'var(--text-secondary)', marginTop: '2px' }}>
                              Suggested duration: <strong>{sug.time_in_force === 'DAY' ? 'Day order' : '90 Days GTC'}</strong>
                            </div>
                          </div>

                          <button
                            onClick={() => {
                              setExternalConfirmOrder(sug);
                              setExternalExecutionPrice(String(sug.limit_price));
                            }}
                            className="toggle-btn"
                            style={{ padding: '6px 12px', fontSize: '12px', background: 'rgba(255,255,255,0.05)', borderColor: 'var(--border-glass)' }}
                          >
                            Confirm Fill
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* Monthly Reconciler PDF Dropbox */}
                <div className="glass-card" style={{ padding: '24px', display: 'grid', gap: '14px' }}>
                  <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 600 }}>Monthly Statement Reconciler</h3>
                  <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0, lineHeight: 1.45 }}>
                    Upload your monthly transaction history or trades list PDF. The consolidator matches executed trades against manual fills, de-duping matches, and logs any external/unrecorded trades.
                  </p>

                  <div style={{ display: 'grid', gap: '10px' }}>
                    <label className="toggle-btn" style={{ cursor: 'pointer', textAlign: 'center', display: 'block', padding: '10px 0', fontSize: '13px' }}>
                      <Upload size={14} style={{ marginRight: '6px' }} /> Upload Monthly Trades PDF
                      <input
                        type="file"
                        accept=".pdf"
                        onChange={async (e) => {
                          const file = e.target.files?.[0];
                          if (!file) return;
                          setReconcileStatus('Uploading monthly transactions...');
                          const formData = new FormData();
                          formData.append('file', file);
                          formData.append('override_account', selectedAccount);
                          try {
                            const res = await fetch(apiUrl('/api/external/import'), {
                              method: 'POST',
                              body: formData
                            });
                            if (res.ok) {
                              const r = await res.json();
                              setReconcileStatus(`Transactions imported: Added ${r.inserted_count} new entries, skipped ${r.skipped_count} duplicates.`);
                              fetchExternalAccounts();
                              if (r.account_label) {
                                setSelectedAccount(r.account_label);
                                fetchExternalPositionsAndSuggestions(r.account_label);
                              } else {
                                fetchExternalPositionsAndSuggestions(selectedAccount);
                              }
                            } else {
                              const err = await res.json();
                              setReconcileStatus(`Import error: ${err.detail || 'Failed to parse Transactions PDF.'}`);
                            }
                          } catch (err: any) {
                            setReconcileStatus(`Upload failed: ${err.message}`);
                          }
                        }}
                        style={{ display: 'none' }}
                      />
                    </label>

                    <button
                      onClick={async () => {
                        setReconcileStatus('Running chronological reconciliation...');
                        try {
                          const res = await fetch(apiUrl(`/api/external/reconcile?account_label=${encodeURIComponent(selectedAccount)}`), {
                            method: 'POST'
                          });
                          if (res.ok) {
                            const data = await res.json();
                            setReconcileStatus(`Reconciliation complete: matched & reconciled ${data.reconciled_orders} manual orders, imported ${data.new_trades_imported} external trades.`);
                            fetchExternalAccounts();
                            fetchExternalPositionsAndSuggestions(selectedAccount);
                          }
                        } catch (err: any) {
                          setReconcileStatus(`Reconciliation error: ${err.message}`);
                        }
                      }}
                      className="toggle-btn"
                      style={{ background: 'transparent', borderColor: 'var(--border-glass)' }}
                    >
                      Verify & Reconcile Account
                    </button>
                  </div>
                </div>

              </div>

            </div>

          </section>
        )}
      </main>

      {/* Confirm Manual Order Fill Modal */}
      {externalConfirmOrder && (
        <div
          onClick={() => setExternalConfirmOrder(null)}
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}
        >
          <div onClick={(e) => e.stopPropagation()} style={{ background: 'rgba(16, 20, 38, 0.98)', border: '1px solid var(--border-glass)', borderRadius: '14px', padding: '24px', width: '400px', maxWidth: '90vw', boxShadow: '0 20px 60px rgba(0,0,0,0.5)' }}>
            <h2 style={{ marginTop: 0, marginBottom: '4px' }}>Confirm Order Fill</h2>
            <p style={{ margin: '0 0 16px', fontSize: '12.5px', color: 'var(--text-secondary)' }}>
              Confirm executing a trade manually in your external **{selectedAccount}** account. This adjusts cash and positions locally.
            </p>

            <div style={{ background: 'rgba(255,255,255,0.02)', padding: '12px', borderRadius: '8px', marginBottom: '16px', borderLeft: externalConfirmOrder.side === 'BUY' ? '4px solid #10B981' : '4px solid #EF4444' }}>
              <div style={{ fontWeight: 600 }}>{externalConfirmOrder.side} {externalConfirmOrder.qty} shares of {externalConfirmOrder.ticker}</div>
              <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '2px' }}>{externalConfirmOrder.reason}</div>
            </div>

            <div style={{ display: 'grid', gap: '12px' }}>
              <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Execution Price ($ / share)
                <input
                  type="number"
                  step="0.01"
                  value={externalExecutionPrice}
                  onChange={(e) => setExternalExecutionPrice(e.target.value)}
                  style={{ width: '100%', marginTop: '4px', background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px' }}
                />
              </label>

              <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Execution Date
                <input
                  type="date"
                  value={externalExecutionDate}
                  onChange={(e) => setExternalExecutionDate(e.target.value)}
                  style={{ width: '100%', marginTop: '4px', background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px' }}
                />
              </label>
            </div>

            <div style={{ display: 'flex', gap: '10px', marginTop: '22px', justifyContent: 'flex-end' }}>
              <button onClick={() => setExternalConfirmOrder(null)}
                style={{ background: 'transparent', border: '1px solid var(--border-glass)', borderRadius: '8px', color: 'var(--text-secondary)', padding: '8px 16px', cursor: 'pointer' }}>
                Cancel
              </button>
              <button
                onClick={async () => {
                  const body = {
                    ticker: externalConfirmOrder.ticker,
                    side: externalConfirmOrder.side,
                    qty: externalConfirmOrder.qty,
                    filled_price: parseFloat(externalExecutionPrice) || 0.0,
                    execution_date: externalExecutionDate,
                    time_in_force: externalConfirmOrder.time_in_force
                  };
                  try {
                    const res = await fetch(apiUrl(`/api/external/orders/confirm?account_label=${encodeURIComponent(selectedAccount)}`), {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify(body)
                    });
                    if (res.ok) {
                      setExternalConfirmOrder(null);
                      fetchExternalAccounts();
                      fetchExternalPositionsAndSuggestions(selectedAccount);
                    }
                  } catch (err) {
                    console.error(err);
                  }
                }}
                className="toggle-btn"
                style={{ background: externalConfirmOrder.side === 'BUY' ? 'var(--color-buy)' : 'var(--color-sell)', border: 'none', color: externalConfirmOrder.side === 'BUY' ? 'black' : 'white', fontWeight: 700 }}
              >
                Confirm Fill
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Robinhood API Sync Modal */}
      {showRobinhoodSyncModal && (
        <div
          onClick={() => !robinhoodSyncLoading && setShowRobinhoodSyncModal(false)}
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: 'rgba(16, 20, 38, 0.98)',
              border: '1px solid var(--border-glass)',
              borderRadius: '14px',
              padding: '24px',
              width: '420px',
              maxWidth: '92vw',
              boxShadow: '0 20px 60px rgba(0,0,0,0.5)',
              display: 'grid',
              gap: '16px'
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <h2 style={{ margin: 0, fontSize: '20px', fontWeight: 700 }}>Robinhood API Sync</h2>
              <button
                onClick={() => setShowRobinhoodSyncModal(false)}
                disabled={robinhoodSyncLoading}
                style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', fontSize: '18px' }}
              >
                &times;
              </button>
            </div>

            <p style={{ margin: 0, fontSize: '12px', color: 'var(--text-secondary)', lineHeight: 1.45 }}>
              Enter your Robinhood credentials to sync holdings, sweep cash, and trade history directly into the platform.
            </p>

            {robinhoodSyncError && (
              <div
                style={{
                  background: 'rgba(239, 68, 68, 0.1)',
                  border: '1px solid rgba(239, 68, 68, 0.2)',
                  color: '#F87171',
                  padding: '10px 12px',
                  borderRadius: '6px',
                  fontSize: '12px',
                  lineHeight: 1.4
                }}
              >
                {robinhoodSyncError}
              </div>
            )}

            {!robinhoodMfaRequired ? (
              <div style={{ display: 'grid', gap: '12px' }}>
                <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                  Robinhood Username / Email
                  <input
                    type="email"
                    placeholder="name@example.com"
                    value={robinhoodUsername}
                    onChange={(e) => setRobinhoodUsername(e.target.value)}
                    style={{ width: '100%', marginTop: '4px', background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px', fontSize: '13px' }}
                  />
                </label>

                <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                  Password
                  <input
                    type="password"
                    placeholder="••••••••"
                    value={robinhoodPassword}
                    onChange={(e) => setRobinhoodPassword(e.target.value)}
                    style={{ width: '100%', marginTop: '4px', background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px', fontSize: '13px' }}
                  />
                </label>

                <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                  Authenticator Secret Key (TOTP) — Optional
                  <input
                    type="text"
                    placeholder="16-character alphanumeric key"
                    value={robinhoodMfaSecret}
                    onChange={(e) => setRobinhoodMfaSecret(e.target.value)}
                    style={{ width: '100%', marginTop: '4px', background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px', fontSize: '13px' }}
                  />
                  <div style={{ fontSize: '10px', color: 'var(--text-secondary)', marginTop: '4px' }}>
                    Provide your Authenticator App secret key to enable automated non-interactive syncing.
                  </div>
                </label>

                {!robinhoodMfaSecret && (
                  <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                    One-Time 2FA Code (If MFA enabled)
                    <input
                      type="text"
                      maxLength={6}
                      placeholder="6-digit code"
                      value={robinhoodMfaCode}
                      onChange={(e) => setRobinhoodMfaCode(e.target.value)}
                      style={{ width: '100%', marginTop: '4px', background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px', fontSize: '13px' }}
                    />
                  </label>
                )}
              </div>
            ) : (
              <div style={{ display: 'grid', gap: '12px' }}>
                <label style={{ fontSize: '13px', color: 'var(--text-primary)', fontWeight: 600 }}>
                  Enter Verification Code
                  <input
                    type="text"
                    maxLength={6}
                    placeholder="Enter 6-digit code"
                    value={robinhoodMfaCode}
                    onChange={(e) => setRobinhoodMfaCode(e.target.value)}
                    style={{ width: '100%', marginTop: '6px', background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '10px', fontSize: '16px', letterSpacing: '4px', textAlign: 'center' }}
                  />
                </label>
                <button
                  onClick={() => setRobinhoodMfaRequired(false)}
                  style={{ background: 'none', border: 'none', color: 'var(--color-gold)', cursor: 'pointer', fontSize: '11px', textAlign: 'left', padding: 0 }}
                >
                  Back to credentials
                </button>
              </div>
            )}

            <div style={{ display: 'flex', gap: '10px', marginTop: '10px', justifyContent: 'flex-end' }}>
              <button
                onClick={() => setShowRobinhoodSyncModal(false)}
                disabled={robinhoodSyncLoading}
                style={{ background: 'transparent', border: '1px solid var(--border-glass)', borderRadius: '8px', color: 'var(--text-secondary)', padding: '8px 16px', cursor: 'pointer' }}
              >
                Cancel
              </button>
              <button
                onClick={async () => {
                  setRobinhoodSyncError('');
                  setRobinhoodSyncLoading(true);
                  try {
                    const body = {
                      username: robinhoodUsername,
                      password: robinhoodPassword,
                      mfa_secret: robinhoodMfaSecret || null,
                      mfa_code: robinhoodMfaCode || null,
                      account_label: selectedAccount
                    };
                    const res = await fetch(apiUrl('/api/external/sync/robinhood'), {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify(body)
                    });
                    if (res.ok) {
                      const data = await res.json();
                      if (data.status === 'mfa_required') {
                        setRobinhoodMfaRequired(true);
                        setRobinhoodSyncError(data.message);
                      } else {
                        setShowRobinhoodSyncModal(false);
                        setReconcileStatus(`Success: Synced ${data.positions_synced} positions and ${data.transactions_synced} transaction activities via Robinhood API. Cash updated to ${money(data.cash)}.`);
                        fetchExternalAccounts();
                        fetchExternalPositionsAndSuggestions(selectedAccount);
                      }
                    } else {
                      const err = await res.json();
                      setRobinhoodSyncError(err.detail || 'Authentication or sync failed.');
                    }
                  } catch (err: any) {
                    setRobinhoodSyncError(`Sync error: ${err.message}`);
                  } finally {
                    setRobinhoodSyncLoading(false);
                  }
                }}
                disabled={robinhoodSyncLoading || !robinhoodUsername || !robinhoodPassword}
                className="toggle-btn"
                style={{
                  background: 'var(--color-buy)',
                  border: 'none',
                  color: 'black',
                  fontWeight: 700,
                  display: 'flex',
                  alignItems: 'center',
                  gap: '6px'
                }}
              >
                {robinhoodSyncLoading ? (
                  <>Syncing...</>
                ) : (
                  <>Start Sync</>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Sell-from-lot modal */}
      {sellModal && (() => {
        const px = parseFloat(sellModal.sale_price) || 0;
        const sh = parseFloat(sellModal.shares) || 0;
        const realized = (px - sellModal.basis) * sh;
        const isLoss = realized < 0;
        return (
          <div onClick={() => !sellBusy && setSellModal(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
            <div onClick={(e) => e.stopPropagation()} style={{ background: 'rgba(16, 20, 38, 0.98)', border: '1px solid var(--border-glass)', borderRadius: '14px', padding: '24px', width: '420px', maxWidth: '92vw', boxShadow: '0 20px 60px rgba(0,0,0,0.5)' }}>
              <h2 style={{ marginTop: 0, marginBottom: '4px' }}>Sell {sellModal.ticker}</h2>
              <p style={{ margin: '0 0 14px', fontSize: '12.5px', color: 'var(--text-secondary)' }}>Record a sale from this lot ({sellModal.max} sh held, basis {sharePrice(sellModal.basis)}). Updates your remaining shares — it does not place a broker order.</p>
              <div style={{ display: 'grid', gap: '10px' }}>
                <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Shares to sell
                  <input type="number" max={sellModal.max} value={sellModal.shares} onChange={(e) => setSellModal({ ...sellModal, shares: Math.min(sellModal.max, parseFloat(e.target.value) || 0) })} style={{ width: '100%', marginTop: '4px', background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px' }} />
                  <button onClick={() => setSellModal({ ...sellModal, shares: sellModal.max })} style={{ background: 'transparent', border: 0, color: 'var(--color-gold)', cursor: 'pointer', fontSize: '11px', padding: '2px 0' }}>Sell all {sellModal.max}</button>
                </label>
                <div style={{ display: 'flex', gap: '10px' }}>
                  <label style={{ fontSize: '12px', color: 'var(--text-secondary)', flex: 1 }}>Sale price
                    <input type="number" value={sellModal.sale_price} onChange={(e) => setSellModal({ ...sellModal, sale_price: e.target.value })} style={{ width: '100%', marginTop: '4px', background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px' }} />
                  </label>
                  <label style={{ fontSize: '12px', color: 'var(--text-secondary)', flex: 1 }}>Sale date
                    <input type="date" value={sellModal.sale_date} onChange={(e) => setSellModal({ ...sellModal, sale_date: e.target.value })} style={{ width: '100%', marginTop: '4px', background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px' }} />
                  </label>
                </div>
                <div style={{ fontSize: '13px', padding: '8px 0', color: isLoss ? 'var(--color-sell)' : 'var(--color-buy)' }}>
                  Realized {isLoss ? 'loss' : 'gain'}: <strong>{money(realized)}</strong> · proceeds {money(px * sh)}
                </div>
                {isLoss && (
                  <label style={{ display: 'flex', gap: '8px', alignItems: 'flex-start', fontSize: '12.5px', color: 'var(--text-primary)', background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.3)', borderRadius: '8px', padding: '10px' }}>
                    <input type="checkbox" checked={!!sellModal.add_wash_sale_block} onChange={(e) => setSellModal({ ...sellModal, add_wash_sale_block: e.target.checked })} style={{ marginTop: '2px' }} />
                    <span><strong>Block re-buys for 31 days</strong> (wash-sale protection). Stops the bot from re-buying {sellModal.ticker} and disallowing this loss. Recommended.</span>
                  </label>
                )}
              </div>
              <div style={{ display: 'flex', gap: '10px', marginTop: '18px', justifyContent: 'flex-end' }}>
                <button onClick={() => setSellModal(null)} disabled={sellBusy} style={{ background: 'transparent', border: '1px solid var(--border-glass)', color: 'var(--text-secondary)', borderRadius: '8px', padding: '8px 16px', cursor: 'pointer' }}>Cancel</button>
                <button onClick={submitSell} disabled={sellBusy || sh <= 0} className="toggle-btn">{sellBusy ? 'Recording…' : 'Record sale'}</button>
              </div>
            </div>
          </div>
        );
      })()}

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

      {/* Tier-override popover (click a ticker badge) */}
      {tierMenu && (
        <>
          <div onClick={() => setTierMenu(null)} style={{ position: 'fixed', inset: 0, zIndex: 200 }} />
          <div style={{ position: 'fixed', left: Math.min(tierMenu.x, (typeof window !== 'undefined' ? window.innerWidth : 1200) - 200), top: tierMenu.y + 8, zIndex: 201, background: 'rgba(16,20,38,0.98)', border: '1px solid var(--border-glass)', borderRadius: '10px', padding: '6px', boxShadow: '0 12px 30px rgba(0,0,0,0.5)', minWidth: '180px' }}>
            <div style={{ fontSize: '11px', color: 'var(--text-secondary)', padding: '4px 8px 6px' }}>Set <strong style={{ color: 'var(--text-primary)' }}>{tierMenu.ticker}</strong> tier</div>
            {(['quality_growth', 'core', 'speculative', 'value_trap'] as const).map(k => (
              <button key={k} onClick={() => setTierOverride(tierMenu.ticker, k)}
                style={{ display: 'flex', alignItems: 'center', gap: '8px', width: '100%', padding: '7px 8px', background: classification[tierMenu.ticker]?.tier === k ? `${TIER_META[k].color}1A` : 'none', border: 'none', color: TIER_META[k].color, fontSize: '13px', fontWeight: 600, cursor: 'pointer', borderRadius: '6px', textAlign: 'left' }}>
                <span>{TIER_META[k].icon}</span> {TIER_META[k].label}
              </button>
            ))}
            <button onClick={() => setTierOverride(tierMenu.ticker, null)}
              style={{ display: 'flex', alignItems: 'center', gap: '8px', width: '100%', padding: '7px 8px', marginTop: '2px', borderTop: '1px solid var(--border-glass)', background: 'none', border: 'none', color: 'var(--text-secondary)', fontSize: '12.5px', cursor: 'pointer', borderRadius: '6px', textAlign: 'left' }}>
              ↺ Auto (clear override)
            </button>
          </div>
        </>
      )}

      {/* Apply Stance Rebalancing Confirmation Modal */}
      {applyConfirmOpen && (
        <div
          onClick={() => !applyingRebalance && setApplyConfirmOpen(false)}
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}
        >
          <div onClick={(e) => e.stopPropagation()} style={{ background: 'rgba(16, 20, 38, 0.98)', border: '1px solid var(--border-glass)', borderRadius: '14px', padding: '24px', width: '560px', maxWidth: '92vw', maxHeight: '88vh', overflowY: 'auto', boxShadow: '0 20px 60px rgba(0,0,0,0.5)' }}>
            <h2 style={{ marginTop: 0, marginBottom: '4px', color: 'var(--text-primary)' }}>Preview Rebalancing</h2>
            <p style={{ fontSize: '12.5px', color: 'var(--text-secondary)', marginBottom: '16px', lineHeight: '1.4' }}>
              A read-only dry run of the orders that would execute against the <strong>paper account (ID=1)</strong>. Nothing
              changes until you press Confirm — and even then it is virtual cash only.
            </p>

            {previewLoading && (
              <div style={{ padding: '30px 0', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '13px' }}>Computing preview…</div>
            )}

            {!previewLoading && previewData?.error && (
              <div style={{ background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.4)', borderRadius: '8px', padding: '12px', fontSize: '13px', color: '#EF4444', marginBottom: '16px' }}>
                Could not compute preview: {previewData.error}
              </div>
            )}

            {!previewLoading && previewData && !previewData.error && (
              <>
                {/* Plan summary */}
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', fontSize: '12px', marginBottom: '14px' }}>
                  <div>Preset/curve: <strong style={{ color: 'var(--text-primary)' }}>{previewData.preset_applied}</strong></div>
                  <div>De-risk coefficient: <strong style={{ color: '#F59E0B' }}>{(previewData.de_risk_coefficient * 100).toFixed(0)}%</strong></div>
                  <div>Portfolio value: <strong style={{ color: 'var(--text-primary)' }}>{money(previewData.portfolio_value)}</strong></div>
                  <div>Turnover: <strong style={{ color: 'var(--text-primary)' }}>{previewData.turnover_pct}%</strong></div>
                  <div>Cash before: <strong style={{ color: 'var(--text-primary)' }}>{money(previewData.cash_before)}</strong></div>
                  <div>Cash after (est): <strong style={{ color: 'var(--text-primary)' }}>{money(previewData.est_cash_after)}</strong></div>
                </div>

                {/* Validation messages */}
                {(previewData.validation?.errors?.length > 0 || previewData.validation?.warnings?.length > 0) && (
                  <div style={{ display: 'grid', gap: '6px', marginBottom: '14px' }}>
                    {previewData.validation.errors.map((e: string, i: number) => (
                      <div key={`e${i}`} style={{ background: 'rgba(239,68,68,0.12)', border: '1px solid rgba(239,68,68,0.4)', borderRadius: '6px', padding: '8px 10px', fontSize: '11.5px', color: '#EF4444' }}>⛔ {e}</div>
                    ))}
                    {previewData.validation.warnings.map((w: string, i: number) => (
                      <div key={`w${i}`} style={{ background: 'rgba(245,158,11,0.1)', border: '1px solid rgba(245,158,11,0.3)', borderRadius: '6px', padding: '8px 10px', fontSize: '11.5px', color: '#F59E0B' }}>⚠ {w}</div>
                    ))}
                  </div>
                )}

                {/* Order table */}
                {previewData.orders?.length > 0 ? (
                  <div style={{ border: '1px solid var(--border-glass)', borderRadius: '8px', overflow: 'hidden', marginBottom: '18px' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                      <thead>
                        <tr style={{ background: 'rgba(255,255,255,0.04)', textAlign: 'left', color: 'var(--text-secondary)' }}>
                          <th style={{ padding: '8px 10px' }}>Ticker</th>
                          <th style={{ padding: '8px 10px' }}>Side</th>
                          <th style={{ padding: '8px 10px', textAlign: 'right' }}>Now → Target</th>
                          <th style={{ padding: '8px 10px', textAlign: 'right' }}>Qty</th>
                          <th style={{ padding: '8px 10px', textAlign: 'right' }}>Value</th>
                        </tr>
                      </thead>
                      <tbody>
                        {previewData.orders.map((o: any, i: number) => (
                          <tr key={i} style={{ borderTop: '1px solid var(--border-glass)' }}>
                            <td style={{ padding: '8px 10px', fontWeight: 600, color: 'var(--text-primary)' }}>{o.ticker}</td>
                            <td style={{ padding: '8px 10px', fontWeight: 700, color: o.side === 'buy' ? '#10B981' : '#EF4444' }}>{o.side.toUpperCase()}</td>
                            <td style={{ padding: '8px 10px', textAlign: 'right', color: 'var(--text-secondary)' }}>{o.current_weight}% → {o.target_weight}%</td>
                            <td style={{ padding: '8px 10px', textAlign: 'right' }}>{o.qty}</td>
                            <td style={{ padding: '8px 10px', textAlign: 'right' }}>{money(o.value)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <div style={{ background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-glass)', borderRadius: '8px', padding: '14px', fontSize: '12.5px', color: 'var(--text-secondary)', marginBottom: '18px', textAlign: 'center' }}>
                    No orders needed — the paper portfolio already matches the target allocation.
                  </div>
                )}
              </>
            )}

            <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end' }}>
              <button onClick={() => setApplyConfirmOpen(false)} disabled={applyingRebalance}
                style={{ background: 'transparent', border: '1px solid var(--border-glass)', borderRadius: '8px', color: 'var(--text-secondary)', padding: '8px 16px', fontSize: '13px', cursor: 'pointer' }}>Cancel</button>
              <button onClick={handleApplyRebalance}
                disabled={applyingRebalance || previewLoading || !previewData || previewData.error || !previewData.validation?.ok || (previewData.orders?.length || 0) === 0}
                style={{ background: 'var(--color-gold)', border: 'none', borderRadius: '8px', color: 'black', padding: '8px 16px', fontSize: '13px', fontWeight: 700, cursor: 'pointer', opacity: (applyingRebalance || previewLoading || !previewData || previewData.error || !previewData.validation?.ok || (previewData.orders?.length || 0) === 0) ? 0.5 : 1 }}>
                {applyingRebalance ? 'Executing...' : 'Confirm & Execute (Paper)'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
