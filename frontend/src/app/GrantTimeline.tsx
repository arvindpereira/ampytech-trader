'use client';

import React, { useState, useEffect, useMemo } from 'react';
import {
  ResponsiveContainer,
  ComposedChart,
  AreaChart,
  Area,
  Line,
  Scatter,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
} from 'recharts';

export interface GrantTimelineProps {
  ticker: string;
  apiBase?: string;
}

interface SeriesPoint {
  date: string;
  price: number;
  avg_basis: number;
  shares_held: number;
  profitable_pct: number;
  underwater_pct: number;
  gain_pct: number;
  is_grant: boolean;
}

interface GrantMarker {
  date: string;
  shares: number;
  basis: number;
  price_at_grant: number | null;
  lot_type: string;
}

interface Summary {
  ticker: string;
  current_price: number;
  avg_basis: number;
  total_shares: number;
  market_value: number;
  cost_value: number;
  unrealized_gain: number;
  unrealized_gain_pct: number;
  profitable_pct: number;
  num_grants: number;
  first_grant: string;
  as_of: string;
}

interface TimelineResponse {
  summary: Summary;
  series: SeriesPoint[];
  grants: GrantMarker[];
}

const GREEN = '#10B981';
const RED = '#EF4444';
const PRICE = '#22D3EE';
const BASIS = '#F59E0B';

const fmtUsd = (n: number | null | undefined, dp = 0) =>
  (n == null || isNaN(n)) ? '—' : n.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: dp });
const fmtNum = (n: number | null | undefined) =>
  (n == null || isNaN(n)) ? '—' : n.toLocaleString();
const fmtPct = (n: number | null | undefined) =>
  (n == null || isNaN(n)) ? '—' : `${n > 0 ? '+' : ''}${n}%`;
const fmtDate = (t: number) =>
  new Date(t).toLocaleDateString(undefined, { year: '2-digit', month: 'short' });
const fmtDateFull = (t: number) =>
  new Date(t).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });

/**
 * Reusable per-stock grant deep-dive. Three time-aligned panels:
 *   1. Market price vs. running weighted-average cost basis, with grant markers (sized by shares).
 *   2. Unrealized gain/loss % over time (green above the basis line, red below).
 *   3. Share of granted shares that are in profit vs underwater (stacked green/red, 0-100%).
 * Drop it anywhere with a ticker that has recorded equity lots/grants.
 */
