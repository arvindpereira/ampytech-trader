'use client';

import React, { useEffect, useState } from 'react';
import { Activity, AlertTriangle, CheckCircle2, PauseCircle, Play } from 'lucide-react';
import { apiUrl } from '../lib/api';
import { money } from './TickerDrawer';

interface Sleeve {
  key: string; label: string; weight: number; effective_weight: number;
  cap: number; deployed: number; available: number; assigned: number;
}
interface Candidate {
  ticker: string; sleeve: string; confidence: number | null;
  verdict: string; detail?: string; shares_est?: number | null; value_est?: number | null;
}
interface LtCandidate { ticker: string; verdict: string; detail?: string; weight?: number; price_dev?: number; }
interface Warning { sleeve: string; level: 'warn' | 'info'; message: string; }
interface Plan {
  paused: boolean; market_open: boolean | null; market_detail: string;
  regime: string; regime_overlay_enabled: boolean; swing_factor: number;
  equity: number; buying_power: number; next_open?: string | null;
  sleeves: Sleeve[]; candidates: Candidate[];
  longterm_candidates: LtCandidate[]; warnings: Warning[];
  summary: { swing_buy_signals: number; high_risk_buy_signals: number; would_execute: number;
             longterm_will_buy: number; longterm_waiting: number };
}
interface LastOrder {
  ticker: string; sleeve: string; side?: string; shares: number; value?: number;
  tif?: string; status?: string;
}
interface PlanResponse {
  live: Plan; verdict_labels: Record<string, string>;
  last_run: { run_at: string; trigger: string; market_open?: boolean | null; orders: LastOrder[] } | null;
}

const VERDICT_COLOR: Record<string, string> = {
  buy: '#10B981', already_held: '#64748b', not_assigned: '#F59E0B',
  blocked: '#F43F5E', locked: '#F43F5E', no_brackets: '#F59E0B',
  budget_exhausted: '#F43F5E', position_too_small: '#64748b',
  would_open: '#10B981', would_add_dip: '#10B981', wait_for_dip: '#F59E0B',
  would_trim: '#00F2FE', at_target: '#64748b',
};

const SLEEVE_COLOR: Record<string, string> = { swing: '#00F2FE', high_risk: '#F59E0B', longterm: '#a78bfa' };

const labelStyle: React.CSSProperties = {
  fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em',
  color: 'var(--text-secondary)', marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '6px',
};

const Pill = ({ color, children }: { color: string; children: React.ReactNode }) => (
  <span style={{ fontSize: '11px', fontWeight: 700, padding: '3px 9px', borderRadius: '12px', background: `${color}22`, border: `1px solid ${color}55`, color }}>{children}</span>
);

