"""Back up (and restore) the trading DB to Google Drive — so the DB can leave Git LFS safely.

Auth: OAuth "Desktop app" client. Put GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET in .env
(Google Cloud Console → APIs & Services → Credentials, with the Drive API enabled). The first run
opens a browser to consent; the token is cached in data/gdrive_token.json and refreshed automatically
after that. Files go into GOOGLE_DRIVE_FOLDER_ID.

Usage:
  python scripts/db_backup.py                 # upload a timestamped backup of the DB
  python scripts/db_backup.py --keep 5         # ...and delete all but the 5 newest backups
  python scripts/db_backup.py --list           # list backups in the Drive folder
  python scripts/db_backup.py --restore [NAME] # download a backup (default: newest) over the local DB
"""
import os
import sys
import argparse
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import (
    DB_PATH, DATA_STORAGE_DIR, GOOGLE_DRIVE_FOLDER_ID,
    GOOGLE_OAUTH_CLIENT_ID, GOOGLE_OAUTH_CLIENT_SECRET,
)

# drive.file = least privilege: the app can only see/manage files it creates (our backups).
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
TOKEN_PATH = os.path.join(DATA_STORAGE_DIR, "gdrive_token.json")
PREFIX = "trading_system_"


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


def _list(svc):
    q = f"'{GOOGLE_DRIVE_FOLDER_ID}' in parents and name contains '{PREFIX}' and trashed=false"
    res = svc.files().list(q=q, orderBy="createdTime desc",
                           fields="files(id,name,size,createdTime)", pageSize=100).execute()
    return res.get("files", [])


def backup(keep=None):
    if not os.path.exists(DB_PATH):
        sys.exit(f"DB not found at {DB_PATH}")
    from googleapiclient.http import MediaFileUpload
    svc = _service()
    name = f"{PREFIX}{datetime.now():%Y%m%d_%H%M%S}.db"
    media = MediaFileUpload(DB_PATH, mimetype="application/x-sqlite3", resumable=True)
    print(f"Uploading {name} ({os.path.getsize(DB_PATH)/1e6:.0f} MB) to Drive folder {GOOGLE_DRIVE_FOLDER_ID}…")
    f = svc.files().create(body={"name": name, "parents": [GOOGLE_DRIVE_FOLDER_ID]},
                           media_body=media, fields="id,name,size").execute()
    print(f"✓ Backed up: {f['name']} (id {f['id']})")
    if keep:
        files = _list(svc)
        for old in files[keep:]:
            svc.files().delete(fileId=old["id"]).execute()
            print(f"  pruned old backup {old['name']}")


def list_backups():
    for f in _list(_service()):
        sz = int(f.get("size", 0)) / 1e6
        print(f"  {f['name']}  {sz:6.0f} MB  {f['createdTime'][:19]}  id={f['id']}")


def restore(name=None):
    import io
    from googleapiclient.http import MediaIoBaseDownload
    svc = _service()
    files = _list(svc)
    if not files:
        sys.exit("No backups found in the Drive folder.")
    target = files[0] if not name else next((x for x in files if x["name"] == name), None)
    if not target:
        sys.exit(f"Backup '{name}' not found. Use --list to see available backups.")
    if os.path.exists(DB_PATH):
        bak = DB_PATH + ".pre-restore"
        os.replace(DB_PATH, bak)
        print(f"Moved existing DB aside → {bak}")
    print(f"Downloading {target['name']} → {DB_PATH}…")
    req = svc.files().get_media(fileId=target["id"])
    with open(DB_PATH, "wb") as fh:
        dl = MediaIoBaseDownload(fh, req)
        done = False
        while not done:
            _, done = dl.next_chunk()
    print(f"✓ Restored {target['name']} to {DB_PATH}")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Back up / restore the trading DB to Google Drive")
    p.add_argument("--keep", type=int, default=None, help="after upload, keep only the N newest backups")
    p.add_argument("--list", action="store_true", help="list backups in the Drive folder")
    p.add_argument("--restore", nargs="?", const="__latest__", help="restore a backup (default: newest)")
    a = p.parse_args()
    if a.list:
        list_backups()
    elif a.restore is not None:
        restore(None if a.restore == "__latest__" else a.restore)
    else:
        backup(keep=a.keep)
