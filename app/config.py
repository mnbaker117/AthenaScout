"""
Configuration loading and persistence.

Two layers of config:

  1. **Environment variables** (read once at import time): things the
     deployment owner sets — paths to Calibre, MAM session token,
     scheduler intervals, the WebUI port. These exist so a Docker
     deployment can be configured purely through `docker run -e ...`
     without ever touching the UI.
  2. **Saved settings** (`settings.json` under DATA_DIR): what the user
     can change at runtime through the Settings page. `load_settings`
     always merges the on-disk file over `DEFAULT_SETTINGS`, so every
     key in DEFAULT_SETTINGS is guaranteed to be present on the
     returned dict — see the long invariant comment on DEFAULT_SETTINGS
     below for the rules around adding a new setting.

Library discovery (`discover_libraries`) lives here too because it
walks both env-driven roots and saved-settings-driven roots and the
two need to share resolution logic.
"""
import os
import re as _re
import json
import logging
from pathlib import Path
from app.runtime import IS_DOCKER, get_data_dir

_cfg_logger = logging.getLogger("athenascout.config")

CALIBRE_PATH = os.getenv("CALIBRE_PATH", "")
CALIBRE_EXTRA_PATHS = os.getenv("CALIBRE_EXTRA_PATHS", "")
# In Docker, default to container paths. In standalone, default to empty
# (user configures via Settings UI or setup wizard).
CALIBRE_DB_PATH = os.getenv("CALIBRE_DB_PATH", "/calibre/metadata.db" if IS_DOCKER else "")
CALIBRE_LIBRARY_PATH = os.getenv("CALIBRE_LIBRARY_PATH", "/calibre" if IS_DOCKER else "")
SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "60"))
LOOKUP_INTERVAL_MINUTES = int(os.getenv("LOOKUP_INTERVAL_MINUTES", "4320"))
MAM_SESSION_ID = os.getenv("MAM_SESSION_ID", "")
# MAM_SKIP_IP_UPDATE removed — always True (IP registration skipped)
MAM_SCAN_INTERVAL_MINUTES = int(os.getenv("MAM_SCAN_INTERVAL_MINUTES", "360"))
# DATA_DIR: use explicit env var if set, otherwise OS-appropriate default.
# Docker sets DATA_DIR=/app/data in Dockerfile. Standalone gets OS standard location.
_data_dir_env = os.getenv("DATA_DIR", "")
DATA_DIR = Path(_data_dir_env) if _data_dir_env else get_data_dir()
APP_DB_PATH = DATA_DIR / "athenascout.db"
SETTINGS_PATH = DATA_DIR / "settings.json"

# Migrate from old name if upgrading from Calibre Librarian
_OLD_DB = DATA_DIR / "librarian.db"
if _OLD_DB.exists() and not APP_DB_PATH.exists():
    _OLD_DB.rename(APP_DB_PATH)

# Docker-configurable env vars that seed settings on first run
ENV_HARDCOVER_API_KEY = os.getenv("HARDCOVER_API_KEY", "")
ENV_CALIBRE_WEB_URL = os.getenv("CALIBRE_WEB_URL", "")
ENV_CALIBRE_URL = os.getenv("CALIBRE_URL", "")
ENV_VERBOSE_LOGGING = os.getenv("VERBOSE_LOGGING", "").lower() in ("true", "1", "yes")
ENV_WEBUI_PORT = int(os.getenv("WEBUI_PORT", "8787"))

DATA_DIR.mkdir(parents=True, exist_ok=True)

LANGUAGE_OPTIONS = [
    "Afrikaans", "Albanian", "Arabic", "Armenian", "Basque", "Bengali",
    "Bulgarian", "Catalan", "Chinese", "Croatian", "Czech", "Danish",
    "Dutch", "English", "Estonian", "Filipino", "Finnish", "French",
    "Galician", "Georgian", "German", "Greek", "Gujarati", "Hebrew",
    "Hindi", "Hungarian", "Icelandic", "Indonesian", "Irish", "Italian",
    "Japanese", "Kannada", "Korean", "Latin", "Latvian", "Lithuanian",
    "Macedonian", "Malay", "Malayalam", "Maltese", "Marathi", "Mongolian",
    "Norwegian", "Persian", "Polish", "Portuguese", "Punjabi", "Romanian",
    "Russian", "Serbian", "Slovak", "Slovenian", "Spanish", "Swahili",
    "Swedish", "Tamil", "Telugu", "Thai", "Turkish", "Ukrainian", "Urdu",
    "Vietnamese", "Welsh",
]

