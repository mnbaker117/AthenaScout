"""
Session cookie creation and validation.

Sessions are stored as signed cookies (not JWTs). The payload is just the
user ID + an issued-at timestamp, signed with itsdangerous'
URLSafeTimedSerializer to prevent tampering and enforce expiry. The
signature is verified on every request via the auth middleware.

Why signed cookies instead of JWTs:
- Simpler to implement and reason about for a single-user app
- No JWT footguns (alg=none, key confusion, claim parsing edge cases)
- Can be invalidated server-side if needed (we don't, but the option exists)

Cookie security flags applied at issue time:
- HttpOnly: cannot be read by JavaScript (mitigates XSS-based session theft)
- SameSite=Lax: prevents CSRF from third-party sites
- Secure: only sent over HTTPS — set conditionally based on request scheme
- Max-Age: 30 days (sliding refresh, not implemented yet — future work)
"""
import time
from typing import Optional

from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.auth_secret import get_auth_secret


SESSION_COOKIE_NAME = "athenascout_session"
SESSION_LIFETIME_SECONDS = 30 * 24 * 60 * 60  # 30 days


def _get_serializer() -> URLSafeTimedSerializer:
    """Build a serializer using the current auth secret. Cheap; the
    serializer itself just stores the key + salt strings."""
    return URLSafeTimedSerializer(
        secret_key=get_auth_secret(),
        salt="athenascout-session",
    )


def create_session_token(user_id: int) -> str:
    """Create a signed session token for the given user ID. Returned
    string is safe to put in a cookie value (URL-safe base64 encoding)."""
    serializer = _get_serializer()
    return serializer.dumps({"user_id": user_id, "issued_at": time.time()})


def verify_session_token(token: str) -> Optional[int]:
    """Verify a session token and return the user_id if valid.

    Returns None if the token is empty, malformed, tampered with, or
    expired. Callers should treat None as "not authenticated" and require
    the user to log in again.
    """
    if not token:
        return None
    serializer = _get_serializer()
    try:
        payload = serializer.loads(token, max_age=SESSION_LIFETIME_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    except Exception:
        # Defensive: any other parsing/decoding failure → treat as invalid.
        return None
    user_id = payload.get("user_id") if isinstance(payload, dict) else None
    if isinstance(user_id, int):
        return user_id
    return None
