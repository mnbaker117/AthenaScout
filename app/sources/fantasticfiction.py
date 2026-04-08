"""
FantasticFiction — scrapes fantasticfiction.com.
Uses cloudscraper to bypass Cloudflare protection.
Tries direct author page URL, then CloudSearch API.
"""
import logging, re, asyncio, time
from typing import Optional
from lxml import html
from app.sources.base import BaseSource, AuthorResult, BookResult, SeriesResult

# Series-index extraction patterns. FantasticFiction lists books inside a
# series section in several formats over the years; we try all of them and
# take the first hit. Each pattern captures a float (supports "1.5" for
# novella entries like "Book 2.5 of Series").
#
#   "Title (Series Name, book 3)"      → pattern 1
#   "Title (#3)"                        → pattern 1
#   "3. Title"                          → pattern 2 (leading ordinal)
#   "Title — Book 3"                    → pattern 3 (em-dash separated)
_RX_FF_IDX_PAREN = re.compile(r'\((?:[^)]*?,\s*)?(?:book|#)\s*(\d+(?:\.\d+)?)\s*\)', re.IGNORECASE)
_RX_FF_IDX_LEAD  = re.compile(r'^\s*(\d+(?:\.\d+)?)[\.)]\s+')
_RX_FF_IDX_DASH  = re.compile(r'[—\-–]\s*book\s*(\d+(?:\.\d+)?)\s*$', re.IGNORECASE)


def _extract_ff_series_index(raw_text: str) -> Optional[float]:
    """Try each of the known FF index patterns on `raw_text`; return float or None."""
    for rx in (_RX_FF_IDX_PAREN, _RX_FF_IDX_LEAD, _RX_FF_IDX_DASH):
        m = rx.search(raw_text)
        if m:
            try:
                return float(m.group(1))
            except (ValueError, TypeError):
                continue
    return None

logger = logging.getLogger("athenascout.fantasticfiction")
BASE = "https://www.fantasticfiction.com"


