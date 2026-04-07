"""
Authentication secret management for AthenaScout.

The auth secret is used to sign session cookies (via itsdangerous).
There are three places it can come from, in priority order:

1. The ATHENASCOUT_AUTH_SECRET environment variable.
   This is the operator-friendly path: inject the secret via Docker
   secrets, Kubernetes secrets, systemd-creds, or a .env file. When
   set, no file is written to disk at all.

2. A file at <data_dir>/auth_secret.
   The default for users who don't set the env var. The file is
   chmodded to 0600 on POSIX so only the owning user can read it.
   Generated on first run with secrets.token_urlsafe(48).

3. Generated fresh and held in memory only.
   Last-resort fallback if both env var is missing AND the file write
   fails (e.g., read-only filesystem). Sessions work for the lifetime
   of the process but are invalidated on every restart. A loud error
   is logged when this happens.

DO NOT change the secret on subsequent runs — it would invalidate every
existing session and force everyone to log in again. The secret is
considered stable for the lifetime of the deployment.

If the secret file is ever lost or deleted, the only side effect is "all
current sessions are invalidated" — users have to log in again. No data
loss, no recovery flow needed.
"""
import logging
import os
import secrets
from pathlib import Path

from app.runtime import get_data_dir

logger = logging.getLogger("athenascout.auth")

_SECRET_FILENAME = "auth_secret"
_ENV_VAR_NAME = "ATHENASCOUT_AUTH_SECRET"
_MIN_LEN = 32  # bytes; below this we treat the secret as corrupted

_cached_secret: str | None = None


def get_auth_secret() -> str:
    """Return the auth secret, generating and persisting it on first call.

    Cached in memory after the first read so we don't hit the env var
    or filesystem on every request. The secret is a 64-character URL-safe
    random string (48 random bytes → ~64 chars after base64 encoding)
    when generated; an env-var-supplied secret is used as-is provided it
    meets the minimum length.
    """
    global _cached_secret
    if _cached_secret is not None:
        return _cached_secret

    # Priority 1: env var override (operators / containerized deployments).
    env_secret = os.environ.get(_ENV_VAR_NAME, "").strip()
    if env_secret:
        if len(env_secret) < _MIN_LEN:
            logger.error(
                f"{_ENV_VAR_NAME} is set but shorter than {_MIN_LEN} chars — "
                "ignoring and falling back to file/generated secret. "
                "Use at least 32 characters of high-entropy randomness."
            )
        else:
            logger.info(f"Using auth secret from {_ENV_VAR_NAME} env var")
            _cached_secret = env_secret
            return _cached_secret

    # Priority 2: persisted secret file in the data directory.
    secret_path = Path(get_data_dir()) / _SECRET_FILENAME

    if secret_path.exists():
        try:
            existing = secret_path.read_text().strip()
            if len(existing) >= _MIN_LEN:
                _cached_secret = existing
                return _cached_secret
            logger.warning(
                f"Auth secret at {secret_path} is shorter than {_MIN_LEN} chars — regenerating"
            )
        except OSError as e:
            logger.warning(f"Could not read auth secret at {secret_path}: {e} — regenerating")

    new_secret = secrets.token_urlsafe(48)
    try:
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        secret_path.write_text(new_secret)
        try:
            os.chmod(secret_path, 0o600)
        except (OSError, NotImplementedError):
            # Windows or filesystem without chmod support — best effort.
            pass
        logger.info(f"Generated new auth secret at {secret_path}")
    except OSError as e:
        # If we can't persist the secret, sessions will still work for the
        # lifetime of the process but won't survive restarts. Loud warning.
        logger.error(
            f"Failed to persist auth secret to {secret_path}: {e}. "
            "Sessions will be invalidated on every restart until this is fixed."
        )

    _cached_secret = new_secret
    return _cached_secret
