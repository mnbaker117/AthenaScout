"""
Library app base class — the interface every library backend implements.

Each library app represents a different ebook or audiobook management
application that AthenaScout can sync from. The current public release
ships only the Calibre backend (`calibre.py`), but the interface is
deliberately small so adding new backends is a self-contained change.
The list of candidate future backends — Alfa, Kavita, Komga,
Audiobookshelf, Libation, OpenAudible — is in `__init__.py`.

A library app defines:
  - How to discover libraries under a root path
  - How to sync data from the source database into AthenaScout
  - How to locate cover images for books
  - What content type it manages (ebook, audiobook)
  - What environment variables it reads for default paths

The discovery loop in `app/config.py:discover_libraries` walks every
registered app and asks it to scan its `env_root_var` for libraries,
so a new backend "just works" the moment it's registered — no changes
needed in the discovery layer, the sync orchestrator, or the UI.
"""
import os
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

logger = logging.getLogger("athenascout.library_apps")


class LibraryApp(ABC):
    """Abstract base class for library source applications.

    Subclasses must define class attributes and implement abstract methods.

    Class Attributes:
        app_type (str): Unique identifier, e.g. "calibre", "audiobookshelf"
        content_type (str): "ebook" or "audiobook"
        display_name (str): Human-readable name, e.g. "Calibre"
        db_filename (str): Database file to look for, e.g. "metadata.db"
        env_root_var (str): Env var for primary root path, e.g. "CALIBRE_PATH"
        env_extra_var (str): Env var for extra mount paths, e.g. "CALIBRE_EXTRA_PATHS"
    """

    app_type: str = ""
    content_type: str = "ebook"  # "ebook" or "audiobook"
    display_name: str = ""
    db_filename: str = ""
    env_root_var: str = ""
    env_extra_var: str = ""

    # ─── Discovery ────────────────────────────────────────

    def get_root_path(self) -> str:
        """Get the primary root path from the environment variable."""
        return os.getenv(self.env_root_var, "")

    def get_extra_paths(self) -> list:
        """Parse the extra paths environment variable into a list."""
        raw = os.getenv(self.env_extra_var, "")
        if not raw:
            return []
        paths = [p.strip() for p in raw.split(",") if p.strip()]
        valid = []
        for p in paths:
            try:
                exists = Path(p).exists()
            except (PermissionError, OSError) as e:
                # Py 3.12+ raises on permission errors; treat as "invisible"
                # so startup doesn't crash on a single unreadable extra path.
                logger.warning(
                    f"{self.display_name}: extra path unreadable ({e}): {p}"
                )
                exists = False
            if exists:
                valid.append(p)
            else:
                logger.warning(f"{self.display_name}: extra path does not exist: {p}")
        return valid

    def discover(self, root_path: str) -> list:
        """Discover libraries under a root path.

        Scans one level deep for directories containing self.db_filename.

        Must be robust to directories the runtime user cannot read — in
        Python 3.12+ `Path.exists()` *propagates* PermissionError rather
        than silently returning False, so a single unreadable child
        (e.g. `/calibre/.dbus/` owned by a different host user) will
        crash the entire scan unless we catch it here. This bit us
        immediately after switching the container to a non-root user.

        Also skips hidden directories (names starting with `.`) because
        they're never legitimate libraries and reliably trigger
        permission traps from apps that leave state in appdata trees
        (.dbus, .cache, .Trash, .config, etc).

        Args:
            root_path: Directory to scan

        Returns:
            List of library dicts with keys:
                name, slug, app_type, content_type, display_name,
                source_db_path, library_path
        """
        from app.config import slugify
        libraries = []
        seen_slugs = set()
        root = Path(root_path)

        try:
            if not root.exists():
                logger.warning(f"{self.display_name}: root path does not exist: {root_path}")
                return []
        except PermissionError:
            logger.warning(
                f"{self.display_name}: cannot stat root path (permission denied): {root_path}"
            )
            return []

        def _add(mdb_path):
            parent = mdb_path.parent
            name = parent.name
            slug = slugify(name)
            base_slug = slug
            counter = 2
            while slug in seen_slugs:
                slug = f"{base_slug}-{counter}"
                counter += 1
            seen_slugs.add(slug)
            libraries.append({
                "name": name,
                "slug": slug,
                "app_type": self.app_type,
                "content_type": self.content_type,
                "display_name": self.display_name,
                "source_db_path": str(mdb_path),
                "library_path": str(parent),
            })

        def _safe_db_exists(db_file: Path) -> bool:
            """Stat a candidate DB file, swallowing permission errors.

            Python 3.12+ made Path.exists() propagate PermissionError; we
            treat those as 'not a library here' and move on rather than
            aborting the whole discovery scan.
            """
            try:
                return db_file.exists()
            except (PermissionError, OSError) as e:
                logger.debug(
                    f"{self.display_name}: skipping {db_file} (unreadable: {e})"
                )
                return False

        # Scan immediate subdirectories
        try:
            children = sorted(root.iterdir())
        except (PermissionError, OSError) as e:
            logger.warning(
                f"{self.display_name}: cannot list {root_path} ({e}) — "
                "returning empty library list"
            )
            return []

        for child in children:
            # Skip hidden/dot directories — never real libraries, often
            # unreadable as a non-root user (.dbus, .cache, .Trash, ...).
            if child.name.startswith("."):
                continue
            try:
                is_dir = child.is_dir()
            except (PermissionError, OSError):
                continue
            if is_dir:
                db_file = child / self.db_filename
                if _safe_db_exists(db_file):
                    _add(db_file)

        # Check root itself
        root_db = root / self.db_filename
        if _safe_db_exists(root_db):
            _add(root_db)

        return libraries

    # ─── Sync ─────────────────────────────────────────────

    @abstractmethod
    async def sync(self, source_db_path: str, library_path: str) -> dict:
        """Sync from the source database into the active AthenaScout database.

        This should read the source application's database and upsert
        books, authors, and series into AthenaScout's database.

        Args:
            source_db_path: Path to the source app's database file
            library_path: Path to the library root directory

        Returns:
            Dict with sync results, e.g. {"books_found": 100, "books_new": 5}
        """
        pass

    # ─── Covers ───────────────────────────────────────────

    @abstractmethod
    def get_cover_path(self, book_path: str, library_path: str) -> Optional[str]:
        """Get the filesystem path to a book's cover image.

        Args:
            book_path: Book-relative path (from the source database)
            library_path: Library root directory

        Returns:
            Full path to cover image file, or None if not found
        """
        pass

    # ─── Timestamps ───────────────────────────────────────

    def get_mtime(self, source_db_path: str) -> float:
        """Get the modification timestamp of the source database.

        Used for mtime optimization — skip sync if unchanged.
        """
        try:
            return os.path.getmtime(source_db_path)
        except OSError:
            return 0.0
