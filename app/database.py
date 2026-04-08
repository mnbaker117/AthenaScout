"""
Database layer for AthenaScout.
"""
import logging
import aiosqlite
from app.config import APP_DB_PATH, DATA_DIR

_db_logger = logging.getLogger("athenascout.database")

# Common SQL filter constant used by routes that query books.
# Excludes hidden books from results. Apply as:
#     WHERE {HF} AND other_conditions...
HF = "b.hidden = 0"

# ─── Active Library Tracking ─────────────────────────────────
_active_library_slug = None


def set_active_library(slug):
    """Set the active library slug. All get_db() calls will use this library."""
    global _active_library_slug
    _active_library_slug = slug
    _db_logger.debug(f"Active library set to: {slug}")


def get_active_library():
    """Get the current active library slug."""
    return _active_library_slug


def get_db_path(slug=None):
    """Get the database file path for a library slug.

    If slug is provided, returns the per-library path.
    If slug is None, uses the active library slug.
    If no active library is set, falls back to the legacy APP_DB_PATH.
    """
    effective_slug = slug or _active_library_slug
    if effective_slug:
        return DATA_DIR / f"athenascout_{effective_slug}.db"
    return APP_DB_PATH


def migrate_legacy_db(target_slug):
    """Rename legacy athenascout.db to the per-library filename.

    Called once during startup when migrating from single-library to multi-library.
    Only renames if the legacy file exists and the target does not.
    Returns the slug the DB was migrated to, or None if no migration occurred.
    """
    legacy = APP_DB_PATH  # /app/data/athenascout.db
    if not legacy.exists():
        return None
    target = DATA_DIR / f"athenascout_{target_slug}.db"
    if not target.exists():
        legacy.rename(target)
        _db_logger.info(f"Migrated legacy database → athenascout_{target_slug}.db")
        return target_slug
    return None


def match_legacy_db_to_library(libraries):
    """Determine which discovered library the legacy athenascout.db belongs to.

    Counts books in the legacy DB and each Calibre metadata.db, then picks
    the library whose book count is closest. This prevents assigning a 2700-book
    DB to a 17-book library just because of alphabetical ordering.

    Returns the best-matching library slug, or the first library's slug as fallback.
    """
    import sqlite3

    legacy = APP_DB_PATH
    if not legacy.exists() or len(libraries) <= 1:
        return libraries[0]["slug"] if libraries else "default"

    # Count books in the legacy AthenaScout DB
    try:
        conn = sqlite3.connect(f"file:{legacy}?mode=ro", uri=True)
        legacy_count = conn.execute("SELECT COUNT(*) FROM books WHERE source='calibre'").fetchone()[0]
        conn.close()
    except Exception as e:
        _db_logger.warning(f"Could not read legacy DB for migration matching: {e}")
        return libraries[0]["slug"]

    _db_logger.info(f"Legacy DB has {legacy_count} Calibre-sourced books")

    # Count books in each Calibre metadata.db. Phase 20A renamed the
    # discovered-library dict field from `calibre_db_path` to the
    # library-agnostic `source_db_path` (see library_apps/base.py:130);
    # this function was missed in that rename and was raising KeyError on
    # every library, then logging a misleading "Could not read Calibre DB"
    # warning while silently falling back to the first library by slug
    # ordering. Fixed in Phase 3a follow-up.
    best_slug = libraries[0]["slug"]
    best_diff = float("inf")
    for lib in libraries:
        db_path = lib.get("source_db_path") or lib.get("calibre_db_path")  # legacy key for backwards compat
        if not db_path:
            _db_logger.warning(f"  Library '{lib['name']}' has no source_db_path — skipping legacy-DB matching")
            continue
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cal_count = conn.execute("SELECT COUNT(*) FROM books").fetchone()[0]
            conn.close()
            diff = abs(legacy_count - cal_count)
            _db_logger.info(f"  Library '{lib['name']}': {cal_count} books in Calibre (diff={diff})")
            if diff < best_diff:
                best_diff = diff
                best_slug = lib["slug"]
        except Exception as e:
            _db_logger.warning(f"  Could not read Calibre DB for '{lib['name']}' at {db_path}: {e}")

    _db_logger.info(f"Best match for legacy DB: '{best_slug}'")
    return best_slug

