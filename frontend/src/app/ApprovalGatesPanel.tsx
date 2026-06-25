'use client';

import React, { useEffect, useState, useCallback } from 'react';
import { ShieldCheck, ShieldAlert, CheckCircle2, XCircle, Lock, Unlock, RefreshCw } from 'lucide-react';
import { apiUrl } from '../lib/api';
import { money } from './TickerDrawer';

interface GateAccount {
  key: string; label: string; is_live: boolean; configured: boolean; gate_on: boolean;
}
interface OpenOrder {
  id: string; symbol: string; side: string; qty: string; type: string | null;
  limit_price: number | null; status: string;
}
interface PendingTrade {
  id: number; account_key: string; ticker: string; side: string; qty: number;
  intended_type: string; limit_price: number | null; take_profit: number | null;
  stop_loss: number | null; intended_price: number | null; time_in_force: string;
  sleeve: string | null; label: string | null; reason: string | null; status: string;
  created_at: string; expires_at: string;
}

const labelStyle: React.CSSProperties = {
  fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em',
  color: 'var(--text-secondary)', marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '6px',
};

const Pill = ({ color, children }: { color: string; children: React.ReactNode }) => (
  <span style={{ fontSize: '11px', fontWeight: 700, padding: '3px 9px', borderRadius: '12px', background: `${color}22`, border: `1px solid ${color}55`, color }}>{children}</span>
);

