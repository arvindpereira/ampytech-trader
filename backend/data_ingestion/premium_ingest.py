"""Ingest premium newsletter articles (e.g. The Information) you receive by email, via IMAP.

Pulls messages from a sender (PREMIUM_SENDER) out of your inbox over IMAP, strips the HTML to text, and
hands each to premium_llm.ingest_article() which LLM-extracts per-ticker scores into news_llm_scores.
Only content you legitimately receive (subscriber emails) is read; only derived scores are stored.

Dedup: processed Message-IDs are remembered in data/premium_ingest_state.json so re-runs don't re-score.

Usage:
  python data_ingestion/premium_ingest.py                 # pull last 7 days of PREMIUM_SENDER emails
  python data_ingestion/premium_ingest.py --days 30
  python data_ingestion/premium_ingest.py --dry-run       # list matching emails, don't score
  python data_ingestion/premium_ingest.py --file path.eml # ingest one local .eml/.html/.txt/.md (testing/manual)
"""
import sys
import os
import re
import json
import email
import imaplib
import argparse
from email.utils import parsedate_to_datetime
from email.header import decode_header, make_header
from datetime import datetime, timedelta

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import (
    IMAP_HOST, IMAP_PORT, IMAP_USER, IMAP_PASSWORD, IMAP_FOLDER,
    PREMIUM_SENDER, PREMIUM_SOURCE_TAG, PREMIUM_SKIP_SUBJECTS,
)
from data_ingestion.premium_llm import ingest_article

_SKIP = [p.strip().lower() for p in (PREMIUM_SKIP_SUBJECTS or "").split(",") if p.strip()]


def _is_skipped(subject):
    s = (subject or "").lower()
    return any(p in s for p in _SKIP)

_STATE_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "data", "premium_ingest_state.json")


def _decode(s):
    """Decode a MIME-encoded header (=?UTF-8?...?=) into a readable string."""
    if not s:
        return ""
    try:
        return str(make_header(decode_header(s))).strip()
    except Exception:
        return s


def _html_to_text(html):
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "head"]):
            tag.decompose()
        text = soup.get_text(separator=" ")
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _email_body(msg):
    """Best-effort plain text from an email.message.Message (prefer HTML part, stripped)."""
    html, plain = None, None
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get("Content-Disposition", "").startswith("attachment"):
                continue
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                charset = part.get_content_charset() or "utf-8"
                content = payload.decode(charset, errors="replace")
            except Exception:
                continue
            if ctype == "text/html" and html is None:
                html = content
            elif ctype == "text/plain" and plain is None:
                plain = content
    else:
        payload = msg.get_payload(decode=True)
        content = payload.decode(msg.get_content_charset() or "utf-8", errors="replace") if payload else ""
        if msg.get_content_type() == "text/html":
            html = content
        else:
            plain = content
    if html:
        return _html_to_text(html)
    return re.sub(r"\s+", " ", (plain or "")).strip()


def _load_state():
    try:
        return set(json.load(open(_STATE_FILE)).get("seen", []))
    except Exception:
        return set()


def _save_state(seen):
    os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
    json.dump({"seen": list(seen)[-5000:]}, open(_STATE_FILE, "w"))


def _msg_date(msg):
    try:
        return parsedate_to_datetime(msg.get("Date")).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def ingest_file(path):
    """Ingest a single local file (.eml / .html / .txt / .md) — for testing and manual saves."""
    raw = open(path, "rb").read()
    if path.lower().endswith(".eml"):
        msg = email.message_from_bytes(raw)
        title, body, date = _decode(msg.get("Subject", os.path.basename(path))), _email_body(msg), _msg_date(msg)
    else:
        text = raw.decode("utf-8", errors="replace")
        body = _html_to_text(text) if path.lower().endswith((".html", ".htm")) else text
        title = os.path.splitext(os.path.basename(path))[0].replace("_", " ")
        date = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d")
    print(f"📄 {title} ({date})")
    accepted = ingest_article(title, body, date, f"file:{os.path.basename(path)}", PREMIUM_SOURCE_TAG)
    for m in accepted:
        print(f"   • {m['ticker']}: s={m['s']:+.2f} rel={m['rel']:.2f}  {m.get('why','')}")
    print(f"✅ {len(accepted)} ticker score(s) stored." if accepted else "   (no tradeable tickers matched)")
    return accepted


def ingest_imap(days=7, dry_run=False):
    if not (IMAP_USER and IMAP_PASSWORD):
        print("IMAP_USER / IMAP_PASSWORD not set in backend/.env — cannot pull email.")
        return
    print(f"📬 Connecting to {IMAP_HOST}:{IMAP_PORT} as {IMAP_USER} (folder {IMAP_FOLDER})…")
    M = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    M.login(IMAP_USER, IMAP_PASSWORD)
    M.select(IMAP_FOLDER)
    since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
    typ, data = M.search(None, "FROM", f'"{PREMIUM_SENDER}"', "SINCE", since)
    ids = data[0].split() if data and data[0] else []
    print(f"🔎 {len(ids)} message(s) from {PREMIUM_SENDER} since {since}.")

    seen = _load_state()
    new_count, total_scores, skipped = 0, 0, 0
    for num in ids:
        typ, mdata = M.fetch(num, "(RFC822)")
        if not mdata or not mdata[0]:
            continue
        msg = email.message_from_bytes(mdata[0][1])
        mid = msg.get("Message-ID") or f"{msg.get('Date','')}|{msg.get('Subject','')}"
        if mid in seen:
            continue
        subject, date = _decode(msg.get("Subject", "(no subject)")), _msg_date(msg)
        if _is_skipped(subject):
            skipped += 1
            if dry_run:
                print(f"   ⊘ {date}  {subject[:80]}   (skipped: digest)")
            continue
        if dry_run:
            print(f"   • {date}  {subject[:90]}")
            continue
        body = _email_body(msg)
        print(f"📄 {date}  {subject[:90]}")
        accepted = ingest_article(subject, body, date, f"imap:{mid}", PREMIUM_SOURCE_TAG)
        for m in accepted:
            print(f"      • {m['ticker']}: s={m['s']:+.2f} rel={m['rel']:.2f}  {m.get('why','')}")
        seen.add(mid)
        new_count += 1
        total_scores += len(accepted)
    if not dry_run:
        _save_state(seen)
        print(f"✅ Ingested {new_count} new email(s) → {total_scores} ticker score(s) into "
              f"news_llm_scores ({skipped} digest email(s) skipped).")
    else:
        print(f"   ({skipped} digest email(s) would be skipped.)")
    M.logout()


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Ingest premium newsletter articles via IMAP into news_llm_scores")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--file", default=None, help="ingest one local .eml/.html/.txt/.md instead of IMAP")
    a = p.parse_args()
    if a.file:
        ingest_file(a.file)
    else:
        ingest_imap(days=a.days, dry_run=a.dry_run)
