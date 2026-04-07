"""
Author endpoints for AthenaScout.

Holds /api/authors, /api/authors/{aid}, lookup/full-rescan triggers,
clear-scan-data.
"""
import logging
from fastapi import APIRouter, Body, HTTPException, Query

from app.config import load_settings
from app.database import get_db, HF
from app.lookup import lookup_author

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api", tags=["authors"])


@router.get("/authors")
async def get_authors(search: str = Query(None), sort: str = Query("name"), sort_dir: str = Query("asc"), has_missing: bool = Query(None), book_type: str = Query(None)):
    db = await get_db()
    try:
        q = f"SELECT a.*, COUNT(DISTINCT CASE WHEN {HF} THEN b.id END) as total_books, SUM(CASE WHEN b.owned=1 AND {HF} THEN 1 ELSE 0 END) as owned_count, SUM(CASE WHEN b.owned=0 AND {HF} THEN 1 ELSE 0 END) as missing_count, SUM(CASE WHEN b.is_new=1 AND b.owned=0 AND {HF} THEN 1 ELSE 0 END) as new_count, COUNT(DISTINCT b.series_id) as series_count FROM authors a LEFT JOIN books b ON a.id=b.author_id"
        p = []; c = []
        if search: c.append("a.name LIKE ?"); p.append(f"%{search}%")
        if book_type == "series": c.append("b.series_id IS NOT NULL")
        elif book_type == "standalone": c.append("b.series_id IS NULL")
        if c: q += " WHERE " + " AND ".join(c)
        q += " GROUP BY a.id"
        if has_missing: q += " HAVING missing_count > 0"
        d = "DESC" if sort_dir == "desc" else "ASC"
        q += {"missing": f" ORDER BY missing_count {d}, a.sort_name ASC", "new": f" ORDER BY new_count {d}, a.sort_name ASC", "total": f" ORDER BY total_books {d}, a.sort_name ASC"}.get(sort, f" ORDER BY a.sort_name {d}")
        return {"authors": [dict(r) for r in await (await db.execute(q, p)).fetchall()]}
    finally:
        await db.close()


@router.get("/authors/{aid}")
async def get_author(aid: int):
    db = await get_db()
    try:
        r = await (await db.execute("SELECT * FROM authors WHERE id=?", (aid,))).fetchone()
        if not r:
            raise HTTPException(404)
        a = dict(r)
        # Find series through books (supports multi-author series)
        a["series"] = [dict(s) for s in await (await db.execute(
            f"""SELECT s.*,
                COUNT(DISTINCT CASE WHEN {HF} THEN b.id END) as book_count,
                COUNT(DISTINCT CASE WHEN b.author_id=? AND {HF} THEN b.id END) as author_book_count,
                SUM(CASE WHEN b.owned=1 AND b.author_id=? AND {HF} THEN 1 ELSE 0 END) as owned_count,
                SUM(CASE WHEN b.owned=0 AND b.author_id=? AND {HF} THEN 1 ELSE 0 END) as missing_count,
                CASE WHEN COUNT(DISTINCT b.author_id) > 1 THEN 1 ELSE 0 END as multi_author
            FROM series s
            JOIN books b ON s.id=b.series_id
            WHERE s.id IN (SELECT DISTINCT series_id FROM books WHERE author_id=? AND series_id IS NOT NULL)
            GROUP BY s.id ORDER BY s.name""",
            (aid, aid, aid, aid)
        )).fetchall()]
        a["standalone_books"] = [dict(b) for b in await (await db.execute(f"SELECT b.*, a2.name as author_name FROM books b JOIN authors a2 ON b.author_id=a2.id WHERE b.author_id=? AND b.series_id IS NULL AND {HF} ORDER BY b.pub_date ASC, b.title ASC", (aid,))).fetchall()]
        return a
    finally:
        await db.close()


@router.post("/authors/{aid}/lookup")
async def trigger_author_lookup(aid: int):
    s = load_settings()
    if not s.get("author_scanning_enabled", True):
        return {"error": "Author scanning is disabled — enable it in Settings"}
    db = await get_db()
    try:
        r = await (await db.execute("SELECT * FROM authors WHERE id=?", (aid,))).fetchone()
        if not r:
            raise HTTPException(404)
    finally:
        await db.close()
    return {"status": "ok", "new_books": await lookup_author(aid, dict(r)["name"])}


