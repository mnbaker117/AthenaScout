"""
Author lookup, sync, and full-rescan endpoints for AthenaScout.

Holds /api/sync/calibre, /api/sync, /api/sync/lookup, /api/lookup,
/api/lookup/cancel, /api/lookup/status, /api/sync/full-rescan,
/api/scanning/author/toggle, /api/scanning/mam/toggle.
"""
import logging
from fastapi import APIRouter

from app.database import get_db
from app import state

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api", tags=["scan"])
