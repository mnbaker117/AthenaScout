"""
Author lookup, sync, and full-rescan endpoints for AthenaScout.

Holds /api/sync/calibre, /api/sync, /api/sync/lookup, /api/lookup,
/api/lookup/cancel, /api/lookup/status, /api/sync/full-rescan,
/api/scanning/author/toggle, /api/scanning/mam/toggle.
"""
import asyncio
import logging
import os
import time
from fastapi import APIRouter, HTTPException

from app.calibre_sync import sync_calibre
from app.config import load_settings, save_settings
from app.database import get_active_library, get_db
from app.library_apps import get_app
from app.lookup import run_full_lookup, run_full_rescan
from app import state

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api", tags=["scan"])


# ─── Sync ────────────────────────────────────────────────────
@router.post("/sync/calibre")
async def trigger_sync():
    active_slug = get_active_library()
    lib = next((l for l in state._discovered_libraries if l["slug"] == active_slug), None)
    try:
        if lib:
            app_instance = get_app(lib.get("app_type", "calibre"))
            if app_instance:
                result = await app_instance.sync(lib["source_db_path"], lib["library_path"])
            else:
                result = await sync_calibre(lib["source_db_path"], lib["library_path"])
            # Update mtime after successful manual sync
            s = load_settings()
            mtimes = s.get("calibre_mtimes", {})
            mtimes[active_slug] = os.path.getmtime(lib["source_db_path"])
            s["calibre_mtimes"] = mtimes
            save_settings(s)
        else:
            result = await sync_calibre()
        state._last_calibre_check["at"] = time.time()
        state._last_calibre_check["synced"] = True
        return {"status": "ok", **result}
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/sync")
async def trigger_sync_alias():
    return await trigger_sync()


# ─── Author Lookup ───────────────────────────────────────────
@router.post("/sync/lookup")
async def trigger_lookup():
    s = load_settings()
    if not s.get("author_scanning_enabled", True):
        return {"error": "Author scanning is disabled — enable it in Settings"}
    if state._lookup_task and not state._lookup_task.done():
        return {"error": "An author scan is already running"}

    # Pre-flight: how many authors are actually due for scanning given the
    # cache window? If zero, surface a clear "nothing to do" status instead
    # of starting a no-op task that briefly shows "Scanning... 0 of 0" then
    # vanishes. Users hitting this most often have just completed a scan and
    # are inside the lookup_interval_days cache window.
    cache_sec = s.get("lookup_interval_days", 3) * 86400
    cutoff = time.time() - cache_sec
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT COUNT(*) c FROM authors WHERE COALESCE(last_lookup_at,0) < ?",
            (cutoff,),
        )).fetchone()
        due_count = row["c"] if row else 0
    finally:
        await db.close()
    if due_count == 0:
        state._lookup_progress = {
            "running": False, "checked": 0, "total": 0, "current_author": "",
            "new_books": 0, "type": "lookup",
            "status": f"no authors due (cache window: {s.get('lookup_interval_days', 3)} days)",
        }
        return {"status": "ok", "due": 0,
                "message": "No authors due for scanning within the current cache window."}

    state._lookup_progress = {"running": True, "checked": 0, "total": due_count, "current_author": "",
                        "new_books": 0, "status": "scanning", "type": "lookup"}
    def _progress(data):
        state._lookup_progress.update({"checked": data["checked"], "total": data["total"],
                                 "current_author": data["current_author"], "new_books": data["new_books"]})
    async def _do():
        try:
            await run_full_lookup(on_progress=_progress)
            state._lookup_progress.update({"running": False, "status": "complete"})
        except Exception as e:
            logger.error(f"Author scan error: {e}")
            state._lookup_progress.update({"running": False, "status": f"error: {e}"})
    state._lookup_task = asyncio.create_task(_do())
    return {"status": "started"}


@router.post("/lookup")
async def trigger_lookup_alias():
    return await trigger_lookup()


@router.post("/lookup/cancel")
async def lookup_cancel():
    """Cancel the currently running author scan."""
    if state._lookup_task and not state._lookup_task.done():
        state._lookup_task.cancel()
        state._lookup_progress.update({"running": False, "status": "cancelled"})
        logger.info("Author scan cancelled by user")
        return {"status": "ok", "message": "Author scan cancelled"}
    return {"status": "ok", "message": "No author scan running"}


@router.get("/lookup/status")
async def lookup_status():
    """Get progress of the current/most recent author scan."""
    return dict(state._lookup_progress)


@router.post("/sync/full-rescan")
async def trigger_full_rescan():
    s = load_settings()
    if not s.get("author_scanning_enabled", True):
        return {"error": "Author scanning is disabled — enable it in Settings"}
    if state._lookup_task and not state._lookup_task.done():
        return {"error": "An author scan is already running"}
    state._lookup_progress = {"running": True, "checked": 0, "total": 0, "current_author": "",
                        "new_books": 0, "status": "scanning", "type": "full_rescan"}
    def _progress(data):
        state._lookup_progress.update({"checked": data["checked"], "total": data["total"],
                                 "current_author": data["current_author"], "new_books": data["new_books"]})
    async def _do():
        try:
            await run_full_rescan(on_progress=_progress)
            state._lookup_progress.update({"running": False, "status": "complete"})
        except Exception as e:
            logger.error(f"Full re-scan error: {e}")
            state._lookup_progress.update({"running": False, "status": f"error: {e}"})
    state._lookup_task = asyncio.create_task(_do())
    return {"status": "started"}


# ─── Scanning Toggles ────────────────────────────────────────
@router.post("/scanning/author/toggle")
async def toggle_author_scanning():
    """Toggle author scanning on/off. Cancels running scan when disabled."""
    s = load_settings()
    new_val = not s.get("author_scanning_enabled", True)
    s["author_scanning_enabled"] = new_val
    save_settings(s)
    if not new_val and state._lookup_task and not state._lookup_task.done():
        state._lookup_task.cancel()
        state._lookup_progress.update({"running": False, "status": "cancelled"})
        logger.info("Author scanning disabled — cancelled running scan")
    return {"enabled": new_val}


@router.post("/scanning/mam/toggle")
async def toggle_mam_scanning():
    """Toggle MAM scanning on/off without affecting MAM feature visibility."""
    s = load_settings()
    new_val = not s.get("mam_scanning_enabled", True)
    s["mam_scanning_enabled"] = new_val
    save_settings(s)
    if not new_val:
        if state._mam_scan_task and not state._mam_scan_task.done():
            state._mam_scan_task.cancel()
            state._mam_scan_progress.update({"running": False, "status": "cancelled"})
        if state._mam_full_scan_task and not state._mam_full_scan_task.done():
            state._mam_full_scan_task.cancel()
            state._mam_scan_progress.update({"running": False, "status": "cancelled"})
        logger.info("MAM scanning disabled — cancelled running scans")
    return {"enabled": new_val}
