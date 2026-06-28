'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { apiUrl } from '../lib/api';

type Range = '1W' | '1M' | '3M' | '6M' | 'YTD' | '1Y' | '5Y' | '10Y';
const RANGES: Range[] = ['1W', '1M', '3M', '6M', 'YTD', '1Y', '5Y', '10Y'];

type Point = { ts: string; date: string; phase: string; total: number; [account: string]: number | string };

// Shaded palette for stacked per-account areas.
const PALETTE = ['#00f2fe', '#7c5cff', '#10b981', '#f59e0b', '#f43f5e', '#38bdf8', '#a3e635'];

const fmtMoney = (v: number) =>
  v >= 1000
    ? `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}`
    : `$${v.toFixed(2)}`;

export default function PortfolioHistoryChart({
  accountLabel = 'consolidated',
  height = 280,
}: {
  accountLabel?: string;
  height?: number;
}) {
  const [activeRange, setActiveRange] = useState<Range>('3M');
  const [accounts, setAccounts] = useState<string[]>([]);
  const [series, setSeries] = useState<Point[]>([]);
  const [loading, setLoading] = useState(false);
  const cache = useRef<Record<string, { accounts: string[]; series: Point[] }>>({});

  const load = useCallback(async (r: Range) => {
    const key = `${accountLabel}:${r}`;
    if (cache.current[key]) {
      setAccounts(cache.current[key].accounts);
      setSeries(cache.current[key].series);
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(
        apiUrl(`/api/external/portfolio/history?range=${r}&account_label=${encodeURIComponent(accountLabel)}`),
      );
      if (res.ok) {
        const j = await res.json();
        const payload = { accounts: j.accounts || [], series: j.series || [] };
        cache.current[key] = payload;
        setAccounts(payload.accounts);
        setSeries(payload.series);
      }
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, [accountLabel]);

  // Refetch when the selected account changes; clear cache so stale account data isn't shown.
  useEffect(() => {
    cache.current = {};
    load(activeRange);
  }, [accountLabel, activeRange, load]);

  const first = series[0]?.total ?? 0;
  const last = series[series.length - 1]?.total ?? 0;
  const isUp = last >= first;
  const trendColor = isUp ? '#10b981' : '#f43f5e';
  const pct = first > 0 ? ((last - first) / first) * 100 : 0;
  const delta = last - first;

  const tickFmt = useCallback((ts: string) => {
    if (!ts) return '';
    const [date] = ts.split('T');
    const [, m, d] = date.split('-');
    if (activeRange === '10Y' || activeRange === '5Y' || activeRange === '1Y') {
      const [y] = date.split('-');
      return `${m}/${y.slice(2)}`;
    }
    return `${m}/${d}`;
  }, [activeRange]);

  const colorFor = useMemo(() => {
    const map: Record<string, string> = {};
    accounts.forEach((a, i) => { map[a] = PALETTE[i % PALETTE.length]; });
    return map;
  }, [accounts]);

  const renderTooltip = useCallback((props: any) => {
    const { active, payload, label } = props;
    if (!active || !payload || !payload.length) return null;
    const pt: Point = payload[0]?.payload;
    const phaseLabel = pt?.phase ? ` · ${pt.phase}` : '';
    const date = typeof label === 'string' ? label.split('T')[0] : label;
    return (
      <div style={{ background: '#1e293b', border: '1px solid rgba(255,255,255,0.1)', borderRadius: '6px', padding: '8px 10px', fontSize: '12px' }}>
        <div style={{ color: '#94a3b8', marginBottom: '4px' }}>{date}{phaseLabel}</div>
        {accounts.map((a) => (
          <div key={a} style={{ display: 'flex', justifyContent: 'space-between', gap: '14px', color: 'var(--text-primary)' }}>
            <span style={{ color: colorFor[a] }}>{a}</span>
            <span style={{ fontWeight: 600 }}>{fmtMoney(Number(pt?.[a] ?? 0))}</span>
          </div>
        ))}
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '14px', marginTop: '4px', paddingTop: '4px', borderTop: '1px solid rgba(255,255,255,0.1)', color: 'var(--text-primary)' }}>
          <span style={{ fontWeight: 700 }}>Total</span>
          <span style={{ fontWeight: 700 }}>{fmtMoney(Number(pt?.total ?? 0))}</span>
        </div>
      </div>
    );
  }, [accounts, colorFor]);

  return (
    <div className="glass-card" style={{ padding: '20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '12px', marginBottom: '12px' }}>
        <div>
          <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 600, color: 'var(--text-primary)' }}>Portfolio Value Over Time</h3>
          <p style={{ margin: '4px 0 0', fontSize: '12px', color: 'var(--text-secondary)' }}>
            Three points per trading day (open · mid · close), stacked by account. Reconstructed from current holdings.
          </p>
        </div>
        {series.length > 0 && (
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: '22px', fontWeight: 700, color: 'var(--text-primary)' }}>{fmtMoney(last)}</div>
            <div style={{ fontSize: '13px', fontWeight: 600, color: trendColor }}>
              {delta >= 0 ? '+' : ''}{fmtMoney(delta)} ({pct >= 0 ? '+' : ''}{pct.toFixed(2)}%)
            </div>
          </div>
        )}
      </div>

      {/* Range selector */}
      <div style={{ display: 'flex', gap: '4px', marginBottom: '12px', flexWrap: 'wrap' }}>
        {RANGES.map((r) => (
          <button
            key={r}
            onClick={() => setActiveRange(r)}
            style={{
              padding: '3px 10px', borderRadius: '4px', fontSize: '11px', fontWeight: 600,
              border: `1px solid ${activeRange === r ? trendColor : 'var(--border-glass)'}`,
              background: activeRange === r ? `${trendColor}22` : 'transparent',
              color: activeRange === r ? trendColor : 'var(--text-secondary)',
              cursor: 'pointer',
            }}
          >
            {r}
          </button>
        ))}
      </div>

      {/* Legend */}
      {accounts.length > 0 && (
        <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap', marginBottom: '8px' }}>
          {accounts.map((a) => (
            <div key={a} style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px', color: 'var(--text-secondary)' }}>
              <span style={{ width: '10px', height: '10px', borderRadius: '2px', background: colorFor[a] }} />
              {a}
            </div>
          ))}
        </div>
      )}

      {/* Chart */}
      <div style={{ height, position: 'relative' }}>
        {loading && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '12px', color: 'var(--text-secondary)' }}>
            Loading…
          </div>
        )}
        {!loading && series.length === 0 && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '12px', color: 'var(--text-secondary)' }}>
            No portfolio history for this range
          </div>
        )}
        {series.length > 0 && (
          <ResponsiveContainer width="100%" height={height}>
            <AreaChart data={series} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
              <defs>
                {accounts.map((a) => (
                  <linearGradient key={a} id={`pf-grad-${a.replace(/[^a-zA-Z0-9]/g, '')}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={colorFor[a]} stopOpacity={0.55} />
                    <stop offset="95%" stopColor={colorFor[a]} stopOpacity={0.08} />
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />
              <XAxis dataKey="ts" tickFormatter={tickFmt} tick={{ fontSize: 9, fill: '#64748b' }}
                tickLine={false} axisLine={false} interval="preserveStartEnd" minTickGap={40} />
              <YAxis domain={['auto', 'auto']} tickFormatter={fmtMoney} tick={{ fontSize: 9, fill: '#64748b' }}
                tickLine={false} axisLine={false} width={56} />
              <Tooltip content={renderTooltip} />
              {accounts.map((a) => (
                <Area
                  key={a}
                  type="monotone"
                  dataKey={a}
                  stackId="pf"
                  stroke={colorFor[a]}
                  strokeWidth={1}
                  fill={`url(#pf-grad-${a.replace(/[^a-zA-Z0-9]/g, '')})`}
                  dot={false}
                  isAnimationActive={false}
                />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
