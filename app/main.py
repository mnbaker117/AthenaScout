"""AthenaScout — Main FastAPI Application"""
import logging, time, json, asyncio, httpx
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Query, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.config import (SYNC_INTERVAL_MINUTES, load_settings, save_settings, LANGUAGE_OPTIONS, apply_logging, discover_libraries, get_extra_mount_paths)
from app.library_apps import get_app
from app.database import init_db, get_db, set_active_library, get_active_library, migrate_legacy_db, match_legacy_db_to_library


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
from app.lookup import run_full_lookup, run_full_rescan, lookup_author, reload_sources
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
HF = "b.hidden = 0"
DB_TABLES = {"books", "authors", "series", "sync_log", "mam_scan_log"}
_discovered_libraries = []
_last_calibre_check = {"at": None, "synced": False}
_mam_scan_task: asyncio.Task | None = None
_mam_full_scan_task: asyncio.Task | None = None
_mam_scan_progress: dict = {"running": False, "scanned": 0, "total": 0, "found": 0, "possible": 0, "not_found": 0, "errors": 0, "status": "idle", "type": "none"}
_lookup_task: asyncio.Task | None = None
_lookup_progress: dict = {"running": False, "checked": 0, "total": 0, "current_author": "", "new_books": 0, "status": "idle", "type": "none"}

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _discovered_libraries
    import os as _os

    s = load_settings()
    apply_logging(s.get("verbose_logging", False))
    reload_sources()

    # ─── Library Discovery ────────────────────────────────
    _discovered_libraries = discover_libraries(s)
    if not _discovered_libraries:
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
        for l in _discovered_libraries:
            at = l.get("display_name", "Unknown")
            by_app.setdefault(at, []).append(l["name"])
        lib_summary = "; ".join(f'{len(v)} {k} ({", ".join(v)})' for k, v in by_app.items())
        logger.info(f"Discovered {len(_discovered_libraries)} libraries: {lib_summary}")

        # Migration: rename legacy athenascout.db → best-matching library's DB file
        first_slug = _discovered_libraries[0]["slug"]
        migration_slug = match_legacy_db_to_library(_discovered_libraries)
        migrated_to = migrate_legacy_db(migration_slug)
        if migrated_to:
            logger.info(f"Legacy database migrated to library '{migrated_to}'")
            first_slug = migrated_to  # use migrated library as default active

        # Initialize all library databases
        for lib in _discovered_libraries:
            await init_db(lib["slug"])
            logger.debug(f"Initialized database for library '{lib['name']}'")

        # Set active library (from settings or first discovered)
        active = s.get("active_library") or first_slug
        valid_slugs = [l["slug"] for l in _discovered_libraries]
        if active not in valid_slugs:
            active = first_slug
        set_active_library(active)
        s["active_library"] = active
        save_settings(s)
        logger.info(f"Active library: '{active}'")

        # Sync each library (with mtime optimization)
        mtimes = s.get("calibre_mtimes", {})
        for lib in _discovered_libraries:
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
        _last_calibre_check["at"] = time.time()
        _last_calibre_check["synced"] = True

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
        for lib in _discovered_libraries:
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
        _last_calibre_check["at"] = time.time()
        _last_calibre_check["synced"] = any_synced

    if sync_min and sync_min > 0:
        if _discovered_libraries:
            scheduler.add_job(_sync_all_libraries, "interval", minutes=sync_min, id="calibre_sync", replace_existing=True)
        else:
            logger.info("Calibre auto-sync skipped - no libraries configured")
    else:
        logger.info("Calibre auto-sync disabled (interval = 0)")
    async def _scheduled_lookup():
        global _lookup_progress
        s = load_settings()
        if not s.get("author_scanning_enabled", True):
            return
        if _lookup_progress.get("running"):
            return
        _lookup_progress = {"running": True, "checked": 0, "total": 0, "current_author": "",
                            "new_books": 0, "status": "scanning", "type": "scheduled_lookup"}
        def _progress(data):
            _lookup_progress.update({"checked": data["checked"], "total": data["total"],
                                     "current_author": data["current_author"], "new_books": data["new_books"]})
        try:
            await run_full_lookup(on_progress=_progress)
            _lookup_progress.update({"running": False, "status": "complete"})
        except Exception as e:
            logger.error(f"Scheduled lookup error: {e}")
            _lookup_progress.update({"running": False, "status": f"error: {e}"})

    if lookup_days and lookup_days > 0:
        scheduler.add_job(_scheduled_lookup, "interval", minutes=lookup_days*1440, id="author_lookup", replace_existing=True)
    else:
        logger.info("Auto-lookup disabled (interval = 0)")
    async def _mam_scheduler():
        global _mam_scan_progress
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
            if _mam_scan_progress.get("running"):
                continue
            if _lookup_progress.get("running"):
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
            _mam_scan_progress = {"running": True, "scanned": 0, "total": scan_limit,
                                  "found": 0, "possible": 0, "not_found": 0,
                                  "errors": 0, "status": "scanning", "type": "scheduled",
                                  "remaining": total_remaining}
            def _sched_progress(stats):
                _mam_scan_progress.update({
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
                    cancel_check=lambda: _lookup_progress.get("running", False),
                )
                _mam_scan_progress.update({
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
                _mam_scan_progress.update({"running": False, "status": f"error: {e}"})
            finally:
                await db.close()
            last_scan_at = time.time()

    asyncio.create_task(_mam_scheduler())
    scheduler.start(); yield; scheduler.shutdown()

app = FastAPI(title="AthenaScout", lifespan=lifespan)

# ─── Settings ────────────────────────────────────────────────
@app.get("/api/settings")
async def get_settings():
    s = load_settings()
    d = dict(s)
    if d.get("hardcover_api_key"): d["hardcover_api_key_set"] = True; d["hardcover_api_key"] = d["hardcover_api_key"][:8] + "..."
    else: d["hardcover_api_key_set"] = False
    if d.get("mam_session_id"):
        sid = d["mam_session_id"]
        d["mam_session_id"] = sid[:8] + "..." + sid[-4:] if len(sid) > 12 else "***"
    d["language_options"] = LANGUAGE_OPTIONS
    d["_extra_mount_paths"] = get_extra_mount_paths()
    d["_discovered_libraries"] = [
        {"name": l["name"], "slug": l["slug"],
         "app_type": l.get("app_type", "calibre"),
         "content_type": l.get("content_type", "ebook"),
         "source_db_path": l["source_db_path"],
         "active": l["slug"] == get_active_library()}
        for l in _discovered_libraries
    ]
    return d

@app.post("/api/settings")
async def update_settings(body: dict = Body(...)):
    cur = load_settings()
    for k, v in body.items():
        if k not in cur:
            continue
        # Don't overwrite real API key with masked/truncated value
        if k == "hardcover_api_key" and isinstance(v, str) and (v.endswith("...") or v == ""):
            continue
        if k == "mam_session_id" and isinstance(v, str) and ("..." in v or v == "***"):
            continue
        cur[k] = v
    save_settings(cur); reload_sources()
    apply_logging(cur.get("verbose_logging", False))
    return {"status": "ok"}

@app.post("/api/settings/reset")
async def reset_settings():
    """Reset all settings to factory defaults."""
    from app.config import DEFAULT_SETTINGS
    fresh = dict(DEFAULT_SETTINGS)
    save_settings(fresh)
    reload_sources()
    apply_logging(False)
    logger.info("All settings reset to defaults")
    return {"status": "ok"}

# ─── Libraries ───────────────────────────────────────────────
@app.get("/api/libraries")
async def list_libraries():
    """Return all discovered Calibre libraries with active flag."""
    active = get_active_library()
    return {
        "libraries": [
            {
                "name": lib["name"],
                "slug": lib["slug"],
                "app_type": lib.get("app_type", "calibre"),
                "content_type": lib.get("content_type", "ebook"),
                "display_name": lib.get("display_name", "Calibre"),
                "source_db_path": lib["source_db_path"],
                "library_path": lib["library_path"],
                "active": lib["slug"] == active,
            }
            for lib in _discovered_libraries
        ]
    }

@app.post("/api/libraries/active")
async def switch_library(body: dict = Body(...)):
    """Switch the active library. Cancels any running scans first."""
    global _lookup_task, _lookup_progress, _mam_scan_task, _mam_scan_progress, _mam_full_scan_task
    slug = body.get("slug", "")
    valid_slugs = [l["slug"] for l in _discovered_libraries]
    if slug not in valid_slugs:
        raise HTTPException(400, f"Unknown library slug: {slug}. Valid: {valid_slugs}")

    old_slug = get_active_library()
    if slug == old_slug:
        return {"status": "ok", "active": slug, "message": "Already active"}

    # ── Cancel all running scans before switching ──
    cancelled = []

    # Cancel author lookup
    if _lookup_task and not _lookup_task.done():
        _lookup_task.cancel()
        _lookup_progress.update({"running": False, "status": "cancelled (library switch)"})
        cancelled.append("author scan")

    # Cancel MAM scan (manual or scheduled)
    if _mam_scan_task and not _mam_scan_task.done():
        _mam_scan_task.cancel()
        _mam_scan_progress.update({"running": False, "status": "cancelled (library switch)"})
        cancelled.append("MAM scan")

    # Cancel MAM full scan
    if _mam_full_scan_task and not _mam_full_scan_task.done():
        _mam_full_scan_task.cancel()
        try:
            db = await get_db()
            try:
                await mam_cancel_full_scan(db)
            finally:
                await db.close()
        except Exception:
            pass
        cancelled.append("MAM full scan")

    if cancelled:
        logger.info(f"Cancelled running scans due to library switch ({old_slug} → {slug}): {', '.join(cancelled)}")

    # ── Switch the active library ──
    set_active_library(slug)
    s = load_settings()
    s["active_library"] = slug
    save_settings(s)
    logger.info(f"Switched active library to '{slug}'")
    return {"status": "ok", "active": slug, "cancelled": cancelled}

@app.post("/api/libraries/validate-path")
async def validate_library_path(body: dict = Body(...)):
    """Validate a filesystem path for use as a library source.

    Supports any registered library app type — uses the app's db_filename
    to look for the correct database file (e.g., metadata.db for Calibre).
    """
    import os as _os
    path = body.get("path", "").strip()
    path_type = body.get("type", "root")
    app_type = body.get("app_type", "calibre")

    if not path:
        return {"valid": False, "error": "No path provided"}
    if not _os.path.exists(path):
        return {"valid": False, "error": f"Path does not exist: {path}"}

    # Get the database filename for this app type
    app_instance = get_app(app_type)
    db_filename = app_instance.db_filename if app_instance else "metadata.db"

    found = []
    if path_type == "root":
        root = Path(path)
        for child in sorted(root.iterdir()):
            if child.is_dir():
                db_file = child / db_filename
                if db_file.exists():
                    found.append({"name": child.name, "path": str(db_file)})
        root_db = root / db_filename
        if root_db.exists():
            found.append({"name": root.name, "path": str(root_db)})
        if not found:
            return {"valid": False, "error": f"No {db_filename} files found in subdirectories"}
        return {"valid": True, "libraries_found": len(found), "details": found}

    elif path_type == "direct":
        p = Path(path)
        if p.name == db_filename and p.exists():
            return {"valid": True, "libraries_found": 1, "details": [{"name": p.parent.name, "path": str(p)}]}
        elif (p / db_filename).exists():
            return {"valid": True, "libraries_found": 1, "details": [{"name": p.name, "path": str(p / db_filename)}]}
        else:
            return {"valid": False, "error": f"No {db_filename} found at this path"}
    else:
        return {"valid": False, "error": f"Unknown type: {path_type}"}


@app.post("/api/libraries/rescan")
async def rescan_libraries():
    """Re-run library discovery from current settings. Initializes new databases."""
    global _discovered_libraries
    s = load_settings()
    new_libs = discover_libraries(s)
    if not new_libs:
        return {"status": "error", "error": "No libraries found after rescan"}

    # Initialize any new library databases
    existing_slugs = {l["slug"] for l in _discovered_libraries}
    for lib in new_libs:
        if lib["slug"] not in existing_slugs:
            await init_db(lib["slug"])
            logger.info(f"Initialized new library database: {lib['slug']}")

    _discovered_libraries = new_libs
    lib_names = [f'"{l["name"]}" ({l["slug"]})' for l in new_libs]
    logger.info(f"Library rescan complete: {len(new_libs)} libraries found: {', '.join(lib_names)}")

    # Ensure active library is still valid
    active = get_active_library()
    valid_slugs = [l["slug"] for l in new_libs]
    if active not in valid_slugs:
        new_active = new_libs[0]["slug"]
        set_active_library(new_active)
        s["active_library"] = new_active
        save_settings(s)
        logger.info(f"Active library reset to '{new_active}' after rescan")

    return {
        "status": "ok",
        "libraries": [
            {"name": l["name"], "slug": l["slug"],
             "source_db_path": l["source_db_path"],
             "library_path": l["library_path"],
             "active": l["slug"] == get_active_library()}
            for l in new_libs
        ]
    }


# ─── Health & Stats ──────────────────────────────────────────
@app.get("/api/health")
async def health(): return {"status": "ok", "time": time.time()}

@app.get("/api/platform")
async def platform_info():
    """Return platform/runtime info for the frontend.

    Used by the setup wizard to detect first-run state, suggest
    library paths, and adapt UI to the runtime environment.
    """
    from app.runtime import get_platform_info
    info = get_platform_info()
    s = load_settings()
    # First run: no libraries discovered AND no user-configured sources AND setup not completed
    info["first_run"] = (
        not _discovered_libraries
        and not s.get("library_sources")
        and not s.get("setup_complete")
    )
    # Check which suggested default paths actually exist on this system
    info["existing_default_paths"] = [
        p for p in info["default_library_paths"]
        if Path(p["path"]).exists()
    ]
    return info

@app.get("/api/stats")
async def get_stats():
    db = await get_db()
    try:
        g = lambda sql: db.execute(sql)
        authors = (await (await g("SELECT COUNT(*) c FROM authors")).fetchone())["c"]
        total = (await (await g(f"SELECT COUNT(*) c FROM books b WHERE {HF}")).fetchone())["c"]
        owned = (await (await g(f"SELECT COUNT(*) c FROM books b WHERE owned=1 AND {HF}")).fetchone())["c"]
        missing = (await (await g(f"SELECT COUNT(*) c FROM books b WHERE owned=0 AND {HF}")).fetchone())["c"]
        new = (await (await g(f"SELECT COUNT(*) c FROM books b WHERE is_new=1 AND owned=0 AND {HF}")).fetchone())["c"]
        upcoming = (await (await g(f"SELECT COUNT(*) c FROM books b WHERE is_unreleased=1 AND owned=0 AND {HF}")).fetchone())["c"]
        series = (await (await g("SELECT COUNT(*) c FROM series")).fetchone())["c"]
        hidden = (await (await g("SELECT COUNT(*) c FROM books WHERE hidden=1")).fetchone())["c"]
        ls = await (await g("SELECT * FROM sync_log WHERE sync_type='calibre' ORDER BY started_at DESC LIMIT 1")).fetchone()
        ll = await (await g("SELECT * FROM sync_log WHERE sync_type='lookup' ORDER BY started_at DESC LIMIT 1")).fetchone()
        s = load_settings()
        mam_stats = None
        if s.get("mam_enabled") and s.get("mam_session_id"):
            mam_stats = await get_mam_stats(db)
        active_lib = get_active_library()
        lib_info = next((l for l in _discovered_libraries if l["slug"] == active_lib), None)
        return {"authors": authors, "total_books": total, "owned_books": owned, "missing_books": missing, "new_books": new, "upcoming_books": upcoming, "total_series": series, "hidden_books": hidden, "last_calibre_sync": dict(ls) if ls else None, "last_lookup": dict(ll) if ll else None, "calibre_web_url": s.get("calibre_web_url", ""), "calibre_url": s.get("calibre_url", ""), "mam": mam_stats, "mam_enabled": s.get("mam_enabled", False), "mam_scanning_enabled": s.get("mam_scanning_enabled", True), "author_scanning_enabled": s.get("author_scanning_enabled", True), "active_library": active_lib, "active_library_name": lib_info["name"] if lib_info else active_lib, "library_count": len(_discovered_libraries), "active_content_type": lib_info.get("content_type", "ebook") if lib_info else "ebook", "active_app_type": lib_info.get("app_type", "calibre") if lib_info else "calibre", "last_calibre_check": _last_calibre_check}
    finally: await db.close()

# ─── Authors ─────────────────────────────────────────────────
@app.get("/api/authors")
async def get_authors(search: str = Query(None), sort: str = Query("name"), sort_dir: str = Query("asc"), has_missing: bool = Query(None), book_type: str = Query(None)):
    db = await get_db()
    try:
        q = f"SELECT a.*, COUNT(DISTINCT CASE WHEN {HF} THEN b.id END) as total_books, SUM(CASE WHEN b.owned=1 AND {HF} THEN 1 ELSE 0 END) as owned_count, SUM(CASE WHEN b.owned=0 AND {HF} THEN 1 ELSE 0 END) as missing_count, SUM(CASE WHEN b.is_new=1 AND b.owned=0 AND {HF} THEN 1 ELSE 0 END) as new_count, COUNT(DISTINCT b.series_id) as series_count FROM authors a LEFT JOIN books b ON a.id=b.author_id"
        p = []; c = []
        if search: c.append("a.name LIKE ?"); p.append(f"%{search}%")
        if book_type == "series": c.append("b.series_id IS NOT NULL")
        elif book_type == "standalone": c.append("b.series_id IS NULL")
        if c: q += " WHERE " + " AND ".join(c)
        q += " GROUP BY a.id"
        if has_missing: q += " HAVING missing_count > 0"
        d = "DESC" if sort_dir == "desc" else "ASC"
        q += {"missing": f" ORDER BY missing_count {d}, a.sort_name ASC", "new": f" ORDER BY new_count {d}, a.sort_name ASC", "total": f" ORDER BY total_books {d}, a.sort_name ASC"}.get(sort, f" ORDER BY a.sort_name {d}")
        return {"authors": [dict(r) for r in await (await db.execute(q, p)).fetchall()]}
    finally: await db.close()

@app.get("/api/authors/{aid}")
async def get_author(aid: int):
    db = await get_db()
    try:
        r = await (await db.execute("SELECT * FROM authors WHERE id=?", (aid,))).fetchone()
        if not r: raise HTTPException(404)
        a = dict(r)
        # Find series through books (supports multi-author series)
        a["series"] = [dict(s) for s in await (await db.execute(
            f"""SELECT s.*,
                COUNT(DISTINCT CASE WHEN {HF} THEN b.id END) as book_count,
                COUNT(DISTINCT CASE WHEN b.author_id=? AND {HF} THEN b.id END) as author_book_count,
                SUM(CASE WHEN b.owned=1 AND b.author_id=? AND {HF} THEN 1 ELSE 0 END) as owned_count,
                SUM(CASE WHEN b.owned=0 AND b.author_id=? AND {HF} THEN 1 ELSE 0 END) as missing_count,
                CASE WHEN COUNT(DISTINCT b.author_id) > 1 THEN 1 ELSE 0 END as multi_author
            FROM series s
            JOIN books b ON s.id=b.series_id
            WHERE s.id IN (SELECT DISTINCT series_id FROM books WHERE author_id=? AND series_id IS NOT NULL)
            GROUP BY s.id ORDER BY s.name""",
            (aid, aid, aid, aid)
        )).fetchall()]
        a["standalone_books"] = [dict(b) for b in await (await db.execute(f"SELECT b.*, a2.name as author_name FROM books b JOIN authors a2 ON b.author_id=a2.id WHERE b.author_id=? AND b.series_id IS NULL AND {HF} ORDER BY b.pub_date ASC, b.title ASC", (aid,))).fetchall()]
        return a
    finally: await db.close()

# ─── Series ──────────────────────────────────────────────────
@app.get("/api/series/{sid}")
async def get_series(sid: int):
    db = await get_db()
    try:
        r = await (await db.execute("SELECT s.*, a.name as author_name FROM series s LEFT JOIN authors a ON s.author_id=a.id WHERE s.id=?", (sid,))).fetchone()
        if not r: raise HTTPException(404)
        s = dict(r)
        s["books"] = [dict(b) for b in await (await db.execute(f"SELECT b.*, a.name as author_name, sr.name as series_name, (SELECT COUNT(*) FROM books b2 WHERE b2.series_id=b.series_id AND b2.hidden=0) as series_total FROM books b JOIN authors a ON b.author_id=a.id LEFT JOIN series sr ON b.series_id=sr.id WHERE b.series_id=? AND {HF} ORDER BY COALESCE(b.series_index,999), b.pub_date ASC", (sid,))).fetchall()]
        return s
    finally: await db.close()

@app.get("/api/series")
async def list_series(search: str = Query(None), sort: str = Query("name"), sort_dir: str = Query("asc"), has_missing: bool = Query(None)):
    db = await get_db()
    try:
        q = f"""SELECT s.*, a.name as author_name,
            COUNT(DISTINCT CASE WHEN {HF} THEN b.id END) as book_count,
            SUM(CASE WHEN b.owned=1 AND {HF} THEN 1 ELSE 0 END) as owned_count,
            SUM(CASE WHEN b.owned=0 AND {HF} THEN 1 ELSE 0 END) as missing_count,
            CASE WHEN COUNT(DISTINCT b.author_id) > 1 THEN 1 ELSE 0 END as multi_author
            FROM series s LEFT JOIN authors a ON s.author_id=a.id LEFT JOIN books b ON s.id=b.series_id"""
        p = []; c = []
        if search: c.append("(s.name LIKE ? OR a.name LIKE ?)"); p.extend([f"%{search}%"]*2)
        if c: q += " WHERE " + " AND ".join(c)
        q += " GROUP BY s.id"
        if has_missing: q += " HAVING missing_count > 0"
        d = "DESC" if sort_dir == "desc" else "ASC"
        q += {"missing": f" ORDER BY missing_count {d}", "author": f" ORDER BY a.sort_name {d}"}.get(sort, f" ORDER BY s.name {d}")
        return {"series": [dict(r) for r in await (await db.execute(q, p)).fetchall()]}
    finally: await db.close()

# ─── Books ───────────────────────────────────────────────────
@app.get("/api/books")
async def get_books(search: str = Query(None), author_id: int = Query(None), series_id: int = Query(None), owned: bool = Query(None), book_type: str = Query(None), mam_status: str = Query(None), sort: str = Query("title"), sort_dir: str = Query("asc"), page: int = Query(1, ge=1), per_page: int = Query(60, ge=1, le=5000), include_hidden: bool = Query(False)):
    db = await get_db()
    try:
        c = []; p = []
        if not include_hidden: c.append(HF)
        if search: c.append("(b.title LIKE ? OR a.name LIKE ? OR COALESCE(s.name,'') LIKE ?)"); p.extend([f"%{search}%"]*3)
        if author_id: c.append("b.author_id=?"); p.append(author_id)
        if series_id: c.append("b.series_id=?"); p.append(series_id)
        if owned is True: c.append("b.owned=1")
        elif owned is False: c.append("b.owned=0")
        if book_type == "series": c.append("b.series_id IS NOT NULL")
        elif book_type == "standalone": c.append("b.series_id IS NULL")
        if mam_status == "found": c.append("b.mam_status='found'")
        elif mam_status == "possible": c.append("b.mam_status='possible'")
        elif mam_status == "not_found": c.append("b.mam_status='not_found'")
        elif mam_status == "unscanned": c.append("b.mam_status IS NULL")
        w = " AND ".join(c) if c else "1=1"
        cnt = (await (await db.execute(f"SELECT COUNT(*) c FROM books b JOIN authors a ON b.author_id=a.id LEFT JOIN series s ON b.series_id=s.id WHERE {w}", p)).fetchone())["c"]
        d = "DESC" if sort_dir == "desc" else "ASC"
        o = {"title": f"b.title {d}", "author": f"a.sort_name {d}, b.title ASC", "series": f"COALESCE(s.name,'zzz') {d}, b.series_index ASC", "date": f"b.pub_date {d}", "added": f"b.first_seen_at {d}"}.get(sort, f"b.title {d}")
        off = (page-1)*per_page
        rows = await (await db.execute(f"SELECT b.*, a.name as author_name, s.name as series_name, (SELECT COUNT(*) FROM books b2 WHERE b2.series_id=b.series_id AND b2.hidden=0) as series_total FROM books b JOIN authors a ON b.author_id=a.id LEFT JOIN series s ON b.series_id=s.id WHERE {w} ORDER BY {o} LIMIT ? OFFSET ?", p+[per_page, off])).fetchall()
        return {"books": [dict(r) for r in rows], "total": cnt, "page": page, "per_page": per_page, "pages": max(1, (cnt+per_page-1)//per_page)}
    finally: await db.close()

@app.get("/api/missing")
async def get_missing(**kw): return await get_books(owned=False, **kw)

@app.get("/api/upcoming")
async def get_upcoming(search: str = Query(None), sort: str = Query("date"), sort_dir: str = Query("asc"), mam_status: str = Query(None), page: int = Query(1, ge=1), per_page: int = Query(60, ge=1, le=5000)):
    db = await get_db()
    try:
        c = [HF, "b.owned=0", "b.is_unreleased=1"]; p = []
        if search: c.append("(b.title LIKE ? OR a.name LIKE ? OR COALESCE(s.name,'') LIKE ?)"); p.extend([f"%{search}%"]*3)
        if mam_status == "found": c.append("b.mam_status='found'")
        elif mam_status == "possible": c.append("b.mam_status='possible'")
        elif mam_status == "not_found": c.append("b.mam_status='not_found'")
        elif mam_status == "unscanned": c.append("b.mam_status IS NULL")
        w = " AND ".join(c)
        cnt = (await (await db.execute(f"SELECT COUNT(*) c FROM books b JOIN authors a ON b.author_id=a.id LEFT JOIN series s ON b.series_id=s.id WHERE {w}", p)).fetchone())["c"]
        d = "DESC" if sort_dir == "desc" else "ASC"
        o = {"date": f"COALESCE(b.expected_date, '9999') {d}", "title": f"b.title {d}", "author": f"a.sort_name {d}"}.get(sort, f"COALESCE(b.expected_date, '9999') {d}")
        off = (page-1)*per_page
        rows = await (await db.execute(f"SELECT b.*, a.name as author_name, s.name as series_name, (SELECT COUNT(*) FROM books b2 WHERE b2.series_id=b.series_id AND b2.hidden=0) as series_total FROM books b JOIN authors a ON b.author_id=a.id LEFT JOIN series s ON b.series_id=s.id WHERE {w} ORDER BY {o} LIMIT ? OFFSET ?", p+[per_page, off])).fetchall()
        return {"books": [dict(r) for r in rows], "total": cnt, "page": page, "per_page": per_page, "pages": max(1, (cnt+per_page-1)//per_page)}
    finally: await db.close()

# ─── Book Actions ────────────────────────────────────────────
@app.post("/api/books/{bid}/hide")
async def hide(bid: int):
    db = await get_db()
    try: await db.execute("UPDATE books SET hidden=1 WHERE id=?", (bid,)); await db.commit(); return {"status": "ok"}
    finally: await db.close()

@app.post("/api/books/{bid}/unhide")
async def unhide(bid: int):
    db = await get_db()
    try: await db.execute("UPDATE books SET hidden=0 WHERE id=?", (bid,)); await db.commit(); return {"status": "ok"}
    finally: await db.close()

@app.get("/api/books/hidden")
async def get_hidden():
    db = await get_db()
    try:
        rows = await (await db.execute("SELECT b.*, a.name as author_name, s.name as series_name, (SELECT COUNT(*) FROM books b2 WHERE b2.series_id=b.series_id AND b2.hidden=0) as series_total FROM books b JOIN authors a ON b.author_id=a.id LEFT JOIN series s ON b.series_id=s.id WHERE b.hidden=1 ORDER BY a.sort_name, b.title")).fetchall()
        return {"books": [dict(r) for r in rows]}
    finally: await db.close()

@app.post("/api/books/{bid}/dismiss")
async def dismiss(bid: int):
    db = await get_db()
    try: await db.execute("UPDATE books SET is_new=0 WHERE id=?", (bid,)); await db.commit(); return {"status": "ok"}
    finally: await db.close()

@app.put("/api/books/{bid}")
async def update_book(bid: int, data: dict = Body(...)):
    db = await get_db()
    try:
        fields = []; vals = []
        for k in ["title", "description", "pub_date", "expected_date", "isbn", "cover_url", "series_index", "source_url"]:
            if k in data:
                fields.append(f"{k}=?"); vals.append(data[k])
        # Handle MAM URL — validate format and update status
        if "mam_url" in data:
            mam_url = (data["mam_url"] or "").strip()
            if mam_url:
                import re
                mam_match = re.match(r'https?://(?:www\.)?myanonamouse\.net/t/(\d+)', mam_url)
                if not mam_match:
                    raise HTTPException(400, "Invalid MAM URL. Expected format: https://www.myanonamouse.net/t/123456")
                torrent_id = int(mam_match.group(1))
                fields.extend(["mam_url=?", "mam_status=?", "mam_torrent_id=?"])
                vals.extend([mam_url, "found", torrent_id])
            else:
                fields.extend(["mam_url=?", "mam_status=?", "mam_torrent_id=?"])
                vals.extend([None, None, None])
        if "is_unreleased" in data:
            fields.append("is_unreleased=?"); vals.append(1 if data["is_unreleased"] else 0)
        if not fields:
            return {"status": "no changes"}
        vals.append(bid)
        await db.execute(f"UPDATE books SET {', '.join(fields)} WHERE id=?", vals)
        await db.commit()
        return {"status": "ok"}
    finally: await db.close()

@app.post("/api/books/add")
async def add_book(data: dict = Body(...)):
    """Manually add a missing/upcoming book."""
    db = await get_db()
    try:
        title = data.get("title", "").strip()
        author_name = data.get("author_name", "").strip()
        if not title or not author_name:
            raise HTTPException(400, "Title and author are required")
        # Find or create author
        row = await (await db.execute("SELECT id FROM authors WHERE name=?", (author_name,))).fetchone()
        if row:
            aid = row["id"]
        else:
            cur = await db.execute("INSERT INTO authors (name, sort_name) VALUES (?, ?)", (author_name, author_name))
            aid = cur.lastrowid
        # Find series if specified
        sid = None
        if data.get("series_name"):
            srow = await (await db.execute("SELECT id FROM series WHERE name=? AND author_id=?", (data["series_name"], aid))).fetchone()
            if srow:
                sid = srow["id"]
            else:
                cur = await db.execute("INSERT INTO series (name, author_id) VALUES (?, ?)", (data["series_name"], aid))
                sid = cur.lastrowid
        is_unreleased = 1 if data.get("is_unreleased") else 0
        src = data.get("source", "manual")
        cur = await db.execute(
            "INSERT INTO books (title, author_id, series_id, series_index, pub_date, expected_date, is_unreleased, description, isbn, cover_url, source, source_url, owned, is_new) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,1)",
            (title, aid, sid, data.get("series_index"), data.get("pub_date"), data.get("expected_date"), is_unreleased, data.get("description"), data.get("isbn"), data.get("cover_url"), src, data.get("source_url"))
        )
        await db.commit()
        return {"status": "ok", "book_id": cur.lastrowid}
    finally: await db.close()

@app.post("/api/books/dismiss-all")
async def dismiss_all():
    db = await get_db()
    try: await db.execute("UPDATE books SET is_new=0 WHERE is_new=1"); await db.commit(); return {"status": "ok"}
    finally: await db.close()

@app.delete("/api/books/{bid}")
async def delete_book(bid: int):
    """Delete a book entry — only non-Calibre (discovered/imported) books can be deleted."""
    db = await get_db()
    try:
        row = await (await db.execute("SELECT id, source, owned, calibre_id FROM books WHERE id=?", (bid,))).fetchone()
        if not row: raise HTTPException(404, "Book not found")
        if row["calibre_id"] and row["source"] == "calibre":
            raise HTTPException(400, "Cannot delete books synced from Calibre. Remove them from Calibre instead.")
        await db.execute("DELETE FROM books WHERE id=?", (bid,))
        await db.commit()
        return {"status": "ok"}
    finally: await db.close()

@app.get("/api/export")
async def export_books(filter: str = Query("missing"), format: str = Query("csv")):
    """Export books as CSV or text. filter: all|library|missing. format: csv|text."""
    from fastapi.responses import Response
    db = await get_db()
    try:
        c = [HF]; p = []
        if filter == "library": c.append("b.owned=1")
        elif filter == "missing": c.append("b.owned=0")
        w = " AND ".join(c)
        rows = await (await db.execute(
            f"SELECT b.title, a.name as author_name, b.pub_date, b.expected_date, b.source, b.source_url, b.is_unreleased, b.mam_status, b.mam_url, b.mam_formats "
            f"FROM books b JOIN authors a ON b.author_id=a.id WHERE {w} ORDER BY a.sort_name, b.title", p
        )).fetchall()
        
        # Priority order for "best" URL
        url_priority = ["goodreads", "hardcover", "kobo", "fantasticfiction"]
        
        def _best_url(source_url_json):
            """Extract the best URL and its source name from JSON."""
            if not source_url_json:
                return "", ""
            try:
                urls = json.loads(source_url_json)
                if not isinstance(urls, dict):
                    return "", ""
                for src in url_priority:
                    if src in urls:
                        return src, urls[src]
                # Return first available if none match priority
                for src, url in urls.items():
                    return src, url
            except:
                pass
            return "", ""
        
        def _all_urls(source_url_json):
            """Get all URLs as formatted string."""
            if not source_url_json:
                return ""
            try:
                urls = json.loads(source_url_json)
                if isinstance(urls, dict):
                    parts = []
                    for src in url_priority:
                        if src in urls:
                            parts.append(urls[src])
                    for src, url in urls.items():
                        if url not in parts:
                            parts.append(url)
                    return " | ".join(parts)
            except:
                pass
            return ""
        
        if format == "csv":
            import csv, io
            buf = io.StringIO()
            writer = csv.writer(buf)
            writer.writerow(["Title", "Author", "Release Date", "Source", "Source URL", "MAM Status", "MAM URL", "MAM Formats"])
            for r in rows:
                src_name, src_url = _best_url(r["source_url"])
                date = r["pub_date"] or r["expected_date"] or ""
                if r["is_unreleased"] and r["expected_date"]:
                    date = f"{r['expected_date']} (upcoming)"
                mam_status = r["mam_status"] or ""
                mam_url = r["mam_url"] or ""
                mam_formats = r["mam_formats"] or ""
                writer.writerow([r["title"], r["author_name"], date, src_name or r["source"] or "", src_url, mam_status, mam_url, mam_formats])
            content = buf.getvalue()
            return Response(content=content, media_type="text/csv",
                          headers={"Content-Disposition": f"attachment; filename=books_{filter}.csv"})
        else:
            lines = ["Title, Author, Release Date, Source, Source URL, MAM Status, MAM URL, MAM Formats"]
            for r in rows:
                src_name, src_url = _best_url(r["source_url"])
                date = r["pub_date"] or r["expected_date"] or ""
                if r["is_unreleased"] and r["expected_date"]:
                    date = f"{r['expected_date']} (upcoming)"
                # Escape commas in titles/authors
                title = r["title"].replace(",", ";")
                author = r["author_name"].replace(",", ";")
                mam_status = r["mam_status"] or ""
                mam_url = r["mam_url"] or ""
                mam_formats = (r["mam_formats"] or "").replace(",", "/")
                lines.append(f"{title}, {author}, {date}, {src_name or r['source'] or ''}, {src_url}, {mam_status}, {mam_url}, {mam_formats}")
            content = "\n".join(lines)
            return Response(content=content, media_type="text/plain",
                          headers={"Content-Disposition": f"attachment; filename=books_{filter}.txt"})
    finally: await db.close()

async def _fetch_goodreads_book(book_id: str) -> dict:
    """Fetch book details from Goodreads by book ID."""
    import re as _re, httpx
    from bs4 import BeautifulSoup
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"}
    async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=True) as client:
        r = await client.get(f"https://www.goodreads.com/book/show/{book_id}")
        r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    result = {"goodreads_id": book_id, "source": "goodreads", "source_url": json.dumps({"goodreads": f"https://www.goodreads.com/book/show/{book_id}"})}
    title_el = soup.find("h1", {"data-testid": "bookTitle"}) or soup.find("h1")
    result["title"] = title_el.get_text(strip=True) if title_el else ""
    author_el = soup.find("span", {"data-testid": "name"}) or soup.select_one("a.ContributorLink span")
    result["author_name"] = author_el.get_text(strip=True) if author_el else ""
    for script in soup.select("script[type='application/ld+json']"):
        try:
            ld = json.loads(script.string)
            if ld.get("image"): result["cover_url"] = ld["image"]
            if ld.get("datePublished"): result["pub_date"] = ld["datePublished"][:10]
            if ld.get("isbn"): result["isbn"] = ld["isbn"]
            if ld.get("numberOfPages"): result["page_count"] = int(ld["numberOfPages"])
        except: pass
    desc_el = soup.find("div", {"data-testid": "description"})
    if desc_el:
        spans = desc_el.find_all("span", class_=_re.compile("Formatted"))
        result["description"] = (spans[-1] if spans else desc_el).get_text(strip=True)[:1000]
    series_el = soup.find("div", {"data-testid": "seriesTitle"})
    if series_el:
        for link in series_el.find_all("a"):
            sm = _re.match(r'(.+?)\s*(?:\(|#)([\d.]+)\)?', link.get_text(strip=True))
            if sm:
                result["series_name"] = sm.group(1).strip()
                try: result["series_index"] = float(sm.group(2))
                except: pass
                break
    pub_el = soup.find("p", {"data-testid": "publicationInfo"})
    if pub_el:
        pt = pub_el.get_text(strip=True)
        em = _re.search(r'[Ee]xpected\s+(?:publication\s+)?(.+?)$', pt)
        if em:
            result["is_unreleased"] = True
            from datetime import datetime
            for fmt in ["%B %d, %Y", "%B %Y", "%Y"]:
                try:
                    result["expected_date"] = datetime.strptime(_re.sub(r'(\d+)(?:st|nd|rd|th)', r'\1', em.group(1).strip()), fmt).strftime("%Y-%m-%d")
                    break
                except: pass
    return result


async def _fetch_hardcover_book(slug: str) -> dict:
    """Fetch book details from Hardcover by slug using search API."""
    import re as _re
    settings = load_settings()
    api_key = settings.get("hardcover_api_key", "")
    if not api_key:
        raise Exception("Hardcover API key not configured")
    headers = {"Content-Type": "application/json", "Authorization": api_key if api_key.startswith("Bearer") else f"Bearer {api_key}"}
    
    # Convert slug to search query: "honor-among-thieves-2014" → "honor among thieves"
    search_term = _re.sub(r'-\d{4}$', '', slug).replace('-', ' ')
    logger.debug(f"  Hardcover import: slug='{slug}' → search='{search_term}'")
    
    # Step 1: Search for candidate book IDs
    search_query = """query($q: String!) {
        search(query: $q, query_type: "Book", per_page: 10, page: 1) { ids }
    }"""
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        r = await client.post("https://api.hardcover.app/v1/graphql",
            json={"query": search_query, "variables": {"q": search_term}})
        r.raise_for_status()
    
    data = r.json()
    ids_list = data.get("data", {}).get("search", {}).get("ids", [])
    logger.debug(f"  Hardcover import: search returned {len(ids_list)} IDs: {ids_list[:10]}")
    
    if not ids_list:
        raise Exception(f"No results on Hardcover for: {search_term}")
    
    # Step 2: Fetch all candidates and match by slug
    detail_query = """query($ids: [Int!]) { books(where: {id: {_in: $ids}}) {
        id title slug description
        series: cached_featured_series
        book_series { position series { name id } }
        contributions { author { name id } }
        editions(order_by: {users_count: desc_nulls_last}, limit: 1) {
            isbn_13 release_date
            image: cached_image
        }
    }}"""
    async with httpx.AsyncClient(timeout=30, headers=headers) as client:
        r = await client.post("https://api.hardcover.app/v1/graphql",
            json={"query": detail_query, "variables": {"ids": [int(i) for i in ids_list[:10]]}})
        r.raise_for_status()
    
    bdata = r.json()
    candidates = bdata.get("data", {}).get("books", [])
    logger.debug(f"  Hardcover import: fetched {len(candidates)} candidates")
    
    # Match by exact slug first
    book = None
    for c in candidates:
        c_slug = c.get("slug", "")
        logger.debug(f"  Hardcover import: candidate slug='{c_slug}' title='{c.get('title')}'")
        if c_slug == slug:
            book = c
            logger.debug(f"  Hardcover import: MATCHED by slug → '{c.get('title')}'")
            break
    
    # Fallback: match by title similarity
    if not book:
        for c in candidates:
            if search_term.lower() in c.get("title", "").lower():
                book = c
                logger.debug(f"  Hardcover import: MATCHED by title → '{c.get('title')}'")
                break
    
    # Last fallback: first result
    if not book and candidates:
        book = candidates[0]
        logger.debug(f"  Hardcover import: FALLBACK to first → '{book.get('title')}'")
    
    if not book:
        raise Exception(f"Book not found on Hardcover for: {search_term}")
    
    # Parse the matched book data (already fetched with full details)
    real_slug = book.get("slug", slug)
    edition = book.get("editions", [{}])[0] if book.get("editions") else {}
    cover = None
    img = edition.get("image")
    if isinstance(img, dict): cover = img.get("url")
    elif isinstance(img, str): cover = img
    author_name = ""
    for c in book.get("contributions", []):
        a = c.get("author", {})
        if isinstance(a, dict) and a.get("name"):
            author_name = a["name"]; break
    series_name = None; series_index = None; series_options = []
    # Collect all series from book_series relation
    bs = book.get("book_series")
    if bs and isinstance(bs, list):
        for bse in bs:
            if isinstance(bse, dict):
                sr_obj = bse.get("series", {})
                if isinstance(sr_obj, dict) and sr_obj.get("name"):
                    series_options.append({"name": sr_obj["name"], "position": bse.get("position")})
    # Also check cached_featured_series
    series_data = book.get("series")
    if series_data and isinstance(series_data, list):
        for s in series_data:
            if isinstance(s, dict) and s.get("name"):
                if not any(so["name"] == s["name"] for so in series_options):
                    series_options.append({"name": s["name"], "position": s.get("position")})
    # Pick best series using same heuristic as scan
    if series_options:
        def _score(c):
            s = 0
            name = c["name"]
            if ":" in name: s += 10
            if c["position"] is not None: s += 5
            if "(" in name: s -= 3
            s += min(len(name.split()), 5)
            return s
        series_options.sort(key=_score, reverse=True)
        series_name = series_options[0]["name"]
        series_index = series_options[0]["position"]
        logger.debug(f"  Hardcover import: {len(series_options)} series found: {[s['name'] for s in series_options]} → default '{series_name}'")
    return {
        "hardcover_id": str(book.get("id")), "source": "hardcover",
        "source_url": json.dumps({"hardcover": f"https://hardcover.app/books/{real_slug}"}),
        "title": book.get("title", ""), "author_name": author_name,
        "description": (book.get("description") or "")[:1000],
        "isbn": edition.get("isbn_13"), "pub_date": edition.get("release_date"),
        "cover_url": cover, "series_name": series_name, "series_index": series_index,
        "series_options": series_options if len(series_options) > 1 else None,
    }


@app.post("/api/books/search-url")
async def search_by_url(data: dict = Body(...)):
    """Fetch book details from a Goodreads or Hardcover URL."""
    import re
    url = data.get("url", "").strip()
    if not url:
        raise HTTPException(400, "URL is required")
    try:
        gr = re.search(r'goodreads\.com/book/show/(\d+)', url)
        hc = re.search(r'hardcover\.app/books/([a-z0-9-]+)', url)
        if gr:
            return await _fetch_goodreads_book(gr.group(1))
        elif hc:
            return await _fetch_hardcover_book(hc.group(1))
        else:
            raise HTTPException(400, "Please provide a Goodreads or Hardcover book URL")
    except HTTPException: raise
    except httpx.HTTPError as e:
        raise HTTPException(502, f"Failed to fetch: {e}")
    except Exception as e:
        raise HTTPException(500, f"Error: {e}")


@app.post("/api/books/import-preview")
async def import_preview(data: dict = Body(...)):
    """Parse multiple URLs, fetch each, and check against DB."""
    import re
    from difflib import SequenceMatcher
    
    def _norm(s):
        """Normalize name for comparison: collapse spaces, strip punctuation."""
        s = re.sub(r'\s+', ' ', s.lower().strip())
        s = re.sub(r'\.\s*', '. ', s)  # "S.A." → "S. A."
        return re.sub(r'\s+', ' ', s).strip()
    
    def _fuzzy(a, b):
        return SequenceMatcher(None, _norm(a), _norm(b)).ratio() > 0.85
    
    urls = data.get("urls", [])
    if not urls:
        raise HTTPException(400, "No URLs provided")
    
    results = []
    db = await get_db()
    try:
        # Pre-load all books for fuzzy matching
        all_books = await (await db.execute(
            f"SELECT b.id, b.title, b.owned, b.source_url, b.author_id, a.name as author_name "
            f"FROM books b JOIN authors a ON b.author_id=a.id WHERE {HF}"
        )).fetchall()
        
        for url in urls[:50]:
            url = url.strip()
            if not url: continue
            entry = {"url": url, "status": "error", "error": None, "book": None}
            try:
                gr = re.search(r'goodreads\.com/book/show/(\d+)', url)
                hc = re.search(r'hardcover\.app/books/([a-zA-Z0-9_-]+)', url)
                if gr:
                    book = await _fetch_goodreads_book(gr.group(1))
                elif hc:
                    book = await _fetch_hardcover_book(hc.group(1))
                else:
                    entry["error"] = "Unrecognized URL format"
                    results.append(entry); continue
                
                entry["book"] = book
                title = book.get("title", "")
                author = book.get("author_name", "")
                
                if title and author:
                    # Fuzzy match against all books in DB
                    matched = None
                    for r in all_books:
                        if _fuzzy(r["title"], title) and _fuzzy(r["author_name"], author):
                            matched = r
                            break
                    if matched:
                        entry["status"] = "owned" if matched["owned"] else "tracked"
                        entry["existing_id"] = matched["id"]
                        entry["has_url"] = bool(matched["source_url"] and matched["source_url"] != "{}")
                    else:
                        entry["status"] = "new"
                else:
                    entry["status"] = "new"
            except Exception as e:
                entry["error"] = str(e)[:200]
            results.append(entry)
            await asyncio.sleep(0.5)
        return {"results": results}
    finally: await db.close()


@app.post("/api/books/import-add")
async def import_add_books(data: dict = Body(...)):
    """Add books from import preview. Expects {books: [{...book data...}]}."""
    import re
    from difflib import SequenceMatcher
    
    def _norm(s):
        s = re.sub(r'\s+', ' ', s.lower().strip())
        s = re.sub(r'\.\s*', '. ', s)
        return re.sub(r'\s+', ' ', s).strip()
    
    def _fuzzy(a, b):
        return SequenceMatcher(None, _norm(a), _norm(b)).ratio() > 0.85
    
    books = data.get("books", [])
    if not books:
        raise HTTPException(400, "No books to import")
    added = 0; updated = 0
    for book_data in books:
        try:
            title = book_data.get("title", "").strip()
            author_name = book_data.get("author_name", "").strip()
            if not title or not author_name: continue
            
            db = await get_db()
            try:
                # Fuzzy-match author
                all_authors = await (await db.execute("SELECT id, name FROM authors")).fetchall()
                aid = None
                for a in all_authors:
                    if _fuzzy(a["name"], author_name):
                        aid = a["id"]; break
                if not aid:
                    cur = await db.execute("INSERT INTO authors (name, sort_name) VALUES (?, ?)", (author_name, author_name))
                    aid = cur.lastrowid
                
                # Fuzzy-match existing book
                existing_books = await (await db.execute("SELECT id, title, source_url FROM books WHERE author_id=?", (aid,))).fetchall()
                existing = None
                for eb in existing_books:
                    if _fuzzy(eb["title"], title):
                        existing = eb; break
                
                if existing:
                    if book_data.get("source_url"):
                        try:
                            new_urls = json.loads(book_data["source_url"])
                            old_urls = json.loads(existing["source_url"] or "{}")
                            old_urls.update(new_urls)
                            await db.execute("UPDATE books SET source_url=? WHERE id=?", (json.dumps(old_urls), existing["id"]))
                            updated += 1
                        except: pass
                else:
                    # Find/create series
                    sid = None
                    if book_data.get("series_name"):
                        srow = await (await db.execute("SELECT id FROM series WHERE LOWER(name)=LOWER(?) AND author_id=?", (book_data["series_name"], aid))).fetchone()
                        if srow: sid = srow["id"]
                        else:
                            cur = await db.execute("INSERT INTO series (name, author_id) VALUES (?, ?)", (book_data["series_name"], aid))
                            sid = cur.lastrowid
                    
                    is_unreleased = 1 if book_data.get("is_unreleased") else 0
                    src = book_data.get("source", "import")
                    await db.execute(
                        "INSERT INTO books (title, author_id, series_id, series_index, pub_date, expected_date, is_unreleased, description, isbn, cover_url, source, source_url, owned, is_new) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,1)",
                        (title, aid, sid, book_data.get("series_index"), book_data.get("pub_date"),
                         book_data.get("expected_date"), is_unreleased, book_data.get("description"),
                         book_data.get("isbn"), book_data.get("cover_url"), src,
                         book_data.get("source_url", "{}"))
                    )
                    added += 1
                await db.commit()
            finally: await db.close()
        except Exception as e:
            logger.error(f"Import error for '{book_data.get('title')}': {e}")
    return {"status": "ok", "added": added, "updated": updated}

# ─── Sync ────────────────────────────────────────────────────
@app.post("/api/sync/calibre")
async def trigger_sync():
    import os as _os
    active_slug = get_active_library()
    lib = next((l for l in _discovered_libraries if l["slug"] == active_slug), None)
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
        _last_calibre_check["at"] = time.time()
        _last_calibre_check["synced"] = True
        return {"status": "ok", **result}
    except Exception as e:
        raise HTTPException(500, str(e))

@app.post("/api/sync")
async def trigger_sync_alias():
    return await trigger_sync()

@app.post("/api/sync/lookup")
async def trigger_lookup():
    global _lookup_task, _lookup_progress
    s = load_settings()
    if not s.get("author_scanning_enabled", True):
        return {"error": "Author scanning is disabled — enable it in Settings"}
    if _lookup_task and not _lookup_task.done():
        return {"error": "An author scan is already running"}
    _lookup_progress = {"running": True, "checked": 0, "total": 0, "current_author": "",
                        "new_books": 0, "status": "scanning", "type": "lookup"}
    def _progress(data):
        _lookup_progress.update({"checked": data["checked"], "total": data["total"],
                                 "current_author": data["current_author"], "new_books": data["new_books"]})
    async def _do():
        global _lookup_progress
        try:
            await run_full_lookup(on_progress=_progress)
            _lookup_progress.update({"running": False, "status": "complete"})
        except Exception as e:
            logger.error(f"Author scan error: {e}")
            _lookup_progress.update({"running": False, "status": f"error: {e}"})
    _lookup_task = asyncio.create_task(_do())
    return {"status": "started"}

@app.post("/api/lookup")
async def trigger_lookup_alias():
    return await trigger_lookup()

@app.post("/api/lookup/cancel")
async def lookup_cancel():
    """Cancel the currently running author scan."""
    global _lookup_task, _lookup_progress
    if _lookup_task and not _lookup_task.done():
        _lookup_task.cancel()
        _lookup_progress.update({"running": False, "status": "cancelled"})
        logger.info("Author scan cancelled by user")
        return {"status": "ok", "message": "Author scan cancelled"}
    return {"status": "ok", "message": "No author scan running"}


@app.get("/api/lookup/status")
async def lookup_status():
    """Get progress of the current/most recent author scan."""
    return dict(_lookup_progress)

@app.post("/api/sync/full-rescan")
async def trigger_full_rescan():
    global _lookup_task, _lookup_progress
    s = load_settings()
    if not s.get("author_scanning_enabled", True):
        return {"error": "Author scanning is disabled — enable it in Settings"}
    if _lookup_task and not _lookup_task.done():
        return {"error": "An author scan is already running"}
    _lookup_progress = {"running": True, "checked": 0, "total": 0, "current_author": "",
                        "new_books": 0, "status": "scanning", "type": "full_rescan"}
    def _progress(data):
        _lookup_progress.update({"checked": data["checked"], "total": data["total"],
                                 "current_author": data["current_author"], "new_books": data["new_books"]})
    async def _do():
        global _lookup_progress
        try:
            await run_full_rescan(on_progress=_progress)
            _lookup_progress.update({"running": False, "status": "complete"})
        except Exception as e:
            logger.error(f"Full re-scan error: {e}")
            _lookup_progress.update({"running": False, "status": f"error: {e}"})
    _lookup_task = asyncio.create_task(_do())
    return {"status": "started"}

@app.post("/api/authors/{aid}/lookup")
async def trigger_author_lookup(aid: int):
    s = load_settings()
    if not s.get("author_scanning_enabled", True):
        return {"error": "Author scanning is disabled — enable it in Settings"}
    db = await get_db()
    try:
        r = await (await db.execute("SELECT * FROM authors WHERE id=?", (aid,))).fetchone()
        if not r: raise HTTPException(404)
    finally: await db.close()
    return {"status": "ok", "new_books": await lookup_author(aid, dict(r)["name"])}

@app.post("/api/authors/{aid}/full-rescan")
async def trigger_author_full_rescan(aid: int):
    """Full re-scan for a single author."""
    s = load_settings()
    if not s.get("author_scanning_enabled", True):
        return {"error": "Author scanning is disabled �� enable it in Settings"}
    db = await get_db()
    try:
        r = await (await db.execute("SELECT * FROM authors WHERE id=?", (aid,))).fetchone()
        if not r: raise HTTPException(404)
    finally: await db.close()
    return {"status": "ok", "new_books": await lookup_author(aid, dict(r)["name"], full_scan=True)}

@app.post("/api/authors/clear-scan-data")
async def clear_author_scan_data(data: dict = Body(...)):
    """Clear source and/or MAM scan data for specified authors."""
    author_ids = data.get("author_ids", [])
    clear_source = data.get("clear_source", False)
    clear_mam = data.get("clear_mam", False)
    if not author_ids:
        return {"error": "No authors specified"}
    if not clear_source and not clear_mam:
        return {"error": "Nothing to clear — specify clear_source and/or clear_mam"}
    db = await get_db()
    try:
        placeholders = ",".join(["?" for _ in author_ids])
        affected = 0
        if clear_source:
            # Count books that will be deleted
            count_row = await db.execute_fetchall(
                f"SELECT COUNT(*) FROM books WHERE author_id IN ({placeholders}) AND owned=0 AND calibre_id IS NULL",
                author_ids
            )
            affected = count_row[0][0] if count_row else 0
            # Delete non-owned books (discovered by source scans) for these authors
            await db.execute(
                f"DELETE FROM books WHERE author_id IN ({placeholders}) AND owned=0 AND calibre_id IS NULL",
                author_ids
            )
            # Reset source URLs on owned books (keep source='calibre' intact)
            await db.execute(
                f"UPDATE books SET source_url=NULL WHERE author_id IN ({placeholders}) AND owned=1",
                author_ids
            )
            await db.execute(
                f"UPDATE authors SET last_lookup_at=NULL WHERE id IN ({placeholders})",
                author_ids
            )
        if clear_mam:
            await db.execute(
                f"UPDATE books SET mam_url=NULL, mam_status=NULL, mam_formats=NULL, mam_torrent_id=NULL, mam_has_multiple=0 WHERE author_id IN ({placeholders})",
                author_ids
            )
        await db.commit()
        logger.info(f"Cleared scan data for {len(author_ids)} authors (source={clear_source}, mam={clear_mam}), {affected} books deleted")
        return {"status": "ok", "authors_cleared": len(author_ids), "books_deleted": affected}
    finally:
        await db.close()

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
    global _mam_scan_task, _mam_scan_progress
    s = load_settings()
    if not s.get("mam_enabled") or not s.get("mam_session_id"):
        return {"error": "MAM not configured or not enabled"}
    if not s.get("mam_scanning_enabled", True):
        return {"error": "MAM scanning is disabled — enable it in Settings"}
    if _mam_scan_progress.get("running"):
        return {"error": "A MAM scan is already running"}
    if _lookup_progress.get("running"):
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
    _mam_scan_progress = {"running": True, "scanned": 0, "total": scan_total,
                          "found": 0, "possible": 0, "not_found": 0, "errors": 0,
                          "status": "scanning", "type": "manual"}

    async def _do_scan():
        global _mam_scan_progress
        batch_num = 0
        while True:
            # Wait for any author scan before starting next batch
            if _lookup_progress.get("running"):
                _mam_scan_progress["status"] = "waiting (author scan running)"
                logger.info("MAM scan waiting for author scan to finish...")
                while _lookup_progress.get("running"):
                    await asyncio.sleep(30)
                logger.info("Author scan finished — MAM scan resuming")
                _mam_scan_progress["status"] = "scanning"
            cs = load_settings()
            if not cs.get("mam_enabled") or not cs.get("mam_session_id"):
                _mam_scan_progress.update({"status": "stopped (MAM disabled)", "running": False})
                return
            db = await get_db()
            try:
                def _progress(stats):
                    _mam_scan_progress.update({
                        "scanned": base_scanned + stats["scanned"],
                        "found": base_found + stats["found"],
                        "possible": base_possible + stats["possible"],
                        "not_found": base_not_found + stats["not_found"],
                        "errors": base_errors + stats["errors"],
                    })
                base_scanned = _mam_scan_progress["scanned"]
                base_found = _mam_scan_progress["found"]
                base_possible = _mam_scan_progress["possible"]
                base_not_found = _mam_scan_progress["not_found"]
                base_errors = _mam_scan_progress["errors"]
                batch_limit = min(100, scan_total - _mam_scan_progress["scanned"])
                if batch_limit <= 0:
                    _mam_scan_progress.update({"status": "complete", "running": False})
                    logger.info(f"MAM scan reached limit ({scan_total}): {_mam_scan_progress['scanned']} scanned, {_mam_scan_progress['found']} found")
                    await db.close()
                    return
                result = await mam_scan_batch(
                    db, session_id=cs["mam_session_id"], limit=batch_limit,
                    delay=cs.get("rate_mam", 2), skip_ip_update=True,
                    format_priority=cs.get("mam_format_priority"),
                    on_progress=_progress,
                    cancel_check=lambda: _lookup_progress.get("running", False),
                )
                # Progress already updated per-book via on_progress callback
                if result.get("error"):
                    _mam_scan_progress.update({"status": f"error: {result['error']}", "running": False})
                    return
                remaining = await db.execute_fetchall(
                    "SELECT COUNT(*) FROM books WHERE mam_status IS NULL AND is_unreleased=0 AND hidden=0"
                )
                left = remaining[0][0] if remaining else 0
                _mam_scan_progress["total"] = _mam_scan_progress["scanned"] + left
                await db.execute(
                    "INSERT INTO sync_log (sync_type, started_at, finished_at, status, books_found, books_new) VALUES (?,?,?,?,?,?)",
                    ("mam", time.time(), time.time(), "complete",
                     result.get("scanned", 0), result.get("found", 0))
                )
                await db.commit()
            except Exception as e:
                logger.error(f"MAM scan batch error: {e}")
                _mam_scan_progress.update({"status": f"error: {e}", "running": False})
                return
            finally:
                await db.close()
            if left == 0 or result.get("scanned", 0) == 0 or _mam_scan_progress["scanned"] >= scan_total:
                _mam_scan_progress.update({"status": "complete", "running": False})
                logger.info(f"MAM scan complete: {_mam_scan_progress['scanned']} scanned, {_mam_scan_progress['found']} found")
                return
            batch_num += 1
            _mam_scan_progress["status"] = "paused"
            logger.info(f"MAM scan batch {batch_num} done ({_mam_scan_progress['scanned']}/{_mam_scan_progress['total']}), pausing 5 min")
            await asyncio.sleep(300)
            # Wait for any author scan to finish before resuming
            if _lookup_progress.get("running"):
                _mam_scan_progress["status"] = "waiting (author scan running)"
                logger.info("MAM scan waiting for author scan to finish...")
                while _lookup_progress.get("running"):
                    await asyncio.sleep(30)
                logger.info("Author scan finished — MAM scan resuming")
            _mam_scan_progress["status"] = "scanning"

    _mam_scan_task = asyncio.create_task(_do_scan())
    return {"status": "started", "total": total}


@app.post("/api/mam/scan/cancel")
async def mam_scan_cancel():
    """Cancel the currently running MAM scan."""
    global _mam_scan_task, _mam_scan_progress
    if _mam_scan_task and not _mam_scan_task.done():
        _mam_scan_task.cancel()
        _mam_scan_progress.update({"running": False, "status": "cancelled"})
        logger.info("MAM scan cancelled by user")
        return {"status": "ok", "message": "MAM scan cancelled"}
    return {"status": "ok", "message": "No MAM scan running"}


@app.get("/api/mam/scan/status")
async def mam_scan_status_endpoint():
    """Get progress of any active MAM scan (manual, scheduled, or full)."""
    global _mam_scan_progress
    if _mam_scan_progress.get("running"):
        return dict(_mam_scan_progress)
    if _mam_full_scan_task and not _mam_full_scan_task.done():
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
    return dict(_mam_scan_progress)


@app.post("/api/mam/test-scan")
async def mam_test_scan():
    """Run a quick test scan of 10 books and return results inline."""
    s = load_settings()
    if not s.get("mam_enabled") or not s.get("mam_session_id"):
        return {"error": "MAM not configured or not enabled"}
    if not s.get("mam_scanning_enabled", True):
        return {"error": "MAM scanning is disabled — enable it in Settings"}
    if _mam_scan_task and not _mam_scan_task.done():
        return {"error": "A MAM scan is already running — wait for it to finish"}
    db = await get_db()
    try:
        result = await mam_scan_batch(
            db, session_id=s["mam_session_id"], limit=10,
            delay=s.get("rate_mam", 2),
            skip_ip_update=True,
            format_priority=s.get("mam_format_priority"),
            cancel_check=lambda: _lookup_progress.get("running", False),
        )
        return result
    finally:
        await db.close()


@app.post("/api/mam/full-scan")
async def mam_full_scan_start():
    """Start a full MAM library scan (250 books/batch, 1hr between batches)."""
    global _mam_full_scan_task
    s = load_settings()
    if not s.get("mam_enabled") or not s.get("mam_session_id"):
        return {"error": "MAM not configured or not enabled"}
    if not s.get("mam_scanning_enabled", True):
        return {"error": "MAM scanning is disabled — enable it in Settings"}
    if _mam_full_scan_task and not _mam_full_scan_task.done():
        return {"error": "A full MAM scan is already running"}
    if _mam_scan_progress.get("running"):
        return {"error": "A MAM scan is already running — wait for it to finish"}
    if _lookup_progress.get("running"):
        return {"error": "An author scan is running — MAM scan will wait until it finishes"}

    db = await get_db()
    try:
        start_result = await mam_start_full_scan(db)
        if "error" in start_result:
            return start_result
    finally:
        await db.close()

    async def _full_scan_loop():
        global _mam_scan_progress
        _mam_scan_progress = {"running": True, "scanned": 0,
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
                _mam_scan_progress.update({
                    "scanned": fs.get("scanned", 0),
                    "total": fs.get("total_books", _mam_scan_progress["total"]),
                    "status": "scanning" if result["status"] == "batch_complete" else result["status"],
                })
            finally:
                await db.close()
            if result["status"] in ("scan_complete", "error", "no_scan"):
                _mam_scan_progress.update({"running": False, "status": result["status"]})
                break
            elif result["status"] == "batch_complete":
                wait = cs.get("mam_full_scan_batch_delay_minutes", 60) * 60
                _mam_scan_progress["status"] = "paused"
                logger.info(f"Full MAM scan: batch done, waiting {wait//60} min")
                await asyncio.sleep(wait)
                # Wait for any author scan to finish before resuming
                if _lookup_progress.get("running"):
                    _mam_scan_progress["status"] = "waiting (author scan running)"
                    logger.info("Full MAM scan waiting for author scan to finish...")
                    while _lookup_progress.get("running"):
                        await asyncio.sleep(30)
                    logger.info("Author scan finished — full MAM scan resuming")
                _mam_scan_progress["status"] = "scanning"

    _mam_full_scan_task = asyncio.create_task(_full_scan_loop())
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
    global _mam_full_scan_task
    db = await get_db()
    try:
        result = await mam_cancel_full_scan(db)
    finally:
        await db.close()
    if _mam_full_scan_task and not _mam_full_scan_task.done():
        _mam_full_scan_task.cancel()
    return result


@app.post("/api/scanning/author/toggle")
async def toggle_author_scanning():
    """Toggle author scanning on/off. Cancels running scan when disabled."""
    global _lookup_task, _lookup_progress
    s = load_settings()
    new_val = not s.get("author_scanning_enabled", True)
    s["author_scanning_enabled"] = new_val
    save_settings(s)
    if not new_val and _lookup_task and not _lookup_task.done():
        _lookup_task.cancel()
        _lookup_progress.update({"running": False, "status": "cancelled"})
        logger.info("Author scanning disabled — cancelled running scan")
    return {"enabled": new_val}


@app.post("/api/scanning/mam/toggle")
async def toggle_mam_scanning():
    """Toggle MAM scanning on/off without affecting MAM feature visibility."""
    global _mam_scan_task, _mam_full_scan_task, _mam_scan_progress
    s = load_settings()
    new_val = not s.get("mam_scanning_enabled", True)
    s["mam_scanning_enabled"] = new_val
    save_settings(s)
    if not new_val:
        if _mam_scan_task and not _mam_scan_task.done():
            _mam_scan_task.cancel()
            _mam_scan_progress.update({"running": False, "status": "cancelled"})
        if _mam_full_scan_task and not _mam_full_scan_task.done():
            _mam_full_scan_task.cancel()
            _mam_scan_progress.update({"running": False, "status": "cancelled"})
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


# FK columns that support name-to-ID resolution in the DB editor
DB_FK_RESOLVERS = {
    "books": {
        "author_id": {"table": "authors", "name_col": "name", "create_cols": {"sort_name": lambda name: ", ".join(reversed(name.split(" ", 1))) if " " in name else name}},
        "series_id": {"table": "series", "name_col": "name"},
    }
}


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


# ─── Covers ──────────────────────────────────────────────────
@app.get("/api/covers/{bid}")
async def get_cover(bid: int):
    db = await get_db()
    try:
        r = await (await db.execute("SELECT cover_path FROM books WHERE id=?", (bid,))).fetchone()
        if not r or not r["cover_path"]: raise HTTPException(404)
        p = Path(r["cover_path"])
        if not p.exists(): raise HTTPException(404)
        return FileResponse(p, media_type="image/jpeg")
    finally: await db.close()

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
