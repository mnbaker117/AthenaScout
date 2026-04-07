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
import time
from typing import Callable, Optional
from urllib.parse import urlencode

import requests

logger = logging.getLogger("athenascout.mam")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAM_SEARCH_URL = "https://www.myanonamouse.net/tor/js/loadSearchJSONbasic.php"
MAM_BROWSE_BASE = "https://www.myanonamouse.net/tor/browse.php"
MAM_TORRENT_BASE = "https://www.myanonamouse.net/t"
MAM_DYNIP_URL = "https://t.myanonamouse.net/json/dynamicSeedbox.php"
EBOOK_CATEGORY = "14"

# ─── SQL predicates for "books needing MAM scan" ─────────────
# Two flavors exist for historical reasons. They're functionally equivalent
# in practice (mam_url and mam_status are always set together by the
# UPDATE statement that records scan results), but kept separate to
# preserve existing behavior exactly. A future cleanup could unify them
# after verifying the invariant holds across the entire codebase.
#
# Each flavor has _BARE (no table alias) and _ALIASED (with `b.` prefix)
# variants because some queries JOIN authors and need to disambiguate.

# Used by scan_books_batch — checks only mam_status (basic flavor)
_NEEDS_SCAN_BASIC_BARE = "mam_status IS NULL AND is_unreleased = 0 AND hidden = 0"
_NEEDS_SCAN_BASIC_ALIASED = "b.mam_status IS NULL AND b.is_unreleased = 0 AND b.hidden = 0"

# Used by full-scan paths — strict flavor including mam_url IS NULL
_NEEDS_SCAN_STRICT_BARE = "mam_url IS NULL AND mam_status IS NULL AND is_unreleased = 0 AND hidden = 0"
_NEEDS_SCAN_STRICT_ALIASED = "b.mam_url IS NULL AND b.mam_status IS NULL AND b.is_unreleased = 0 AND b.hidden = 0"

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

# How many results to request per search. The MAM API allows 5–1000.
# Phase 22B.2.6: bumped from 25 → 100 because, for prolific authors with
# many torrents in a series, the actual exact match would get pushed off
# page 1 by collection bundles and series-sibling torrents that the API
# ranks higher. The captured logs showed Robert Jordan's "The Eye of the
# World" being completely absent from a 25-result page despite existing
# on MAM, because Wheel of Time bundles took the top slots.
RESULTS_PER_PAGE = 100

# MAM language ID mapping. The MAM API uses numeric language IDs both for
# the request payload (`tor.browse_lang`) and for the per-result `language`
# field. We send the IDs corresponding to the user's selected languages so
# foreign editions don't consume our perpage budget or pass the title match
# threshold via shared filler words.
#
# IDs below were captured from real MAM responses during testing — DO NOT
# guess at IDs you haven't verified, because a wrong ID will silently pull
# results in an unrelated language. To add a new language: open MAM's
# torrent search, filter by that language, inspect the network request
# payload's `browse_lang` array, and add the entry here.
MAM_LANGUAGES: dict[str, int] = {
    "English": 1,
    "Spanish": 4,
    "Dutch": 22,
    "Hungarian": 28,
    "French": 36,
    "Italian": 43,
    "Portuguese": 52,
}

# Default English language ID — used when nothing in the user's language
# selection resolves to a known MAM ID, so we never accidentally send an
# empty browse_lang (which would un-filter the search entirely).
_ENGLISH_LANG_ID = MAM_LANGUAGES["English"]


def _resolve_mam_languages(language_names: list[str]) -> list[int]:
    """Convert human-readable language names to MAM browse_lang IDs.

    Names not in MAM_LANGUAGES are silently dropped (debug-logged) — we
    deliberately don't guess at IDs we haven't verified. If nothing
    resolves we fall back to English-only so the search remains filtered.
    """
    if not language_names:
        return [_ENGLISH_LANG_ID]
    ids: list[int] = []
    unknown: list[str] = []
    for name in language_names:
        mid = MAM_LANGUAGES.get(name)
        if mid is None:
            unknown.append(name)
        elif mid not in ids:
            ids.append(mid)
    if unknown:
        logger.debug(
            f"MAM language(s) not yet mapped, ignoring: {unknown}. "
            f"To add: inspect MAM's browse_lang request payload for that language "
            f"and add the numeric ID to MAM_LANGUAGES in app/sources/mam.py."
        )
    if not ids:
        logger.debug("No selected languages map to MAM IDs — defaulting to English")
        return [_ENGLISH_LANG_ID]
    return ids

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


