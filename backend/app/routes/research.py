"""Research Analyst API routes."""
import json
import os
import threading
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/research", tags=["research"])

_RESEARCH_RESULTS = {}
_RESEARCH_QUERY_META = {}
_job_new = None
_job_update = None


def register_jobs(job_new, job_update):
    global _job_new, _job_update
    _job_new = job_new
    _job_update = job_update


class ResearchQueryRequest(BaseModel):
    query: str
    deep_research: bool = False
    use_premium: bool = False
    use_web_search: bool = False
    extra_tickers: Optional[List[str]] = None
    thread_id: Optional[str] = None


class ResearchMessageRequest(BaseModel):
    message: str


class ResearchRejectRequest(BaseModel):
    feedback_notes: str
    feedback_tags: Optional[List[str]] = None


class WatchlistRequest(BaseModel):
    tickers: List[str]
    action: str = "add"  # add | remove


def _now():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _run_query_job(jid, req_data):
    from app.database import ResearchMessage, ResearchThread, SessionLocal
    from data_ingestion.research_kb_refresh import materialize_ticker
    from ml_engine.intent_router import route
    from ml_engine.context_expander import resolve_query_tickers
    from ml_engine.research_analyst import build_report, prepare_context
    from ml_engine.citation_resolver import attach_citations
    from ml_engine.research_llm_router import decide, stamp_generation, upgrade_offer

    db = SessionLocal()
    try:
        use_premium = bool(req_data.get("use_premium") or req_data.get("deep_research"))
        use_web_search = bool(req_data.get("use_web_search") or req_data.get("deep_research"))
        _job_update(jid, progress=5, stage="Routing query")
        routed = route(
            req_data.get("query", ""),
            deep_research=use_web_search or req_data.get("deep_research", False),
            extra_tickers=req_data.get("extra_tickers"),
        )
        _job_update(jid, progress=10, stage="Resolving tickers & portfolio context")
        tickers, expansion = resolve_query_tickers(
            routed, db, extra_tickers=req_data.get("extra_tickers")
        )
        if expansion.get("error") == "no_primary_ticker":
            _job_update(jid, status="error", error="No ticker detected — mention a symbol (e.g. MU or Micron).")
            return
        if expansion.get("error") == "no_holdings":
            _job_update(jid, status="error", error="No portfolio holdings found for crowding analysis.")
            return
        if expansion.get("error") == "no_ticker":
            _job_update(jid, status="error", error=expansion.get("message", "No ticker detected for earnings analysis."))
            return
        if not tickers and routed.intent == "ticker_outlook":
            _job_update(jid, status="error", error="No ticker detected in query.")
            return
        if not tickers and routed.intent == "theme_rank":
            _job_update(jid, status="error", error="No tickers resolved for theme.")
            return
        if not tickers and routed.intent != "sector_screen":
            _job_update(jid, status="error", error="No tickers resolved for this query.")
            return

        if expansion.get("sectors"):
            sec_label = ", ".join(expansion["sectors"][:3])
            _job_update(jid, progress=12, stage=f"Screening sectors: {sec_label}")
        elif expansion.get("sector_peers"):
            peers = ", ".join(expansion["sector_peers"][:6])
            _job_update(jid, progress=12, stage=f"Including portfolio peers: {peers}")

        _job_update(jid, progress=15, stage="Refreshing knowledge base")
        from data_ingestion.earnings_content_fetcher import refresh_ticker as refresh_earnings_data

        for t in tickers:
            try:
                if routed.intent == "earnings_report":
                    refresh_earnings_data(db, t)
                materialize_ticker(db, t)
            except Exception:
                pass

        _job_update(jid, progress=35, stage="Loading snapshots, news & retrieval")
        facts_by_ticker, items_by_ticker, coverage, news_by_ticker = prepare_context(
            tickers, db, query=routed.raw_query
        )
        route_decision = decide(routed, coverage, use_premium=use_premium)

        web_items = []
        low_coverage = bool(coverage) and min(coverage.values()) < 0.5
        if route_decision.use_search or use_web_search or low_coverage:
            _job_update(jid, progress=38, stage="Fetching web search snippets")
            try:
                from data_ingestion.web_search_fetcher import fetch_for_research
                web_items = fetch_for_research(routed.raw_query, tickers, db=db)
                if web_items:
                    facts_by_ticker, items_by_ticker, coverage, news_by_ticker = prepare_context(
                        tickers, db, query=routed.raw_query, web_items=web_items
                    )
            except Exception:
                web_items = []
        tier_label = {"standard": "GPT-4o mini", "premium": "Premium AI", "local": "local AI"}.get(
            route_decision.tier, route_decision.tier
        )
        _job_update(
            jid,
            progress=45,
            stage=f"Selected {tier_label} — {route_decision.reason}",
        )

        def report_progress(pct: int, stage: str) -> None:
            _job_update(jid, progress=45 + int(pct * 0.47), stage=stage)

        report = build_report(
            routed,
            facts_by_ticker,
            items_by_ticker,
            route_decision,
            progress_cb=report_progress,
            news_by_ticker=news_by_ticker,
            expansion=expansion,
            web_items=web_items,
        )
        report = stamp_generation(report, route_decision)
        upgrade = upgrade_offer(route_decision, routed.intent, len(tickers))
        report = attach_citations(report, items_by_ticker, facts_by_ticker, db=db)
        report["upgrade_offer"] = upgrade
        gen = report.get("generation") or {}

        thread_id = req_data.get("thread_id") or uuid.uuid4().hex
        title = (routed.raw_query or "Research")[:120]
        avg_cov = sum(coverage.values()) / len(coverage) if coverage else 0.0
        tldr = report.get("tldr") or title

        thread = db.query(ResearchThread).filter(ResearchThread.id == thread_id).first()
        if not thread:
            thread = ResearchThread(
                id=thread_id,
                title=title,
                intent=routed.intent,
                status="draft",
                summary=tldr[:300],
                tickers_json=json.dumps(tickers),
                theme=routed.theme,
                coverage_pct=avg_cov,
                llm_tier=route_decision.tier,
                created_at=_now(),
                updated_at=_now(),
            )
            db.add(thread)
        else:
            thread.updated_at = _now()
            thread.llm_tier = route_decision.tier

        db.add(ResearchMessage(
            thread_id=thread_id,
            role="user",
            content=routed.raw_query,
            created_at=_now(),
        ))
        db.add(ResearchMessage(
            thread_id=thread_id,
            role="assistant",
            content=tldr,
            structured_json=json.dumps(report),
            snapshot_tickers_json=json.dumps(tickers),
            model=gen.get("model") or route_decision.tier,
            created_at=_now(),
        ))
        _job_update(jid, progress=95, stage="Saving draft")
        db.commit()

        result = {
            "thread_id": thread_id,
            "report": report,
            "tier": route_decision.tier,
            "generation": gen,
            "generation_note": report.get("generation_note"),
            "upgrade_offer": upgrade,
            "complexity": route_decision.complexity,
            "reason": route_decision.reason,
            "tickers": tickers,
            "coverage": coverage,
            "intent": routed.intent,
            "expansion": expansion,
        }
        _RESEARCH_RESULTS[jid] = result
        _RESEARCH_QUERY_META[thread_id] = {
            "tickers": tickers,
            "facts_by_ticker": facts_by_ticker,
            "items_by_ticker": {t: [it.id for it in items_by_ticker.get(t, [])] for t in tickers},
        }
        _job_update(jid, progress=100, status="done", stage="Complete")
    except Exception as e:
        _job_update(jid, status="error", error=str(e)[:300])
    finally:
        db.close()


