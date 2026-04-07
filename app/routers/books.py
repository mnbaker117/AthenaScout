"""
Book query and mutation endpoints for AthenaScout.

Holds /api/books, /api/missing, /api/upcoming, hide/unhide/dismiss/edit/add/delete.
"""
import logging
import re
from fastapi import APIRouter, Body, HTTPException, Query

from app.database import get_db, HF

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api", tags=["books"])


# ─── Books ───────────────────────────────────────────────────
@router.get("/books")
async def get_books(search: str = Query(None), author_id: int = Query(None), series_id: int = Query(None), owned: bool = Query(None), book_type: str = Query(None), mam_status: str = Query(None), sort: str = Query("title"), sort_dir: str = Query("asc"), page: int = Query(1, ge=1), per_page: int = Query(60, ge=1, le=5000), include_hidden: bool = Query(False)):
    db = await get_db()
    try:
        c = []; p = []
        if not include_hidden: c.append(HF)
        if search: c.append("(b.title LIKE ? OR a.name LIKE ? OR COALESCE(s.name,'') LIKE ?)"); p.extend([f"%{search}%"]*3)
        if author_id: c.append("b.author_id=?"); p.append(author_id)
        if series_id: c.append("b.series_id=?"); p.append(series_id)
        if owned is True: c.append("b.owned=1")
        elif owned is False: c.append("b.owned=0")
        if book_type == "series": c.append("b.series_id IS NOT NULL")
        elif book_type == "standalone": c.append("b.series_id IS NULL")
        if mam_status == "found": c.append("b.mam_status='found'")
        elif mam_status == "possible": c.append("b.mam_status='possible'")
        elif mam_status == "not_found": c.append("b.mam_status='not_found'")
        elif mam_status == "unscanned": c.append("b.mam_status IS NULL")
        w = " AND ".join(c) if c else "1=1"
        cnt = (await (await db.execute(f"SELECT COUNT(*) c FROM books b JOIN authors a ON b.author_id=a.id LEFT JOIN series s ON b.series_id=s.id WHERE {w}", p)).fetchone())["c"]
        d = "DESC" if sort_dir == "desc" else "ASC"
        o = {"title": f"b.title {d}", "author": f"a.sort_name {d}, b.title ASC", "series": f"COALESCE(s.name,'zzz') {d}, b.series_index ASC", "date": f"b.pub_date {d}", "added": f"b.first_seen_at {d}"}.get(sort, f"b.title {d}")
        off = (page-1)*per_page
        rows = await (await db.execute(f"SELECT b.*, a.name as author_name, s.name as series_name, (SELECT COUNT(*) FROM books b2 WHERE b2.series_id=b.series_id AND b2.hidden=0) as series_total FROM books b JOIN authors a ON b.author_id=a.id LEFT JOIN series s ON b.series_id=s.id WHERE {w} ORDER BY {o} LIMIT ? OFFSET ?", p+[per_page, off])).fetchall()
        return {"books": [dict(r) for r in rows], "total": cnt, "page": page, "per_page": per_page, "pages": max(1, (cnt+per_page-1)//per_page)}
    finally: await db.close()


@router.get("/missing")
async def get_missing(**kw):
    return await get_books(owned=False, **kw)


@router.get("/upcoming")
async def get_upcoming(search: str = Query(None), sort: str = Query("date"), sort_dir: str = Query("asc"), mam_status: str = Query(None), page: int = Query(1, ge=1), per_page: int = Query(60, ge=1, le=5000)):
    db = await get_db()
    try:
        c = [HF, "b.owned=0", "b.is_unreleased=1"]; p = []
        if search: c.append("(b.title LIKE ? OR a.name LIKE ? OR COALESCE(s.name,'') LIKE ?)"); p.extend([f"%{search}%"]*3)
        if mam_status == "found": c.append("b.mam_status='found'")
        elif mam_status == "possible": c.append("b.mam_status='possible'")
        elif mam_status == "not_found": c.append("b.mam_status='not_found'")
        elif mam_status == "unscanned": c.append("b.mam_status IS NULL")
        w = " AND ".join(c)
        cnt = (await (await db.execute(f"SELECT COUNT(*) c FROM books b JOIN authors a ON b.author_id=a.id LEFT JOIN series s ON b.series_id=s.id WHERE {w}", p)).fetchone())["c"]
        d = "DESC" if sort_dir == "desc" else "ASC"
        o = {"date": f"COALESCE(b.expected_date, '9999') {d}", "title": f"b.title {d}", "author": f"a.sort_name {d}"}.get(sort, f"COALESCE(b.expected_date, '9999') {d}")
        off = (page-1)*per_page
        rows = await (await db.execute(f"SELECT b.*, a.name as author_name, s.name as series_name, (SELECT COUNT(*) FROM books b2 WHERE b2.series_id=b.series_id AND b2.hidden=0) as series_total FROM books b JOIN authors a ON b.author_id=a.id LEFT JOIN series s ON b.series_id=s.id WHERE {w} ORDER BY {o} LIMIT ? OFFSET ?", p+[per_page, off])).fetchall()
        return {"books": [dict(r) for r in rows], "total": cnt, "page": page, "per_page": per_page, "pages": max(1, (cnt+per_page-1)//per_page)}
    finally: await db.close()


# ─── Book Actions ────────────────────────────────────────────
@router.post("/books/{bid}/hide")
async def hide(bid: int):
    db = await get_db()
    try:
        await db.execute("UPDATE books SET hidden=1 WHERE id=?", (bid,))
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


@router.post("/books/{bid}/unhide")
async def unhide(bid: int):
    db = await get_db()
    try:
        await db.execute("UPDATE books SET hidden=0 WHERE id=?", (bid,))
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


@router.get("/books/hidden")
async def get_hidden():
    db = await get_db()
    try:
        rows = await (await db.execute("SELECT b.*, a.name as author_name, s.name as series_name, (SELECT COUNT(*) FROM books b2 WHERE b2.series_id=b.series_id AND b2.hidden=0) as series_total FROM books b JOIN authors a ON b.author_id=a.id LEFT JOIN series s ON b.series_id=s.id WHERE b.hidden=1 ORDER BY a.sort_name, b.title")).fetchall()
        return {"books": [dict(r) for r in rows]}
    finally:
        await db.close()


@router.post("/books/{bid}/dismiss")
async def dismiss(bid: int):
    db = await get_db()
    try:
        await db.execute("UPDATE books SET is_new=0 WHERE id=?", (bid,))
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


@router.put("/books/{bid}")
async def update_book(bid: int, data: dict = Body(...)):
    db = await get_db()
    try:
        fields = []; vals = []
        for k in ["title", "description", "pub_date", "expected_date", "isbn", "cover_url", "series_index", "source_url"]:
            if k in data:
                fields.append(f"{k}=?"); vals.append(data[k])
        # Handle MAM URL — validate format and update status
        if "mam_url" in data:
            mam_url = (data["mam_url"] or "").strip()
            if mam_url:
                mam_match = re.match(r'https?://(?:www\.)?myanonamouse\.net/t/(\d+)', mam_url)
                if not mam_match:
                    raise HTTPException(400, "Invalid MAM URL. Expected format: https://www.myanonamouse.net/t/123456")
                torrent_id = int(mam_match.group(1))
                fields.extend(["mam_url=?", "mam_status=?", "mam_torrent_id=?"])
                vals.extend([mam_url, "found", torrent_id])
            else:
                fields.extend(["mam_url=?", "mam_status=?", "mam_torrent_id=?"])
                vals.extend([None, None, None])
        if "is_unreleased" in data:
            fields.append("is_unreleased=?"); vals.append(1 if data["is_unreleased"] else 0)
        if not fields:
            return {"status": "no changes"}
        vals.append(bid)
        await db.execute(f"UPDATE books SET {', '.join(fields)} WHERE id=?", vals)
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


@router.post("/books/add")
async def add_book(data: dict = Body(...)):
    """Manually add a missing/upcoming book."""
    db = await get_db()
    try:
        title = data.get("title", "").strip()
        author_name = data.get("author_name", "").strip()
        if not title or not author_name:
            raise HTTPException(400, "Title and author are required")
        # Find or create author
        row = await (await db.execute("SELECT id FROM authors WHERE name=?", (author_name,))).fetchone()
        if row:
            aid = row["id"]
        else:
            cur = await db.execute("INSERT INTO authors (name, sort_name) VALUES (?, ?)", (author_name, author_name))
            aid = cur.lastrowid
        # Find series if specified
        sid = None
        if data.get("series_name"):
            srow = await (await db.execute("SELECT id FROM series WHERE name=? AND author_id=?", (data["series_name"], aid))).fetchone()
            if srow:
                sid = srow["id"]
            else:
                cur = await db.execute("INSERT INTO series (name, author_id) VALUES (?, ?)", (data["series_name"], aid))
                sid = cur.lastrowid
        is_unreleased = 1 if data.get("is_unreleased") else 0
        src = data.get("source", "manual")
        cur = await db.execute(
            "INSERT INTO books (title, author_id, series_id, series_index, pub_date, expected_date, is_unreleased, description, isbn, cover_url, source, source_url, owned, is_new) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,1)",
            (title, aid, sid, data.get("series_index"), data.get("pub_date"), data.get("expected_date"), is_unreleased, data.get("description"), data.get("isbn"), data.get("cover_url"), src, data.get("source_url"))
        )
        await db.commit()
        return {"status": "ok", "book_id": cur.lastrowid}
    finally:
        await db.close()


@router.post("/books/dismiss-all")
async def dismiss_all():
    db = await get_db()
    try:
        await db.execute("UPDATE books SET is_new=0 WHERE is_new=1")
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


@router.delete("/books/{bid}")
async def delete_book(bid: int):
    """Delete a book entry — only non-Calibre (discovered/imported) books can be deleted."""
    db = await get_db()
    try:
        row = await (await db.execute("SELECT id, source, owned, calibre_id FROM books WHERE id=?", (bid,))).fetchone()
        if not row:
            raise HTTPException(404, "Book not found")
        if row["calibre_id"] and row["source"] == "calibre":
            raise HTTPException(400, "Cannot delete books synced from Calibre. Remove them from Calibre instead.")
        await db.execute("DELETE FROM books WHERE id=?", (bid,))
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()
