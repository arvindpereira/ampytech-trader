'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { apiUrl } from '../lib/api';
import { Brain, BookOpen, Search, RefreshCw, CheckCircle2, XCircle, AlertTriangle, Circle, Loader2 } from 'lucide-react';

const EXAMPLES = [
  "What's the outlook for quantum computing companies in H2? Rank the companies.",
  "What's the outlook for NVIDIA over the next year? What are analyst price targets?",
  "What is NVDA's consensus price target?",
];

const RESEARCH_STEPS = [
  { threshold: 5, label: 'Understand your question' },
  { threshold: 15, label: 'Refresh company knowledge base' },
  { threshold: 35, label: 'Load snapshots & analyst items' },
  { threshold: 45, label: 'Select analysis tier' },
  { threshold: 55, label: 'Synthesize research narrative (AI)' },
  { threshold: 90, label: 'Assemble report' },
  { threshold: 95, label: 'Save draft' },
];

function stepState(progress: number, threshold: number, nextThreshold: number) {
  if (progress >= nextThreshold) return 'done';
  if (progress >= threshold) return 'active';
  return 'pending';
}

type Report = {
  template?: string;
  tldr?: string;
  ranked_companies?: Array<{ rank: number; ticker: string; score: number; coverage_pct?: number }>;
  snapshot_summary?: Record<string, unknown>;
  outlook_narrative?: string;
  winners_summary?: string;
  losers_summary?: string;
  theme_narrative?: string;
  catalysts?: string[];
  risks?: string[];
  caveats?: string[];
};

