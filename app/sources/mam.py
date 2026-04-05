"""
MyAnonamouse (MAM) integration for AthenaScout.

Ported from the standalone mam_calibre_check.py script into an async service
that integrates with AthenaScout's SQLite database and FastAPI scheduler.

Authentication:
  MAM uses IP-locked session tokens. The user generates one from
  MAM → Preferences → Security and enters it in AthenaScout settings.
  On each scan run, we first ping MAM's dynamic seedbox endpoint to
  register the Docker container's IP, then search for books.

Search strategy (5-pass cascade, same as standalone script):
  Pass 1 — author + full title
  Pass 2 — author + core title  (volume/series prefix stripped)
  Pass 3 — author + subtitle right  (part after colon)
  Pass 4 — author + short title  (part before colon)
  Pass 5 — title words only  (no author, loose cleaning)

Format preference:
  When multiple MAM results match a book, each result is scored by:
    1. Highest-priority ebook format present (user-configurable)
    2. Number of formats available (more = more choice)
  The best result's torrent page is linked. If multiple distinct uploads
  exist for the same book, a flag is set so the UI can note it.
"""

import asyncio
import json
import logging
import re
import time as _time
from typing import Optional
from urllib.parse import urlencode

# aiohttp removed — using requests via asyncio.to_thread() instead
# (MAM's TLS fingerprinting rejects Python aiohttp)

logger = logging.getLogger("athenascout.mam")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAM_SEARCH_URL = "https://www.myanonamouse.net/tor/js/loadSearchJSONbasic.php"
MAM_BROWSE_BASE = "https://www.myanonamouse.net/tor/browse.php"
MAM_TORRENT_BASE = "https://www.myanonamouse.net/t"
MAM_DYNIP_URL = "https://t.myanonamouse.net/json/dynamicSeedbox.php"
EBOOK_CATEGORY = "14"

# Match quality thresholds (same as standalone script)
MATCH_MIN_PCT = 25.0       # below this → junk, skip
MATCH_PROMOTE_PCT = 50.0   # at or above + author match → promote to "found"

# Status constants
STATUS_FOUND = "found"
STATUS_POSSIBLE = "possible"
STATUS_NOT_FOUND = "not_found"
STATUS_AUTH_ERROR = "auth_error"
STATUS_ERROR = "error"

# Default delay between MAM API requests (seconds)
DEFAULT_DELAY = 2.0

# How many results to request per search (enough to find format variations)
RESULTS_PER_PAGE = 25

# Default format priority (user can override in settings)
DEFAULT_FORMAT_PRIORITY = ["epub", "azw3", "mobi", "kfx", "pdf", "html", "lit", "rtf", "doc"]

# All known ebook format tokens MAM might return in filetypes
KNOWN_EBOOK_FORMATS = {
    "epub", "mobi", "azw", "azw3", "kfx", "pdf", "html", "htm",
    "lit", "rtf", "doc", "docx", "djvu", "fb2", "txt", "cbr", "cbz",
}


# ---------------------------------------------------------------------------
# Regex patterns (ported from standalone script)
# ---------------------------------------------------------------------------
HONORIFICS = re.compile(
    r'\b(Mr|Mrs|Ms|Miss|Dr|PhD|Professor|Prof)\.?\s?\b', re.IGNORECASE
)
RE_ADD_SPACE = re.compile(r'(?<=\S)[;:,.\-\u2014](?=\S)')
RE_PUNCT = re.compile(r'[;:,.\-\u2014]')
RE_SPECIAL = re.compile(r'[^a-zA-Z0-9\s]')
RE_SPECIAL_KEEP_HYPHEN = re.compile(r'[^a-zA-Z0-9\s\-]')

SUBTITLE_DELIMITERS = [':', ' - ', '|']

