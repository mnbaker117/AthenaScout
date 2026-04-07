"""
Hardcover.app — GraphQL API.
Based on the official Calibre Hardcover plugin approach:
1. Search returns flat ids list  
2. Fetch books by ids with fragments
3. Auth: 'Authorization' header with full value (user pastes 'Bearer ...')
"""
import asyncio
import httpx, logging, json
from typing import Optional
from app.sources.base import BaseSource, AuthorResult, BookResult, SeriesResult

logger = logging.getLogger("athenascout.hardcover")
API = "https://api.hardcover.app/v1/graphql"

# Fragments matching the official plugin + book-level contributions
FRAGMENTS = """
fragment BookData on books {
  id title slug rating description
  series: cached_featured_series
  book_series { position series { name id } }
  tags: cached_tags
  canonical_id
  contributions { author { name id } }
}
fragment EditionData on editions {
  title id isbn_13 asin
  contributors: cached_contributors
  image: cached_image
  reading_format_id
  release_date
  users_count
  language { code3 }
}
"""

SEARCH_QUERY = """
query Search($query: String!) {
  search(query: $query, query_type: "Book", per_page: 50) {
    ids
    results
  }
}
"""

FIND_BOOKS_BY_IDS = FRAGMENTS + """
query FindBooksByIds($ids: [Int!], $languages: [String!]) {
  books(where: {id: {_in: $ids}}, order_by: {users_read_count: desc_nulls_last}) {
    ...BookData
    editions(
      where: {reading_format_id: {_in: [1, 4]}, language: {_or: [{code3: {_in: $languages}}, {code3: {_is_null: true}}]}}
      order_by: {users_count: desc_nulls_last}
    ) { ...EditionData }
  }
}
"""

# Direct author query (may work with some API keys)
AUTHOR_BOOKS_QUERY = FRAGMENTS + """
query AuthorBooks($id: Int!, $languages: [String!]) {
  authors(where: {id: {_eq: $id}}) {
    id name bio image { url }
    book_authors(order_by: {book: {release_date: asc}}) {
      book {
        ...BookData
        editions(
          where: {reading_format_id: {_in: [1, 4]}, language: {_or: [{code3: {_in: $languages}}, {code3: {_is_null: true}}]}}
          order_by: {users_count: desc_nulls_last}
          limit: 1
        ) { ...EditionData }
      }
    }
  }
}
"""


