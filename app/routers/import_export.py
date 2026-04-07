"""
Import / Export endpoints for AthenaScout.

Holds /api/export, /api/books/search-url, /api/books/import-preview,
/api/books/import-add, plus the Goodreads/Hardcover fetch helpers used by
the import routes.
"""
import logging
from fastapi import APIRouter

from app.database import get_db, HF
from app import state

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api", tags=["import_export"])
