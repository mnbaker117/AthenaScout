"""
Calibre Library App — implementation for Calibre ebook management.

Wraps the existing calibre_sync.py functions behind the LibraryApp interface.
This adapter allows Calibre to work within the multi-app framework without
modifying the proven sync logic.
"""
import os
import logging
from typing import Optional
from app.library_apps.base import LibraryApp

logger = logging.getLogger("athenascout.library_apps.calibre")


class CalibreApp(LibraryApp):
    """Calibre ebook library source.

    Discovers libraries by looking for metadata.db files.
    Syncs by reading Calibre's metadata.db schema.
    Covers are stored as cover.jpg in each book's folder.
    """

    app_type = "calibre"
    content_type = "ebook"
    display_name = "Calibre"
    db_filename = "metadata.db"
    env_root_var = "CALIBRE_PATH"
    env_extra_var = "CALIBRE_EXTRA_PATHS"

    async def sync(self, source_db_path: str, library_path: str) -> dict:
        """Sync Calibre metadata.db into AthenaScout's active database.

        Delegates to the existing sync_calibre() function which handles
        all the complex Calibre schema reading, author/series upsert,
        and book import logic.
        """
        from app.calibre_sync import sync_calibre
        return await sync_calibre(
            calibre_db_path=source_db_path,
            calibre_library_path=library_path,
        )

    def get_cover_path(self, book_path: str, library_path: str) -> Optional[str]:
        """Get Calibre cover path.

        Calibre stores covers as cover.jpg in each book's subdirectory.
        The book_path is relative to the library root.
        """
        if not book_path:
            return None
        candidate = os.path.join(library_path, book_path, "cover.jpg")
        return candidate if os.path.exists(candidate) else None


# ─── Future Template ─────────────────────────────────────────
# To add a new source app (e.g., audiobookshelf), create a file like this:
#
# class AudiobookshelfApp(LibraryApp):
#     app_type = "audiobookshelf"
#     content_type = "audiobook"
#     display_name = "Audiobookshelf"
#     db_filename = "audiobookshelf.db"  # or whatever its DB is named
#     env_root_var = "AUDIOBOOKSHELF_PATH"
#     env_extra_var = "AUDIOBOOKSHELF_EXTRA_PATHS"
#
#     async def sync(self, source_db_path, library_path):
#         # Read audiobookshelf's database and import into AthenaScout
#         pass
#
#     def get_cover_path(self, book_path, library_path):
#         # Audiobookshelf cover logic
#         pass
#
# Then register in __init__.py:
#   from .audiobookshelf import AudiobookshelfApp
#   LIBRARY_APPS["audiobookshelf"] = AudiobookshelfApp()
