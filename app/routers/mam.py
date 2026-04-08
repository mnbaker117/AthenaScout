"""
MyAnonamouse integration endpoints for AthenaScout.

Holds /api/mam/* — validation, status, scan/cancel/status, test-scan,
full-scan flow, toggle, books list, reset.
"""
import asyncio
import logging
import time
from fastapi import APIRouter, HTTPException, Query

from app.config import load_settings, save_settings
from app.database import get_db
from app.sources.mam import (
    validate_connection as mam_validate,
    scan_books_batch as mam_scan_batch,
    start_full_scan as mam_start_full_scan,
    run_full_scan_batch as mam_run_full_scan_batch,
    cancel_full_scan as mam_cancel_full_scan,
    get_full_scan_status as mam_get_full_scan_status,
    get_mam_stats,
    check_book as mam_check_book,
    _resolve_mam_languages,
)
from app import state

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api/mam", tags=["mam"])


@router.post("/validate")
async def mam_validate_endpoint():
    """Test MAM session ID — runs IP registration + search auth."""
    s = load_settings()
    session_id = s.get("mam_session_id", "")
    if not session_id:
        return {"success": False, "message": "No MAM session ID configured"}
    skip_ip = s.get("mam_skip_ip_update", False)
    result = await mam_validate(session_id, skip_ip)
    if result["success"]:
        s["mam_enabled"] = True
        s["last_mam_validated_at"] = time.time()
        s["mam_validation_ok"] = True
    else:
        s["mam_validation_ok"] = False
    save_settings(s)
    return result


@router.get("/status")
async def mam_status_endpoint():
    """Get MAM integration status and stats."""
    s = load_settings()
    enabled = s.get("mam_enabled", False) and bool(s.get("mam_session_id", ""))
    if not enabled:
        return {"enabled": False, "stats": None, "full_scan": None}
    db = await get_db()
    try:
        stats = await get_mam_stats(db)
        scan_status = await mam_get_full_scan_status(db)
        return {"enabled": True, "stats": stats, "full_scan": scan_status,
                "validation_ok": s.get("mam_validation_ok", True),
                "last_validated_at": s.get("last_mam_validated_at")}
    finally:
        await db.close()