@router.get("/snapshot/{ticker}")
def get_snapshot(ticker: str):
    from app.database import SessionLocal
    from ml_engine import research_dossier

    db = SessionLocal()
    try:
        row = research_dossier.get_snapshot(db, ticker)
        if not row:
            raise HTTPException(status_code=404, detail="No snapshot for ticker — run research-kb-refresh")
        return {
            "ticker": row.ticker,
            "as_of_date": row.as_of_date,
            "coverage_pct": row.coverage_pct,
            "refreshed_at": row.refreshed_at,
            "facts": research_dossier.snapshot_row_to_dict(row),
        }
    finally:
        db.close()


@router.get("/kb/status")
def kb_status():
    from sqlalchemy import func
    from app.database import CompanySnapshot, SessionLocal

    db = SessionLocal()
    try:
        latest = db.query(CompanySnapshot).order_by(CompanySnapshot.refreshed_at.desc()).first()
        count = db.query(func.count(func.distinct(CompanySnapshot.ticker))).scalar() or 0
        return {
            "ticker_count": count,
            "last_refreshed": latest.refreshed_at if latest else None,
            "latest_as_of": latest.as_of_date if latest else None,
        }
    finally:
        db.close()


@router.post("/kb/refresh")
def kb_refresh():
    if not _job_new:
        raise HTTPException(status_code=500, detail="Research jobs not registered")
    jid = _job_new("research_kb", "Refreshing research knowledge base")

    def _run(jid):
        try:
            from data_ingestion.research_kb_refresh import run_refresh
            _job_update(jid, progress=20, stage="Refreshing analyst items + snapshots")
            res = run_refresh()
            _job_update(jid, progress=100, status="done", stage=f"Done — {res.get('tickers')} tickers")
            _RESEARCH_RESULTS[jid] = res
        except Exception as e:
            _job_update(jid, status="error", error=str(e)[:200])

    threading.Thread(target=_run, args=(jid,), daemon=True).start()
    return {"status": "started", "job_id": jid}


