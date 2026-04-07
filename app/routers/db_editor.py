"""
Database browser and row editor endpoints for AthenaScout.

Holds /api/db/tables, /api/db/table/{table_name}/schema,
/api/db/table/{table_name} (list/update/add), and row delete.
"""
import logging
import sqlite3
from fastapi import APIRouter, Body, HTTPException, Query

from app.database import get_db

logger = logging.getLogger("athenascout")

router = APIRouter(prefix="/api/db", tags=["db_editor"])


# ─── Editor constants (only used by this router) ───────────
DB_TABLES = {"books", "authors", "series", "sync_log", "mam_scan_log"}

DB_FK_RESOLVERS = {
    "books": {
        "author_id": {
            "table": "authors",
            "name_col": "name",
            "create_cols": {
                "sort_name": lambda name: ", ".join(reversed(name.split(" ", 1))) if " " in name else name
            },
        },
        "series_id": {"table": "series", "name_col": "name"},
    }
}


# ─── FK resolver helper ────────────────────────────────────
async def _resolve_fk_value(db, table_name, col_name, value, row_context=None):
    """Resolve a FK value that might be a name string instead of an integer ID.

    Returns (resolved_int, error_string_or_None).
    - If value is already a valid int → return it directly
    - If value is a string → look up by name in the referenced table
    - If not found → create a new entry and return the new ID
    """
    # Already an integer?
    try:
        return int(value), None
    except (ValueError, TypeError):
        pass

    # Not a number — try name resolution
    resolvers = DB_FK_RESOLVERS.get(table_name, {})
    resolver = resolvers.get(col_name)
    if not resolver:
        return None, f"Expected INTEGER for '{col_name}', got '{value}'"

    ref_table = resolver["table"]
    name_col = resolver["name_col"]
    name_str = str(value).strip()
    if not name_str:
        return None, None  # Empty → NULL

    # Look up by exact name (case-insensitive)
    row = await (await db.execute(
        f"SELECT id FROM [{ref_table}] WHERE LOWER([{name_col}]) = LOWER(?)", (name_str,)
    )).fetchone()

    if row:
        logger.info(f"DB editor: resolved '{name_str}' → {ref_table}.id={row[0]}")
        return row[0], None

    # Not found — create a new entry
    create_cols = resolver.get("create_cols", {})
    insert_cols = [f"[{name_col}]"]
    insert_vals = [name_str]
    for extra_col, gen_fn in create_cols.items():
        insert_cols.append(f"[{extra_col}]")
        insert_vals.append(gen_fn(name_str) if callable(gen_fn) else gen_fn)

    # For series, we need an author_id — get it from the row being edited
    if ref_table == "series" and row_context:
        author_id = row_context.get("author_id")
        if author_id:
            insert_cols.append("[author_id]")
            insert_vals.append(int(author_id))
        else:
            return None, f"Cannot create new series '{name_str}' without an author_id in the same row"

    placeholders = ",".join(["?"] * len(insert_cols))
    try:
        cursor = await db.execute(
            f"INSERT INTO [{ref_table}] ({','.join(insert_cols)}) VALUES ({placeholders})",
            insert_vals
        )
        new_id = cursor.lastrowid
        logger.info(f"DB editor: created new {ref_table} entry '{name_str}' → id={new_id}")
        return new_id, None
    except Exception as e:
        return None, f"Failed to create {ref_table} entry '{name_str}': {e}"


# ─── Routes ────────────────────────────────────────────────
@router.get("/tables")
async def db_list_tables():
    """List all browsable tables in the active library database."""
    db = await get_db()
    try:
        rows = await db.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = [r[0] for r in rows if r[0] in DB_TABLES]
        return {"tables": sorted(tables)}
    finally:
        await db.close()