@router.post("/scan")
async def mam_scan_endpoint(limit: int = Query(None, ge=1)):
    """Scan books missing MAM data. Batches of 100 with 5-min pauses.
    If limit is provided, scan at most that many books total."""
    s = load_settings()
    if not s.get("mam_enabled") or not s.get("mam_session_id"):
        return {"error": "MAM not configured or not enabled"}
    if not s.get("mam_scanning_enabled", True):
        return {"error": "MAM scanning is disabled — enable it in Settings"}
    if state._mam_scan_progress.get("running"):
        return {"error": "A MAM scan is already running"}
    # Phase 3d-1 (post-feedback): no longer blocking on a concurrent
    # author scan — see _wait_for_other_writers() docstring below.

    # Phase 3d-1 (post-feedback): snapshot the FULL set of book IDs
    # eligible for scanning RIGHT NOW. The scan processes only this
    # snapshot — books that get added to the database during the scan
    # (e.g. by a concurrent author/source scan discovering new titles)
    # are NOT picked up by this run, they wait for the next MAM scan.
    # This is what the user expects: "MAM scan won't constantly
    # recalculate". Without snapshotting, each batch's `WHERE
    # mam_status IS NULL` re-query would grow the queue endlessly
    # under sustained author-scan throughput.
    db = await get_db()
    try:
        id_rows = await db.execute_fetchall(
            "SELECT id FROM books WHERE mam_status IS NULL AND is_unreleased=0 AND hidden=0 "
            "ORDER BY owned DESC, id ASC"
        )
        all_ids = [r[0] for r in id_rows]
    finally:
        await db.close()

    if not all_ids:
        return {"status": "complete", "message": "No books need scanning — all already have MAM data"}

    snapshot_ids = all_ids[:limit] if limit else all_ids
    scan_total = len(snapshot_ids)
    state._mam_scan_progress = {"running": True, "scanned": 0, "total": scan_total,
                          "found": 0, "possible": 0, "not_found": 0, "errors": 0,
                          "status": "scanning", "type": "manual"}

    async def _wait_for_other_writers():
        """Yield to Calibre library sync before grabbing the write lock.

        Phase 3d-1 (post-feedback): the historical wait-out for in-flight
        author/source scans was REMOVED. The original incident at 22:33
        was a MAM batch racing a scheduled Calibre SYNC, not an author
        scan — Calibre sync does massive bulk inserts in big transactions
        and can hold the SQLite write lock for tens of seconds, longer
        than busy_timeout. Author scans, by contrast, do small per-row
        UPDATEs with sub-100ms write windows that the WAL-mode + 30s
        busy_timeout combo absorbs cleanly. Letting MAM and author scans
        run concurrently means a long Sanderson source scan no longer
        blocks the entire MAM scan queue, which was the user-visible
        annoyance that motivated this change.
        """
        if state._calibre_sync_in_progress:
            state._mam_scan_progress["status"] = "waiting (calibre sync running)"
            logger.info("MAM scan waiting for Calibre sync to finish...")
            while state._calibre_sync_in_progress:
                await asyncio.sleep(5)
            logger.info("Calibre sync finished — MAM scan resuming")
        state._mam_scan_progress["status"] = "scanning"

    async def _do_scan():
        batch_num = 0
        cursor = 0  # index into snapshot_ids; advances each batch
        while True:
            # Wait for Calibre sync (only) before starting next batch
            await _wait_for_other_writers()
            cs = load_settings()
            if not cs.get("mam_enabled") or not cs.get("mam_session_id"):
                state._mam_scan_progress.update({"status": "stopped (MAM disabled)", "running": False})
                return
            db = await get_db()
            try:
                def _progress(stats):
                    state._mam_scan_progress.update({
                        "scanned": base_scanned + stats["scanned"],
                        "found": base_found + stats["found"],
                        "possible": base_possible + stats["possible"],
                        "not_found": base_not_found + stats["not_found"],
                        "errors": base_errors + stats["errors"],
                    })
                base_scanned = state._mam_scan_progress["scanned"]
                base_found = state._mam_scan_progress["found"]
                base_possible = state._mam_scan_progress["possible"]
                base_not_found = state._mam_scan_progress["not_found"]
                base_errors = state._mam_scan_progress["errors"]
                # Slice the next batch out of the frozen snapshot. This
                # is the snapshot guarantee: only IDs captured at scan
                # start are processed. New books added by a concurrent
                # author scan will NOT inflate the total or appear in
                # later batches of THIS scan.
                batch_ids = snapshot_ids[cursor:cursor + 100]
                if not batch_ids:
                    state._mam_scan_progress.update({"status": "complete", "running": False})
                    logger.info(f"MAM scan complete (snapshot exhausted): {state._mam_scan_progress['scanned']}/{scan_total} scanned, {state._mam_scan_progress['found']} found")
                    await db.close()
                    return
                result = await mam_scan_batch(
                    db, session_id=cs["mam_session_id"], limit=len(batch_ids),
                    delay=cs.get("rate_mam", 2), skip_ip_update=True,
                    format_priority=cs.get("mam_format_priority"),
                    on_progress=_progress,
                    lang_ids=_resolve_mam_languages(cs.get("languages", ["English"])),
                    book_ids=batch_ids,
                )
                if result.get("error"):
                    state._mam_scan_progress.update({"status": f"error: {result['error']}", "running": False})
                    return
                cursor += len(batch_ids)
                # NOTE: total stays fixed at scan_total — no recompute.
                # The user explicitly asked that the MAM scan not grow
                # its queue when concurrent author scans add new books.
                await db.execute(
                    "INSERT INTO sync_log (sync_type, started_at, finished_at, status, books_found, books_new) VALUES (?,?,?,?,?,?)",
                    ("mam", time.time(), time.time(), "complete",
                     result.get("scanned", 0), result.get("found", 0))
                )
                await db.commit()
            except Exception as e:
                logger.error(f"MAM scan batch error: {e}", exc_info=True)
                state._mam_scan_progress.update({"status": f"error: {e}", "running": False})
                return
            finally:
                await db.close()
            if cursor >= scan_total:
                state._mam_scan_progress.update({"status": "complete", "running": False})
                logger.info(f"MAM scan complete: {state._mam_scan_progress['scanned']}/{scan_total} scanned, {state._mam_scan_progress['found']} found")
                return
            batch_num += 1
            state._mam_scan_progress["status"] = "paused"
            logger.info(f"MAM scan batch {batch_num} done ({state._mam_scan_progress['scanned']}/{state._mam_scan_progress['total']}), pausing 5 min")
            await asyncio.sleep(300)
            # Wait for any author scan or Calibre sync to finish before resuming
            await _wait_for_other_writers()

    state._mam_scan_task = asyncio.create_task(_do_scan())
    return {"status": "started", "total": scan_total}


