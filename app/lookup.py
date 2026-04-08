"""
Lookup Engine — Goodreads → Hardcover → FantasticFiction → Kobo
Features: author validation (relaxed matching), language filtering, caching.
"""
import asyncio, time, re, logging, json
from difflib import SequenceMatcher
from app.config import load_settings
from app.database import get_db
from app.sources.hardcover import HardcoverSource
from app.sources.goodreads import GoodreadsSource
from app.sources.fantasticfiction import FantasticFictionSource
from app.sources.kobo import KoboSource
from app.sources.base import AuthorResult

logger = logging.getLogger("athenascout.lookup")


# ─── Pre-compiled regex patterns ─────────────────────────────
# Hoisted to module scope so `_normalize`, `_normalize_light`,
# `_looks_foreign`, and `_is_series_ref_title` don't re-lookup (and in
# the worst case recompile) the same patterns thousands of times per
# scan. Python's built-in regex LRU is only 512 entries and can thrash
# with inline literal patterns in hot loops — explicit compilation is
# faster AND makes the patterns visible at the top of the file.
_RX_LEADING_ARTICLE = re.compile(r'^(the|a|an)\s+')
_RX_PARENS = re.compile(r'\s*\([^)]*\)\s*')
_RX_SUBTITLE = re.compile(r'\s*:.*$')
_RX_NONWORD = re.compile(r'[^\w\s]')
_RX_SPACES = re.compile(r'\s+')
_RX_FOREIGN_ACCENTS = re.compile(
    r'[àáâãäåæçèéêëìíîïðñòóôõöùúûüýþÿąćęłńóśźżšžřůďťňĺľŕäöüß]',
    re.I,
)
_RX_FOREIGN_UNICODE = re.compile(
    r'[\u0400-\u04ff\u3000-\u9fff\u0600-\u06ff\uac00-\ud7af]'
)
_RX_SERIES_REF_TITLE = re.compile(r'^.+\s+#\d+\s*$')


def _merge_source_urls(existing_json: str, source_name: str, new_url: str) -> str:
    """Merge a new source URL into the JSON dict stored in source_url column."""
    if not new_url:
        return existing_json or "{}"
    try:
        urls = json.loads(existing_json) if existing_json else {}
    except (json.JSONDecodeError, TypeError):
        urls = {}
    if not isinstance(urls, dict):
        # Migrate from old plain-string format
        urls = {}
    urls[source_name] = new_url
    return json.dumps(urls)

hardcover = HardcoverSource()
goodreads = GoodreadsSource()
fantasticfiction = FantasticFictionSource()
kobo = KoboSource()


def reload_sources():
    global hardcover, goodreads, fantasticfiction, kobo
    s = load_settings()
    hardcover = HardcoverSource(api_key=s.get("hardcover_api_key", ""))
    goodreads = GoodreadsSource(rate_limit=s.get("rate_goodreads", 2))
    fantasticfiction = FantasticFictionSource(rate_limit=s.get("rate_fantasticfiction", 2))
    kobo = KoboSource(rate_limit=s.get("rate_kobo", 3))


def _normalize(t: str) -> str:
    t = t.lower().strip()
    t = _RX_LEADING_ARTICLE.sub('', t)
    t = _RX_PARENS.sub(' ', t)  # Remove parenthetical
    t = _RX_SUBTITLE.sub('', t)  # Remove subtitle after colon
    t = _RX_NONWORD.sub('', t)
    t = _RX_SPACES.sub(' ', t)
    return t.strip()

def _normalize_light(t: str) -> str:
    """Light normalization — keeps subtitles, just cleans punctuation."""
    t = t.lower().strip()
    t = _RX_PARENS.sub(' ', t)
    t = _RX_NONWORD.sub(' ', t)
    t = _RX_SPACES.sub(' ', t)
    return t.strip()


