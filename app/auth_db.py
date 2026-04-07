"""
Dedicated authentication database for AthenaScout.

Auth credentials live in a SEPARATE SQLite file from the per-library
databases for two reasons:

1. **Library independence.** Switching active libraries must NOT log the
   user out or expose a different admin account. Auth is global to the
   deployment, not scoped to a library.

2. **Independent permissions.** The auth DB file can have its own
   filesystem permissions (0600 on POSIX) without affecting the much
   larger library DBs that the user might want to back up or share.

The file lives at `<data_dir>/athenascout_auth.db`. It contains a single
table (`auth_users`) with one row (the admin). Schema is versioned via
PRAGMA user_version following the same pattern as app/database.py.
"""
import logging
import os
from pathlib import Path

import aiosqlite

from app.runtime import get_data_dir

logger = logging.getLogger("athenascout.auth")


_AUTH_DB_FILENAME = "athenascout_auth.db"

_AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_login_at REAL,
    failed_login_count INTEGER NOT NULL DEFAULT 0,
    failed_login_locked_until REAL
);
"""

# Each entry is a one-shot migration applied when current_version < len(MIGRATIONS).
# Append-only — never reorder or delete entries.
_AUTH_MIGRATIONS: list[str] = []


def get_auth_db_path() -> Path:
    """Return the absolute path to the auth database file."""
    return Path(get_data_dir()) / _AUTH_DB_FILENAME


async def get_auth_db() -> aiosqlite.Connection:
    """Open a connection to the auth database. Caller is responsible for
    closing it (use try/finally or `async with` patterns)."""
    path = get_auth_db_path()
    db = await aiosqlite.connect(str(path))
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await db.execute("PRAGMA busy_timeout=5000")
    return db


async def init_auth_db() -> None:
    """Create the auth database file if missing, ensure the schema is
    current, and tighten file permissions on POSIX systems.

    Idempotent — safe to call on every startup. Does nothing on subsequent
    calls if the schema version is already current.
    """
    path = get_auth_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    db = await get_auth_db()
    try:
        cursor = await db.execute("PRAGMA user_version")
        row = await cursor.fetchone()
        current_version = row[0] if row else 0
        target_version = len(_AUTH_MIGRATIONS)

        # Always ensure the base table exists (cheap, idempotent).
        await db.executescript(_AUTH_SCHEMA)
        await db.commit()

        if current_version < target_version:
            logger.info(
                f"Migrating auth database schema: v{current_version} → v{target_version}"
            )
            for i, migration in enumerate(_AUTH_MIGRATIONS):
                if i < current_version:
                    continue
                try:
                    await db.execute(migration)
                except aiosqlite.OperationalError as e:
                    msg = str(e).lower()
                    if "duplicate column" in msg or "already exists" in msg:
                        continue
                    logger.warning(
                        f"Auth migration #{i} failed: {e} (SQL: {migration[:80]}...)"
                    )
            await db.commit()
            await db.execute(f"PRAGMA user_version = {target_version}")
            await db.commit()
    finally:
        await db.close()

    # Lock down the file on POSIX systems so only the owning user can read
    # the password hash. Best-effort — silently ignored on Windows or any
    # filesystem that doesn't support chmod.
    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):
        pass
