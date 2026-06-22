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
import { AlertTriangle, ChevronDown, ChevronRight, RefreshCw, Compass, ArrowRightLeft } from 'lucide-react';
import { apiUrl } from '../lib/api';

type RecommendationRow = {
  ticker: string;
  tier: string | null;
  quality: number | null;
  upside_pct: number | null;
  recommendation_key: string | null;
  held: boolean;
};

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
  recommendations?: RecommendationRow[];
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
  etfs?: Record<string, string>;
};

const money = (n: number) =>
  n >= 1e6 ? `$${(n / 1e6).toFixed(2)}M` : n >= 1e3 ? `$${(n / 1e3).toFixed(1)}K` : `$${n.toFixed(0)}`;

const pct = (w: number) => `${(w * 100).toFixed(1)}%`;

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

  const suggestions = useMemo(() => {
    if (!data?.sectors || !data?.etfs) return [];
    return data.sectors
      .map((s) => {
        const etf = data.etfs?.[s.sector] || 'ETF';
        return {
          sector: s.sector,
          delta: s.delta,
          portfolio_weight: s.portfolio_weight,
          benchmark_weight: s.benchmark_weight,
          alert: s.alert,
          etf,
          recommendations: s.recommendations || [],
        };
      })
      .filter((s) => Math.abs(s.delta) >= 0.03) // Drifts >= 3%
      .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta));
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
    <div className="glass-card" style={{ padding: '24px', display: 'grid', gap: '20px' }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '12px' }}>
        <div>
          <h3 style={{ margin: 0, fontSize: '18px', fontWeight: 600, color: 'var(--text-primary)' }}>Sector Exposure Simulator</h3>
          <p style={{ margin: '6px 0 0', fontSize: '12.5px', color: 'var(--text-secondary)', maxWidth: '680px', lineHeight: 1.5 }}>
            Consolidated holdings (trading account + external lots) mapped to canonical GICS sectors and compared to {data?.benchmark?.name || 'S&P 500'} weights ({data?.benchmark?.as_of || 'benchmark'}). Alerts trigger when deviations exceed {data?.alert_threshold_pp ?? 5}pp.
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

      {/* Alerts */}
      {data?.alerts && data.alerts.length > 0 && (
        <div style={{ display: 'grid', gap: '8px' }}>
          {data.alerts.map((a) => (
            <div
              key={a.sector}
              style={{
                display: 'flex',
                gap: '10px',
                alignItems: 'flex-start',
                padding: '12px 14px',
                borderRadius: '8px',
                background: 'rgba(239, 68, 68, 0.08)',
                border: '1px solid rgba(239, 68, 68, 0.25)',
                fontSize: '13px',
                color: '#FCA5A5',
              }}
            >
              <AlertTriangle size={16} color="#EF4444" style={{ flexShrink: 0, marginTop: '2px' }} />
              <span>{a.message}</span>
            </div>
          ))}
        </div>
      )}

      {/* Main visual side-by-side charts */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 340px', gap: '24px', alignItems: 'stretch', flexWrap: 'wrap' }}>
        {/* Horizontal Bar Chart */}
        <div style={{ minHeight: 340, display: 'flex', flexDirection: 'column' }}>
          <div style={{ fontSize: '12.5px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '12px' }}>
            Portfolio Allocation vs Benchmark Weights (%)
          </div>
          <div style={{ flex: 1, minHeight: 320 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart layout="vertical" data={chartData} margin={{ top: 8, right: 16, left: 16, bottom: 8 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" horizontal={true} vertical={true} />
                <XAxis type="number" tick={{ fontSize: 10, fill: '#94a3b8' }} unit="%" />
                <YAxis type="category" dataKey="sector" tick={{ fontSize: 10, fill: '#94a3b8' }} width={120} interval={0} />
                <Tooltip
                  contentStyle={{ background: '#1e293b', border: '1px solid rgba(255,255,255,0.1)', fontSize: '12px' }}
                  formatter={(v: number) => [`${v}%`, '']}
                />
                <Legend wrapperStyle={{ fontSize: '11px', paddingTop: '10px' }} />
                <Bar dataKey="portfolio" name="Your portfolio" fill="#00F2FE" radius={[0, 3, 3, 0]} />
                <Bar dataKey="benchmark" name="S&P 500" fill="rgba(148,163,184,0.4)" radius={[0, 3, 3, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </div>

        {/* Diverging Active Tilt Heatmap */}
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          <div style={{ fontSize: '12.5px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '12px' }}>
            Active Exposure Tilt (Δ vs Benchmark)
          </div>
          <div style={{ display: 'grid', gap: '8px', background: 'rgba(255,255,255,0.01)', border: '1px solid var(--border-glass)', borderRadius: '8px', padding: '16px', flex: 1 }}>
            {heatmapRows.slice(0, 11).map((s) => {
              const widthPercent = Math.min(50, Math.abs(s.delta * 100) * 5);
              const color = s.delta >= 0 ? '#10B981' : '#F43F5E';
              return (
                <div key={s.sector} style={{ display: 'flex', alignItems: 'center', gap: '8px', height: '24px' }}>
                  <span style={{ width: '100px', fontSize: '11px', color: 'var(--text-secondary)', textAlign: 'right', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={s.sector}>
                    {s.sector.replace('Consumer ', 'Cons. ').replace('Financial ', 'Fin. ')}
                  </span>
                  <div style={{ flex: 1, height: '14px', background: 'rgba(255,255,255,0.04)', borderRadius: '3px', position: 'relative', overflow: 'hidden', border: s.alert ? '1px solid rgba(255,255,255,0.2)' : 'none' }}>
                    <div style={{ position: 'absolute', left: '50%', top: 0, bottom: 0, width: '1px', background: 'rgba(255,255,255,0.15)' }} />
                    <div
                      title={`${s.sector}: ${pct(s.portfolio_weight)} vs ${pct(s.benchmark_weight)}`}
                      style={{
                        position: 'absolute',
                        top: 0,
                        bottom: 0,
                        left: s.delta >= 0 ? '50%' : 'auto',
                        right: s.delta < 0 ? '50%' : 'auto',
                        width: `${widthPercent}%`,
                        background: s.delta >= 0 ? 'linear-gradient(90deg, #10B981, #34D399)' : 'linear-gradient(90deg, #FB7185, #F43F5E)',
                        borderRadius: '2px',
                      }}
                    />
                  </div>
                  <span style={{ width: '52px', fontSize: '11px', color: s.alert ? (s.delta >= 0 ? '#6EE7B7' : '#FCA5A5') : 'var(--text-secondary)', fontWeight: s.alert ? 600 : 400, textAlign: 'left' }}>
                    {s.delta >= 0 ? '+' : ''}{(s.delta * 100).toFixed(1)}pp
                  </span>
                </div>
              );
            })}
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '9.5px', color: 'var(--text-secondary)', borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '10px', marginTop: '6px' }}>
              <span>◀ Underweight (Rose)</span>
              <span>Balanced (0.0pp)</span>
              <span>Overweight (Emerald) ▶</span>
            </div>
          </div>
        </div>
      </div>

      {/* Actionable Suggestions */}
      {suggestions.length > 0 && (
        <div style={{ padding: '16px', background: 'rgba(255,255,255,0.01)', border: '1px solid var(--border-glass)', borderRadius: '8px' }}>
          <div style={{ fontSize: '13.5px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Compass size={16} color="#00F2FE" />
            <span>Actionable Exposure Suggestions</span>
          </div>
          <div style={{ display: 'grid', gap: '12px' }}>
            {suggestions.map((s) => {
              const isUnder = s.delta < 0;
              const absDelta = (Math.abs(s.delta) * 100).toFixed(1);
              return (
                <div key={s.sector} style={{ fontSize: '12.5px', borderLeft: `3px solid ${isUnder ? '#F43F5E' : '#10B981'}`, paddingLeft: '12px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                  <div>
                    <strong style={{ color: 'var(--text-primary)' }}>{s.sector}</strong> is{' '}
                    <span style={{ color: isUnder ? '#FCA5A5' : '#6EE7B7', fontWeight: 600 }}>{absDelta}pp {isUnder ? 'underweight' : 'overweight'}</span>{' '}
                    ({pct(s.portfolio_weight)} portfolio vs {pct(s.benchmark_weight)} S&P 500)
                  </div>
                  <div style={{ color: 'var(--text-secondary)', lineHeight: 1.45 }}>
                    {isUnder ? (
                      <>
                        To bridge the gap, consider acquiring sector ETF proxy <strong style={{ color: 'var(--text-primary)' }}>{s.etf}</strong> or adding exposure to high-quality candidates:{' '}
                        {s.recommendations.length > 0 ? (
                          s.recommendations.map((r, i) => (
                            <span key={r.ticker}>
                              {i > 0 && ', '}
                              <strong style={{ color: 'var(--text-primary)' }}>{r.ticker}</strong> (Quality: {r.quality || 'N/A'}{r.upside_pct ? `, Upside: +${(r.upside_pct * 100).toFixed(1)}%` : ''}{r.held ? ' · Held' : ''})
                            </span>
                          ))
                        ) : (
                          'No direct recommendations found in snapshot database.'
                        )}
                      </>
                    ) : (
                      <>
                        To reduce risk, consider trimming holdings or hedging using sector ETF proxy <strong style={{ color: 'var(--text-primary)' }}>{s.etf}</strong>.
                      </>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Equity Value Footer */}
      <div style={{ fontSize: '12.5px', color: 'var(--text-secondary)', borderTop: '1px solid rgba(255,255,255,0.05)', paddingTop: '10px' }}>
        Total equity value analyzed:{' '}
        <strong style={{ color: 'var(--text-primary)', fontSize: '13px' }}>{money(data?.total_equity_value || 0)}</strong>
        {data?.unclassified && data.unclassified.length > 0 && (
          <span> · Unclassified assets: {data.unclassified.map((u) => u.ticker).join(', ')}</span>
        )}
      </div>

      {/* Drill-down cards */}
      <div>
        <div style={{ fontSize: '14px', fontWeight: 600, color: 'var(--text-primary)', marginBottom: '12px' }}>Drill-down by sector</div>
        <div style={{ display: 'grid', gap: '10px' }}>
          {(data?.sectors || [])
            .filter((s) => s.holdings.length > 0)
            .map((s) => {
              const open = expanded[s.sector];
              return (
                <div key={s.sector} style={{ border: '1px solid var(--border-glass)', borderRadius: '8px', overflow: 'hidden', background: 'rgba(255,255,255,0.01)' }}>
                  <button
                    type="button"
                    onClick={() => setExpanded((prev) => ({ ...prev, [s.sector]: !prev[s.sector] }))}
                    style={{
                      width: '100%',
                      display: 'flex',
                      justifyContent: 'space-between',
                      alignItems: 'center',
                      padding: '12px 16px',
                      background: 'rgba(255,255,255,0.02)',
                      border: 'none',
                      color: 'var(--text-primary)',
                      cursor: 'pointer',
                      fontSize: '13.5px',
                    }}
                  >
                    <span style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                      {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                      <strong>{s.sector}</strong>
                      <span style={{ color: 'var(--text-secondary)', fontWeight: 400, fontSize: '12.5px' }}>
                        {pct(s.portfolio_weight)} · Δ {(s.delta * 100).toFixed(1)}pp
                      </span>
                      {s.alert && (
                        <span style={{ fontSize: '9px', background: 'rgba(239, 68, 68, 0.2)', border: '1px solid rgba(239, 68, 68, 0.4)', color: '#FCA5A5', padding: '1px 5px', borderRadius: '4px', fontWeight: 600 }}>ALERT</span>
                      )}
                    </span>
                    <span style={{ color: 'var(--text-secondary)', fontSize: '13px' }}>{money(s.market_value)}</span>
                  </button>

                  {open && (
                    <div style={{ padding: '16px', display: 'grid', gap: '14px', borderTop: '1px solid rgba(255,255,255,0.04)' }}>
                      {/* Industry Groups list */}
                      {s.industries.length > 0 && (
                        <div style={{ fontSize: '11.5px', color: 'var(--text-secondary)' }}>
                          <span style={{ fontWeight: 600 }}>Industry Groups:</span>{' '}
                          {s.industries.map((i) => `${i.industry} (${pct(i.portfolio_weight)})`).join(' · ')}
                        </div>
                      )}

                      {/* Current Holdings Table */}
                      <div style={{ overflowX: 'auto' }}>
                        <table className="trade-table" style={{ width: '100%', fontSize: '12px', textAlign: 'left' }}>
                          <thead>
                            <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
                              <th style={{ padding: '6px 4px' }}>Ticker</th>
                              <th>Weight</th>
                              <th>Market Value</th>
                              <th>Industry</th>
                              <th>Revenue driver</th>
                            </tr>
                          </thead>
                          <tbody>
                            {s.holdings.map((h) => (
                              <tr key={h.ticker} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                                <td style={{ fontWeight: 700, padding: '8px 4px' }}>{h.ticker}</td>
                                <td>{pct(h.portfolio_weight)}</td>
                                <td style={{ color: 'var(--text-primary)' }}>{money(h.market_value)}</td>
                                <td style={{ color: 'var(--text-secondary)' }}>{h.industry || '—'}</td>
                                <td style={{ color: 'var(--text-secondary)', fontSize: '11px', maxWidth: '320px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={h.revenue_driver}>
                                  {h.revenue_driver || '—'}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>

                      {/* Recommendations sub-grid */}
                      {s.recommendations && s.recommendations.length > 0 && (
                        <div style={{ borderTop: '1px dashed rgba(255,255,255,0.08)', paddingTop: '12px', marginTop: '4px' }}>
                          <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                            <ArrowRightLeft size={13} color="#00F2FE" />
                            <span>Top Sector Candidates (RKB snapshots)</span>
                          </div>
                          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(230px, 1fr))', gap: '8px' }}>
                            {s.recommendations.map((r) => (
                              <div
                                key={r.ticker}
                                style={{
                                  padding: '8px 12px',
                                  background: 'rgba(255,255,255,0.02)',
                                  borderRadius: '6px',
                                  border: '1px solid var(--border-glass)',
                                  fontSize: '12px',
                                  display: 'flex',
                                  justifyContent: 'space-between',
                                  alignItems: 'center',
                                }}
                              >
                                <div>
                                  <strong style={{ color: 'var(--text-primary)' }}>{r.ticker}</strong>
                                  <span style={{ color: 'var(--text-secondary)', fontSize: '10px', marginLeft: '6px', textTransform: 'capitalize' }}>
                                    {r.tier?.replace('_', ' ') || 'unrated'}
                                  </span>
                                </div>
                                <div style={{ textAlign: 'right', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: '2px' }}>
                                  {r.upside_pct !== null && (
                                    <span style={{ color: '#10B981', fontWeight: 600, fontSize: '11px' }}>
                                      +{(r.upside_pct * 100).toFixed(1)}% upside
                                    </span>
                                  )}
                                  <span style={{ fontSize: '10px', color: 'var(--text-secondary)' }}>
                                    Quality: {r.quality !== null ? r.quality : '—'} {r.held && '· Held'}
                                  </span>
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
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