# ─── DEFAULT_SETTINGS — canonical source of truth for every setting ──
#
# `load_settings` merges saved settings into DEFAULT_SETTINGS via
# `{**DEFAULT_SETTINGS, **saved}`, so every key listed here is
# guaranteed to be present on every dict returned. The inline `.get(key,
# fallback)` calls scattered across the routers are defensive
# redundancy — they should never actually fall back.
#
# INVARIANT for adding a new setting:
#   1. Add it here first.
#   2. Inline `.get("new_key", FALLBACK)` calls in routers MUST use the
#      same FALLBACK value as the entry in this dict. Mismatched
#      defaults silently diverge for users whose `settings.json` was
#      written before the key existed — those users see the inline
#      fallback while everyone else sees the dict default.
DEFAULT_SETTINGS = {
    "hardcover_api_key": "",
    "goodreads_enabled": True,
    "hardcover_enabled": True,
    "kobo_enabled": True,
    "amazon_enabled": False,
    "ibdb_enabled": False,
    "google_books_enabled": False,
    "theme": "dark",
    "languages": ["English"],
    "lookup_interval_days": 3,
    "library_sync_interval_minutes": 60,
    "rate_goodreads": 2,
    "rate_hardcover": 1,
    "rate_kobo": 3,
    "rate_amazon": 2,
    "rate_ibdb": 1,
    "rate_google_books": 1.5,
    "verbose_logging": False,
    "author_scanning_enabled": True,
    # When True, source scans only enrich metadata on books the user already
    # owns in Calibre — no new "missing" / "upcoming" book rows are created.
    # Lets the user fully populate metadata on their existing library before
    # turning the discovery firehose on. Default off preserves existing behavior.
    "author_scan_owned_only": False,
    # Filter out audiobook-only editions, narrator credits, and non-ebook
    # formats during source scans. Prevents audiobook entries from cluttering
    # an ebook-focused library.
    "exclude_audiobooks": True,
    "hermeece_url": "",
    "hermeece_api_key": "",
    "ntfy_url": "",
    "ntfy_topic": "",
    "ntfy_on_scan_complete": True,
    "ntfy_on_new_books": True,
    "ntfy_on_mam_complete": True,
    "ntfy_on_hermeece_sent": True,
    "ntfy_on_library_sync": False,
    "ntfy_on_mam_cookie_rotated": False,
    "ntfy_digest_enabled": False,
    "ntfy_digest_schedule": "daily",
    "calibre_web_url": "",
    "calibre_url": "",
    "mam_session_id": "",
    "mam_enabled": False,
    "mam_scanning_enabled": True,
    "mam_skip_ip_update": True,
    "mam_scan_interval_minutes": 360,
    "mam_format_priority": ["epub", "azw", "azw3", "pdf", "djvu", "azw4"],
    "rate_mam": 2,
    "last_mam_validated_at": None,
    "mam_validation_ok": True,
    "active_library": "",
    "library_mtimes": {},
    "library_sources": [],
    "setup_complete": False,
}


def apply_logging(verbose: bool = False):
    """Configure logging levels based on verbose setting."""
    import logging
    level = logging.DEBUG if verbose else logging.INFO
    # Set source loggers
    for name in ["athenascout", "athenascout.goodreads", "athenascout.hardcover",
                 "athenascout.kobo", "athenascout.lookup", "athenascout.calibre_sync", "athenascout.mam"]:
        logging.getLogger(name).setLevel(level)
    # Keep httpx at INFO always (too noisy at DEBUG)
    logging.getLogger("httpx").setLevel(logging.INFO)
    logging.getLogger("athenascout").info(f"Logging set to {'VERBOSE (DEBUG)' if verbose else 'NORMAL (INFO)'}")


def slugify(name):
    """Convert a folder name to a safe slug for DB filenames."""
    s = name.lower().strip()
    s = _re.sub(r'[^a-z0-9]+', '-', s)
    s = s.strip('-')
    return s or 'default'


def get_extra_mount_paths():
    """Collect extra mount paths from all registered library apps.

    Each app can define its own EXTRA_PATHS env var. All valid paths
    are merged into a single list for the Settings UI.
    """
    from app.library_apps import get_all_apps
    all_paths = []
    # Include paths from all registered apps
    for app_type, app in get_all_apps().items():
        for p in app.get_extra_paths():
            if p not in all_paths:
                all_paths.append(p)
    # Also include legacy CALIBRE_EXTRA_PATHS for backward compat
    if CALIBRE_EXTRA_PATHS:
        for p in [x.strip() for x in CALIBRE_EXTRA_PATHS.split(",") if x.strip()]:
            try:
                exists = Path(p).exists()
            except (PermissionError, OSError):
                # Python 3.12+ raises rather than returning False on
                # permission errors; treat as "not visible" so a single
                # unreadable extra path doesn't crash startup.
                exists = False
            if exists and p not in all_paths:
                all_paths.append(p)
    return all_paths


