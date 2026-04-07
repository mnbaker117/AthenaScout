"""
Cover image serving for AthenaScout.

Holds /api/covers/{bid}.
"""
import logging
from fastapi import APIRouter

from app.database import get_db
from app import state

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api", tags=["covers"])