class HardcoverSource(BaseSource):
    name = "hardcover"
    default_headers = {
        "Content-Type": "application/json",
        "User-Agent": "AthenaScout/1.0 (https://github.com/mnbaker117/AthenaScout)",
    }
    default_timeout = 30.0

    def __init__(self, api_key: str = ""):
        super().__init__(rate_limit=1.0)
        self.api_key = api_key.strip()

    def _get_client(self) -> httpx.AsyncClient:
        """Override to inject the Bearer token header from self.api_key.

        Always creates a fresh client so that update_api_key() can force a
        reconnect with the new credentials.
        """
        headers = dict(self.default_headers)
        if self.api_key:
            token = self.api_key
            # Match plugin logic: add Bearer if not already present
            if " " not in token:
                token = f"Bearer {token}"
            headers["Authorization"] = token

        # Close any existing client before creating a new one
        if self._client is not None:
            try:
                # Schedule the close but don't block on it
                asyncio.create_task(self._client.aclose())
            except Exception:
                pass

        self._client = httpx.AsyncClient(
            timeout=self.default_timeout,
            headers=headers,
            follow_redirects=self.follow_redirects,
        )
        return self._client

    # client property inherited from BaseSource

    def update_api_key(self, key: str):
        """Force client recreation with new API key on next access."""
        self.api_key = key.strip()
        self._client = None  # Next client access will trigger _get_client()

    async def _query(self, query: str, variables: dict = None) -> dict:
        if not self.api_key:
            return {}
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        try:
            resp = await self.client.post(API, json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"Hardcover HTTP {e.response.status_code}: {e}")
            return {}
        data = resp.json()
        if "errors" in data:
            msgs = [e.get("message", "?") for e in data["errors"]]
            logger.warning(f"Hardcover GraphQL errors: {msgs}")
            return {}
        return data.get("data", {})

    async def search_author(self, author_name: str, owned_titles: list = None) -> Optional[AuthorResult]:
        """Search for author by searching their known books, then finding all books by same author."""
        if not self.api_key:
            return None
        try:
            # Search using author name + known book titles for better results
            search_queries = [author_name]
            if owned_titles:
                for t in owned_titles[:3]:  # Try up to 3 book titles
                    search_queries.append(f"{t} {author_name}")

            all_book_ids = set()
            for sq in search_queries:
                data = await self._query(SEARCH_QUERY, {"query": sq})
                search = data.get("search", {})
                ids = search.get("ids", [])
                for bid in ids[:10]:
                    try:
                        all_book_ids.add(int(bid))
                    except (ValueError, TypeError):
                        pass
                if all_book_ids:
                    break  # Found something, stop searching
            
            if not all_book_ids:
                logger.info(f"  Hardcover: no search results for '{author_name}'")
                return None
            
            book_ids = list(all_book_ids)[:20]
            logger.info(f"  Hardcover: search returned {len(book_ids)} book IDs")
            
            # Step 2: Fetch books by IDs
            books_data = await self._query(FIND_BOOKS_BY_IDS, {
                "ids": book_ids, "languages": ["eng", "en"]
            })
            books = books_data.get("books", [])
            if not books:
                return None
            
            # Extract author info from cached_contributors
            author_id = None
            
            def _check_contributor(c, target_name):
                """Check if a contributor matches our author name."""
                target = target_name.lower().strip()
                target_parts = set(target.replace(".", "").split())
                if isinstance(c, dict):
                    # Try multiple name fields
                    cname = ""
                    if c.get("name"):
                        cname = c["name"]
                    elif isinstance(c.get("author"), dict) and c["author"].get("name"):
                        cname = c["author"]["name"]
                    elif c.get("author_name"):
                        cname = c["author_name"]
                    
                    cn = cname.lower().strip()
                    # Exact match
                    if cn == target:
                        return True, cname, c.get("id") or c.get("author_id")
                    # Match ignoring periods/dots (J. K. Rowling vs JK Rowling)
                    if cn.replace(".", "") == target.replace(".", ""):
                        return True, cname, c.get("id") or c.get("author_id")
                    # All name parts present (handles "James S A Corey" vs "James S. A. Corey")
                    cn_parts = set(cn.replace(".", "").split())
                    if target_parts and cn_parts and target_parts == cn_parts:
                        return True, cname, c.get("id") or c.get("author_id")
                    return False, cname, c.get("id") or c.get("author_id")
                elif isinstance(c, str):
                    cn = c.lower().strip()
                    if cn == target or cn.replace(".", "") == target.replace(".", ""):
                        return True, c, None
                    return False, c, None
                return False, str(c), None
            
            def _parse_contributors(raw):
                """Parse contributors from various formats."""
                if isinstance(raw, list):
                    return raw
                if isinstance(raw, str):
                    try:
                        import json as jn
                        return jn.loads(raw)
                    except:
                        return [{"name": raw}]
                return []
            
            for book in books:
                # Check book-level contributions first
                for contrib in book.get("contributions", []):
                    author_obj = contrib.get("author", {})
                    if isinstance(author_obj, dict):
                        aname = author_obj.get("name", "")
                        matched, _, _ = _check_contributor({"name": aname}, author_name)
                        if matched:
                            author_id = str(author_obj.get("id", "matched"))
                            break
                if author_id:
                    break
                # Fall back to edition contributors
                for edition in book.get("editions", []):
                    contribs = _parse_contributors(edition.get("contributors"))
                    for c in contribs:
                        matched, cname, cid = _check_contributor(c, author_name)
                        if matched:
                            author_id = str(cid) if cid else "matched"
                            break
                if author_id:
                    break
            
            # Build result from found books
            series_map = {}
            standalone = []
            
            for book in books:
                is_by_author = False
                
                # Check 1: Book-level contributions (most reliable)
                for contrib in book.get("contributions", []):
                    author_obj = contrib.get("author", {})
                    if isinstance(author_obj, dict):
                        aname = author_obj.get("name", "")
                        matched, _, _ = _check_contributor({"name": aname}, author_name)
                        if matched:
                            is_by_author = True
                            if not author_id:
                                author_id = str(author_obj.get("id", "matched"))
                            logger.debug(f"  Hardcover: '{book.get('title')}' matched via contributions → '{aname}'")
                            break
                
                # Check 2: Edition-level cached_contributors
                if not is_by_author:
                    for edition in book.get("editions", []):
                        contribs = _parse_contributors(edition.get("contributors"))
                        for c in contribs:
                            matched, cname, _ = _check_contributor(c, author_name)
                            if matched:
                                is_by_author = True
                                logger.debug(f"  Hardcover: '{book.get('title')}' matched via edition contributor → '{cname}'")
                                break
                        if is_by_author:
                            break
                
                if not is_by_author:
                    # Log what we found for diagnosis
                    book_authors = [c.get("author", {}).get("name", "?") for c in book.get("contributions", [])]
                    ed_contribs = []
                    for ed in book.get("editions", []):
                        for c in _parse_contributors(ed.get("contributors")):
                            _, cn, _ = _check_contributor(c, author_name)
                            if cn: ed_contribs.append(cn)
                    all_names = book_authors + ed_contribs
                    logger.info(f"  Hardcover: skipping '{book.get('title')}' — contributors: {all_names[:5] if all_names else '(none)'}")
                    continue
                
                edition = book.get("editions", [{}])[0] if book.get("editions") else {}
                cover = None
                cached_img = edition.get("image")
                if cached_img and isinstance(cached_img, dict):
                    cover = cached_img.get("url")
                elif cached_img and isinstance(cached_img, str):
                    cover = cached_img
                
                slug = book.get("slug", "")
                br = BookResult(
                    title=book.get("title", ""),
                    isbn=edition.get("isbn_13"),
                    cover_url=cover,
                    pub_date=edition.get("release_date"),
                    description=book.get("description"),
                    external_id=str(book.get("id")),
                    source="hardcover",
                    source_url=f"https://hardcover.app/books/{slug}" if slug else None,
                )
                
                # Check series info: try book_series relation first, then cached_featured_series
                sname = None; spos = None
                bs = book.get("book_series")
                if bs and isinstance(bs, list) and len(bs) > 0:
                    # Pick the most specific series (avoid broad franchise catch-alls)
                    candidates = []
                    for bse in bs:
                        if isinstance(bse, dict):
                            sr_obj = bse.get("series", {})
                            if isinstance(sr_obj, dict) and sr_obj.get("name"):
                                candidates.append({"name": sr_obj["name"], "position": bse.get("position"), "id": sr_obj.get("id")})
                    if candidates:
                        # Score: prefer specific sub-series, penalize parenthetical variants
                        def _score(c):
                            s = 0
                            name = c["name"]
                            if ":" in name: s += 10  # sub-series like "Star Wars: Empire and Rebellion"
                            if c["position"] is not None: s += 5
                            if "(" in name: s -= 3  # penalize "(Chronological)", "(Publication Order)" etc.
                            s += min(len(name.split()), 5)  # more words = more specific, cap at 5
                            return s
                        candidates.sort(key=_score, reverse=True)
                        best = candidates[0]
                        sname = best["name"]
                        spos = best["position"]
                        if len(candidates) > 1:
                            logger.debug(f"  Hardcover: '{book.get('title')}' has {len(candidates)} series: {[c['name'] for c in candidates]} → picked '{sname}'")
                        else:
                            logger.debug(f"  Hardcover: '{book.get('title')}' series from book_series → '{sname}' #{spos}")
                if not sname:
                    series = book.get("series")
                    if series and isinstance(series, list) and len(series) > 0:
                        s = series[0]
                        if isinstance(s, dict) and s.get("name"):
                            sname = s["name"]
                            spos = s.get("position")
                            logger.debug(f"  Hardcover: '{book.get('title')}' series from cached → '{sname}' #{spos}")
                
                if sname:
                    br.series_name = sname
                    br.series_index = spos
                    if sname not in series_map:
                        series_map[sname] = SeriesResult(name=sname, books=[])
                    series_map[sname].books.append(br)
                    continue
                
                standalone.append(br)
            
            total = len(standalone) + sum(len(s.books) for s in series_map.values())
            logger.info(f"  Hardcover: found {total} books by '{author_name}' ({len(series_map)} series)")
            
            return AuthorResult(
                name=author_name,
                external_id=author_id or "search",
                books=standalone,
                series=list(series_map.values()),
            )
            
        except Exception as e:
            logger.error(f"Hardcover error for '{author_name}': {e}")
            return None

    async def get_author_books(self, author_id: str) -> Optional[AuthorResult]:
        """For Hardcover, search_author already returns full results."""
        return None  # Already handled in search_author
