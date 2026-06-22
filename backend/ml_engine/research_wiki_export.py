"""Export published research threads to Jekyll wiki markdown."""
import json
import os
import re
from datetime import date
from typing import List, Optional

from app.core.config import BASE_DIR
from app.database import ResearchMessage, ResearchThread, SessionLocal
from ml_engine.citation_resolver import render_inline_citations, render_inline_citations_html

WIKI_ROOT = os.path.join(os.path.dirname(BASE_DIR), "research-wiki")  # repo root / research-wiki
REPORTS_DIR = os.path.join(WIKI_ROOT, "reports")
SITE_DIR = os.path.join(WIKI_ROOT, "site")

_HTML_STYLE = """
body { font-family: Georgia, serif; max-width: 820px; margin: 0 auto; padding: 24px; line-height: 1.6;
  color: #1a1a2e; background: #f8f9fc; }
a { color: #5b21b6; }
header { margin-bottom: 24px; font-weight: 700; }
article { background: #fff; padding: 28px; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
.meta { font-size: 0.85rem; color: #666; }
table { border-collapse: collapse; width: 100%; margin: 16px 0; }
th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
th { background: #f3f4f6; }
ul { padding-left: 1.2rem; }
"""


def _esc(s) -> str:
    if s is None:
        return ""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "report").lower()).strip("-")
    return s[:80] or "report"


def _report_message(db, thread_id: str) -> Optional[ResearchMessage]:
    return (
        db.query(ResearchMessage)
        .filter(ResearchMessage.thread_id == thread_id, ResearchMessage.role == "assistant")
        .order_by(ResearchMessage.id.asc())
        .first()
    )


def _by_ref(report: dict) -> dict:
    return report.get("citations_by_ref") or {}


def _md_cite(report: dict, text: str) -> str:
    return render_inline_citations(text or "", _by_ref(report))


def _html_cite(report: dict, text: str) -> str:
    return render_inline_citations_html(text or "", _by_ref(report))


def _render_sources_md(report: dict) -> List[str]:
    bundle = report.get("source_bundle") or []
    citations = report.get("citations") or []
    if not bundle and not citations:
        return []
    lines = [
        "## Sources\n",
        "`item:N` = external headline/analyst row from the research KB. "
        "`snapshot:field` = computed fact from `company_snapshots` (price, momentum, targets, etc.).\n",
    ]
    if bundle:
        lines.append("### Articles & analyst items reviewed\n")
        for s in bundle:
            ref = s.get("ref", "")
            title = (s.get("title") or "—").replace("|", "/")
            src = s.get("source") or ""
            url = s.get("url")
            if url:
                lines.append(f"- `{ref}` **{s.get('ticker', '')}** [{title}]({url}) — _{src}_")
            else:
                lines.append(f"- `{ref}` **{s.get('ticker', '')}** {title} — _{src}_ (no publisher URL on file)")
    snap_rows = [c for c in citations if c.get("kind") == "snapshot"]
    if snap_rows:
        lines.append("\n### Snapshot fields cited\n")
        for s in snap_rows:
            val = s.get("value")
            lines.append(
                f"- `{s.get('ref')}` **{s.get('ticker')}** {s.get('label')}: {val} "
                f"(as of {s.get('as_of') or 'n/a'}, from {s.get('source_table')})"
            )
    lines.append("")
    lines.append("*\\* = no url available*\n")
    return lines


