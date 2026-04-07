"""AthenaScout — Main FastAPI Application"""
import logging, time, asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Query, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.config import (SYNC_INTERVAL_MINUTES, load_settings, save_settings, apply_logging, discover_libraries)
from app.library_apps import get_app
from app.database import init_db, get_db, set_active_library, get_active_library, migrate_legacy_db, match_legacy_db_to_library, HF
from app import state
from app.routers.db_editor import DB_TABLES, DB_FK_RESOLVERS


# Filter out noisy health check and cover/series access logs
class QuietAccessFilter(logging.Filter):
    NOISY = ("/api/health", "/api/covers/", "/api/series/")
    def filter(self, record):
        msg = record.getMessage()
        return not any(p in msg for p in self.NOISY)

# Apply filter to uvicorn access logger
uv_access = logging.getLogger("uvicorn.access")
uv_access.addFilter(QuietAccessFilter())
from app.calibre_sync import sync_calibre
from app.lookup import run_full_lookup, run_full_rescan, reload_sources
from app.sources.mam import (
    validate_connection as mam_validate,
    scan_books_batch as mam_scan_batch,
    start_full_scan as mam_start_full_scan,
    run_full_scan_batch as mam_run_full_scan_batch,
    cancel_full_scan as mam_cancel_full_scan,
    get_full_scan_status as mam_get_full_scan_status,
    get_mam_stats,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("athenascout")
scheduler = AsyncIOScheduler()

@asynccontextmanager
async def lifespan(app: FastAPI):
    import os as _os

    s = load_settings()
    apply_logging(s.get("verbose_logging", False))
    reload_sources()

    # ─── Library Discovery ────────────────────────────────
    state._discovered_libraries = discover_libraries(s)
    if not state._discovered_libraries:
        from app.runtime import IS_DOCKER
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
                current_mtime = _os.path.getmtime(lib["source_db_path"])
                last_mtime = mtimes.get(lib["slug"])
                if last_mtime is not None and current_mtime == last_mtime:
                    logger.info(f"Library '{lib['name']}': metadata.db unchanged, skipping sync")
                else:
                    app = get_app(lib.get("app_type", "calibre"))
                    logger.info(f"Library '{lib['name']}': syncing from {app.display_name if app else 'unknown'}...")
                    if app:
                        await app.sync(lib["source_db_path"], lib["library_path"])
                    else:
                        from app.calibre_sync import sync_calibre
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
        import os as _os2
        current_active = get_active_library()
        st = load_settings()
        mtimes = st.get("calibre_mtimes", {})
        any_synced = False
        for lib in state._discovered_libraries:
            try:
                set_active_library(lib["slug"])
                current_mtime = _os2.path.getmtime(lib["source_db_path"])
                last_mtime = mtimes.get(lib["slug"])
                if last_mtime is not None and current_mtime == last_mtime:
                    logger.debug(f"Scheduled sync: '{lib['name']}' metadata.db unchanged, skipping")
                    continue
                app = get_app(lib.get("app_type", "calibre"))
                logger.info(f"Scheduled sync: '{lib['name']}' {app.db_filename if app else 'database'} changed, syncing...")
                if app:
                    await app.sync(lib["source_db_path"], lib["library_path"])
                else:
                    from app.calibre_sync import sync_calibre
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
    scheduler.start(); yield; scheduler.shutdown()

app = FastAPI(title="AthenaScout", lifespan=lifespan)

# ─── Router registration ─────────────────────────────────────
# Routers are currently empty scaffolds; routes still live inline below.
# Stage A3 will move routes out one group at a time.
from app.routers import (
    config as _r_config,
    libraries as _r_libraries,
    books as _r_books,
    authors as _r_authors,
    series as _r_series,
    covers as _r_covers,
    scan as _r_scan,
    mam as _r_mam,
    db_editor as _r_db_editor,
    import_export as _r_import_export,
)
app.include_router(_r_config.router)
app.include_router(_r_libraries.router)
app.include_router(_r_books.router)
app.include_router(_r_authors.router)
app.include_router(_r_series.router)
app.include_router(_r_covers.router)
app.include_router(_r_scan.router)
app.include_router(_r_mam.router)
app.include_router(_r_db_editor.router)
app.include_router(_r_import_export.router)


# ─── Sync ────────────────────────────────────────────────────
@app.post("/api/sync/calibre")
async def trigger_sync():
    import os as _os
    active_slug = get_active_library()
    lib = next((l for l in state._discovered_libraries if l["slug"] == active_slug), None)
    try:
        if lib:
            app = get_app(lib.get("app_type", "calibre"))
            if app:
                result = await app.sync(lib["source_db_path"], lib["library_path"])
            else:
                from app.calibre_sync import sync_calibre
                result = await sync_calibre(lib["source_db_path"], lib["library_path"])
            # Update mtime after successful manual sync
            s = load_settings()
            mtimes = s.get("calibre_mtimes", {})
            mtimes[active_slug] = _os.path.getmtime(lib["source_db_path"])
            s["calibre_mtimes"] = mtimes
            save_settings(s)
        else:
            result = await sync_calibre()
        state._last_calibre_check["at"] = time.time()
        state._last_calibre_check["synced"] = True
        return {"status": "ok", **result}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/sync")
async def trigger_sync_alias():
    return await trigger_sync()

@app.post("/api/sync/lookup")
async def trigger_lookup():
    s = load_settings()
    if not s.get("author_scanning_enabled", True):
        return {"error": "Author scanning is disabled — enable it in Settings"}
    if state._lookup_task and not state._lookup_task.done():
        return {"error": "An author scan is already running"}
    state._lookup_progress = {"running": True, "checked": 0, "total": 0, "current_author": "",
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

@app.post("/api/lookup")
async def trigger_lookup_alias():
    return await trigger_lookup()

@app.post("/api/lookup/cancel")
async def lookup_cancel():
    """Cancel the currently running author scan."""
    if state._lookup_task and not state._lookup_task.done():
        state._lookup_task.cancel()
        state._lookup_progress.update({"running": False, "status": "cancelled"})
        logger.info("Author scan cancelled by user")
        return {"status": "ok", "message": "Author scan cancelled"}
    return {"status": "ok", "message": "No author scan running"}


@app.get("/api/lookup/status")
async def lookup_status():
    """Get progress of the current/most recent author scan."""
    return dict(state._lookup_progress)

@app.post("/api/sync/full-rescan")
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


# ─── MAM Integration ─────────────────────────────────────────

@app.post("/api/mam/validate")
async def mam_validate_endpoint():
    """Test MAM session ID — runs IP registration + search auth."""
    s = load_settings()
    session_id = s.get("mam_session_id", "")
    if not session_id:
        return {"success": False, "message": "No MAM session ID configured"}
    skip_ip = s.get("mam_skip_ip_update", False)
    result = await mam_validate(session_id, skip_ip)
    if result["success"]:
        s["mam_enabled"] = True
        s["last_mam_validated_at"] = time.time()
        s["mam_validation_ok"] = True
    else:
        s["mam_validation_ok"] = False
    save_settings(s)
    return result


@app.get("/api/mam/status")
async def mam_status_endpoint():
    """Get MAM integration status and stats."""
    s = load_settings()
    enabled = s.get("mam_enabled", False) and bool(s.get("mam_session_id", ""))
    if not enabled:
        return {"enabled": False, "stats": None, "full_scan": None}
    db = await get_db()
    try:
        stats = await get_mam_stats(db)
        scan_status = await mam_get_full_scan_status(db)
        return {"enabled": True, "stats": stats, "full_scan": scan_status,
                "validation_ok": s.get("mam_validation_ok", True),
                "last_validated_at": s.get("last_mam_validated_at")}
    finally:
        await db.close()


@app.post("/api/mam/scan")
async def mam_scan_endpoint(limit: int = Query(None, ge=1)):
    """Scan books missing MAM data. Batches of 100 with 5-min pauses.
    If limit is provided, scan at most that many books total."""
    s = load_settings()
    if not s.get("mam_enabled") or not s.get("mam_session_id"):
        return {"error": "MAM not configured or not enabled"}
    if not s.get("mam_scanning_enabled", True):
        return {"error": "MAM scanning is disabled — enable it in Settings"}
    if state._mam_scan_progress.get("running"):
        return {"error": "A MAM scan is already running"}
    if state._lookup_progress.get("running"):
        return {"error": "An author scan is running — MAM scan will wait until it finishes"}

    db = await get_db()
    try:
        row = await db.execute_fetchall(
            "SELECT COUNT(*) FROM books WHERE mam_status IS NULL AND is_unreleased=0 AND hidden=0"
        )
        total = row[0][0] if row else 0
    finally:
        await db.close()

    if total == 0:
        return {"status": "complete", "message": "No books need scanning — all already have MAM data"}

    scan_total = min(total, limit) if limit else total
    state._mam_scan_progress = {"running": True, "scanned": 0, "total": scan_total,
                          "found": 0, "possible": 0, "not_found": 0, "errors": 0,
                          "status": "scanning", "type": "manual"}

    async def _do_scan():
        batch_num = 0
        while True:
            # Wait for any author scan before starting next batch
            if state._lookup_progress.get("running"):
                state._mam_scan_progress["status"] = "waiting (author scan running)"
                logger.info("MAM scan waiting for author scan to finish...")
                while state._lookup_progress.get("running"):
                    await asyncio.sleep(30)
                logger.info("Author scan finished — MAM scan resuming")
                state._mam_scan_progress["status"] = "scanning"
            cs = load_settings()
            if not cs.get("mam_enabled") or not cs.get("mam_session_id"):
                state._mam_scan_progress.update({"status": "stopped (MAM disabled)", "running": False})
                return
            db = await get_db()
            try:
                def _progress(stats):
                    state._mam_scan_progress.update({
                        "scanned": base_scanned + stats["scanned"],
                        "found": base_found + stats["found"],
                        "possible": base_possible + stats["possible"],
                        "not_found": base_not_found + stats["not_found"],
                        "errors": base_errors + stats["errors"],
                    })
                base_scanned = state._mam_scan_progress["scanned"]
                base_found = state._mam_scan_progress["found"]
                base_possible = state._mam_scan_progress["possible"]
                base_not_found = state._mam_scan_progress["not_found"]
                base_errors = state._mam_scan_progress["errors"]
                batch_limit = min(100, scan_total - state._mam_scan_progress["scanned"])
                if batch_limit <= 0:
                    state._mam_scan_progress.update({"status": "complete", "running": False})
                    logger.info(f"MAM scan reached limit ({scan_total}): {state._mam_scan_progress['scanned']} scanned, {state._mam_scan_progress['found']} found")
                    await db.close()
                    return
                result = await mam_scan_batch(
                    db, session_id=cs["mam_session_id"], limit=batch_limit,
                    delay=cs.get("rate_mam", 2), skip_ip_update=True,
                    format_priority=cs.get("mam_format_priority"),
                    on_progress=_progress,
                    cancel_check=lambda: state._lookup_progress.get("running", False),
                )
                # Progress already updated per-book via on_progress callback
                if result.get("error"):
                    state._mam_scan_progress.update({"status": f"error: {result['error']}", "running": False})
                    return
                remaining = await db.execute_fetchall(
                    "SELECT COUNT(*) FROM books WHERE mam_status IS NULL AND is_unreleased=0 AND hidden=0"
                )
                left = remaining[0][0] if remaining else 0
                state._mam_scan_progress["total"] = state._mam_scan_progress["scanned"] + left
                await db.execute(
                    "INSERT INTO sync_log (sync_type, started_at, finished_at, status, books_found, books_new) VALUES (?,?,?,?,?,?)",
                    ("mam", time.time(), time.time(), "complete",
                     result.get("scanned", 0), result.get("found", 0))
                )
                await db.commit()
            except Exception as e:
                logger.error(f"MAM scan batch error: {e}")
                state._mam_scan_progress.update({"status": f"error: {e}", "running": False})
                return
            finally:
                await db.close()
            if left == 0 or result.get("scanned", 0) == 0 or state._mam_scan_progress["scanned"] >= scan_total:
                state._mam_scan_progress.update({"status": "complete", "running": False})
                logger.info(f"MAM scan complete: {state._mam_scan_progress['scanned']} scanned, {state._mam_scan_progress['found']} found")
                return
            batch_num += 1
            state._mam_scan_progress["status"] = "paused"
            logger.info(f"MAM scan batch {batch_num} done ({state._mam_scan_progress['scanned']}/{state._mam_scan_progress['total']}), pausing 5 min")
            await asyncio.sleep(300)
            # Wait for any author scan to finish before resuming
            if state._lookup_progress.get("running"):
                state._mam_scan_progress["status"] = "waiting (author scan running)"
                logger.info("MAM scan waiting for author scan to finish...")
                while state._lookup_progress.get("running"):
                    await asyncio.sleep(30)
                logger.info("Author scan finished — MAM scan resuming")
            state._mam_scan_progress["status"] = "scanning"

    state._mam_scan_task = asyncio.create_task(_do_scan())
    return {"status": "started", "total": total}


@app.post("/api/mam/scan/cancel")
async def mam_scan_cancel():
    """Cancel the currently running MAM scan."""
    if state._mam_scan_task and not state._mam_scan_task.done():
        state._mam_scan_task.cancel()
        state._mam_scan_progress.update({"running": False, "status": "cancelled"})
        logger.info("MAM scan cancelled by user")
        return {"status": "ok", "message": "MAM scan cancelled"}
    return {"status": "ok", "message": "No MAM scan running"}


@app.get("/api/mam/scan/status")
async def mam_scan_status_endpoint():
    """Get progress of any active MAM scan (manual, scheduled, or full)."""
    if state._mam_scan_progress.get("running"):
        return dict(state._mam_scan_progress)
    if state._mam_full_scan_task and not state._mam_full_scan_task.done():
        db = await get_db()
        try:
            fs = await mam_get_full_scan_status(db)
            if fs.get("active"):
                return {"running": True, "scanned": fs.get("scanned", 0),
                        "total": fs.get("total_books", 0), "found": 0,
                        "possible": 0, "not_found": 0, "errors": 0,
                        "status": "scanning", "type": "full_scan",
                        "progress_pct": fs.get("progress_pct", 0)}
        finally:
            await db.close()
    return dict(state._mam_scan_progress)


@app.post("/api/mam/test-scan")
async def mam_test_scan():
    """Run a quick test scan of 10 books and return results inline."""
    s = load_settings()
    if not s.get("mam_enabled") or not s.get("mam_session_id"):
        return {"error": "MAM not configured or not enabled"}
    if not s.get("mam_scanning_enabled", True):
        return {"error": "MAM scanning is disabled — enable it in Settings"}
    if state._mam_scan_task and not state._mam_scan_task.done():
        return {"error": "A MAM scan is already running — wait for it to finish"}
    db = await get_db()
    try:
        result = await mam_scan_batch(
            db, session_id=s["mam_session_id"], limit=10,
            delay=s.get("rate_mam", 2),
            skip_ip_update=True,
            format_priority=s.get("mam_format_priority"),
            cancel_check=lambda: state._lookup_progress.get("running", False),
        )
        return result
    finally:
        await db.close()


@app.post("/api/mam/full-scan")
async def mam_full_scan_start():
    """Start a full MAM library scan (250 books/batch, 1hr between batches)."""
    s = load_settings()
    if not s.get("mam_enabled") or not s.get("mam_session_id"):
        return {"error": "MAM not configured or not enabled"}
    if not s.get("mam_scanning_enabled", True):
        return {"error": "MAM scanning is disabled — enable it in Settings"}
    if state._mam_full_scan_task and not state._mam_full_scan_task.done():
        return {"error": "A full MAM scan is already running"}
    if state._mam_scan_progress.get("running"):
        return {"error": "A MAM scan is already running — wait for it to finish"}
    if state._lookup_progress.get("running"):
        return {"error": "An author scan is running — MAM scan will wait until it finishes"}

    db = await get_db()
    try:
        start_result = await mam_start_full_scan(db)
        if "error" in start_result:
            return start_result
    finally:
        await db.close()

    async def _full_scan_loop():
        state._mam_scan_progress = {"running": True, "scanned": 0,
                              "total": start_result.get("total_books", 0),
                              "found": 0, "possible": 0, "not_found": 0,
                              "errors": 0, "status": "scanning", "type": "full_scan"}
        while True:
            db = await get_db()
            try:
                cs = load_settings()
                result = await mam_run_full_scan_batch(
                    db, session_id=cs["mam_session_id"],
                    skip_ip_update=True,
                    delay=cs.get("rate_mam", 2),
                    format_priority=cs.get("mam_format_priority"),
                )
                fs = await mam_get_full_scan_status(db)
                state._mam_scan_progress.update({
                    "scanned": fs.get("scanned", 0),
                    "total": fs.get("total_books", state._mam_scan_progress["total"]),
                    "status": "scanning" if result["status"] == "batch_complete" else result["status"],
                })
            finally:
                await db.close()
            if result["status"] in ("scan_complete", "error", "no_scan"):
                state._mam_scan_progress.update({"running": False, "status": result["status"]})
                break
            elif result["status"] == "batch_complete":
                wait = cs.get("mam_full_scan_batch_delay_minutes", 60) * 60
                state._mam_scan_progress["status"] = "paused"
                logger.info(f"Full MAM scan: batch done, waiting {wait//60} min")
                await asyncio.sleep(wait)
                # Wait for any author scan to finish before resuming
                if state._lookup_progress.get("running"):
                    state._mam_scan_progress["status"] = "waiting (author scan running)"
                    logger.info("Full MAM scan waiting for author scan to finish...")
                    while state._lookup_progress.get("running"):
                        await asyncio.sleep(30)
                    logger.info("Author scan finished — full MAM scan resuming")
                state._mam_scan_progress["status"] = "scanning"

    state._mam_full_scan_task = asyncio.create_task(_full_scan_loop())
    return {"status": "started", "scan_id": start_result["id"],
            "total_books": start_result["total_books"]}


@app.get("/api/mam/full-scan/status")
async def mam_full_scan_status():
    """Get progress of the current/most recent full MAM scan."""
    db = await get_db()
    try:
        return await mam_get_full_scan_status(db)
    finally:
        await db.close()


@app.post("/api/mam/full-scan/cancel")
async def mam_full_scan_cancel():
    """Cancel a running full MAM scan."""
    db = await get_db()
    try:
        result = await mam_cancel_full_scan(db)
    finally:
        await db.close()
    if state._mam_full_scan_task and not state._mam_full_scan_task.done():
        state._mam_full_scan_task.cancel()
    return result


@app.post("/api/scanning/author/toggle")
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


@app.post("/api/scanning/mam/toggle")
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


@app.post("/api/mam/toggle")
async def mam_toggle():
    """Toggle MAM features on/off (only works if session ID exists)."""
    s = load_settings()
    if not s.get("mam_session_id"):
        return {"error": "No MAM session ID configured"}
    s["mam_enabled"] = not s.get("mam_enabled", False)
    save_settings(s)
    return {"enabled": s["mam_enabled"]}


@app.get("/api/mam/books")
async def mam_books_endpoint(section: str = "upload", search: str = "",
                              sort: str = "title", page: int = 1, per_page: int = 50):
    """Get books for the MAM page, filtered by section."""
    db = await get_db()
    try:
        if section == "upload":
            where = "b.owned=1 AND b.mam_status='not_found' AND b.hidden=0"
        elif section == "download":
            where = "b.owned=0 AND b.mam_status IN ('found','possible') AND b.is_unreleased=0 AND b.hidden=0"
        elif section == "missing_everywhere":
            where = "b.owned=0 AND b.mam_status='not_found' AND b.is_unreleased=0 AND b.hidden=0"
        else:
            return {"error": f"Unknown section: {section}"}

        params = []
        if search:
            where += " AND (b.title LIKE ? OR a.name LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])

        sort_map = {"title": "b.title ASC", "author": "a.name ASC",
                    "date": "b.pub_date DESC", "series": "s.name ASC, b.series_index ASC"}
        order = sort_map.get(sort, "b.title ASC")

        count_sql = f"SELECT COUNT(*) FROM books b JOIN authors a ON b.author_id=a.id LEFT JOIN series s ON b.series_id=s.id WHERE {where}"
        count_row = await db.execute_fetchall(count_sql, params)
        total = count_row[0][0] if count_row else 0

        offset = (page - 1) * per_page
        data_sql = f"""SELECT b.*, a.name as author_name, s.name as series_name,
            (SELECT COUNT(*) FROM books b2 WHERE b2.series_id=b.series_id AND b2.hidden=0) as series_total
            FROM books b JOIN authors a ON b.author_id=a.id
            LEFT JOIN series s ON b.series_id=s.id
            WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?"""
        rows = await db.execute_fetchall(data_sql, params + [per_page, offset])
        books = [dict(r) for r in rows]

        return {"books": books, "total": total, "page": page, "per_page": per_page,
                "total_pages": (total + per_page - 1) // per_page}
    finally:
        await db.close()


@app.post("/api/mam/reset")
async def mam_reset_scans():
    """Reset all MAM scan data — clears all mam_* fields on all books."""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE books SET mam_url=NULL, mam_status=NULL, mam_formats=NULL, "
            "mam_torrent_id=NULL, mam_has_multiple=0"
        )
        await db.execute("DELETE FROM mam_scan_log")
        await db.commit()
        return {"status": "ok", "message": "All MAM scan data cleared"}
    finally:
        await db.close()

# ─── Database Browser ────────────────────────────────────────
@app.get("/api/db/tables")
async def db_list_tables():
    """List all browsable tables in the active library database."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = [r[0] for r in rows if r[0] in DB_TABLES]
        return {"tables": sorted(tables)}
    finally:
        await db.close()


@app.get("/api/db/table/{table_name}/schema")
async def db_table_schema(table_name: str):
    """Get column definitions for a table using PRAGMA table_info."""
    if table_name not in DB_TABLES:
        raise HTTPException(400, f"Table '{table_name}' is not accessible. Allowed: {sorted(DB_TABLES)}")
    db = await get_db()
    try:
        cols = await db.execute_fetchall(f"PRAGMA table_info({table_name})")
        count_row = await (await db.execute(f"SELECT COUNT(*) FROM [{table_name}]")).fetchone()
        row_count = count_row[0] if count_row else 0
        return {
            "table": table_name,
            "columns": [
                {
                    "name": c[1],
                    "type": c[2] or "TEXT",
                    "notnull": bool(c[3]),
                    "default": c[4],
                    "pk": bool(c[5]),
                }
                for c in cols
            ],
            "row_count": row_count,
        }
    finally:
        await db.close()


@app.get("/api/db/table/{table_name}")
async def db_table_rows(
    table_name: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    sort: str = Query("id"),
    sort_dir: str = Query("asc"),
    search: str = Query(""),
):
    """Get paginated rows from a table with optional sorting and search."""
    if table_name not in DB_TABLES:
        raise HTTPException(400, f"Table '{table_name}' is not accessible. Allowed: {sorted(DB_TABLES)}")
    db = await get_db()
    try:
        # Get column info for search and sort validation
        cols = await db.execute_fetchall(f"PRAGMA table_info({table_name})")
        col_names = [c[1] for c in cols]
        col_types = {c[1]: (c[2] or "TEXT").upper() for c in cols}

        # Validate sort column
        sort_col = sort if sort in col_names else "id" if "id" in col_names else col_names[0]
        direction = "DESC" if sort_dir.lower() == "desc" else "ASC"

        # Build search filter (search across all TEXT-like columns)
        where = "1=1"
        params = []
        if search.strip():
            text_cols = [c for c in col_names if col_types[c] in ("TEXT", "")]
            if text_cols:
                clauses = [f"[{c}] LIKE ?" for c in text_cols]
                where = f"({' OR '.join(clauses)})"
                params = [f"%{search.strip()}%"] * len(text_cols)

        # Count total matching rows
        count_row = await (await db.execute(
            f"SELECT COUNT(*) FROM [{table_name}] WHERE {where}", params
        )).fetchone()
        total = count_row[0] if count_row else 0

        # Fetch page
        offset = (page - 1) * per_page
        rows = await db.execute_fetchall(
            f"SELECT * FROM [{table_name}] WHERE {where} ORDER BY [{sort_col}] {direction} LIMIT ? OFFSET ?",
            params + [per_page, offset]
        )

        # Convert rows to dicts
        row_dicts = []
        for row in rows:
            d = {}
            for i, col in enumerate(col_names):
                d[col] = row[i]
            row_dicts.append(d)

        return {
            "rows": row_dicts,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
        }
    finally:
        await db.close()



async def _resolve_fk_value(db, table_name, col_name, value, row_context=None):
    """Resolve a FK value that might be a name string instead of an integer ID.

    Returns (resolved_int, error_string_or_None).
    - If value is already a valid int → return it directly
    - If value is a string → look up by name in the referenced table
    - If not found → create a new entry and return the new ID
    """
    # Already an integer?
    try:
        return int(value), None
    except (ValueError, TypeError):
        pass

    # Not a number — try name resolution
    resolvers = DB_FK_RESOLVERS.get(table_name, {})
    resolver = resolvers.get(col_name)
    if not resolver:
        return None, f"Expected INTEGER for '{col_name}', got '{value}'"

    ref_table = resolver["table"]
    name_col = resolver["name_col"]
    name_str = str(value).strip()
    if not name_str:
        return None, None  # Empty → NULL

    # Look up by exact name (case-insensitive)
    row = await (await db.execute(
        f"SELECT id FROM [{ref_table}] WHERE LOWER([{name_col}]) = LOWER(?)", (name_str,)
    )).fetchone()

    if row:
        logger.info(f"DB editor: resolved '{name_str}' → {ref_table}.id={row[0]}")
        return row[0], None

    # Not found — create a new entry
    create_cols = resolver.get("create_cols", {})
    insert_cols = [f"[{name_col}]"]
    insert_vals = [name_str]
    for extra_col, gen_fn in create_cols.items():
        insert_cols.append(f"[{extra_col}]")
        insert_vals.append(gen_fn(name_str) if callable(gen_fn) else gen_fn)

    # For series, we need an author_id — get it from the row being edited
    if ref_table == "series" and row_context:
        author_id = row_context.get("author_id")
        if author_id:
            insert_cols.append("[author_id]")
            insert_vals.append(int(author_id))
        else:
            return None, f"Cannot create new series '{name_str}' without an author_id in the same row"

    placeholders = ",".join(["?"] * len(insert_cols))
    try:
        cursor = await db.execute(
            f"INSERT INTO [{ref_table}] ({','.join(insert_cols)}) VALUES ({placeholders})",
            insert_vals
        )
        new_id = cursor.lastrowid
        logger.info(f"DB editor: created new {ref_table} entry '{name_str}' → id={new_id}")
        return new_id, None
    except Exception as e:
        return None, f"Failed to create {ref_table} entry '{name_str}': {e}"


@app.post("/api/db/table/{table_name}/update")
async def db_table_update(table_name: str, body: dict = Body(...)):
    """Batch update cells in a table. All changes applied in a single transaction.

    Body: {"edits": {"row_id": {"col": value, ...}, ...}}
    Validates types against PRAGMA table_info before applying.
    """
    if table_name not in DB_TABLES:
        raise HTTPException(400, f"Table '{table_name}' is not accessible")
    edits = body.get("edits", {})
    if not edits:
        return {"status": "ok", "updated": 0}

    db = await get_db()
    try:
        # Get column metadata for validation
        cols = await db.execute_fetchall(f"PRAGMA table_info({table_name})")
        col_meta = {}
        pk_col = None
        for c in cols:
            col_meta[c[1]] = {
                "type": (c[2] or "TEXT").upper(),
                "notnull": bool(c[3]),
                "pk": bool(c[5]),
            }
            if c[5]:
                pk_col = c[1]

        # Validate all edits first
        errors = []
        for row_id, changes in edits.items():
            for col, val in changes.items():
                if col not in col_meta:
                    errors.append({"row": row_id, "column": col, "value": str(val), "error": f"Unknown column '{col}'"})
                    continue
                meta = col_meta[col]
                if meta["pk"]:
                    errors.append({"row": row_id, "column": col, "value": str(val), "error": "Cannot edit primary key"})
                    continue
                # Null check
                if (val is None or val == "") and meta["notnull"]:
                    errors.append({"row": row_id, "column": col, "value": str(val), "error": f"Column '{col}' cannot be NULL"})
                    continue
                # Type check (only if not null/empty)
                if val is not None and val != "":
                    col_type = meta["type"]
                    if "INTEGER" in col_type:
                        # Check if this is a FK column that supports name resolution
                        fk_resolvers = DB_FK_RESOLVERS.get(table_name, {})
                        if col in fk_resolvers:
                            # Will resolve during apply phase — skip strict int check
                            try:
                                int(val)
                            except (ValueError, TypeError):
                                pass  # Non-integer is OK for FK columns — will resolve by name
                        else:
                            try:
                                int(val)
                            except (ValueError, TypeError):
                                errors.append({"row": row_id, "column": col, "value": str(val), "error": f"Expected INTEGER, got '{val}'"})
                    elif "REAL" in col_type:
                        try:
                            float(val)
                        except (ValueError, TypeError):
                            errors.append({"row": row_id, "column": col, "value": str(val), "error": f"Expected REAL number, got '{val}'"})

        if errors:
            return {"status": "error", "errors": errors}

        # Apply all edits in a transaction (with FK resolution)
        updated = 0
        for row_id, changes in edits.items():
            set_parts = []
            params = []
            # Build row context for FK resolution (e.g., series needs author_id)
            row_context = dict(changes)
            # Also fetch current row values for context
            pk = pk_col or "id"
            try:
                existing = await (await db.execute(
                    f"SELECT * FROM [{table_name}] WHERE [{pk}] = ?", (int(row_id),)
                )).fetchone()
                if existing:
                    col_names_list = [c[1] for c in cols]
                    for i, cn in enumerate(col_names_list):
                        if cn not in row_context:
                            row_context[cn] = existing[i]
            except Exception:
                pass

            for col, val in changes.items():
                if col_meta[col]["pk"]:
                    continue
                set_parts.append(f"[{col}] = ?")
                # Convert types
                if val is None or val == "":
                    params.append(None)
                elif "INTEGER" in col_meta[col]["type"]:
                    # Try FK resolution for supported columns
                    fk_resolvers = DB_FK_RESOLVERS.get(table_name, {})
                    if col in fk_resolvers:
                        resolved, err = await _resolve_fk_value(db, table_name, col, val, row_context)
                        if err:
                            errors.append({"row": row_id, "column": col, "value": str(val), "error": err})
                            continue
                        params.append(resolved)
                    else:
                        params.append(int(val))
                elif "REAL" in col_meta[col]["type"]:
                    params.append(float(val))
                else:
                    params.append(str(val))
            if set_parts:
                pk = pk_col or "id"
                params.append(int(row_id))
                await db.execute(
                    f"UPDATE [{table_name}] SET {', '.join(set_parts)} WHERE [{pk}] = ?",
                    params
                )
                updated += 1
        if errors:
            return {"status": "error", "errors": errors}
        await db.commit()
        logger.info(f"DB editor: updated {updated} rows in {table_name}")
        return {"status": "ok", "updated": updated}
    except Exception as e:
        logger.error(f"DB editor update error: {e}")
        raise HTTPException(500, str(e))
    finally:
        await db.close()


@app.post("/api/db/table/{table_name}/add")
async def db_table_add_row(table_name: str, body: dict = Body(...)):
    """Add a new row to a table.

    Body: {"values": {"col": value, ...}}
    Only includes columns with non-empty values.
    """
    if table_name not in DB_TABLES:
        raise HTTPException(400, f"Table '{table_name}' is not accessible")
    values = body.get("values", {})
    if not values:
        raise HTTPException(400, "No values provided")

    db = await get_db()
    try:
        # Get column metadata
        cols = await db.execute_fetchall(f"PRAGMA table_info({table_name})")
        col_meta = {c[1]: {"type": (c[2] or "TEXT").upper(), "notnull": bool(c[3]), "pk": bool(c[5])} for c in cols}

        # Filter to valid columns, skip PK (auto-increment)
        insert_cols = []
        insert_vals = []
        for col, val in values.items():
            if col not in col_meta or col_meta[col]["pk"]:
                continue
            if val is None or val == "":
                if col_meta[col]["notnull"]:
                    raise HTTPException(400, f"Column '{col}' cannot be NULL")
                insert_cols.append(f"[{col}]")
                insert_vals.append(None)
            else:
                col_type = col_meta[col]["type"]
                try:
                    if "INTEGER" in col_type:
                        fk_resolvers = DB_FK_RESOLVERS.get(table_name, {})
                        if col in fk_resolvers:
                            resolved, err = await _resolve_fk_value(db, table_name, col, val, values)
                            if err:
                                raise HTTPException(400, f"FK resolution error for {col}: {err}")
                            insert_vals.append(resolved)
                        else:
                            insert_vals.append(int(val))
                    elif "REAL" in col_type:
                        insert_vals.append(float(val))
                    else:
                        insert_vals.append(str(val))
                    insert_cols.append(f"[{col}]")
                except HTTPException:
                    raise
                except (ValueError, TypeError) as e:
                    raise HTTPException(400, f"Invalid value for {col} ({col_type}): {val}")

        if not insert_cols:
            raise HTTPException(400, "No valid columns to insert")

        placeholders = ",".join(["?"] * len(insert_cols))
        cursor = await db.execute(
            f"INSERT INTO [{table_name}] ({','.join(insert_cols)}) VALUES ({placeholders})",
            insert_vals
        )
        await db.commit()
        new_id = cursor.lastrowid
        logger.info(f"DB editor: added row {new_id} to {table_name}")
        return {"status": "ok", "id": new_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"DB editor add error: {e}")
        raise HTTPException(500, str(e))
    finally:
        await db.close()


@app.delete("/api/db/table/{table_name}/row/{row_id}")
async def db_table_delete_row(table_name: str, row_id: int):
    """Delete a row by primary key."""
    if table_name not in DB_TABLES:
        raise HTTPException(400, f"Table '{table_name}' is not accessible")
    db = await get_db()
    try:
        # Find PK column
        cols = await db.execute_fetchall(f"PRAGMA table_info({table_name})")
        pk_col = next((c[1] for c in cols if c[5]), "id")

        # Verify row exists
        row = await (await db.execute(
            f"SELECT [{pk_col}] FROM [{table_name}] WHERE [{pk_col}] = ?", (row_id,)
        )).fetchone()
        if not row:
            raise HTTPException(404, f"Row {row_id} not found in {table_name}")

        await db.execute(f"DELETE FROM [{table_name}] WHERE [{pk_col}] = ?", (row_id,))
        await db.commit()
        logger.info(f"DB editor: deleted row {row_id} from {table_name}")
        return {"status": "ok"}
    finally:
        await db.close()


# ─── Frontend ────────────────────────────────────────────────
# Support both source tree and PyInstaller bundle layouts.
# PyInstaller sets sys._MEIPASS to its temp extraction directory.
import sys as _sys
_pyinstaller_base = getattr(_sys, '_MEIPASS', None)
if _pyinstaller_base:
    FD = Path(_pyinstaller_base) / "frontend" / "dist"
else:
    FD = Path(__file__).parent.parent / "frontend" / "dist"

if FD.exists():
    if (FD / "assets").exists(): app.mount("/assets", StaticFiles(directory=FD / "assets"), name="assets")
    @app.get("/{path:path}")
    async def serve_fe(path: str):
        fp = FD / path
        return FileResponse(fp if fp.is_file() else FD / "index.html")
else:
    from app.runtime import IS_STANDALONE
    if IS_STANDALONE:
        @app.get("/{path:path}")
        async def serve_fe_missing(path: str):
            return {"error": "Frontend not built. Run 'cd frontend && npm install && npm run build' first."}
