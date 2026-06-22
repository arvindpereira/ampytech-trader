/** Inline research citations: item:13 → [13] link or [13]* with footnote. */
import React, { useMemo } from 'react';

export type CitationRef = {
  ref: string;
  kind?: string;
  id?: number;
  ticker?: string;
  title?: string;
  label?: string;
  value?: unknown;
  url?: string | null;
  source?: string;
  published_at?: string;
  missing?: boolean;
  note?: string;
};

const CITE_RE = /item:(\d+)|snapshot:([\w_]+)/g;

export function extractCitationRefs(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  let m: RegExpExecArray | null;
  const re = new RegExp(CITE_RE.source, 'g');
  while ((m = re.exec(text)) !== null) {
    const ref = m[1] ? `item:${m[1]}` : `snapshot:${m[2]}`;
    if (!seen.has(ref)) {
      seen.add(ref);
      out.push(ref);
    }
  }
  return out;
}

function bracketLabel(ref: string): string {
  if (ref.startsWith('item:')) return `[${ref.slice(5)}]`;
  if (ref.startsWith('snapshot:')) return `[${ref.slice(9)}]`;
  return `[${ref}]`;
}

const linkStyle: React.CSSProperties = {
  color: '#a78bfa',
  textDecoration: 'underline',
  fontWeight: 600,
  fontSize: '0.92em',
};

const noLinkStyle: React.CSSProperties = {
  color: '#c4b5fd',
  fontWeight: 600,
  fontSize: '0.92em',
  cursor: 'help',
};

export function CitationText({
  text,
  citationsByRef,
}: {
  text: string;
  citationsByRef?: Record<string, CitationRef>;
}) {
  const nodes = useMemo(() => {
    const parts: React.ReactNode[] = [];
    let last = 0;
    let m: RegExpExecArray | null;
    const re = new RegExp(CITE_RE.source, 'g');
    let key = 0;
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) parts.push(text.slice(last, m.index));
      const ref = m[1] ? `item:${m[1]}` : `snapshot:${m[2]}`;
      const label = bracketLabel(ref);
      const meta = citationsByRef?.[ref];
      const url = meta?.url;
      if (url) {
        parts.push(
          <a key={key++} href={url} target="_blank" rel="noopener noreferrer" style={linkStyle} title={meta?.title || ref}>
            {label}
          </a>,
        );
      } else {
        const tip = meta?.kind === 'snapshot'
          ? `${meta.label || ref} — snapshot field (no external URL)`
          : meta?.title
            ? `${meta.title} — no url available`
            : 'no url available';
        parts.push(
          <span key={key++} style={noLinkStyle} title={tip}>
            {label}*
          </span>,
        );
      }
      last = m.index + m[0].length;
    }
    if (last < text.length) parts.push(text.slice(last));
    return parts;
  }, [text, citationsByRef]);

  return <>{nodes}</>;
}

export function CitationFootnotes({
  refs,
  citationsByRef,
}: {
  refs: string[];
  citationsByRef?: Record<string, CitationRef>;
}) {
  if (!refs.length) return null;
  return (
    <div style={{ marginTop: '10px', paddingTop: '8px', borderTop: '1px dashed var(--border-glass)', fontSize: '11px', color: 'var(--text-secondary)' }}>
      <div style={{ fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '6px', fontSize: '10px' }}>
        Reference notes
      </div>
      <ul style={{ margin: 0, paddingLeft: '16px', lineHeight: 1.55 }}>
        {refs.map((ref) => {
          const meta = citationsByRef?.[ref];
          const label = bracketLabel(ref);
          if (meta?.kind === 'snapshot') {
            return (
              <li key={ref}>
                <code style={{ fontSize: '10px' }}>{label}</code>
                {' '}{meta.label || ref}
                {meta.ticker ? ` (${meta.ticker})` : ''}: {String(meta.value ?? 'n/a')}
                {' '}— <em>snapshot field, no external URL</em>
              </li>
            );
          }
          if (meta?.url) {
            return (
              <li key={ref}>
                <code style={{ fontSize: '10px' }}>{label}</code>
                {' '}
                <a href={meta.url} target="_blank" rel="noopener noreferrer" style={{ color: '#a78bfa' }}>
                  {meta.title || meta.ref}
                </a>
                {meta.ticker ? ` · ${meta.ticker}` : ''}
              </li>
            );
          }
          return (
            <li key={ref}>
              <code style={{ fontSize: '10px' }}>{label}</code>
              {' '}{meta?.title || ref}
              {meta?.ticker ? ` · ${meta.ticker}` : ''}
              {' '}— <em>no url available</em>
            </li>
          );
        })}
      </ul>
      <div style={{ marginTop: '4px', fontSize: '10px' }}>* no url available</div>
    </div>
  );
}

export function collectReportCitationRefs(report: {
  tldr?: string;
  outlook_narrative?: string;
  theme_narrative?: string;
  spillover_narrative?: string;
  sector_narrative?: string;
  event_summary?: string;
  winners_summary?: string;
  losers_summary?: string;
  catalysts?: string[];
  risks?: string[];
  caveats?: string[];
  related_holdings?: Array<{ impact?: string }>;
  holdings_impact?: Array<{ impact?: string }>;
}): string[] {
  const chunks = [
    report.tldr,
    report.outlook_narrative,
    report.theme_narrative,
    report.spillover_narrative,
    report.sector_narrative,
    report.event_summary,
    report.winners_summary,
    report.losers_summary,
    ...(report.catalysts || []),
    ...(report.risks || []),
    ...(report.caveats || []),
    ...(report.related_holdings || []).map((h) => h.impact),
    ...(report.holdings_impact || []).map((h) => h.impact),
  ].filter(Boolean) as string[];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const c of chunks) {
    for (const ref of extractCitationRefs(c)) {
      if (!seen.has(ref)) {
        seen.add(ref);
        out.push(ref);
      }
    }
  }
  return out;
}

/** Build citations_by_ref from report fields when only source_bundle exists (older reports). */
export function buildCitationsMap(report: {
  citations_by_ref?: Record<string, CitationRef>;
  citations?: CitationRef[];
  source_bundle?: CitationRef[];
}): Record<string, CitationRef> {
  if (report.citations_by_ref) return report.citations_by_ref;
  const map: Record<string, CitationRef> = {};
  for (const c of report.citations || []) map[c.ref] = c;
  for (const c of report.source_bundle || []) map[c.ref] = { ...map[c.ref], ...c };
  return map;
}

export function NarrativeBlock({
  text,
  citationsByRef,
  showFootnotes = true,
}: {
  text: string;
  citationsByRef?: Record<string, CitationRef>;
  showFootnotes?: boolean;
}) {
  const refs = useMemo(() => extractCitationRefs(text), [text]);
  return (
    <div>
      <p style={{ fontSize: '13px', lineHeight: 1.6, margin: 0 }}>
        <CitationText text={text} citationsByRef={citationsByRef} />
      </p>
      {showFootnotes && <CitationFootnotes refs={refs} citationsByRef={citationsByRef} />}
    </div>
  );
}
