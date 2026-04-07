"""AthenaScout — Main FastAPI Application

This file is the application entry point. It creates the FastAPI app, sets up
the lifespan context manager (library discovery, initial sync, scheduler,
shutdown cleanup), and registers all route modules from app/routers/.

For individual endpoints, see app/routers/. For shared background-task state
(scan tasks, library discovery cache), see app/state.py.
"""
import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.calibre_sync import sync_calibre
from app.config import (
    SYNC_INTERVAL_MINUTES,
    apply_logging,
    discover_libraries,
    load_settings,
    save_settings,
)
from app.database import (
    get_active_library,
    get_db,
    init_db,
    match_legacy_db_to_library,
    migrate_legacy_db,
    set_active_library,
)
from app.library_apps import get_app
from app.lookup import reload_sources, run_full_lookup
from app.routers import (
    authors,
    books,
    config,
    covers,
    db_editor,
    import_export,
    libraries,
    mam,
    scan,
    series,
)
from app.runtime import IS_DOCKER, IS_STANDALONE
from app.sources.mam import (
    close_session as mam_close_session,
    scan_books_batch as mam_scan_batch,
    validate_connection as mam_validate,
    _resolve_mam_languages,
)
from app import state


# ─── Logging setup ───────────────────────────────────────────
class QuietAccessFilter(logging.Filter):
    """Filter out noisy polling endpoints from uvicorn's access log."""
    NOISY = ("/api/health", "/api/covers/", "/api/series/")

    def filter(self, record):
        msg = record.getMessage()
        return not any(p in msg for p in self.NOISY)


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logging.getLogger("uvicorn.access").addFilter(QuietAccessFilter())
logger = logging.getLogger("athenascout")

scheduler = AsyncIOScheduler()


