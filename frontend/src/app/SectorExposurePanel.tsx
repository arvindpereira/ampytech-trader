'use client';

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { AlertTriangle, ChevronDown, ChevronRight, RefreshCw } from 'lucide-react';
import { apiUrl } from '../lib/api';

type SectorRow = {
  sector: string;
  portfolio_weight: number;
  benchmark_weight: number;
  delta: number;
  alert: boolean;
  market_value: number;
  industries: Array<{ industry: string; portfolio_weight: number }>;
  holdings: Array<{
    ticker: string;
    portfolio_weight: number;
    market_value: number;
    industry?: string;
    revenue_driver?: string;
    accounts?: string[];
  }>;
};

type ExposureData = {
  as_of?: string;
  total_equity_value?: number;
  alert_threshold_pp?: number;
  benchmark?: { name?: string; as_of?: string; source?: string };
  sectors?: SectorRow[];
  alerts?: Array<{
    sector: string;
    direction: string;
    delta_pct: number;
    message: string;
  }>;
  unclassified?: Array<{ ticker: string; weight: number }>;
  error?: string;
};

const money = (n: number) =>
  n >= 1e6 ? `$${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `$${(n / 1e3).toFixed(1)}K` : `$${n.toFixed(0)}`;

const pct = (w: number) => `${(w * 100).toFixed(1)}%`;

function deltaColor(delta: number): string {
  if (delta >= 0.05) return '#EF4444';
  if (delta <= -0.05) return '#3B82F6';
  if (delta > 0.02) return '#F59E0B';
  if (delta < -0.02) return '#60A5FA';
  return 'rgba(255,255,255,0.15)';
}

export default function SectorExposurePanel() {
  const [data, setData] = useState<ExposureData | null>(null);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(apiUrl('/api/portfolio/sector-exposure?mode=real'));
      if (res.ok) setData(await res.json());
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const chartData = useMemo(() => {
    return (data?.sectors || [])
      .filter((s) => s.portfolio_weight > 0 || s.benchmark_weight > 0)
      .map((s) => ({
        sector: s.sector.replace('Consumer ', 'Cons. ').replace('Financial ', 'Fin. '),
        fullSector: s.sector,
        portfolio: +(s.portfolio_weight * 100).toFixed(1),
        benchmark: +(s.benchmark_weight * 100).toFixed(1),
        delta: +(s.delta * 100).toFixed(1),
        alert: s.alert,
      }));
  }, [data]);

  const heatmapRows = useMemo(() => {
    return (data?.sectors || []).filter((s) => s.portfolio_weight > 0.001 || s.benchmark_weight > 0);
  }, [data]);

  if (loading && !data) {
    return (
      <div className="glass-card" style={{ padding: '24px', color: 'var(--text-secondary)', fontSize: '13px' }}>
        Loading sector exposure…
      </div>
    );
  }

  if (data?.error === 'no_priced_holdings') {
    return (
      <div className="glass-card" style={{ padding: '24px', color: 'var(--text-secondary)', fontSize: '13px' }}>
        No priced holdings found for sector exposure analysis.
      </div>
    );
  }

  return (
    <div className="glass-card" style={{ padding: '24px', display: 'grid', gap: '18px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 600 }}>Sector Exposure Simulator</h3>
          <p style={{ margin: '6px 0 0', fontSize: '12px', color: 'var(--text-secondary)', maxWidth: '640px', lineHeight: 1.45 }}>
            Consolidated trading + external accounts mapped to GICS sectors. Compared to{' '}
            {data?.benchmark?.name || 'S&P 500'} weights ({data?.benchmark?.as_of || 'benchmark'}).
            Alerts fire when deviation exceeds {data?.alert_threshold_pp ?? 5}pp.
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="toggle-btn"
          style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px' }}
        >
          <RefreshCw size={14} className={loading ? 'spin' : ''} /> Refresh
        </button>
      </div>

      {data?.alerts && data.alerts.length > 0 && (
        <div style={{ display: 'grid', gap: '8px' }}>
          {data.alerts.map((a) => (
            <div
              key={a.sector}
              style={{
                display: 'flex',
                gap: '10px',
                alignItems: 'flex-start',
                padding: '10px 12px',
                borderRadius: '8px',
                background: 'rgba(239, 68, 68, 0.08)',
                border: '1px solid rgba(239, 68, 68, 0.25)',
                fontSize: '12.5px',
              }}
            >
              <AlertTriangle size={16} color="#EF4444" style={{ flexShrink: 0, marginTop: '1px' }} />
              <span>{a.message}</span>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 280px', gap: '16px', alignItems: 'stretch' }}>
        <div style={{ minHeight: 280 }}>
          <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
            Portfolio vs benchmark sector weights (%)
          </div>
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 60 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
              <XAxis dataKey="sector" tick={{ fontSize: 10, fill: '#94a3b8' }} angle={-35} textAnchor="end" interval={0} height={70} />
              <YAxis tick={{ fontSize: 10, fill: '#94a3b8' }} unit="%" />
              <Tooltip
                contentStyle={{ background: '#1e293b', border: '1px solid rgba(255,255,255,0.1)', fontSize: '12px' }}
                formatter={(v: number) => [`${v}%`, '']}
              />
              <Legend wrapperStyle={{ fontSize: '11px' }} />
              <Bar dataKey="portfolio" name="Your portfolio" fill="#00F2FE" radius={[3, 3, 0, 0]} />
              <Bar dataKey="benchmark" name="S&P 500" fill="rgba(148,163,184,0.5)" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div>
          <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
            Active tilt heatmap (Δ vs benchmark)
          </div>
          <div style={{ display: 'grid', gap: '6px' }}>
            {heatmapRows.slice(0, 11).map((s) => (
              <div key={s.sector} style={{ display: 'grid', gridTemplateColumns: '1fr 48px', gap: '8px', alignItems: 'center' }}>
                <div
                  title={`${s.sector}: ${pct(s.portfolio_weight)} vs ${pct(s.benchmark_weight)}`}
                  style={{
                    height: '22px',
                    borderRadius: '4px',
                    background: deltaColor(s.delta),
                    opacity: s.alert ? 1 : 0.65,
                    border: s.alert ? '1px solid rgba(255,255,255,0.35)' : 'none',
                  }}
                />
                <span style={{ fontSize: '10px', color: s.alert ? '#FCA5A5' : 'var(--text-secondary)', textAlign: 'right' }}>
                  {s.delta >= 0 ? '+' : ''}{(s.delta * 100).toFixed(1)}pp
                </span>
              </div>
            ))}
          </div>
          <div style={{ fontSize: '10px', color: 'var(--text-secondary)', marginTop: '10px', lineHeight: 1.4 }}>
            Red = overweight · Blue = underweight · Bold border = alert (&gt;5pp)
          </div>
        </div>
      </div>

      <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
        Total equity analyzed: <strong style={{ color: 'var(--text-primary)' }}>{money(data?.total_equity_value || 0)}</strong>
        {data?.unclassified && data.unclassified.length > 0 && (
          <span> · Unclassified: {data.unclassified.map((u) => u.ticker).join(', ')}</span>
        )}
      </div>

      <div>
        <div style={{ fontSize: '13px', fontWeight: 600, marginBottom: '10px' }}>Drill-down by sector</div>
        <div style={{ display: 'grid', gap: '8px' }}>
          {(data?.sectors || [])
            .filter((s) => s.holdings.length > 0)
            .map((s) => {
              const open = expanded[s.sector];
              return (
                <div key={s.sector} style={{ border: '1px solid var(--border-glass)', borderRadius: '8px', overflow: 'hidden' }}>
                  <button
                    type="button"
                    onClick={() => setExpanded((prev) => ({ ...prev, [s.sector]: !prev[s.sector] }))}
                    style={{
                      width: '100%',
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '10px 12px',
                      background: 'rgba(255,255,255,0.02)',
                      border: 'none',
                      color: 'var(--text-primary)',
                      cursor: 'pointer',
                      fontSize: '13px',
                    }}
                  >
                    <span style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      <strong>{s.sector}</strong>
                      <span style={{ color: 'var(--text-secondary)', fontWeight: 400 }}>
                        {pct(s.portfolio_weight)} · Δ {(s.delta * 100).toFixed(1)}pp
                      </span>
                      {s.alert && (
                        <span style={{ fontSize: '10px', color: '#FCA5A5', fontWeight: 600 }}>ALERT</span>
                      )}
                    </span>
                    <span style={{ color: 'var(--text-secondary)', fontSize: '12px' }}>{money(s.market_value)}</span>
                  </button>
                  {open && (
                    <div style={{ padding: '0 12px 12px' }}>
                      {s.industries.length > 0 && (
                        <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
                          Industry groups:{' '}
                          {s.industries.map((i) => `${i.industry} (${pct(i.portfolio_weight)})`).join(' · ')}
                        </div>
                      )}
                      <table className="trade-table" style={{ width: '100%', fontSize: '12px' }}>
                        <thead>
                          <tr>
                            <th>Ticker</th>
                            <th>Weight</th>
                            <th>Value</th>
                            <th>Industry</th>
                            <th>Revenue driver</th>
                          </tr>
                        </thead>
                        <tbody>
                          {s.holdings.map((h) => (
                            <tr key={h.ticker}>
                              <td style={{ fontWeight: 700 }}>{h.ticker}</td>
                              <td>{pct(h.portfolio_weight)}</td>
                              <td>{money(h.market_value)}</td>
                              <td style={{ color: 'var(--text-secondary)' }}>{h.industry || '—'}</td>
                              <td style={{ color: 'var(--text-secondary)', fontSize: '11px', maxWidth: '280px' }}>
                                {h.revenue_driver || '—'}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              );
            })}
        </div>
      </div>
    </div>
  );
}