_RE_PUNCT_TOKEN = re.compile(r"[^\w\s]+")


def _word_match_pct(text1: str, text2: str) -> float:
    """Sorted-token word overlap percentage.

    Strips punctuation before tokenizing so that "Reach:" matches "Reach"
    (Phase 22B.2.6). Without this, colons / apostrophes / commas attached
    to words caused real exact matches to score below the promote threshold,
    which silently mis-linked books in series with subtitled titles like
    "Halo: Shadows of Reach: A Master Chief Story".
    """
    def _tokens(t: str) -> list[str]:
        return sorted(_RE_PUNCT_TOKEN.sub(" ", t.lower()).split())
    w1 = _tokens(text1)
    w2 = _tokens(text2)
    i = j = m = 0
    while i < len(w1) and j < len(w2):
        if w1[i] == w2[j]:
            m += 1; i += 1; j += 1
        elif w1[i] < w2[j]:
            i += 1
        else:
            j += 1
    return round(m / max(len(w1), len(w2), 1) * 100, 1)


def _parse_author_info(raw) -> list[str]:
    """Parse MAM's author_info field into a list of author names.

    MAM returns author_info as a JSON-encoded string mapping author IDs to
    names, e.g. '{"12345":"Brandon Sanderson","6789":"Janci Patterson"}'.
    Falls back to treating the input as a plain string if JSON parsing fails.
    """
    if not raw:
        return []
    if isinstance(raw, dict):
        return [str(v) for v in raw.values() if v]
    if isinstance(raw, list):
        return [str(v) for v in raw if v]
    s = str(raw).strip()
    if not s:
        return []
    try:
        parsed = json.loads(s)
    except (ValueError, TypeError):
        return [s]
    if isinstance(parsed, dict):
        return [str(v) for v in parsed.values() if v]
    if isinstance(parsed, list):
        return [str(v) for v in parsed if v]
    return [str(parsed)]


def _author_match(calibre_authors: str, mam_result: dict) -> bool:
    """Check if MAM result author plausibly matches our author string."""
    mam_authors = _parse_author_info(mam_result.get("author_info"))
    if not mam_authors:
        return True

    def tokens(s: str) -> set:
        s = re.sub(r'\.', '', s.lower())
        return set(re.findall(r'[a-z]+', s))

    cal_tok = tokens(calibre_authors)
    mam_tok = set()
    for name in mam_authors:
        mam_tok |= tokens(name)
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


# Phase 22B.2.6 — Books at or above this match_pct are considered the
# "same book" with high confidence. The format-aware sort that prefers more
# formats is only meaningful WITHIN this set; allowing low-confidence
# matches into the format-preference comparison caused the historical bug
# where a wrong-but-multi-format result would beat the right-but-single-
# format match.
HIGH_CONFIDENCE_PCT = 80.0


def _pick_best_result(
    matches: list[dict],
    format_priority: list[str],
) -> dict:
    """
    From a list of scored MAM matches, pick the best one.

    Each match dict has: torrent_id, mam_title, formats, match_pct,
    author_matched, seeders, plus per-result fields.

    Selection logic (Phase 22B.2.6):
      1. Filter to high-confidence title matches (>= HIGH_CONFIDENCE_PCT) if
         any exist; this prevents a wrong-book-with-more-formats from beating
         a right-book-with-fewer-formats. Fall back to all matches if no
         high-confidence ones are present.
      2. Among the candidates, score by user's format preference (rank).
      3. Within the same format rank, prefer the result with the highest
         match_pct (the actual title match), then with more formats, then
         with more seeders.

    The previous version sorted by (fmt_rank, -fmt_count, -match_pct), which
    placed format count BEFORE match quality and silently produced wrong
    matches for any series where one torrent bundled extra formats.
    """
    if not matches:
        return None

    # ── Filter to high-confidence matches when possible ────────
    high = [m for m in matches if m["match_pct"] >= HIGH_CONFIDENCE_PCT]
    candidates = high if high else matches

    scored = []
    for m in candidates:
        rank, count, best_fmt = _format_score(m["formats"], format_priority)
        scored.append({
            **m,
            "fmt_rank": rank,
            "fmt_count": count,
            "best_format": best_fmt,
        })

    # Sort: lowest fmt_rank, highest match_pct, highest fmt_count, highest seeders
    scored.sort(key=lambda x: (
        x["fmt_rank"],
        -x["match_pct"],
        -x["fmt_count"],
        -x.get("seeders", 0),
    ))
    return scored[0]