@router.post("/scan/cancel")
async def mam_scan_cancel():
    """Cancel the currently running MAM scan."""
    if state._mam_scan_task and not state._mam_scan_task.done():
        state._mam_scan_task.cancel()
        state._mam_scan_progress.update({"running": False, "status": "cancelled"})
        logger.info("MAM scan cancelled by user")
        return {"status": "ok", "message": "MAM scan cancelled"}
    return {"status": "ok", "message": "No MAM scan running"}


@router.get("/scan/status")
async def mam_scan_status_endpoint():
    """Get progress of any active MAM scan (manual, scheduled, or full)."""
    if state._mam_scan_progress.get("running"):
        return dict(state._mam_scan_progress)
    if state._mam_full_scan_task and not state._mam_full_scan_task.done():
        db = await get_db()
        try:
            fs = await mam_get_full_scan_status(db)
            if fs.get("active"):
                return {"running": True, "scanned": fs.get("scanned", 0),
                        "total": fs.get("total_books", 0), "found": 0,
                        "possible": 0, "not_found": 0, "errors": 0,
                        "status": "scanning", "type": "full_scan",
                        "progress_pct": fs.get("progress_pct", 0)}
        finally:
            await db.close()
    return dict(state._mam_scan_progress)


@router.post("/test-scan")
async def mam_test_scan():
    """Run a quick test scan of 10 books and return results inline."""
    s = load_settings()
    if not s.get("mam_enabled") or not s.get("mam_session_id"):
        return {"error": "MAM not configured or not enabled"}
    if not s.get("mam_scanning_enabled", True):
        return {"error": "MAM scanning is disabled — enable it in Settings"}
    if state._mam_scan_task and not state._mam_scan_task.done():
        return {"error": "A MAM scan is already running — wait for it to finish"}
    db = await get_db()
    try:
        result = await mam_scan_batch(
            db, session_id=s["mam_session_id"], limit=10,
            delay=s.get("rate_mam", 2),
            skip_ip_update=True,
            format_priority=s.get("mam_format_priority"),
            lang_ids=_resolve_mam_languages(s.get("languages", ["English"])),
        )
        return result
    finally:
        await db.close()