def _fuzzy_match(a: str, b: str) -> bool:
    """Relaxed title matching using normalization + sequence matching."""
    na, nb = _normalize(a), _normalize(b)
    if na == nb: return True
    if na in nb or nb in na: return True
    # Also check with light normalization (keeps subtitles)
    la, lb = _normalize_light(a), _normalize_light(b)
    if la == lb: return True
    if la in lb or lb in la: return True
    # Fuzzy ratio check for close matches (try both normalizations)
    if len(na) > 3 and len(nb) > 3:
        if SequenceMatcher(None, na, nb).ratio() > 0.75: return True
    if len(la) > 3 and len(lb) > 3:
        if SequenceMatcher(None, la, lb).ratio() > 0.75: return True
    return False


def _lang_ok(book_lang: str, allowed: list[str]) -> bool:
    """Check if a book's language is in the allowed list."""
    if not allowed: return True
    if not book_lang: return True  # Unknown language, assume ok
    bl = book_lang.lower().strip()
    return any(al.lower().strip() in bl or bl in al.lower().strip() for al in allowed)


def _looks_foreign(title: str) -> bool:
    """Detect titles that are likely non-English."""
    if _RX_FOREIGN_ACCENTS.search(title):
        return True
    if _RX_FOREIGN_UNICODE.search(title):
        return True
    # Common foreign words in translated titles
    tl = title.lower()
    foreign_kw = ['hamvai', 'kapuja', 'háborúja', 'bosszúja', 'przebudzenie',
                  'ekspansja', 'lewiatana', 'babilon', 'пробуждение', 'врата']
    if any(fw in tl for fw in foreign_kw):
        return True
    return False


def _is_series_ref_title(title: str) -> bool:
    """Detect titles like 'The Expanse #3' or 'New Novella #2' — series position refs, not real titles."""
    return bool(_RX_SERIES_REF_TITLE.match(title.strip()))


# Patterns that indicate a book set/collection
_SET_PATTERNS = re.compile(
    r'(?i)\b(box\s*set|boxset|books?\s+#?\d+\s*[-–]\s*#?\d+|'
    r'series\s+#?\d+\s*[-–]\s*#?\d+|series\s+\d+\s+books?\b|'
    r'collection\s+#?\d+\s*[-–]\s*#?\d+|collection\s+set|'
    r'\d+\s*books?\s+in\s+\d|complete\s+series|book\s+set|'
    r'series\s+set|hardcover\s+set|paperback\s+set|'
    r'volumes?\s+\d+\s*[-–]\s*\d+|'
    r'\d+\s+books?\s+collection|roleplaying\s+game)\b'
)


def _is_book_set(title: str) -> bool:
    """Check if a title looks like a book set/collection rather than an individual book."""
    return bool(_SET_PATTERNS.search(title))


async def _validate_author(author_name: str, our_titles: list[str], result: AuthorResult) -> bool:
    """Validate found author by checking if ANY of our books fuzzy-match their catalog."""
    if not our_titles: return True
    src_titles = [b.title for b in result.books]
    for sr in result.series:
        src_titles.extend([b.title for b in sr.books])
    if not src_titles: return False
    for ours in our_titles:
        for theirs in src_titles:
            if _fuzzy_match(ours, theirs):
                return True
    logger.info(f"  Validation FAILED for '{author_name}': 0/{len(our_titles)} matched in {len(src_titles)} source books")
    return False


