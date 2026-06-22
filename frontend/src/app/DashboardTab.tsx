'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity, AlertTriangle, ArrowRight, BarChart2, ChevronRight,
  Layers, ShieldAlert, TrendingDown, TrendingUp, X, Zap,
} from 'lucide-react';
import { apiUrl } from '../lib/api';
import PriceChart from './PriceChart';

// ─── types ──────────────────────────────────────────────────────────────────

interface SwingSuggestion {
  ticker: string; close: number; action: 'BUY' | 'HOLD';
  confidence: number; stop_loss: number | null; take_profit: number | null;
  horizon_days: number; llm_news: number; llm_news_intensity: number; reasoning: string;
}
interface Allocation {
  ticker: string; weight: number; current_shares?: number; current_price?: number;
  current_value?: number; target_shares?: number; suggested_action?: string;
}
interface PriceSummaryRow {
  ticker: string; price: number | null; d1?: number; w1?: number; m1?: number; y1?: number; is_live?: boolean;
}
interface TickerInfo {
  company_name: string | null; description: string | null;
  sector: string | null; industry: string | null; market_cap: number | null;
}
interface Classification { tier?: string; quality?: number; volatility?: number; }

// ─── helpers ─────────────────────────────────────────────────────────────────

const money = (n: number) =>
  n >= 1e9 ? `$${(n / 1e9).toFixed(2)}B`
  : n >= 1e6 ? `$${(n / 1e6).toFixed(2)}M`
  : n >= 1e3 ? `$${(n / 1e3).toFixed(1)}K`
  : `$${n.toFixed(2)}`;

