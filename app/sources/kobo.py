"""
Kobo — scrapes kobo.com for book metadata.
Uses cloudscraper to bypass Cloudflare protection.
"""
import logging, asyncio, time
from typing import Optional
from lxml import html
from app.sources.base import BaseSource, AuthorResult, BookResult

logger = logging.getLogger("athenascout.kobo")
BASE = "https://www.kobo.com"


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

    async def get_author_books(self, author_name: str, **kw) -> Optional[AuthorResult]:
        # author_name arrives from search_author's external_id, which is
        # now the ORIGINAL user-provided name (Phase 3a) — not a slug that
        # would need lossy .title() reconstruction. Old param name was
        # `author_slug`; renamed for accuracy.
        try:
            search_url = f"{BASE}/us/en/search?query={author_name.replace(' ', '%20')}&fcsearchfield=Author&numrecords=60"
            page_html = await self._fetch(search_url)
            if not page_html:
                return None

            page = html.fromstring(page_html)
            books = []
            series_map = {}

            # New search page format
            items = page.xpath("//a[@data-testid='title']")
            if not items:
                # Old format
                items = page.xpath("//h2[@class='title product-field']/a")
            if not items:
                logger.debug(f"  Kobo: no book items matched on get_author_books page for '{author_name}' ({len(page_html)} bytes)")

            for item in items:
                title = item.text_content().strip()
                href = item.get("href", "")
                if not title:
                    continue

                # Extract Kobo book ID from URL
                kobo_id = href.rstrip("/").split("/")[-1] if href else None

                # Try to get cover image
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

                # Language left as None: Kobo's search-result page doesn't
                # expose a language field, and hardcoding "English" was a
                # Phase 3a correctness bug — the `/us/en/` storefront happens
                # to return English titles today but that's a property of
                # the URL, not a guarantee about individual books. lookup.py
                # `_lang_ok()` treats None as "unknown, assume ok", so the
                # behavior for English-only users is unchanged, and
                # multi-language users no longer get mis-tagged books.
                br = BookResult(
                    title=title, cover_url=cover,
                    external_id=kobo_id, source="kobo",
                    source_url=kobo_url,
                )
                books.append(br)

            logger.info(f"  Kobo: found {len(books)} books for '{author_name}'")

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