# ─── Lifespan ────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    s = load_settings()
    apply_logging(s.get("verbose_logging", False))
    reload_sources()

    # ─── Library Discovery ────────────────────────────────
    state._discovered_libraries = discover_libraries(s)
    if not state._discovered_libraries:
        if IS_DOCKER:
            logger.warning("No libraries found. Check CALIBRE_PATH env var and volume mounts, or use the setup wizard.")
        else:
            logger.info("No libraries configured yet. The setup wizard will guide you through setup.")
        # Initialize a default database so the app can start and serve the UI
        await init_db()
    else:
        # Group discovered libraries by app type for logging
        by_app = {}
        for l in state._discovered_libraries:
            at = l.get("display_name", "Unknown")
            by_app.setdefault(at, []).append(l["name"])
        lib_summary = "; ".join(f'{len(v)} {k} ({", ".join(v)})' for k, v in by_app.items())
        logger.info(f"Discovered {len(state._discovered_libraries)} libraries: {lib_summary}")

        # Migration: rename legacy athenascout.db → best-matching library's DB file
        first_slug = state._discovered_libraries[0]["slug"]
        migration_slug = match_legacy_db_to_library(state._discovered_libraries)
        migrated_to = migrate_legacy_db(migration_slug)
        if migrated_to:
            logger.info(f"Legacy database migrated to library '{migrated_to}'")
            first_slug = migrated_to  # use migrated library as default active

        # Initialize all library databases
        for lib in state._discovered_libraries:
            await init_db(lib["slug"])
            logger.debug(f"Initialized database for library '{lib['name']}'")

        # Set active library (from settings or first discovered)
        active = s.get("active_library") or first_slug
        valid_slugs = [l["slug"] for l in state._discovered_libraries]
        if active not in valid_slugs:
            active = first_slug
        set_active_library(active)
        s["active_library"] = active
        save_settings(s)
        logger.info(f"Active library: '{active}'")

        # Sync each library (with mtime optimization)
        mtimes = s.get("calibre_mtimes", {})
        for lib in state._discovered_libraries:
            set_active_library(lib["slug"])
            try:
                current_mtime = os.path.getmtime(lib["source_db_path"])
                last_mtime = mtimes.get(lib["slug"])
                if last_mtime is not None and current_mtime == last_mtime:
                    logger.info(f"Library '{lib['name']}': metadata.db unchanged, skipping sync")
                else:
                    lib_app = get_app(lib.get("app_type", "calibre"))
                    logger.info(f"Library '{lib['name']}': syncing from {lib_app.display_name if lib_app else 'unknown'}...")
                    if lib_app:
                        await lib_app.sync(lib["source_db_path"], lib["library_path"])
                    else:
                        await sync_calibre(lib["source_db_path"], lib["library_path"])
                    mtimes[lib["slug"]] = current_mtime
                    s["calibre_mtimes"] = mtimes
                    save_settings(s)
            except Exception as e:
                logger.warning(f"Sync failed for library '{lib['name']}': {e}")

        # Restore active library after syncing all
        set_active_library(active)
        state._last_calibre_check["at"] = time.time()
        state._last_calibre_check["synced"] = True

    # ─── Scheduled Calibre Sync (all libraries) ───────────
    s = load_settings()
    sync_min = s.get("calibre_sync_interval_minutes", SYNC_INTERVAL_MINUTES)
    lookup_days = s.get("lookup_interval_days", 3)

    async def _sync_all_libraries():
        """Scheduled task: sync all libraries with mtime optimization."""
        current_active = get_active_library()
        st = load_settings()
        mtimes = st.get("calibre_mtimes", {})
        any_synced = False
        for lib in state._discovered_libraries:
            try:
                set_active_library(lib["slug"])
                current_mtime = os.path.getmtime(lib["source_db_path"])
                last_mtime = mtimes.get(lib["slug"])
                if last_mtime is not None and current_mtime == last_mtime:
                    logger.debug(f"Scheduled sync: '{lib['name']}' metadata.db unchanged, skipping")
                    continue
                lib_app = get_app(lib.get("app_type", "calibre"))
                logger.info(f"Scheduled sync: '{lib['name']}' {lib_app.db_filename if lib_app else 'database'} changed, syncing...")
                if lib_app:
                    await lib_app.sync(lib["source_db_path"], lib["library_path"])
                else:
                    await sync_calibre(lib["source_db_path"], lib["library_path"])
                mtimes[lib["slug"]] = current_mtime
                st["calibre_mtimes"] = mtimes
                save_settings(st)
                any_synced = True
            except Exception as e:
                logger.warning(f"Scheduled sync failed for '{lib['name']}': {e}")
        set_active_library(current_active)
        state._last_calibre_check["at"] = time.time()
        state._last_calibre_check["synced"] = any_synced

    if sync_min and sync_min > 0:
        if state._discovered_libraries:
            scheduler.add_job(_sync_all_libraries, "interval", minutes=sync_min, id="calibre_sync", replace_existing=True)
        else:
            logger.info("Calibre auto-sync skipped - no libraries configured")
    else:
        logger.info("Calibre auto-sync disabled (interval = 0)")

    async def _scheduled_lookup():
        s = load_settings()
        if not s.get("author_scanning_enabled", True):
            return
        if state._lookup_progress.get("running"):
            return
        state._lookup_progress = {"running": True, "checked": 0, "total": 0, "current_author": "",
                            "new_books": 0, "status": "scanning", "type": "scheduled_lookup"}
        def _progress(data):
            state._lookup_progress.update({"checked": data["checked"], "total": data["total"],
                                     "current_author": data["current_author"], "new_books": data["new_books"]})
        try:
            await run_full_lookup(on_progress=_progress)
            state._lookup_progress.update({"running": False, "status": "complete"})
        except Exception as e:
            logger.error(f"Scheduled lookup error: {e}")
            state._lookup_progress.update({"running": False, "status": f"error: {e}"})

    if lookup_days and lookup_days > 0:
        scheduler.add_job(_scheduled_lookup, "interval", minutes=lookup_days*1440, id="author_lookup", replace_existing=True)
    else:
        logger.info("Auto-lookup disabled (interval = 0)")

    async def _mam_scheduler():
        last_scan_at = 0.0
        while True:
            await asyncio.sleep(60)
            s = load_settings()
            interval = s.get("mam_scan_interval_minutes", 360)
            if interval <= 0 or not s.get("mam_enabled") or not s.get("mam_session_id") or not s.get("mam_scanning_enabled", True):
                continue
            elapsed_min = (time.time() - last_scan_at) / 60
            if elapsed_min < interval:
                continue
            if state._mam_scan_progress.get("running"):
                continue
            if state._lookup_progress.get("running"):
                continue  # Author scan has priority
            last_val = s.get("last_mam_validated_at") or 0
            if time.time() - last_val > 86400:
                logger.info("MAM daily validation check...")
                vr = await mam_validate(s["mam_session_id"], True)
                if vr["success"]:
                    s["last_mam_validated_at"] = time.time()
                    s["mam_validation_ok"] = True
                else:
                    s["mam_validation_ok"] = False
                save_settings(s)
                if not vr["success"]:
                    logger.error(f"MAM validation failed — skipping scan: {vr['message']}")
                    last_scan_at = time.time()
                    continue
            # Query total remaining for context
            db = await get_db()
            try:
                rem_row = await db.execute_fetchall(
                    "SELECT COUNT(*) FROM books WHERE mam_status IS NULL AND is_unreleased=0 AND hidden=0"
                )
                total_remaining = rem_row[0][0] if rem_row else 0
            finally:
                await db.close()
            if total_remaining == 0:
                logger.info("MAM scheduled scan: no books need scanning")
                last_scan_at = time.time()
                continue
            scan_limit = min(100, total_remaining)
            logger.info(f"MAM scheduled scan starting ({scan_limit} books, {total_remaining} total remaining)")
            state._mam_scan_progress = {"running": True, "scanned": 0, "total": scan_limit,
                                  "found": 0, "possible": 0, "not_found": 0,
                                  "errors": 0, "status": "scanning", "type": "scheduled",
                                  "remaining": total_remaining}
            def _sched_progress(stats):
                state._mam_scan_progress.update({
                    "scanned": stats["scanned"],
                    "found": stats["found"],
                    "possible": stats["possible"],
                    "not_found": stats["not_found"],
                    "errors": stats["errors"],
                })
            db = await get_db()
            try:
                result = await mam_scan_batch(
                    db, session_id=s["mam_session_id"], limit=100,
                    delay=s.get("rate_mam", 2), skip_ip_update=True,
                    format_priority=s.get("mam_format_priority"),
                    on_progress=_sched_progress,
                    cancel_check=lambda: state._lookup_progress.get("running", False),
                    lang_ids=_resolve_mam_languages(s.get("languages", ["English"])),
                )
                state._mam_scan_progress.update({
                    "running": False,
                    "status": "complete" if not result.get("error") else f"error: {result.get('error')}",
                })
                await db.execute(
                    "INSERT INTO sync_log (sync_type, started_at, finished_at, status, books_found, books_new) VALUES (?,?,?,?,?,?)",
                    ("mam", time.time(), time.time(),
                     "complete" if not result.get("error") else "error",
                     result.get("scanned", 0), result.get("found", 0))
                )
                await db.commit()
                logger.info(f"MAM scheduled scan done: {result.get('scanned', 0)} scanned, {result.get('found', 0)} found")
            except Exception as e:
                logger.error(f"MAM scheduled scan error: {e}")
                state._mam_scan_progress.update({"running": False, "status": f"error: {e}"})
            finally:
                await db.close()
            last_scan_at = time.time()

    asyncio.create_task(_mam_scheduler())
    scheduler.start()
    yield
    scheduler.shutdown()
    # Tear down the MAM HTTP session (Phase 22B.2.5)
    mam_close_session()


# ─── App + Router Registration ───────────────────────────────
app = FastAPI(title="AthenaScout", lifespan=lifespan)

# All API routes live in app/routers/ — see individual files for endpoints.
for r in (config, libraries, books, authors, series, covers, scan, mam, db_editor, import_export):
    app.include_router(r.router)


# ─── Frontend Static File Serving ────────────────────────────
# Support both source tree and PyInstaller bundle layouts.
# PyInstaller sets sys._MEIPASS to its temp extraction directory.
_pyinstaller_base = getattr(sys, '_MEIPASS', None)
if _pyinstaller_base:
    FD = Path(_pyinstaller_base) / "frontend" / "dist"
else:
    FD = Path(__file__).parent.parent / "frontend" / "dist"

if FD.exists():
    if (FD / "assets").exists():
        app.mount("/assets", StaticFiles(directory=FD / "assets"), name="assets")

    @app.get("/{path:path}")
    async def serve_fe(path: str):
        fp = FD / path
        return FileResponse(fp if fp.is_file() else FD / "index.html")
elif IS_STANDALONE:
    @app.get("/{path:path}")
    async def serve_fe_missing(path: str):
        return {"error": "Frontend not built. Run 'cd frontend && npm install && npm run build' first."}
