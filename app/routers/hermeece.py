"""
Hermeece integration — Send to Hermeece for download.

When a book has a confirmed MAM match (mam_status="found"), the user
can send it to Hermeece for automatic download and processing. Supports
both single-book sends (from the sidebar or MAM page) and bulk sends
(from multi-select on the MAM page).

Only "found" books are sent — "possible" and "not_found" entries are
silently skipped in bulk operations.

Requires `hermeece_url` in settings (e.g., "http://10.0.10.20:8686").
"""
import logging

import httpx
from fastapi import APIRouter, Body, HTTPException

from app.config import load_settings
from app.database import get_db
from app.secrets import get_secret

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api", tags=["hermeece"])


@router.post("/hermeece/send")
async def send_to_hermeece(data: dict = Body(...)):
    """Send one or more books to Hermeece for download.

    Accepts a list of book IDs. Only books with mam_status="found"
    are sent — others are silently skipped. Returns counts of sent
    vs skipped, plus per-item results from Hermeece.
    """
    book_ids = data.get("book_ids", [])
    if not book_ids:
        raise HTTPException(400, "No books specified")

    s = load_settings()
    hermeece_url = (s.get("hermeece_url") or "").strip().rstrip("/")
    if not hermeece_url:
        raise HTTPException(400, "Hermeece URL not configured. Set it in Settings → Library → Hermeece URL.")
    # The API key lives in the encrypted store (settings.json is
    # blanked after the Sprint 6 migration). Fall back to the raw
    # settings read for pre-migration installs.
    hermeece_key = (await get_secret("hermeece_api_key") or "").strip()
    if not hermeece_key:
        hermeece_key = (s.get("hermeece_api_key") or "").strip()
    if not hermeece_key:
        raise HTTPException(400, "Hermeece API key not configured. Generate one in Hermeece → Credentials → AthenaScout shared API key and paste it into AthenaScout Settings.")

    db = await get_db()
    try:
        placeholders = ",".join("?" * len(book_ids))
        rows = await (await db.execute(
            f"SELECT b.id, b.title, b.mam_url, b.mam_status, b.mam_torrent_id, "
            f"b.mam_category, "
            f"b.source_url, b.isbn, b.series_id, b.series_index, b.cover_url, "
            f"b.description, b.page_count, "
            f"a.name as author_name, s.name as series_name "
            f"FROM books b "
            f"JOIN authors a ON b.author_id = a.id "
            f"LEFT JOIN series s ON b.series_id = s.id "
            f"WHERE b.id IN ({placeholders})",
            book_ids,
        )).fetchall()
    finally:
        await db.close()

    if not rows:
        raise HTTPException(404, "No books found for the given IDs")

    # Filter to found-only
    found_rows = [r for r in rows if r["mam_status"] == "found" and r["mam_torrent_id"]]
    skipped = len(rows) - len(found_rows)

    if not found_rows:
        return {
            "sent": 0,
            "skipped": skipped,
            "message": "No books with 'Found' MAM status to send",
        }

    # Build Hermeece payload. Including the book title lets Hermeece
    # store a real torrent name on the grab row instead of the
    # `manual_inject_<id>` placeholder — the placeholder leaks into
    # dashboards, the review queue label, and the metadata enricher's
    # fuzzy search (see Hermeece v1.1.4 bug report).
    items = []
    for r in found_rows:
        items.append({
            "url_or_id": str(r["mam_torrent_id"]),
            "author": r["author_name"] or "",
            "title": r["title"] or "",
            # MAM category captured during scan (e.g. "Ebooks - Fantasy").
            # Empty string for pre-v1.1.5 rows scanned before the column
            # existed — Hermeece tolerates the empty-string fallback.
            "category": r["mam_category"] or "",
        })

    # Send to Hermeece
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{hermeece_url}/api/v1/grabs/from-athenascout",
                json={"items": items},
                headers={"X-API-Key": hermeece_key},
            )
            resp.raise_for_status()
            result = resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"Hermeece returned HTTP {e.response.status_code}: {e.response.text[:200]}")
        raise HTTPException(502, f"Hermeece returned error: {e.response.status_code}")
    except Exception as e:
        logger.error(f"Failed to reach Hermeece at {hermeece_url}: {e}")
        raise HTTPException(502, f"Cannot reach Hermeece: {e}")

    sent = result.get("submitted", 0)
    failed = result.get("failed", 0)

    # Fire notification
    try:
        from app.notify import notify_hermeece_sent
        await notify_hermeece_sent(sent, skipped)
    except Exception:
        pass

    logger.info(
        f"Sent {sent} book(s) to Hermeece ({skipped} skipped non-found, "
        f"{failed} failed at Hermeece)"
    )

    return {
        "sent": sent,
        "skipped": skipped,
        "failed": failed,
        "message": f"Sent {sent} to Hermeece" + (f", {skipped} skipped (not Found)" if skipped else ""),
        "results": result.get("results", []),
    }


@router.get("/hermeece/status")
async def hermeece_status():
    """Check if Hermeece is configured and reachable."""
    s = load_settings()
    url = (s.get("hermeece_url") or "").strip().rstrip("/")
    if not url:
        return {"configured": False, "reachable": False, "url": ""}

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{url}/api/health")
            return {"configured": True, "reachable": resp.status_code == 200, "url": url}
    except Exception:
        return {"configured": True, "reachable": False, "url": url}
