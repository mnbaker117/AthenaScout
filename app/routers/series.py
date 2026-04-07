"""
Series endpoints for AthenaScout.

Holds /api/series and /api/series/{sid}.
"""
import logging
from fastapi import APIRouter

from app.database import get_db, HF
from app import state

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api", tags=["series"])