@router.post("/authors/{aid}/full-rescan")
async def trigger_author_full_rescan(aid: int):
    """Full re-scan for a single author."""
    s = load_settings()
    if not s.get("author_scanning_enabled", True):
        return {"error": "Author scanning is disabled — enable it in Settings"}
    db = await get_db()
    try:
        r = await (await db.execute("SELECT * FROM authors WHERE id=?", (aid,))).fetchone()
        if not r:
            raise HTTPException(404)
    finally:
        await db.close()
    return {"status": "ok", "new_books": await lookup_author(aid, dict(r)["name"], full_scan=True)}


@router.post("/authors/clear-scan-data")
async def clear_author_scan_data(data: dict = Body(...)):
    """Clear source and/or MAM scan data for specified authors."""
    author_ids = data.get("author_ids", [])
    clear_source = data.get("clear_source", False)
    clear_mam = data.get("clear_mam", False)
    if not author_ids:
        return {"error": "No authors specified"}
    if not clear_source and not clear_mam:
        return {"error": "Nothing to clear — specify clear_source and/or clear_mam"}
    db = await get_db()
    try:
        placeholders = ",".join(["?" for _ in author_ids])
        affected = 0
        if clear_source:
            # Count books that will be deleted
            count_row = await db.execute_fetchall(
                f"SELECT COUNT(*) FROM books WHERE author_id IN ({placeholders}) AND owned=0 AND calibre_id IS NULL",
                author_ids
            )
            affected = count_row[0][0] if count_row else 0
            # Delete non-owned books (discovered by source scans) for these authors
            await db.execute(
                f"DELETE FROM books WHERE author_id IN ({placeholders}) AND owned=0 AND calibre_id IS NULL",
                author_ids
            )
            # Reset source URLs on owned books (keep source='calibre' intact)
            await db.execute(
                f"UPDATE books SET source_url=NULL WHERE author_id IN ({placeholders}) AND owned=1",
                author_ids
            )
            await db.execute(
                f"UPDATE authors SET last_lookup_at=NULL WHERE id IN ({placeholders})",
                author_ids
            )
        if clear_mam:
            await db.execute(
                f"UPDATE books SET mam_url=NULL, mam_status=NULL, mam_formats=NULL, mam_torrent_id=NULL, mam_has_multiple=0, mam_my_snatched=0 WHERE author_id IN ({placeholders})",
                author_ids
            )
        await db.commit()
        logger.info(f"Cleared scan data for {len(author_ids)} authors (source={clear_source}, mam={clear_mam}), {affected} books deleted")
        return {"status": "ok", "authors_cleared": len(author_ids), "books_deleted": affected}
    finally:
        await db.close()


@router.post("/authors/scan-sources")
async def scan_authors_sources(data: dict = Body(...)):
    """Run a source-plugin lookup for each of the given authors.

    Used by the Authors page bulk-select bar. Loops sequentially because
    lookup_author is rate-limited per-source and parallelizing would just
    queue up against the existing semaphores.
    """
    author_ids = data.get("author_ids", [])
    if not author_ids:
        return {"error": "No authors specified"}

    db = await get_db()
    try:
        placeholders = ",".join(["?" for _ in author_ids])
        rows = await db.execute_fetchall(
            f"SELECT id, name FROM authors WHERE id IN ({placeholders})",
            author_ids,
        )
    finally:
        await db.close()
    if not rows:
        return {"error": "No matching authors found"}

    total_new = 0
    scanned = 0
    errors = 0
    for row in rows:
        aid, name = row[0], row[1]
        try:
            new_books = await lookup_author(aid, name)
            total_new += int(new_books or 0)
            scanned += 1
        except Exception as e:
            logger.error(f"Bulk source scan error for author {aid} ({name}): {e}")
            errors += 1
    return {"status": "complete", "scanned": scanned, "new_books": total_new, "errors": errors}