SCHEMA = """
CREATE TABLE IF NOT EXISTS authors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sort_name TEXT NOT NULL,
    calibre_id INTEGER,
    hardcover_id TEXT,
    goodreads_id TEXT,
    kobo_id TEXT,
    fictiondb_id TEXT,
    image_url TEXT,
    bio TEXT,
    verified INTEGER NOT NULL DEFAULT 0,
    last_lookup_at REAL,
    created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE(name)
);

CREATE TABLE IF NOT EXISTS series (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    author_id INTEGER NOT NULL,
    hardcover_id TEXT,
    goodreads_id TEXT,
    kobo_id TEXT,
    fictiondb_id TEXT,
    total_books INTEGER,
    description TEXT,
    last_lookup_at REAL,
    created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
    FOREIGN KEY (author_id) REFERENCES authors(id),
    UNIQUE(name, author_id)
);

CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    author_id INTEGER NOT NULL,
    series_id INTEGER,
    series_index REAL,
    isbn TEXT,
    hardcover_id TEXT,
    goodreads_id TEXT,
    fictiondb_id TEXT,
    kobo_id TEXT,
    cover_url TEXT,
    cover_path TEXT,
    pub_date TEXT,
    expected_date TEXT,
    is_unreleased INTEGER NOT NULL DEFAULT 0,
    description TEXT,
    page_count INTEGER,
    source TEXT NOT NULL DEFAULT 'calibre',
    owned INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    calibre_id INTEGER,
    is_new INTEGER NOT NULL DEFAULT 0,
    language TEXT,
    rating REAL,
    tags TEXT,
    publisher TEXT,
    formats TEXT,
    mam_url TEXT,
    mam_status TEXT,
    mam_formats TEXT,
    mam_torrent_id TEXT,
    mam_has_multiple INTEGER NOT NULL DEFAULT 0,
    mam_my_snatched INTEGER NOT NULL DEFAULT 0,
    -- source_url stores a JSON dict mapping source-plugin name to URL:
    --   {"goodreads": "https://www.goodreads.com/book/show/123",
    --    "hardcover": "https://hardcover.app/books/slug", ...}
    -- It's JSON because a single book can be enriched by multiple sources
    -- over time (each scan adds its own URL via _merge_source_urls in
    -- lookup.py). The frontend parses it in BookSidebar.jsx and
    -- BookViews.jsx and renders one badge per source. There is no
    -- migration that validates/repairs corrupt JSON — all writes go
    -- through json.dumps, so corruption would only arise from direct
    -- SQL editing or a botched import/export round-trip.
    source_url TEXT,
    first_seen_at REAL NOT NULL DEFAULT (strftime('%s','now')),
    created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
    FOREIGN KEY (author_id) REFERENCES authors(id),
    FOREIGN KEY (series_id) REFERENCES series(id)
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sync_type TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL DEFAULT 'running',
    books_found INTEGER DEFAULT 0,
    books_new INTEGER DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS mam_scan_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    total_books INTEGER NOT NULL DEFAULT 0,
    last_offset INTEGER NOT NULL DEFAULT 0,
    batch_size INTEGER NOT NULL DEFAULT 250,
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL DEFAULT 'running'
);

-- Phase 3c: Source-consensus series suggestions.
-- One row per book with an active suggestion. The merge layer
-- (lookup.py:_merge_result + _compute_series_suggestions) populates
-- this whenever 2+ sources agree on a (series_name, series_index)
-- tuple that differs from what's currently stored on the book row.
-- The user reviews pending suggestions in the UI and either applies
-- (writes back to books.series_id/series_index, status→applied) or
-- ignores (status→ignored, suppresses re-suggestion of the SAME tuple).
-- A future scan that produces a DIFFERENT consensus from a previously
-- ignored one creates a fresh pending suggestion.
--
-- The current_* columns snapshot the book's series state at the moment
-- the suggestion was generated, so the UI can render "currently: X →
-- suggested: Y" diffs without re-querying the books row (which may
-- have changed by the time the user reviews).
CREATE TABLE IF NOT EXISTS book_series_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id INTEGER NOT NULL UNIQUE,
    suggested_series_name TEXT,
    suggested_series_index REAL,
    sources_agreeing TEXT NOT NULL,
    current_series_name TEXT,
    current_series_index REAL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
    updated_at REAL,
    FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_books_author ON books(author_id);
CREATE INDEX IF NOT EXISTS idx_books_series ON books(series_id);
CREATE INDEX IF NOT EXISTS idx_books_owned ON books(owned);
CREATE INDEX IF NOT EXISTS idx_books_new ON books(is_new);
CREATE INDEX IF NOT EXISTS idx_books_hidden ON books(hidden);
CREATE INDEX IF NOT EXISTS idx_authors_name ON authors(name);
CREATE INDEX IF NOT EXISTS idx_books_mam_status ON books(mam_status);
-- Composite index for the most common combined filter across the app:
-- "all owned (or missing) books for a given author". Used heavily by
-- the author-detail page and the lookup-merge pass that runs once per
-- author during source scans.
CREATE INDEX IF NOT EXISTS idx_books_author_owned ON books(author_id, owned);
CREATE INDEX IF NOT EXISTS idx_suggestions_status ON book_series_suggestions(status);
CREATE INDEX IF NOT EXISTS idx_suggestions_book ON book_series_suggestions(book_id);
"""

