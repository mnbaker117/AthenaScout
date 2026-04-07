"""
App-level config and status endpoints for AthenaScout.

Holds /api/settings, /api/settings/reset, /api/health, /api/platform, /api/stats.
"""
import logging
from fastapi import APIRouter

from app.database import get_db, HF
from app import state

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api", tags=["config"])
