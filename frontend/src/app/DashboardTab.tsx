'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { BarChart2, Layers, Zap } from 'lucide-react';
import { apiUrl } from '../lib/api';
import { money, pct, TIER_COLOR, TIER_LABEL } from './TickerDrawer';
import type {
  SwingSuggestion, Allocation, PriceSummaryRow, TickerInfo, Classification,
} from './TickerDrawer';
import ExecutionPanel from './ExecutionPanel';
import ApprovalGatesPanel from './ApprovalGatesPanel';

// ─── types ──────────────────────────────────────────────────────────────────

// A single holding row within an account group, enriched downstream by ticker.
interface HoldingRow {
  ticker: string; shares: number; price: number; value: number;
  pl: number | null; plPct: number | null;
  buyTarget?: number | null; takeProfit?: number | null;
  ltEligible?: boolean; ltEligibleShares?: number | null; nextEligibleDate?: string | null;
}
interface AccountGroup {
  label: string; rows: HoldingRow[]; totalValue: number; totalPl: number;
}

const REGIME_COLOR: Record<string, string> = { growth: '#10B981', transition: '#F59E0B', crisis: '#EF4444' };

const INTERNAL_LABEL = 'Bot account (Alpaca)';

// Per-account accent colour, mirroring the external-accounts tab so the same
// account reads the same colour everywhere.
const accountTheme = (label: string): { text: string; border: string; bg: string; badgeBg: string } => {
  const n = label.toLowerCase();
  if (n.includes('bot') || n.includes('internal') || n.includes('alpaca'))
    return { text: '#10B981', border: 'rgba(16,185,129,0.3)', bg: 'rgba(16,185,129,0.04)', badgeBg: 'rgba(16,185,129,0.15)' };
  if (n.includes('vanguard'))
    return { text: '#6366F1', border: 'rgba(99,102,241,0.3)', bg: 'rgba(99,102,241,0.04)', badgeBg: 'rgba(99,102,241,0.15)' };
  if (n.includes('joint'))
    return { text: '#8B5CF6', border: 'rgba(139,92,246,0.3)', bg: 'rgba(139,92,246,0.04)', badgeBg: 'rgba(139,92,246,0.15)' };
  return { text: '#38BDF8', border: 'rgba(56,189,248,0.3)', bg: 'rgba(56,189,248,0.04)', badgeBg: 'rgba(56,189,248,0.15)' };
};

// ─── Main DashboardTab ───────────────────────────────────────────────────────