def render_markdown(thread: ResearchThread, report: dict) -> str:
    tickers = json.loads(thread.tickers_json) if thread.tickers_json else []
    fm = {
        "title": thread.title or "Research Report",
        "date": (thread.published_at or thread.created_at or date.today().isoformat())[:10],
        "intent": thread.intent,
        "tickers": tickers,
        "thread_id": thread.id,
        "coverage_pct": thread.coverage_pct,
        "layout": "report",
    }
    lines = ["---"]
    for k, v in fm.items():
        if isinstance(v, list):
            lines.append(f"{k}: {json.dumps(v)}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---\n")

    gen = report.get("generation") or {}
    if report.get("generation_note"):
        lines.append(f"*{report['generation_note']}*\n")
    elif gen.get("note"):
        lines.append(f"*{gen['note']}*\n")

    lines.append(f"## TLDR\n\n{_md_cite(report, report.get('tldr', ''))}\n")

    if report.get("template") == "theme_rank":
        lines.append("## Ranked companies\n")
        lines.append("| Rank | Ticker | Score | Coverage |")
        lines.append("|------|--------|-------|----------|")
        for r in report.get("ranked_companies") or []:
            lines.append(
                f"| {r.get('rank')} | {r.get('ticker')} | {r.get('score')} | {r.get('coverage_pct')} |"
            )
        if report.get("winners_summary"):
            lines.append(f"\n### Winners\n\n{_md_cite(report, report['winners_summary'])}\n")
        if report.get("losers_summary"):
            lines.append(f"\n### Laggards\n\n{_md_cite(report, report['losers_summary'])}\n")
        if report.get("theme_narrative"):
            lines.append(f"\n## Theme narrative\n\n{_md_cite(report, report['theme_narrative'])}\n")

    elif report.get("template") == "ticker_outlook":
        snap = report.get("snapshot_summary") or {}
        lines.append("## Snapshot\n")
        lines.append(f"- Price: {snap.get('price')}")
        lines.append(f"- Consensus target: {snap.get('target_mean')} (high {snap.get('target_high')}, low {snap.get('target_low')})")
        lines.append(f"- Upside: {snap.get('upside_pct')}")
        lines.append(f"- Analysts: {snap.get('num_analysts')} ({snap.get('recommendation_key')})")
        lines.append(f"- Tier: {snap.get('tier')}\n")
        if report.get("outlook_narrative"):
            lines.append(f"## Outlook\n\n{_md_cite(report, report['outlook_narrative'])}\n")

    lines.extend(_render_sources_md(report))

    for section in ("catalysts", "risks", "caveats"):
        items = report.get(section)
        if items:
            lines.append(f"## {section.title()}\n")
            for it in items:
                lines.append(f"- {_md_cite(report, str(it))}")
            lines.append("")

    return "\n".join(lines)


def render_html(thread: ResearchThread, report: dict) -> str:
    """Standalone HTML page (no Jekyll/Ruby required)."""
    tickers = json.loads(thread.tickers_json) if thread.tickers_json else []
    title = _esc(thread.title or "Research Report")
    d = _esc((thread.published_at or thread.created_at or "")[:10])
    parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>{title}</title><style>{_HTML_STYLE}</style></head><body>",
        "<header><a href='index.html'>← Research Library</a></header>",
        "<article>",
        f"<h1>{title}</h1>",
        f"<p class='meta'>Date: {d} · Intent: {_esc(thread.intent)} · "
        f"Tickers: {_esc(', '.join(tickers))} · Coverage: {thread.coverage_pct}</p>",
    ]
    gen_note = report.get("generation_note") or (report.get("generation") or {}).get("note")
    if gen_note:
        parts.append(f"<p class='meta'><em>{_esc(gen_note)}</em></p>")
    parts.append(f"<h2>TLDR</h2><p>{_html_cite(report, report.get('tldr', ''))}</p>")
    if report.get("template") == "theme_rank":
        parts.append("<h2>Ranked companies</h2><table><tr><th>Rank</th><th>Ticker</th><th>Score</th><th>Coverage</th></tr>")
        for r in report.get("ranked_companies") or []:
            parts.append(
                f"<tr><td>{r.get('rank')}</td><td>{_esc(r.get('ticker'))}</td>"
                f"<td>{r.get('score')}</td><td>{r.get('coverage_pct')}</td></tr>"
            )
        parts.append("</table>")
        if report.get("winners_summary"):
            parts.append(f"<h3>Winners</h3><p>{_html_cite(report, report['winners_summary'])}</p>")
        if report.get("losers_summary"):
            parts.append(f"<h3>Laggards</h3><p>{_html_cite(report, report['losers_summary'])}</p>")
        if report.get("theme_narrative"):
            parts.append(f"<h2>Theme narrative</h2><p>{_html_cite(report, report['theme_narrative'])}</p>")
    elif report.get("template") == "ticker_outlook":
        snap = report.get("snapshot_summary") or {}
        parts.append("<h2>Snapshot</h2><ul>")
        for k, v in snap.items():
            parts.append(f"<li>{_esc(k)}: {_esc(v)}</li>")
        parts.append("</ul>")
        if report.get("outlook_narrative"):
            parts.append(f"<h2>Outlook</h2><p>{_html_cite(report, report['outlook_narrative'])}</p>")
    bundle = report.get("source_bundle") or []
    if bundle:
        parts.append("<h2>Sources checked</h2><ul>")
        for s in bundle:
            ref = _esc(s.get("ref", ""))
            title = _esc(s.get("title") or "—")
            url = s.get("url")
            if url:
                parts.append(f"<li><code>{ref}</code> {_esc(s.get('ticker', ''))} — <a href='{_esc(url)}'>{title}</a></li>")
            else:
                parts.append(f"<li><code>{ref}</code> {_esc(s.get('ticker', ''))} — {title} <em>(no URL on file)</em></li>")
        parts.append("</ul>")
    for section in ("catalysts", "risks", "caveats"):
        items = report.get(section)
        if items:
            parts.append(f"<h2>{section.title()}</h2><ul>")
            for it in items:
                parts.append(f"<li>{_html_cite(report, str(it))}</li>")
            parts.append("</ul>")
    parts.append("</article></body></html>")
    return "".join(parts)


