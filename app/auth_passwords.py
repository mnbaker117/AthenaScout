"""
Password hashing and verification using bcrypt directly.

We use the `bcrypt` library directly rather than going through passlib.
The reason is a long-standing layering bug between passlib 1.7.4 (the
last release, from 2020) and bcrypt 4.x where passlib's "bug detection
probe" trips on bcrypt 4.x's stricter handling of long passwords. Calling
bcrypt directly is simpler, has no extra dependency layer, and the API
is dead simple anyway.

Work factor: 12 rounds is the modern default — slow enough to be
expensive to brute force, fast enough to verify in <300ms on the kind
of hardware AthenaScout typically runs on (Unraid boxes, NUCs, laptops).

Bcrypt has a hard 72-byte input limit. We truncate longer passwords
explicitly so they hash without raising; combined with the 256-character
upper bound enforced at the API layer (app/routers/auth.py), this means
no user input ever exceeds the truncation point in any meaningful way.
"""
import bcrypt

_BCRYPT_ROUNDS = 12
# bcrypt hard-limits the input to 72 bytes. Anything longer is silently
# truncated by some implementations and rejected by others. We pre-truncate
# explicitly so behavior is consistent across versions.
_BCRYPT_MAX_BYTES = 72


def _to_bcrypt_bytes(plain_password: str) -> bytes:
    """Encode a password to UTF-8 and truncate to bcrypt's 72-byte input
    limit. Truncation is at the byte level, which can split a multi-byte
    character — that's fine for hashing because the same truncation will
    happen on verification."""
    return plain_password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(plain_password: str) -> str:
    """Hash a plain password using bcrypt. Returns a self-describing hash
    string that includes the algorithm identifier, work factor, and salt
    (bcrypt embeds salt in the hash so no separate salt storage is needed).
    """
    salt = bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return bcrypt.hashpw(_to_bcrypt_bytes(plain_password), salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a stored bcrypt hash.

    Returns True if they match, False otherwise. Uses bcrypt's constant-
    time comparison internally to avoid timing attacks. Any exception
    (corrupted hash, wrong algorithm prefix, etc.) is caught and treated
    as a failed verification — callers always get a bool.
    """
    try:
        return bcrypt.checkpw(
            _to_bcrypt_bytes(plain_password),
            hashed_password.encode("utf-8"),
        )
    except Exception:
        return False