export default function ExecutionPanel({ onTickerClick }: { onTickerClick?: (t: string) => void }) {
  const [data, setData] = useState<PlanResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [pipeline, setPipeline] = useState<{ running: boolean; stage: string; progress: number; error?: string | null } | null>(null);
  const [forceRetrain, setForceRetrain] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const res = await fetch(apiUrl('/api/execution/plan'));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
      setErr(null);
    } catch (e: any) {
      setErr(e?.message || 'failed to load');
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => { load(); }, []);

  const pollPipeline = (jobId: string) => {
    const tick = async () => {
      try {
        const res = await fetch(apiUrl('/api/jobs'));
        const jobs = res.ok ? (await res.json()).jobs || [] : [];
        const job = jobs.find((j: any) => j.id === jobId) || jobs.find((j: any) => j.type === 'pipeline');
        if (!job) { setPipeline(null); load(); return; }
        setPipeline({ running: job.status === 'running', stage: job.stage, progress: job.progress, error: job.error });
        if (job.status === 'running') {
          setTimeout(tick, 2000);
        } else {
          load();                                  // refresh the plan + last-run once done
          setTimeout(() => setPipeline(null), 6000);
        }
      } catch { setTimeout(tick, 3000); }
    };
    tick();
  };

  const runPipeline = async () => {
    if (pipeline?.running) return;
    const ok = window.confirm(
      'Run the full trading pipeline now?\n\nThis will: refresh data + news, retrain models if stale, '
      + 'regenerate signals, and PLACE/ENQUEUE REAL TRADES on Alpaca (unless auto-trading is paused).'
    );
    if (!ok) return;
    setPipeline({ running: true, stage: 'Starting…', progress: 0 });
    try {
      const res = await fetch(apiUrl(`/api/pipeline/run?retrain=${forceRetrain ? 'always' : 'auto'}`), { method: 'POST' });
      const j = await res.json();
      if (j.job_id) pollPipeline(j.job_id);
      else setPipeline({ running: false, stage: 'Failed to start', progress: 0, error: 'no job id' });
    } catch (e: any) {
      setPipeline({ running: false, stage: 'Failed to start', progress: 0, error: e?.message });
    }
  };

  const plan = data?.live;
  const labels = data?.verdict_labels ?? {};

  return (
    <div className="glass-card" style={{ padding: '16px', gridColumn: '1 / -1' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
        <div style={labelStyle}>
          <Activity size={13} color="#00F2FE" /> Execution plan — why the bot is / isn&apos;t buying
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <label title="Retrain the models even if no new data has arrived. Otherwise retraining happens automatically when fresh daily data is available."
            style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '11px', color: 'var(--text-secondary)', cursor: 'pointer', userSelect: 'none' }}>
            <input type="checkbox" checked={forceRetrain} onChange={(e) => setForceRetrain(e.target.checked)} disabled={pipeline?.running} />
            Force retrain
          </label>
          <button onClick={runPipeline} disabled={pipeline?.running}
            style={{ fontSize: '11px', fontWeight: 700, padding: '5px 12px', borderRadius: '6px',
              border: '1px solid rgba(16,185,129,0.45)', background: 'rgba(16,185,129,0.12)', color: '#10B981',
              cursor: pipeline?.running ? 'wait' : 'pointer', display: 'flex', alignItems: 'center', gap: '5px' }}>
            <Play size={11} /> {pipeline?.running ? 'Running…' : 'Run pipeline now'}
          </button>
          <button onClick={load} style={{ fontSize: '11px', padding: '5px 10px', borderRadius: '6px', border: '1px solid var(--border-glass)', background: 'rgba(255,255,255,0.04)', color: 'var(--text-secondary)', cursor: 'pointer' }}>
            {loading ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

      {/* Pipeline progress */}
      {pipeline && (
        <div style={{ margin: '6px 0 12px', padding: '8px 10px', borderRadius: '6px',
          background: pipeline.error ? 'rgba(244,63,94,0.10)' : 'rgba(16,185,129,0.08)',
          border: `1px solid ${pipeline.error ? 'rgba(244,63,94,0.4)' : 'rgba(16,185,129,0.3)'}` }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '12px', marginBottom: '5px' }}>
            <span style={{ color: 'var(--text-primary)' }}>{pipeline.error ? `Pipeline error: ${pipeline.error}` : pipeline.stage}</span>
            {!pipeline.error && <span style={{ color: 'var(--text-secondary)' }}>{pipeline.progress}%</span>}
          </div>
          {!pipeline.error && (
            <div style={{ height: '5px', borderRadius: '3px', background: 'rgba(255,255,255,0.08)', overflow: 'hidden' }}>
              <div style={{ width: `${pipeline.progress}%`, height: '100%', background: '#10B981', transition: 'width 0.4s' }} />
            </div>
          )}
        </div>
      )}

      {err && <p style={{ fontSize: '12px', color: '#F43F5E', margin: '4px 0' }}>Couldn&apos;t load execution plan: {err}</p>}
      {!plan && !err && <p style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Loading…</p>}

      {plan && (
        <>
          {/* Status row */}
          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '14px' }}>
            {plan.paused
              ? <Pill color="#F43F5E"><PauseCircle size={11} style={{ verticalAlign: '-1px' }} /> Auto-trading PAUSED</Pill>
              : <Pill color="#10B981"><CheckCircle2 size={11} style={{ verticalAlign: '-1px' }} /> Auto-trading on</Pill>}
            <Pill color={plan.market_open ? '#10B981' : '#64748b'}>Market {plan.market_open ? 'open' : 'closed'}</Pill>
            <Pill color="#a78bfa">Regime: {plan.regime}{plan.regime_overlay_enabled && plan.swing_factor < 1 ? ` · swing ×${plan.swing_factor}` : ''}</Pill>
            <Pill color={plan.summary.would_execute > 0 ? '#10B981' : '#F59E0B'}>
              {plan.summary.would_execute} would execute now
            </Pill>
            <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
              {plan.summary.swing_buy_signals} swing + {plan.summary.high_risk_buy_signals} high-risk BUY signals · MPT {plan.summary.longterm_will_buy} buying / {plan.summary.longterm_waiting} waiting
            </span>
          </div>

          {/* Warnings */}
          {plan.warnings?.length > 0 && (
            <div style={{ display: 'grid', gap: '6px', marginBottom: '14px' }}>
              {plan.warnings.map((w, i) => {
                const c = w.level === 'warn' ? '#F43F5E' : '#F59E0B';
                return (
                  <div key={i} style={{ display: 'flex', gap: '8px', alignItems: 'flex-start', fontSize: '12px',
                    padding: '8px 10px', borderRadius: '6px', background: `${c}14`, border: `1px solid ${c}40`, color: 'var(--text-primary)' }}>
                    <AlertTriangle size={13} color={c} style={{ flexShrink: 0, marginTop: '1px' }} />
                    <span>{w.message}</span>
                  </div>
                );
              })}
            </div>
          )}

          {/* Sleeve budget bars */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '10px', marginBottom: '16px' }}>
            {plan.sleeves.map((s) => {
              const frac = s.cap > 0 ? Math.min(1, s.deployed / s.cap) : (s.deployed > 0 ? 1 : 0);
              const over = s.cap > 0 && s.deployed > s.cap;
              const color = SLEEVE_COLOR[s.key] ?? '#64748b';
              return (
                <div key={s.key} style={{ padding: '10px 12px', borderRadius: '8px', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-glass)' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '6px' }}>
                    <span style={{ fontSize: '12px', fontWeight: 700, color }}>{s.label}</span>
                    <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                      {(s.effective_weight * 100).toFixed(0)}% cap{s.effective_weight !== s.weight ? ` (${(s.weight * 100).toFixed(0)}%×regime)` : ''}
                    </span>
                  </div>
                  <div style={{ height: '6px', borderRadius: '3px', background: 'rgba(255,255,255,0.07)', overflow: 'hidden', marginBottom: '6px' }}>
                    <div style={{ width: `${frac * 100}%`, height: '100%', background: over ? '#F43F5E' : color }} />
                  </div>
                  <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                    {money(s.deployed)} / {money(s.cap)} deployed
                    {' · '}
                    <strong style={{ color: s.available > 0 ? '#10B981' : '#F43F5E' }}>
                      {s.available > 0 ? `${money(s.available)} free` : (over ? 'over cap — $0 free' : '$0 free')}
                    </strong>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Candidate verdict table */}
          {plan.candidates.length > 0 ? (
            <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.07)' }}>
                    {['Ticker', 'Sleeve', 'Conf', 'Verdict', 'Detail'].map((h) => (
                      <th key={h} style={{ padding: '6px 8px', textAlign: h === 'Conf' ? 'right' : 'left', fontWeight: 600, fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {plan.candidates.map((c) => {
                    const vc = VERDICT_COLOR[c.verdict] ?? '#64748b';
                    return (
                      <tr key={`${c.sleeve}-${c.ticker}`} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                        <td onClick={() => onTickerClick?.(c.ticker)} style={{ padding: '8px', fontWeight: 700, color: 'var(--text-primary)', cursor: onTickerClick ? 'pointer' : 'default' }}>{c.ticker}</td>
                        <td style={{ padding: '8px', color: SLEEVE_COLOR[c.sleeve] ?? 'var(--text-secondary)' }}>{c.sleeve}</td>
                        <td style={{ padding: '8px', textAlign: 'right', color: 'var(--text-secondary)' }}>{c.confidence != null ? `${(c.confidence * 100).toFixed(0)}%` : '—'}</td>
                        <td style={{ padding: '8px' }}><span style={{ color: vc, fontWeight: 600 }}>{labels[c.verdict] ?? c.verdict}</span></td>
                        <td style={{ padding: '8px', color: 'var(--text-secondary)' }}>
                          {c.verdict === 'buy' && c.shares_est ? `${c.shares_est} sh · ${money(c.value_est ?? 0)}` : (c.detail ?? '')}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0 }}>
              {plan.paused ? 'Auto-trading is paused — no candidates evaluated.' : 'No swing/high-risk model BUY signals to evaluate right now.'}
            </p>
          )}

          {/* Long-term MPT grid */}
          {plan.longterm_candidates?.length > 0 && (
            <div style={{ marginTop: '16px' }}>
              <div style={labelStyle}>Long-term (MPT) grid — adds only on new names or 3%+ dips</div>
              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                {plan.longterm_candidates.map((c) => {
                  const vc = VERDICT_COLOR[c.verdict] ?? '#64748b';
                  return (
                    <button key={c.ticker} onClick={() => onTickerClick?.(c.ticker)}
                      title={c.detail}
                      style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px',
                        padding: '5px 9px', borderRadius: '14px', cursor: onTickerClick ? 'pointer' : 'default',
                        background: `${vc}14`, border: `1px solid ${vc}40`, color: 'var(--text-primary)' }}>
                      <strong>{c.ticker}</strong>
                      <span style={{ color: vc, fontWeight: 600 }}>{labels[c.verdict] ?? c.verdict}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Last run — what was actually submitted (market orders fill same-day; they don't queue) */}
          {data?.last_run && (
            <div style={{ marginTop: '16px' }}>
              <div style={labelStyle}>
                Last run — {new Date(data.last_run.run_at).toLocaleString()} ({data.last_run.trigger}) · market {data.last_run.market_open ? 'open' : 'closed'}
              </div>
              {data.last_run.orders.length === 0 ? (
                <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0 }}>No orders were submitted on the last run.</p>
              ) : (
                <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                  {data.last_run.orders.map((o, i) => {
                    const filled = (o.status || '').toLowerCase() === 'filled';
                    const sideC = o.side === 'sell' ? '#F43F5E' : '#10B981';
                    return (
                      <button key={i} onClick={() => onTickerClick?.(o.ticker)}
                        style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '11px',
                          padding: '5px 9px', borderRadius: '14px', cursor: onTickerClick ? 'pointer' : 'default',
                          background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-glass)', color: 'var(--text-primary)' }}>
                        <span style={{ color: sideC, fontWeight: 700 }}>{(o.side || 'buy').toUpperCase()}</span>
                        <strong>{o.ticker}</strong>
                        <span style={{ color: 'var(--text-secondary)' }}>{o.shares} sh{o.value ? ` · ${money(o.value)}` : ''}</span>
                        <span style={{ color: filled ? '#10B981' : '#F59E0B', fontWeight: 600 }}>
                          {filled ? 'filled' : (o.status || 'pending')}
                        </span>
                        {o.tif && <span style={{ color: 'var(--text-secondary)', opacity: 0.7 }}>{o.tif.toUpperCase()}</span>}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
