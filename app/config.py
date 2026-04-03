import os
import json
from pathlib import Path

CALIBRE_DB_PATH = os.getenv("CALIBRE_DB_PATH", "/calibre/metadata.db")
CALIBRE_LIBRARY_PATH = os.getenv("CALIBRE_LIBRARY_PATH", "/calibre")
SYNC_INTERVAL_MINUTES = int(os.getenv("SYNC_INTERVAL_MINUTES", "60"))
LOOKUP_INTERVAL_MINUTES = int(os.getenv("LOOKUP_INTERVAL_MINUTES", "4320"))
MAM_SESSION_ID = os.getenv("MAM_SESSION_ID", "")
MAM_SKIP_IP_UPDATE = os.getenv("MAM_SKIP_IP_UPDATE", "false").lower() == "true"
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
    "calibre_web_url": "",
    "calibre_url": "",
    "mam_session_id": "",
    "mam_enabled": False,
    "mam_skip_ip_update": False,
    "mam_scan_interval_minutes": 360,
    "mam_format_priority": ["epub", "azw3", "mobi", "kfx", "pdf", "html"],
    "rate_mam": 2,
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
            # Env vars always override when set (Docker-idiomatic)
            _apply_env_overrides(merged)
            return merged
        except Exception:
            pass
    # First run — start from defaults
    settings = dict(DEFAULT_SETTINGS)
    _apply_env_overrides(settings)
    save_settings(settings)
    return settings


def _apply_env_overrides(settings: dict):
    """Apply env vars only when the setting is empty/unset — never overwrite user customizations."""
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
    if MAM_SKIP_IP_UPDATE and not settings.get("mam_skip_ip_update"):
        settings["mam_skip_ip_update"] = True


def save_settings(settings: dict):
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)
