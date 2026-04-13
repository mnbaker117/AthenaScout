"""
ntfy notification sender for AthenaScout.

Sends push notifications via ntfy.sh (or a self-hosted ntfy server)
for significant events: scan completions, new books found, MAM matches.
No-op when ntfy_url is empty — callers don't need to check config.

Ported from Hermeece's notify/ntfy.py.
"""
import logging
from typing import Optional
from urllib.parse import urlparse

import httpx

from app.config import load_settings

logger = logging.getLogger("athenascout.notify")

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
    return _client


async def aclose() -> None:
    global _client
    if _client is not None:
        try:
            await _client.aclose()
        except Exception:
            pass
        finally:
            _client = None


def _resolve_endpoint(url: str, topic: str) -> Optional[str]:
    """Resolve full ntfy endpoint from user settings.

    Accepts: "https://ntfy.sh" + topic, "ntfy.sh/mytopic", etc.
    """
    if not url or not url.strip():
        return None
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.path and parsed.path != "/":
        return url.rstrip("/")
    if not topic or not topic.strip():
        return None
    return f"{url.rstrip('/')}/{topic.strip()}"


async def send(
    *,
    title: str,
    message: str,
    priority: int = 3,
    tags: Optional[list[str]] = None,
) -> bool:
    """Send a notification via ntfy. Returns True on success.

    Reads ntfy_url and ntfy_topic from settings. No-op if not configured.
    """
    s = load_settings()
    endpoint = _resolve_endpoint(s.get("ntfy_url", ""), s.get("ntfy_topic", ""))
    if not endpoint:
        return False

    headers = {"Title": title, "Priority": str(priority)}
    if tags:
        headers["Tags"] = ",".join(tags)

    try:
        resp = await _get_client().post(
            endpoint, content=message.encode("utf-8"), headers=headers,
        )
        if resp.status_code == 200:
            logger.debug(f"ntfy sent: {title}")
            return True
        logger.warning(f"ntfy HTTP {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception:
        logger.debug("ntfy send failed", exc_info=True)
        return False


# ─── Event-specific senders ─────────────────────────────────

async def notify_scan_complete(
    author_name: str, new_books: int, total_sources: int,
) -> bool:
    if new_books == 0:
        return False
    s = load_settings()
    if not s.get("ntfy_on_scan_complete", True):
        return False
    return await send(
        title=f"Scan complete: {author_name}",
        message=f"{new_books} new book(s) found across {total_sources} source(s)",
        tags=["books", "mag"],
    )


async def notify_new_books(author_name: str, count: int) -> bool:
    s = load_settings()
    if not s.get("ntfy_on_new_books", True):
        return False
    return await send(
        title=f"New books: {author_name}",
        message=f"{count} new book(s) discovered",
        tags=["books", "sparkles"],
    )


async def notify_mam_scan_complete(
    scanned: int, found: int, possible: int, not_found: int,
) -> bool:
    s = load_settings()
    if not s.get("ntfy_on_mam_complete", True):
        return False
    return await send(
        title="MAM scan complete",
        message=(
            f"Scanned {scanned} books\n"
            f"Found: {found} · Possible: {possible} · Not found: {not_found}"
        ),
        tags=["mag"],
    )


async def notify_hermeece_sent(sent: int, skipped: int) -> bool:
    s = load_settings()
    if not s.get("ntfy_on_hermeece_sent", True):
        return False
    return await send(
        title=f"Sent {sent} book(s) to Hermeece",
        message=f"{sent} queued for download" + (f", {skipped} skipped" if skipped else ""),
        tags=["arrow_down", "books"],
    )


async def notify_library_sync(library_name: str, new: int, updated: int) -> bool:
    s = load_settings()
    if not s.get("ntfy_on_library_sync", False):
        return False
    if new == 0 and updated == 0:
        return False
    return await send(
        title=f"Library synced: {library_name}",
        message=f"{new} new, {updated} updated",
        tags=["books"],
    )


async def notify_mam_cookie_rotated() -> bool:
    s = load_settings()
    if not s.get("ntfy_on_mam_cookie_rotated", False):
        return False
    return await send(
        title="MAM cookie rotated",
        message="Session token automatically refreshed",
        priority=2,
        tags=["key"],
    )