# ---------------------------------------------------------------------------
# HTTP layer (sync helpers + Session + auth flow)
# ---------------------------------------------------------------------------

def _build_headers(token: str) -> dict:
    """Build headers for MAM API requests. Uses curl User-Agent to pass TLS fingerprinting."""
    return {
        "Content-Type": "application/json",
        "User-Agent": "curl/8.0",
        "Cookie": f"mam_id={token}",
    }


# ---------------------------------------------------------------------------
# HTTP helpers (sync — call via asyncio.to_thread)
# ---------------------------------------------------------------------------
# These wrap requests.get/post with the standard MAM headers. They're
# synchronous because MAM's TLS fingerprinting rejects async HTTP clients,
# so all calls go through asyncio.to_thread() at the caller. DO NOT change
# the underlying library or header format — see _build_headers for why.
#
# We use a module-level requests.Session() for connection reuse. Without it,
# every search would do a fresh TCP+TLS handshake (50-150ms each), and a
# 100-book batch with a 5-pass cascade could spend 30-60 seconds just on
# handshakes. With the Session, the connection is reused across requests
# and that overhead drops to one handshake per batch.
#
# Critical: using a Session does NOT change the TLS fingerprint — it's the
# same `requests` library producing the same ClientHello, so MAM still
# accepts the requests. Same User-Agent, same Cookie header, same data=
# POST encoding.

_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    """Lazy-initialized module-level requests.Session for connection reuse.

    Single-consumer (called from the scan loop via asyncio.to_thread).
    `requests.Session` handles stale connections automatically — if the
    server has closed the keep-alive connection, requests reconnects on
    the next call.
    """
    global _session
    if _session is None:
        _session = requests.Session()
        logger.debug("MAM HTTP session created")
    return _session


def close_session() -> None:
    """Tear down the module-level Session.

    Called from main.py's lifespan() during app shutdown. Safe to call
    multiple times — subsequent calls are no-ops.
    """
    global _session
    if _session is not None:
        try:
            _session.close()
            logger.debug("MAM HTTP session closed")
        except Exception as e:
            logger.warning(f"Error closing MAM session: {e}")
        finally:
            _session = None


def _do_get(url: str, token: str, timeout: int = 15) -> requests.Response:
    """Synchronous GET to a MAM endpoint with standard headers."""
    return _get_session().get(url, headers=_build_headers(token), timeout=timeout)


def _do_post(url: str, token: str, payload: str, timeout: int = 20) -> requests.Response:
    """Synchronous POST to a MAM endpoint with standard headers.

    `payload` should be a JSON-encoded string (use json.dumps at the call
    site). MAM expects `data=` not `json=` — DO NOT change this without
    re-testing the entire MAM integration.
    """
    return _get_session().post(url, headers=_build_headers(token), data=payload, timeout=timeout)


