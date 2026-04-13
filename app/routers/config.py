"""
App-level config and status endpoints.

  GET  /api/settings        — current saved settings (DEFAULT_SETTINGS
                              merged with user overrides)
  POST /api/settings        — partial update; merged into settings.json
  POST /api/settings/reset  — wipe back to defaults
  GET  /api/health          — liveness probe (public, no auth)
  GET  /api/platform        — runtime mode + OS info for the setup wizard
  GET  /api/stats           — Dashboard stats (counts, last sync time, etc.)
"""
import logging
import time
from pathlib import Path
from fastapi import APIRouter, Body

from app.config import (
    LANGUAGE_OPTIONS,
    load_settings,
    save_settings,
    apply_logging,
    get_extra_mount_paths,
)
from app.database import get_db, get_active_library, HF
from app.lookup import reload_sources
from app.sources.mam import get_mam_stats
from app import state

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api", tags=["config"])


# ─── Settings ────────────────────────────────────────────────
@router.get("/settings")
async def get_settings():
    s = load_settings()
    d = dict(s)
    if d.get("hardcover_api_key"):
        d["hardcover_api_key_set"] = True
        d["hardcover_api_key"] = d["hardcover_api_key"][:8] + "..."
    else:
        d["hardcover_api_key_set"] = False
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
        for l in state._discovered_libraries
    ]
    return d


@router.post("/settings")
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
    save_settings(cur)
    reload_sources()
    apply_logging(cur.get("verbose_logging", False))
    return {"status": "ok"}


@router.post("/settings/reset")
async def reset_settings():
    """Reset all settings to factory defaults."""
    from app.config import DEFAULT_SETTINGS
    fresh = dict(DEFAULT_SETTINGS)
    save_settings(fresh)
    reload_sources()
    apply_logging(False)
    logger.info("All settings reset to defaults")
    return {"status": "ok"}


# ─── Health & Stats ──────────────────────────────────────────
@router.get("/health")
async def health():
    return {"status": "ok", "time": time.time()}


@router.get("/version")
async def version_info():
    """Return the build version (git SHA) baked into the Docker image."""
    from pathlib import Path
    version_file = Path("/app/VERSION")
    sha = version_file.read_text().strip() if version_file.exists() else "dev"
    return {"sha": sha, "short_sha": sha[:7] if len(sha) > 7 else sha}


@router.get("/platform")
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
        not state._discovered_libraries
        and not s.get("library_sources")
        and not s.get("setup_complete")
    )
    # Check which suggested default paths actually exist on this system
    info["existing_default_paths"] = [
        p for p in info["default_library_paths"]
        if Path(p["path"]).exists()
    ]
    return info


@router.get("/stats")
async def get_stats():
    db = await get_db()
    try:
        g = lambda sql: db.execute(sql)
        # Match the Authors page browse view (routers/authors.py:get_authors)
        # by excluding orphans. Otherwise the Dashboard's author count is
        # higher than the Authors page list and looks like a bug to users.
        authors = (await (await g("SELECT COUNT(*) c FROM authors WHERE id IN (SELECT DISTINCT author_id FROM books)")).fetchone())["c"]
        total = (await (await g(f"SELECT COUNT(*) c FROM books b WHERE {HF}")).fetchone())["c"]
        owned = (await (await g(f"SELECT COUNT(*) c FROM books b WHERE owned=1 AND {HF}")).fetchone())["c"]
        missing = (await (await g(f"SELECT COUNT(*) c FROM books b WHERE owned=0 AND {HF}")).fetchone())["c"]
        new = (await (await g(f"SELECT COUNT(*) c FROM books b WHERE is_new=1 AND owned=0 AND {HF}")).fetchone())["c"]
        upcoming = (await (await g(f"SELECT COUNT(*) c FROM books b WHERE is_unreleased=1 AND owned=0 AND {HF}")).fetchone())["c"]
        series = (await (await g("SELECT COUNT(*) c FROM series")).fetchone())["c"]
        hidden = (await (await g("SELECT COUNT(*) c FROM books WHERE hidden=1")).fetchone())["c"]
        # Pull the most recent library-sync row from sync_log. The set
        # of "library sync" types is whatever's currently registered in
        # the library_apps registry, so this query stays correct as new
        # backends land — no future code change needed.
        from app.library_apps import get_all_apps
        lib_types = list(get_all_apps().keys())
        if lib_types:
            placeholders = ",".join("?" * len(lib_types))
            ls = await (await db.execute(
                f"SELECT * FROM sync_log WHERE sync_type IN ({placeholders}) "
                f"ORDER BY started_at DESC LIMIT 1",
                lib_types,
            )).fetchone()
        else:
            ls = None
        ll = await (await g("SELECT * FROM sync_log WHERE sync_type='lookup' ORDER BY started_at DESC LIMIT 1")).fetchone()
        s = load_settings()
        mam_stats = None
        if s.get("mam_enabled") and s.get("mam_session_id"):
            mam_stats = await get_mam_stats(db)
        active_lib = get_active_library()
        lib_info = next((l for l in state._discovered_libraries if l["slug"] == active_lib), None)
        return {"authors": authors, "total_books": total, "owned_books": owned, "missing_books": missing, "new_books": new, "upcoming_books": upcoming, "total_series": series, "hidden_books": hidden, "last_library_sync": dict(ls) if ls else None, "last_lookup": dict(ll) if ll else None, "calibre_web_url": s.get("calibre_web_url", ""), "calibre_url": s.get("calibre_url", ""), "mam": mam_stats, "mam_enabled": s.get("mam_enabled", False), "mam_scanning_enabled": s.get("mam_scanning_enabled", True), "author_scanning_enabled": s.get("author_scanning_enabled", True), "active_library": active_lib, "active_library_name": lib_info["name"] if lib_info else active_lib, "library_count": len(state._discovered_libraries), "active_content_type": lib_info.get("content_type", "ebook") if lib_info else "ebook", "active_app_type": lib_info.get("app_type", "calibre") if lib_info else "calibre", "last_library_sync_check": state._last_library_sync_check}
    finally:
        await db.close()
