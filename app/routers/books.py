"""
Book query and mutation endpoints for AthenaScout.

Holds /api/books, /api/missing, /api/upcoming, hide/unhide/dismiss/edit/add/delete.
"""
import logging
from fastapi import APIRouter

from app.database import get_db, HF
from app import state

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api", tags=["books"])
