"""
Series endpoints for AthenaScout.

Holds /api/series and /api/series/{sid}.
"""
import logging
from fastapi import APIRouter, HTTPException, Query

from app.database import get_db, HF

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api", tags=["series"])


@router.get("/series/{sid}")
async def get_series(sid: int):
    db = await get_db()
    try:
        r = await (await db.execute("SELECT s.*, a.name as author_name FROM series s LEFT JOIN authors a ON s.author_id=a.id WHERE s.id=?", (sid,))).fetchone()
        if not r:
            raise HTTPException(404)
        s = dict(r)
        s["books"] = [dict(b) for b in await (await db.execute(f"SELECT b.*, a.name as author_name, sr.name as series_name, (SELECT COUNT(*) FROM books b2 WHERE b2.series_id=b.series_id AND b2.hidden=0) as series_total FROM books b JOIN authors a ON b.author_id=a.id LEFT JOIN series sr ON b.series_id=sr.id WHERE b.series_id=? AND {HF} ORDER BY COALESCE(b.series_index,999), b.pub_date ASC", (sid,))).fetchall()]
        return s
    finally:
        await db.close()


@router.get("/series")
async def list_series(search: str = Query(None), sort: str = Query("name"), sort_dir: str = Query("asc"), has_missing: bool = Query(None)):
    db = await get_db()
    try:
        q = f"""SELECT s.*, a.name as author_name,
            COUNT(DISTINCT CASE WHEN {HF} THEN b.id END) as book_count,
            SUM(CASE WHEN b.owned=1 AND {HF} THEN 1 ELSE 0 END) as owned_count,
            SUM(CASE WHEN b.owned=0 AND {HF} THEN 1 ELSE 0 END) as missing_count,
            CASE WHEN COUNT(DISTINCT b.author_id) > 1 THEN 1 ELSE 0 END as multi_author
            FROM series s LEFT JOIN authors a ON s.author_id=a.id LEFT JOIN books b ON s.id=b.series_id"""
        p = []
        c = []
        if search:
            c.append("(s.name LIKE ? OR a.name LIKE ?)")
            p.extend([f"%{search}%"] * 2)
        if c:
            q += " WHERE " + " AND ".join(c)
        q += " GROUP BY s.id"
        if has_missing:
            q += " HAVING missing_count > 0"
        d = "DESC" if sort_dir == "desc" else "ASC"
        q += {"missing": f" ORDER BY missing_count {d}", "author": f" ORDER BY a.sort_name {d}"}.get(sort, f" ORDER BY s.name {d}")
        return {"series": [dict(r) for r in await (await db.execute(q, p)).fetchall()]}
    finally:
        await db.close()
