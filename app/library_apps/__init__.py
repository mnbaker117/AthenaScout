"""
Library app registry — central registry of supported library backends.

AthenaScout's discovery system, sync engine, and UI all hang off this
registry: anything registered here automatically becomes a candidate
backend that the user can point at a library directory for. The
current public release ships only the Calibre backend, but the
`LibraryApp` interface in `base.py` is deliberately small so that
adding new backends is a self-contained change.

Candidate ebook backends a future contributor could implement:

  - **Alfa**            (ebook management)
  - **Kavita**          (ebook side)
  - **Komga**           (ebook + comics)
  - **Audiobookshelf**  (ebook side)

Candidate audiobook backends:

  - **Audiobookshelf**  (audiobook side)
  - **Kavita**          (audiobook side)
  - **Libation**        (Audible audiobook downloader / library)
  - **OpenAudible**     (Audible audiobook manager)

To add a new backend:
  1. Create a new file in this directory (e.g. `audiobookshelf.py`).
  2. Implement the `LibraryApp` interface from `base.py`.
  3. Add the import + a registry entry below.

The `app_type` string used as the dict key shows up in the `books`,
`authors`, and `series` row `source` columns and in the library
discovery JSON, so keep it short, lowercase, and stable once chosen.
"""
from app.library_apps.calibre import CalibreApp

# ─── Registry ────────────────────────────────────────────────
# Each key is the `app_type` string used throughout the system.
# Each value is an instance of a `LibraryApp` subclass.
LIBRARY_APPS = {
    "calibre": CalibreApp(),
    # Future backends slot in here. See module docstring above for the
    # candidate list of ebook and audiobook apps the framework is
    # designed to accept.
}


def get_app(app_type):
    """Get a registered library app by type string."""
    return LIBRARY_APPS.get(app_type)


def get_all_apps():
    """Get all registered library apps."""
    return LIBRARY_APPS
