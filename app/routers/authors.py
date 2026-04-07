"""
Author endpoints for AthenaScout.

Holds /api/authors, /api/authors/{aid}, lookup/full-rescan triggers,
clear-scan-data.
"""
import logging
from fastapi import APIRouter

from app.database import get_db, HF
from app import state

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api", tags=["authors"])
