"""
Automatic + on-demand backups of the SQLite database to Google Drive.

WHY
----
Even a properly-configured Render persistent disk is a single point of
failure — accidental deletion, a plan/billing lapse, disk corruption, etc.
This keeps a rolling set of timestamped copies of cmms.db in a Google Drive
folder you control, completely independent of Render.

ONE-TIME SETUP
----------------
1. In Google Cloud Console (console.cloud.google.com), create a project (or
   reuse one) and enable the "Google Drive API" for it.
2. Create a Service Account (IAM & Admin → Service Accounts), then create a
   JSON key for it and download the file. Service accounts don't expire
   like personal OAuth tokens, so they're the right fit for an unattended
   server-side job like this.
3. In your OWN Google Drive (the one with real storage quota), create a
   folder for backups, e.g. "CMMS Backups". Right-click → Share, and share
   it with the service account's email address — it looks like
   xxxxx@xxxxx.iam.gserviceaccount.com and is the "client_email" field
   inside the JSON key you downloaded. Give it "Editor" access.
   This step matters: a bare service account has 0 storage of its own, so
   without sharing a real folder into it, uploads will fail.
4. Open that folder in a browser and copy its ID out of the URL:
       https://drive.google.com/drive/folders/<THIS_PART_IS_THE_FOLDER_ID>
5. On Render, add two environment variables to your web service:
       GDRIVE_BACKUP_FOLDER_ID     = the folder ID from step 4
       GDRIVE_SERVICE_ACCOUNT_JSON = the ENTIRE contents of the JSON key
                                      file, pasted as one env var value
   Optional:
       GDRIVE_BACKUP_RETENTION    = how many backups to keep (default 30)
6. Make sure google-api-python-client and google-auth are in
   requirements.txt (already added) and redeploy.

WHAT IT DOES
-------------
- Runs automatically once a day via the existing APScheduler in main.py,
  uploading a timestamped copy of cmms.db (cmms_backup_YYYYMMDD_HHMMSS.db).
- Keeps only the most recent GDRIVE_BACKUP_RETENTION backups in the Drive
  folder, deleting older ones so storage doesn't grow forever.
- Also reachable on demand via POST /admin/backup-now (admin only) — handy
  right before a risky change, or just to confirm it's wired up correctly.
- Never crashes the app if a backup fails (missing config, bad credentials,
  network hiccup, Drive API error) — it logs the problem and the rest of
  the CMMS keeps running untouched.
"""
import asyncio
import json
import logging
import os
from datetime import datetime

logger = logging.getLogger("backup")

FOLDER_ID        = os.environ.get("GDRIVE_BACKUP_FOLDER_ID")
SERVICE_ACCOUNT_JSON = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON")
RETENTION_COUNT  = int(os.environ.get("GDRIVE_BACKUP_RETENTION", "30"))


def is_configured() -> bool:
    return bool(FOLDER_ID and SERVICE_ACCOUNT_JSON)


def _get_drive_service():
    """
    Lazily builds an authenticated Google Drive API client. The Google
    client libraries are imported here (not at module level) so the rest
    of the app can still start up fine even before backups are configured
    or the packages are installed.
    """
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    if not SERVICE_ACCOUNT_JSON:
        raise RuntimeError(
            "GDRIVE_SERVICE_ACCOUNT_JSON is not set — see backup.py's "
            "module docstring for one-time setup steps."
        )
    info = json.loads(SERVICE_ACCOUNT_JSON)
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def backup_now() -> dict:
    """
    Uploads a timestamped copy of the current SQLite database file to the
    configured Google Drive folder, then prunes old backups beyond
    RETENTION_COUNT. Synchronous/blocking — call via asyncio.to_thread(...)
    from async code (see scheduled_backup_job below). Raises on hard
    failures (missing config, auth errors, upload errors) so callers can
    decide how to surface them.
    """
    from db import DEFAULT_DB_PATH
    from googleapiclient.http import MediaFileUpload

    if not FOLDER_ID:
        raise RuntimeError(
            "GDRIVE_BACKUP_FOLDER_ID is not set — see backup.py's "
            "module docstring for one-time setup steps."
        )
    if not os.path.exists(DEFAULT_DB_PATH):
        raise RuntimeError(f"Database file not found at {DEFAULT_DB_PATH}")

    service  = _get_drive_service()
    stamp    = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"cmms_backup_{stamp}.db"

    media = MediaFileUpload(DEFAULT_DB_PATH, mimetype="application/x-sqlite3", resumable=False)
    uploaded = service.files().create(
        body={"name": filename, "parents": [FOLDER_ID]},
        media_body=media,
        fields="id, name, createdTime, size",
    ).execute()

    logger.info("Backup uploaded: %s (%s bytes)", uploaded.get("name"), uploaded.get("size"))
    deleted = _prune_old_backups(service)
    if deleted:
        logger.info("Pruned %d old backup(s): %s", len(deleted), ", ".join(deleted))

    return {"uploaded": uploaded, "deleted": deleted}


def _prune_old_backups(service) -> list[str]:
    """Keeps only the RETENTION_COUNT most recent backups in the Drive folder."""
    resp = service.files().list(
        q=f"'{FOLDER_ID}' in parents and name contains 'cmms_backup_' and trashed = false",
        fields="files(id, name, createdTime)",
        orderBy="createdTime desc",
        pageSize=1000,
    ).execute()
    files = resp.get("files", [])
    to_delete = files[RETENTION_COUNT:]
    deleted_names = []
    for f in to_delete:
        try:
            service.files().delete(fileId=f["id"]).execute()
            deleted_names.append(f["name"])
        except Exception as e:
            logger.warning("Failed to delete old backup %s: %s", f["name"], e)
    return deleted_names


async def scheduled_backup_job():
    """
    APScheduler entry point. Runs the (blocking) Drive upload in a worker
    thread so it never blocks the async event loop / the rest of the app,
    and swallows failures so a bad backup run can't take the scheduler or
    the API down with it.
    """
    if not is_configured():
        logger.info("Skipping scheduled backup — GDRIVE_BACKUP_FOLDER_ID / "
                     "GDRIVE_SERVICE_ACCOUNT_JSON not set yet.")
        return
    try:
        result = await asyncio.to_thread(backup_now)
        logger.info("Scheduled backup complete: %s", result["uploaded"]["name"])
    except Exception as e:
        logger.error("Scheduled backup FAILED: %s", e)