const pct = (n: number | null | undefined, decimals = 1) =>
  n == null ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(decimals)}%`;

const TIER_COLOR: Record<string, string> = {
  quality_growth: '#10B981', core: '#00F2FE', speculative: '#F59E0B', value_trap: '#6B7280',
};
const TIER_LABEL: Record<string, string> = {
  quality_growth: 'Hot', core: 'Solid', speculative: 'Long-shot', value_trap: 'Cold',
};

const REGIME_COLOR: Record<string, string> = { growth: '#10B981', transition: '#F59E0B', crisis: '#EF4444' };

// ─── Ticker Drawer ────────────────────────────────────────────────────────────

function TickerDrawer({
  ticker, info, classification, priceRow, swing, allocation, onClose,
}: {
  ticker: string; info: TickerInfo | null; classification: Classification | null;
  priceRow: PriceSummaryRow | null; swing: SwingSuggestion | null;
  allocation: Allocation | null; onClose: () => void;
}) {
  const tier = classification?.tier;
  const price = priceRow?.price;

  return (
    <div style={{
      position: 'fixed', right: 0, top: 0, bottom: 0, width: 'min(480px, 100vw)',
      background: 'var(--bg-card)', borderLeft: '1px solid var(--border-glass)',
      zIndex: 200, display: 'flex', flexDirection: 'column', overflowY: 'auto',
      boxShadow: '-8px 0 32px rgba(0,0,0,0.4)',
    }}>
      {/* Header */}
      <div style={{ padding: '20px 20px 12px', borderBottom: '1px solid var(--border-glass)', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
            <span style={{ fontSize: '22px', fontWeight: 700, color: 'var(--text-primary)' }}>{ticker}</span>
            {tier && (
              <span style={{ fontSize: '10px', fontWeight: 700, padding: '2px 7px', borderRadius: '4px', background: `${TIER_COLOR[tier]}22`, border: `1px solid ${TIER_COLOR[tier]}55`, color: TIER_COLOR[tier] }}>
                {TIER_LABEL[tier] ?? tier}
              </span>
            )}
            {price != null && (
              <span style={{ fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)' }}>
                {money(price)}
              </span>
            )}
            {priceRow?.d1 != null && (
              <span style={{ fontSize: '12px', fontWeight: 600, color: priceRow.d1 >= 0 ? '#10B981' : '#F43F5E' }}>
                {pct(priceRow.d1)} today
              </span>
            )}
          </div>
          {info?.company_name && (
            <div style={{ fontSize: '14px', color: 'var(--text-secondary)', marginTop: '4px' }}>{info.company_name}</div>
          )}
          {(info?.sector || info?.industry) && (
            <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '2px', opacity: 0.7 }}>
              {[info.sector, info.industry].filter(Boolean).join(' · ')}
            </div>
          )}
        </div>
        <button onClick={onClose} style={{ background: 'none', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', padding: '4px' }}>
          <X size={20} />
        </button>
      </div>

      <div style={{ padding: '16px 20px', flex: 1 }}>
        {/* Description */}
        {info?.description && (
          <p style={{ fontSize: '12px', lineHeight: 1.6, color: 'var(--text-secondary)', marginBottom: '16px', borderLeft: '3px solid var(--border-glass)', paddingLeft: '10px' }}>
            {info.description}
          </p>
        )}

        {/* Price chart */}
        <div style={{ marginBottom: '20px' }}>
          <PriceChart ticker={ticker} height={200} />
        </div>

        {/* Signals */}
        {swing && (
          <div style={{ marginBottom: '16px', padding: '12px', borderRadius: '8px', background: swing.action === 'BUY' ? 'rgba(16,185,129,0.08)' : 'rgba(255,255,255,0.04)', border: `1px solid ${swing.action === 'BUY' ? 'rgba(16,185,129,0.3)' : 'var(--border-glass)'}` }}>
            <div style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-secondary)', marginBottom: '8px' }}>Swing signal</div>
            <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', alignItems: 'center' }}>
              <span style={{ fontSize: '18px', fontWeight: 700, color: swing.action === 'BUY' ? '#10B981' : 'var(--text-secondary)' }}>{swing.action}</span>
              <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>Confidence: <strong style={{ color: 'var(--text-primary)' }}>{(swing.confidence * 100).toFixed(0)}%</strong></span>
              {swing.stop_loss && <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>Stop: <strong style={{ color: '#F43F5E' }}>{money(swing.stop_loss)}</strong></span>}
              {swing.take_profit && <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>Target: <strong style={{ color: '#10B981' }}>{money(swing.take_profit)}</strong></span>}
              <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>Horizon: {swing.horizon_days}d</span>
            </div>
            {swing.llm_news !== 0 && (
              <div style={{ marginTop: '8px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                LLM News: <strong style={{ color: swing.llm_news > 0 ? '#10B981' : '#F43F5E' }}>{swing.llm_news > 0 ? '+' : ''}{swing.llm_news.toFixed(2)}</strong>
                {swing.llm_news_intensity > 0 && <span> · intensity {swing.llm_news_intensity.toFixed(2)}</span>}
              </div>
            )}
            {swing.reasoning && (
              <p style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '8px', lineHeight: 1.5, margin: '8px 0 0' }}>{swing.reasoning}</p>
            )}
          </div>
        )}

        {/* Long-term allocation */}
        {allocation && (
          <div style={{ marginBottom: '16px', padding: '12px', borderRadius: '8px', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-glass)' }}>
            <div style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-secondary)', marginBottom: '8px' }}>Long-term MPT</div>
            <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
              <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>Target weight: <strong style={{ color: 'var(--text-primary)' }}>{(allocation.weight * 100).toFixed(1)}%</strong></span>
              {allocation.current_shares != null && <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>Held: <strong style={{ color: 'var(--text-primary)' }}>{allocation.current_shares.toFixed(2)} sh</strong></span>}
              {allocation.target_shares != null && <span style={{ fontSize: '13px', color: 'var(--text-secondary)' }}>Target: <strong style={{ color: 'var(--text-primary)' }}>{allocation.target_shares.toFixed(2)} sh</strong></span>}
            </div>
            {allocation.suggested_action && (
              <div style={{ marginTop: '8px', fontSize: '12px', fontWeight: 600, color: allocation.suggested_action.includes('BUY') ? '#10B981' : allocation.suggested_action.includes('SELL') ? '#F43F5E' : 'var(--text-secondary)' }}>
                {allocation.suggested_action}
              </div>
            )}
          </div>
        )}

        {/* Price changes */}
        {priceRow && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '8px' }}>
            {([['1D', priceRow.d1], ['1W', priceRow.w1], ['1M', priceRow.m1], ['1Y', priceRow.y1]] as [string, number | undefined][]).map(([label, val]) => (
              <div key={label} style={{ padding: '8px', borderRadius: '6px', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-glass)', textAlign: 'center' }}>
                <div style={{ fontSize: '10px', color: 'var(--text-secondary)', marginBottom: '2px' }}>{label}</div>
                <div style={{ fontSize: '13px', fontWeight: 600, color: val == null ? 'var(--text-secondary)' : val >= 0 ? '#10B981' : '#F43F5E' }}>
                  {pct(val)}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Main DashboardTab ───────────────────────────────────────────────────────

export default function DashboardTab({
  regime, date, swingSuggestions, allocations, priceSummary, portfolio, classification,
}: {
  regime: string; date: string;
  swingSuggestions: SwingSuggestion[]; allocations: Allocation[];
  priceSummary: PriceSummaryRow[]; portfolio: any; classification: Record<string, Classification>;
}) {
  const [tickerInfo, setTickerInfo] = useState<Record<string, TickerInfo>>({});
  const [selectedTicker, setSelectedTicker] = useState<string | null>(null);
  const infoFetched = useRef(false);

  // Collect all unique tickers across signals + positions
  const allTickers = useMemo(() => {
    const s = new Set<string>();
    swingSuggestions.forEach((x) => s.add(x.ticker));
    allocations.forEach((x) => s.add(x.ticker));
    (portfolio?.positions || []).forEach((x: any) => s.add(x.ticker));
    return Array.from(s);
  }, [swingSuggestions, allocations, portfolio]);

  const fetchTickerInfo = useCallback(async (tickers: string[]) => {
    if (!tickers.length) return;
    try {
      const res = await fetch(apiUrl(`/api/tickers/info?tickers=${tickers.join(',')}`));
      if (res.ok) {
        const j = await res.json();
        setTickerInfo((prev) => ({ ...prev, ...j.tickers }));
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    if (allTickers.length && !infoFetched.current) {
      infoFetched.current = true;
      fetchTickerInfo(allTickers);
    }
  }, [allTickers, fetchTickerInfo]);

  const priceMap = useMemo(() => {
    const m: Record<string, PriceSummaryRow> = {};
    (priceSummary || []).forEach((r) => { m[r.ticker] = r; });
    return m;
  }, [priceSummary]);

  const swingMap = useMemo(() => {
    const m: Record<string, SwingSuggestion> = {};
    swingSuggestions.forEach((s) => { m[s.ticker] = s; });
    return m;
  }, [swingSuggestions]);

  const allocMap = useMemo(() => {
    const m: Record<string, Allocation> = {};
    allocations.forEach((a) => { m[a.ticker] = a; });
    return m;
  }, [allocations]);

  const buySignals = useMemo(() =>
    swingSuggestions.filter((s) => s.action === 'BUY').sort((a, b) => b.confidence - a.confidence),
    [swingSuggestions]);

  const mptActions = useMemo(() =>
    allocations.filter((a) => a.suggested_action && a.suggested_action !== 'Hold')
      .sort((a, b) => Math.abs((b.target_shares ?? 0) - (b.current_shares ?? 0)) - Math.abs((a.target_shares ?? 0) - (a.current_shares ?? 0))),
    [allocations]);

  const positions: any[] = useMemo(() => portfolio?.positions || [], [portfolio]);

  const selectedSwing = selectedTicker ? swingMap[selectedTicker] ?? null : null;
  const selectedAlloc = selectedTicker ? allocMap[selectedTicker] ?? null : null;
  const selectedInfo = selectedTicker ? (tickerInfo[selectedTicker] ?? null) : null;
  const selectedPriceRow = selectedTicker ? (priceMap[selectedTicker] ?? null) : null;
  const selectedClass = selectedTicker ? (classification[selectedTicker] ?? null) : null;

  const labelStyle: React.CSSProperties = {
    fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em',
    color: 'var(--text-secondary)', marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '6px',
  };

  return (
    <div style={{ gridColumn: '1 / -1', display: 'grid', gap: '16px' }}>

      {/* ── Portfolio Summary Bar ───────────────────────────────────────── */}
      <div className="glass-card" style={{ padding: '16px 20px', display: 'flex', gap: '24px', flexWrap: 'wrap', alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '2px' }}>Total Portfolio</div>
          <div style={{ fontSize: '22px', fontWeight: 700, color: 'var(--text-primary)' }}>
            {portfolio?.totals?.total_value != null ? money(portfolio.totals.total_value) : '—'}
          </div>
        </div>
        {portfolio?.totals?.unrealized_pl != null && (
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '2px' }}>Unrealized P&amp;L</div>
            <div style={{ fontSize: '18px', fontWeight: 700, color: portfolio.totals.unrealized_pl >= 0 ? '#10B981' : '#F43F5E' }}>
              {portfolio.totals.unrealized_pl >= 0 ? '+' : ''}{money(Math.abs(portfolio.totals.unrealized_pl))}
              {portfolio.totals.unrealized_plpc != null && (
                <span style={{ fontSize: '13px', marginLeft: '6px' }}>({pct(portfolio.totals.unrealized_plpc)})</span>
              )}
            </div>
          </div>
        )}
        {portfolio?.totals?.cash != null && (
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '2px' }}>Cash</div>
            <div style={{ fontSize: '16px', fontWeight: 600 }}>{money(portfolio.totals.cash)}</div>
          </div>
        )}
        {portfolio?.totals?.equity != null && (
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '2px' }}>Equity</div>
            <div style={{ fontSize: '16px', fontWeight: 600 }}>{money(portfolio.totals.equity)}</div>
          </div>
        )}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ padding: '6px 14px', borderRadius: '20px', fontSize: '12px', fontWeight: 700, background: `${REGIME_COLOR[regime] ?? '#64748b'}22`, border: `1px solid ${REGIME_COLOR[regime] ?? '#64748b'}55`, color: REGIME_COLOR[regime] ?? '#64748b' }}>
            {regime.toUpperCase()} REGIME
          </div>
          {date && <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Signals as of {date}</div>}
        </div>
      </div>

      {/* ── Two-column: Signals + Holdings ──────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.6fr', gap: '16px', alignItems: 'start' }}>

        {/* ── Left: Action Items ─────────────────────────────────────────── */}
        <div style={{ display: 'grid', gap: '12px' }}>

          {/* Swing BUY signals */}
          <div className="glass-card" style={{ padding: '16px' }}>
            <div style={labelStyle}>
              <Zap size={13} color="#10B981" /> Swing BUY signals ({buySignals.length})
            </div>
            {buySignals.length === 0 ? (
              <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0 }}>No swing BUY signals today.</p>
            ) : (
              <div style={{ display: 'grid', gap: '8px' }}>
                {buySignals.map((s) => {
                  const info = tickerInfo[s.ticker];
                  const pr = priceMap[s.ticker];
                  return (
                    <button key={s.ticker} onClick={() => setSelectedTicker(s.ticker)}
                      style={{ width: '100%', textAlign: 'left', background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.25)', borderRadius: '8px', padding: '10px 12px', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px' }}>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <strong style={{ color: 'var(--text-primary)', fontSize: '14px' }}>{s.ticker}</strong>
                          {pr?.price != null && <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{money(pr.price)}</span>}
                          {pr?.d1 != null && <span style={{ fontSize: '11px', color: pr.d1 >= 0 ? '#10B981' : '#F43F5E' }}>{pct(pr.d1)}</span>}
                        </div>
                        {info?.company_name && <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '1px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{info.company_name}</div>}
                        {s.stop_loss && <div style={{ fontSize: '10px', color: 'var(--text-secondary)', marginTop: '2px' }}>Stop {money(s.stop_loss)} · Target {s.take_profit ? money(s.take_profit) : '—'} · {s.horizon_days}d</div>}
                      </div>
                      <div style={{ flexShrink: 0, textAlign: 'right' }}>
                        <div style={{ fontSize: '14px', fontWeight: 700, color: '#10B981' }}>BUY</div>
                        <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>{(s.confidence * 100).toFixed(0)}%</div>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* MPT rebalancing alerts */}
          {mptActions.length > 0 && (
            <div className="glass-card" style={{ padding: '16px' }}>
              <div style={labelStyle}>
                <BarChart2 size={13} color="#00F2FE" /> Long-term MPT rebalances ({mptActions.length})
              </div>
              <div style={{ display: 'grid', gap: '6px' }}>
                {mptActions.slice(0, 8).map((a) => {
                  const isBuy = (a.suggested_action || '').includes('BUY');
                  const isSell = (a.suggested_action || '').includes('SELL');
                  const color = isBuy ? '#10B981' : isSell ? '#F43F5E' : 'var(--text-secondary)';
                  const pr = priceMap[a.ticker];
                  return (
                    <button key={a.ticker} onClick={() => setSelectedTicker(a.ticker)}
                      style={{ width: '100%', textAlign: 'left', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px 10px', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px' }}>
                      <div>
                        <span style={{ fontWeight: 700, fontSize: '13px', color: 'var(--text-primary)' }}>{a.ticker}</span>
                        {pr?.price != null && <span style={{ fontSize: '11px', color: 'var(--text-secondary)', marginLeft: '6px' }}>{money(pr.price)}</span>}
                        <div style={{ fontSize: '10px', color: 'var(--text-secondary)', marginTop: '1px' }}>
                          {(a.weight * 100).toFixed(1)}% target · {a.current_shares?.toFixed(1) ?? '0'} → {a.target_shares?.toFixed(1) ?? '?'} sh
                        </div>
                      </div>
                      <span style={{ fontSize: '12px', fontWeight: 700, color, flexShrink: 0 }}>{a.suggested_action?.split(' ')[0]}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* ── Right: Holdings table ────────────────────────────────────── */}
        <div className="glass-card" style={{ padding: '16px' }}>
          <div style={labelStyle}>
            <Layers size={13} color="#a78bfa" /> Current holdings ({positions.length})
          </div>
          {positions.length === 0 ? (
            <p style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>No positions in this account.</p>
          ) : (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.07)' }}>
                    {['Ticker', 'Name', 'Price', '1D', '1M', 'Shares', 'Value', 'P&L', 'Signal'].map((h) => (
                      <th key={h} style={{ padding: '6px 8px', textAlign: h === 'Ticker' || h === 'Name' ? 'left' : 'right', fontWeight: 600, fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {positions.map((p: any) => {
                    const tk: string = p.ticker || p.symbol || '';
                    const pr = priceMap[tk];
                    const info = tickerInfo[tk];
                    const swing = swingMap[tk];
                    const cls = classification[tk];
                    const tier = cls?.tier;
                    const unrealizedPl = p.unrealized_pl ?? p.unrealized_plpc;
                    const plVal = p.unrealized_pl != null ? p.unrealized_pl : null;
                    const plPct = p.unrealized_plpc != null ? p.unrealized_plpc : null;
                    return (
                      <tr key={tk} onClick={() => setSelectedTicker(tk)}
                        style={{ borderBottom: '1px solid rgba(255,255,255,0.04)', cursor: 'pointer', transition: 'background 0.1s' }}
                        onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.04)')}
                        onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}>
                        <td style={{ padding: '9px 8px', whiteSpace: 'nowrap' }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                            <strong style={{ color: 'var(--text-primary)' }}>{tk}</strong>
                            {tier && <span style={{ fontSize: '9px', padding: '1px 4px', borderRadius: '3px', background: `${TIER_COLOR[tier]}22`, color: TIER_COLOR[tier], fontWeight: 700 }}>{TIER_LABEL[tier] ?? tier}</span>}
                          </div>
                        </td>
                        <td style={{ padding: '9px 8px', color: 'var(--text-secondary)', maxWidth: '140px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {info?.company_name ?? '—'}
                        </td>
                        <td style={{ padding: '9px 8px', textAlign: 'right', fontWeight: 600 }}>
                          {p.current_price != null ? money(p.current_price) : pr?.price != null ? money(pr.price) : '—'}
                        </td>
                        <td style={{ padding: '9px 8px', textAlign: 'right', color: pr?.d1 == null ? 'var(--text-secondary)' : pr.d1 >= 0 ? '#10B981' : '#F43F5E' }}>
                          {pct(pr?.d1)}
                        </td>
                        <td style={{ padding: '9px 8px', textAlign: 'right', color: pr?.m1 == null ? 'var(--text-secondary)' : pr.m1 >= 0 ? '#10B981' : '#F43F5E' }}>
                          {pct(pr?.m1)}
                        </td>
                        <td style={{ padding: '9px 8px', textAlign: 'right' }}>
                          {p.quantity != null ? p.quantity.toFixed(2) : p.qty != null ? parseFloat(p.qty).toFixed(2) : '—'}
                        </td>
                        <td style={{ padding: '9px 8px', textAlign: 'right', fontWeight: 600 }}>
                          {p.market_value != null ? money(p.market_value) : '—'}
                        </td>
                        <td style={{ padding: '9px 8px', textAlign: 'right', color: plVal == null ? 'var(--text-secondary)' : plVal >= 0 ? '#10B981' : '#F43F5E' }}>
                          {plVal != null ? `${plVal >= 0 ? '+' : ''}${money(Math.abs(plVal))}` : '—'}
                          {plPct != null && <span style={{ fontSize: '10px', marginLeft: '3px' }}>({pct(plPct)})</span>}
                        </td>
                        <td style={{ padding: '9px 8px', textAlign: 'right' }}>
                          {swing ? (
                            <span style={{ fontSize: '11px', fontWeight: 700, color: swing.action === 'BUY' ? '#10B981' : 'var(--text-secondary)' }}>
                              {swing.action} {(swing.confidence * 100).toFixed(0)}%
                            </span>
                          ) : allocMap[tk] ? (
                            <span style={{ fontSize: '10px', color: '#00F2FE' }}>MPT</span>
                          ) : (
                            <span style={{ color: 'var(--text-secondary)' }}>—</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {/* Ticker detail drawer */}
      {selectedTicker && (
        <>
          <div onClick={() => setSelectedTicker(null)} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', zIndex: 199 }} />
          <TickerDrawer
            ticker={selectedTicker}
            info={selectedInfo}
            classification={selectedClass}
            priceRow={selectedPriceRow}
            swing={selectedSwing}
            allocation={selectedAlloc}
            onClose={() => setSelectedTicker(null)}
          />
        </>
      )}
    </div>
  );
}
