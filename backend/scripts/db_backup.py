"""Back up (and restore) the trading DB to Google Drive — so the DB can leave Git LFS safely.

Each backup is STAMPED WITH THE GIT COMMIT it was taken at (in the filename and in Drive
appProperties), so a restore can be matched to the code version it belongs to — the DB schema and the
code must agree. `--restore-commit` restores the newest backup taken on the commit you're checked out at.

Auth: OAuth "Desktop app" client. Put GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET in .env
(Google Cloud Console → Drive API enabled). First run opens a browser to consent; the token is cached
in data/gdrive_token.json and refreshed automatically. Files go into GOOGLE_DRIVE_FOLDER_ID.

Usage:
  python scripts/db_backup.py                 # upload a timestamped, commit-stamped backup
  python scripts/db_backup.py --keep 10        # ...and delete all but the 10 newest backups
  python scripts/db_backup.py --list           # list backups (with their commit) in the folder
  python scripts/db_backup.py --restore [NAME] # download a backup (default: newest) over the local DB
  python scripts/db_backup.py --restore-commit  # download the newest backup taken on the current commit
"""
import os
import sys
import time
import argparse
import subprocess
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import (
    DB_PATH, DATA_STORAGE_DIR, GOOGLE_DRIVE_FOLDER_ID,
    GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET,
)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]   # least privilege: only files the app creates
TOKEN_PATH = os.path.join(DATA_STORAGE_DIR, "gdrive_token.json")
PREFIX = "trading_system_"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _git_info():
    """(short_sha, branch, dirty) for the repo this script lives in."""
    def g(*args):
        try:
            return subprocess.check_output(["git", *args], cwd=REPO_ROOT, text=True,
                                           stderr=subprocess.DEVNULL).strip()
        except Exception:
            return ""
    sha = g("rev-parse", "--short", "HEAD") or "nogit"
    branch = g("rev-parse", "--abbrev-ref", "HEAD") or "?"
    dirty = bool(g("status", "--porcelain"))
    return sha, branch, dirty