@router.get("/feedback/summary")
def feedback_summary_route():
    from app.database import SessionLocal
    from ml_engine.feedback_analytics import feedback_summary

    db = SessionLocal()
    try:
        return feedback_summary(db)
    finally:
        db.close()


@router.get("/methodology")
def methodology():
    from ml_engine.research_framework import (
        GICS_SECTOR_ETF,
        METHODOLOGY_VERSION,
        STOCK_FACTOR_WEIGHTS,
        calibration_metadata,
        get_stock_factor_weights,
    )
    cal = calibration_metadata()
    return {
        "version": METHODOLOGY_VERSION,
        "doc": "docs/research-methodology.md",
        "stock_factor_weights": get_stock_factor_weights(),
        "default_factor_weights": STOCK_FACTOR_WEIGHTS,
        "calibration": cal,
        "sector_etfs": GICS_SECTOR_ETF,
    }


@router.get("/premium/estimate")
def premium_estimate(query: str, extra_tickers: Optional[str] = None):
    """Estimated cost for a premium-model re-synthesis of this query."""
    from app.database import SessionLocal
    from ml_engine.intent_router import route
    from ml_engine.context_expander import resolve_query_tickers
    from ml_engine.research_llm_router import estimate_cost_for_tier

    db = SessionLocal()
    try:
        extras = [t.strip().upper() for t in (extra_tickers or "").split(",") if t.strip()]
        routed = route(query, deep_research=False, extra_tickers=extras or None)
        tickers, _ = resolve_query_tickers(routed, db, extra_tickers=extras or None)
        n = max(len(tickers), 1)
        est = estimate_cost_for_tier("premium", routed.intent, n)
        return {"query": query, "intent": routed.intent, "ticker_count": n, **est}
    finally:
        db.close()


@router.post("/query")
def start_query(req: ResearchQueryRequest):
    if not _job_new:
        raise HTTPException(status_code=500, detail="Research jobs not registered")
    jid = _job_new("research_query", "Research analysis")
    threading.Thread(target=_run_query_job, args=(jid, req.dict()), daemon=True).start()
    return {"status": "started", "job_id": jid}


@router.get("/query/result")
def query_result(job_id: str):
    if job_id in _RESEARCH_RESULTS:
        return {"status": "done", "result": _RESEARCH_RESULTS[job_id]}
    from app.main import _jobs_snapshot
    j = [x for x in _jobs_snapshot() if x["id"] == job_id]
    if j:
        return {"status": j[0]["status"], "progress": j[0]["progress"], "stage": j[0]["stage"], "error": j[0].get("error")}
    return {"status": "unknown"}


@router.get("/themes")
def get_themes():
    from ml_engine.theme_resolver import list_themes
    return {"themes": list_themes()}


@router.get("/sectors")
def get_sectors():
    """GICS sector handbook — ETF proxies, cap-ranked seeds, portfolio classification."""
    from ml_engine.sector_resolver import list_sectors as list_sector_catalog, load_catalog, portfolio_by_sector
    from ml_engine.research_framework import GICS_SECTOR_ETF

    cat = load_catalog()
    return {
        "sectors": list_sector_catalog(),
        "sector_etfs": GICS_SECTOR_ETF,
        "source": "backend/data/research_sectors.json",
        "last_refreshed_at": cat.get("last_refreshed_at"),
        "portfolio_by_sector": portfolio_by_sector(),
    }


@router.get("/portfolio/sectors")
def portfolio_sectors():
    """Portfolio tickers classified by GICS sector (internal + external holdings)."""
    from ml_engine.portfolio_holdings import all_holdings, portfolio_tickers
    from ml_engine.sector_resolver import load_catalog, portfolio_classification
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        return {
            "tickers": portfolio_tickers(db),
            "holdings": all_holdings(db),
            "classification": portfolio_classification(),
            "by_sector": load_catalog().get("portfolio_by_sector") or {},
            "last_refreshed_at": load_catalog().get("last_refreshed_at"),
            "refresh": "make research-sectors-refresh",
        }
    finally:
        db.close()


@router.get("/threads")
def list_threads(status: Optional[str] = None, limit: int = 50):
    from app.database import ResearchThread, SessionLocal

    db = SessionLocal()
    try:
        q = db.query(ResearchThread).order_by(ResearchThread.updated_at.desc())
        if status:
            q = q.filter(ResearchThread.status == status)
        rows = q.limit(min(limit, 100)).all()
        return {"threads": [_thread_dict(r) for r in rows]}
    finally:
        db.close()