@router.post("/full-scan")
async def mam_full_scan_start():
    """Start a full MAM library scan (250 books/batch, 1hr between batches)."""
    s = load_settings()
    if not s.get("mam_enabled") or not s.get("mam_session_id"):
        return {"error": "MAM not configured or not enabled"}
    if not s.get("mam_scanning_enabled", True):
        return {"error": "MAM scanning is disabled — enable it in Settings"}
    if state._mam_full_scan_task and not state._mam_full_scan_task.done():
        return {"error": "A full MAM scan is already running"}
    if state._mam_scan_progress.get("running"):
        return {"error": "A MAM scan is already running — wait for it to finish"}
    # Phase 3d-1 (post-feedback): no longer rejecting on a concurrent
    # author scan — they're allowed to run side-by-side now. See the
    # _wait_for_other_writers comment in /api/mam/scan above.
    #
    # TODO (post-3d): the full MAM scan still re-queries
    # `WHERE mam_status IS NULL` each batch, so books added by a
    # concurrent author scan WILL get picked up mid-run (unlike the
    # manual /api/mam/scan path, which now snapshots IDs at start).
    # Fixing requires adding a book_ids snapshot column to the
    # mam_scan_log table — a schema migration that's worth doing as
    # part of 3d-2 cleanup, not in this round.

    db = await get_db()
    try:
        start_result = await mam_start_full_scan(db)
        if "error" in start_result:
            return start_result
    finally:
        await db.close()

    async def _full_scan_loop():
        state._mam_scan_progress = {"running": True, "scanned": 0,
                              "total": start_result.get("total_books", 0),
                              "found": 0, "possible": 0, "not_found": 0,
                              "errors": 0, "status": "scanning", "type": "full_scan"}
        while True:
            db = await get_db()
            try:
                cs = load_settings()
                result = await mam_run_full_scan_batch(
                    db, session_id=cs["mam_session_id"],
                    skip_ip_update=True,
                    delay=cs.get("rate_mam", 2),
                    format_priority=cs.get("mam_format_priority"),
                    lang_ids=_resolve_mam_languages(cs.get("languages", ["English"])),
                )
                fs = await mam_get_full_scan_status(db)
                state._mam_scan_progress.update({
                    "scanned": fs.get("scanned", 0),
                    "total": fs.get("total_books", state._mam_scan_progress["total"]),
                    "status": "scanning" if result["status"] == "batch_complete" else result["status"],
                })
            finally:
                await db.close()
            if result["status"] in ("scan_complete", "error", "no_scan"):
                state._mam_scan_progress.update({"running": False, "status": result["status"]})
                break
            elif result["status"] == "batch_complete":
                wait = cs.get("mam_full_scan_batch_delay_minutes", 60) * 60
                state._mam_scan_progress["status"] = "paused"
                logger.info(f"Full MAM scan: batch done, waiting {wait//60} min")
                await asyncio.sleep(wait)
                # Phase 3d-1 (post-feedback): no longer waiting on a
                # concurrent author scan between batches.
                state._mam_scan_progress["status"] = "scanning"

    state._mam_full_scan_task = asyncio.create_task(_full_scan_loop())
    return {"status": "started", "scan_id": start_result["id"],
            "total_books": start_result["total_books"]}


@router.get("/full-scan/status")
async def mam_full_scan_status():
    """Get progress of the current/most recent full MAM scan."""
    db = await get_db()
    try:
        return await mam_get_full_scan_status(db)
    finally:
        await db.close()


@router.post("/full-scan/cancel")
async def mam_full_scan_cancel():
    """Cancel a running full MAM scan."""
    db = await get_db()
    try:
        result = await mam_cancel_full_scan(db)
    finally:
        await db.close()
    if state._mam_full_scan_task and not state._mam_full_scan_task.done():
        state._mam_full_scan_task.cancel()
    return result


@router.post("/toggle")
async def mam_toggle():
    """Toggle MAM features on/off (only works if session ID exists)."""
    s = load_settings()
    if not s.get("mam_session_id"):
        return {"error": "No MAM session ID configured"}
    s["mam_enabled"] = not s.get("mam_enabled", False)
    save_settings(s)
    return {"enabled": s["mam_enabled"]}