export default function DashboardTab({
  regime, date, swingSuggestions, allocations, priceSummary, portfolio, externalPositions, classification,
  onTickerClick,
}: {
  regime: string; date: string;
  swingSuggestions: SwingSuggestion[]; allocations: Allocation[];
  priceSummary: PriceSummaryRow[]; portfolio: any; externalPositions: any[];
  classification: Record<string, Classification>;
  onTickerClick: (ticker: string) => void;
}) {
  const [tickerInfo, setTickerInfo] = useState<Record<string, TickerInfo>>({});
  const infoFetched = useRef(false);

  // Collect all unique tickers across signals + every held position
  const allTickers = useMemo(() => {
    const s = new Set<string>();
    swingSuggestions.forEach((x) => s.add(x.ticker));
    allocations.forEach((x) => s.add(x.ticker));
    (portfolio?.holdings || []).forEach((x: any) => x.ticker && s.add(x.ticker.toUpperCase()));
    (externalPositions || []).forEach((x: any) => x.ticker && s.add(x.ticker.toUpperCase()));
    return Array.from(s);
  }, [swingSuggestions, allocations, portfolio, externalPositions]);

  const fetchTickerInfo = useCallback(async (tickers: string[]) => {
    if (!tickers.length) return;
    try {
      const res = await fetch(apiUrl(`/api/tickers/info?tickers=${tickers.join(',')}`));
      if (res.ok) {
        const j = await res.json();
        setTickerInfo((prev) => ({ ...prev, ...j.tickers }));
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    if (allTickers.length && !infoFetched.current) {
      infoFetched.current = true;
      fetchTickerInfo(allTickers);
    }
  }, [allTickers, fetchTickerInfo]);

  const priceMap = useMemo(() => {
    const m: Record<string, PriceSummaryRow> = {};
    (priceSummary || []).forEach((r) => { m[r.ticker] = r; });
    return m;
  }, [priceSummary]);

  const swingMap = useMemo(() => {
    const m: Record<string, SwingSuggestion> = {};
    swingSuggestions.forEach((s) => { m[s.ticker] = s; });
    return m;
  }, [swingSuggestions]);

  const allocMap = useMemo(() => {
    const m: Record<string, Allocation> = {};
    allocations.forEach((a) => { m[a.ticker] = a; });
    return m;
  }, [allocations]);

  const buySignals = useMemo(() =>
    swingSuggestions.filter((s) => s.action === 'BUY').sort((a, b) => b.confidence - a.confidence),
    [swingSuggestions]);

  const mptActions = useMemo(() =>
    allocations.filter((a) => a.suggested_action && a.suggested_action !== 'Hold')
      .sort((a, b) => Math.abs((b.target_shares ?? 0) - (b.current_shares ?? 0)) - Math.abs((a.target_shares ?? 0) - (a.current_shares ?? 0))),
    [allocations]);

  // Build holdings grouped by account: the internal Alpaca bot account plus every
  // external/real brokerage account (each external position carries per-account lots).
  const accountGroups: AccountGroup[] = useMemo(() => {
    const groups: Record<string, AccountGroup> = {};
    const ensure = (label: string) => (groups[label] ??= { label, rows: [], totalValue: 0, totalPl: 0 });

    // Internal bot account (from /api/portfolio).
    (portfolio?.holdings || []).forEach((h: any) => {
      const tk = (h.ticker || '').toUpperCase();
      if (!tk) return;
      const g = ensure(INTERNAL_LABEL);
      const value = h.market_value ?? 0;
      const pl = h.unrealized_pl ?? null;
      g.rows.push({
        ticker: tk, shares: h.shares ?? 0, price: h.current_price ?? 0, value,
        pl, plPct: h.unrealized_pl_pct ?? null,
        buyTarget: h.buy_target ?? null, takeProfit: h.take_profit ?? null,
        ltEligible: h.lt_eligible, ltEligibleShares: h.lt_eligible_shares ?? null, nextEligibleDate: h.next_eligible_date ?? null,
      });
      g.totalValue += value;
      if (pl != null) g.totalPl += pl;
    });

    // External accounts — each consolidated position fans out into per-account lots.
    (externalPositions || []).forEach((pos: any) => {
      const tk = (pos.ticker || '').toUpperCase();
      const price = pos.current_price ?? 0;
      (pos.lots || []).forEach((lot: any) => {
        const label = lot.account_label || 'External Account';
        const g = ensure(label);
        let row = g.rows.find((r) => r.ticker === tk);
        if (!row) {
          row = { ticker: tk, shares: 0, price, value: 0, pl: 0, plPct: null,
                  buyTarget: pos.buy_target ?? null, takeProfit: pos.take_profit ?? null,
                  ltEligible: pos.lt_eligible, ltEligibleShares: pos.lt_eligible_shares ?? null, nextEligibleDate: pos.next_eligible_date ?? null };
          g.rows.push(row);
        }
        const shares = lot.shares ?? 0;
        const cost = shares * (lot.cost_basis_per_share ?? 0);
        const value = shares * price;
        row.shares += shares;
        row.value += value;
        row.pl = (row.pl ?? 0) + (value - cost);
        g.totalValue += value;
        g.totalPl += value - cost;
      });
    });

    return Object.values(groups)
      .map((g) => {
        g.rows.forEach((r) => { r.plPct = r.plPct ?? (r.value - (r.pl ?? 0) > 0 && r.pl != null ? (r.pl / (r.value - r.pl)) * 100 : null); });
        g.rows.sort((a, b) => b.value - a.value);
        return g;
      })
      // Bot account first, then largest accounts by value.
      .sort((a, b) => (a.label === INTERNAL_LABEL ? -1 : b.label === INTERNAL_LABEL ? 1 : b.totalValue - a.totalValue));
  }, [portfolio, externalPositions]);

  const holdingsCount = useMemo(() => accountGroups.reduce((n, g) => n + g.rows.length, 0), [accountGroups]);

  // Grand totals across every account (true "what I own").
  const grand = useMemo(() => {
    const value = accountGroups.reduce((s, g) => s + g.totalValue, 0);
    const pl = accountGroups.reduce((s, g) => s + g.totalPl, 0);
    const cost = value - pl;
    return { value, pl, plPct: cost > 0 ? (pl / cost) * 100 : null };
  }, [accountGroups]);

  const labelStyle: React.CSSProperties = {
    fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.05em',
    color: 'var(--text-secondary)', marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '6px',
  };

  return (
    <div style={{ gridColumn: '1 / -1', display: 'grid', gap: '16px' }}>

      {/* ── Portfolio Summary Bar ───────────────────────────────────────── */}
      <div className="glass-card" style={{ padding: '16px 20px', display: 'flex', gap: '24px', flexWrap: 'wrap', alignItems: 'center' }}>
        <div>
          <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '2px' }}>
            Total Holdings <span style={{ opacity: 0.6 }}>· all accounts</span>
          </div>
          <div style={{ fontSize: '22px', fontWeight: 700, color: 'var(--text-primary)' }}>
            {grand.value > 0 ? money(grand.value) : '—'}
          </div>
        </div>
        {grand.value > 0 && (
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '2px' }}>Unrealized P&amp;L</div>
            <div style={{ fontSize: '18px', fontWeight: 700, color: grand.pl >= 0 ? '#10B981' : '#F43F5E' }}>
              {grand.pl >= 0 ? '+' : ''}{money(Math.abs(grand.pl))}
              {grand.plPct != null && (
                <span style={{ fontSize: '13px', marginLeft: '6px' }}>({pct(grand.plPct)})</span>
              )}
            </div>
          </div>
        )}
        {portfolio?.totals?.cash != null && (
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '2px' }}>Cash <span style={{ opacity: 0.6 }}>· bot</span></div>
            <div style={{ fontSize: '16px', fontWeight: 600 }}>{money(portfolio.totals.cash)}</div>
          </div>
        )}
        {portfolio?.totals?.equity != null && (
          <div>
            <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginBottom: '2px' }}>Equity <span style={{ opacity: 0.6 }}>· bot</span></div>
            <div style={{ fontSize: '16px', fontWeight: 600 }}>{money(portfolio.totals.equity)}</div>
          </div>
        )}
        <div style={{ marginLeft: 'auto', display: 'flex', gap: '12px', alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ padding: '6px 14px', borderRadius: '20px', fontSize: '12px', fontWeight: 700, background: `${REGIME_COLOR[regime] ?? '#64748b'}22`, border: `1px solid ${REGIME_COLOR[regime] ?? '#64748b'}55`, color: REGIME_COLOR[regime] ?? '#64748b' }}>
            {regime.toUpperCase()} REGIME
          </div>
          {date && <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Signals as of {date}</div>}
        </div>
      </div>

      {/* ── Execution plan / Why ────────────────────────────────────────── */}
      <ExecutionPanel onTickerClick={onTickerClick} />

      {/* ── Approval gates + pending-trade queue ─────────────────────────── */}
      <ApprovalGatesPanel onTickerClick={onTickerClick} />

      {/* ── Two-column: Signals + Holdings ──────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.6fr', gap: '16px', alignItems: 'start' }}>

        {/* ── Left: Action Items ─────────────────────────────────────────── */}
        <div style={{ display: 'grid', gap: '12px' }}>

          {/* Swing BUY signals */}
          <div className="glass-card" style={{ padding: '16px' }}>
            <div style={labelStyle}>
              <Zap size={13} color="#10B981" /> Swing BUY signals ({buySignals.length})
            </div>
            {buySignals.length === 0 ? (
              <p style={{ fontSize: '12px', color: 'var(--text-secondary)', margin: 0 }}>No swing BUY signals today.</p>
            ) : (
              <div style={{ display: 'grid', gap: '8px' }}>
                {buySignals.map((s) => {
                  const info = tickerInfo[s.ticker];
                  const pr = priceMap[s.ticker];
                  return (
                    <button key={s.ticker} onClick={() => onTickerClick(s.ticker)}
                      style={{ width: '100%', textAlign: 'left', background: 'rgba(16,185,129,0.06)', border: '1px solid rgba(16,185,129,0.25)', borderRadius: '8px', padding: '10px 12px', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px' }}>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                          <strong style={{ color: 'var(--text-primary)', fontSize: '14px' }}>{s.ticker}</strong>
                          {pr?.price != null && <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>{money(pr.price)}</span>}
                          {pr?.d1 != null && <span style={{ fontSize: '11px', color: pr.d1 >= 0 ? '#10B981' : '#F43F5E' }}>{pct(pr.d1)}</span>}
                        </div>
                        {info?.company_name && <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '1px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{info.company_name}</div>}
                        {s.stop_loss && <div style={{ fontSize: '10px', color: 'var(--text-secondary)', marginTop: '2px' }}>Stop {money(s.stop_loss)} · Target {s.take_profit ? money(s.take_profit) : '—'} · {s.horizon_days}d</div>}
                      </div>
                      <div style={{ flexShrink: 0, textAlign: 'right' }}>
                        <div style={{ fontSize: '14px', fontWeight: 700, color: '#10B981' }}>BUY</div>
                        <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>{(s.confidence * 100).toFixed(0)}%</div>
                      </div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* MPT rebalancing alerts */}
          {mptActions.length > 0 && (
            <div className="glass-card" style={{ padding: '16px' }}>
              <div style={labelStyle}>
                <BarChart2 size={13} color="#00F2FE" /> Long-term MPT rebalances ({mptActions.length})
              </div>
              <div style={{ display: 'grid', gap: '6px' }}>
                {mptActions.slice(0, 8).map((a) => {
                  const isBuy = (a.suggested_action || '').includes('BUY');
                  const isSell = (a.suggested_action || '').includes('SELL');
                  const color = isBuy ? '#10B981' : isSell ? '#F43F5E' : 'var(--text-secondary)';
                  const pr = priceMap[a.ticker];
                  return (
                    <button key={a.ticker} onClick={() => onTickerClick(a.ticker)}
                      style={{ width: '100%', textAlign: 'left', background: 'rgba(255,255,255,0.03)', border: '1px solid var(--border-glass)', borderRadius: '6px', padding: '8px 10px', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px' }}>
                      <div>
                        <span style={{ fontWeight: 700, fontSize: '13px', color: 'var(--text-primary)' }}>{a.ticker}</span>
                        {pr?.price != null && <span style={{ fontSize: '11px', color: 'var(--text-secondary)', marginLeft: '6px' }}>{money(pr.price)}</span>}
                        <div style={{ fontSize: '10px', color: 'var(--text-secondary)', marginTop: '1px' }}>
                          {(a.weight * 100).toFixed(1)}% target · {a.current_shares?.toFixed(1) ?? '0'} → {a.target_shares?.toFixed(1) ?? '?'} sh
                        </div>
                      </div>
                      <span style={{ fontSize: '12px', fontWeight: 700, color, flexShrink: 0 }}>{a.suggested_action?.split(' ')[0]}</span>
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>

        {/* ── Right: Holdings grouped by account ───────────────────────── */}
        <div className="glass-card" style={{ padding: '16px' }}>
          <div style={labelStyle}>
            <Layers size={13} color="#a78bfa" /> Current holdings ({holdingsCount} · {accountGroups.length} {accountGroups.length === 1 ? 'account' : 'accounts'})
          </div>
          {holdingsCount === 0 ? (
            <p style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
              No holdings found. Link a brokerage account in the External Accounts tab, or let the bot open positions.
            </p>
          ) : (
            <div style={{ display: 'grid', gap: '14px' }}>
              {accountGroups.map((g) => {
                const theme = accountTheme(g.label);
                return (
                  <div key={g.label} style={{ border: `1px solid ${theme.border}`, background: theme.bg, borderRadius: '10px', overflow: 'hidden' }}>
                    {/* Account header */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px', flexWrap: 'wrap', padding: '10px 12px', borderBottom: `1px solid ${theme.border}` }}>
                      <span style={{ fontSize: '11px', fontWeight: 700, letterSpacing: '0.04em', textTransform: 'uppercase', color: theme.text, background: theme.badgeBg, padding: '3px 9px', borderRadius: '12px' }}>
                        {g.label}
                      </span>
                      <div style={{ display: 'flex', gap: '12px', alignItems: 'baseline' }}>
                        <span style={{ fontSize: '13px', fontWeight: 700, color: 'var(--text-primary)' }}>{money(g.totalValue)}</span>
                        {g.totalPl !== 0 && (
                          <span style={{ fontSize: '11px', fontWeight: 600, color: g.totalPl >= 0 ? '#10B981' : '#F43F5E' }}>
                            {g.totalPl >= 0 ? '+' : ''}{money(Math.abs(g.totalPl))}
                          </span>
                        )}
                      </div>
                    </div>
                    {/* Holdings table */}
                    <div style={{ overflowX: 'auto' }}>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '12px' }}>
                        <thead>
                          <tr style={{ borderBottom: '1px solid rgba(255,255,255,0.07)' }}>
                            {['Ticker', 'Name', 'Price', '1D', '1M', 'Shares', 'Value', 'P&L', 'Targets', 'Signal'].map((h) => (
                              <th key={h} style={{ padding: '6px 8px', textAlign: h === 'Ticker' || h === 'Name' ? 'left' : 'right', fontWeight: 600, fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-secondary)', whiteSpace: 'nowrap' }}>{h}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {g.rows.map((r) => {
                            const tk = r.ticker;
                            const pr = priceMap[tk];
                            const info = tickerInfo[tk];
                            const swing = swingMap[tk];
                            const tier = classification[tk]?.tier;
                            const price = r.price || pr?.price || null;
                            return (
                              <tr key={tk} onClick={() => onTickerClick(tk)}
                                style={{ borderBottom: '1px solid rgba(255,255,255,0.04)', cursor: 'pointer', transition: 'background 0.1s' }}
                                onMouseEnter={(e) => (e.currentTarget.style.background = 'rgba(255,255,255,0.04)')}
                                onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}>
                                <td style={{ padding: '9px 8px', whiteSpace: 'nowrap' }}>
                                  <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                                    <strong style={{ color: 'var(--text-primary)' }}>{tk}</strong>
                                    {tier && <span style={{ fontSize: '9px', padding: '1px 4px', borderRadius: '3px', background: `${TIER_COLOR[tier]}22`, color: TIER_COLOR[tier], fontWeight: 700 }}>{TIER_LABEL[tier] ?? tier}</span>}
                                  </div>
                                </td>
                                <td style={{ padding: '9px 8px', color: 'var(--text-secondary)', maxWidth: '140px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                  {info?.company_name ?? '—'}
                                </td>
                                <td style={{ padding: '9px 8px', textAlign: 'right', fontWeight: 600 }}>
                                  {price != null ? money(price) : '—'}
                                </td>
                                <td style={{ padding: '9px 8px', textAlign: 'right', color: pr?.d1 == null ? 'var(--text-secondary)' : pr.d1 >= 0 ? '#10B981' : '#F43F5E' }}>
                                  {pct(pr?.d1)}
                                </td>
                                <td style={{ padding: '9px 8px', textAlign: 'right', color: pr?.m1 == null ? 'var(--text-secondary)' : pr.m1 >= 0 ? '#10B981' : '#F43F5E' }}>
                                  {pct(pr?.m1)}
                                </td>
                                <td style={{ padding: '9px 8px', textAlign: 'right' }}>
                                  {r.shares.toFixed(2)}
                                </td>
                                <td style={{ padding: '9px 8px', textAlign: 'right', fontWeight: 600 }}>
                                  {money(r.value)}
                                </td>
                                <td style={{ padding: '9px 8px', textAlign: 'right', color: r.pl == null ? 'var(--text-secondary)' : r.pl >= 0 ? '#10B981' : '#F43F5E' }}>
                                  {r.pl != null ? `${r.pl >= 0 ? '+' : ''}${money(Math.abs(r.pl))}` : '—'}
                                  {r.plPct != null && <span style={{ fontSize: '10px', marginLeft: '3px' }}>({pct(r.plPct)})</span>}
                                </td>
                                <td style={{ padding: '9px 8px', textAlign: 'right', whiteSpace: 'nowrap' }}>
                                  {r.buyTarget != null || r.takeProfit != null ? (
                                    <span style={{ fontSize: '10px' }} title="Add a tranche on a dip to the buy price; take profit at the upper price (relative to cost basis)">
                                      {r.buyTarget != null && <span style={{ color: '#10B981' }}>↓{money(r.buyTarget)}</span>}
                                      {r.takeProfit != null && (
                                        <span style={{ marginLeft: '4px' }}
                                          title={r.ltEligible
                                            ? 'Long-term tax-eligible — take-profit trim is actionable now'
                                            : `Take-profit waits for long-term tax eligibility (held >1yr). Eligible: ${(r.ltEligibleShares ?? 0).toFixed(1)}/${r.shares.toFixed(1)} sh${r.nextEligibleDate ? ` · next ${r.nextEligibleDate}` : ''}`}>
                                          <span style={{ color: '#F59E0B' }}>↑{money(r.takeProfit)}</span>
                                          {r.ltEligible === false && (
                                            <span style={{ color: 'var(--text-secondary)', marginLeft: '2px' }}>
                                              🔒{r.nextEligibleDate ? <span style={{ fontSize: '9px', marginLeft: '2px' }}>{r.nextEligibleDate.slice(2)}</span> : ''}
                                            </span>
                                          )}
                                        </span>
                                      )}
                                    </span>
                                  ) : <span style={{ color: 'var(--text-secondary)' }}>—</span>}
                                </td>
                                <td style={{ padding: '9px 8px', textAlign: 'right' }}>
                                  {swing ? (
                                    <span style={{ fontSize: '11px', fontWeight: 700, color: swing.action === 'BUY' ? '#10B981' : 'var(--text-secondary)' }}>
                                      {swing.action} {(swing.confidence * 100).toFixed(0)}%
                                    </span>
                                  ) : allocMap[tk] ? (
                                    <span style={{ fontSize: '10px', color: '#00F2FE' }}>MPT</span>
                                  ) : (
                                    <span style={{ color: 'var(--text-secondary)' }}>—</span>
                                  )}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