def _service():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not (GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET):
                sys.exit("Missing GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET in .env. Create an "
                         "OAuth 'Desktop app' client (with the Drive API enabled) and add them, then retry.")
            cfg = {"installed": {
                "client_id": GOOGLE_OAUTH_CLIENT_ID, "client_secret": GOOGLE_OAUTH_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"]}}
            print("Opening a browser for Google consent (one time)…")
            creds = InstalledAppFlow.from_client_config(cfg, SCOPES).run_local_server(port=0)
        with open(TOKEN_PATH, "w") as f:
            f.write(creds.to_json())
        os.chmod(TOKEN_PATH, 0o600)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _exec(req, tries=4):
    """Execute a Drive request, retrying transient failures — including Google's edge-level HTML
    'Error 400 (Bad Request)!!1' / 5xx hiccups (which arrive as HTML, not a real JSON API error)."""
    from googleapiclient.errors import HttpError
    for i in range(tries):
        try:
            return req.execute()
        except HttpError as e:
            status = int(getattr(e.resp, "status", 0) or 0)
            content = e.content if isinstance(e.content, (bytes, bytearray)) else (e.content or b"")
            is_html = b"<html" in (content.lower() if isinstance(content, (bytes, bytearray)) else b"")
            transient = is_html or status in (429, 500, 502, 503, 504)
            if transient and i < tries - 1:
                wait = 2 ** i
                print(f"  Drive API transient error ({status or 'edge'}); retrying in {wait}s…")
                time.sleep(wait)
                continue
            raise


def _list(svc):
    q = f"'{GOOGLE_DRIVE_FOLDER_ID}' in parents and name contains '{PREFIX}' and trashed=false"
    res = _exec(svc.files().list(q=q, orderBy="createdTime desc",
                                 fields="files(id,name,size,createdTime,appProperties)", pageSize=200))
    return res.get("files", [])


def _commit_of(f):
    return (f.get("appProperties") or {}).get("commit", "?")


def backup(keep=None):
    if not os.path.exists(DB_PATH):
        sys.exit(f"DB not found at {DB_PATH}")
    from googleapiclient.http import MediaFileUpload
    svc = _service()
    sha, branch, dirty = _git_info()
    stamp = f"{datetime.now():%Y%m%d_%H%M%S}__{sha}{'-dirty' if dirty else ''}"
    name = f"{PREFIX}{stamp}.db"
    media = MediaFileUpload(DB_PATH, mimetype="application/x-sqlite3", resumable=True)
    meta = {"name": name, "parents": [GOOGLE_DRIVE_FOLDER_ID],
            "appProperties": {"commit": sha, "branch": branch, "dirty": str(dirty).lower(),
                              "taken_at": datetime.now().isoformat(timespec="seconds")}}
    print(f"Uploading {name} ({os.path.getsize(DB_PATH)/1e6:.0f} MB) @ commit {sha}"
          f"{' (dirty tree!)' if dirty else ''} → folder {GOOGLE_DRIVE_FOLDER_ID}…")
    if dirty:
        print("  ⚠ working tree has uncommitted changes — this backup's 'commit' stamp is approximate.")
    f = _exec(svc.files().create(body=meta, media_body=media, fields="id,name"))
    print(f"✓ Backed up: {f['name']} (id {f['id']})")
    if keep:
        # The backup is already safely uploaded — never let prune-of-old-backups turn a successful
        # backup into a failure (e.g. a transient Drive 400 on the list/delete calls).
        try:
            for old in _list(svc)[keep:]:
                _exec(svc.files().delete(fileId=old["id"]))
                print(f"  pruned old backup {old['name']}")
        except Exception as e:
            print(f"  ⚠ backup OK, but pruning old backups failed (will retry next run): {str(e)[:140]}")


def list_backups():
    cur, _, _ = _git_info()
    for f in _list(_service()):
        sz = int(f.get("size", 0)) / 1e6
        c = _commit_of(f)
        here = "  ← current commit" if c == cur else ""
        print(f"  {f['name']:48} {sz:6.0f} MB  {f['createdTime'][:19]}  commit={c}{here}")


def restore(name=None, match_commit=False):
    import io
    from googleapiclient.http import MediaIoBaseDownload
    svc = _service()
    files = _list(svc)
    if not files:
        sys.exit("No backups found in the Drive folder.")
    cur, _, _ = _git_info()

    if match_commit:
        matches = [f for f in files if _commit_of(f) == cur]
        if not matches:
            avail = ", ".join(sorted({_commit_of(f) for f in files}))
            sys.exit(f"No backup found for the current commit {cur}. Available commits: {avail}. "
                     f"Use --list, or checkout the matching commit, or restore by name.")
        target = matches[0]
    elif name:
        target = next((x for x in files if x["name"] == name), None)
        if not target:
            sys.exit(f"Backup '{name}' not found. Use --list to see available backups.")
    else:
        target = files[0]

    tcommit = _commit_of(target)
    if tcommit != cur and not match_commit:
        print(f"⚠ This backup was taken at commit {tcommit}, but you're on {cur}. "
              f"Schema/data may not match the code — consider `git checkout {tcommit}` or --restore-commit.")
    if os.path.exists(DB_PATH):
        bak = DB_PATH + ".pre-restore"
        os.replace(DB_PATH, bak)
        print(f"Moved existing DB aside → {bak}")
    print(f"Downloading {target['name']} (commit {tcommit}) → {DB_PATH}…")
    req = svc.files().get_media(fileId=target["id"])
    with open(DB_PATH, "wb") as fh:
        dl = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _, done = dl.next_chunk()
    print(f"✓ Restored {target['name']} to {DB_PATH}")


def verify(name=None, match_commit=False):
    """Download a backup to a temp file and validate it (SQLite integrity + row counts) WITHOUT touching
    the live DB. Proves the backup is restorable; exercises the same download path as --restore."""
    import sqlite3
    from googleapiclient.http import MediaIoBaseDownload
    svc = _service()
    files = _list(svc)
    if not files:
        sys.exit("No backups found in the Drive folder.")
    cur, _, _ = _git_info()
    if match_commit:
        target = next((f for f in files if _commit_of(f) == cur), None)
        if not target:
            sys.exit(f"No backup found for the current commit {cur}.")
    elif name:
        target = next((x for x in files if x["name"] == name), None)
        if not target:
            sys.exit(f"Backup '{name}' not found. Use --list to see available backups.")
    else:
        target = files[0]

    tmp = DB_PATH + ".verify"
    print(f"Downloading {target['name']} (commit {_commit_of(target)}) → {tmp} for verification…")
    req = svc.files().get_media(fileId=target["id"])
    with open(tmp, "wb") as fh:
        dl = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _, done = dl.next_chunk()

    tables = ["news_llm_scores", "daily_prices", "recent_prices", "universe_tickers", "virtual_orders"]
    try:
        c = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
        integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
        b_counts = {t: _safe_count(c, t) for t in tables}
        c.close()
        live = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        l_counts = {t: _safe_count(live, t) for t in tables}
        live.close()
        size = os.path.getsize(tmp) / 1e6
        print(f"\nBackup file: {size:.0f} MB | PRAGMA integrity_check: {integrity}")
        print(f"{'table':<20}{'backup':>12}{'live':>12}")
        for t in tables:
            flag = "" if b_counts[t] == l_counts[t] else "  ⚠ differs"
            print(f"{t:<20}{str(b_counts[t]):>12}{str(l_counts[t]):>12}{flag}")
        ok = integrity == "ok" and all(b_counts[t] is not None for t in tables)
        print("\n✓ Backup is valid and restorable." if ok else "\n⚠ Verification found issues — inspect before relying on it.")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _safe_count(conn, table):
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except Exception:
        return None


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Back up / restore the trading DB to Google Drive (commit-stamped)")
    p.add_argument("--keep", type=int, default=None, help="after upload, keep only the N newest backups")
    p.add_argument("--list", action="store_true", help="list backups (with commit) in the Drive folder")
    p.add_argument("--restore", nargs="?", const="__latest__", help="restore a backup (default: newest)")
    p.add_argument("--restore-commit", action="store_true", help="restore the newest backup matching the current git commit")
    p.add_argument("--verify", nargs="?", const="__latest__", help="download+validate a backup without touching the live DB (default: newest)")
    p.add_argument("--verify-commit", action="store_true", help="verify the newest backup matching the current git commit")
    a = p.parse_args()
    if a.list:
        list_backups()
    elif a.verify_commit:
        verify(match_commit=True)
    elif a.verify is not None:
        verify(None if a.verify == "__latest__" else a.verify)
    elif a.restore_commit:
        restore(match_commit=True)
    elif a.restore is not None:
        restore(None if a.restore == "__latest__" else a.restore)
    else:
        backup(keep=a.keep)