def discover_libraries(settings=None):
    """Find all libraries from all registered source apps. Returns list of dicts.

    Priority:
    1. User-configured library_sources in settings
    2. Registered library apps (each checks its own env var)
    3. CALIBRE_DB_PATH env var (legacy single-library fallback)

    Each library dict includes: name, slug, app_type, content_type,
    display_name, source_db_path, library_path
    """
    from app.library_apps import get_all_apps

    libraries = []
    seen_slugs = set()

    def _add_library(lib_dict):
        """Add a library, deduplicating by slug."""
        slug = lib_dict["slug"]
        base_slug = slug
        counter = 2
        while slug in seen_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        seen_slugs.add(slug)
        lib_dict["slug"] = slug
        libraries.append(lib_dict)

    # Priority 1: User-configured library sources (from Settings UI)
    if settings and settings.get("library_sources"):
        for src in settings["library_sources"]:
            src_path = src.get("path", "")
            src_type = src.get("type", "root")
            src_app = src.get("app_type", "calibre")
            if not src_path:
                continue
            app = get_all_apps().get(src_app)
            if not app:
                _cfg_logger.warning(f"Unknown app type '{src_app}' in library_sources, skipping")
                continue
            if src_type == "root":
                for lib in app.discover(src_path):
                    _add_library(lib)
            elif src_type == "direct":
                mdb = Path(src_path)
                try:
                    mdb_exists = mdb.exists()
                except (PermissionError, OSError) as e:
                    _cfg_logger.warning(
                        f"Direct library path unreadable (permission denied): {src_path} ({e})"
                    )
                    mdb_exists = False
                if mdb_exists and mdb.name == app.db_filename:
                    _add_library({
                        "name": mdb.parent.name,
                        "slug": slugify(mdb.parent.name),
                        "app_type": app.app_type,
                        "content_type": app.content_type,
                        "display_name": app.display_name,
                        "source_db_path": str(mdb),
                        "library_path": str(mdb.parent),
                    })
                else:
                    _cfg_logger.warning(f"Direct library path not found or invalid: {src_path}")
        if libraries:
            return libraries

    # Priority 2: Registered library apps (each checks its env var)
    for app_type, app in get_all_apps().items():
        root_path = app.get_root_path()
        if root_path:
            found = app.discover(root_path)
            for lib in found:
                _add_library(lib)

    if libraries:
        return libraries

    # Priority 3: Legacy CALIBRE_DB_PATH (single direct path)
    if CALIBRE_DB_PATH:
        legacy_mdb = Path(CALIBRE_DB_PATH)
        try:
            legacy_exists = legacy_mdb.exists()
        except (PermissionError, OSError) as e:
            _cfg_logger.warning(
                f"Legacy CALIBRE_DB_PATH unreadable: {CALIBRE_DB_PATH} ({e})"
            )
            legacy_exists = False
        if legacy_exists:
            _add_library({
                "name": legacy_mdb.parent.name,
                "slug": slugify(legacy_mdb.parent.name),
                "app_type": "calibre",
                "content_type": "ebook",
                "display_name": "Calibre",
                "source_db_path": str(legacy_mdb),
                "library_path": str(legacy_mdb.parent),
            })

    return libraries


# ─── Settings cache ──────────────────────────────────────────
# `load_settings()` used to open+parse the JSON file on every call. The
# MAM scheduler, lookup engine, and most routers call it multiple times
# per request (lookup.py alone hits it 4+ times per author during scans),
# so we were doing dozens of disk reads per second on large scans.
#
# Now we cache the parsed dict keyed by the file's mtime. Any call site
# that calls `save_settings()` will bump the mtime, which invalidates the
# cache on the next `load_settings()` call automatically — no explicit
# invalidation hook required. Handles the first-run case (no file yet)
# by caching under mtime=None until the file appears.
_settings_cache: dict = {"mtime": object(), "data": None}


