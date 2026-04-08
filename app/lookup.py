"""
Lookup Engine — Goodreads → Hardcover → Kobo
Features: author validation (relaxed matching), language filtering, caching.
"""
import asyncio, time, re, logging, json
from difflib import SequenceMatcher
from app.config import load_settings
from app.database import get_db
from app.sources.hardcover import HardcoverSource
from app.sources.goodreads import GoodreadsSource
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
# Inverse of _RX_SUBTITLE: strips up to and including the first colon.
# Used for the "SeriesPrefix: BookTitle" case that Hardcover often uses
# (e.g., "Mistborn: The Final Empire" while Calibre has just "The Final
# Empire"). _normalize strips the suffix after `:` which is the wrong
# half for this layout — _normalize_strip_prefix handles the other.
_RX_SERIES_PREFIX = re.compile(r'^[^:]+:\s*')

# "Generic-subtitle" matcher: detects suffixes that are obviously
# marketing/edition taglines rather than real book titles. Used by
# _normalize to decide whether stripping the suffix-after-colon is safe.
# Without this, "Mistborn: The Final Empire" reduced to "mistborn" and
# any two books in the same series collapsed to identical normalized
# forms — fine when only Hardcover used "Series:" prefix format because
# Calibre never did, but fragile if a future scan ever produced two
# such titles. Patterns covered:
#   - "A Novel" / "A Memoir" / "A Tale" / etc.
#   - "The Definitive/Complete/Illustrated/… Edition"
#   - "Book/Volume/Vol/Part/Chapter/Tome <number>" or "<word number>"
#   - "<n>th Anniversary Edition"
_RX_GENERIC_SUBTITLE = re.compile(
    r'\s*:\s*'
    r'(?:'
    r'an?\s+(?:novel|novella|memoir|story|tale|history|biography|autobiography'
    r'|guide|companion|handbook|introduction|adventure|fable|romance|mystery'
    r'|thriller|epic|chronicle|trilogy)s?'
    r'|the\s+(?:definitive|complete|illustrated|annotated|original|expanded'
    r'|revised|special|limited|deluxe|collector\'?s|anniversary)\s+'
    r'(?:edition|version|collection)'
    r'|\d+(?:st|nd|rd|th)\s+anniversary\s+edition'
    r'|(?:book|volume|vol\.?|part|chapter|tome)\s+\d+'
    r'|(?:book|volume|vol\.?|part|chapter|tome)\s+'
    r'(?:one|two|three|four|five|six|seven|eight|nine|ten)'
    r')\s*$',
    re.IGNORECASE,
)
_RX_NONWORD = re.compile(r'[^\w\s]')
_RX_SPACES = re.compile(r'\s+')
# Word-joining punctuation that needs to become a SPACE before _RX_NONWORD
# strips it. Without this, "The Dragon's Path/Leviathan Wakes" normalized
# to "dragons pathleviathan wakes" (no space between "path" and "leviathan"
# because the slash got eaten as a non-word char), which then matched
# "leviathan wakes" via substring containment — a false positive that
# linked "Leviathan Wakes" to a Daniel Abraham anthology containing an
# ARC of it. Slashes, ampersands, and explicit ' and ' are the typical
# joiners between bound-edition titles.
_RX_TITLE_JOINERS = re.compile(r'\s*[/&+]\s*')
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
kobo = KoboSource()


def reload_sources():
    global hardcover, goodreads, kobo
    s = load_settings()
    hardcover = HardcoverSource(api_key=s.get("hardcover_api_key", ""))
    goodreads = GoodreadsSource(rate_limit=s.get("rate_goodreads", 2))
    kobo = KoboSource(rate_limit=s.get("rate_kobo", 3))


def _smart_strip_subtitle(t: str) -> str:
    """Strip the part after `:` only when it looks like a real subtitle.

    Two acceptance rules — strip if EITHER fires:
      1. The PREFIX (before colon) has 3+ words. Real titles tend to be
         multi-word ("Project Hail Mary", "The Catcher in the Rye");
         series names tend to be 1-2 words ("Mistborn", "Star Wars",
         "Doctor Who"). 3-word prefixes are unambiguous.
      2. The SUFFIX (after colon) matches a generic-subtitle pattern
         like "A Novel", "Part 1", "The Definitive Edition" — those
         are never real book titles, so stripping is always safe even
         when the prefix is just one word ("Dune: A Novel").

    Otherwise, leave the colon and everything after it intact. The
    `_normalize_strip_prefix` path in _fuzzy_match handles the inverse
    case ("Series: BookTitle" vs bare "BookTitle"), so dropping the
    blanket strip doesn't lose match coverage — it just stops the
    spurious cross-book collision where "Mistborn: The Final Empire"
    and "Mistborn: The Hero of Ages" both reduced to just "mistborn".
    """
    if ':' not in t:
        return t
    prefix = t.split(':', 1)[0]
    if len(prefix.split()) >= 3:
        return _RX_SUBTITLE.sub('', t)
    if _RX_GENERIC_SUBTITLE.search(t):
        return _RX_SUBTITLE.sub('', t)
    return t