export default function GrantTimeline({ ticker, apiBase = 'http://localhost:8008' }: GrantTimelineProps) {
  const [data, setData] = useState<TimelineResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetch(`${apiBase}/api/equity/grant-timeline/${encodeURIComponent(ticker)}`)
      .then(async (r) => {
        if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || `HTTP ${r.status}`);
        return r.json();
      })
      .then((d) => { if (!cancelled) setData(d); })
      .catch((e) => { if (!cancelled) setError(String(e.message || e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [ticker, apiBase]);

  // Merge the price series and the grant markers onto one numeric (timestamp) x-axis so the grant
  // scatter dots align exactly with the lines even when grants fall between downsampled points.
  const merged = useMemo(() => {
    if (!data) return [];
    const byT = new Map<number, any>();
    for (const p of data.series) {
      const t = new Date(p.date).getTime();
      byT.set(t, { t, ...p });
    }
    for (const g of data.grants) {
      const t = new Date(g.date).getTime();
      const row = byT.get(t) || { t, date: g.date };
      row.grantBasis = g.basis;
      row.grantShares = g.shares;
      row.grantLotType = g.lot_type;
      row.priceAtGrant = g.price_at_grant;
      byT.set(t, row);
    }
    // Some grant dates land on weekends/holidays with no price bar, so they arrive as grant-only rows
    // (no price/basis). Forward-fill the line fields from the prior trading day (using the grant's own
    // market price when available) so the lines stay continuous and nothing is undefined downstream.
    const arr = Array.from(byT.values()).sort((a, b) => a.t - b.t);
    let last: any = {};
    for (const r of arr) {
      if (r.price == null) r.price = r.priceAtGrant ?? last.price ?? null;
      if (r.avg_basis == null) r.avg_basis = last.avg_basis ?? null;
      if (r.shares_held == null) r.shares_held = last.shares_held ?? null;
      if (r.profitable_pct == null) r.profitable_pct = last.profitable_pct ?? null;
      if (r.underwater_pct == null) r.underwater_pct = last.underwater_pct ?? null;
      if (r.gain_pct == null && r.price != null && r.avg_basis)
        r.gain_pct = Math.round((r.price / r.avg_basis - 1) * 1000) / 10;
      if (r.gain_pct == null) r.gain_pct = last.gain_pct ?? null;
      last = {
        price: r.price, avg_basis: r.avg_basis, shares_held: r.shares_held,
        profitable_pct: r.profitable_pct, underwater_pct: r.underwater_pct, gain_pct: r.gain_pct,
      };
    }
    return arr;
  }, [data]);

  // Gradient split point (fraction from top) where gain_pct crosses zero — green above, red below.
  const gainOffset = useMemo(() => {
    if (!merged.length) return 0.5;
    const vals = merged.map((d) => d.gain_pct).filter((v) => v != null) as number[];
    const max = Math.max(...vals, 0);
    const min = Math.min(...vals, 0);
    if (max <= 0) return 0;
    if (min >= 0) return 1;
    return max / (max - min);
  }, [merged]);

  if (loading) return <Shell ticker={ticker}><Muted>Loading grant history…</Muted></Shell>;
  if (error) return <Shell ticker={ticker}><Muted style={{ color: RED }}>Could not load: {error}</Muted></Shell>;
  if (!data || !merged.length) return <Shell ticker={ticker}><Muted>No grant/price history.</Muted></Shell>;

  const s = data.summary;
  const gainPositive = s.unrealized_gain >= 0;
  const xDomain = [merged[0].t, merged[merged.length - 1].t];
  const xAxisCommon = {
    dataKey: 't' as const,
    type: 'number' as const,
    domain: xDomain as [number, number],
    scale: 'time' as const,
    tickFormatter: fmtDate,
    stroke: 'var(--text-secondary)',
    tick: { fontSize: 10 },
  };

  return (
    <Shell ticker={ticker}>
      {/* Summary stat row */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '20px', marginBottom: '14px' }}>
        <Stat label="Shares held" value={fmtNum(s.total_shares)} />
        <Stat label="Avg cost basis" value={fmtUsd(s.avg_basis, 2)} color={BASIS} />
        <Stat label="Market price" value={fmtUsd(s.current_price, 2)} color={PRICE} />
        <Stat
          label="Unrealized P/L"
          value={`${gainPositive ? '+' : ''}${fmtUsd(s.unrealized_gain)} (${s.unrealized_gain_pct > 0 ? '+' : ''}${s.unrealized_gain_pct}%)`}
          color={gainPositive ? GREEN : RED}
        />
        <Stat label="Shares in profit" value={`${s.profitable_pct}%`} color={s.profitable_pct >= 50 ? GREEN : RED} />
        <Stat label="Grants" value={`${s.num_grants} · since ${s.first_grant}`} />
      </div>

      {/* Panel 1 — Price vs. weighted-average cost basis, with grant markers */}
      <PanelTitle>Market price vs. average cost basis</PanelTitle>
      <ResponsiveContainer width="100%" height={230}>
        <ComposedChart data={merged} margin={{ top: 6, right: 12, left: 4, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
          <XAxis {...xAxisCommon} />
          <YAxis stroke="var(--text-secondary)" tick={{ fontSize: 10 }} width={48}
            tickFormatter={(v) => `$${v}`} domain={['auto', 'auto']} />
          <Tooltip content={<PriceTooltip />} />
          <Line type="monotone" dataKey="price" name="Market price" stroke={PRICE} strokeWidth={2}
            dot={false} isAnimationActive={false} />
          <Line type="stepAfter" dataKey="avg_basis" name="Avg cost basis" stroke={BASIS} strokeWidth={1.5}
            strokeDasharray="5 4" dot={false} isAnimationActive={false} />
          <Scatter dataKey="grantBasis" name="Grant" isAnimationActive={false} shape={<GrantDot />} />
        </ComposedChart>
      </ResponsiveContainer>

      {/* Panel 2 — Unrealized gain/loss % over time (above/below the basis line) */}
      <PanelTitle>How far above / below the line (unrealized %)</PanelTitle>
      <ResponsiveContainer width="100%" height={140}>
        <AreaChart data={merged} margin={{ top: 6, right: 12, left: 4, bottom: 0 }}>
          <defs>
            <linearGradient id="gainSplit" x1="0" y1="0" x2="0" y2="1">
              <stop offset={0} stopColor={GREEN} stopOpacity={0.7} />
              <stop offset={gainOffset} stopColor={GREEN} stopOpacity={0.15} />
              <stop offset={gainOffset} stopColor={RED} stopOpacity={0.15} />
              <stop offset={1} stopColor={RED} stopOpacity={0.7} />
            </linearGradient>
            <linearGradient id="gainStroke" x1="0" y1="0" x2="0" y2="1">
              <stop offset={gainOffset} stopColor={GREEN} />
              <stop offset={gainOffset} stopColor={RED} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
          <XAxis {...xAxisCommon} />
          <YAxis stroke="var(--text-secondary)" tick={{ fontSize: 10 }} width={48}
            tickFormatter={(v) => `${v}%`} />
          <Tooltip content={<GainTooltip />} />
          <ReferenceLine y={0} stroke="var(--text-secondary)" strokeOpacity={0.5} />
          <Area type="monotone" dataKey="gain_pct" name="Unrealized %" stroke="url(#gainStroke)"
            strokeWidth={1.5} fill="url(#gainSplit)" isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>

      {/* Panel 3 — Ratio of granted shares profitable vs underwater */}
      <PanelTitle>Ratio of granted shares profitable (green) vs. at a loss (red)</PanelTitle>
      <ResponsiveContainer width="100%" height={140}>
        <AreaChart data={merged} margin={{ top: 6, right: 12, left: 4, bottom: 4 }} stackOffset="expand">
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
          <XAxis {...xAxisCommon} />
          <YAxis stroke="var(--text-secondary)" tick={{ fontSize: 10 }} width={48}
            tickFormatter={(v) => `${Math.round(v * 100)}%`} domain={[0, 1]} />
          <Tooltip content={<RatioTooltip />} />
          <Area type="monotone" dataKey="profitable_pct" name="In profit" stackId="r" stroke={GREEN}
            fill={GREEN} fillOpacity={0.55} strokeWidth={0} isAnimationActive={false} />
          <Area type="monotone" dataKey="underwater_pct" name="At a loss" stackId="r" stroke={RED}
            fill={RED} fillOpacity={0.45} strokeWidth={0} isAnimationActive={false} />
        </AreaChart>
      </ResponsiveContainer>

      <div style={{ display: 'flex', gap: '16px', marginTop: '8px', flexWrap: 'wrap' }}>
        <LegendDot color={PRICE} label="Market price" />
        <LegendDot color={BASIS} label="Avg cost basis (stepped)" />
        <LegendDot color="#A78BFA" label="Grant (dot size = shares)" />
        <LegendDot color={GREEN} label="Profitable" />
        <LegendDot color={RED} label="At a loss" />
      </div>
    </Shell>
  );
}

// Grant scatter dot: radius scales with share count; purple ring for visibility over the lines.
function GrantDot(props: any) {
  const { cx, cy, payload } = props;
  if (cx == null || cy == null || payload?.grantBasis == null) return null;
  const sh = payload.grantShares || 0;
  const r = Math.max(3, Math.min(9, 3 + Math.sqrt(sh) / 6));
  return (
    <g>
      <circle cx={cx} cy={cy} r={r} fill="#A78BFA" fillOpacity={0.85} stroke="#fff" strokeWidth={0.8} />
    </g>
  );
}

function PriceTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <TooltipBox date={p.t}>
      <Row label="Market price" value={fmtUsd(p.price, 2)} color={PRICE} />
      <Row label="Avg cost basis" value={fmtUsd(p.avg_basis, 2)} color={BASIS} />
      <Row label="Shares held" value={fmtNum(p.shares_held)} />
      <Row label="Unrealized" value={fmtPct(p.gain_pct)} color={(p.gain_pct ?? 0) >= 0 ? GREEN : RED} />
      {p.grantShares != null && (
        <Row label={`Grant (${p.grantLotType?.toUpperCase?.() || ''})`}
          value={`${fmtNum(p.grantShares)} sh @ ${fmtUsd(p.grantBasis, 2)}`} color="#A78BFA" />
      )}
    </TooltipBox>
  );
}

function GainTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <TooltipBox date={p.t}>
      <Row label="Unrealized" value={fmtPct(p.gain_pct)} color={(p.gain_pct ?? 0) >= 0 ? GREEN : RED} />
    </TooltipBox>
  );
}