async def register_ip(session_id: str, skip_ip_update: bool = True) -> dict:
    """
    Ping MAM's dynamic seedbox endpoint to register this server's IP.
    Returns {"success": bool, "message": str}

    Note: skip_ip_update defaults to True because IP registration is only
    needed for non-ASN-locked sessions, and the AthenaScout codebase always
    passes True at the call site to avoid interfering with seedbox sessions.
    The False default would only fire if someone called this from a new
    code path without specifying — making the safer behavior the default.
    """
    if skip_ip_update:
        return {"success": True, "message": "Skipped IP registration (ASN-locked session)"}

    logger.info("Registering server IP with MAM...")

    try:
        resp = await asyncio.to_thread(_do_get, MAM_DYNIP_URL, session_id)
        body = resp.text.strip()
        logger.debug(f"IP registration response: {body}")

        # MAM dynamicSeedbox.php returns JSON like:
        #   {"Success": true, "msg": "Completed", "ip": "...", "ASN": 12345, "AS": "..."}
        # On failure msg may be "No Session Cookie", "Incorrect session type - ...",
        # "Invalid session - IP mismatch", "Last Change too recent", etc.
        try:
            data = resp.json()
        except Exception:
            if "<html" in body.lower():
                return {"success": False, "message": "Got HTML login page — token wrong or expired"}
            return {"success": False, "message": f"Non-JSON response: {body[:200]}"}

        msg = str(data.get("msg", "")).strip()
        if data.get("Success"):
            logger.info(f"IP registration OK ({msg or 'no message'})")
            return {
                "success": True,
                "message": msg or "OK",
                "ip": data.get("ip"),
                "asn": data.get("ASN"),
                "as_org": data.get("AS"),
            }

        # Success=false branch — interpret known msg values
        msg_l = msg.lower()
        if "incorrect session type" in msg_l:
            logger.warning("ASN-locked session — IP registration not needed")
            return {"success": True, "message": "ASN-locked session — IP registration not needed"}
        if "no session cookie" in msg_l or "invalid cookie" in msg_l:
            return {"success": False, "message": "Token not recognised by MAM"}
        if "ip mismatch" in msg_l or "asn mismatch" in msg_l:
            return {"success": False, "message": f"Session locked to a different network: {msg}"}
        if "too recent" in msg_l:
            return {"success": False, "message": f"IP change rate-limited by MAM: {msg}"}
        return {"success": False, "message": msg or f"Unexpected response: {body[:200]}"}
    except asyncio.TimeoutError:
        return {"success": False, "message": "Timeout connecting to MAM"}
    except Exception as e:
        return {"success": False, "message": f"Network error: {str(e)}"}


async def verify_search_auth(session_id: str) -> dict:
    """Verify MAM search API access with a test query."""
    logger.info("Verifying MAM search API access...")

    # Auth probe only — always English, regardless of user language settings.
    test_payload = json.dumps({
        "tor": {
            "text": "test",
            "srchIn": {"title": "true"},
            "searchType": "active",
            "searchIn": "torrents",
            "main_cat": [EBOOK_CATEGORY],
            "browse_lang": [_ENGLISH_LANG_ID],
            "startNumber": "0",
        },
        "perpage": 5,
    })

    try:
        resp = await asyncio.to_thread(_do_post, MAM_SEARCH_URL, session_id, test_payload, 15)
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