def _normalize(t: str) -> str:
    t = t.lower().strip()
    t = _RX_LEADING_ARTICLE.sub('', t)
    t = _RX_PARENS.sub(' ', t)  # Remove parenthetical
    t = _smart_strip_subtitle(t)  # Strip "X: Subtitle" only when safe
    t = _RX_TITLE_JOINERS.sub(' ', t)  # "/" "&" "+" between titles → space
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


def _normalize_strip_prefix(t: str) -> str:
    """Same as _normalize but strips the part BEFORE the first colon
    instead of after. Lets `_fuzzy_match` link Hardcover-style
    "Mistborn: The Final Empire" against Calibre's "The Final Empire"
    by reducing the former to "final empire" instead of "mistborn".
    Returns "" for inputs with no colon (caller can skip).
    """
    if ':' not in t:
        return ''
    t = t.lower().strip()
    t = _RX_PARENS.sub(' ', t)
    t = _RX_SERIES_PREFIX.sub('', t, count=1)
    t = _RX_LEADING_ARTICLE.sub('', t)  # post-strip "the" survivor
    t = _RX_TITLE_JOINERS.sub(' ', t)
    t = _RX_NONWORD.sub('', t)
    t = _RX_SPACES.sub(' ', t)
    return t.strip()


def _fuzzy_match(a: str, b: str) -> bool:
    """Relaxed title matching using normalization + sequence matching.

    Substring-containment branches require a length ratio of at least
    0.75 — the shorter title must be ≥75% of the longer. Without this
    guard, three documented false positives slipped through:

      - "Leviathan Wakes" (15) vs "Dragons Path Leviathan Wakes" (28),
        ratio 0.54 — a Daniel Abraham anthology containing an ARC of
        the Corey book. Wrongly linked the user's Corey book to the
        Abraham anthology page on Goodreads.
      - "Pride and Prejudice" (19) vs "Pride and Prejudice and Zombies"
        (31), ratio 0.61 — a parody is a different book.
      - "Foundation" (10) vs "Foundation Trilogy" (18), ratio 0.55 —
        an omnibus is a different cataloged work.

    Legitimate matches almost always go through the exact-normalized
    path (the prefilter dict in _merge_result), so the substring branch
    is a tiebreaker for cases like "Title: Subtitle" where the user's
    Calibre has the bare title. 0.75 is the same threshold the
    SequenceMatcher branch already uses, which keeps the two paths in
    sync. The trade-off: very-short-title vs very-long-extended-subtitle
    cases ("Foo" vs "Foo, A Tale of Bar and Baz") won't auto-link via
    substring; they'd need either exact normalization (which usually
    works thanks to subtitle stripping) or manual linking.
    """
    def _len_ratio_ok(short_len: int, long_len: int) -> bool:
        if long_len == 0:
            return False
        return (short_len / long_len) >= 0.75

    na, nb = _normalize(a), _normalize(b)
    if na == nb: return True
    if na in nb and _len_ratio_ok(len(na), len(nb)): return True
    if nb in na and _len_ratio_ok(len(nb), len(na)): return True
    # Also check with light normalization (keeps subtitles)
    la, lb = _normalize_light(a), _normalize_light(b)
    if la == lb: return True
    if la in lb and _len_ratio_ok(len(la), len(lb)): return True
    if lb in la and _len_ratio_ok(len(lb), len(la)): return True
    # Fuzzy ratio check for close matches (try both normalizations).
    # Threshold bumped from 0.75 → 0.85 in Phase 3a follow-up because
    # 0.75 was admitting "Pride and Prejudice" matching "Pride and
    # Prejudice and Zombies" (SequenceMatcher ratio 0.76 — a parody is
    # not the same book). 0.85 still catches genuine close-matches like
    # spelling variants ("Colour" vs "Color", ratio 0.91), light typos,
    # and minor punctuation differences. Anything below 0.85 should
    # either go through the exact-normalized path or be treated as a
    # different book.
    if len(na) > 3 and len(nb) > 3:
        if SequenceMatcher(None, na, nb).ratio() > 0.85: return True
    if len(la) > 3 and len(lb) > 3:
        if SequenceMatcher(None, la, lb).ratio() > 0.85: return True

    # Phase 3b-H2 followup: handle "SeriesPrefix: BookTitle" against
    # bare-title Calibre rows. _normalize() strips the suffix after `:`
    # which is the wrong half for this layout — Hardcover returns
    # "Mistborn: The Final Empire" while Calibre has "The Final Empire",
    # and the strip-suffix path reduces both to "mistborn" vs "final
    # empire" (no match). _normalize_strip_prefix() does the inverse.
    #
    # Two-word minimum: a strip-prefix result of 1 word is too generic
    # to safely auto-link. "Stephen King: A Biography" → "biography"
    # would otherwise match any Calibre row called "Biography", and
    # "Star Wars: Aftermath" → "aftermath" could collide with Stephen
    # King's "Aftermath", Linda Hogan's "Aftermath", etc. The 2-word
    # floor still catches the common cases ("final empire", "new hope",
    # "way of kings") which are the ones actually showing up in scans.
    def _strip_prefix_ok(p: str) -> bool:
        return bool(p) and len(p.split()) >= 2

    pa = _normalize_strip_prefix(a)
    pb = _normalize_strip_prefix(b)
    if _strip_prefix_ok(pa):
        if pa == nb: return True
        if pa in nb and _len_ratio_ok(len(pa), len(nb)): return True
        if nb in pa and _len_ratio_ok(len(nb), len(pa)): return True
    if _strip_prefix_ok(pb):
        if pb == na: return True
        if pb in na and _len_ratio_ok(len(pb), len(na)): return True
        if na in pb and _len_ratio_ok(len(na), len(pb)): return True
    if _strip_prefix_ok(pa) and _strip_prefix_ok(pb) and pa == pb: return True

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
# Bound-edition / anthology detector. A title like "The Dragon's Path /
# Leviathan Wakes" is two distinct books pressed into one publishing
# event (often an Advance Reading Copy giveaway or a publisher promo
# pack). Source scanners return these as single books, and the fuzzy
# matcher used to false-positive them onto the user's owned book of
# the same name.
#
# We ONLY match `/` as the joiner — not `&`, `+`, or ` and `. Those
# all appear in legitimate single-book titles ("Pride and Prejudice",
# "War and Peace", "Beauty & the Beast", "Foundation and Empire"),
# and rejecting them would skip thousands of real books. Forward-slash
# is the only marker that is essentially never used in a real title.
# It must have non-space chars on BOTH sides so we don't trip on
# stray punctuation, and we tolerate optional whitespace around it.
_RX_ANTHOLOGY = re.compile(r'\S\s*/\s*\S')