# Migrations for existing databases
MIGRATIONS = [
    "ALTER TABLE books ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE books ADD COLUMN cover_path TEXT",
    "ALTER TABLE authors ADD COLUMN verified INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE authors ADD COLUMN fantasticfiction_id TEXT",
    "ALTER TABLE authors ADD COLUMN fictiondb_id TEXT",
    "ALTER TABLE series ADD COLUMN fantasticfiction_id TEXT",
    "ALTER TABLE series ADD COLUMN fictiondb_id TEXT",
    "ALTER TABLE books ADD COLUMN fantasticfiction_id TEXT",
    "ALTER TABLE books ADD COLUMN fictiondb_id TEXT",
    "ALTER TABLE books ADD COLUMN expected_date TEXT",
    "ALTER TABLE books ADD COLUMN is_unreleased INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE books ADD COLUMN language TEXT",
    "ALTER TABLE books ADD COLUMN rating REAL",
    "ALTER TABLE books ADD COLUMN tags TEXT",
    "ALTER TABLE books ADD COLUMN publisher TEXT",
    "ALTER TABLE books ADD COLUMN formats TEXT",
    "ALTER TABLE books ADD COLUMN source_url TEXT",
    "CREATE INDEX IF NOT EXISTS idx_books_hidden ON books(hidden)",
    "ALTER TABLE books ADD COLUMN mam_url TEXT",
    "ALTER TABLE books ADD COLUMN mam_status TEXT",
    "ALTER TABLE books ADD COLUMN mam_formats TEXT",
    "ALTER TABLE books ADD COLUMN mam_torrent_id TEXT",
    "ALTER TABLE books ADD COLUMN mam_has_multiple INTEGER NOT NULL DEFAULT 0",
    "CREATE INDEX IF NOT EXISTS idx_books_mam_status ON books(mam_status)",
    "ALTER TABLE books ADD COLUMN mam_my_snatched INTEGER NOT NULL DEFAULT 0",
    "CREATE INDEX IF NOT EXISTS idx_books_author_owned ON books(author_id, owned)",
    # ── FantasticFiction removal ─────────────────────────────────
    # FF was dropped as a source entirely (it duplicated coverage of
    # Goodreads/Hardcover/Kobo and was Cloudflare-blocked anyway). Null
    # any leftover IDs first, then drop the columns. SQLite 3.35+ is
    # required for DROP COLUMN; the migration loop tolerates "no such
    # column" and other expected errors via its existing exception
    # handling, so re-running on a fresh DB (where columns were never
    # added) is safe.
    "UPDATE authors SET fantasticfiction_id = NULL",
    "UPDATE series SET fantasticfiction_id = NULL",
    "UPDATE books SET fantasticfiction_id = NULL",
    "ALTER TABLE authors DROP COLUMN fantasticfiction_id",
    "ALTER TABLE series DROP COLUMN fantasticfiction_id",
    "ALTER TABLE books DROP COLUMN fantasticfiction_id",
    # ── Phase 3c: source-consensus series suggestions ────────────
    # See SCHEMA above for full doc. Single new table; no changes
    # to existing tables. Indexes are created via the SCHEMA index
    # block at startup so they're not duplicated here.
    """CREATE TABLE IF NOT EXISTS book_series_suggestions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_id INTEGER NOT NULL UNIQUE,
        suggested_series_name TEXT,
        suggested_series_index REAL,
        sources_agreeing TEXT NOT NULL,
        current_series_name TEXT,
        current_series_index REAL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at REAL NOT NULL DEFAULT (strftime('%s','now')),
        updated_at REAL,
        FOREIGN KEY (book_id) REFERENCES books(id) ON DELETE CASCADE
    )""",
    # ── Orphan series cleanup ────────────────────────────────────
    # Existing databases accumulated phantom series rows because the
    # pre-fix _merge_result inserted a series row BEFORE processing any
    # books in that series. In owned_only (library-only) scans, all the
    # books then got filtered out, leaving the series row pointing at
    # nothing. On the user's live container this was 649 of 1324 series
    # rows (~49%). Lazy upsert in lookup.py prevents NEW orphans; this
    # one-shot DELETE kills the existing ones. Idempotent — running it
    # twice deletes nothing the second time.
    "DELETE FROM series WHERE id NOT IN (SELECT DISTINCT series_id FROM books WHERE series_id IS NOT NULL)",
]


