'use client';

import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { apiUrl } from '../lib/api';

type Range = '1D' | '3D' | '1W' | '1M' | '6M' | 'YTD' | '1Y' | '5Y';
const RANGES: Range[] = ['1D', '3D', '1W', '1M', '6M', 'YTD', '1Y', '5Y'];

type Point = { date: string; close: number };

const fmt = (v: number) =>
  v >= 1000 ? `$${v.toLocaleString(undefined, { maximumFractionDigits: 0 })}` : `$${v.toFixed(2)}`;

export default function PriceChart({ ticker, height = 180 }: { ticker: string; height?: number }) {
  const [range, setRange] = useState<Range>('3M' as Range);
  const [series, setSeries] = useState<Point[]>([]);
  const [loading, setLoading] = useState(false);
  const cache = useRef<Record<string, Point[]>>({});

  // Default to 1M on first render
  const [activeRange, setActiveRange] = useState<Range>('1M');

  const load = useCallback(async (r: Range) => {
    const key = `${ticker}:${r}`;
    if (cache.current[key]) {
      setSeries(cache.current[key]);
      return;
    }
    setLoading(true);
    try {
      const res = await fetch(apiUrl(`/api/prices/chart?ticker=${encodeURIComponent(ticker)}&range=${r}`));
      if (res.ok) {
        const j = await res.json();
        const pts: Point[] = j.series || [];
        cache.current[key] = pts;
        setSeries(pts);
      }
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, [ticker]);

  useEffect(() => { load(activeRange); }, [activeRange, load]);

  const first = series[0]?.close ?? 0;
  const last = series[series.length - 1]?.close ?? 0;
  const isUp = last >= first;
  const color = isUp ? '#10B981' : '#F43F5E';
  const pct = first > 0 ? ((last - first) / first * 100) : 0;

  const tickFmt = (date: string) => {
    if (!date) return '';
    // 5-min bars: "2026-06-22 14:35:00" → show time (or date+time for 3D/1W)
    if (activeRange === '1D') {
      const d = new Date(date.replace(' ', 'T'));
      return `${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`;
    }
    if (activeRange === '3D' || activeRange === '1W') {
      const d = new Date(date.replace(' ', 'T'));
      return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, '0')}`;
    }
    const [, m, d] = date.split('-');
    return `${m}/${d}`;
  };

  return (
    <div>
      {/* Range selector */}
      <div style={{ display: 'flex', gap: '4px', marginBottom: '8px', flexWrap: 'wrap' }}>
        {RANGES.map((r) => (
          <button
            key={r}
            onClick={() => setActiveRange(r)}
            style={{
              padding: '2px 8px', borderRadius: '4px', fontSize: '11px', fontWeight: 600,
              border: `1px solid ${activeRange === r ? color : 'var(--border-glass)'}`,
              background: activeRange === r ? `${color}22` : 'transparent',
              color: activeRange === r ? color : 'var(--text-secondary)',
              cursor: 'pointer',
            }}
          >
            {r}
          </button>
        ))}
        {series.length > 0 && (
          <span style={{ marginLeft: 'auto', fontSize: '11px', fontWeight: 700, color, alignSelf: 'center' }}>
            {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%
          </span>
        )}
      </div>

      {/* Chart */}
      <div style={{ height, position: 'relative' }}>
        {loading && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '12px', color: 'var(--text-secondary)' }}>
            Loading…
          </div>
        )}
        {!loading && series.length === 0 && (
          <div style={{ position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '12px', color: 'var(--text-secondary)' }}>
            No price data for {ticker} / {activeRange}
          </div>
        )}
        {series.length > 0 && (
          <ResponsiveContainer width="100%" height={height}>
            <AreaChart data={series} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id={`grad-${ticker}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={color} stopOpacity={0.25} />
                  <stop offset="95%" stopColor={color} stopOpacity={0} />
                </linearGradient>
              </defs>
              <XAxis dataKey="date" tickFormatter={tickFmt} tick={{ fontSize: 9, fill: '#64748b' }}
                tickLine={false} axisLine={false} interval="preserveStartEnd" minTickGap={40} />
              <YAxis domain={['auto', 'auto']} tickFormatter={fmt} tick={{ fontSize: 9, fill: '#64748b' }}
                tickLine={false} axisLine={false} width={52} />
              <Tooltip
                contentStyle={{ background: '#1e293b', border: '1px solid rgba(255,255,255,0.1)', fontSize: '12px', borderRadius: '6px' }}
                formatter={(v: number) => [fmt(v), ticker]}
                labelFormatter={(l) => l}
              />
              <Area type="monotone" dataKey="close" stroke={color} strokeWidth={1.5}
                fill={`url(#grad-${ticker})`} dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
