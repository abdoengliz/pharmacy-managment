from __future__ import annotations

from typing import Any


def table_exists(db: Any, table_name: str) -> bool:
    row = db.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=?) AS exists",
        (table_name,),
    ).fetchone()
    return bool(row[0]) if row else False


def table_columns(db: Any, table_name: str) -> set[str]:
    rows = db.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name=? ORDER BY ordinal_position",
        (table_name,),
    ).fetchall()
    return {str(row[0]) for row in rows}


def database_health(db: Any) -> str:
    return str(db.execute("SELECT 'ok'").fetchone()[0])
