"""
Kobo — scrapes kobo.com for book metadata.
Uses cloudscraper to bypass Cloudflare protection.

Two-pass scraping (Phase 3b-K2):
1. Author search page → list of book URLs + basic title/cover
2. Per-book detail page → series, pub_date, language, isbn, page_count,
   description, publisher, full-res cover

For books already in the DB, the detail fetch is skipped — we only emit
a minimal BookResult so the merge layer can backfill source_url. Same
pattern as goodreads.py uses to keep scan times reasonable.
"""
import logging, asyncio, time, re
from datetime import datetime
from typing import Optional
from lxml import html
from app.sources.base import BaseSource, AuthorResult, BookResult, SeriesResult

logger = logging.getLogger("athenascout.kobo")
BASE = "https://www.kobo.com"


def _parse_kobo_date(text: str) -> Optional[str]:
    """Parse a Kobo 'Release Date:' value to ISO YYYY-MM-DD.

    Kobo's eBook Details panel renders dates as 'June 15, 2011' or, for
    pre-orders/old titles where only month or year is known, 'June 2011'
    or '2011'. We try each from most-specific to least and fall back to
    None on a miss so the merge layer treats the field as unknown.
    """
    if not text:
        return None
    text = text.strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %Y", "%b %Y", "%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _create_scraper():
    try:
        import cloudscraper
        return cloudscraper.create_scraper(
            browser={"custom": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0"},
        )
    except ImportError:
        logger.warning("cloudscraper not installed — Kobo will be limited")
        return None


class KoboSource(BaseSource):
    """Kobo uses cloudscraper (sync) instead of httpx, so it doesn't use the
    base class's _get/_get_client machinery. It still inherits from BaseSource
    for interface consistency and shared logger/rate_limit."""
    name = "kobo"

    def __init__(self, rate_limit: float = 3.0):
        super().__init__(rate_limit=rate_limit)
        self._session = None

    def _get_session(self):
        if self._session is None:
            self._session = _create_scraper()
        return self._session

    def _fetch_sync(self, url: str) -> Optional[str]:
        session = self._get_session()
        if not session:
            return None
        time.sleep(self.rate_limit)
        try:
            r = session.get(url, timeout=15)
            if r.status_code == 200:
                return r.text
            return None
        except Exception as e:
            logger.debug(f"  Kobo fetch error: {e}")
            return None

    async def _fetch(self, url: str) -> Optional[str]:
        return await asyncio.to_thread(self._fetch_sync, url)

    async def _get_book_details(self, kobo_url: str) -> dict:
        """Fetch a Kobo book detail page and extract structured metadata.

        Phase 3b-K2: prior to this, get_author_books only returned title +
        cover_url + kobo_id from search results, and series detection was
        always empty (Kobo's search page doesn't surface series). The book
        detail page contains everything we want in clean HTML structure
        (no JS rendering required for these fields):

          - h1.title.product-field                       → title
          - span.series.product-field a                  → series name
          - span.sequenced-name-prefix                   → 'Book N -' index
          - div.book-stats strong (next to 'Pages')      → page_count
          - div.bookitem-secondary-metadata li           → release date,
                                                            ISBN, language,
                                                            publisher
          - div[data-full-synopsis]                      → description
          - img.cover-image                              → high-res cover

        Returns a dict of extracted fields; all values default to None on
        miss so the caller can build a BookResult with COALESCE-friendly
        defaults.
        """
        details = {
            "title": None, "series_name": None, "series_index": None,
            "pub_date": None, "language": None, "isbn": None,
            "page_count": None, "description": None, "publisher": None,
            "cover_url": None,
        }
        page_html = await self._fetch(kobo_url)
        if not page_html:
            return details
        try:
            page = html.fromstring(page_html)
        except Exception as e:
            logger.debug(f"  Kobo: detail-page parse error for {kobo_url}: {e}")
            return details

        # Title (canonical, used to validate the page actually loaded)
        title_el = page.xpath("//h1[contains(@class,'title') and contains(@class,'product-field')]/text()")
        if title_el:
            details["title"] = title_el[0].strip()

        # High-res cover (Kobo serves a 353x569 version on detail pages
        # vs the 80x120 thumbnail on search results)
        cover_el = page.xpath("//img[contains(@class,'cover-image')]/@src")
        if cover_el:
            c = cover_el[0]
            details["cover_url"] = ("https:" + c) if c.startswith("//") else c

        # Series name (anchor inside span.series.product-field)
        series_el = page.xpath("//span[contains(@class,'series') and contains(@class,'product-field')]//a/text()")
        if series_el:
            details["series_name"] = series_el[0].strip()

        # Series index — Kobo renders this as "Book 1 - " in a separate
        # span before the series link. Pull the first number; supports
        # decimals like "Book 2.5 - " for novellas.
        seq_el = page.xpath("//span[@class='sequenced-name-prefix']/text()")
        if seq_el:
            m = re.search(r'(\d+(?:\.\d+)?)', seq_el[0])
            if m:
                try:
                    details["series_index"] = float(m.group(1))
                except ValueError:
                    pass

        # Page count: <strong>592</strong> followed by <span>Pages</span>
        # inside book-stats. We pick the strong whose sibling span text is
        # exactly "Pages" so we don't confuse it with "hours" or "words".
        pages_el = page.xpath(
            "//div[contains(@class,'book-stats')]"
            "//div[@class='column'][.//span[normalize-space()='Pages']]"
            "//strong/text()"
        )
        if pages_el:
            try:
                details["page_count"] = int(pages_el[0].strip())
            except ValueError:
                pass

        # eBook Details panel (Release Date, ISBN, Language, Publisher).
        # Each <li> is a labeled field except the very first which is just
        # the publisher name (no "Publisher:" prefix on Kobo).
        detail_lis = page.xpath("//div[contains(@class,'bookitem-secondary-metadata')]//li")
        known_prefixes = ("Release Date:", "Book ID:", "Language:", "Imprint:",
                          "Download options:", "File size:", "ISBN:")
        for li in detail_lis:
            text = li.text_content().strip()
            if text.startswith("Release Date:"):
                details["pub_date"] = _parse_kobo_date(text.split(":", 1)[1])
            elif text.startswith("Book ID:") or text.startswith("ISBN:"):
                # Kobo labels their identifier "Book ID" but it's the EAN/ISBN-13
                isbn = text.split(":", 1)[1].strip()
                # Validate it looks like an ISBN-13 (13 digits) before accepting
                if re.fullmatch(r'\d{10}|\d{13}', isbn):
                    details["isbn"] = isbn
            elif text.startswith("Language:"):
                details["language"] = text.split(":", 1)[1].strip()
            elif not any(text.startswith(p) for p in known_prefixes):
                # Unlabeled <li> = publisher name (always the first li in
                # the panel). Only set once so we don't overwrite with
                # later unlabeled noise.
                if not details["publisher"]:
                    details["publisher"] = text

        # Description: Kobo hides the full synopsis in <div data-full-synopsis>
        # which is display:none until the user clicks "Read more". The text
        # is present in the static HTML so we don't need JS execution.
        # We strip the trailing series listing (Kobo often appends "The
        # Expanse: Leviathan Wakes / Caliban's War / ..." after the synopsis)
        # by capping at 2000 chars — long enough for any real synopsis,
        # short enough to drop the bibliography.
        desc_el = page.xpath("//div[@data-full-synopsis]")
        if desc_el:
            desc_text = desc_el[0].text_content().strip()
            desc_text = re.sub(r'\s+', ' ', desc_text)
            if desc_text:
                details["description"] = desc_text[:2000]

        return details

    async def search_author(self, author_name: str) -> Optional[AuthorResult]:
        try:
            search_url = f"{BASE}/us/en/search?query={author_name.replace(' ', '%20')}&fcsearchfield=Author"
            page_html = await self._fetch(search_url)
            if not page_html:
                return None

            page = html.fromstring(page_html)

            # Check for author match in results
            # New Kobo: data-testid='search-result-widget'
            # Old Kobo: h2.title.product-field
            result_titles_new = page.xpath("//a[@data-testid='title']")
            result_titles_old = page.xpath("//h2[@class='title product-field']/a")
            result_titles = result_titles_new or result_titles_old

            # external_id stores the ORIGINAL author_name (not a URL slug),
            # because Kobo has no stable public author-id and get_author_books
            # needs to re-query by name. The pre-Phase-3a code stored a slug
            # here and then reconstructed the name via `slug.replace("-", " ").title()`,
            # which was lossy on apostrophes, accents, and hyphens
            # ("O'Brien" → "O'brien", "jean-luc" → "Jean Luc"). Round-tripping
            # the raw name avoids the whole problem.
            if result_titles:
                which = "new layout" if result_titles_new else "old layout"
                logger.debug(f"  Kobo: matched {len(result_titles)} results via {which} selectors for '{author_name}'")
                return AuthorResult(name=author_name, external_id=author_name)

            # Neither selector matched. Two possibilities: Kobo genuinely
            # has no results (expected for obscure authors), or Kobo changed
            # their DOM again and our selectors are stale. Distinguish the
            # two via explicit markers before the pre-Phase-3a code's
            # "assume the author exists" fallback — that fallback was
            # silently masking DOM changes that would show up as empty
            # AuthorResult objects downstream.
            if "No results found" in page_html:
                logger.debug(f"  Kobo: 'No results found' marker present for '{author_name}'")
                return None
            if len(page_html) < 5000:
                logger.debug(f"  Kobo: short response ({len(page_html)} bytes) for '{author_name}' — likely empty/error page")
                return None

            # Page is >5000 bytes and has no "No results" marker but NONE
            # of our selectors matched. Almost certainly a Kobo DOM change.
            # Emit a warning so the user sees it and can report the layout
            # change — then return None rather than the old lenient
            # fallback, which was constructing fake AuthorResults that
            # caused get_author_books to fire a pointless follow-up fetch.
            logger.warning(
                f"  Kobo: {len(page_html)} bytes returned for '{author_name}' "
                f"but no result selectors matched — Kobo may have changed their DOM"
            )
            return None

        except Exception as e:
            logger.error(f"Kobo search error '{author_name}': {e}")
            return None

    async def get_author_books(self, author_name: str, existing_titles: set = None, **kw) -> Optional[AuthorResult]:
        # author_name arrives from search_author's external_id, which is
        # now the ORIGINAL user-provided name (Phase 3a) — not a slug that
        # would need lossy .title() reconstruction. Old param name was
        # `author_slug`; renamed for accuracy.
        #
        # Phase 3b-K2: For each search-result book, we now visit the book
        # detail page to extract series, language, pub_date, ISBN, page
        # count, and description — Kobo's search results only carry
        # title + cover. To keep scan time bounded, books that already
        # exist in the DB get a minimal BookResult (URL backfill only,
        # no detail fetch). Same pattern as goodreads.py.
        if existing_titles is None:
            existing_titles = set()
        try:
            base_search_url = (
                f"{BASE}/us/en/search?query={author_name.replace(' ', '%20')}"
                f"&fcsearchfield=Author&numrecords=60"
            )
            page_html = await self._fetch(base_search_url)
            if not page_html:
                return None

            page = html.fromstring(page_html)
            books = []
            series_map = {}

            def _extract_items(p):
                # New search page format
                its = p.xpath("//a[@data-testid='title']")
                if not its:
                    # Old format
                    its = p.xpath("//h2[@class='title product-field']/a")
                return its

            items = _extract_items(page)
            if not items:
                logger.debug(f"  Kobo: no book items matched on get_author_books page for '{author_name}' ({len(page_html)} bytes)")

            # ── Phase 3b-K1: pagination ────────────────────────────────
            # Kobo's author search caps at 60 results per page (controlled
            # by &numrecords=60 in our URL). For very prolific authors
            # (J.N. Chaney has ~288 audiobook entries on Kobo, Sanderson
            # has 60+), the first page silently truncates the rest.
            #
            # Pagination is JavaScript-driven `<button>` elements rather
            # than href links, but Kobo's canonical URL exposes the
            # state as `&pagenumber=N` (page 1 has no param). The total
            # page count is rendered as plain text inside
            # `<button data-testid="pagination-item-last-page"><span>N</span></button>`,
            # which we parse to know when to stop.
            #
            # MAX_PAGES caps runaway scans — at 60 results × 10 pages
            # that's 600 entries, comfortably above any plausible single
            # author's catalog while keeping the worst case bounded
            # (10 fetches × ~3s rate-limit = ~30s for the search-results
            # phase, with the per-book detail enrichment dwarfing it
            # anyway for any author with this many books).
            MAX_PAGES = 10
            last_page = 1
            last_page_btns = page.xpath(
                "//button[@data-testid='pagination-item-last-page']//span/text()"
            )
            if last_page_btns:
                try:
                    last_page = int(last_page_btns[0].strip())
                except (ValueError, TypeError):
                    last_page = 1

            if last_page > 1:
                target_pages = min(last_page, MAX_PAGES)
                logger.info(
                    f"  Kobo: '{author_name}' has {last_page} result pages — "
                    f"fetching {target_pages}{' (capped)' if last_page > MAX_PAGES else ''}"
                )
                for pn in range(2, target_pages + 1):
                    page_url = f"{base_search_url}&pagenumber={pn}"
                    extra_html = await self._fetch(page_url)
                    if not extra_html:
                        logger.debug(f"  Kobo: page {pn} fetch failed — stopping pagination")
                        break
                    extra_page = html.fromstring(extra_html)
                    extra_items = _extract_items(extra_page)
                    if not extra_items:
                        logger.debug(f"  Kobo: page {pn} returned 0 items — stopping pagination")
                        break
                    items = items + extra_items
                    logger.debug(f"  Kobo: page {pn} added {len(extra_items)} raw items (running total {len(items)})")

            # Phase 1: collect raw search results (title, href, cover thumbnail).
            # Phase 3b-K2-fix: dedupe by kobo_id. The XPath
            # `//a[@data-testid='title']` matches BOTH the cover-image
            # anchor and the title-text anchor for each result, so without
            # dedupe every book gets processed twice (visible in the first
            # 3b-K2 verification scan as paired DETAIL/SKIP-KNOWN log lines).
            raw_books = []
            seen_ids = set()
            for item in items:
                title = item.text_content().strip()
                href = item.get("href", "")
                if not title:
                    continue

                # Extract Kobo book ID from URL
                kobo_id = href.rstrip("/").split("/")[-1] if href else None

                # Dedupe: skip if we've already seen this kobo_id. Falls
                # back to (title, href) for items missing a kobo_id so
                # we still dedupe correctly on edge cases.
                dedupe_key = kobo_id or (title, href)
                if dedupe_key in seen_ids:
                    continue
                seen_ids.add(dedupe_key)

                # Try to get cover image (thumbnail from search page; will
                # be replaced with the full-res version from the detail
                # page if the per-book fetch runs)
                cover = None
                parent = item.xpath("ancestor::div[contains(@class,'item-detail') or contains(@class,'result-item')]")
                if parent:
                    img = parent[0].xpath(".//img/@src")
                    if img:
                        cover = img[0]
                        if cover.startswith("//"):
                            cover = "https:" + cover

                # Build full Kobo URL
                kobo_url = None
                if href:
                    kobo_url = href if href.startswith("http") else f"https://www.kobo.com{href}"

                raw_books.append({
                    "title": title, "kobo_id": kobo_id,
                    "cover": cover, "kobo_url": kobo_url,
                })

            # Phase 2: per-book detail enrichment. Skip-known mirrors the
            # goodreads.py logic — for titles that already match something
            # in the DB, we only need to backfill the source_url.
            def _norm(t):
                t = re.sub(r'[^\w\s]', '', t.lower()).strip()
                return re.sub(r'\s+', ' ', t)

            existing_norm = {_norm(t) for t in existing_titles}
            skipped_known = 0
            enriched = 0

            for i, rb in enumerate(raw_books):
                norm = _norm(rb["title"])
                is_known = bool(existing_norm) and any(
                    norm == et or norm in et or et in norm for et in existing_norm
                )

                # Log progress every 5 books (Kobo is slower than Goodreads
                # because of cloudscraper's sync HTTP)
                if (i + 1) % 5 == 0 or i == 0:
                    logger.info(f"  Kobo: processing book {i+1}/{len(raw_books)}...")

                if is_known or not rb["kobo_url"]:
                    # Minimal BookResult for URL backfill — no detail fetch.
                    # Language left None so lookup.py's _lang_ok treats it
                    # as "unknown, assume ok".
                    skipped_known += 1
                    logger.debug(f"    SKIP-KNOWN (URL backfill): '{rb['title']}'")
                    br = BookResult(
                        title=rb["title"], cover_url=rb["cover"],
                        external_id=rb["kobo_id"], source="kobo",
                        source_url=rb["kobo_url"],
                    )
                    books.append(br)
                    continue

                # Unknown book — visit the detail page for full metadata
                details = await self._get_book_details(rb["kobo_url"])
                enriched += 1
                logger.debug(
                    f"    DETAIL: '{rb['title']}' → series={details.get('series_name')}"
                    f"#{details.get('series_index')}, date={details.get('pub_date')},"
                    f" lang={details.get('language')}, isbn={details.get('isbn')},"
                    f" pages={details.get('page_count')}"
                )

                br = BookResult(
                    title=rb["title"],
                    series_name=details.get("series_name"),
                    series_index=details.get("series_index"),
                    isbn=details.get("isbn"),
                    cover_url=details.get("cover_url") or rb["cover"],
                    pub_date=details.get("pub_date"),
                    description=details.get("description"),
                    page_count=details.get("page_count"),
                    external_id=rb["kobo_id"],
                    language=details.get("language"),
                    source="kobo",
                    source_url=rb["kobo_url"],
                )

                if details.get("series_name"):
                    sname = details["series_name"]
                    if sname not in series_map:
                        series_map[sname] = SeriesResult(name=sname, books=[])
                    series_map[sname].books.append(br)
                else:
                    books.append(br)

            logger.info(
                f"  Kobo: found {len(books) + sum(len(s.books) for s in series_map.values())} "
                f"books for '{author_name}' ({enriched} enriched, {skipped_known} URL-backfill)"
            )

            return AuthorResult(
                name=author_name, external_id=author_name,
                books=books, series=list(series_map.values()),
            )
        except Exception as e:
            logger.error(f"Kobo author books error '{author_name}': {e}")
            return None

    async def close(self):
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
        await super().close()