async def get_db(slug=None) -> aiosqlite.Connection:
    """Get a database connection for a specific library (or the active library).

    Args:
        slug: Library slug. If None, uses the active library.
              Falls back to legacy APP_DB_PATH if no active library is set.
    """
    path = get_db_path(slug)
    db = await aiosqlite.connect(str(path))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    # 30s busy_timeout gives background tasks (MAM scan batches, UI
    # queries) plenty of room to wait out a Calibre bulk-sync that's
    # holding the write lock. Previously 5s, which was too short — a
    # 2700-book Calibre sync takes ~15s and was causing "database is
    # locked" errors on concurrent writers. WAL mode keeps *readers*
    # unblocked entirely; this only matters for writer↔writer contention.
    await db.execute("PRAGMA busy_timeout=30000")
    return db


async def init_db(slug=None):
    """Initialize schema and run migrations for a library database.

    Uses PRAGMA user_version to track which migrations have been applied,
    so the migration loop is skipped on subsequent startups (avoiding
    redundant work and silent error swallowing).

    Adding a new migration: append to the MIGRATIONS list. The next startup
    will detect that user_version < len(MIGRATIONS) and run only the new
    entries, then update user_version.

    Args:
        slug: Library slug. If None, uses the active library / legacy path.
    """
    db = await get_db(slug)
    try:
        # ── Step 1: Read current schema version ────────────────────
        # PRAGMA user_version returns 0 for fresh databases or those that
        # were initialized before we started using version tracking.
        cursor = await db.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        current_version = row[0] if row else 0
        target_version = len(MIGRATIONS)

        # ── Step 2: Always ensure base tables exist ────────────────
        # CREATE TABLE IF NOT EXISTS is cheap and safe — handles fresh DBs
        # without us needing a separate "first install" code path.
        tables_sql = SCHEMA.split("CREATE INDEX")[0]
        await db.executescript(tables_sql)
        await db.commit()

        # ── Step 3: Run only the migrations we haven't applied yet ─
        if current_version < target_version:
            _db_logger.info(
                f"Migrating database schema: v{current_version} → v{target_version}"
            )
            for i, migration in enumerate(MIGRATIONS):
                if i < current_version:
                    continue
                try:
                    await db.execute(migration)
                except aiosqlite.OperationalError as e:
                    # The "duplicate column" / "already exists" cases are
                    # expected when migrating a legacy database that already
                    # had columns added by the old always-run loop. Silently
                    # tolerate those, but log anything else as a warning so
                    # real migration failures don't disappear.
                    msg = str(e).lower()
                    if ("duplicate column" in msg or "already exists" in msg
                            or "no such column" in msg):
                        continue
                    _db_logger.warning(
                        f"Migration #{i} failed unexpectedly: {e} "
                        f"(SQL: {migration[:80]}...)"
                    )
            await db.commit()

            # Stamp the new version so we skip this loop next startup
            await db.execute(f"PRAGMA user_version = {target_version}")
            await db.commit()

        # ── Step 4: Ensure indexes exist (cheap, idempotent) ───────
        # Indexes are always checked because adding a new index to SCHEMA
        # without a corresponding migration entry should still work.
        index_statements = [line.strip() for line in SCHEMA.split(";")
                           if "CREATE INDEX" in line]
        for idx_sql in index_statements:
            try:
                await db.execute(idx_sql)
            except aiosqlite.OperationalError as e:
                # "already exists" is the expected case for indexes
                if "already exists" not in str(e).lower():
                    _db_logger.warning(f"Index creation failed: {e}")
        await db.commit()
    finally:
        await db.close()
