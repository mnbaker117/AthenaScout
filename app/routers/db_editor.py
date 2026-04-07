"""
Database browser and row editor endpoints for AthenaScout.

Holds /api/db/tables, /api/db/table/{table_name}/schema,
/api/db/table/{table_name} (list/update/add), and row delete.
"""
import logging
from fastapi import APIRouter

from app.database import get_db
from app import state

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api/db", tags=["db_editor"])


# ─── Editor constants (only used by this router) ───────────
DB_TABLES = {"books", "authors", "series", "sync_log", "mam_scan_log"}

DB_FK_RESOLVERS = {
    "books": {
        "author_id": {
            "table": "authors",
            "name_col": "name",
            "create_cols": {
                "sort_name": lambda name: ", ".join(reversed(name.split(" ", 1))) if " " in name else name
            },
        },
        "series_id": {"table": "series", "name_col": "name"},
    }
}
