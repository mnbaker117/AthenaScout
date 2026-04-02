from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BookResult:
    title: str
    series_name: Optional[str] = None
    series_index: Optional[float] = None
    isbn: Optional[str] = None
    cover_url: Optional[str] = None
    pub_date: Optional[str] = None
    expected_date: Optional[str] = None
    is_unreleased: bool = False
    description: Optional[str] = None
    page_count: Optional[int] = None
    external_id: Optional[str] = None
    language: Optional[str] = None
    source: str = ""
    source_url: Optional[str] = None


@dataclass
class SeriesResult:
    name: str
    total_books: Optional[int] = None
    description: Optional[str] = None
    external_id: Optional[str] = None
    books: list[BookResult] = field(default_factory=list)


@dataclass
class AuthorResult:
    name: str
    bio: Optional[str] = None
    image_url: Optional[str] = None
    external_id: Optional[str] = None
    books: list[BookResult] = field(default_factory=list)
    series: list[SeriesResult] = field(default_factory=list)


class BaseSource:
    name: str = "base"

    async def search_author(self, author_name: str) -> Optional[AuthorResult]:
        raise NotImplementedError

    async def get_author_books(self, author_id: str) -> Optional[AuthorResult]:
        raise NotImplementedError