async def _merge_result(author_id: int, result: AuthorResult, source_name: str, languages: list[str], full_scan: bool = False, owned_only: bool = False):
    """Merge an AuthorResult, filtering by language. In full_scan mode, updates metadata on existing books.

    When owned_only=True (the "Library-only source scan" setting), the
    function still UPDATEs existing books with new URLs, series links, and
    (in full_scan mode) refreshed metadata, but it skips the INSERT branches
    entirely. The result: source scans become a metadata-enrichment pass
    over the user's owned library without ever discovering new missing or
    upcoming books. Useful for getting an existing library polished before
    turning the discovery firehose on.
    """
    db = await get_db()
    try:
        new_books = 0; updated_books = 0
        # Update author metadata
        up = []; pr = []
        if result.image_url: up.append("image_url = COALESCE(image_url, ?)"); pr.append(result.image_url)
        if result.bio: up.append("bio = COALESCE(bio, ?)"); pr.append(result.bio)
        if result.external_id: up.append(f"{source_name}_id = ?"); pr.append(result.external_id)
        up.append("last_lookup_at = ?"); pr.append(time.time()); pr.append(author_id)
        if up: await db.execute(f"UPDATE authors SET {', '.join(up)} WHERE id = ?", pr)

        rows = await (await db.execute("SELECT id, title, source_url, series_id, series_index, source FROM books WHERE author_id = ?", (author_id,))).fetchall()
        existing = {_normalize(r["title"]) for r in rows}
        # Build an O(1) prefilter: normalized-title → row. The book-merge
        # loops below used to linearly scan all `rows` for each incoming
        # source book, which was O(n*m) — 200 owned × 200 source = 40k
        # fuzzy-match calls per author. Most matches hit on exact
        # normalized equality (the first check inside _fuzzy_match), so
        # checking the dict first short-circuits the common case. The
        # linear loop stays as the fallback for substring and sequence-
        # matching cases the dict can't catch.
        rows_by_norm = {_normalize(r["title"]): r for r in rows}

        # Source priority: Goodreads can overwrite series from any other source
        SOURCE_PRIORITY = {"goodreads": 1, "hardcover": 2, "kobo": 3, "fantasticfiction": 4, "manual": 5, "import": 5, "calibre": 0}
        
        def _update_existing(matched_row, bk, series_id=None):
            """Build UPDATE for an existing book — URL merge always, series with priority, metadata in full_scan."""
            nonlocal updated_books
            sets = []; vals = []
            if bk.source_url:
                merged = _merge_source_urls(matched_row["source_url"], source_name, bk.source_url)
                sets.append("source_url=?"); vals.append(merged)
            sets.append(f"{source_name}_id=COALESCE({source_name}_id,?)"); vals.append(bk.external_id)
            # Series update: fill if empty, or overwrite if current source has higher priority
            if series_id:
                existing_series = matched_row["series_id"]
                try: existing_source = matched_row["source"]
                except (IndexError, KeyError): existing_source = ""
                cur_priority = SOURCE_PRIORITY.get(source_name, 5)
                existing_priority = SOURCE_PRIORITY.get(existing_source or "", 5)
                if not existing_series or (cur_priority < existing_priority and existing_series != series_id):
                    sets.append("series_id=?"); vals.append(series_id)
                    if bk.series_index: sets.append("series_index=?"); vals.append(bk.series_index)
                    logger.debug(f"    MERGE SERIES: '{bk.title}' (id={matched_row['id']}) → series_id={series_id} #{bk.series_index} (source={source_name}, was={existing_source})")
            if full_scan:
                fields_updated = []
                if bk.description: sets.append("description=?"); vals.append(bk.description); fields_updated.append("description")
                if bk.pub_date: sets.append("pub_date=?"); vals.append(bk.pub_date); fields_updated.append("pub_date")
                if bk.expected_date: sets.append("expected_date=?"); vals.append(bk.expected_date); fields_updated.append("expected_date")
                if bk.cover_url: sets.append("cover_url=COALESCE(cover_url,?)"); vals.append(bk.cover_url); fields_updated.append("cover_url")
                if bk.page_count: sets.append("page_count=COALESCE(page_count,?)"); vals.append(bk.page_count); fields_updated.append("page_count")
                if bk.isbn: sets.append("isbn=COALESCE(isbn,?)"); vals.append(bk.isbn); fields_updated.append("isbn")
                if bk.is_unreleased is not None: sets.append("is_unreleased=?"); vals.append(1 if bk.is_unreleased else 0)
                updated_books += 1
                logger.debug(f"    MERGE UPDATE: '{bk.title}' (id={matched_row['id']}) fields=[{','.join(fields_updated)}]")
            else:
                logger.debug(f"    MERGE URL: '{bk.title}' (id={matched_row['id']}) ← {source_name}")
            vals.append(matched_row["id"])
            return f"UPDATE books SET {', '.join(sets)} WHERE id=?", vals

        for sr in result.series:
            # Look for series globally first (supports multi-author series)
            row = await (await db.execute("SELECT id FROM series WHERE LOWER(name) = LOWER(?)", (sr.name,))).fetchone()
            if row:
                sid = row["id"]
                await db.execute("UPDATE series SET last_lookup_at = ? WHERE id = ?", (time.time(), sid))
            else:
                cur = await db.execute("INSERT INTO series (name, author_id, total_books, last_lookup_at) VALUES (?,?,?,?)", (sr.name, author_id, sr.total_books, time.time()))
                sid = cur.lastrowid
            for bk in sr.books:
                if not _lang_ok(bk.language, languages): continue
                if _is_book_set(bk.title): continue
                if _is_series_ref_title(bk.title): continue
                if "English" in languages and _looks_foreign(bk.title): continue
                norm = _normalize(bk.title)
                matched_row = rows_by_norm.get(norm)
                if matched_row is None:
                    for r in rows:
                        if _fuzzy_match(bk.title, r["title"]):
                            matched_row = r
                            break
                if matched_row:
                    sql, vals = _update_existing(matched_row, bk, series_id=sid)
                    await db.execute(sql, vals)
                    continue
                if owned_only:
                    # Library-only scan: don't add discovered series books that
                    # we don't already own. The series row itself was upserted
                    # above so existing owned books in this series still get
                    # linked correctly via _update_existing.
                    continue
                if norm in existing:
                    logger.debug(f"    SKIP (norm dup): '{bk.title}'")
                    continue
                initial_urls = json.dumps({source_name: bk.source_url}) if bk.source_url else "{}"
                await db.execute(f"INSERT OR IGNORE INTO books (title,author_id,series_id,series_index,isbn,cover_url,pub_date,expected_date,is_unreleased,description,page_count,source,source_url,owned,is_new,{source_name}_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,1,?)",
                    (bk.title, author_id, sid, bk.series_index, bk.isbn, bk.cover_url, bk.pub_date, bk.expected_date, 1 if bk.is_unreleased else 0, bk.description, bk.page_count, source_name, initial_urls, bk.external_id))
                existing.add(norm); new_books += 1
                logger.debug(f"    NEW: '{bk.title}' → series '{sr.name}' from {source_name}")

        for bk in result.books:
            if not _lang_ok(bk.language, languages): continue
            if _is_book_set(bk.title): continue
            if _is_series_ref_title(bk.title): continue
            if "English" in languages and _looks_foreign(bk.title): continue
            norm = _normalize(bk.title)
            matched_row = rows_by_norm.get(norm)
            if matched_row is None:
                for r in rows:
                    if _fuzzy_match(bk.title, r["title"]):
                        matched_row = r
                        break
            if matched_row:
                sql, vals = _update_existing(matched_row, bk)
                await db.execute(sql, vals)
                continue
            if owned_only:
                # Library-only scan: skip discovered standalone books we don't own.
                continue
            if norm in existing:
                logger.debug(f"    SKIP (norm dup): '{bk.title}'")
                continue
            initial_urls = json.dumps({source_name: bk.source_url}) if bk.source_url else "{}"
            await db.execute(f"INSERT OR IGNORE INTO books (title,author_id,isbn,cover_url,pub_date,expected_date,is_unreleased,description,page_count,source,source_url,owned,is_new,{source_name}_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,0,1,?)",
                (bk.title, author_id, bk.isbn, bk.cover_url, bk.pub_date, bk.expected_date, 1 if bk.is_unreleased else 0, bk.description, bk.page_count, source_name, initial_urls, bk.external_id))
            existing.add(norm); new_books += 1
            logger.debug(f"    NEW: '{bk.title}' → standalone from {source_name}")

        await db.commit()
        return new_books, updated_books
    finally:
        await db.close()


