'use client';

import React, { useCallback, useEffect, useState } from 'react';
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { apiUrl } from '../lib/api';

type Curve = {
  label: string;
  equity_curve: number[];
  total_return: number;
  max_drawdown: number;
  sharpe: number;
  turnover: number;
};
type Wf = {
  dates: string[];
  benchmark: Curve;
  series: Curve[];
  defense_mix?: Record<string, number>;
  coverage?: { real_weight?: number; proxy_weight?: number };
  start_date?: string;
  end_date?: string;
};

const COLORS: Record<string, string> = {
  conservative: '#10B981', balanced: '#3B82F6', aggressive: '#F59E0B', custom: '#A855F7',
};

export default function CrashRebalanceWalkforward({
  mode, preset, theta, k, gamma,
}: {
  mode: 'paper' | 'live';
  preset: string;
  theta: number;
  k: number;
  gamma: number;
}) {
  const [data, setData] = useState<Wf | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError(null);
    try {
      const knobs = preset === 'custom' ? `&theta=${theta}&k=${k}&gamma=${gamma}` : '';
      const res = await fetch(apiUrl(`/api/crash/rebalance/walkforward?mode=${mode}&preset=${preset}&years=5${knobs}`));
      if (res.ok) setData(await res.json());
      else { const e = await res.json().catch(() => ({})); setError(e.detail || res.statusText); }
    } catch (e: any) { setError(e.message || String(e)); }
    finally { setLoading(false); }
  }, [mode, preset, theta, k, gamma]);

  // Only fetch once expanded (it's a heavier backtest).
  useEffect(() => { if (open) load(); }, [open, load]);

  const rows: Curve[] = data ? [...data.series, data.benchmark] : [];

  return (
    <div style={{ border: '1px solid var(--border-glass)', borderRadius: '10px', padding: '14px', background: 'rgba(255,255,255,0.02)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px' }}>
        <div>
          <strong style={{ fontSize: '13.5px', color: 'var(--text-primary)' }}>Walk-forward: would de-risking have helped?</strong>
          <p style={{ fontSize: '10.5px', color: 'var(--text-secondary)', margin: '3px 0 0', lineHeight: 1.4 }}>
            Backtests the glide-path de-risk policy on <strong>your current holdings</strong> (steered by the real historical
            crash index) vs holding through. Uses current holdings as a static sleeve — older returns carry survivorship caveats.
          </p>
        </div>
        <button onClick={() => setOpen(o => !o)} className="toggle-btn" style={{ fontSize: '11.5px', padding: '4px 12px', whiteSpace: 'nowrap' }}>
          {open ? 'Hide' : 'Run backtest'}
        </button>
      </div>

      {open && (
        <div style={{ marginTop: '12px' }}>
          {loading && <div style={{ fontSize: '12px', color: 'var(--text-secondary)', padding: '20px 0', textAlign: 'center' }}>Running 5-year walk-forward…</div>}
          {error && <div style={{ fontSize: '12px', color: '#EF4444' }}>Could not run backtest: {error}</div>}

          {data && !loading && (
            <>
              <div style={{ width: '100%', height: '220px' }}>
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart
                    data={(data.dates || []).map((d, i) => {
                      const row: any = { date: d };
                      data.series.forEach(s => { row[s.label] = s.equity_curve[i]; });
                      row['Buy & Hold'] = data.benchmark.equity_curve[i];
                      return row;
                    })}
                    margin={{ top: 5, right: 10, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                    <XAxis dataKey="date" tick={{ fontSize: 9, fill: '#64748b' }} minTickGap={40} tickLine={false} axisLine={false} />
                    <YAxis tick={{ fontSize: 9, fill: '#64748b' }} tickFormatter={(v) => `$${(v / 1000).toFixed(0)}k`} width={44} tickLine={false} axisLine={false} />
                    <Tooltip contentStyle={{ background: '#1e293b', border: '1px solid rgba(255,255,255,0.1)', fontSize: '11px', borderRadius: '6px' }}
                      formatter={(v: any, n: any) => [v != null ? `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })}` : '—', n]} />
                    <Legend wrapperStyle={{ fontSize: '10px' }} />
                    {data.series.map(s => (
                      <Line key={s.label} type="monotone" dataKey={s.label} stroke={COLORS[s.label] || '#888'} dot={false} strokeWidth={1.6} />
                    ))}
                    <Line type="monotone" dataKey="Buy & Hold" stroke="#9CA3AF" strokeDasharray="5 4" dot={false} strokeWidth={1.5} />
                  </LineChart>
                </ResponsiveContainer>
              </div>

              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '11.5px', marginTop: '8px' }}>
                <thead>
                  <tr style={{ textAlign: 'right', color: 'var(--text-secondary)' }}>
                    <th style={{ textAlign: 'left', padding: '4px 6px' }}>Policy</th>
                    <th style={{ padding: '4px 6px' }}>Return</th>
                    <th style={{ padding: '4px 6px' }}>Max DD</th>
                    <th style={{ padding: '4px 6px' }}>Sharpe</th>
                    <th style={{ padding: '4px 6px' }}>Turnover</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((s, i) => (
                    <tr key={i} style={{ borderTop: '1px solid var(--border-glass)', textAlign: 'right' }}>
                      <td style={{ textAlign: 'left', padding: '4px 6px', fontWeight: 600, color: COLORS[s.label] || 'var(--text-primary)', textTransform: 'capitalize' }}>{s.label}</td>
                      <td style={{ padding: '4px 6px' }}>{s.total_return.toFixed(1)}%</td>
                      <td style={{ padding: '4px 6px', color: '#EF4444' }}>{s.max_drawdown.toFixed(1)}%</td>
                      <td style={{ padding: '4px 6px' }}>{s.sharpe.toFixed(2)}</td>
                      <td style={{ padding: '4px 6px' }}>{s.turnover.toFixed(1)}x</td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {data.coverage && (
                <div style={{ fontSize: '10px', color: 'var(--text-secondary)', marginTop: '6px' }}>
                  Basket price coverage: {Math.round((data.coverage.real_weight || 0) * 100)}% real,
                  {' '}{Math.round((data.coverage.proxy_weight || 0) * 100)}% beta-proxy ·
                  {' '}Defense: {data.defense_mix && Object.entries(data.defense_mix).map(([t, w]) => `${t} ${Math.round(w * 100)}%`).join(' / ')}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
