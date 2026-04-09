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

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.auth_db import init_auth_db
from app.auth_sessions import SESSION_COOKIE_NAME, verify_session_token
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
    auth,
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
    suggestions,
)
from app.runtime import IS_DOCKER, IS_STANDALONE
from app.sources.mam import (
    aclose_session as mam_aclose_session,
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

    # ─── Auth database ────────────────────────────────────
    # The auth DB is global to the deployment (NOT per-library) so the
    # admin login persists across library switches. See app/auth_db.py.
    await init_auth_db()

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
        # Signal background writers (MAM scanner, etc.) that a bulk sync
        # is in flight so they yield gracefully instead of racing us.
        # try/finally ensures the flag ALWAYS clears — a crash mid-sync
        # must not leave background tasks permanently paused.
        state._calibre_sync_in_progress = True
        try:
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
        finally:
            state._calibre_sync_in_progress = False

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
                            "current_book": "",
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
            # Phase 3d-1 (post-feedback): no longer deferring on a
            # concurrent author scan — WAL + busy_timeout absorbs the
            # contention from per-row updates from both sides. Only
            # Calibre sync (which holds the lock for tens of seconds
            # during bulk inserts) still gets deferred.
            if state._calibre_sync_in_progress:
                # Calibre bulk sync holds the SQLite write lock; defer
                # this scheduled MAM scan tick and try again next cycle.
                logger.debug("MAM scheduled scan deferred — Calibre sync in progress")
                continue
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
            # Phase 3d-1 (post-feedback): scheduled MAM scans run a
            # single 150-book batch per cycle (was 100). The interval
            # in Settings controls ONLY this scheduler — it does NOT
            # trigger a full library scan, just a regular bounded
            # batch via mam_scan_batch (same code path as the
            # Dashboard MAM Scan button, just smaller/single-batch).
            scan_limit = min(150, total_remaining)
            logger.info(f"MAM scheduled scan starting ({scan_limit} books, {total_remaining} total remaining)")
            state._mam_scan_progress = {"running": True, "scanned": 0, "total": scan_limit,
                                  "found": 0, "possible": 0, "not_found": 0,
                                  "errors": 0, "current_book": "",
                                  "status": "scanning", "type": "scheduled",
                                  "remaining": total_remaining}
            def _sched_progress(stats):
                state._mam_scan_progress.update({
                    "scanned": stats["scanned"],
                    "found": stats["found"],
                    "possible": stats["possible"],
                    "not_found": stats["not_found"],
                    "errors": stats["errors"],
                    # Phase 3d-2: forward in-flight book title.
                    "current_book": stats.get("current_book", ""),
                })
            db = await get_db()
            try:
                # Phase 3d-1 (post-feedback): no cancel_check anymore
                # (concurrent author scans are now allowed). Limit
                # raised 100→150 to match the manual scan batch size.
                result = await mam_scan_batch(
                    db, session_id=s["mam_session_id"], limit=150,
                    delay=s.get("rate_mam", 2), skip_ip_update=True,
                    format_priority=s.get("mam_format_priority"),
                    on_progress=_sched_progress,
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

    # Use supervised_task so an uncaught exception inside the scheduler loop
    # gets logged with full traceback AND auto-restarts after a short delay,
    # instead of silently killing the scheduler for the rest of the process
    # lifetime (the default `create_task` behavior).
    state.supervised_task(_mam_scheduler, name="mam_scheduler")
    scheduler.start()
    yield
    scheduler.shutdown()
    # Tear down the MAM httpx.AsyncClient (Batch C). aclose_session is a
    # coroutine — must be awaited so the underlying transport actually closes
    # before uvicorn finishes shutting down the event loop.
    await mam_aclose_session()


# ─── App + Router Registration ───────────────────────────────
app = FastAPI(title="AthenaScout", lifespan=lifespan)


# ─── Authentication Middleware ───────────────────────────────
# Routes that don't require authentication. Everything else under /api/
# requires a valid session cookie. The frontend SPA bundle (anything not
# under /api/) is always public so the login page can render.
_PUBLIC_API_PATHS = frozenset({
    "/api/health",
    "/api/platform",
    "/api/auth/setup",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/check",
})


class AuthMiddleware(BaseHTTPMiddleware):
    """Enforce authentication on protected /api/* routes.

    Requests outside /api/ pass through unchanged so the frontend bundle
    (HTML, JS, CSS, images) can always load. API requests in the public
    allowlist also pass through. Every other API request must carry a
    valid signed session cookie.

    Also forces `Cache-Control: no-store` on every /api/* response. API
    payloads are dynamic (scan progress, library state, etc.) and must
    never be served from the browser HTTP cache. Without this header,
    FileResponse heuristics or upstream caches can poison API URLs with
    a stale `index.html` body — observed in the wild when an unauth'd
    request fell through the SPA fallback once and the browser then
    cached that HTML against the API URL, silently breaking polling.
    """
    async def dispatch(self, request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        if path in _PUBLIC_API_PATHS:
            response = await call_next(request)
        else:
            token = request.cookies.get(SESSION_COOKIE_NAME, "")
            if verify_session_token(token) is None:
                response = JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required"},
                )
            else:
                response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response


app.add_middleware(AuthMiddleware)


# All API routes live in app/routers/ — see individual files for endpoints.
# `auth` is registered first by convention since it gates everything else.
for r in (auth, config, libraries, books, authors, series, suggestions, covers, scan, mam, db_editor, import_export):
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

    # ─── SPA fallback whitelist ───────────────────────────
    # Top-level files that vite emits to dist/. Built ONCE at startup
    # from the trusted dist directory; user input is only used as a
    # dict key, never concatenated into a path. CodeQL recognizes the
    # membership lookup as a sanitizer for py/path-injection.
    #
    # This replaces the earlier resolve()/is_relative_to() approach,
    # which was functionally correct (verified by smoke tests blocking
    # /etc/passwd, /proc/self/environ, etc.) but which CodeQL's data-
    # flow analysis couldn't recognize as safe. The whitelist pattern
    # is simpler AND CodeQL-friendly. Phase 22B.3 Stage 2 follow-up.
    _INDEX_HTML = (FD / "index.html").resolve()
    _SERVE_FE_FILES: dict[str, Path] = {
        p.name: p.resolve() for p in FD.iterdir() if p.is_file()
    }

    @app.get("/{path:path}")
    async def serve_fe(path: str):
        """SPA fallback handler.

        Top-level files emitted by vite (index.html, icon.png, icon.svg,
        favicon, etc.) are served from a startup-computed whitelist.
        Anything else — including all client-side SPA routes — falls
        through to index.html so the React router can take over.

        Two important guards:

        1. Paths that look like API calls (`api/...`) get a real 404
           instead of the SPA index. Without this, a request to a
           non-existent or not-yet-registered API route would receive
           `index.html`, and browsers happily cache that against the
           API URL — silently breaking subsequent fetches to the same
           URL even after the route exists. We hit this exact bug with
           polling fetches to /api/lookup/status during scan progress.
        2. The SPA index is served with `Cache-Control: no-cache` so
           the browser must revalidate on every request. The hashed
           assets under /assets/ are still cacheable (mounted via
           StaticFiles, unaffected by this).

        Security: user input is ONLY used as a key into the
        `_SERVE_FE_FILES` dict, which is built from `FD.iterdir()` at
        startup. The path values served to FileResponse always come
        from the trusted set, never from string concatenation with
        user input. This makes path traversal structurally impossible
        and is a pattern CodeQL's py/path-injection rule recognizes
        as a sanitizer.
        """
        if path.startswith("api/") or path == "api":
            raise HTTPException(status_code=404, detail="Not Found")
        safe_file = _SERVE_FE_FILES.get(path)
        if safe_file is not None:
            return FileResponse(safe_file)
        return FileResponse(_INDEX_HTML, headers={"Cache-Control": "no-cache"})
elif IS_STANDALONE:
    @app.get("/{path:path}")
    async def serve_fe_missing(path: str):
        return {"error": "Frontend not built. Run 'cd frontend && npm install && npm run build' first."}
