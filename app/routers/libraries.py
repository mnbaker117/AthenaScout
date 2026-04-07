"""
Library discovery, switching, and validation endpoints for AthenaScout.

Holds /api/libraries, /api/libraries/active, /api/libraries/validate-path,
/api/libraries/rescan.
"""
import logging
from fastapi import APIRouter

from app.database import get_db
from app import state

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api", tags=["libraries"])
