"""
Kobo — scrapes kobo.com for book metadata.
Uses cloudscraper to bypass Cloudflare protection.
"""
import logging, re, asyncio, time
from typing import Optional
from lxml import html
from app.sources.base import BaseSource, AuthorResult, BookResult, SeriesResult

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
    name = "kobo"

    def __init__(self, rate_limit: float = 3.0):
        self.rate_limit = rate_limit
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
            author_found = False
            result_titles = page.xpath("//a[@data-testid='title']")
            if not result_titles:
                result_titles = page.xpath("//h2[@class='title product-field']/a")

            if result_titles:
                author_found = True
                slug = author_name.lower().replace(" ", "-").replace(".", "")
                return AuthorResult(name=author_name, external_id=slug)

            # Check if page has any book results at all
            if "No results found" in page_html or len(page_html) < 5000:
                return None

            slug = author_name.lower().replace(" ", "-").replace(".", "")
            return AuthorResult(name=author_name, external_id=slug)

        except Exception as e:
            logger.error(f"Kobo search error '{author_name}': {e}")
            return None

    async def get_author_books(self, author_slug: str, **kw) -> Optional[AuthorResult]:
        try:
            author_name = author_slug.replace("-", " ").title()
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

                br = BookResult(
                    title=title, cover_url=cover,
                    external_id=kobo_id, source="kobo", language="English",
                    source_url=kobo_url,
                )
                books.append(br)

            logger.info(f"  Kobo: found {len(books)} books for '{author_name}'")

            return AuthorResult(
                name=author_name, external_id=author_slug,
                books=books, series=list(series_map.values()),
            )
        except Exception as e:
            logger.error(f"Kobo author books error '{author_slug}': {e}")
            return None

    async def close(self):
        if self._session:
            self._session.close()