def _create_scraper():
    """Create a cloudscraper session."""
    try:
        import cloudscraper
        scraper = cloudscraper.create_scraper(
            browser={"custom": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0"},
        )
        logger.info("  FantasticFiction: cloudscraper session created")
        return scraper
    except ImportError:
        logger.warning("cloudscraper not installed — FantasticFiction/Kobo will be limited")
        return None
    except Exception as e:
        logger.warning(f"cloudscraper creation failed: {type(e).__name__}: {e}")
        return None


class FantasticFictionSource(BaseSource):
    """FantasticFiction uses cloudscraper (sync) instead of httpx, so it
    doesn't use the base class's _get/_get_client machinery. It still inherits
    from BaseSource for interface consistency and shared logger/rate_limit."""
    name = "fantasticfiction"

    def __init__(self, rate_limit: float = 2.0):
        super().__init__(rate_limit=rate_limit)
        self._session = None

    def _get_session(self):
        if self._session is None:
            self._session = _create_scraper()
        return self._session

    def _fetch_sync(self, url: str) -> Optional[str]:
        """Synchronous fetch using cloudscraper."""
        session = self._get_session()
        if not session:
            logger.warning("  FantasticFiction: cloudscraper not available")
            return None
        time.sleep(self.rate_limit)
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 200 and len(r.text) > 1000:
                # Check it's not a Cloudflare challenge page
                if "challenge" in r.text[:500].lower() or "cf-browser-verification" in r.text[:1000].lower():
                    logger.info(f"  FantasticFiction: Cloudflare challenge page for {url}")
                    return None
                return r.text
            logger.info(f"  FantasticFiction: got HTTP {r.status_code} ({len(r.text)} bytes) for {url}")
            return None
        except Exception as e:
            logger.info(f"  FantasticFiction: fetch error: {type(e).__name__}: {e}")
            return None

    async def _fetch(self, url: str) -> Optional[str]:
        """Async wrapper for cloudscraper fetch."""
        return await asyncio.to_thread(self._fetch_sync, url)

    async def search_author(self, author_name: str) -> Optional[AuthorResult]:
        """Find author on FantasticFiction by guessing their URL."""
        # URL pattern: /first-letter-of-lastname/firstname-lastname/
        parts = author_name.lower().split()
        if len(parts) < 2:
            return None

        # Try: /b/robyn-bee/ or /c/james-s-a-corey/
        last_initial = parts[-1][0]
        slug = "-".join(parts)
        # Remove dots from initials: "james s. a. corey" → "james-s-a-corey"
        slug = slug.replace(".", "")
        author_path = f"{last_initial}/{slug}"

        page_html = await self._fetch(f"{BASE}/{author_path}/")
        if page_html:
            logger.info(f"  FantasticFiction: found author via /{author_path}/")
            return AuthorResult(name=author_name, external_id=author_path)

        # Try alternate URL patterns
        # Some authors have middle names dropped: "james-corey" instead of "james-s-a-corey"
        if len(parts) > 2:
            short_slug = f"{parts[0]}-{parts[-1]}"
            alt_path = f"{parts[-1][0]}/{short_slug}"
            page_html = await self._fetch(f"{BASE}/{alt_path}/")
            if page_html:
                logger.info(f"  FantasticFiction: found author via alternate /{alt_path}/")
                return AuthorResult(name=author_name, external_id=alt_path)

        logger.info(f"  FantasticFiction: could not find author '{author_name}'")
        return None

    async def get_author_books(self, author_path: str, **kw) -> Optional[AuthorResult]:
        """Scrape the FantasticFiction author page for all books."""
        try:
            page_html = await self._fetch(f"{BASE}/{author_path}/")
            if not page_html:
                logger.info(f"  FantasticFiction: could not load author page /{author_path}/")
                return None

            page = html.fromstring(page_html)

            # Get author name
            name_el = page.xpath("//h1")
            author_name = name_el[0].text_content().strip() if name_el else "Unknown"

            books = []
            series_map = {}
            current_series = None

            # FantasticFiction structure: series headings followed by book links
            # Look for all content divs
            for el in page.xpath("//*"):
                tag = el.tag
                cls = el.get("class", "")

                # Detect series headings
                if tag in ("h3", "h2") or "sectionheading" in cls:
                    heading = el.text_content().strip()
                    if heading and not heading.startswith("Also by"):
                        current_series = re.sub(r'\s*[Ss]eries\s*$', '', heading).strip()

                # Detect book links
                if tag == "a" and el.get("href", "").endswith(".htm"):
                    href = el.get("href", "")
                    title = el.text_content().strip()
                    if not title or "/index" in href:
                        continue
                    # Must be a book page link (has path with author dir)
                    if not re.search(r'/\w+/[\w-]+/[\w-]+\.htm', href) and not re.search(r'/[\w-]+\.htm$', href):
                        continue

                    # Get year from sibling span
                    year_el = el.getnext()
                    pub_date = None
                    if year_el is not None:
                        yt = year_el.text_content().strip().strip("()")
                        if yt and re.match(r'\d{4}$', yt):
                            pub_date = f"{yt}-01-01"

                    # Series index extraction. FantasticFiction is a
                    # series-tracking site by design — it's the strongest
                    # source for "which book in the series is this?", but
                    # the pre-Phase-3a code never parsed the index at all.
                    # We look at the link text itself and the parent element
                    # text (which often includes "(Series, book 3)" suffixes
                    # that aren't part of the link) and take the first match
                    # from any of the known FF patterns.
                    #
                    # Left None on miss — the merge logic in lookup.py will
                    # accept None and leave series_index null; a higher-
                    # priority source can fill it in later.
                    series_index = _extract_ff_series_index(title)
                    if series_index is None:
                        parent = el.getparent()
                        if parent is not None:
                            ptext = parent.text_content().strip()
                            if ptext and ptext != title:
                                series_index = _extract_ff_series_index(ptext)

                    ext_id = re.search(r'/([^/]+)\.htm$', href)
                    ff_url = f"https://www.fantasticfiction.com{href}" if href and not href.startswith("http") else href
                    br = BookResult(
                        title=title, pub_date=pub_date,
                        series_index=series_index,
                        external_id=ext_id.group(1) if ext_id else None,
                        source="fantasticfiction", language="English",
                        source_url=ff_url,
                    )

                    if current_series:
                        br.series_name = current_series
                        if current_series not in series_map:
                            series_map[current_series] = SeriesResult(name=current_series, books=[])
                        series_map[current_series].books.append(br)
                    else:
                        books.append(br)

            total = len(books) + sum(len(s.books) for s in series_map.values())
            if total == 0:
                # Author page loaded successfully (>1000 bytes, no CF
                # challenge) but our XPath extraction produced nothing.
                # Almost always means FF changed their markup or the page
                # uses a different structure than the one the scraper
                # recognizes — log the size so the user can compare to
                # a known-good scan.
                logger.warning(
                    f"  FantasticFiction: loaded /{author_path}/ ({len(page_html)} bytes) "
                    f"but parsed 0 books — FF may have changed their HTML structure"
                )
            else:
                logger.info(f"  FantasticFiction: found {total} books for '{author_name}'")

            return AuthorResult(
                name=author_name, external_id=author_path,
                books=books, series=list(series_map.values()),
            )
        except Exception as e:
            logger.error(f"FantasticFiction author page error '{author_path}': {e}")
            return None

    async def close(self):
        if self._session:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
        await super().close()
