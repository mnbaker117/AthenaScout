"""
Library App Registry — central registry of all supported library source applications.

To add a new source app:
1. Create a new file in this directory (e.g., audiobookshelf.py)
2. Implement the LibraryApp interface from base.py
3. Import and register it in this file

The discovery system, sync engine, and UI automatically pick up registered apps.
"""
from app.library_apps.calibre import CalibreApp

# ─── Registry ────────────────────────────────────────────────
# Each key is the app_type string used throughout the system.
# Each value is an instance of a LibraryApp subclass.
LIBRARY_APPS = {
    "calibre": CalibreApp(),
    # Future source apps:
    # "audiobookshelf": AudiobookshelfApp(),
    # "epubor": EpuborApp(),
    # "libation": LibationApp(),
}


def get_app(app_type):
    """Get a registered library app by type string."""
    return LIBRARY_APPS.get(app_type)


def get_all_apps():
    """Get all registered library apps."""
    return LIBRARY_APPS