export default function ApprovalGatesPanel({ onTickerClick }: { onTickerClick?: (t: string) => void }) {
  const [accounts, setAccounts] = useState<GateAccount[]>([]);
  const [selected, setSelected] = useState<string>('');
  const [pending, setPending] = useState<PendingTrade[]>([]);
  const [openOrders, setOpenOrders] = useState<OpenOrder[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  // Approve modal: the trade being approved + the chosen placement + editable limit price.
  const [approving, setApproving] = useState<PendingTrade | null>(null);
  const [placement, setPlacement] = useState<'market_bracket' | 'limit'>('market_bracket');
  const [limitPrice, setLimitPrice] = useState('');
  const [modalErr, setModalErr] = useState<string | null>(null);
  // Refresh-queue: re-fetch prices + re-run the model (no retrain) so the queue is replaced with a
  // fresh, current-priced set (the backend supersedes the prior queue, so no duplicates).
  const [refreshStage, setRefreshStage] = useState<string | null>(null);

  const loadAccounts = useCallback(async () => {
    try {
      const res = await fetch(apiUrl('/api/execution/accounts'));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: GateAccount[] = await res.json();
      setAccounts(data);
      setSelected((cur) => cur || (data.find((a) => a.gate_on && a.configured) || data.find((a) => a.configured) || data[0])?.key || '');
      setErr(null);
    } catch (e: any) {
      setErr(e?.message || 'failed to load accounts');
    }
  }, []);

  const loadPending = useCallback(async (key: string) => {
    if (!key) return;
    try {
      const res = await fetch(apiUrl(`/api/pending-trades?account_key=${encodeURIComponent(key)}`));
      if (res.ok) setPending(await res.json());
    } catch { /* surfaced via err on the accounts call */ }
  }, []);

  const loadOpenOrders = useCallback(async (key: string) => {
    if (!key) { setOpenOrders([]); return; }
    try {
      const res = await fetch(apiUrl(`/api/execution/open-orders?account_key=${encodeURIComponent(key)}`));
      setOpenOrders(res.ok ? await res.json() : []);
    } catch { setOpenOrders([]); }
  }, []);

  const cancelOrder = async (id: string) => {
    setBusy(true);
    try {
      await fetch(apiUrl(`/api/execution/open-orders/${encodeURIComponent(id)}/cancel?account_key=${encodeURIComponent(selected)}`), { method: 'POST' });
      await loadOpenOrders(selected);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => { loadAccounts(); }, [loadAccounts]);
  useEffect(() => { loadPending(selected); loadOpenOrders(selected); }, [selected, loadPending, loadOpenOrders]);

  // Re-fetch prices + re-run the model (retrain skipped), then reload the queue. The backend
  // supersedes the prior pending trades, so this REPLACES the queue with a fresh, current-priced set.
  const refreshQueue = async () => {
    if (refreshStage) return;
    setRefreshStage('Starting…');
    try {
      const res = await fetch(apiUrl('/api/pipeline/run?retrain=never'), { method: 'POST' });
      const j = await res.json().catch(() => ({}));
      const jobId = j.job_id;
      const tick = async () => {
        try {
          const r = await fetch(apiUrl('/api/jobs'));
          const jobs = r.ok ? (await r.json()).jobs || [] : [];
          const job = jobs.find((x: any) => x.id === jobId) || jobs.find((x: any) => x.type === 'pipeline');
          if (job && job.status === 'running') {
            setRefreshStage(`${job.stage || 'Working'} ${job.progress ?? ''}%`);
            setTimeout(tick, 2000);
          } else {
            setRefreshStage(null);
            await loadAccounts();
            await loadPending(selected);
            await loadOpenOrders(selected);
          }
        } catch { setTimeout(tick, 3000); }
      };
      tick();
    } catch {
      setRefreshStage(null);
    }
  };

  const toggleGate = async (acc: GateAccount) => {
    setBusy(true);
    try {
      await fetch(apiUrl(`/api/execution/accounts/${encodeURIComponent(acc.key)}/gate`), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ gate_on: !acc.gate_on }),
      });
      await loadAccounts();
    } finally {
      setBusy(false);
    }
  };

  const openApprove = (t: PendingTrade) => {
    setApproving(t);
    setPlacement('market_bracket');
    setLimitPrice(t.intended_price != null ? String(t.intended_price) : '');
    setModalErr(null);
  };

  const submitApprove = async () => {
    if (!approving) return;
    setBusy(true);
    setModalErr(null);
    try {
      const body: any = { placement };
      if (placement === 'limit') body.limit_price = parseFloat(limitPrice) || 0;
      const res = await fetch(apiUrl(`/api/pending-trades/${approving.id}/approve`), {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { setModalErr(data.detail || `HTTP ${res.status}`); return; }
      setApproving(null);
      await loadPending(selected);
      await loadOpenOrders(selected);   // an approved limit may now be resting at the broker
    } catch (e: any) {
      setModalErr(e?.message || 'approval failed');
    } finally {
      setBusy(false);
    }
  };

  const rejectTrade = async (t: PendingTrade) => {
    setBusy(true);
    try {
      await fetch(apiUrl(`/api/pending-trades/${t.id}/reject`), { method: 'POST' });
      await loadPending(selected);
    } finally {
      setBusy(false);
    }
  };

  const inputStyle: React.CSSProperties = { background: 'rgba(0,0,0,0.3)', color: 'var(--text-primary)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px', fontSize: '13px' };

  return (
    <div className="glass-card" style={{ padding: '16px', gridColumn: '1 / -1' }}>
      <div style={labelStyle}>
        <ShieldCheck size={13} color="#a78bfa" /> Approval gates — review real-account trades before they&apos;re placed
      </div>

      {err && <p style={{ fontSize: '12px', color: '#F43F5E', margin: '4px 0' }}>Couldn&apos;t load accounts: {err}</p>}

      {/* Per-account gate toggles */}
      <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', marginBottom: '14px' }}>
        {accounts.map((acc) => (
          <div key={acc.key} style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '10px 12px',
            borderRadius: '8px', border: `1px solid ${acc.gate_on ? 'rgba(167,139,250,0.5)' : 'var(--border-glass)'}`,
            background: acc.gate_on ? 'rgba(167,139,250,0.08)' : 'rgba(255,255,255,0.02)' }}>
            <div style={{ display: 'grid', gap: '3px' }}>
              <div style={{ fontSize: '13px', fontWeight: 600, color: 'var(--text-primary)', display: 'flex', alignItems: 'center', gap: '6px' }}>
                {acc.label}
                {acc.is_live ? <Pill color="#F43F5E">LIVE</Pill> : <Pill color="#64748b">paper</Pill>}
              </div>
              {acc.configured
                ? <Pill color={acc.gate_on ? '#a78bfa' : '#10B981'}>{acc.gate_on ? 'Gate ON — approval required' : 'Auto-execute'}</Pill>
                : <Pill color="#64748b">Not configured</Pill>}
            </div>
            <button onClick={() => toggleGate(acc)} disabled={busy || !acc.configured} className="toggle-btn"
              title={acc.configured ? 'Toggle the approval gate' : 'Add credentials to enable this account'}
              style={{ padding: '5px 10px', fontSize: '11.5px', display: 'flex', alignItems: 'center', gap: '5px',
                borderColor: acc.gate_on ? '#10B981' : '#a78bfa',
                cursor: (busy || !acc.configured) ? 'not-allowed' : 'pointer', opacity: acc.configured ? 1 : 0.5 }}>
              {acc.gate_on ? <><Unlock size={12} /> Turn gate off</> : <><Lock size={12} /> Turn gate on</>}
            </button>
          </div>
        ))}
      </div>

      {/* Account selector for the pending queue + refresh */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px', flexWrap: 'wrap' }}>
        <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Pending approval for:</span>
        {accounts.filter((a) => a.configured).map((a) => (
          <button key={a.key} onClick={() => setSelected(a.key)} className={`toggle-btn ${selected === a.key ? 'active' : ''}`}
            style={{ padding: '4px 10px', fontSize: '11.5px' }}>{a.label}</button>
        ))}
        <button onClick={refreshQueue} disabled={!!refreshStage}
          title="Re-fetch prices, re-run the model, and replace the queue with a fresh set (no retrain)."
          className="toggle-btn" style={{ marginLeft: 'auto', padding: '4px 10px', fontSize: '11.5px',
            display: 'flex', alignItems: 'center', gap: '5px', cursor: refreshStage ? 'wait' : 'pointer' }}>
          <RefreshCw size={12} /> {refreshStage ? refreshStage : 'Refresh queue'}
        </button>
      </div>

      {/* Resting (working) orders at the broker — e.g. an approved limit that hasn't filled. Cancel
          stale ones here; the bot won't re-queue a name while its order is still working. */}
      {openOrders.length > 0 && (
        <div style={{ marginBottom: '12px', padding: '10px 12px', borderRadius: '8px', border: '1px solid rgba(245,158,11,0.4)', background: 'rgba(245,158,11,0.06)' }}>
          <div style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em', color: '#F59E0B', marginBottom: '8px' }}>
            Resting orders at broker ({openOrders.length})
          </div>
          <div style={{ display: 'grid', gap: '6px' }}>
            {openOrders.map((o) => (
              <div key={o.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px', fontSize: '12px' }}>
                <span style={{ color: 'var(--text-secondary)' }}>
                  <strong style={{ color: o.side === 'buy' ? '#10B981' : '#F43F5E' }}>{o.side.toUpperCase()}</strong>{' '}
                  <strong style={{ color: 'var(--text-primary)' }}>{o.symbol}</strong> {o.qty} ·{' '}
                  {o.type === 'limit' && o.limit_price != null ? `limit ${money(o.limit_price)}` : o.type} · {o.status}
                </span>
                <button onClick={() => cancelOrder(o.id)} disabled={busy}
                  style={{ padding: '3px 9px', fontSize: '11px', borderRadius: '5px', border: '1px solid var(--border-glass)', background: 'transparent', color: '#F43F5E', cursor: 'pointer' }}>
                  Cancel
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Pending trades */}
      {pending.length === 0 ? (
        <div style={{ padding: '18px 0', textAlign: 'center', color: 'var(--text-secondary)', fontSize: '12.5px' }}>
          No trades awaiting approval. When this account is gated, the bot queues its calculated trades here.
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '8px' }}>
          {pending.map((t) => {
            const sideColor = t.side === 'buy' ? '#10B981' : '#F43F5E';
            return (
              <div key={t.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '10px',
                padding: '10px 12px', borderRadius: '8px', border: '1px solid var(--border-glass)', background: 'rgba(255,255,255,0.02)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap' }}>
                  <Pill color={sideColor}>{t.side.toUpperCase()}</Pill>
                  <strong style={{ color: 'var(--text-primary)', cursor: onTickerClick ? 'pointer' : 'default' }}
                    onClick={() => onTickerClick?.(t.ticker)}>{t.ticker}</strong>
                  <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                    {t.qty} sh{t.intended_price != null ? ` @ ~$${t.intended_price.toFixed(2)}` : ''}
                    {t.intended_price != null ? ` · ${money(t.qty * t.intended_price)}` : ''}
                  </span>
                  {t.sleeve && <Pill color="#64748b">{t.sleeve}</Pill>}
                  {t.take_profit != null && t.stop_loss != null &&
                    <span style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>TP ${t.take_profit.toFixed(2)} / SL ${t.stop_loss.toFixed(2)}</span>}
                  {t.reason && <span style={{ fontSize: '11px', color: 'var(--text-secondary)', fontStyle: 'italic' }}>{t.reason}</span>}
                </div>
                <div style={{ display: 'flex', gap: '6px', whiteSpace: 'nowrap' }}>
                  <button onClick={() => openApprove(t)} disabled={busy} className="toggle-btn"
                    style={{ padding: '4px 10px', fontSize: '11.5px', borderColor: '#10B981', display: 'flex', alignItems: 'center', gap: '4px' }}>
                    <CheckCircle2 size={12} /> Approve
                  </button>
                  <button onClick={() => rejectTrade(t)} disabled={busy}
                    style={{ padding: '4px 10px', fontSize: '11.5px', borderRadius: '6px', border: '1px solid var(--border-glass)', background: 'transparent', color: '#F43F5E', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '4px' }}>
                    <XCircle size={12} /> Reject
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Approve modal */}
      {approving && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}
          onClick={() => !busy && setApproving(null)}>
          <div className="glass-card" style={{ padding: '24px', width: '440px', maxWidth: '92vw', display: 'grid', gap: '14px' }}
            onClick={(e) => e.stopPropagation()}>
            <h3 style={{ margin: 0, fontSize: '16px', fontWeight: 600 }}>
              Approve {approving.side.toUpperCase()} {approving.qty} {approving.ticker}
            </h3>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
              Placing on <strong style={{ color: 'var(--text-primary)' }}>{accounts.find((a) => a.key === approving.account_key)?.label || approving.account_key}</strong>.
            </div>

            <div style={{ display: 'flex', gap: '8px' }}>
              <button onClick={() => setPlacement('market_bracket')} className={`toggle-btn ${placement === 'market_bracket' ? 'active' : ''}`} style={{ flex: 1, padding: '8px', fontSize: '12px' }}>
                Market bracket
              </button>
              <button onClick={() => setPlacement('limit')} className={`toggle-btn ${placement === 'limit' ? 'active' : ''}`} style={{ flex: 1, padding: '8px', fontSize: '12px' }}>
                Limit order
              </button>
            </div>

            {placement === 'market_bracket' ? (
              <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
                Submits the bot&apos;s intended market order
                {approving.take_profit != null && approving.stop_loss != null
                  ? <> with bracket take-profit <strong>${approving.take_profit.toFixed(2)}</strong> / stop <strong>${approving.stop_loss.toFixed(2)}</strong>.</>
                  : <>.</>}
              </div>
            ) : (
              <div style={{ display: 'grid', gap: '6px' }}>
                <label style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>Limit price</label>
                <input type="number" step="any" value={limitPrice} onChange={(e) => setLimitPrice(e.target.value)} style={inputStyle} />
                <span style={{ fontSize: '11px', color: '#F59E0B' }}>Bracket take-profit / stop are not applied to limit orders.</span>
              </div>
            )}

            {modalErr && <div style={{ fontSize: '12px', color: '#F43F5E' }}>{modalErr}</div>}

            <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
              <button onClick={() => setApproving(null)} disabled={busy}
                style={{ padding: '8px 14px', borderRadius: '6px', border: '1px solid var(--border-glass)', background: 'transparent', color: 'var(--text-secondary)', cursor: 'pointer' }}>
                Cancel
              </button>
              <button onClick={submitApprove} disabled={busy} className="toggle-btn"
                style={{ padding: '8px 14px', fontSize: '13px', borderColor: '#10B981' }}>
                {busy ? 'Placing…' : 'Place order'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
