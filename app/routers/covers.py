"""
Cover image serving for AthenaScout.

Holds /api/covers/{bid}.
"""
import logging
from pathlib import Path
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.database import get_db

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api", tags=["covers"])


@router.get("/covers/{bid}")
async def get_cover(bid: int):
    db = await get_db()
    try:
        r = await (await db.execute("SELECT cover_path FROM books WHERE id=?", (bid,))).fetchone()
        if not r or not r["cover_path"]:
            raise HTTPException(404)
        p = Path(r["cover_path"])
        if not p.exists():
            raise HTTPException(404)
        return FileResponse(p, media_type="image/jpeg")
    finally:
        await db.close()
