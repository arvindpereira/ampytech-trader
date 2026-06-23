'use client';

import React, { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { apiUrl } from '../lib/api';
import PriceChart from './PriceChart';

// ─── shared types ─────────────────────────────────────────────────────────────

export interface SwingSuggestion {
  ticker: string; close: number; action: 'BUY' | 'HOLD';
  confidence: number; stop_loss: number | null; take_profit: number | null;
  horizon_days: number; llm_news: number; llm_news_intensity: number; reasoning: string;
}
export interface Allocation {
  ticker: string; weight: number; current_shares?: number; current_price?: number;
  current_value?: number; target_shares?: number; suggested_action?: string;
}
export interface PriceSummaryRow {
  ticker: string; price: number | null; d1?: number; w1?: number; m1?: number; y1?: number; is_live?: boolean;
}
export interface TickerInfo {
  company_name: string | null; description: string | null;
  sector: string | null; industry: string | null; market_cap: number | null;
  ceo?: string | null; website?: string | null; country?: string | null;
  employees?: number | null; exchange?: string | null; logo_url?: string | null;
}
export interface Classification { tier?: string; quality?: number; volatility?: number; }
export interface QuoteStats {
  ticker: string; price: number | null; open: number | null;
  day_high: number | null; day_low: number | null;
  volume: number | null; avg_volume: number | null;
  week52_high: number | null; week52_low: number | null;
  market_cap: number | null; pe_ratio: number | null; dividend_yield: number | null;
  short_shares: number | null; short_pct_float: number | null; short_ratio: number | null;
  borrow_rate: number | null;
}

// ─── shared helpers ───────────────────────────────────────────────────────────

export const money = (n: number) =>
  n >= 1e9 ? `$${(n / 1e9).toFixed(2)}B`
  : n >= 1e6 ? `$${(n / 1e6).toFixed(2)}M`
  : n >= 1e3 ? `$${(n / 1e3).toFixed(1)}K`
  : `$${n.toFixed(2)}`;

export const pct = (n: number | null | undefined, decimals = 1) =>
  n == null ? '—' : `${n >= 0 ? '+' : ''}${n.toFixed(decimals)}%`;

// Compact share/volume counts: 5.74M, 1.2B (no currency sign).
const num = (n: number | null | undefined) =>
  n == null ? '—'
  : n >= 1e9 ? `${(n / 1e9).toFixed(2)}B`
  : n >= 1e6 ? `${(n / 1e6).toFixed(2)}M`
  : n >= 1e3 ? `${(n / 1e3).toFixed(1)}K`
  : `${n}`;

export const TIER_COLOR: Record<string, string> = {
  quality_growth: '#10B981', core: '#00F2FE', speculative: '#F59E0B', value_trap: '#6B7280',
};
export const TIER_LABEL: Record<string, string> = {
  quality_growth: 'Hot', core: 'Solid', speculative: 'Long-shot', value_trap: 'Cold',
};

// ─── Ticker Drawer ────────────────────────────────────────────────────────────

export function TickerDrawer({
  ticker, info, classification, priceRow, swing, allocation, quote, onClose,
}: {
  ticker: string; info: TickerInfo | null; classification: Classification | null;
  priceRow: PriceSummaryRow | null; swing: SwingSuggestion | null;
  allocation: Allocation | null; quote?: QuoteStats | null; onClose: () => void;
}) {
  const tier = classification?.tier;
  const price = priceRow?.price;

  return (
    <div style={{
      position: 'fixed', right: 0, top: 0, bottom: 0, width: 'min(480px, 100vw)',
      background: 'rgba(11, 14, 28, 0.98)', backdropFilter: 'blur(16px)',
      borderLeft: '1px solid var(--border-glass)',
      zIndex: 200, display: 'flex', flexDirection: 'column', overflowY: 'auto',
      boxShadow: '-8px 0 32px rgba(0,0,0,0.4)',
    }}>
      {/* Header */}
      <div style={{ padding: '20px 20px 12px', borderBottom: '1px solid var(--border-glass)', display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px' }}>
        {info?.logo_url && (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={info.logo_url} alt="" width={36} height={36}
            style={{ borderRadius: '8px', background: '#fff', objectFit: 'contain', flexShrink: 0, padding: '2px' }}
            onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }} />
        )}
        <div style={{ flex: 1, minWidth: 0 }}>
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
          {(info?.sector || info?.industry || info?.exchange) && (
            <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '2px', opacity: 0.7 }}>
              {[info?.sector, info?.industry, info?.exchange].filter(Boolean).join(' · ')}
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

        {/* Company facts */}
        {info && (info.ceo || info.market_cap || info.employees || info.country || info.website) && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginBottom: '20px' }}>
            {([
              ['CEO', info.ceo ?? null],
              ['Market cap', info.market_cap != null ? money(info.market_cap) : null],
              ['Employees', info.employees != null ? info.employees.toLocaleString() : null],
              ['Country', info.country ?? null],
            ] as [string, string | null][]).filter(([, v]) => v).map(([label, val]) => (
              <div key={label} style={{ padding: '8px 10px', borderRadius: '6px', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-glass)' }}>
                <div style={{ fontSize: '10px', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '2px' }}>{label}</div>
                <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{val}</div>
              </div>
            ))}
            {info.website && (
              <a href={info.website} target="_blank" rel="noopener noreferrer"
                style={{ gridColumn: '1 / -1', fontSize: '12px', color: '#00F2FE', textDecoration: 'none', padding: '4px 0' }}>
                {info.website.replace(/^https?:\/\/(www\.)?/, '').replace(/\/$/, '')} ↗
              </a>
            )}
          </div>
        )}

        {/* Stats */}
        {quote && (
          <div style={{ marginBottom: '20px' }}>
            <div style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-secondary)', marginBottom: '8px' }}>Stats</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '1px', background: 'var(--border-glass)', border: '1px solid var(--border-glass)', borderRadius: '8px', overflow: 'hidden' }}>
              {([
                ['Volume', num(quote.volume)],
                ['Avg volume', num(quote.avg_volume)],
                ['Open', quote.open != null ? money(quote.open) : '—'],
                ["Today's high", quote.day_high != null ? money(quote.day_high) : '—'],
                ["Today's low", quote.day_low != null ? money(quote.day_low) : '—'],
                ['Market cap', quote.market_cap != null ? money(quote.market_cap) : '—'],
                ['52-wk high', quote.week52_high != null ? money(quote.week52_high) : '—'],
                ['52-wk low', quote.week52_low != null ? money(quote.week52_low) : '—'],
                ['P/E ratio', quote.pe_ratio != null ? quote.pe_ratio.toFixed(1) : '—'],
                ['Div yield', quote.dividend_yield != null ? `${quote.dividend_yield.toFixed(2)}%` : '—'],
                ['Short % float', quote.short_pct_float != null ? `${quote.short_pct_float.toFixed(1)}%` : '—'],
                ['Borrow rate', quote.borrow_rate != null ? `${quote.borrow_rate.toFixed(2)}%` : '—'],
              ] as [string, string][]).map(([label, val]) => (
                <div key={label} style={{ background: 'var(--bg-card)', padding: '8px 10px' }}>
                  <div style={{ fontSize: '9px', color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '2px', whiteSpace: 'nowrap' }}>{label}</div>
                  <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)' }}>{val}</div>
                </div>
              ))}
            </div>
          </div>
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

// ─── Self-contained host: render once at page level, open from any tab ────────

export function TickerDrawerHost({
  ticker, onClose, priceSummary = [], swingSuggestions = [], allocations = [],
  classification = {},
}: {
  ticker: string | null; onClose: () => void;
  priceSummary?: PriceSummaryRow[]; swingSuggestions?: SwingSuggestion[];
  allocations?: Allocation[]; classification?: Record<string, Classification>;
}) {
  // Company-profile cache, fetched lazily per ticker from the metadata API.
  const [infoCache, setInfoCache] = useState<Record<string, TickerInfo>>({});
  // Live quote stats, fetched fresh each time a ticker opens (not cached client-side).
  const [quote, setQuote] = useState<QuoteStats | null>(null);

  useEffect(() => {
    if (!ticker || infoCache[ticker]) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(apiUrl(`/api/tickers/info?tickers=${encodeURIComponent(ticker)}`));
        if (res.ok && !cancelled) {
          const j = await res.json();
          setInfoCache((prev) => ({ ...prev, ...j.tickers }));
        }
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, [ticker, infoCache]);

  useEffect(() => {
    setQuote(null);
    if (!ticker) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(apiUrl(`/api/tickers/quote?tickers=${encodeURIComponent(ticker)}`));
        if (res.ok && !cancelled) {
          const j = await res.json();
          setQuote(j.quotes?.[ticker] ?? null);
        }
      } catch { /* ignore */ }
    })();
    return () => { cancelled = true; };
  }, [ticker]);

  if (!ticker) return null;

  const priceRow = priceSummary.find((r) => r.ticker === ticker) ?? null;
  const swing = swingSuggestions.find((s) => s.ticker === ticker) ?? null;
  const allocation = allocations.find((a) => a.ticker === ticker) ?? null;

  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', zIndex: 199 }} />
      <TickerDrawer
        ticker={ticker}
        info={infoCache[ticker] ?? null}
        classification={classification[ticker] ?? null}
        priceRow={priceRow}
        swing={swing}
        allocation={allocation}
        quote={quote}
        onClose={onClose}
      />
    </>
  );
}
