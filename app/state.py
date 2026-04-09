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
from typing import Optional, List, Dict, Any, Callable, Awaitable
import asyncio
import logging

_log = logging.getLogger("athenascout")


def supervised_task(
    coro_factory: Callable[[], Awaitable[None]],
    *,
    name: str,
    restart_on_crash: bool = True,
    restart_delay: float = 5.0,
) -> asyncio.Task:
    """Wrap a long-running background coroutine with exception logging.

    The problem: `asyncio.create_task(some_coro())` silently loses exceptions
    unless the task is awaited. For fire-and-forget schedulers (`_mam_scheduler`,
    etc) a crash shows up as a one-line "Task exception was never retrieved"
    at interpreter shutdown — no traceback in normal logs, no restart, no
    visible failure mode.

    Wrap the coroutine in an outer try/except that logs the full traceback
    through the project logger, and optionally restarts after a delay so a
    transient failure (DB lock, network hiccup) doesn't silently take a
    scheduler out of service for the rest of the process lifetime.

    `coro_factory` is a zero-arg callable that RETURNS a fresh coroutine —
    not a coroutine object — because restarting requires building a new one
    on each crash (coroutines can only be awaited once).

    Cancellation is propagated: if the caller cancels the returned task,
    CancelledError bubbles out without being logged or restarted.
    """
    async def _runner():
        while True:
            try:
                await coro_factory()
                _log.info(f"supervised task {name!r} completed normally")
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                _log.exception(f"supervised task {name!r} crashed")
                if not restart_on_crash:
                    return
                _log.warning(
                    f"supervised task {name!r} restarting in {restart_delay}s"
                )
                try:
                    await asyncio.sleep(restart_delay)
                except asyncio.CancelledError:
                    raise
    return asyncio.create_task(_runner(), name=name)


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

# Phase 3d-2: per-book progress for the active Calibre sync. Populated
# by sync_calibre() during its book-upsert pass so the unified scan
# widget can show "Syncing 142/675 — The Final Empire" the same way it
# shows source/MAM scan progress. The MAM-blocking semantics still
# come from `_calibre_sync_in_progress` above — this dict is purely
# UX/visibility. Reset to idle when no sync is active.
_calibre_sync_progress: Dict[str, Any] = {
    "running": False,
    "current": 0,
    "total": 0,
    "current_book": "",
    "books_new": 0,
    "books_updated": 0,
    "status": "idle",
    "type": "none",
}


# ─── Author lookup scan state ────────────────────────────────
_lookup_task: Optional[asyncio.Task] = None
_lookup_progress: Dict[str, Any] = {
    "running": False,
    "checked": 0,
    "total": 0,
    "current_author": "",
    # Phase 3d-2: per-book progress for source scans. Set by Goodreads/
    # Kobo/Hardcover via the `_on_book` callback that lookup.py stashes
    # on each source instance. Only emitted for books that actually
    # consume work (DETAIL page fetches + URL-backfill matches) — the
    # filter-noise SKIPs (foreign/set/translation/contributor/unowned)
    # never reach the user. Cleared between authors so a stale title
    # from a previous author doesn't bleed into the next.
    "current_book": "",
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
    # Phase 3d-2: title of the book MAM is currently checking. Updated
    # per-book by scan_books_batch (via the on_progress stats dict) and
    # by the single-author scan loop in routers/mam.py. MAM intentionally
    # shows EVERY attempt — unlike source scans, MAM has no filter-noise
    # to hide.
    "current_book": "",
    "status": "idle",
    "type": "none",
}
_mam_full_scan_task: Optional[asyncio.Task] = None
