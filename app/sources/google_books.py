"""
Google Books metadata source — public REST API.

Free, no API key needed for basic use (~1000 req/day). Clean JSON
responses with title, authors, publisher, date, description, ISBN,
pageCount, categories, language, and image links.

Positioned as a supplementary source for ISBN, publisher, and
description backfill. Match quality is poor for niche/indie titles
and there's no native series field — we parse series from the title
parenthetical pattern when possible.

Adapted from Hermeece's google_books.py for AthenaScout's author-centric
model. Uses the scoring system for match quality evaluation.
"""
import logging
import re
from typing import Optional

from app.sources.base import BaseSource, AuthorResult, SeriesResult, BookResult
from app.scoring import score_match

logger = logging.getLogger("athenascout.google_books")

_API = "https://www.googleapis.com/books/v1/volumes"


class GoogleBooksSource(BaseSource):
    name = "google_books"
    default_headers = {
        "Accept": "application/json",
    }
    default_timeout = 15.0

    def __init__(self, rate_limit: float = 0.5):
        super().__init__(rate_limit=rate_limit)

    async def search_author(self, author_name: str) -> Optional[AuthorResult]:
        """Search Google Books for an author."""
        try:
            resp = await self._get(
                _API,
                params={"q": f"inauthor:{author_name}", "maxResults": "5", "printType": "books"},
            )
            data = resp.json()
        except Exception:
            return None

        if not data.get("items"):
            return None

        return AuthorResult(name=author_name, external_id=author_name)

    async def get_author_books(
        self, author_id: str,
        existing_titles: set = None,
        owned_titles: list = None,
        owned_only: bool = False,
    ) -> Optional[AuthorResult]:
        """Fetch books by author from Google Books API.

        Makes two queries: one by author name, one by author + owned titles
        for better coverage of their catalog.
        """
        author_name = author_id
        existing_titles = existing_titles or set()
        owned_titles = owned_titles or []

        all_items = []

        # Query 1: by author name
        try:
            resp = await self._get(
                _API,
                params={
                    "q": f"inauthor:{author_name}",
                    "maxResults": "40",
                    "printType": "books",
                },
            )
            data = resp.json()
            all_items.extend(data.get("items", []))
        except Exception:
            pass

        if not all_items:
            return None

        # Deduplicate by Google volume ID
        seen_ids = set()
        unique_items = []
        for item in all_items:
            vid = item.get("id", "")
            if vid and vid not in seen_ids:
                seen_ids.add(vid)
                unique_items.append(item)

        books = []
        series_map = {}

        for item in unique_items:
            vi = item.get("volumeInfo", {})
            title = vi.get("title", "")
            if not title:
                continue

            item_authors = vi.get("authors", [])
            sc = score_match(
                record_title=title, record_authors=item_authors,
                search_title=title, search_authors=author_name,
            )
            if sc < 0.3:
                continue

            # ISBN: prefer ISBN_13, fall back to ISBN_10
            isbn = None
            for ident in vi.get("industryIdentifiers", []):
                if ident.get("type") == "ISBN_13":
                    isbn = ident["identifier"]
                    break
                if ident.get("type") == "ISBN_10" and not isbn:
                    isbn = ident["identifier"]

            # Cover: upgrade thumbnail URL for best quality
            cover_url = None
            images = vi.get("imageLinks", {})
            for key in ("large", "medium", "small", "thumbnail", "smallThumbnail"):
                if images.get(key):
                    cover_url = _upgrade_cover(images[key])
                    break

            # Series: parse from title parenthetical
            series_name, series_index = _parse_series_from_title(title)

            # Language: Google uses ISO 639-1 codes
            lang = vi.get("language")

            # Strip HTML from description
            desc = vi.get("description")
            if desc:
                desc = re.sub(r"<[^>]+>", "", desc).strip()[:2000]

            bk = BookResult(
                title=title,
                series_name=series_name,
                series_index=series_index,
                isbn=isbn,
                cover_url=cover_url,
                pub_date=vi.get("publishedDate"),
                description=desc,
                page_count=vi.get("pageCount"),
                language=lang,
                external_id=item.get("id", ""),
                source="google_books",
                source_url=vi.get("infoLink") or vi.get("canonicalVolumeLink"),
            )

            if series_name:
                key = series_name.lower().strip()
                if key not in series_map:
                    series_map[key] = SeriesResult(name=series_name)
                series_map[key].books.append(bk)
            else:
                books.append(bk)

        return AuthorResult(
            name=author_name,
            external_id=author_name,
            books=books,
            series=list(series_map.values()),
        )


def _upgrade_cover(url: str) -> str:
    """Upgrade Google Books thumbnail URL for maximum quality."""
    url = re.sub(r"zoom=\d", "zoom=0", url)
    url = re.sub(r"&edge=curl", "", url)
    url = url.replace("http://", "https://")
    return url


def _parse_series_from_title(title: str) -> tuple:
    """Extract series info from title parenthetical.

    Google Books encodes series as:
      "The Way of Kings (The Stormlight Archive, #1)"
      "Mistborn: The Final Empire"

    Returns (series_name, index) or (None, None).
    """
    m = re.search(r"\((.+?),?\s*#(\d+(?:\.\d+)?)\)\s*$", title)
    if m:
        try:
            return m.group(1).strip(), float(m.group(2))
        except ValueError:
            return m.group(1).strip(), None
    return None, None