@router.get("/table/{table_name}/schema")
async def db_table_schema(table_name: str):
    """Get column definitions for a table using PRAGMA table_info."""
    if table_name not in DB_TABLES:
        raise HTTPException(400, f"Table '{table_name}' is not accessible. Allowed: {sorted(DB_TABLES)}")
    db = await get_db()
    try:
        cols = await db.execute_fetchall(f"PRAGMA table_info({table_name})")
        count_row = await (await db.execute(f"SELECT COUNT(*) FROM [{table_name}]")).fetchone()
        row_count = count_row[0] if count_row else 0
        return {
            "table": table_name,
            "columns": [
                {
                    "name": c[1],
                    "type": c[2] or "TEXT",
                    "notnull": bool(c[3]),
                    "default": c[4],
                    "pk": bool(c[5]),
                }
                for c in cols
            ],
            "row_count": row_count,
        }
    finally:
        await db.close()


@router.get("/table/{table_name}")
async def db_table_rows(
    table_name: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    sort: str = Query("id"),
    sort_dir: str = Query("asc"),
    search: str = Query(""),
):
    """Get paginated rows from a table with optional sorting and search."""
    if table_name not in DB_TABLES:
        raise HTTPException(400, f"Table '{table_name}' is not accessible. Allowed: {sorted(DB_TABLES)}")
    db = await get_db()
    try:
        # Get column info for search and sort validation
        cols = await db.execute_fetchall(f"PRAGMA table_info({table_name})")
        col_names = [c[1] for c in cols]
        col_types = {c[1]: (c[2] or "TEXT").upper() for c in cols}

        # Validate sort column
        sort_col = sort if sort in col_names else "id" if "id" in col_names else col_names[0]
        direction = "DESC" if sort_dir.lower() == "desc" else "ASC"

        # Build search filter (search across all TEXT-like columns)
        where = "1=1"
        params = []
        if search.strip():
            text_cols = [c for c in col_names if col_types[c] in ("TEXT", "")]
            if text_cols:
                clauses = [f"[{c}] LIKE ?" for c in text_cols]
                where = f"({' OR '.join(clauses)})"
                params = [f"%{search.strip()}%"] * len(text_cols)

        # Count total matching rows
        count_row = await (await db.execute(
            f"SELECT COUNT(*) FROM [{table_name}] WHERE {where}", params
        )).fetchone()
        total = count_row[0] if count_row else 0

        # Fetch page
        offset = (page - 1) * per_page
        rows = await db.execute_fetchall(
            f"SELECT * FROM [{table_name}] WHERE {where} ORDER BY [{sort_col}] {direction} LIMIT ? OFFSET ?",
            params + [per_page, offset]
        )

        # Convert rows to dicts
        row_dicts = []
        for row in rows:
            d = {}
            for i, col in enumerate(col_names):
                d[col] = row[i]
            row_dicts.append(d)

        return {
            "rows": row_dicts,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
        }
    finally:
        await db.close()