@router.get("/books")
async def mam_books_endpoint(section: str = "upload", search: str = "",
                              sort: str = "title", page: int = 1, per_page: int = 50):
    """Get books for the MAM page, filtered by section."""
    db = await get_db()
    try:
        if section == "upload":
            where = "b.owned=1 AND b.mam_status='not_found' AND b.hidden=0"
        elif section == "download":
            where = "b.owned=0 AND b.mam_status IN ('found','possible') AND b.is_unreleased=0 AND b.hidden=0"
        elif section == "missing_everywhere":
            where = "b.owned=0 AND b.mam_status='not_found' AND b.is_unreleased=0 AND b.hidden=0"
        else:
            return {"error": f"Unknown section: {section}"}

        params = []
        if search:
            where += " AND (b.title LIKE ? OR a.name LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])

        sort_map = {"title": "b.title ASC", "author": "a.name ASC",
                    "date": "b.pub_date DESC", "series": "s.name ASC, b.series_index ASC"}
        order = sort_map.get(sort, "b.title ASC")

        count_sql = f"SELECT COUNT(*) FROM books b JOIN authors a ON b.author_id=a.id LEFT JOIN series s ON b.series_id=s.id WHERE {where}"
        count_row = await db.execute_fetchall(count_sql, params)
        total = count_row[0][0] if count_row else 0

        offset = (page - 1) * per_page
        # Pre-aggregated series_total (same refactor as routers/books.py) —
        # replaces a correlated COUNT(*) that fired once per returned row.
        data_sql = f"""SELECT b.*, a.name as author_name, s.name as series_name,
            COALESCE(st.series_total, 0) as series_total
            FROM books b JOIN authors a ON b.author_id=a.id
            LEFT JOIN series s ON b.series_id=s.id
            LEFT JOIN (
                SELECT series_id, COUNT(*) AS series_total
                FROM books
                WHERE hidden=0 AND series_id IS NOT NULL
                GROUP BY series_id
            ) st ON st.series_id = b.series_id
            WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?"""
        rows = await db.execute_fetchall(data_sql, params + [per_page, offset])
        books = [dict(r) for r in rows]

        return {"books": books, "total": total, "page": page, "per_page": per_page,
                "total_pages": (total + per_page - 1) // per_page}
    finally:
        await db.close()


@router.post("/scan-book/{book_id}")
async def mam_scan_single_book(book_id: int):
    """Re-scan a single book against MAM, ignoring its existing mam_status.

    Used by the "Re-scan MAM" button in BookSidebar so the user can manually
    refresh a stale or wrong match without waiting for a full or scheduled scan.
    """
    s = load_settings()
    if not s.get("mam_enabled") or not s.get("mam_session_id"):
        return {"error": "MAM not configured or not enabled"}
    if not s.get("mam_scanning_enabled", True):
        return {"error": "MAM scanning is disabled — enable it in Settings"}

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT b.id, b.title, a.name FROM books b JOIN authors a ON b.author_id=a.id WHERE b.id=?",
            (book_id,),
        )
        if not rows:
            return {"error": f"Book {book_id} not found"}
        _, title, author = rows[0]

        check = await mam_check_book(
            s["mam_session_id"], title, author,
            format_priority=s.get("mam_format_priority"),
            delay=s.get("rate_mam", 2),
            lang_ids=_resolve_mam_languages(s.get("languages", ["English"])),
        )
        await db.execute("""
            UPDATE books SET mam_url=?, mam_status=?, mam_formats=?,
                   mam_torrent_id=?, mam_has_multiple=?, mam_my_snatched=?
            WHERE id=?
        """, (
            check["mam_url"], check["status"], check["mam_formats"],
            check["mam_torrent_id"],
            1 if check["mam_has_multiple"] else 0,
            1 if check.get("mam_my_snatched") else 0,
            book_id,
        ))
        await db.commit()
        return {
            "status": check["status"],
            "mam_url": check["mam_url"],
            "mam_torrent_id": check["mam_torrent_id"],
            "mam_title": check.get("mam_title"),
            "mam_formats": check["mam_formats"],
            "mam_has_multiple": check["mam_has_multiple"],
            "mam_my_snatched": check.get("mam_my_snatched", False),
            "match_pct": check.get("match_pct"),
            "best_format": check.get("best_format"),
            "passes_tried": check.get("passes_tried", []),
        }
    finally:
        await db.close()


