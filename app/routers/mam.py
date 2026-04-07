"""
MyAnonamouse integration endpoints for AthenaScout.

Holds /api/mam/* — validation, status, scan/cancel/status, test-scan,
full-scan flow, toggle, books list, reset.
"""
import logging
from fastapi import APIRouter

from app.database import get_db
from app import state

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api/mam", tags=["mam"])
