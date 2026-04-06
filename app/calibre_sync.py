"""
Calibre Sync — reads Calibre's metadata.db and imports authors, series, books.
Fixed: ISBN from identifiers table, series_index from books table, proper delimiters.
"""
import sqlite3
import time
import logging
import os
from pathlib import Path
from app.config import CALIBRE_DB_PATH, CALIBRE_LIBRARY_PATH
from app.database import get_db

logger = logging.getLogger("athenascout.calibre_sync")

# Use a delimiter that won't appear in author/series names
SEP = "|||"
FIELD_SEP = ":::"


def _read_calibre_db(calibre_path: str, library_path: str = None) -> dict:
    """Read Calibre metadata.db (synchronous, read-only).

    Args:
        calibre_path: Path to metadata.db
        library_path: Path to the Calibre library root (for cover images).
                      If None, falls back to CALIBRE_LIBRARY_PATH.
    """
    lib_path = library_path or CALIBRE_LIBRARY_PATH
    if not Path(calibre_path).exists():
        raise FileNotFoundError(f"Calibre database not found at {calibre_path}")

    conn = sqlite3.connect(f"file:{calibre_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        books_raw = conn.execute("""
            SELECT
                b.id as book_id,
                b.title,
                b.pubdate,
                b.series_index,
                b.path as book_path,
                COALESCE(c.text, '') as comments
            FROM books b
            LEFT JOIN comments c ON c.book = b.id
        """).fetchall()

        result = []
        for bk in books_raw:
            book_id = bk["book_id"]

            # Get authors
            authors = conn.execute("""
                SELECT a.id, a.name, a.sort
                FROM books_authors_link bal
                JOIN authors a ON bal.author = a.id
                WHERE bal.book = ?
            """, (book_id,)).fetchall()

            # Get series
            series_list = conn.execute("""
                SELECT s.id, s.name
                FROM books_series_link bsl
                JOIN series s ON bsl.series = s.id
                WHERE bsl.book = ?
            """, (book_id,)).fetchall()

            # Get ISBN
            isbn_row = conn.execute("""
                SELECT val FROM identifiers
                WHERE book = ? AND type IN ('isbn', 'isbn13', 'isbn10')
                LIMIT 1
            """, (book_id,)).fetchone()

            # Get tags
            tags = conn.execute("""
                SELECT t.name FROM books_tags_link btl
                JOIN tags t ON btl.tag = t.id
                WHERE btl.book = ?
            """, (book_id,)).fetchall()

            # Get rating (Calibre stores 0-10, we want 0-5)
            rating_row = conn.execute("""
                SELECT r.rating FROM books_ratings_link brl
                JOIN ratings r ON brl.rating = r.id
                WHERE brl.book = ?
            """, (book_id,)).fetchone()

            # Get languages
            langs = conn.execute("""
                SELECT l.lang_code FROM books_languages_link bll
                JOIN languages l ON bll.lang_code = l.id
                WHERE bll.book = ?
            """, (book_id,)).fetchall()

            # Get publisher
            pub_row = conn.execute("""
                SELECT p.name FROM books_publishers_link bpl
                JOIN publishers p ON bpl.publisher = p.id
                WHERE bpl.book = ?
            """, (book_id,)).fetchone()

            # Get formats
            formats = conn.execute("""
                SELECT format FROM data WHERE book = ?
            """, (book_id,)).fetchall()

            # Build cover path
            cover_path = None
            if bk["book_path"]:
                candidate = os.path.join(lib_path, bk["book_path"], "cover.jpg")
                if os.path.exists(candidate):
                    cover_path = candidate

            # Clean description (strip HTML tags)
            desc = bk["comments"] or ""
            if desc:
                import re as _re
                desc = _re.sub(r'<[^>]+>', '', desc).strip()
                if len(desc) > 1000:
                    desc = desc[:1000]

            result.append({
                "book_id": book_id,
                "title": bk["title"],
                "pubdate": bk["pubdate"],
                "series_index": bk["series_index"],
                "book_path": bk["book_path"],
                "cover_path": cover_path,
                "isbn": isbn_row["val"] if isbn_row else None,
                "authors": [{"id": a["id"], "name": a["name"], "sort": a["sort"]} for a in authors],
                "series": [{"id": s["id"], "name": s["name"]} for s in series_list],
                "tags": ", ".join(t["name"] for t in tags) if tags else None,
                "rating": (rating_row["rating"] / 2.0) if rating_row and rating_row["rating"] else None,
                "description": desc if desc else None,
                "language": langs[0]["lang_code"] if langs else None,
                "publisher": pub_row["name"] if pub_row else None,
                "formats": ", ".join(f["format"] for f in formats) if formats else None,
            })

        return {"books": result}
    finally:
        conn.close()


async def sync_calibre(calibre_db_path=None, calibre_library_path=None):
    """Main sync — imports Calibre data into our database.

    Args:
        calibre_db_path: Path to metadata.db. If None, uses CALIBRE_DB_PATH from config.
        calibre_library_path: Path to library root. If None, uses CALIBRE_LIBRARY_PATH from config.
    """
    cal_path = calibre_db_path or CALIBRE_DB_PATH
    lib_path = calibre_library_path or CALIBRE_LIBRARY_PATH
    logger.info(f"Starting Calibre sync from {cal_path}...")
    start_time = time.time()

    db = await get_db()
    try:
        cursor = await db.execute(
            "INSERT INTO sync_log (sync_type, started_at) VALUES (?, ?)",
            ("calibre", start_time)
        )
        sync_id = cursor.lastrowid
        await db.commit()

        calibre_data = _read_calibre_db(cal_path, lib_path)
        books_found = 0
        books_new = 0

        # Pass 1: upsert authors
        author_map = {}  # calibre_author_id -> our_id
        for book in calibre_data["books"]:
            for author in book["authors"]:
                cal_id = author["id"]
                if cal_id in author_map:
                    continue

                row = await (await db.execute(
                    "SELECT id FROM authors WHERE name = ?", (author["name"],)
                )).fetchone()
                if row:
                    author_map[cal_id] = row["id"]
                    await db.execute(
                        "UPDATE authors SET calibre_id = ?, sort_name = ? WHERE id = ?",
                        (cal_id, author["sort"], row["id"])
                    )
                else:
                    cur = await db.execute(
                        "INSERT INTO authors (name, sort_name, calibre_id) VALUES (?, ?, ?)",
                        (author["name"], author["sort"], cal_id)
                    )
                    author_map[cal_id] = cur.lastrowid

        # Pass 2: upsert series
        series_map = {}  # (calibre_series_id, our_author_id) -> our_id
        for book in calibre_data["books"]:
            if not book["series"] or not book["authors"]:
                continue
            primary_author_cal_id = book["authors"][0]["id"]
            our_author_id = author_map.get(primary_author_cal_id)
            if not our_author_id:
                continue

            for s in book["series"]:
                key = (s["id"], our_author_id)
                if key in series_map:
                    continue

                # Look for series globally (supports multi-author series)
                row = await (await db.execute(
                    "SELECT id FROM series WHERE LOWER(name) = LOWER(?)",
                    (s["name"],)
                )).fetchone()
                if row:
                    series_map[key] = row["id"]
                else:
                    cur = await db.execute(
                        "INSERT INTO series (name, author_id) VALUES (?, ?)",
                        (s["name"], our_author_id)
                    )
                    series_map[key] = cur.lastrowid

        # Pass 3: upsert books
        for book in calibre_data["books"]:
            if not book["authors"]:
                continue
            books_found += 1

            primary_cal_id = book["authors"][0]["id"]
            our_author_id = author_map.get(primary_cal_id)
            if not our_author_id:
                continue

            our_series_id = None
            series_index = book["series_index"]
            if book["series"]:
                first_series = book["series"][0]
                key = (first_series["id"], our_author_id)
                our_series_id = series_map.get(key)

            row = await (await db.execute(
                "SELECT id FROM books WHERE calibre_id = ? AND source = 'calibre'",
                (book["book_id"],)
            )).fetchone()

            if row:
                await db.execute("""
                    UPDATE books SET title=?, author_id=?, series_id=?,
                    series_index=?, isbn=?, owned=1, cover_path=?,
                    description=COALESCE(?,description), tags=?, rating=?,
                    language=?, publisher=?, formats=?
                    WHERE id=?
                """, (book["title"], our_author_id, our_series_id,
                      series_index, book["isbn"], book["cover_path"],
                      book["description"], book["tags"], book["rating"],
                      book["language"], book["publisher"], book["formats"],
                      row["id"]))
                logger.debug(f"  Calibre: updated '{book['title']}' (calibre_id={book['book_id']}, tags={book['tags']}, rating={book['rating']})")
            else:
                await db.execute("""
                    INSERT INTO books (title, author_id, series_id, series_index,
                    isbn, calibre_id, source, owned, pub_date, cover_path,
                    description, tags, rating, language, publisher, formats)
                    VALUES (?, ?, ?, ?, ?, ?, 'calibre', 1, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (book["title"], our_author_id, our_series_id,
                      series_index, book["isbn"], book["book_id"],
                      book["pubdate"], book["cover_path"],
                      book["description"], book["tags"], book["rating"],
                      book["language"], book["publisher"], book["formats"]))
                books_new += 1
                logger.debug(f"  Calibre: NEW '{book['title']}' by {book['authors'][0]['name']} (tags={book['tags']}, lang={book['language']})")

            # Match external books by title
            await db.execute("""
                UPDATE books SET owned = 1
                WHERE author_id = ? AND owned = 0 AND source != 'calibre'
                AND (
                    LOWER(TRIM(title)) = LOWER(TRIM(?))
                    OR REPLACE(LOWER(TRIM(title)), 'the ', '') =
                       REPLACE(LOWER(TRIM(?)), 'the ', '')
                )
            """, (our_author_id, book["title"], book["title"]))

        await db.commit()

        await db.execute("""
            UPDATE sync_log SET finished_at=?, status='complete',
            books_found=?, books_new=? WHERE id=?
        """, (time.time(), books_found, books_new, sync_id))
        await db.commit()

        logger.info(f"Calibre sync complete: {books_found} books, {books_new} new")
        return {"books_found": books_found, "books_new": books_new}

    except Exception as e:
        logger.error(f"Calibre sync error: {e}", exc_info=True)
        await db.execute(
            "UPDATE sync_log SET finished_at=?, status='error', error=? WHERE id=?",
            (time.time(), str(e), sync_id)
        )
        await db.commit()
        raise
    finally:
        await db.close()