@router.post("/table/{table_name}/update")
async def db_table_update(table_name: str, body: dict = Body(...)):
    """Batch update cells in a table. All changes applied in a single transaction.

    Body: {"edits": {"row_id": {"col": value, ...}, ...}}
    Validates types against PRAGMA table_info before applying.
    """
    if table_name not in DB_TABLES:
        raise HTTPException(400, f"Table '{table_name}' is not accessible")
    edits = body.get("edits", {})
    if not edits:
        return {"status": "ok", "updated": 0}

    db = await get_db()
    try:
        # Get column metadata for validation
        cols = await db.execute_fetchall(f"PRAGMA table_info({table_name})")
        col_meta = {}
        pk_col = None
        for c in cols:
            col_meta[c[1]] = {
                "type": (c[2] or "TEXT").upper(),
                "notnull": bool(c[3]),
                "pk": bool(c[5]),
            }
            if c[5]:
                pk_col = c[1]

        # Validate all edits first
        errors = []
        for row_id, changes in edits.items():
            for col, val in changes.items():
                if col not in col_meta:
                    errors.append({"row": row_id, "column": col, "value": str(val), "error": f"Unknown column '{col}'"})
                    continue
                meta = col_meta[col]
                if meta["pk"]:
                    errors.append({"row": row_id, "column": col, "value": str(val), "error": "Cannot edit primary key"})
                    continue
                # Null check
                if (val is None or val == "") and meta["notnull"]:
                    errors.append({"row": row_id, "column": col, "value": str(val), "error": f"Column '{col}' cannot be NULL"})
                    continue
                # Type check (only if not null/empty)
                if val is not None and val != "":
                    col_type = meta["type"]
                    if "INTEGER" in col_type:
                        # Check if this is a FK column that supports name resolution
                        fk_resolvers = DB_FK_RESOLVERS.get(table_name, {})
                        if col in fk_resolvers:
                            # Will resolve during apply phase — skip strict int check
                            try:
                                int(val)
                            except (ValueError, TypeError):
                                pass  # Non-integer is OK for FK columns — will resolve by name
                        else:
                            try:
                                int(val)
                            except (ValueError, TypeError):
                                errors.append({"row": row_id, "column": col, "value": str(val), "error": f"Expected INTEGER, got '{val}'"})
                    elif "REAL" in col_type:
                        try:
                            float(val)
                        except (ValueError, TypeError):
                            errors.append({"row": row_id, "column": col, "value": str(val), "error": f"Expected REAL number, got '{val}'"})

        if errors:
            return {"status": "error", "errors": errors}

        # Apply all edits in a transaction (with FK resolution)
        updated = 0
        for row_id, changes in edits.items():
            set_parts = []
            params = []
            # Build row context for FK resolution (e.g., series needs author_id)
            row_context = dict(changes)
            # Also fetch current row values for context
            pk = pk_col or "id"
            try:
                existing = await (await db.execute(
                    f"SELECT * FROM [{table_name}] WHERE [{pk}] = ?", (int(row_id),)
                )).fetchone()
                if existing:
                    col_names_list = [c[1] for c in cols]
                    for i, cn in enumerate(col_names_list):
                        if cn not in row_context:
                            row_context[cn] = existing[i]
            except Exception:
                pass

            for col, val in changes.items():
                if col_meta[col]["pk"]:
                    continue
                set_parts.append(f"[{col}] = ?")
                # Convert types
                if val is None or val == "":
                    params.append(None)
                elif "INTEGER" in col_meta[col]["type"]:
                    # Try FK resolution for supported columns
                    fk_resolvers = DB_FK_RESOLVERS.get(table_name, {})
                    if col in fk_resolvers:
                        resolved, err = await _resolve_fk_value(db, table_name, col, val, row_context)
                        if err:
                            errors.append({"row": row_id, "column": col, "value": str(val), "error": err})
                            continue
                        params.append(resolved)
                    else:
                        params.append(int(val))
                elif "REAL" in col_meta[col]["type"]:
                    params.append(float(val))
                else:
                    params.append(str(val))
            if set_parts:
                pk = pk_col or "id"
                params.append(int(row_id))
                await db.execute(
                    f"UPDATE [{table_name}] SET {', '.join(set_parts)} WHERE [{pk}] = ?",
                    params
                )
                updated += 1
        if errors:
            return {"status": "error", "errors": errors}
        await db.commit()
        logger.info(f"DB editor: updated {updated} rows in {table_name}")
        return {"status": "ok", "updated": updated}
    except Exception as e:
        logger.error(f"DB editor update error: {e}")
        raise HTTPException(500, str(e))
    finally:
        await db.close()