async def _try_source(source, author_name, author_id, our_titles, languages, source_name, existing_titles=None, full_scan=False, owned_only=False):
    """Try a single source with validation and detailed logging."""
    try:
        logger.info(f"  [{source_name}] {'Full scan' if full_scan else 'Searching'} for '{author_name}'...")
        # Hardcover needs owned_titles to search by book title
        if hasattr(source, '_owned_titles'):
            found = await source.search_author(author_name, owned_titles=source._owned_titles)
        else:
            found = await source.search_author(author_name)
        if not found:
            logger.info(f"  [{source_name}] No author match found")
            return 0
        if not found.external_id:
            logger.info(f"  [{source_name}] Found author but no external ID")
            return 0
        logger.info(f"  [{source_name}] Found: '{found.name}' (id={found.external_id})")

        # Some sources (like Hardcover) return full results from search_author
        has_data = len(found.books) > 0 or len(found.series) > 0
        if has_data:
            full = found
        else:
            # In full_scan mode, pass empty existing_titles to force page visits
            scan_existing = set() if full_scan else (existing_titles or set())
            try:
                full = await source.get_author_books(
                    found.external_id, 
                    existing_titles=scan_existing,
                    owned_titles=our_titles or [],
                )
            except TypeError:
                full = await source.get_author_books(found.external_id)
        
        if not full:
            logger.info(f"  [{source_name}] No books returned")
            return 0

        total_src = len(full.books) + sum(len(s.books) for s in full.series)
        if total_src == 0:
            logger.info(f"  [{source_name}] No books found in catalog")
            return 0
        logger.info(f"  [{source_name}] Retrieved {total_src} books ({len(full.series)} series, {len(full.books)} standalone)")

        # Validate: skip if author already confirmed from previous scans
        if existing_titles and len(existing_titles) > 0:
            logger.debug(f"  [{source_name}] Author already confirmed ({len(existing_titles)} known books)")
        elif not await _validate_author(author_name, our_titles, full):
            logger.info(f"  [{source_name}] Author validation failed — skipping (likely wrong author)")
            return 0

        n, u = await _merge_result(author_id, full, source_name, languages, full_scan=full_scan, owned_only=owned_only)
        parts = []
        if n > 0: parts.append(f"{n} new")
        if u > 0: parts.append(f"{u} updated")
        if parts:
            logger.info(f"  [{source_name}] ✓ Merged {', '.join(parts)} books for {author_name}")
        else:
            logger.info(f"  [{source_name}] ✓ No changes (all {total_src} already known)")
        return n
    except Exception as e:
        logger.error(f"  [{source_name}] Error for {author_name}: {e}")
        return 0


