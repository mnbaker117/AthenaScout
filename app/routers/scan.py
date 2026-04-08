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
    # Flag the sync so background writers (MAM scanner) yield to us
    # instead of racing on the write lock. Always cleared in finally.
    state._calibre_sync_in_progress = True
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
    finally:
        state._calibre_sync_in_progress = False


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
        # Match what run_full_lookup() actually iterates: skip orphan
        # authors so the "due count" estimate isn't inflated by authors
        # that the lookup loop will silently filter out.
        row = await (await db.execute(
            "SELECT COUNT(*) c FROM authors WHERE COALESCE(last_lookup_at,0) < ? AND id IN (SELECT DISTINCT author_id FROM books)",
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


# ─── Phase 3d-1: unified scan status ─────────────────────────
# A single endpoint the Dashboard can poll instead of fanning out to
# /lookup/status and /mam/scan/status separately. Each progress dict
# is projected into a uniform shape with `current`/`total` (the actual
# numerator/denominator the dict tracks under different field names),
# a `kind`/`type`/`label` triple, and a `extra` bag for kind-specific
# numbers like new_books or MAM found/possible counts. The frontend
# maps over `scans` and renders one row per active scan.
#
# We project rather than restructure the underlying state to keep the
# legacy /lookup/status and /mam/scan/status endpoints working for the
# MAMPage and SettingsPage, which still consume them directly.
def _label_for(kind: str, scan_type: str) -> str:
    """Human-readable label for a (kind, type) pair."""
    if kind == "lookup":
        return {
            "lookup":             "Source Scan",
            "full_rescan":        "Full Re-Scan",
            "scheduled_lookup":   "Scheduled Source Scan",
            "single_author":      "Author Scan",
            "single_author_full": "Author Full Re-Scan",
            "bulk_authors":       "Bulk Author Scan",
            "bulk_books":         "Bulk Book Scan",
        }.get(scan_type, "Source Scan")
    if kind == "mam":
        return {
            "manual":    "MAM Scan",
            "scheduled": "Scheduled MAM Scan",
            "full_scan": "MAM Full Scan",
        }.get(scan_type, "MAM Scan")
    return scan_type or kind


def _project_lookup() -> dict:
    """Project _lookup_progress into the unified shape."""
    p = state._lookup_progress
    return {
        "kind": "lookup",
        "type": p.get("type", "none"),
        "label": _label_for("lookup", p.get("type", "none")),
        "running": bool(p.get("running")),
        "current": p.get("checked", 0),
        "total": p.get("total", 0),
        "current_label": p.get("current_author", "") or None,
        "status": p.get("status", "idle"),
        "extra": {
            "new_books": p.get("new_books", 0),
        },
    }


def _project_mam() -> dict:
    """Project _mam_scan_progress into the unified shape."""
    p = state._mam_scan_progress
    return {
        "kind": "mam",
        "type": p.get("type", "none"),
        "label": _label_for("mam", p.get("type", "none")),
        "running": bool(p.get("running")),
        "current": p.get("scanned", 0),
        "total": p.get("total", 0),
        "current_label": None,
        "status": p.get("status", "idle"),
        "extra": {
            "found":     p.get("found", 0),
            "possible":  p.get("possible", 0),
            "not_found": p.get("not_found", 0),
            "errors":    p.get("errors", 0),
            "remaining": p.get("remaining"),
        },
    }


@router.get("/scan-status")
async def scan_status():
    """Unified scan progress for the Dashboard widget.

    Returns every tracked scan in a uniform shape regardless of whether
    it's an author lookup, full re-scan, MAM scan, scheduled job, or a
    single-author trigger from the Author page. The frontend renders
    one row per scan with running > complete > idle ordering. A scan
    in 'idle' state with type='none' is filtered out so the widget
    auto-hides when nothing has run yet.
    """
    out = []
    for proj in (_project_lookup(), _project_mam()):
        # Hide entries that are pristine idle (never ran). Keep complete
        # ones so the user sees the result of the last scan even after
        # it finishes.
        if proj["status"] == "idle" and proj["type"] == "none":
            continue
        out.append(proj)
    return {"scans": out}


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