@router.post("/scan-author/{author_id}")
async def mam_scan_single_author(author_id: int):
    """Scan all of an author's missing/un-scanned books against MAM.

    Phase 3d-1 (post-feedback): now spawned as a background asyncio task
    tracked via state._mam_scan_task and reported through
    state._mam_scan_progress so the unified Dashboard widget can show
    live progress and the Stop button on the widget can cancel mid-run.
    Mirrors the same lock semantics as /api/mam/scan — only one MAM
    scan running at a time, regardless of trigger source.
    """
    s = load_settings()
    if not s.get("mam_enabled") or not s.get("mam_session_id"):
        raise HTTPException(400, "MAM not configured or not enabled")
    if not s.get("mam_scanning_enabled", True):
        raise HTTPException(400, "MAM scanning is disabled — enable it in Settings")
    if state._mam_scan_progress.get("running"):
        raise HTTPException(409, "A MAM scan is already running")
    if state._mam_scan_task and not state._mam_scan_task.done():
        raise HTTPException(409, "A MAM scan is already running")

    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT name FROM authors WHERE id=?", (author_id,),
        )
        if not rows:
            raise HTTPException(404, f"Author {author_id} not found")
        author_name = rows[0][0]

        book_rows = await db.execute_fetchall(
            "SELECT id, title FROM books WHERE author_id=? AND mam_status IS NULL "
            "AND is_unreleased=0 AND hidden=0 ORDER BY title",
            (author_id,),
        )
    finally:
        await db.close()

    if not book_rows:
        # Nothing to scan — surface as a benign idle status (the unified
        # widget will render this as a "complete" row that auto-clears).
        state._mam_scan_progress = {
            "running": False, "scanned": 0, "total": 0,
            "found": 0, "possible": 0, "not_found": 0, "errors": 0,
            "status": "complete", "type": "manual",
        }
        return {"status": "complete", "message": "No un-scanned books for this author",
                "scanned": 0, "found": 0, "possible": 0, "not_found": 0}

    state._mam_scan_progress = {
        "running": True, "scanned": 0, "total": len(book_rows),
        "found": 0, "possible": 0, "not_found": 0, "errors": 0,
        "status": "scanning", "type": "manual",
    }

    delay = s.get("rate_mam", 2)
    format_priority = s.get("mam_format_priority")
    token = s["mam_session_id"]
    lang_ids = _resolve_mam_languages(s.get("languages", ["English"]))

    async def _do_scan():
        bdb = await get_db()
        try:
            for bid, btitle in book_rows:
                try:
                    check = await mam_check_book(token, btitle, author_name, format_priority, delay, lang_ids=lang_ids)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    logger.error(f"Author scan error on book {bid} ({btitle[:40]}): {e}")
                    state._mam_scan_progress["errors"] += 1
                    state._mam_scan_progress["scanned"] += 1
                    continue
                await bdb.execute("""
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
                state._mam_scan_progress["scanned"] += 1
                if check["status"] == "found":
                    state._mam_scan_progress["found"] += 1
                elif check["status"] == "possible":
                    state._mam_scan_progress["possible"] += 1
                elif check["status"] == "not_found":
                    state._mam_scan_progress["not_found"] += 1
            await bdb.commit()
            state._mam_scan_progress.update({"running": False, "status": "complete"})
        except asyncio.CancelledError:
            state._mam_scan_progress.update({"running": False, "status": "cancelled"})
            raise
        except Exception as e:
            logger.error(f"MAM single-author scan failed: {e}", exc_info=True)
            state._mam_scan_progress.update({"running": False, "status": f"error: {e}"})
        finally:
            await bdb.close()

    state._mam_scan_task = asyncio.create_task(_do_scan())
    return {"status": "started", "author": author_name, "total": len(book_rows)}


@router.post("/reset")
async def mam_reset_scans():
    """Reset all MAM scan data — clears all mam_* fields on all books."""
    db = await get_db()
    try:
        await db.execute(
            "UPDATE books SET mam_url=NULL, mam_status=NULL, mam_formats=NULL, "
            "mam_torrent_id=NULL, mam_has_multiple=0, mam_my_snatched=0"
        )
        await db.execute("DELETE FROM mam_scan_log")
        await db.commit()
        return {"status": "ok", "message": "All MAM scan data cleared"}
    finally:
        await db.close()