async def lookup_author(author_id: int, author_name: str, full_scan: bool = False):
    logger.info(f"{'Full re-scan' if full_scan else 'Looking up'} author: {author_name}")
    total = 0
    settings = load_settings()
    languages = settings.get("languages", ["English"])
    owned_only = bool(settings.get("author_scan_owned_only", False))
    if owned_only:
        logger.info(f"  Library-only mode: only enriching owned books for '{author_name}', no new discoveries")

    db = await get_db()
    try:
        rows = await (await db.execute("SELECT title FROM books WHERE author_id = ? AND owned = 1", (author_id,))).fetchall()
        our_titles = [r["title"] for r in rows]
        all_rows = await (await db.execute("SELECT title FROM books WHERE author_id = ?", (author_id,))).fetchall()
        existing_titles = set()
        for r in all_rows:
            t = re.sub(r'[^\w\s]', '', r["title"].lower()).strip()
            t = re.sub(r'\s+', ' ', t)
            existing_titles.add(t)
    finally:
        await db.close()

    # 1. Goodreads (PRIMARY)
    total += await _try_source(goodreads, author_name, author_id, our_titles, languages, "goodreads", existing_titles=existing_titles, full_scan=full_scan, owned_only=owned_only)

    # 2. Hardcover
    if settings.get("hardcover_api_key"):
        hardcover.update_api_key(settings["hardcover_api_key"])
        hardcover._owned_titles = our_titles
        total += await _try_source(hardcover, author_name, author_id, our_titles, languages, "hardcover", existing_titles=existing_titles, full_scan=full_scan, owned_only=owned_only)

    # 3. FantasticFiction
    if settings.get("fantasticfiction_enabled", False):
        total += await _try_source(fantasticfiction, author_name, author_id, our_titles, languages, "fantasticfiction", existing_titles=existing_titles, full_scan=full_scan, owned_only=owned_only)

    # 4. Kobo
    if settings.get("kobo_enabled", True):
        total += await _try_source(kobo, author_name, author_id, our_titles, languages, "kobo", existing_titles=existing_titles, full_scan=full_scan, owned_only=owned_only)

    db2 = await get_db()
    try:
        await db2.execute("UPDATE authors SET verified=1, last_lookup_at=? WHERE id=?", (time.time(), author_id))
        await db2.commit()
    finally:
        await db2.close()

    logger.info(f"{'Full re-scan' if full_scan else 'Lookup'} complete for '{author_name}': {total} new books found across all sources")
    return total


