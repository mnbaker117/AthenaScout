import os
import re as _re
import json
import logging
from pathlib import Path

_cfg_logger = logging.getLogger("athenascout.config")

CALIBRE_PATH = os.getenv("CALIBRE_PATH", "")
CALIBRE_DB_PATH = os.getenv("CALIBRE_DB_PATH", "/calibre/metadata.db")
CALIBRE_LIBRARY_PATH = os.getenv("CALIBRE_LIBRARY_PATH", "/calibre")
SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "60"))
LOOKUP_INTERVAL_MINUTES = int(os.getenv("LOOKUP_INTERVAL_MINUTES", "4320"))
MAM_SESSION_ID = os.getenv("MAM_SESSION_ID", "")
# MAM_SKIP_IP_UPDATE removed — always True (IP registration skipped)
MAM_SCAN_INTERVAL_MINUTES = int(os.getenv("MAM_SCAN_INTERVAL_MINUTES", "360"))
DATA_DIR = Path(os.getenv("DATA_DIR", "/app/data"))
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

DEFAULT_SETTINGS = {
    "hardcover_api_key": "",
    "fantasticfiction_enabled": False,
    "kobo_enabled": True,
    "theme": "dark",
    "languages": ["English"],
    "lookup_interval_days": 3,
    "calibre_sync_interval_minutes": 60,
    "rate_goodreads": 2,
    "rate_hardcover": 1,
    "rate_fantasticfiction": 2,
    "rate_kobo": 3,
    "verbose_logging": False,
    "author_scanning_enabled": True,
    "calibre_web_url": "",
    "calibre_url": "",
    "mam_session_id": "",
    "mam_enabled": False,
    "mam_scanning_enabled": True,
    "mam_skip_ip_update": True,
    "mam_scan_interval_minutes": 360,
    "mam_format_priority": ["epub", "azw", "azw3", "pdf", "djvu", "azw4"],
    "rate_mam": 2,
    "mam_full_scan_batch_delay_minutes": 60,
    "last_mam_validated_at": None,
    "mam_validation_ok": True,
    "active_library": "",
    "calibre_mtimes": {},
    "library_sources": [],
}


def apply_logging(verbose: bool = False):
    """Configure logging levels based on verbose setting."""
    import logging
    level = logging.DEBUG if verbose else logging.INFO
    # Set source loggers
    for name in ["athenascout", "athenascout.goodreads", "athenascout.hardcover", "athenascout.fantasticfiction",
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


def discover_libraries(settings=None):
    """Find all Calibre libraries. Returns list of dicts.

    Priority:
    1. User-configured library_sources in settings (for future Settings UI)
    2. CALIBRE_PATH env var (recursive scan for metadata.db)
    3. CALIBRE_DB_PATH env var (legacy single-library fallback)
    """
    libraries = []
    seen_slugs = set()

    def _add_library(mdb_path):
        """Add a library from a metadata.db path, deduplicating by slug."""
        parent = mdb_path.parent
        name = parent.name
        slug = slugify(name)
        # Handle duplicate slugs by appending a counter
        base_slug = slug
        counter = 2
        while slug in seen_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        seen_slugs.add(slug)
        libraries.append({
            "name": name,
            "slug": slug,
            "calibre_db_path": str(mdb_path),
            "calibre_library_path": str(parent),
        })

    def _scan_root(root_path):
        """Scan a root directory for metadata.db files (one level deep)."""
        root = Path(root_path)
        if not root.exists():
            _cfg_logger.warning(f"Library root path does not exist: {root_path}")
            return
        # Look for metadata.db in immediate subdirectories
        for child in sorted(root.iterdir()):
            if child.is_dir():
                mdb = child / "metadata.db"
                if mdb.exists():
                    _add_library(mdb)
        # Also check root itself (in case metadata.db is directly in root)
        root_mdb = root / "metadata.db"
        if root_mdb.exists():
            _add_library(root_mdb)

    # Priority 1: User-configured library sources (from Settings UI, Phase 17C)
    if settings and settings.get("library_sources"):
        for src in settings["library_sources"]:
            src_path = src.get("path", "")
            src_type = src.get("type", "root")
            if not src_path:
                continue
            if src_type == "root":
                _scan_root(src_path)
            elif src_type == "direct":
                mdb = Path(src_path)
                if mdb.exists() and mdb.name == "metadata.db":
                    _add_library(mdb)
                else:
                    _cfg_logger.warning(f"Direct library path not found or invalid: {src_path}")
        if libraries:
            return libraries

    # Priority 2: CALIBRE_PATH env var (root scan)
    if CALIBRE_PATH:
        _scan_root(CALIBRE_PATH)
        if libraries:
            return libraries

    # Priority 3: Legacy CALIBRE_DB_PATH (single direct path)
    legacy_mdb = Path(CALIBRE_DB_PATH)
    if legacy_mdb.exists():
        _add_library(legacy_mdb)

    return libraries


def load_settings() -> dict:
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
                merged["rate_fantasticfiction"] = old_rate
                merged["rate_kobo"] = old_rate + 1
            # Env vars only seed on first run — settings.json is source of truth
            return merged
        except Exception:
            pass
    # First run — start from defaults
    settings = dict(DEFAULT_SETTINGS)
    _apply_env_overrides(settings)
    save_settings(settings)
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