@router.get("/thread/{thread_id}")
def get_thread(thread_id: str):
    from app.database import ResearchMessage, ResearchThread, SessionLocal

    db = SessionLocal()
    try:
        t = db.query(ResearchThread).filter(ResearchThread.id == thread_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Thread not found")
        msgs = (
            db.query(ResearchMessage)
            .filter(ResearchMessage.thread_id == thread_id)
            .order_by(ResearchMessage.id.asc())
            .all()
        )
        return {
            "thread": _thread_dict(t),
            "messages": [_msg_dict(m) for m in msgs],
        }
    finally:
        db.close()


@router.post("/thread/{thread_id}/publish")
def publish_thread(thread_id: str):
    from app.database import ResearchThread, SessionLocal
    from ml_engine.research_wiki_export import export_thread

    db = SessionLocal()
    try:
        t = db.query(ResearchThread).filter(ResearchThread.id == thread_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Thread not found")
        t.status = "published"
        t.published_at = _now()
        t.updated_at = _now()
        db.commit()
        try:
            path = export_thread(thread_id, db)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Wiki export failed: {str(e)[:300]}")
        t.wiki_exported_at = _now()
        db.commit()
        return {"status": "published", "wiki_path": str(path) if path else None}
    finally:
        db.close()


@router.post("/thread/{thread_id}/reject")
def reject_thread(thread_id: str, req: ResearchRejectRequest):
    from app.database import ResearchThread, SessionLocal
    from ml_engine.research_wiki_export import remove_thread_export

    if not req.feedback_notes.strip():
        raise HTTPException(status_code=400, detail="feedback_notes required")
    db = SessionLocal()
    try:
        t = db.query(ResearchThread).filter(ResearchThread.id == thread_id).first()
        if not t:
            raise HTTPException(status_code=404, detail="Thread not found")
        t.status = "rejected"
        t.feedback_notes = req.feedback_notes.strip()
        t.feedback_tags = json.dumps(req.feedback_tags or [])
        t.rejected_at = _now()
        t.updated_at = _now()
        db.commit()
        remove_thread_export(t.slug or thread_id)
        return {"status": "rejected"}
    finally:
        db.close()


@router.get("/library")
def library(intent: Optional[str] = None, status: str = "published"):
    from app.database import ResearchThread, SessionLocal

    db = SessionLocal()
    try:
        q = db.query(ResearchThread).filter(ResearchThread.status == status)
        if intent:
            q = q.filter(ResearchThread.intent == intent)
        rows = q.order_by(ResearchThread.published_at.desc()).limit(100).all()
        return {"reports": [_thread_dict(r) for r in rows]}
    finally:
        db.close()


@router.get("/analyst-items/{ticker}")
def analyst_items(ticker: str, limit: int = 20):
    from app.database import SessionLocal
    from data_ingestion.analyst_content_fetcher import recent_items

    db = SessionLocal()
    try:
        rows = recent_items(db, ticker, limit=limit)
        return {"items": [_item_dict(r) for r in rows]}
    finally:
        db.close()


@router.post("/watchlist")
def watchlist(req: WatchlistRequest):
    from app.database import ResearchWatchlist, SessionLocal

    db = SessionLocal()
    try:
        for t in req.tickers:
            tk = t.upper().strip()
            if not tk:
                continue
            if req.action == "remove":
                db.query(ResearchWatchlist).filter(ResearchWatchlist.ticker == tk).delete()
            else:
                if not db.query(ResearchWatchlist).filter(ResearchWatchlist.ticker == tk).first():
                    db.add(ResearchWatchlist(ticker=tk, added_at=_now()))
        db.commit()
        rows = db.query(ResearchWatchlist).all()
        return {"watchlist": [r.ticker for r in rows]}
    finally:
        db.close()


@router.post("/wiki/rebuild")
def wiki_rebuild():
    from ml_engine.research_wiki_export import rebuild_all
    return rebuild_all()


def _thread_dict(t):
    return {
        "id": t.id,
        "title": t.title,
        "intent": t.intent,
        "status": t.status,
        "slug": t.slug,
        "summary": t.summary,
        "tickers": json.loads(t.tickers_json) if t.tickers_json else [],
        "theme": t.theme,
        "coverage_pct": t.coverage_pct,
        "llm_tier": t.llm_tier,
        "feedback_notes": t.feedback_notes,
        "published_at": t.published_at,
        "rejected_at": t.rejected_at,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


def _msg_dict(m):
    structured = None
    if m.structured_json:
        try:
            structured = json.loads(m.structured_json)
        except Exception:
            structured = None
    return {
        "id": m.id,
        "role": m.role,
        "content": m.content,
        "structured": structured,
        "model": m.model,
        "created_at": m.created_at,
    }


def _item_dict(it):
    return {
        "id": it.id,
        "ticker": it.ticker,
        "source": it.source,
        "title": it.title,
        "excerpt": it.excerpt,
        "published_at": it.published_at,
        "analyst_firm": it.analyst_firm,
        "rating": it.rating,
        "target_price": it.target_price,
        "source_url": it.source_url,
    }
