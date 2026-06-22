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
    extra_tickers: Optional[List[str]] = None


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
    from data_ingestion.research_kb_refresh import materialize_ticker, refresh_tickers
    from ml_engine.intent_router import route
    from ml_engine.research_analyst import build_report, prepare_context
    from ml_engine.research_llm_router import decide
    from ml_engine.theme_resolver import resolve

    db = SessionLocal()
    try:
        _job_update(jid, progress=5, stage="Routing query")
        routed = route(
            req_data.get("query", ""),
            deep_research=req_data.get("deep_research", False),
            extra_tickers=req_data.get("extra_tickers"),
        )
        _job_update(jid, progress=10, stage="Resolving tickers & theme")
        if routed.intent == "theme_rank":
            tickers = resolve(routed.theme, routed.tickers)
        else:
            tickers = routed.tickers or []
        if not tickers and routed.intent == "ticker_outlook":
            _job_update(jid, status="error", error="No ticker detected in query.")
            return

        _job_update(jid, progress=15, stage="Refreshing knowledge base")
        for t in tickers:
            try:
                materialize_ticker(db, t)
            except Exception:
                pass

        _job_update(jid, progress=35, stage="Loading snapshots & analyst items")
        facts_by_ticker, items_by_ticker, coverage = prepare_context(tickers, db)
        route_decision = decide(routed, coverage)
        _job_update(
            jid,
            progress=45,
            stage=f"Selected {route_decision.tier} tier — {route_decision.reason}",
        )

        def report_progress(pct: int, stage: str) -> None:
            _job_update(jid, progress=45 + int(pct * 0.47), stage=stage)

        report = build_report(
            routed,
            facts_by_ticker,
            items_by_ticker,
            route_decision,
            progress_cb=report_progress,
        )

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
            model=route_decision.tier,
            created_at=_now(),
        ))
        _job_update(jid, progress=95, stage="Saving draft")
        db.commit()

        result = {
            "thread_id": thread_id,
            "report": report,
            "tier": route_decision.tier,
            "complexity": route_decision.complexity,
            "reason": route_decision.reason,
            "tickers": tickers,
            "coverage": coverage,
            "intent": routed.intent,
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
        path = export_thread(thread_id, db)
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
