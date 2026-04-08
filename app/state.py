"""
Shared mutable state for AthenaScout background tasks.

This module holds module-level variables that need to be accessed and mutated
by multiple routers AND by the lifespan context manager in main.py. Because
Python modules are singletons within a process, all consumers that import
from `app.state` see the same values.

Usage:
    from app import state
    if state._lookup_task and not state._lookup_task.done():
        state._lookup_task.cancel()

IMPORTANT: Access these as attributes of the `state` module (e.g.,
`state._lookup_task`), not via `from app.state import _lookup_task`. Direct
imports create a local binding that won't see updates from other modules.

For REASSIGNMENT (e.g., `state._lookup_task = new_task`), you MUST use the
module attribute form. Bare assignment `_lookup_task = new_task` inside a
function only rebinds a local variable — the shared state is not updated.
"""
from typing import Optional, List, Dict, Any
import asyncio


# ─── Library discovery cache ─────────────────────────────────
# Populated in lifespan startup, mutated by /api/libraries/rescan
_discovered_libraries: List[dict] = []


# ─── Calibre sync check tracking ─────────────────────────────
# Updated after every successful calibre sync (manual or scheduled)
# Displayed on dashboard via /api/stats
_last_calibre_check: Dict[str, Any] = {"at": None, "synced": False}

# True while a Calibre library sync is actively running (manual or
# scheduled). MAM scan batches and other write-heavy background tasks
# check this flag before grabbing the DB write lock, so they yield
# cleanly instead of racing against the bulk upsert and getting hit
# with "database is locked" errors after the busy_timeout expires.
# Always cleared in a try/finally block — never leave this stuck True.
_calibre_sync_in_progress: bool = False


# ─── Author lookup scan state ────────────────────────────────
_lookup_task: Optional[asyncio.Task] = None
_lookup_progress: Dict[str, Any] = {
    "running": False,
    "checked": 0,
    "total": 0,
    "current_author": "",
    "new_books": 0,
    "status": "idle",
    "type": "none",
}


# ─── MAM scan state ──────────────────────────────────────────
_mam_scan_task: Optional[asyncio.Task] = None
_mam_scan_progress: Dict[str, Any] = {
    "running": False,
    "scanned": 0,
    "total": 0,
    "found": 0,
    "possible": 0,
    "not_found": 0,
    "errors": 0,
    "status": "idle",
    "type": "none",
}
_mam_full_scan_task: Optional[asyncio.Task] = None