RE_VOL_PREFIX = re.compile(
    r'^.{2,}?'
    r'(?:[,\s]+)'
    r'(?:Vol(?:ume)?|Book|Part|Bk|Pt)'
    r'[\s.]*'
    r'(?:\d+(?:\.\d+)?|[IVXLCDM]+)'
    r'(?:\s*[:\-]\s*|\s+)',
    re.IGNORECASE,
)
RE_NUM_PREFIX = re.compile(
    r'^.{2,}?[,\s]+#\d+(?:\s*[:\-]\s*|\s+)',
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Text helpers (ported from standalone)
# ---------------------------------------------------------------------------

def _clean_title(title: str) -> str:
    """Normalise a title for MAM search (strips hyphens and punctuation)."""
    t = RE_ADD_SPACE.sub(' ', title)
    t = RE_PUNCT.sub('', t)
    t = RE_SPECIAL.sub('', t)
    return ' '.join(t.split())


def _clean_title_loose(title: str) -> str:
    """Minimal cleaning for title-only searches (keeps hyphens)."""
    t = RE_SPECIAL_KEEP_HYPHEN.sub('', title)
    return ' '.join(t.split())


def _clean_authors(authors: str) -> str:
    """Strip honorifics and periods from initials/abbreviations."""
    a = HONORIFICS.sub('', authors)
    a = re.sub(r'\.', '', a)
    return ' '.join(a.split())


def _strip_subtitle(title: str) -> Optional[str]:
    for delim in SUBTITLE_DELIMITERS:
        if delim in title:
            return title.split(delim)[0].strip()
    return None


def _extract_subtitle_part(title: str) -> Optional[str]:
    for delim in SUBTITLE_DELIMITERS:
        if delim in title:
            right = title.split(delim, 1)[1].strip()
            if len(right) >= 3:
                return right
    return None


def _extract_core_title(title: str) -> Optional[str]:
    for pattern in (RE_VOL_PREFIX, RE_NUM_PREFIX):
        m = pattern.match(title)
        if m:
            core = title[m.end():].strip()
            if len(core) >= 3:
                return core
    return None


def _build_query(authors: str, title: str) -> str:
    return f"{_clean_authors(authors)} {_clean_title(title)}"


def build_search_link(authors: str, title: str) -> str:
    """Build a clickable MAM browse URL for manual searching."""
    params = {
        "tor[text]": _build_query(authors, title),
        "tor[srchIn][author]": "true",
        "tor[srchIn][title]": "true",
        "tor[srchIn][series]": "true",
        "tor[srchIn][description]": "true",
        "tor[srchIn][filenames]": "true",
        "tor[srchIn][narrator]": "true",
        "tor[srchIn][tags]": "true",
        "tor[searchIn]": "torrents",
        "tor[searchType]": "active",
        "tor[main_cat]": EBOOK_CATEGORY,
    }
    return f"{MAM_BROWSE_BASE}?{urlencode(params)}"


def _torrent_url(torrent_id) -> str:
    """Build a direct link to a MAM torrent page."""
    return f"{MAM_TORRENT_BASE}/{torrent_id}"


def _word_match_pct(text1: str, text2: str) -> float:
    """Sorted-token word overlap percentage."""
    w1 = sorted(text1.lower().split())
    w2 = sorted(text2.lower().split())
    i = j = m = 0
    while i < len(w1) and j < len(w2):
        if w1[i] == w2[j]:
            m += 1; i += 1; j += 1
        elif w1[i] < w2[j]:
            i += 1
        else:
            j += 1
    return round(m / max(len(w1), len(w2), 1) * 100, 1)


def _author_match(calibre_authors: str, mam_result: dict) -> bool:
    """Check if MAM result author plausibly matches our author string."""
    mam_author = mam_result.get("author_info", "") or ""
    if not mam_author:
        return True

    def tokens(s: str) -> set:
        s = re.sub(r'\.', '', s.lower())
        return set(re.findall(r'[a-z]+', s))

    cal_tok = tokens(calibre_authors)
    mam_tok = tokens(str(mam_author))
    overlap = {t for t in cal_tok & mam_tok if len(t) > 1}
    return bool(overlap)


# ---------------------------------------------------------------------------
# Format preference scoring
# ---------------------------------------------------------------------------

def _parse_formats(filetypes_str: str) -> list[str]:
    """
    Parse MAM filetypes string into a list of known ebook formats.
    Input: space-separated string like "epub mobi pdf" or "epub mp3 m4a"
    Output: list of ebook formats only, e.g. ["epub", "mobi", "pdf"]
    """
    if not filetypes_str:
        return []
    all_tokens = set(f.strip().lower() for f in filetypes_str.split() if f.strip())
    # Only keep tokens that are known ebook formats (filter out audio: mp3, m4a, etc.)
    return sorted(t for t in all_tokens if t in KNOWN_EBOOK_FORMATS)


def _format_score(formats: list[str], priority: list[str]) -> tuple[int, int, str]:
    """
    Score a torrent's formats against user's priority list.

    Returns (priority_rank, format_count, best_format):
      priority_rank: 0 = user's #1 format found, 1 = #2, etc. 999 = none found
      format_count:  total ebook formats in this torrent (more = more choice)
      best_format:   name of the highest-priority format found

    Comparison logic:
      - Lower priority_rank is always better (user's preferred format wins)
      - Among same rank, higher format_count wins (more choice for user)
    """
    fmt_set = set(f.lower() for f in formats)
    for rank, pref in enumerate(priority):
        if pref.lower() in fmt_set:
            return (rank, len(formats), pref.lower())
    # No preferred format found — still return format info
    return (999, len(formats), formats[0] if formats else "unknown")


def _pick_best_result(
    matches: list[dict],
    format_priority: list[str],
) -> dict:
    """
    From a list of scored MAM matches, pick the best one based on format preference.

    Each match dict has: torrent_id, title, formats, match_pct, author_matched, search_link, raw

    Selection logic:
      1. Find which results contain the user's highest-priority format
      2. Among those, prefer the one with the most total formats (more choice)
      3. If tied, prefer higher match_pct
    """
    if not matches:
        return None

    scored = []
    for m in matches:
        rank, count, best_fmt = _format_score(m["formats"], format_priority)
        scored.append({
            **m,
            "fmt_rank": rank,
            "fmt_count": count,
            "best_format": best_fmt,
        })

    # Sort: lowest fmt_rank first, then highest fmt_count, then highest match_pct
    scored.sort(key=lambda x: (x["fmt_rank"], -x["fmt_count"], -x["match_pct"]))
    return scored[0]


# ---------------------------------------------------------------------------
# Session / auth (async versions)
# ---------------------------------------------------------------------------

def _build_headers(token: str) -> dict:
    """Build headers for MAM API requests. Uses curl User-Agent to pass TLS fingerprinting."""
    return {
        "Content-Type": "application/json",
        "User-Agent": "curl/8.0",
        "Cookie": f"mam_id={token}",
    }


async def register_ip(session_id: str, skip_ip_update: bool = False) -> dict:
    """
    Ping MAM's dynamic seedbox endpoint to register this server's IP.
    Returns {"success": bool, "message": str}
    """
    if skip_ip_update:
        return {"success": True, "message": "Skipped IP registration (ASN-locked session)"}

    logger.info("Registering server IP with MAM...")

    def _do_request():
        import requests
        return requests.get(
            MAM_DYNIP_URL,
            headers=_build_headers(session_id),
            timeout=15
        )

    try:
        resp = await asyncio.to_thread(_do_request)
        body = resp.text.strip()
        logger.debug(f"IP registration response: {body}")

        if "Completed" in body or "No Change" in body:
            logger.info("IP registration OK")
            return {"success": True, "message": body}
        elif "No Session Cookie" in body:
            return {"success": False, "message": "Token not recognised by MAM"}
        elif "Incorrect session type" in body:
            logger.warning("ASN-locked session — IP registration skipped")
            return {"success": True, "message": "ASN-locked session — IP registration not needed"}
        elif "<html" in body.lower():
            return {"success": False, "message": "Got HTML login page — token wrong or expired"}
        else:
            return {"success": False, "message": f"Unexpected response: {body[:200]}"}
    except asyncio.TimeoutError:
        return {"success": False, "message": "Timeout connecting to MAM"}
    except Exception as e:
        return {"success": False, "message": f"Network error: {str(e)}"}


async def verify_search_auth(session_id: str) -> dict:
    """Verify MAM search API access with a test query."""
    logger.info("Verifying MAM search API access...")

    def _do_request():
        import requests
        return requests.post(
            MAM_SEARCH_URL,
            headers=_build_headers(session_id),
            data=json.dumps({
                "tor": {
                    "text": "test",
                    "srchIn": {"title": "true"},
                    "searchType": "active",
                    "searchIn": "torrents",
                    "main_cat": [EBOOK_CATEGORY],
                    "startNumber": "0",
                },
                "perpage": 5,
            }),
            timeout=15,
        )

    try:
        resp = await asyncio.to_thread(_do_request)
        if resp.status_code == 200 and len(resp.text) > 0:
            logger.info("MAM search auth OK")
            return {"success": True, "message": "Connection successful"}
        elif resp.status_code == 200 and len(resp.text) == 0:
            return {"success": False, "message": "HTTP 200 but empty response — token may be invalid or expired"}
        elif resp.status_code == 403:
            return {"success": False,
                    "message": "HTTP 403 — session rejected. Check token is valid for this server's IP/ASN."}
        else:
            return {"success": False, "message": f"Unexpected HTTP {resp.status_code}"}
    except Exception as e:
        return {"success": False, "message": f"Network error: {str(e)}"}


async def validate_connection(session_id: str, skip_ip_update: bool = False) -> dict:
    """Full validation: IP registration + search auth test."""
    ip_result = await register_ip(session_id, skip_ip_update)
    if not ip_result["success"]:
        return {
            "success": False,
            "message": f"IP registration failed: {ip_result['message']}",
            "ip_result": ip_result, "search_result": None,
        }
    search_result = await verify_search_auth(session_id)
    return {
        "success": search_result["success"],
        "message": search_result["message"] if search_result["success"]
                   else f"Search auth failed: {search_result['message']}",
        "ip_result": ip_result, "search_result": search_result,
    }


# ---------------------------------------------------------------------------
# MAM search (async)
# ---------------------------------------------------------------------------

class _AuthError(Exception):
    pass


async def _mam_search(
    token: str,
    authors: Optional[str],
    title: str,
    perpage: int = RESULTS_PER_PAGE,
) -> Optional[dict]:
    """
    Search MAM using requests library (via asyncio.to_thread).
    Uses curl User-Agent to pass MAM's TLS fingerprinting.
    Pass authors=None for title-only search (pass 5).
    Returns parsed JSON response or None on error.
    Raises _AuthError on 401/403.
    """
    if authors is None:
        query = _clean_title_loose(title)
    else:
        query = _build_query(authors, title)

    payload = json.dumps({
        "tor": {
            "text": query,
            "srchIn": {
                "author": "true",
                "description": "true",
                "filenames": "true",
                "narrator": "true",
                "series": "true",
                "tags": "true",
                "title": "true",
            },
            "searchType": "active",
            "searchIn": "torrents",
            "main_cat": [EBOOK_CATEGORY],
            "browseFlagsHideVsShow": "0",
            "startDate": "", "endDate": "", "hash": "",
            "sortType": "default",
            "startNumber": "0",
        },
        "perpage": perpage,
    })

    def _do_request():
        import requests
        return requests.post(
            MAM_SEARCH_URL,
            headers=_build_headers(token),
            data=payload,
            timeout=20,
        )

    try:
        resp = await asyncio.to_thread(_do_request)
        if resp.status_code in (401, 403):
            raise _AuthError(f"HTTP {resp.status_code}")
        resp.raise_for_status()
        if not resp.text or len(resp.text) == 0:
            return None
        return resp.json()
    except _AuthError:
        raise
    except Exception as e:
        logger.debug(f"Search error for '{query[:60]}': {e}")
        return None


# ---------------------------------------------------------------------------
# Result evaluation — scores all results from a search
# ---------------------------------------------------------------------------

def _evaluate_results(
    data: list[dict],
    calibre_title: str,
    search_title: str,
    authors: str,
    format_priority: list[str],
) -> list[dict]:
    """
    Evaluate all MAM search results for a book. Returns a list of viable
    matches, each with scoring info. Empty list = no viable matches.

    Each returned match dict:
      torrent_id, mam_title, formats, format_str, match_pct,
      author_matched, search_link, raw
    """
    matches = []
    for item in data:
        mam_title = item.get("title", "") or item.get("name", "") or ""
        torrent_id = item.get("id", "")

        # Check author match
        author_ok = _author_match(authors, item)

        # Score title match (take best of full title vs search term)
        pct_full = _word_match_pct(calibre_title, mam_title)
        pct_search = _word_match_pct(search_title, mam_title)
        pct = max(pct_full, pct_search)

        if pct < MATCH_MIN_PCT:
            logger.debug(f"  Eval: SKIP '{mam_title[:50]}' — match {pct}% < {MATCH_MIN_PCT}% min")
            continue  # junk result

        # Parse ebook formats from filetypes field
        filetypes_raw = item.get("filetype", "") or item.get("filetypes", "") or ""
        formats = _parse_formats(filetypes_raw)

        matches.append({
            "torrent_id": str(torrent_id),
            "mam_title": mam_title,
            "formats": formats,
            "format_str": ",".join(formats) if formats else filetypes_raw.strip(),
            "match_pct": pct,
            "author_matched": author_ok,
            "seeders": int(item.get("seeders", 0) or 0),
        })

    return matches


# ---------------------------------------------------------------------------
# Per-book check — five-pass cascade with format-aware scoring
# ---------------------------------------------------------------------------

async def check_book(
    token: str,
    title: str,
    authors: str,
    format_priority: list[str] = None,
    delay: float = DEFAULT_DELAY,
) -> dict:
    """
    Five-pass search cascade for a single book, with format preference scoring.

    Returns dict with:
      status, mam_url, mam_torrent_id, mam_title, mam_formats, mam_has_multiple,
      match_pct, best_format, passes_tried, search_link, error
    """
    if format_priority is None:
        format_priority = DEFAULT_FORMAT_PRIORITY

    # Default result — search link as fallback URL
    fallback_search_link = build_search_link(authors, title)
    result = {
        "status": STATUS_NOT_FOUND,
        "mam_url": fallback_search_link,
        "mam_torrent_id": None,
        "mam_title": None,
        "mam_formats": None,
        "mam_has_multiple": False,
        "match_pct": None,
        "best_format": None,
        "passes_tried": [],
        "search_link": fallback_search_link,
        "error": None,
    }

    # Track best "possible" across all passes
    best_possible = None

    def _try_evaluate(pass_num: int, resp: dict, search_title: str) -> bool:
        """
        Evaluate all results from a search pass. Returns True if cascade should stop.
        Updates result dict and best_possible as side effects.
        """
        nonlocal best_possible

        if not resp or not resp.get("data"):
            logger.debug(f"  Pass {pass_num}: no data in response")
            return False

        data = resp["data"]
        matches = _evaluate_results(data, title, search_title, authors, format_priority)

        if not matches:
            return False

        # Separate into author-confirmed and author-unconfirmed
        confirmed = [m for m in matches if m["author_matched"]]
        all_viable = confirmed if confirmed else matches

        # Check if multiple distinct uploads exist (different torrent IDs)
        unique_ids = set(m["torrent_id"] for m in all_viable)
        has_multiple = len(unique_ids) > 1

        # Pick best result by format preference
        best = _pick_best_result(all_viable, format_priority)
        if not best:
            return False

        pct = best["match_pct"]

        # Build candidate info
        candidate = {
            "pass": pass_num,
            "torrent_id": best["torrent_id"],
            "mam_title": best["mam_title"],
            "formats": best["format_str"],
            "has_multiple": has_multiple,
            "match_pct": pct,
            "best_format": best.get("best_format", ""),
            "author_matched": best["author_matched"],
        }

        # Promote to FOUND if match is strong and author checks out
        if pct >= MATCH_PROMOTE_PCT and best["author_matched"]:
            result["status"] = STATUS_FOUND
            result["passes_tried"].append(pass_num)
            result["mam_url"] = _torrent_url(best["torrent_id"])
            result["mam_torrent_id"] = best["torrent_id"]
            result["mam_title"] = best["mam_title"]
            result["mam_formats"] = best["format_str"]
            result["mam_has_multiple"] = has_multiple
            result["match_pct"] = pct
            result["best_format"] = best.get("best_format", "")
            return True  # stop cascade

        # Otherwise save as best possible so far
        if best_possible is None or pct > best_possible["match_pct"]:
            best_possible = candidate
        return False

    try:
        # --- Pass 1: author + full title ---
        r = await _mam_search(token, authors, title)
        await asyncio.sleep(delay)
        result["passes_tried"].append(1)
        if _try_evaluate(1, r, title):
            return result

        # --- Pass 2: author + core title (volume prefix stripped) ---
        core = _extract_core_title(title)
        if core:
            r = await _mam_search(token, authors, core)
            await asyncio.sleep(delay)
            if _try_evaluate(2, r, core):
                return result

        # --- Pass 3: author + subtitle right (part after colon) ---
        sub_right = _extract_subtitle_part(title)
        if sub_right and sub_right != core:
            r = await _mam_search(token, authors, sub_right)
            await asyncio.sleep(delay)
            if _try_evaluate(3, r, sub_right):
                return result

        # --- Pass 4: author + short title (part before colon) ---
        short = _strip_subtitle(title)
        if short and short != title and short != core:
            r = await _mam_search(token, authors, short)
            await asyncio.sleep(delay)
            if _try_evaluate(4, r, short):
                return result

        # --- Pass 5: title only (no author), loose cleaning ---
        title_only = core or sub_right or short or title
        r = await _mam_search(token, None, title_only)
        await asyncio.sleep(delay)
        if _try_evaluate(5, r, title_only):
            return result

    except _AuthError as e:
        result["status"] = STATUS_AUTH_ERROR
        result["error"] = str(e)
        return result

    # No pass hit promotion — use best possible if we have one
    if best_possible:
        result["status"] = STATUS_POSSIBLE
        result["mam_url"] = _torrent_url(best_possible["torrent_id"])
        result["mam_torrent_id"] = best_possible["torrent_id"]
        result["mam_title"] = best_possible["mam_title"]
        result["mam_formats"] = best_possible["formats"]
        result["mam_has_multiple"] = best_possible["has_multiple"]
        result["match_pct"] = best_possible["match_pct"]
        result["best_format"] = best_possible.get("best_format", "")
        result["passes_tried"] = [best_possible["pass"]]

    return result


# ---------------------------------------------------------------------------
# Batch scanning — processes books from the DB
# ---------------------------------------------------------------------------

async def scan_books_batch(
    db,
    session_id: str,
    limit: int = 100,
    delay: float = DEFAULT_DELAY,
    skip_ip_update: bool = False,
    format_priority: list[str] = None,
    on_progress: callable = None,
    cancel_check: callable = None,
) -> dict:
    """
    Scan a batch of books that don't yet have MAM data.
    Returns {"scanned": int, "found": int, "possible": int,
             "not_found": int, "errors": int, "error": str|None}
    """
    if format_priority is None:
        format_priority = DEFAULT_FORMAT_PRIORITY

    # Register IP first
    ip_result = await register_ip(session_id, skip_ip_update)
    if not ip_result["success"]:
        return {"scanned": 0, "found": 0, "possible": 0, "not_found": 0,
                "errors": 0, "error": f"IP registration failed: {ip_result['message']}"}

    # Get books needing scan (no mam_status yet, not upcoming)
    rows = await db.execute_fetchall("""
        SELECT b.id, b.title, a.name as author_name, b.owned, b.is_unreleased
        FROM books b
        JOIN authors a ON b.author_id = a.id
        WHERE b.mam_status IS NULL
          AND b.is_unreleased = 0
          AND b.hidden = 0
        ORDER BY b.owned DESC, b.id ASC
        LIMIT ?
    """, (limit,))

    if not rows:
        logger.info("MAM scan: no books need scanning")
        return {"scanned": 0, "found": 0, "possible": 0, "not_found": 0,
                "errors": 0, "error": None}

    logger.info(f"MAM scan: processing {len(rows)} books (limit={limit})")
    stats = {"scanned": 0, "found": 0, "possible": 0, "not_found": 0, "errors": 0, "error": None}

    for i, row in enumerate(rows):
        book_id, book_title, author_name = row[0], row[1], row[2]

        logger.debug(f"MAM [{i+1}/{len(rows)}] {book_title[:65]} — {author_name[:35]}")

        check = await check_book(session_id, book_title, author_name, format_priority, delay)
        stats["scanned"] += 1

        # Write result to DB
        await db.execute("""
            UPDATE books SET mam_url=?, mam_status=?, mam_formats=?,
                   mam_torrent_id=?, mam_has_multiple=?
            WHERE id=?
        """, (
            check["mam_url"],
            check["status"],
            check["mam_formats"],
            check["mam_torrent_id"],
            1 if check["mam_has_multiple"] else 0,
            book_id,
        ))

        if check["status"] == STATUS_FOUND:
            stats["found"] += 1
        elif check["status"] == STATUS_POSSIBLE:
            stats["possible"] += 1
        elif check["status"] == STATUS_AUTH_ERROR:
            stats["errors"] += 1
            stats["error"] = check.get("error", "Auth error")
            logger.error(f"MAM auth error — stopping scan: {check.get('error')}")
            await db.commit()
            return stats
        elif check["status"] == STATUS_ERROR:
            stats["errors"] += 1
        else:
            stats["not_found"] += 1

        if on_progress:
            on_progress(dict(stats))

        if cancel_check and cancel_check():
            logger.info(f"MAM scan: pause requested after {stats['scanned']} books")
            await db.commit()
            return stats

        if (i + 1) % 10 == 0:
            await db.commit()

    await db.commit()
    logger.info(f"MAM scan complete: {stats}")
    return stats


# ---------------------------------------------------------------------------
# Full scan management
# ---------------------------------------------------------------------------

async def start_full_scan(db) -> dict:
    """Start a full MAM scan. Creates a tracking row in mam_scan_log."""
    running = await db.execute_fetchall(
        "SELECT id FROM mam_scan_log WHERE status='running'"
    )
    if running:
        return {"error": "A full scan is already in progress"}

    row = await db.execute_fetchall("""
        SELECT COUNT(*) FROM books
        WHERE mam_url IS NULL AND mam_status IS NULL
          AND is_unreleased = 0 AND hidden = 0
    """)
    total = row[0][0] if row else 0

    if total == 0:
        return {"error": "No books need scanning — all books already have MAM data"}

    now = _time.time()
    cursor = await db.execute(
        """INSERT INTO mam_scan_log (total_books, last_offset, batch_size, started_at, status)
           VALUES (?, 0, 250, ?, 'running')""",
        (total, now)
    )
    scan_id = cursor.lastrowid
    await db.commit()
    logger.info(f"Full MAM scan started: {total} books, scan_id={scan_id}")
    return {"id": scan_id, "total_books": total}


async def run_full_scan_batch(
    db,
    session_id: str,
    skip_ip_update: bool = False,
    delay: float = DEFAULT_DELAY,
    format_priority: list[str] = None,
) -> dict:
    """
    Run one batch of a full scan (250 books).
    Returns {"status": "batch_complete"|"scan_complete"|"error"|"no_scan", ...}
    """
    if format_priority is None:
        format_priority = DEFAULT_FORMAT_PRIORITY

    rows = await db.execute_fetchall(
        "SELECT id, total_books, last_offset, batch_size FROM mam_scan_log WHERE status='running' LIMIT 1"
    )
    if not rows:
        return {"status": "no_scan", "scanned": 0, "remaining": 0, "next_batch_in_seconds": None}

    scan_id, total_books, last_offset, batch_size = rows[0]

    # Register IP
    ip_result = await register_ip(session_id, skip_ip_update)
    if not ip_result["success"]:
        return {"status": "error", "scanned": 0, "remaining": 0,
                "next_batch_in_seconds": None,
                "error": f"IP registration failed: {ip_result['message']}"}

    # Get next batch
    book_rows = await db.execute_fetchall("""
        SELECT b.id, b.title, a.name as author_name
        FROM books b
        JOIN authors a ON b.author_id = a.id
        WHERE b.mam_url IS NULL AND b.mam_status IS NULL
          AND b.is_unreleased = 0 AND b.hidden = 0
        ORDER BY b.owned DESC, b.id ASC
        LIMIT ?
    """, (batch_size,))

    if not book_rows:
        await db.execute(
            "UPDATE mam_scan_log SET status='complete', finished_at=? WHERE id=?",
            (_time.time(), scan_id)
        )
        await db.commit()
        logger.info(f"Full MAM scan complete (scan_id={scan_id})")
        return {"status": "scan_complete", "scanned": 0, "remaining": 0, "next_batch_in_seconds": None}

    logger.info(f"Full scan batch: {len(book_rows)} books (scan_id={scan_id})")
    scanned = 0

    for i, row in enumerate(book_rows):
        book_id, book_title, author_name = row

        check = await check_book(session_id, book_title, author_name, format_priority, delay)
        scanned += 1

        await db.execute("""
            UPDATE books SET mam_url=?, mam_status=?, mam_formats=?,
                   mam_torrent_id=?, mam_has_multiple=?
            WHERE id=?
        """, (
            check["mam_url"], check["status"], check["mam_formats"],
            check["mam_torrent_id"], 1 if check["mam_has_multiple"] else 0,
            book_id,
        ))

        if check["status"] == STATUS_AUTH_ERROR:
            logger.error(f"Full scan auth error — pausing")
            await db.execute(
                "UPDATE mam_scan_log SET last_offset=last_offset+?, status='auth_error' WHERE id=?",
                (scanned, scan_id)
            )
            await db.commit()
            return {"status": "error", "scanned": scanned,
                    "remaining": total_books - last_offset - scanned,
                    "next_batch_in_seconds": None, "error": check.get("error")}

        if (i + 1) % 10 == 0:
            await db.commit()

    # Update progress
    new_offset = last_offset + scanned
    await db.execute(
        "UPDATE mam_scan_log SET last_offset=? WHERE id=?",
        (new_offset, scan_id)
    )
    await db.commit()

    # Check remaining
    remaining_row = await db.execute_fetchall("""
        SELECT COUNT(*) FROM books
        WHERE mam_url IS NULL AND mam_status IS NULL
          AND is_unreleased = 0 AND hidden = 0
    """)
    remaining = remaining_row[0][0] if remaining_row else 0

    if remaining == 0:
        await db.execute(
            "UPDATE mam_scan_log SET status='complete', finished_at=? WHERE id=?",
            (_time.time(), scan_id)
        )
        await db.commit()
        logger.info(f"Full MAM scan complete (scan_id={scan_id})")
        return {"status": "scan_complete", "scanned": scanned, "remaining": 0, "next_batch_in_seconds": None}

    logger.info(f"Full scan batch done: {scanned} scanned, {remaining} remaining")
    return {"status": "batch_complete", "scanned": scanned,
            "remaining": remaining, "next_batch_in_seconds": 3600}


async def cancel_full_scan(db) -> dict:
    rows = await db.execute_fetchall("SELECT id FROM mam_scan_log WHERE status='running'")
    if not rows:
        return {"success": False, "message": "No running scan to cancel"}
    await db.execute(
        "UPDATE mam_scan_log SET status='cancelled', finished_at=? WHERE id=?",
        (_time.time(), rows[0][0])
    )
    await db.commit()
    logger.info(f"Full MAM scan cancelled (scan_id={rows[0][0]})")
    return {"success": True, "message": "Full scan cancelled"}


async def get_full_scan_status(db) -> dict:
    rows = await db.execute_fetchall("""
        SELECT id, total_books, last_offset, batch_size, started_at, finished_at, status
        FROM mam_scan_log ORDER BY started_at DESC LIMIT 1
    """)
    if not rows:
        return {"active": False, "status": None}
    scan_id, total, offset, batch, started, finished, status = rows[0]
    return {
        "active": status == "running",
        "scan_id": scan_id, "total_books": total, "scanned": offset,
        "batch_size": batch, "status": status,
        "started_at": started, "finished_at": finished,
        "progress_pct": round(offset / max(total, 1) * 100, 1),
    }


# ---------------------------------------------------------------------------
# Stats helpers
# ---------------------------------------------------------------------------

async def get_mam_stats(db) -> dict:
    upload_row = await db.execute_fetchall(
        "SELECT COUNT(*) FROM books WHERE owned=1 AND mam_status='not_found' AND hidden=0"
    )
    download_row = await db.execute_fetchall(
        "SELECT COUNT(*) FROM books WHERE owned=0 AND mam_status IN ('found','possible') AND is_unreleased=0 AND hidden=0"
    )
    nowhere_row = await db.execute_fetchall(
        "SELECT COUNT(*) FROM books WHERE owned=0 AND mam_status='not_found' AND is_unreleased=0 AND hidden=0"
    )
    scanned_row = await db.execute_fetchall(
        "SELECT COUNT(*) FROM books WHERE mam_status IS NOT NULL AND hidden=0"
    )
    unscanned_row = await db.execute_fetchall(
        "SELECT COUNT(*) FROM books WHERE mam_status IS NULL AND is_unreleased=0 AND hidden=0"
    )
    return {
        "upload_candidates": upload_row[0][0] if upload_row else 0,
        "available_to_download": download_row[0][0] if download_row else 0,
        "missing_everywhere": nowhere_row[0][0] if nowhere_row else 0,
        "total_scanned": scanned_row[0][0] if scanned_row else 0,
        "total_unscanned": unscanned_row[0][0] if unscanned_row else 0,
    }