def load_settings() -> dict:
    # Stat the settings file once per call. Cheap (single syscall) and
    # lets us serve the cached dict without re-reading when nothing has
    # changed since last call.
    try:
        cur_mtime = SETTINGS_PATH.stat().st_mtime if SETTINGS_PATH.exists() else None
    except OSError:
        cur_mtime = None

    if _settings_cache["data"] is not None and cur_mtime == _settings_cache["mtime"]:
        return _settings_cache["data"]

    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH) as f:
                saved = json.load(f)
            merged = {**DEFAULT_SETTINGS, **saved}
            # Migrate old global rate limit
            if "rate_limit_delay_seconds" in merged and "rate_goodreads" not in saved:
                old_rate = merged.pop("rate_limit_delay_seconds", 2)
                merged["rate_goodreads"] = old_rate
                merged["rate_hardcover"] = max(1, old_rate - 1)
                merged["rate_kobo"] = old_rate + 1
            # Strip orphaned FantasticFiction settings if present in old settings.json
            for k in ("fantasticfiction_enabled", "rate_fantasticfiction"):
                merged.pop(k, None)
            # ── calibre_* → library_* settings rename ────────────
            # The framework's library backend layer is generic, so the
            # settings keys that hold backend-agnostic state (sync
            # interval, mtimes-per-library) are renamed accordingly.
            # `calibre_web_url` and `calibre_url` are NOT migrated —
            # they specifically point at Calibre-Web / Calibre, not
            # at the active library backend.
            #
            # Idempotent: only migrates when the old key exists AND
            # the new key is absent. After the first migrated load,
            # the old keys are gone and this block becomes a no-op.
            settings_dirty = False
            _RENAMES = {
                "calibre_sync_interval_minutes": "library_sync_interval_minutes",
                "calibre_mtimes": "library_mtimes",
            }
            for old_key, new_key in _RENAMES.items():
                if old_key in saved and new_key not in saved:
                    merged[new_key] = saved[old_key]
                    merged.pop(old_key, None)
                    settings_dirty = True
                    _cfg_logger.info(
                        f"Settings migration: renamed '{old_key}' → '{new_key}'"
                    )
                elif old_key in merged:
                    # Old key snuck back in via DEFAULT_SETTINGS merge
                    # path or stale cache; drop it.
                    merged.pop(old_key, None)
                    settings_dirty = True
            if settings_dirty:
                # Persist the rename so the next startup doesn't
                # re-run the migration. save_settings() also warms
                # the cache with the new dict.
                save_settings(merged)
                try:
                    cur_mtime = SETTINGS_PATH.stat().st_mtime
                except OSError:
                    cur_mtime = None
            # Env vars only seed on first run — settings.json is source of truth
            _settings_cache["data"] = merged
            _settings_cache["mtime"] = cur_mtime
            return merged
        except Exception:
            pass
    # First run — start from defaults
    settings = dict(DEFAULT_SETTINGS)
    _apply_env_overrides(settings)
    save_settings(settings)
    # save_settings() bumps the mtime; re-stat so the cache key matches.
    try:
        _settings_cache["mtime"] = SETTINGS_PATH.stat().st_mtime
    except OSError:
        _settings_cache["mtime"] = None
    _settings_cache["data"] = settings
    return settings


def _apply_env_overrides(settings: dict):
    """Seed settings from Docker env vars on first run only. After settings.json exists, this is never called."""
    if ENV_HARDCOVER_API_KEY and not settings.get("hardcover_api_key"):
        settings["hardcover_api_key"] = ENV_HARDCOVER_API_KEY
    if ENV_CALIBRE_WEB_URL and not settings.get("calibre_web_url"):
        settings["calibre_web_url"] = ENV_CALIBRE_WEB_URL
    if ENV_CALIBRE_URL and not settings.get("calibre_url"):
        settings["calibre_url"] = ENV_CALIBRE_URL
    if ENV_VERBOSE_LOGGING and not settings.get("verbose_logging"):
        settings["verbose_logging"] = True
    if MAM_SESSION_ID and not settings.get("mam_session_id"):
        settings["mam_session_id"] = MAM_SESSION_ID
    # mam_skip_ip_update is always True — IP registration can
    # interfere with seedbox sessions regardless of lock type


def save_settings(settings: dict):
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)
    # Warm the cache with the just-saved dict so an immediate subsequent
    # load_settings() in the same process returns fresh data without
    # relying on mtime precision (some filesystems are 1-second granular
    # and rapid save→load sequences could otherwise serve stale data).
    try:
        _settings_cache["mtime"] = SETTINGS_PATH.stat().st_mtime
    except OSError:
        _settings_cache["mtime"] = None
    _settings_cache["data"] = dict(settings)