def _write_static_site(db):
    """Build research-wiki/site/ for Python http.server (no Ruby/Jekyll)."""
    rows = (
        db.query(ResearchThread)
        .filter(ResearchThread.status == "published")
        .order_by(ResearchThread.published_at.desc())
        .all()
    )
    os.makedirs(SITE_DIR, exist_ok=True)
    links = []
    for t in rows:
        msg = _report_message(db, t.id)
        if not msg or not msg.structured_json:
            continue
        report = json.loads(msg.structured_json)
        slug = t.slug or f"{(t.published_at or t.created_at or '')[:10]}-{_slugify(t.title or t.intent)}"
        html_path = os.path.join(SITE_DIR, f"{slug}.html")
        with open(html_path, "w") as f:
            f.write(render_html(t, report))
        d = (t.published_at or t.created_at or "")[:10]
        links.append((d, t.title or slug, slug, t.intent))

    index_parts = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        "<title>Ampytech Research Library</title>",
        f"<style>{_HTML_STYLE}</style></head><body>",
        "<header>Ampytech Research Library</header>",
        "<article><h1>Published reports</h1>",
    ]
    if not links:
        index_parts.append(
            "<p>No published reports yet. Publish a report from the <strong>Research Analyst</strong> tab.</p>"
        )
    else:
        index_parts.append("<table><tr><th>Date</th><th>Title</th><th>Intent</th></tr>")
        for d, title, slug, intent in links:
            index_parts.append(
                f"<tr><td>{_esc(d)}</td><td><a href='{_esc(slug)}.html'>{_esc(title)}</a></td>"
                f"<td>{_esc(intent)}</td></tr>"
            )
        index_parts.append("</table>")
    index_parts.append("</article></body></html>")
    with open(os.path.join(SITE_DIR, "index.html"), "w") as f:
        f.write("".join(index_parts))


def export_thread(thread_id: str, db=None) -> Optional[str]:
    close = False
    if db is None:
        db = SessionLocal()
        close = True
    try:
        thread = db.query(ResearchThread).filter(ResearchThread.id == thread_id).first()
        if not thread or thread.status != "published":
            return None
        msg = _report_message(db, thread_id)
        if not msg or not msg.structured_json:
            return None
        report = json.loads(msg.structured_json)
        d = (thread.published_at or thread.created_at or date.today().isoformat())[:10]
        slug = thread.slug or f"{d}-{_slugify(thread.title or thread.intent)}"
        thread.slug = slug
        os.makedirs(REPORTS_DIR, exist_ok=True)
        path = os.path.join(REPORTS_DIR, f"{slug}.md")
        with open(path, "w") as f:
            f.write(render_markdown(thread, report))
        db.commit()
        _write_index(db)
        _write_static_site(db)
        return path
    finally:
        if close:
            db.close()


def remove_thread_export(slug_or_id: str):
    if os.path.isdir(REPORTS_DIR):
        for name in os.listdir(REPORTS_DIR):
            if slug_or_id in name:
                os.remove(os.path.join(REPORTS_DIR, name))
    if os.path.isdir(SITE_DIR):
        for name in os.listdir(SITE_DIR):
            if slug_or_id in name and name.endswith(".html"):
                os.remove(os.path.join(SITE_DIR, name))


def rebuild_all():
    db = SessionLocal()
    try:
        rows = db.query(ResearchThread).filter(ResearchThread.status == "published").all()
        written = 0
        for t in rows:
            if export_thread(t.id, db):
                written += 1
        _write_static_site(db)
        return {"written": written, "site_dir": SITE_DIR}
    finally:
        db.close()


def _write_index(db):
    rows = (
        db.query(ResearchThread)
        .filter(ResearchThread.status == "published")
        .order_by(ResearchThread.published_at.desc())
        .all()
    )
    lines = [
        "---",
        "title: Research Library",
        "layout: default",
        "---\n",
        "# Research Library\n",
        "| Date | Title | Intent |",
        "|------|-------|--------|",
    ]
    for t in rows:
        d = (t.published_at or t.created_at or "")[:10]
        slug = t.slug or t.id
        lines.append(f"| {d} | [{t.title}](reports/{slug}.html) | {t.intent} |")
    index_path = os.path.join(WIKI_ROOT, "index.md")
    os.makedirs(WIKI_ROOT, exist_ok=True)
    with open(index_path, "w") as f:
        f.write("\n".join(lines) + "\n")
