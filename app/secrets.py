"""
Encrypted credential store for AthenaScout.

Sensitive values (MAM session, Hardcover API key) are stored encrypted
in the auth DB using Fernet symmetric encryption. The encryption key
is derived from the auth_secret (already persisted securely).

Replaces plaintext storage in settings.json for credentials. The rest
of the app reads credentials through get_secret(key) instead of
load_settings().get(key).

Ported from Hermeece's app/secrets.py.
"""
import base64
import hashlib
import logging
from typing import Optional

import aiosqlite
from cryptography.fernet import Fernet, InvalidToken

from app.auth_db import get_auth_db
from app.auth_secret import get_auth_secret

logger = logging.getLogger("athenascout.secrets")

# Keys that are stored encrypted. Only genuinely sensitive values.
SECRET_KEYS: dict[str, str] = {
    "mam_session_id": "MAM session cookie",
    "hardcover_api_key": "Hardcover API Bearer token",
}

_SECRETS_TABLE = """
CREATE TABLE IF NOT EXISTS secrets (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


async def init_secrets_table() -> None:
    """Ensure the secrets table exists in the auth DB."""
    db = await get_auth_db()
    try:
        await db.executescript(_SECRETS_TABLE)
        await db.commit()
    finally:
        await db.close()


def _fernet_key() -> bytes:
    """Derive a Fernet key from the auth secret (SHA-256 → base64)."""
    raw = get_auth_secret().encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return base64.urlsafe_b64encode(digest)


def _encrypt(plaintext: str) -> str:
    return Fernet(_fernet_key()).encrypt(plaintext.encode("utf-8")).decode("utf-8")


def _decrypt(ciphertext: str) -> str:
    return Fernet(_fernet_key()).decrypt(ciphertext.encode("utf-8")).decode("utf-8")


async def get_secret(key: str) -> Optional[str]:
    """Read a decrypted secret, or None if not set."""
    db = await get_auth_db()
    try:
        cursor = await db.execute("SELECT value FROM secrets WHERE key = ?", (key,))
        row = await cursor.fetchone()
        if row is None:
            return None
        try:
            return _decrypt(str(row["value"]))
        except (InvalidToken, Exception):
            logger.warning(f"Secret '{key}' failed to decrypt — possibly corrupted")
            return None
    finally:
        await db.close()


async def set_secret(key: str, value: str) -> None:
    """Encrypt and store a secret."""
    encrypted = _encrypt(value)
    db = await get_auth_db()
    try:
        await db.execute(
            "INSERT INTO secrets (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, encrypted),
        )
        await db.commit()
    finally:
        await db.close()


async def delete_secret(key: str) -> None:
    """Remove a secret."""
    db = await get_auth_db()
    try:
        await db.execute("DELETE FROM secrets WHERE key = ?", (key,))
        await db.commit()
    finally:
        await db.close()


async def list_configured() -> dict[str, bool]:
    """Return {key: True/False} for every known secret key."""
    db = await get_auth_db()
    try:
        cursor = await db.execute("SELECT key FROM secrets")
        rows = await cursor.fetchall()
        stored = {str(r["key"]) for r in rows}
    finally:
        await db.close()
    return {k: k in stored for k in SECRET_KEYS}


async def migrate_from_settings() -> int:
    """One-time migration: copy secrets from settings.json into the
    encrypted store, then blank them in settings.json.

    Returns the number of secrets migrated.
    """
    from app.config import load_settings, save_settings

    settings = load_settings()
    migrated = 0

    for key in SECRET_KEYS:
        value = settings.get(key)
        if value and isinstance(value, str) and value.strip():
            existing = await get_secret(key)
            if existing:
                continue
            await set_secret(key, value.strip())
            migrated += 1

    if migrated > 0:
        settings = dict(load_settings())
        for key in SECRET_KEYS:
            if key in settings and settings[key]:
                settings[key] = ""
        save_settings(settings)
        logger.info(f"Migrated {migrated} secret(s) from settings.json to encrypted store")

    return migrated