function RatioTooltip({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <TooltipBox date={p.t}>
      <Row label="In profit" value={p.profitable_pct == null ? '—' : `${p.profitable_pct}%`} color={GREEN} />
      <Row label="At a loss" value={p.underwater_pct == null ? '—' : `${p.underwater_pct}%`} color={RED} />
    </TooltipBox>
  );
}

function TooltipBox({ date, children }: { date: number; children: React.ReactNode }) {
  return (
    <div style={{
      background: 'rgba(15,18,28,0.95)', border: '1px solid var(--border-glass)', borderRadius: '8px',
      padding: '8px 10px', fontSize: '12px', boxShadow: '0 6px 20px rgba(0,0,0,0.4)',
    }}>
      <div style={{ color: 'var(--text-secondary)', marginBottom: '4px', fontWeight: 600 }}>{fmtDateFull(date)}</div>
      {children}
    </div>
  );
}

function Row({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', gap: '14px' }}>
      <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
      <span style={{ color: color || 'var(--text-primary)', fontWeight: 600 }}>{value}</span>
    </div>
  );
}

function Shell({ ticker, children }: { ticker: string; children: React.ReactNode }) {
  return (
    <div style={{
      background: 'var(--bg-glass, rgba(255,255,255,0.02))', border: '1px solid var(--border-glass)',
      borderRadius: '12px', padding: '18px',
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '10px', marginBottom: '12px' }}>
        <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 700, color: 'var(--text-primary)' }}>{ticker}</h3>
        <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>grant deep-dive</span>
      </div>
      {children}
    </div>
  );
}

function PanelTitle({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ fontSize: '11px', fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase',
      color: 'var(--text-secondary)', margin: '10px 0 2px' }}>
      {children}
    </div>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div>
      <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '2px' }}>{label}</div>
      <div style={{ fontSize: '15px', fontWeight: 700, color: color || 'var(--text-primary)' }}>{value}</div>
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: 'var(--text-secondary)' }}>
      <span style={{ width: '10px', height: '10px', borderRadius: '50%', background: color }} />
      {label}
    </span>
  );
}

function Muted({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return <div style={{ color: 'var(--text-secondary)', fontSize: '13px', padding: '24px 0', ...style }}>{children}</div>;
}
