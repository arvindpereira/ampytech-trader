"""Aggregate feedback from rejected research reports."""
from __future__ import annotations

import json
from collections import Counter
from typing import Dict, List, Optional


def feedback_summary(db, limit: int = 100) -> Dict:
    from app.database import ResearchThread

    rows = (
        db.query(ResearchThread)
        .filter(ResearchThread.status == "rejected")
        .order_by(ResearchThread.rejected_at.desc())
        .limit(limit)
        .all()
    )
    by_intent: Counter = Counter()
    tag_counts: Counter = Counter()
    samples: List[Dict] = []
    for r in rows:
        by_intent[r.intent or "unknown"] += 1
        tags = []
        if r.feedback_tags:
            try:
                tags = json.loads(r.feedback_tags)
            except Exception:
                tags = []
        for t in tags:
            tag_counts[str(t)] += 1
        if r.feedback_notes:
            samples.append({
                "thread_id": r.id,
                "intent": r.intent,
                "notes": (r.feedback_notes or "")[:300],
                "tags": tags,
                "rejected_at": r.rejected_at,
            })
    return {
        "rejected_count": len(rows),
        "by_intent": dict(by_intent),
        "tag_counts": dict(tag_counts.most_common(20)),
        "recent_samples": samples[:15],
    }