async def run_full_lookup(on_progress=None):
    logger.info("Starting scheduled lookup...")
    reload_sources()
    start = time.time()
    settings = load_settings()
    cache_sec = settings.get("lookup_interval_days", 3) * 86400
    sid = None
    db = await get_db()
    try:
        cur = await db.execute("INSERT INTO sync_log (sync_type, started_at) VALUES (?, ?)", ("lookup", start))
        sid = cur.lastrowid; await db.commit()
        rows = await (await db.execute("SELECT id, name FROM authors WHERE COALESCE(last_lookup_at,0) < ? ORDER BY COALESCE(last_lookup_at,0) ASC", (time.time() - cache_sec,))).fetchall()
        authors = list(rows)
        total = 0; checked = 0
        for a in authors:
            if on_progress:
                on_progress({"checked": checked, "total": len(authors), "current_author": a["name"], "new_books": total})
            try: total += await lookup_author(a["id"], a["name"]); checked += 1
            except Exception as e: logger.error(f"Error for {a['name']}: {e}")
        if on_progress:
            on_progress({"checked": checked, "total": len(authors), "current_author": "", "new_books": total})
        await db.execute("UPDATE sync_log SET finished_at=?,status='complete',books_found=?,books_new=? WHERE id=?", (time.time(), checked, total, sid))
        await db.commit()
        logger.info(f"Lookup done: {checked} authors, {total} new books")
        return {"authors_checked": checked, "new_books": total}
    except Exception as e:
        if sid:
            try:
                await db.execute("UPDATE sync_log SET finished_at=?,status='error',error=? WHERE id=?", (time.time(), str(e), sid))
                await db.commit()
            except Exception as cleanup_err:
                # Don't mask the original error, but don't lose the cleanup
                # failure either — log it so debugging is possible.
                logger.warning(f"Failed to mark sync_log {sid} as errored: {cleanup_err}")
        raise
    finally:
        await db.close()


async def run_full_rescan(on_progress=None):
    """Full re-scan: visits every book page to refresh metadata, ignoring skip optimizations."""
    logger.info("Starting FULL RE-SCAN of all authors...")
    reload_sources()
    start = time.time()
    sid = None
    db = await get_db()
    try:
        cur = await db.execute("INSERT INTO sync_log (sync_type, started_at) VALUES (?, ?)", ("full_rescan", start))
        sid = cur.lastrowid; await db.commit()
        rows = await (await db.execute("SELECT id, name FROM authors ORDER BY sort_name ASC")).fetchall()
        authors = list(rows)
        total = 0; checked = 0
        for a in authors:
            if on_progress:
                on_progress({"checked": checked, "total": len(authors), "current_author": a["name"], "new_books": total})
            try: total += await lookup_author(a["id"], a["name"], full_scan=True); checked += 1
            except Exception as e: logger.error(f"Full re-scan error for {a['name']}: {e}")
        if on_progress:
            on_progress({"checked": checked, "total": len(authors), "current_author": "", "new_books": total})
        await db.execute("UPDATE sync_log SET finished_at=?,status='complete',books_found=?,books_new=? WHERE id=?", (time.time(), checked, total, sid))
        await db.commit()
        logger.info(f"Full re-scan done: {checked} authors, {total} new books")
        return {"authors_checked": checked, "new_books": total}
    except Exception as e:
        if sid:
            try:
                await db.execute("UPDATE sync_log SET finished_at=?,status='error',error=? WHERE id=?", (time.time(), str(e), sid))
                await db.commit()
            except Exception as cleanup_err:
                # Don't mask the original error, but don't lose the cleanup
                # failure either — log it so debugging is possible.
                logger.warning(f"Failed to mark sync_log {sid} as errored: {cleanup_err}")
        raise
    finally:
        await db.close()