def _is_book_set(title: str) -> bool:
    """Check if a title looks like a book set/collection rather than an individual book."""
    if _SET_PATTERNS.search(title):
        return True
    if _RX_ANTHOLOGY.search(title):
        return True
    return False


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


async def _merge_result(author_id: int, result: AuthorResult, source_name: str, languages: list[str], full_scan: bool = False, owned_only: bool = False, series_collector: dict | None = None):
    """Merge an AuthorResult, filtering by language. In full_scan mode, updates metadata on existing books.

    When owned_only=True (the "Library-only source scan" setting), the
    function still UPDATEs existing books with new URLs, series links, and
    (in full_scan mode) refreshed metadata, but it skips the INSERT branches
    entirely. The result: source scans become a metadata-enrichment pass
    over the user's owned library without ever discovering new missing or
    upcoming books. Useful for getting an existing library polished before
    turning the discovery firehose on.

    Phase 3c: when `series_collector` is a dict, this function records each
    matched (book_id → source_name → (series_name, series_index)) tuple
    into it. The collector is owned by lookup_author() and threaded through
    every source's _merge_result call so that after all sources have run,
    _compute_series_suggestions() can tally per-source agreement and write
    suggestion rows where 2+ sources agree on a series different from the
    book's current value. Recording is non-destructive — the merge layer's
    actual write behavior (priority-gated for series, Calibre-locked for
    owned books) is unchanged.
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

        # SELECT includes pub_date, description, expected_date so the
        # owned-book metadata logic in _update_existing can compare against
        # what's currently stored without a second round-trip per book.
        # The smart-description and oldest-pub_date rules need to read the
        # current value to decide whether to overwrite.
        rows = await (await db.execute(
            "SELECT id, title, source_url, series_id, series_index, source, "
            "pub_date, expected_date, description "
            "FROM books WHERE author_id = ?",
            (author_id,)
        )).fetchall()
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
        SOURCE_PRIORITY = {"goodreads": 1, "hardcover": 2, "kobo": 3, "manual": 5, "import": 5, "calibre": 0}
        
        def _update_existing(matched_row, bk, series_id=None):
            """Build UPDATE for an existing book — URL merge always, series with priority, metadata in full_scan.

            Calibre source-of-truth protection (Phase 3a follow-up):
            owned-Calibre books treat each metadata field with a tailored
            rule rather than a blanket lock. The current rules:

              cover_url        : LOCKED (Calibre cover_path is authoritative)
              title            : LOCKED (structural — never updated by sources)
              author_id        : LOCKED (structural — never updated by sources)
              description      : SMART (see below)
              pub_date         : OLDEST WINS (see below)
              expected_date    : COALESCE-fill (only if Calibre left it null)
              page_count       : COALESCE-fill (only if Calibre left it null)
              isbn             : COALESCE-fill (only if Calibre left it null)
              is_unreleased    : LOCKED (almost always False for owned books)
              series_id/index  : priority-gated (calibre=0 always wins)
              source_url       : merged into JSON dict (additive, never destructive)
              {source}_id      : COALESCE-fill

            DESCRIPTION (smart stub-detection):
              Calibre imports of older books often have "stub" descriptions —
              one-sentence blurbs from a metadata source that didn't have a
              good summary at the time. The rule is:
                - If existing is null/empty → fill from source.
                - If existing is < 10 words AND new is at least 3x longer
                  in word count → overwrite (the source has a real summary).
                - Otherwise → leave existing alone.
              Threshold is conservative on purpose: a 9-word Calibre stub
              upgrading to a 27-word source description is a clear win;
              we don't want to thrash a 9-word user-curated description
              into a 12-word source description because that's not a real
              improvement.

            PUB_DATE (oldest wins):
              Calibre often has edition-specific dates (a 2015 paperback
              reprint of a 1965 novel). Source scans should be allowed to
              correct that DOWNWARD to the original publication date, but
              never UPWARD (a more-recent edition shouldn't displace the
              original). Rule: if the source's pub_date is strictly older
              (lexicographic compare on ISO YYYY-MM-DD strings, which works
              correctly for dates) than the existing pub_date, overwrite.
              Iterates correctly across multiple sources because each one
              compares against the current value.

            For unowned/missing/discovered books (`source != 'calibre'`),
            none of this applies — the source IS the authority for those
            rows, so the existing full-overwrite behavior continues.
            """
            nonlocal updated_books
            sets = []; vals = []

            try: existing_source = matched_row["source"]
            except (IndexError, KeyError): existing_source = ""
            is_owned_calibre = (existing_source == "calibre")

            if bk.source_url:
                merged = _merge_source_urls(matched_row["source_url"], source_name, bk.source_url)
                sets.append("source_url=?"); vals.append(merged)
            sets.append(f"{source_name}_id=COALESCE({source_name}_id,?)"); vals.append(bk.external_id)
            # Series update: fill if empty, or overwrite if current source has higher priority
            if series_id:
                existing_series = matched_row["series_id"]
                cur_priority = SOURCE_PRIORITY.get(source_name, 5)
                existing_priority = SOURCE_PRIORITY.get(existing_source or "", 5)
                if not existing_series or (cur_priority < existing_priority and existing_series != series_id):
                    sets.append("series_id=?"); vals.append(series_id)
                    if bk.series_index: sets.append("series_index=?"); vals.append(bk.series_index)
                    logger.debug(f"    MERGE SERIES: '{bk.title}' (id={matched_row['id']}) → series_id={series_id} #{bk.series_index} (source={source_name}, was={existing_source})")
            if full_scan:
                fields_updated = []
                if is_owned_calibre:
                    # ── Owned-Calibre book: per-field rules ──

                    # Description: smart stub-detection
                    if bk.description:
                        existing_desc = (matched_row["description"] or "").strip() if "description" in matched_row.keys() else ""
                        existing_words = len(existing_desc.split()) if existing_desc else 0
                        new_words = len(bk.description.split())
                        # Fill if Calibre is empty, OR if Calibre is a stub
                        # (<10 words) AND new is at least 3x longer.
                        if existing_words == 0:
                            sets.append("description=?"); vals.append(bk.description); fields_updated.append("description(filled)")
                        elif existing_words < 10 and new_words >= existing_words * 3:
                            sets.append("description=?"); vals.append(bk.description); fields_updated.append(f"description(stub→{new_words}w)")

                    # pub_date: oldest wins (lexicographic compare on ISO dates)
                    if bk.pub_date:
                        existing_pub = matched_row["pub_date"] if "pub_date" in matched_row.keys() else None
                        if not existing_pub:
                            sets.append("pub_date=?"); vals.append(bk.pub_date); fields_updated.append("pub_date(filled)")
                        elif bk.pub_date < existing_pub:
                            sets.append("pub_date=?"); vals.append(bk.pub_date); fields_updated.append(f"pub_date({existing_pub}→{bk.pub_date})")

                    # expected_date: COALESCE-fill only
                    if bk.expected_date:
                        existing_exp = matched_row["expected_date"] if "expected_date" in matched_row.keys() else None
                        if not existing_exp:
                            sets.append("expected_date=?"); vals.append(bk.expected_date); fields_updated.append("expected_date(filled)")

                    # page_count + isbn: COALESCE-fill (unchanged from Issue 2a v1)
                    if bk.page_count: sets.append("page_count=COALESCE(page_count,?)"); vals.append(bk.page_count); fields_updated.append("page_count")
                    if bk.isbn: sets.append("isbn=COALESCE(isbn,?)"); vals.append(bk.isbn); fields_updated.append("isbn")

                    if fields_updated:
                        updated_books += 1
                        logger.debug(f"    MERGE UPDATE (owned): '{bk.title}' (id={matched_row['id']}) fields=[{','.join(fields_updated)}]")
                    else:
                        logger.debug(f"    MERGE NOOP (owned, all rules satisfied): '{bk.title}' (id={matched_row['id']})")
                else:
                    # Unowned / missing / discovered book: full overwrite
                    # behavior. No user data to protect — the source IS the
                    # authority for these rows.
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
            # ── Lazy series upsert (orphan-row prevention) ───────────
            # Previously the series row was created here, before any
            # book in the series had been processed. In owned_only
            # mode, the inner book loop then skipped every non-matched
            # book, leaving the series row pointing at nothing — 649
            # such orphans on the user's container before this fix.
            # Now we defer the upsert until the first book in the
            # inner loop actually needs the series_id. If no book
            # needs it (all filtered/skipped), no row is created.
            sid = None  # populated lazily by _ensure_series()

            async def _ensure_series(_sr=sr):
                """Upsert the series row on first call; return cached id thereafter.

                Bug B prevention: the SELECT first looks for an exact
                LOWER(name) match (matches today's behavior), then for
                a normalized-name match against any existing row for
                this author so canonical-form variants like "Mistborn"
                vs "The Mistborn Saga" collapse to one row instead of
                duplicating. First-source-wins on the stored name —
                if a later scan brings a more canonical version, it
                still links to the existing row but doesn't rename it.
                Manual rename via the Series page is the escape hatch.
                """
                nonlocal sid
                if sid is not None:
                    return sid
                row = await (await db.execute(
                    "SELECT id FROM series WHERE LOWER(name) = LOWER(?)",
                    (_sr.name,),
                )).fetchone()
                if row:
                    sid = row["id"]
                else:
                    # Normalized fallback: search this author's existing
                    # series for a normalized-name match before inserting.
                    # _norm_consensus_series mirrors _norm_series in
                    # hardcover.py — strips leading articles, trailing
                    # tail words ("saga", "series", etc.), punctuation.
                    target_norm = _norm_consensus_series(_sr.name)
                    if target_norm:
                        author_series = await (await db.execute(
                            "SELECT id, name FROM series WHERE author_id = ?",
                            (author_id,),
                        )).fetchall()
                        for ar in author_series:
                            if _norm_consensus_series(ar["name"]) == target_norm:
                                sid = ar["id"]
                                break
                if sid is not None:
                    await db.execute(
                        "UPDATE series SET last_lookup_at = ? WHERE id = ?",
                        (time.time(), sid),
                    )
                else:
                    cur = await db.execute(
                        "INSERT INTO series (name, author_id, total_books, last_lookup_at) VALUES (?,?,?,?)",
                        (_sr.name, author_id, _sr.total_books, time.time()),
                    )
                    sid = cur.lastrowid
                return sid

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
                    # Lazy series upsert: only create the series row
                    # now that we know a real book is going to link to it.
                    sid_use = await _ensure_series()
                    sql, vals = _update_existing(matched_row, bk, series_id=sid_use)
                    await db.execute(sql, vals)
                    # Phase 3c: record this source's series claim for the
                    # matched book so consensus can be computed at the
                    # end of lookup_author. We use the SOURCE's reported
                    # series name (sr.name), not the resolved series_id
                    # — different sources may use slightly different
                    # canonical names ("The Mistborn Saga" vs "Mistborn
                    # Saga") and the consensus computer normalizes them.
                    if series_collector is not None:
                        series_collector.setdefault(matched_row["id"], {})[source_name] = (sr.name, bk.series_index)
                    continue
                if owned_only:
                    # Library-only scan: don't add discovered series books
                    # that we don't already own. _ensure_series() is NOT
                    # called on this path — if NO matched_row in this
                    # series fired _ensure_series above, no series row
                    # gets created at all (the orphan-row prevention).
                    continue
                if norm in existing:
                    logger.debug(f"    SKIP (norm dup): '{bk.title}'")
                    continue
                # Insert path: also needs the series row to exist.
                sid_use = await _ensure_series()
                initial_urls = json.dumps({source_name: bk.source_url}) if bk.source_url else "{}"
                await db.execute(f"INSERT OR IGNORE INTO books (title,author_id,series_id,series_index,isbn,cover_url,pub_date,expected_date,is_unreleased,description,page_count,source,source_url,owned,is_new,{source_name}_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0,1,?)",
                    (bk.title, author_id, sid_use, bk.series_index, bk.isbn, bk.cover_url, bk.pub_date, bk.expected_date, 1 if bk.is_unreleased else 0, bk.description, bk.page_count, source_name, initial_urls, bk.external_id))
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
                # Phase 3c: record (None, None) — this source thinks
                # the book is a standalone, not in any series. This
                # captures disagreements where Source A says "in series
                # X" while Source B says "standalone", which is just as
                # important to surface as a name conflict.
                if series_collector is not None:
                    series_collector.setdefault(matched_row["id"], {})[source_name] = (None, None)
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

        # ── Orphan series safety net ─────────────────────────────────
        # Defense in depth: even with the lazy upsert above, some other
        # code path could in theory leave a series row pointing at no
        # books for this author. Scope the cleanup to THIS author's
        # series only (not a global sweep) so we don't accidentally
        # touch concurrently-running scans for other authors. Idempotent
        # — running it twice deletes nothing the second time.
        cleanup_cur = await db.execute(
            "DELETE FROM series WHERE author_id = ? AND id NOT IN "
            "(SELECT DISTINCT series_id FROM books "
            " WHERE series_id IS NOT NULL AND author_id = ?)",
            (author_id, author_id),
        )
        if cleanup_cur.rowcount > 0:
            logger.debug(
                f"  Orphan series cleanup: dropped {cleanup_cur.rowcount} "
                f"unlinked series rows for author_id={author_id}"
            )
        await db.commit()
        return new_books, updated_books
    finally:
        await db.close()


# ─── Phase 3c: source-consensus series suggestions ───────────────────
# Series-name normalization for consensus grouping. Mirrors hardcover.py's
# _norm_series so two sources reporting "The Mistborn Saga" and "Mistborn
# Saga" group together. Standalone (None) is its own bucket and never
# normalizes into a name group.
_RX_CONSENSUS_LEAD = re.compile(r'^(the|a|an)\s+', re.IGNORECASE)
_RX_CONSENSUS_TAIL = re.compile(
    r'\s+(saga|series|trilogy|cycle|chronicles|novels|books)\s*$',
    re.IGNORECASE,
)
_RX_CONSENSUS_PUNCT = re.compile(r'[^\w\s]')


def _norm_consensus_series(name):
    """Normalize a series name for consensus grouping. Returns "" for None."""
    if not name:
        return ""
    n = name.strip()
    n = _RX_CONSENSUS_LEAD.sub('', n)
    for _ in range(3):  # iterative tail strip handles "Mistborn Saga Series"
        new_n = _RX_CONSENSUS_TAIL.sub('', n)
        if new_n == n:
            break
        n = new_n
    n = _RX_CONSENSUS_PUNCT.sub(' ', n).lower()
    return re.sub(r'\s+', ' ', n).strip()


def _norm_consensus_index(idx):
    """Normalize a series index for grouping. 3 == 3.0 == "3"; None stays None.

    Sources can return ints, floats, or strings. We coerce to float and
    round to 1 decimal so 3 / 3.0 / "3" group together but 3.0 and 3.5
    stay distinct (sub-numbered novellas like Edgedancer #2.5).
    """
    if idx is None:
        return None
    try:
        return round(float(idx), 1)
    except (ValueError, TypeError):
        return None


async def _compute_series_suggestions(author_id, series_collector):
    """Tally per-source series claims and write pending suggestions.

    Called by lookup_author() after all sources have run. For each
    book in series_collector, groups the per-source (name, index)
    tuples by their normalized form. If the largest group has >=2
    sources AND that group's value differs from the book's CURRENT
    series state in the DB, upsert a row in book_series_suggestions.

    Suggestion lifecycle (see schema doc in database.py):
      - No existing row + new disagreement → INSERT pending
      - status='applied' → leave alone (user already accepted)
      - status='ignored' + same (name, idx) → leave alone (respect ignore)
      - status='ignored' + different (name, idx) → UPDATE to pending
        (a NEW disagreement, the old ignore doesn't apply)
      - status='pending' → UPDATE to latest values + bump updated_at
      - Consensus no longer disagrees → DELETE row entirely (resolved)

    Computes the consensus by majority vote of normalized tuples. Ties
    are broken by preferring source priority (Goodreads > Hardcover >
    Kobo) — i.e., a 1-1 tie between Goodreads and Hardcover picks the
    Goodreads value, since Goodreads is the most-trusted source. (Tie
    handling rarely fires in practice — most disagreements are 1-1-1
    or 2-1, which is unambiguous.)
    """
    if not series_collector:
        return

    db = await get_db()
    try:
        # Pull current DB state for all books in the collector in one
        # query. Joins to series so we get the canonical name string.
        book_ids = list(series_collector.keys())
        placeholders = ",".join("?" * len(book_ids))
        rows = await (await db.execute(
            f"SELECT b.id, b.title, b.series_index AS cur_idx, "
            f"s.name AS cur_series_name "
            f"FROM books b LEFT JOIN series s ON b.series_id = s.id "
            f"WHERE b.id IN ({placeholders})",
            book_ids,
        )).fetchall()
        current_state = {
            r["id"]: (r["cur_series_name"], r["cur_idx"], r["title"])
            for r in rows
        }

        # Pull existing suggestion rows for these books in one query
        # so we don't issue N round-trips.
        existing_rows = await (await db.execute(
            f"SELECT id, book_id, suggested_series_name, suggested_series_index, "
            f"status FROM book_series_suggestions WHERE book_id IN ({placeholders})",
            book_ids,
        )).fetchall()
        existing_by_book = {r["book_id"]: dict(r) for r in existing_rows}

        # Source priority for tiebreaking the consensus vote. Mirrors
        # SOURCE_PRIORITY in _merge_result; lower number = higher trust.
        SOURCE_RANK = {"goodreads": 1, "hardcover": 2, "kobo": 3}

        suggestions_created = 0
        suggestions_updated = 0
        suggestions_resolved = 0

        for book_id, per_source in series_collector.items():
            if book_id not in current_state:
                continue  # book may have been deleted between scan and now
            cur_name, cur_idx, book_title = current_state[book_id]
            cur_norm_name = _norm_consensus_series(cur_name)
            cur_norm_idx = _norm_consensus_index(cur_idx)

            # Group sources by their normalized claim. Key is the
            # normalized tuple; value is a list of (source_name,
            # raw_name, raw_idx) so we can pick a canonical display
            # value when the group wins.
            groups = {}
            for src_name, (raw_name, raw_idx) in per_source.items():
                key = (_norm_consensus_series(raw_name), _norm_consensus_index(raw_idx))
                groups.setdefault(key, []).append((src_name, raw_name, raw_idx))

            if not groups:
                continue

            # Pick the largest group, with source-priority tiebreak
            # (a group containing Goodreads beats a same-size group
            # without it). Equal-size groups containing the SAME
            # priority source — extremely rare — fall back to dict
            # iteration order, which is stable in Python 3.7+.
            def _group_score(item):
                key, members = item
                size = len(members)
                # Best (lowest) source rank in this group; missing
                # sources score 99 so they lose tiebreaks naturally.
                best_rank = min(SOURCE_RANK.get(m[0], 99) for m in members)
                # Score: size dominates (×100), tiebreak by inverted rank
                return size * 100 + (10 - best_rank)

            largest_key, largest_members = max(groups.items(), key=_group_score)

            # Threshold: need at least 2 sources to call it a consensus.
            if len(largest_members) < 2:
                # Check if there's a stale suggestion to clean up:
                # the consensus collapsed (used to have 2+, now only 1).
                if book_id in existing_by_book:
                    ex = existing_by_book[book_id]
                    if ex["status"] == "pending":
                        await db.execute(
                            "DELETE FROM book_series_suggestions WHERE id = ?",
                            (ex["id"],),
                        )
                        suggestions_resolved += 1
                        logger.debug(
                            f"    SUGGESTION RESOLVED (consensus collapsed): "
                            f"'{book_title}' (book_id={book_id})"
                        )
                continue

            consensus_norm_name, consensus_norm_idx = largest_key

            # Does the consensus actually differ from the current DB value?
            if (consensus_norm_name == cur_norm_name and
                    consensus_norm_idx == cur_norm_idx):
                # Consensus matches what's already in the DB — no
                # suggestion needed. Clean up any stale pending row
                # (the disagreement was resolved by a previous Apply
                # or by the user manually editing).
                if book_id in existing_by_book:
                    ex = existing_by_book[book_id]
                    if ex["status"] == "pending":
                        await db.execute(
                            "DELETE FROM book_series_suggestions WHERE id = ?",
                            (ex["id"],),
                        )
                        suggestions_resolved += 1
                        logger.debug(
                            f"    SUGGESTION RESOLVED (matches current): "
                            f"'{book_title}' (book_id={book_id})"
                        )
                continue

            # Pick the canonical display name from the group. Prefer the
            # highest-priority source's raw name string (so we display
            # "The Mistborn Saga" instead of "Mistborn Saga" if Goodreads
            # is in the winning group).
            canonical_member = min(
                largest_members,
                key=lambda m: SOURCE_RANK.get(m[0], 99),
            )
            suggested_name = canonical_member[1]
            suggested_idx = canonical_member[2]
            sources_agreeing = sorted(
                m[0] for m in largest_members
            )
            sources_json = json.dumps(sources_agreeing)
            now = time.time()

            ex = existing_by_book.get(book_id)
            if ex is None:
                # No existing suggestion — INSERT a new pending row
                await db.execute(
                    "INSERT INTO book_series_suggestions "
                    "(book_id, suggested_series_name, suggested_series_index, "
                    "sources_agreeing, current_series_name, current_series_index, "
                    "status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
                    (book_id, suggested_name, suggested_idx, sources_json,
                     cur_name, cur_idx, now, now),
                )
                suggestions_created += 1
                logger.info(
                    f"    SUGGESTION NEW: '{book_title}' (book_id={book_id}) "
                    f"current=({cur_name!r}, {cur_idx}) → "
                    f"suggested=({suggested_name!r}, {suggested_idx}) "
                    f"by {sources_agreeing}"
                )
                continue

            # Existing row — branch on status
            if ex["status"] == "applied":
                continue  # user already accepted; never re-suggest

            ex_norm_name = _norm_consensus_series(ex["suggested_series_name"])
            ex_norm_idx = _norm_consensus_index(ex["suggested_series_index"])
            same_as_existing = (
                ex_norm_name == consensus_norm_name and
                ex_norm_idx == consensus_norm_idx
            )

            if ex["status"] == "ignored":
                if same_as_existing:
                    continue  # respect the ignore
                # Different consensus → reset to pending. The user's
                # ignore was for the OLD values, not these.
                await db.execute(
                    "UPDATE book_series_suggestions SET "
                    "suggested_series_name=?, suggested_series_index=?, "
                    "sources_agreeing=?, current_series_name=?, "
                    "current_series_index=?, status='pending', updated_at=? "
                    "WHERE id=?",
                    (suggested_name, suggested_idx, sources_json,
                     cur_name, cur_idx, now, ex["id"]),
                )
                suggestions_updated += 1
                logger.info(
                    f"    SUGGESTION REOPENED (was ignored, new consensus): "
                    f"'{book_title}' → ({suggested_name!r}, {suggested_idx})"
                )
                continue

            # status == 'pending' — refresh values + updated_at
            if same_as_existing:
                # Same consensus, just touch updated_at to keep it fresh
                await db.execute(
                    "UPDATE book_series_suggestions SET "
                    "sources_agreeing=?, current_series_name=?, "
                    "current_series_index=?, updated_at=? WHERE id=?",
                    (sources_json, cur_name, cur_idx, now, ex["id"]),
                )
            else:
                # Pending row's consensus shifted to a different value
                await db.execute(
                    "UPDATE book_series_suggestions SET "
                    "suggested_series_name=?, suggested_series_index=?, "
                    "sources_agreeing=?, current_series_name=?, "
                    "current_series_index=?, updated_at=? WHERE id=?",
                    (suggested_name, suggested_idx, sources_json,
                     cur_name, cur_idx, now, ex["id"]),
                )
                suggestions_updated += 1
                logger.info(
                    f"    SUGGESTION UPDATED: '{book_title}' "
                    f"→ ({suggested_name!r}, {suggested_idx})"
                )

        await db.commit()
        if suggestions_created or suggestions_updated or suggestions_resolved:
            logger.info(
                f"  Series suggestions for author_id={author_id}: "
                f"{suggestions_created} new, {suggestions_updated} updated, "
                f"{suggestions_resolved} resolved"
            )
    finally:
        await db.close()


async def _try_source(source, author_name, author_id, our_titles, languages, source_name, existing_titles=None, full_scan=False, owned_only=False, series_collector=None):
    """Try a single source with validation and detailed logging."""
    try:
        logger.info(f"  [{source_name}] {'Full scan' if full_scan else 'Searching'} for '{author_name}'...")
        # Hardcover needs owned_titles to search by book title, and as
        # of Phase 3b-H2 also takes owned_series_names so its per-book
        # series picker can prefer candidates that match what Calibre
        # already has (avoids "Mistborn Saga: Original Trilogy" being
        # picked over "The Mistborn Saga"). Both attributes are stashed
        # on the source instance by lookup_author() before this runs.
        if hasattr(source, '_owned_titles'):
            found = await source.search_author(
                author_name,
                owned_titles=source._owned_titles,
                owned_series_names=getattr(source, '_owned_series_names', None),
            )
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
                    owned_only=owned_only,
                )
            except TypeError:
                # Fallback: source has an older signature without owned_only.
                # Behavior is unchanged (still slow on library-only scans for
                # those sources) until they adopt the kwarg.
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

        n, u = await _merge_result(author_id, full, source_name, languages, full_scan=full_scan, owned_only=owned_only, series_collector=series_collector)
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
        # Phase 3b-H2: collect distinct series names the user already
        # has tagged for this author. Used by Hardcover (and any future
        # source) to prefer matching series candidates over deeper
        # sub-series in the source's series taxonomy.
        series_rows = await (await db.execute(
            "SELECT DISTINCT s.name FROM series s "
            "JOIN books b ON b.series_id = s.id "
            "WHERE b.author_id = ? AND b.owned = 1 AND s.name IS NOT NULL",
            (author_id,)
        )).fetchall()
        our_series_names = [r["name"] for r in series_rows]
    finally:
        await db.close()

    # Phase 3c: per-source series collector. Threaded through every
    # _try_source call so each source's matched-book series claims are
    # recorded. After all sources have run, _compute_series_suggestions()
    # tallies the per-source agreement and writes pending suggestions
    # for any book where 2+ sources agree on a series different from the
    # book's current value. The dict is local to this scan and discarded
    # after the consensus pass — we don't need to persist per-source raw
    # data, only the final consensus diffs.
    series_collector: dict[int, dict[str, tuple]] = {}

    # 1. Goodreads (PRIMARY)
    if settings.get("goodreads_enabled", True):
        total += await _try_source(goodreads, author_name, author_id, our_titles, languages, "goodreads", existing_titles=existing_titles, full_scan=full_scan, owned_only=owned_only, series_collector=series_collector)

    # 2. Hardcover
    if settings.get("hardcover_api_key") and settings.get("hardcover_enabled", True):
        hardcover.update_api_key(settings["hardcover_api_key"])
        hardcover._owned_titles = our_titles
        hardcover._owned_series_names = our_series_names
        total += await _try_source(hardcover, author_name, author_id, our_titles, languages, "hardcover", existing_titles=existing_titles, full_scan=full_scan, owned_only=owned_only, series_collector=series_collector)

    # 3. Kobo
    if settings.get("kobo_enabled", True):
        total += await _try_source(kobo, author_name, author_id, our_titles, languages, "kobo", existing_titles=existing_titles, full_scan=full_scan, owned_only=owned_only, series_collector=series_collector)

    # Phase 3c: compute consensus and write pending suggestions for
    # any per-book disagreement that meets the 2+ sources threshold.
    # Runs after all sources so it sees the full per-book picture.
    if series_collector:
        await _compute_series_suggestions(author_id, series_collector)

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
        # Skip orphan authors (no linked books). These are typically
        # secondary co-authors of multi-author Calibre entries — see
        # routers/authors.py:get_authors() docstring. Scanning them
        # wastes time on a lookup that has no books to merge into.
        rows = await (await db.execute("SELECT id, name FROM authors WHERE COALESCE(last_lookup_at,0) < ? AND id IN (SELECT DISTINCT author_id FROM books) ORDER BY COALESCE(last_lookup_at,0) ASC", (time.time() - cache_sec,))).fetchall()
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
        # Skip orphan authors — same reasoning as run_full_lookup above.
        rows = await (await db.execute("SELECT id, name FROM authors WHERE id IN (SELECT DISTINCT author_id FROM books) ORDER BY sort_name ASC")).fetchall()
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
