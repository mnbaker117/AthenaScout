"""
Database layer for AthenaScout.
"""
import aiosqlite
from app.config import APP_DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS authors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    sort_name TEXT NOT NULL,
    calibre_id INTEGER,
    hardcover_id TEXT,
    goodreads_id TEXT,
    fantasticfiction_id TEXT,
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
    fantasticfiction_id TEXT,
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
    fantasticfiction_id TEXT,
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

CREATE INDEX IF NOT EXISTS idx_books_author ON books(author_id);
CREATE INDEX IF NOT EXISTS idx_books_series ON books(series_id);
CREATE INDEX IF NOT EXISTS idx_books_owned ON books(owned);
CREATE INDEX IF NOT EXISTS idx_books_new ON books(is_new);
CREATE INDEX IF NOT EXISTS idx_books_hidden ON books(hidden);
CREATE INDEX IF NOT EXISTS idx_authors_name ON authors(name);
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
]


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(str(APP_DB_PATH))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    return db


async def init_db():
    db = await get_db()
    try:
        # Step 1: Create tables (IF NOT EXISTS — safe for existing DBs)
        # Split schema: tables first, indexes later
        tables_sql = SCHEMA.split("CREATE INDEX")[0]
        await db.executescript(tables_sql)
        await db.commit()

        # Step 2: Run migrations to add new columns to existing tables
        for migration in MIGRATIONS:
            try:
                await db.execute(migration)
                await db.commit()
            except Exception:
                pass  # Column/index already exists

        # Step 3: Now create indexes (columns guaranteed to exist)
        index_statements = [line.strip() for line in SCHEMA.split(";")
                           if "CREATE INDEX" in line]
        for idx_sql in index_statements:
            try:
                await db.execute(idx_sql)
                await db.commit()
            except Exception:
                pass  # Index already exists
    finally:
        await db.close()
