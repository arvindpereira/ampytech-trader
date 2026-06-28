'use client';

import React, { useCallback, useEffect, useState } from 'react';
import {
  CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis, Legend,
} from 'recharts';
import { apiUrl } from '../lib/api';

type EvPoint = {
  participation_pct: number;
  normal_return: number;
  crash_return: number;
  expected_geom_return: number;
  ev: number;
};
type Reco = {
  suggested_participation_pct: number;
  p_crash: number;
  crash_era: string;
  risk_band?: string;
  composite_index?: number;
  ev_curve: EvPoint[];
  rationale: string;
  warnings?: string[];
};

const ERAS: { key: string; label: string }[] = [
  { key: 'gfc', label: '2008 GFC' },
  { key: 'covid', label: 'COVID' },
  { key: 'dotcom', label: 'Dot-com' },
  { key: '2022', label: '2022' },
];

export default function CrashRebalanceRecommendation({
  mode,
  preset,
  theta,
  k,
  gamma,
  onUse,
  onSuggestion,
}: {
  mode: 'paper' | 'live';
  preset: string;
  theta: number;
  k: number;
  gamma: number;
  onUse: (pct: number) => void;
  onSuggestion?: (pct: number) => void;
}) {
  const [era, setEra] = useState('gfc');
  const [data, setData] = useState<Reco | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const presetParam = preset === 'custom'
        ? `preset=custom&theta=${theta}&k=${k}&gamma=${gamma}`
        : `preset=${preset}`;
      const res = await fetch(apiUrl(`/api/crash/rebalance/recommend?mode=${mode}&crash_era=${era}&${presetParam}`));
      if (res.ok) {
        const j: Reco = await res.json();
        setData(j);
        onSuggestion?.(j.suggested_participation_pct);
      } else {
        const e = await res.json().catch(() => ({}));
        setError(e.detail || res.statusText);
      }
    } catch (e: any) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [mode, preset, theta, k, gamma, era, onSuggestion]);

  useEffect(() => { load(); }, [load]);

  const suggestedPctLabel = data ? `${Math.round(data.suggested_participation_pct * 100)}%` : '—';

  return (
    <div style={{ border: '1px solid var(--border-glass)', borderRadius: '10px', padding: '14px', background: 'rgba(255,255,255,0.02)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px', marginBottom: '8px' }}>
        <strong style={{ fontSize: '13.5px', color: 'var(--text-primary)' }}>Model-recommended rebalance amount</strong>
        <div style={{ display: 'flex', gap: '4px' }}>
          {ERAS.map(e => (
            <button key={e.key} onClick={() => setEra(e.key)} className="toggle-btn"
              style={{
                fontSize: '10.5px', padding: '2px 8px',
                borderColor: era === e.key ? 'var(--color-gold)' : 'var(--border-glass)',
                background: era === e.key ? 'rgba(245,158,11,0.15)' : 'transparent',
                color: era === e.key ? 'var(--text-primary)' : 'var(--text-secondary)',
              }}>
              {e.label}
            </button>
          ))}
        </div>
      </div>

      {loading && <div style={{ fontSize: '12px', color: 'var(--text-secondary)', padding: '20px 0', textAlign: 'center' }}>Sweeping the slider across scenarios…</div>}
      {error && <div style={{ fontSize: '12px', color: '#EF4444' }}>Could not compute recommendation: {error}</div>}

      {data && !loading && (
        <>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '12px', flexWrap: 'wrap', marginBottom: '8px' }}>
            <div style={{ fontSize: '26px', fontWeight: 800, color: 'var(--color-gold)' }}>{suggestedPctLabel}</div>
            <div style={{ fontSize: '11.5px', color: 'var(--text-secondary)' }}>
              to safety · {Math.round(data.p_crash * 100)}% modeled crash probability ({data.risk_band})
            </div>
            <button onClick={() => onUse(data.suggested_participation_pct)} className="toggle-btn"
              style={{ marginLeft: 'auto', fontSize: '11.5px', padding: '4px 12px', fontWeight: 700, background: 'var(--color-gold)', color: 'black', border: 'none' }}>
              Use {suggestedPctLabel}
            </button>
          </div>

          <p style={{ fontSize: '11px', color: 'var(--text-secondary)', margin: '0 0 10px', lineHeight: 1.45 }}>{data.rationale}</p>

          <div style={{ width: '100%', height: '200px' }}>
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={data.ev_curve.map(p => ({ ...p, pct: Math.round(p.participation_pct * 100) }))}
                margin={{ top: 6, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                <XAxis dataKey="pct" tick={{ fontSize: 9, fill: '#64748b' }} tickFormatter={(v) => `${v}%`}
                  tickLine={false} axisLine={false} />
                <YAxis tick={{ fontSize: 9, fill: '#64748b' }} tickFormatter={(v) => `${v}%`} width={42}
                  tickLine={false} axisLine={false} />
                <Tooltip
                  contentStyle={{ background: '#1e293b', border: '1px solid rgba(255,255,255,0.1)', fontSize: '11px', borderRadius: '6px' }}
                  formatter={(v: number, n: string) => [`${Number(v).toFixed(1)}%`, n]}
                  labelFormatter={(l) => `${l}% to safety`} />
                <Legend wrapperStyle={{ fontSize: '10px' }} />
                <ReferenceLine x={Math.round(data.suggested_participation_pct * 100)} stroke="var(--color-gold)"
                  strokeDasharray="4 3" label={{ value: 'rec', fill: 'var(--color-gold)', fontSize: 9, position: 'top' }} />
                <Line type="monotone" dataKey="normal_return" name="Normal /yr" stroke="#10B981" dot={false} strokeWidth={1.6} />
                <Line type="monotone" dataKey="crash_return" name={`${data.crash_era.toUpperCase()} crash`} stroke="#EF4444" dot={false} strokeWidth={1.6} />
                <Line type="monotone" dataKey="expected_geom_return" name="Exp. growth" stroke="var(--color-gold)" dot={false} strokeWidth={2} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          {(data.warnings?.length || 0) > 0 && (
            <div style={{ fontSize: '10.5px', color: '#F59E0B', marginTop: '6px' }}>
              {data.warnings!.map((w, i) => <div key={i}>⚠ {w}</div>)}
            </div>
          )}
        </>
      )}
    </div>
  );
}