export default function ResearchAnalystPanel() {
  const [subTab, setSubTab] = useState<'query' | 'library'>('query');
  const [query, setQuery] = useState('');
  const [deepResearch, setDeepResearch] = useState(false);
  const [kbStatus, setKbStatus] = useState<{ ticker_count?: number; last_refreshed?: string } | null>(null);
  const [themes, setThemes] = useState<Array<{ id: string; label: string }>>([]);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [stage, setStage] = useState('');
  const [error, setError] = useState('');
  const [threadId, setThreadId] = useState<string | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [tier, setTier] = useState('');
  const [library, setLibrary] = useState<Array<Record<string, unknown>>>([]);
  const [rejectOpen, setRejectOpen] = useState(false);
  const [feedback, setFeedback] = useState('');

  const fetchKb = useCallback(async () => {
    try {
      const r = await fetch(apiUrl('/api/research/kb/status'));
      if (r.ok) setKbStatus(await r.json());
    } catch { /* ignore */ }
  }, []);

  const fetchLibrary = useCallback(async () => {
    try {
      const r = await fetch(apiUrl('/api/research/library?status=published'));
      if (r.ok) {
        const j = await r.json();
        setLibrary(j.reports || []);
      }
    } catch { /* ignore */ }
  }, []);

  const fetchThemes = useCallback(async () => {
    try {
      const r = await fetch(apiUrl('/api/research/themes'));
      if (r.ok) {
        const j = await r.json();
        setThemes((j.themes || []).map((t: { id: string; label: string }) => ({ id: t.id, label: t.label })));
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    fetchKb();
    fetchThemes();
    fetchLibrary();
  }, [fetchKb, fetchThemes, fetchLibrary]);

  const pollJob = async (jobId: string) => {
    for (let i = 0; i < 120; i++) {
      const r = await fetch(apiUrl(`/api/research/query/result?job_id=${jobId}`));
      const j = await r.json();
      if (j.status === 'done' && j.result) {
        setReport(j.result.report);
        setThreadId(j.result.thread_id);
        setTier(j.result.tier || '');
        setLoading(false);
        setProgress(100);
        setStage('');
        setError('');
        return;
      }
      if (j.status === 'error') {
        setLoading(false);
        setError(j.error || 'Research failed');
        setStage('');
        return;
      }
      setProgress(j.progress ?? 0);
      setStage(j.stage || j.status || 'Running…');
      await new Promise((res) => setTimeout(res, 1500));
    }
    setLoading(false);
    setError('Timed out waiting for research to complete');
    setStage('');
  };

  const runQuery = async () => {
    if (!query.trim()) return;
    setLoading(true);
    setReport(null);
    setProgress(0);
    setStage('Starting…');
    setError('');
    setTier('');
    try {
      const r = await fetch(apiUrl('/api/research/query'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query, deep_research: deepResearch }),
      });
      const j = await r.json();
      if (j.job_id) await pollJob(j.job_id);
      else {
        setLoading(false);
        setError('Failed to start research job');
        setStage('');
      }
    } catch (e) {
      setLoading(false);
      setError(String(e));
      setStage('');
    }
  };

  const publish = async () => {
    if (!threadId) return;
    await fetch(apiUrl(`/api/research/thread/${threadId}/publish`), { method: 'POST' });
    fetchLibrary();
    alert('Published to library and wiki export.');
  };

  const reject = async () => {
    if (!threadId || !feedback.trim()) return;
    await fetch(apiUrl(`/api/research/thread/${threadId}/reject`), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ feedback_notes: feedback }),
    });
    setRejectOpen(false);
    setFeedback('');
    setReport(null);
    setThreadId(null);
  };

  const openLibraryReport = async (id: string) => {
    const r = await fetch(apiUrl(`/api/research/thread/${id}`));
    if (!r.ok) return;
    const j = await r.json();
    const assistant = (j.messages || []).find((m: { role: string }) => m.role === 'assistant');
    setReport(assistant?.structured || null);
    setThreadId(id);
    setSubTab('query');
  };

  const labelStyle: React.CSSProperties = {
    fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em',
    color: 'var(--text-secondary)', marginBottom: '5px',
  };

  const progressPanel = () => {
    const isError = Boolean(error);
    return (
      <div style={{ padding: '8px 0' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '14px' }}>
          {!isError && <RefreshCw size={20} className="animate-spin" color="#a78bfa" />}
          {isError && <XCircle size={20} color="#EF4444" />}
          <div>
            <div style={{ fontWeight: 600, fontSize: '14px', color: isError ? '#EF4444' : 'var(--text-primary)' }}>
              {isError ? 'Research failed' : 'Research in progress'}
            </div>
            <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginTop: '2px' }}>
              {isError ? error : 'Please wait while the analyst prepares your report.'}
            </div>
          </div>
          {!isError && (
            <span style={{ marginLeft: 'auto', fontSize: '12px', color: '#a78bfa', fontWeight: 600 }}>
              {progress}%
            </span>
          )}
        </div>

        {!isError && (
          <>
            <div style={{ height: '6px', background: 'rgba(255,255,255,0.06)', borderRadius: '999px', overflow: 'hidden', marginBottom: '16px' }}>
              <div style={{ width: `${progress}%`, height: '100%', background: '#a78bfa', transition: 'width 0.4s' }} />
            </div>
            {stage && (
              <div style={{ fontSize: '12px', color: '#c4b5fd', marginBottom: '14px', padding: '8px 10px', borderRadius: '6px', background: 'rgba(139,92,246,0.12)', border: '1px solid rgba(139,92,246,0.2)' }}>
                {stage}
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {RESEARCH_STEPS.map((step, i) => {
                const next = RESEARCH_STEPS[i + 1]?.threshold ?? 101;
                const state = stepState(progress, step.threshold, next);
                const icon = state === 'done'
                  ? <CheckCircle2 size={14} color="#10B981" />
                  : state === 'active'
                    ? <Loader2 size={14} className="animate-spin" color="#a78bfa" />
                    : <Circle size={14} color="var(--text-secondary)" style={{ opacity: 0.35 }} />;
                return (
                  <div key={step.threshold} style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: state === 'pending' ? 'var(--text-secondary)' : 'var(--text-primary)', opacity: state === 'pending' ? 0.55 : 1 }}>
                    {icon}
                    <span style={{ fontWeight: state === 'active' ? 600 : 400 }}>{step.label}</span>
                  </div>
                );
              })}
            </div>
            <p style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '14px', marginBottom: 0, lineHeight: 1.5 }}>
              AI synthesis can take 30–90 seconds for complex themes. Simple lookups finish faster.
            </p>
          </>
        )}
      </div>
    );
  };

  return (
    <section style={{ gridColumn: '1 / -1', display: 'grid', gridTemplateColumns: 'minmax(280px, 1fr) minmax(360px, 2fr)', gap: '16px' }}>
      <div className="glass-card" style={{ padding: '16px' }}>
        <div className="toggle-group" style={{ marginBottom: '14px' }}>
          <button className={`toggle-btn ${subTab === 'query' ? 'active' : ''}`} onClick={() => setSubTab('query')}>New query</button>
          <button className={`toggle-btn ${subTab === 'library' ? 'active' : ''}`} onClick={() => { setSubTab('library'); fetchLibrary(); }}>Library</button>
        </div>

        {subTab === 'query' && (
          <>
            <h2 style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: 0 }}>
              <Brain size={20} color="#a78bfa" /> Research Analyst
            </h2>
            <p style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '12px' }}>
              KB: {kbStatus?.ticker_count ?? '—'} companies
              {kbStatus?.last_refreshed ? ` · updated ${kbStatus.last_refreshed}` : ''}
            </p>
            <textarea
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Ask about a stock, theme, or sector…"
              rows={4}
              style={{ width: '100%', background: 'rgba(0,0,0,0.25)', border: '1px solid var(--border-glass)', borderRadius: '8px', color: 'var(--text-primary)', padding: '10px', fontSize: '13px', resize: 'vertical' }}
            />
            <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12px', color: 'var(--text-secondary)', margin: '10px 0' }}>
              <input type="checkbox" checked={deepResearch} onChange={(e) => setDeepResearch(e.target.checked)} />
              Deep research (OpenAI expert tier)
            </label>
            <button onClick={runQuery} disabled={loading || !query.trim()}
              style={{ width: '100%', padding: '10px', borderRadius: '8px', border: 'none', background: 'rgba(139,92,246,0.25)', color: 'var(--text-primary)', fontWeight: 600, cursor: loading ? 'default' : 'pointer', opacity: loading ? 0.7 : 1 }}>
              {loading ? (
                <span style={{ display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: '8px' }}>
                  <RefreshCw size={14} className="animate-spin" /> Analyzing… {progress > 0 ? `${progress}%` : ''}
                </span>
              ) : 'Run analysis'}
            </button>
            {tier && <p style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '8px' }}>Tier: {tier}</p>}
            <div style={{ marginTop: '16px' }}>
              <div style={labelStyle}>Examples</div>
              {EXAMPLES.map((ex) => (
                <button key={ex} onClick={() => setQuery(ex)}
                  style={{ display: 'block', width: '100%', textAlign: 'left', marginBottom: '6px', padding: '8px', fontSize: '11px', background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border-glass)', borderRadius: '6px', color: 'var(--text-secondary)', cursor: 'pointer' }}>
                  {ex}
                </button>
              ))}
            </div>
            {themes.length > 0 && (
              <div style={{ marginTop: '12px', fontSize: '11px', color: 'var(--text-secondary)' }}>
                Themes: {themes.map((t) => t.label).join(', ')}
              </div>
            )}
          </>
        )}

        {subTab === 'library' && (
          <>
            <h2 style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: 0 }}>
              <BookOpen size={20} /> Published reports
            </h2>
            {library.length === 0 && <p style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>No published reports yet.</p>}
            {library.map((rep) => (
              <button key={String(rep.id)} onClick={() => openLibraryReport(String(rep.id))}
                style={{ display: 'block', width: '100%', textAlign: 'left', marginBottom: '8px', padding: '10px', background: 'rgba(255,255,255,0.04)', border: '1px solid var(--border-glass)', borderRadius: '8px', cursor: 'pointer' }}>
                <div style={{ fontWeight: 600, color: 'var(--text-primary)', fontSize: '13px' }}>{String(rep.title)}</div>
                <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>{String(rep.intent)} · {String(rep.published_at || rep.created_at)}</div>
              </button>
            ))}
          </>
        )}
      </div>

      <div className="glass-card" style={{ padding: '16px', minHeight: '400px' }}>
        {!report && !loading && !error && (
          <div style={{ color: 'var(--text-secondary)', fontSize: '13px', textAlign: 'center', marginTop: '80px' }}>
            <Search size={32} style={{ opacity: 0.4, marginBottom: '12px' }} />
            <div>Run a query to generate a templated research report.</div>
          </div>
        )}
        {(loading || error) && progressPanel()}
        {report && (
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '12px', marginBottom: '14px' }}>
              <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>Template: {report.template}</div>
              {threadId && (
                <div style={{ display: 'flex', gap: '8px' }}>
                  <button onClick={publish} style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '11px', padding: '6px 10px', borderRadius: '6px', border: '1px solid rgba(16,185,129,0.4)', background: 'rgba(16,185,129,0.1)', color: '#10B981', cursor: 'pointer' }}>
                    <CheckCircle2 size={14} /> Publish
                  </button>
                  <button onClick={() => setRejectOpen(true)} style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '11px', padding: '6px 10px', borderRadius: '6px', border: '1px solid rgba(239,68,68,0.4)', background: 'rgba(239,68,68,0.1)', color: '#EF4444', cursor: 'pointer' }}>
                    <XCircle size={14} /> Reject
                  </button>
                </div>
              )}
            </div>
            {report.tldr && (
              <div style={{ fontSize: '14px', lineHeight: 1.6, padding: '12px', borderRadius: '8px', background: 'rgba(139,92,246,0.1)', borderLeft: '3px solid #a78bfa', marginBottom: '16px' }}>
                {report.tldr}
              </div>
            )}
            {report.ranked_companies && report.ranked_companies.length > 0 && (
              <div style={{ marginBottom: '16px' }}>
                <div style={labelStyle}>Ranked companies (computed)</div>
                <table className="trade-table" style={{ width: '100%', fontSize: '12px' }}>
                  <thead>
                    <tr><th>Rank</th><th>Ticker</th><th>Score</th><th>Coverage</th></tr>
                  </thead>
                  <tbody>
                    {report.ranked_companies.map((r) => (
                      <tr key={r.ticker}>
                        <td>{r.rank}</td><td>{r.ticker}</td><td>{r.score}</td><td>{r.coverage_pct ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {report.theme_narrative && <div style={{ marginBottom: '12px' }}><div style={labelStyle}>Theme</div><p style={{ fontSize: '13px', lineHeight: 1.6 }}>{report.theme_narrative}</p></div>}
            {report.outlook_narrative && <div style={{ marginBottom: '12px' }}><div style={labelStyle}>Outlook</div><p style={{ fontSize: '13px', lineHeight: 1.6 }}>{report.outlook_narrative}</p></div>}
            {report.winners_summary && <div style={{ marginBottom: '8px' }}><div style={labelStyle}>Winners</div><p style={{ fontSize: '12.5px' }}>{report.winners_summary}</p></div>}
            {report.losers_summary && <div style={{ marginBottom: '8px' }}><div style={labelStyle}>Laggards</div><p style={{ fontSize: '12.5px' }}>{report.losers_summary}</p></div>}
            {(report.caveats || []).length > 0 && (
              <div style={{ marginTop: '14px', borderTop: '1px solid var(--border-glass)', paddingTop: '12px' }}>
                <div style={{ ...labelStyle, color: 'var(--color-gold)' }}><AlertTriangle size={12} style={{ display: 'inline', marginRight: '4px' }} />Caveats</div>
                <ul style={{ margin: 0, paddingLeft: '18px', fontSize: '12px', color: 'var(--text-secondary)' }}>
                  {report.caveats!.map((c, i) => <li key={i}>{c}</li>)}
                </ul>
              </div>
            )}
          </div>
        )}
      </div>

      {rejectOpen && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
          <div className="glass-card" style={{ padding: '20px', width: 'min(480px, 90vw)' }}>
            <h3 style={{ marginTop: 0 }}>Reject report</h3>
            <p style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>What was wrong with this report?</p>
            <textarea value={feedback} onChange={(e) => setFeedback(e.target.value)} rows={4}
              style={{ width: '100%', background: 'rgba(0,0,0,0.25)', border: '1px solid var(--border-glass)', borderRadius: '8px', color: 'var(--text-primary)', padding: '10px' }} />
            <div style={{ display: 'flex', gap: '10px', justifyContent: 'flex-end', marginTop: '12px' }}>
              <button onClick={() => setRejectOpen(false)}>Cancel</button>
              <button onClick={reject} disabled={!feedback.trim()} style={{ color: '#EF4444' }}>Reject</button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