async def validate_connection(session_id: str, skip_ip_update: bool = True) -> dict:
    """Full validation: IP registration + search auth test.

    See register_ip() for why skip_ip_update defaults to True.
    """
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
    lang_ids: Optional[list[int]] = None,
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

    if not lang_ids:
        lang_ids = [_ENGLISH_LANG_ID]

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
            "browse_lang": lang_ids,
            "browseFlagsHideVsShow": "0",
            "startDate": "", "endDate": "", "hash": "",
            "sortType": "default",
            "startNumber": "0",
        },
        "perpage": perpage,
    })

    try:
        resp = await asyncio.to_thread(_do_post, MAM_SEARCH_URL, token, payload)
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
    lang_ids: Optional[list[int]] = None,
) -> list[dict]:
    """
    Evaluate all MAM search results for a book. Returns a list of viable
    matches, each with scoring info. Empty list = no viable matches.

    Each returned match dict:
      torrent_id, mam_title, formats, format_str, match_pct,
      author_matched, search_link, raw
    """
    if not lang_ids:
        lang_ids = [_ENGLISH_LANG_ID]
    allowed_lang_set = set(lang_ids)

    matches = []
    for item in data:
        mam_title = item.get("title", "") or item.get("name", "") or ""
        torrent_id = item.get("id", "")

        # Belt-and-suspenders language filter. browse_lang in the request body
        # already restricts to the user's selected languages, but if MAM ever
        # returns a row in a different language (e.g. cataloging glitches) we
        # don't want it slipping through. We check the numeric `language`
        # field first because it's the same vocabulary as browse_lang; falls
        # back to the 3-letter `lang_code` only if the numeric field is missing.
        result_lang = item.get("language")
        if isinstance(result_lang, int):
            if result_lang not in allowed_lang_set:
                logger.debug(f"  Eval: SKIP '{mam_title[:50]}' — language={result_lang} not in {sorted(allowed_lang_set)}")
                continue
        else:
            # No numeric language — fall back to 3-letter code (rare).
            lang_code = str(item.get("lang_code") or "").strip().lower()
            if lang_code and lang_code not in ("eng", "en", "english"):
                # We only know how to fall-back-match English. Anything else
                # gets a free pass since we can't safely correlate.
                logger.debug(f"  Eval: SKIP '{mam_title[:50]}' — non-English lang_code={lang_code} (no numeric language field)")
                continue

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

        # MAM marks torrents the user has already snatched via "my_snatched"
        # (truthy when present). Capture so we can show a badge in the UI.
        my_snatched = bool(item.get("my_snatched"))

        matches.append({
            "torrent_id": str(torrent_id),
            "mam_title": mam_title,
            "formats": formats,
            "format_str": ",".join(formats) if formats else filetypes_raw.strip(),
            "match_pct": pct,
            "author_matched": author_ok,
            "seeders": int(item.get("seeders", 0) or 0),
            "my_snatched": my_snatched,
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
    lang_ids: Optional[list[int]] = None,
) -> dict:
    """
    Five-pass search cascade for a single book, with format preference scoring.

    Returns dict with:
      status, mam_url, mam_torrent_id, mam_title, mam_formats, mam_has_multiple,
      match_pct, best_format, passes_tried, search_link, error
    """
    if format_priority is None:
        format_priority = DEFAULT_FORMAT_PRIORITY
    if not lang_ids:
        lang_ids = [_ENGLISH_LANG_ID]

    # Default result — search link as fallback URL
    fallback_search_link = build_search_link(authors, title)
    result = {
        "status": STATUS_NOT_FOUND,
        "mam_url": fallback_search_link,
        "mam_torrent_id": None,
        "mam_title": None,
        "mam_formats": None,
        "mam_has_multiple": False,
        "mam_my_snatched": False,
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
        # H10: log total_found vs returned so we can spot truncated result sets
        total_found = resp.get("found") or resp.get("total_found") or resp.get("total")
        if total_found is not None and isinstance(total_found, (int, str)):
            try:
                tf = int(total_found)
                if tf > len(data):
                    logger.debug(
                        f"  Pass {pass_num}: MAM returned {len(data)} of {tf} total — "
                        f"results may be truncated by perpage limit"
                    )
            except (ValueError, TypeError):
                pass
        matches = _evaluate_results(data, title, search_title, authors, format_priority, lang_ids)

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
            "my_snatched": best.get("my_snatched", False),
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
            result["mam_my_snatched"] = best.get("my_snatched", False)
            result["match_pct"] = pct
            result["best_format"] = best.get("best_format", "")
            return True  # stop cascade

        # Otherwise save as best possible so far
        if best_possible is None or pct > best_possible["match_pct"]:
            best_possible = candidate
        return False

    try:
        # --- Pass 1: author + full title ---
        r = await _mam_search(token, authors, title, lang_ids=lang_ids)
        await asyncio.sleep(delay)
        result["passes_tried"].append(1)
        if _try_evaluate(1, r, title):
            return result

        # --- Pass 2: author + core title (volume prefix stripped) ---
        core = _extract_core_title(title)
        if core:
            r = await _mam_search(token, authors, core, lang_ids=lang_ids)
            await asyncio.sleep(delay)
            if _try_evaluate(2, r, core):
                return result

        # --- Pass 3: author + subtitle right (part after colon) ---
        sub_right = _extract_subtitle_part(title)
        if sub_right and sub_right != core:
            r = await _mam_search(token, authors, sub_right, lang_ids=lang_ids)
            await asyncio.sleep(delay)
            if _try_evaluate(3, r, sub_right):
                return result

        # --- Pass 4: author + short title (part before colon) ---
        short = _strip_subtitle(title)
        if short and short != title and short != core:
            r = await _mam_search(token, authors, short, lang_ids=lang_ids)
            await asyncio.sleep(delay)
            if _try_evaluate(4, r, short):
                return result

        # --- Pass 5: title only (no author), loose cleaning ---
        title_only = core or sub_right or short or title
        r = await _mam_search(token, None, title_only, lang_ids=lang_ids)
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
        result["mam_my_snatched"] = best_possible.get("my_snatched", False)
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
    skip_ip_update: bool = True,
    format_priority: list[str] = None,
    on_progress: Optional[Callable[[dict], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    lang_ids: Optional[list[int]] = None,
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
    rows = await db.execute_fetchall(f"""
        SELECT b.id, b.title, a.name as author_name, b.owned, b.is_unreleased
        FROM books b
        JOIN authors a ON b.author_id = a.id
        WHERE {_NEEDS_SCAN_BASIC_ALIASED}
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

        check = await check_book(session_id, book_title, author_name, format_priority, delay, lang_ids=lang_ids)
        stats["scanned"] += 1

        # Write result to DB
        await db.execute("""
            UPDATE books SET mam_url=?, mam_status=?, mam_formats=?,
                   mam_torrent_id=?, mam_has_multiple=?, mam_my_snatched=?
            WHERE id=?
        """, (
            check["mam_url"],
            check["status"],
            check["mam_formats"],
            check["mam_torrent_id"],
            1 if check["mam_has_multiple"] else 0,
            1 if check.get("mam_my_snatched") else 0,
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

    row = await db.execute_fetchall(f"""
        SELECT COUNT(*) FROM books
        WHERE {_NEEDS_SCAN_STRICT_BARE}
    """)
    total = row[0][0] if row else 0

    if total == 0:
        return {"error": "No books need scanning — all books already have MAM data"}

    now = time.time()
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
    skip_ip_update: bool = True,
    delay: float = DEFAULT_DELAY,
    format_priority: list[str] = None,
    lang_ids: Optional[list[int]] = None,
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
    book_rows = await db.execute_fetchall(f"""
        SELECT b.id, b.title, a.name as author_name
        FROM books b
        JOIN authors a ON b.author_id = a.id
        WHERE {_NEEDS_SCAN_STRICT_ALIASED}
        ORDER BY b.owned DESC, b.id ASC
        LIMIT ?
    """, (batch_size,))

    if not book_rows:
        await db.execute(
            "UPDATE mam_scan_log SET status='complete', finished_at=? WHERE id=?",
            (time.time(), scan_id)
        )
        await db.commit()
        logger.info(f"Full MAM scan complete (scan_id={scan_id})")
        return {"status": "scan_complete", "scanned": 0, "remaining": 0, "next_batch_in_seconds": None}

    logger.info(f"Full scan batch: {len(book_rows)} books (scan_id={scan_id})")
    scanned = 0

    for i, row in enumerate(book_rows):
        book_id, book_title, author_name = row

        check = await check_book(session_id, book_title, author_name, format_priority, delay, lang_ids=lang_ids)
        scanned += 1

        await db.execute("""
            UPDATE books SET mam_url=?, mam_status=?, mam_formats=?,
                   mam_torrent_id=?, mam_has_multiple=?, mam_my_snatched=?
            WHERE id=?
        """, (
            check["mam_url"], check["status"], check["mam_formats"],
            check["mam_torrent_id"], 1 if check["mam_has_multiple"] else 0,
            1 if check.get("mam_my_snatched") else 0,
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
    remaining_row = await db.execute_fetchall(f"""
        SELECT COUNT(*) FROM books
        WHERE {_NEEDS_SCAN_STRICT_BARE}
    """)
    remaining = remaining_row[0][0] if remaining_row else 0

    if remaining == 0:
        await db.execute(
            "UPDATE mam_scan_log SET status='complete', finished_at=? WHERE id=?",
            (time.time(), scan_id)
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
        (time.time(), rows[0][0])
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
        f"SELECT COUNT(*) FROM books WHERE {_NEEDS_SCAN_BASIC_BARE}"
    )
    return {
        "upload_candidates": upload_row[0][0] if upload_row else 0,
        "available_to_download": download_row[0][0] if download_row else 0,
        "missing_everywhere": nowhere_row[0][0] if nowhere_row else 0,
        "total_scanned": scanned_row[0][0] if scanned_row else 0,
        "total_unscanned": unscanned_row[0][0] if unscanned_row else 0,
    }