@router.post("/authors/scan-mam")
async def scan_authors_mam(data: dict = Body(...)):
    """Run a MAM scan for every un-scanned book belonging to the given authors.

    Mirrors mam_scan_single_author from app.routers.mam but for a list of
    authors. Per-author state isn't tracked in state._mam_scan_progress
    because this is a synchronous bulk action driven from the Select bar.
    """
    from app.sources.mam import check_book as mam_check_book, _resolve_mam_languages
    from app import state

    author_ids = data.get("author_ids", [])
    if not author_ids:
        return {"error": "No authors specified"}

    s = load_settings()
    if not s.get("mam_enabled") or not s.get("mam_session_id"):
        return {"error": "MAM not configured or not enabled"}
    if not s.get("mam_scanning_enabled", True):
        return {"error": "MAM scanning is disabled — enable it in Settings"}
    if state._mam_scan_progress.get("running"):
        return {"error": "A MAM scan is already running"}

    db = await get_db()
    try:
        placeholders = ",".join(["?" for _ in author_ids])
        book_rows = await db.execute_fetchall(
            f"SELECT b.id, b.title, a.name FROM books b JOIN authors a ON b.author_id=a.id "
            f"WHERE b.author_id IN ({placeholders}) AND b.mam_status IS NULL "
            f"AND b.is_unreleased=0 AND b.hidden=0 ORDER BY a.sort_name, b.title",
            author_ids,
        )
        if not book_rows:
            return {"status": "complete", "message": "No un-scanned books for these authors",
                    "scanned": 0, "found": 0, "possible": 0, "not_found": 0}

        delay = s.get("rate_mam", 2)
        format_priority = s.get("mam_format_priority")
        token = s["mam_session_id"]
        lang_ids = _resolve_mam_languages(s.get("languages", ["English"]))
        stats = {"scanned": 0, "found": 0, "possible": 0, "not_found": 0, "errors": 0}

        for row in book_rows:
            bid, btitle, aname = row[0], row[1], row[2]
            try:
                check = await mam_check_book(token, btitle, aname, format_priority, delay, lang_ids=lang_ids)
            except Exception as e:
                logger.error(f"Bulk author MAM scan error on book {bid} ({btitle[:40]}): {e}")
                stats["errors"] += 1
                continue
            await db.execute("""
                UPDATE books SET mam_url=?, mam_status=?, mam_formats=?,
                       mam_torrent_id=?, mam_has_multiple=?, mam_my_snatched=?
                WHERE id=?
            """, (
                check["mam_url"], check["status"], check["mam_formats"],
                check["mam_torrent_id"],
                1 if check["mam_has_multiple"] else 0,
                1 if check.get("mam_my_snatched") else 0,
                bid,
            ))
            stats["scanned"] += 1
            if check["status"] == "found":
                stats["found"] += 1
            elif check["status"] == "possible":
                stats["possible"] += 1
            elif check["status"] == "not_found":
                stats["not_found"] += 1
        await db.commit()
        return {"status": "complete", "authors": len(author_ids), **stats}
    finally:
        await db.close()


@router.post("/sources/reset")
async def reset_all_source_scan_data():
    """Reset all source scan data across the entire library.

    Deletes every non-Calibre, non-owned book (i.e. books discovered by source
    scans), clears source_url on owned/Calibre books, and resets last_lookup_at
    on every author so future scans treat all authors as never-scanned.
    MAM data is left untouched.
    """
    db = await get_db()
    try:
        # Count discovered books that will be deleted
        count_row = await db.execute_fetchall(
            "SELECT COUNT(*) FROM books WHERE owned=0 AND calibre_id IS NULL"
        )
        affected = count_row[0][0] if count_row else 0
        # Delete all non-owned discovered books
        await db.execute("DELETE FROM books WHERE owned=0 AND calibre_id IS NULL")
        # Clear source URLs on owned books
        await db.execute("UPDATE books SET source_url=NULL WHERE owned=1")
        # Reset every author's last_lookup_at so the next scheduled scan picks them all up
        await db.execute("UPDATE authors SET last_lookup_at=NULL")
        await db.commit()
        logger.info(f"Reset all source scan data: {affected} discovered books deleted")
        return {"status": "ok", "books_deleted": affected}
    finally:
        await db.close()