@router.post("/table/{table_name}/add")
async def db_table_add_row(table_name: str, body: dict = Body(...)):
    """Add a new row to a table.

    Body: {"values": {"col": value, ...}}
    Only includes columns with non-empty values.
    """
    if table_name not in DB_TABLES:
        raise HTTPException(400, f"Table '{table_name}' is not accessible")
    values = body.get("values", {})
    if not values:
        raise HTTPException(400, "No values provided")

    db = await get_db()
    try:
        # Get column metadata
        cols = await db.execute_fetchall(f"PRAGMA table_info({table_name})")
        col_meta = {c[1]: {"type": (c[2] or "TEXT").upper(), "notnull": bool(c[3]), "pk": bool(c[5])} for c in cols}

        # Filter to valid columns, skip PK (auto-increment)
        insert_cols = []
        insert_vals = []
        for col, val in values.items():
            if col not in col_meta or col_meta[col]["pk"]:
                continue
            if val is None or val == "":
                if col_meta[col]["notnull"]:
                    raise HTTPException(400, f"Column '{col}' cannot be NULL")
                insert_cols.append(f"[{col}]")
                insert_vals.append(None)
            else:
                col_type = col_meta[col]["type"]
                try:
                    if "INTEGER" in col_type:
                        fk_resolvers = DB_FK_RESOLVERS.get(table_name, {})
                        if col in fk_resolvers:
                            resolved, err = await _resolve_fk_value(db, table_name, col, val, values)
                            if err:
                                raise HTTPException(400, f"FK resolution error for {col}: {err}")
                            insert_vals.append(resolved)
                        else:
                            insert_vals.append(int(val))
                    elif "REAL" in col_type:
                        insert_vals.append(float(val))
                    else:
                        insert_vals.append(str(val))
                    insert_cols.append(f"[{col}]")
                except HTTPException:
                    raise
                except (ValueError, TypeError):
                    raise HTTPException(400, f"Invalid value for {col} ({col_type}): {val}")

        if not insert_cols:
            raise HTTPException(400, "No valid columns to insert")

        placeholders = ",".join(["?"] * len(insert_cols))
        cursor = await db.execute(
            f"INSERT INTO [{table_name}] ({','.join(insert_cols)}) VALUES ({placeholders})",
            insert_vals
        )
        await db.commit()
        new_id = cursor.lastrowid
        logger.info(f"DB editor: added row {new_id} to {table_name}")
        return {"status": "ok", "id": new_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"DB editor add error: {e}")
        raise HTTPException(500, str(e))
    finally:
        await db.close()


@router.delete("/table/{table_name}/row/{row_id}")
async def db_table_delete_row(table_name: str, row_id: int):
    """Delete a row by primary key."""
    if table_name not in DB_TABLES:
        raise HTTPException(400, f"Table '{table_name}' is not accessible")
    db = await get_db()
    try:
        # Find PK column
        cols = await db.execute_fetchall(f"PRAGMA table_info({table_name})")
        pk_col = next((c[1] for c in cols if c[5]), "id")

        # Verify row exists
        row = await (await db.execute(
            f"SELECT [{pk_col}] FROM [{table_name}] WHERE [{pk_col}] = ?", (row_id,)
        )).fetchone()
        if not row:
            raise HTTPException(404, f"Row {row_id} not found in {table_name}")

        try:
            await db.execute(f"DELETE FROM [{table_name}] WHERE [{pk_col}] = ?", (row_id,))
            await db.commit()
        except sqlite3.IntegrityError as e:
            # Most commonly: foreign key constraint (child rows reference this row)
            msg = str(e)
            if "FOREIGN KEY" in msg.upper():
                hint = "This row is referenced by other records. Delete or reassign those first."
                if table_name == "authors":
                    hint = "This author still has books in the books table. Delete or reassign their books first."
                elif table_name == "series":
                    hint = "This series still has books referencing it. Delete or reassign those books first."
                raise HTTPException(409, f"Cannot delete: {hint}")
            raise HTTPException(409, f"Cannot delete: {msg}")
        logger.info(f"DB editor: deleted row {row_id} from {table_name}")
        return {"status": "ok"}
    finally:
        await db.close()
