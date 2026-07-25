from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any


class CompatRow(Mapping[str, Any], Sequence[Any]):
    """Row object supporting both row[0] and row['column'], like sqlite3.Row."""

    def __init__(self, columns: list[str], values: tuple[Any, ...]):
        self._columns = columns
        self._values = values
        self._mapping = dict(zip(columns, values))

    def __getitem__(self, key: int | slice | str) -> Any:
        if isinstance(key, (int, slice)):
            return self._values[key]
        return self._mapping[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._values)

    def keys(self):
        return self._mapping.keys()


class PostgresCursorCompat:
    def __init__(self, connection: "PostgresConnectionCompat", cursor: Any):
        self._connection = connection
        self._cursor = cursor
        self._lastrowid: int | None = None

    @property
    def description(self):
        return self._cursor.description

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    @property
    def lastrowid(self) -> int | None:
        """ID returned by the same INSERT statement.

        PostgreSQL has no SQLite-style connection-safe lastrowid.  The
        compatibility connection appends ``RETURNING id`` to eligible INSERTs
        and stores that exact value here.  We intentionally do not use
        LASTVAL(), which is unsafe with pooled/serverless connections.
        """
        return self._lastrowid

    def _convert(self, row: tuple[Any, ...] | None) -> CompatRow | None:
        if row is None:
            return None
        columns = [item.name if hasattr(item, "name") else item[0] for item in self._cursor.description or []]
        return CompatRow(columns, tuple(row))

    def fetchone(self) -> CompatRow | None:
        return self._convert(self._cursor.fetchone())

    def fetchall(self) -> list[CompatRow]:
        return [self._convert(row) for row in self._cursor.fetchall()]  # type: ignore[list-item]

    def __iter__(self):
        for row in self._cursor:
            yield self._convert(row)


def _extract_insert_target(sql: str) -> tuple[str, str] | None:
    """Return ``(schema, table)`` for a simple INSERT target."""
    match = re.match(
        r'\s*INSERT\s+INTO\s+(?:(?P<schema>"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)\s*\.\s*)?'
        r'(?P<table>"[^"]+"|[A-Za-z_][A-Za-z0-9_$]*)',
        sql,
        flags=re.I,
    )
    if not match:
        return None

    def unquote(identifier: str | None, default: str) -> str:
        if not identifier:
            return default
        if identifier.startswith('"') and identifier.endswith('"'):
            return identifier[1:-1].replace('""', '"')
        return identifier.lower()

    return unquote(match.group('schema'), 'public'), unquote(match.group('table'), '')


def _can_append_returning(sql: str) -> bool:
    """Whether an INSERT can safely receive an automatic RETURNING clause."""
    stripped = sql.strip().rstrip(';')
    upper = stripped.upper()
    if not upper.startswith('INSERT INTO '):
        return False
    if ' RETURNING ' in f' {upper} ':
        return False
    if re.search(r'\bINSERT\s+INTO\b.+?\bSELECT\b', stripped, flags=re.I | re.S):
        return False
    return True


class PostgresConnectionCompat:
    def __init__(self, raw: Any):
        self._raw = raw
        self._table_has_id_cache: dict[tuple[str, str], bool] = {}

    def _table_has_id(self, schema: str, table: str) -> bool:
        key = (schema, table)
        if key in self._table_has_id_cache:
            return self._table_has_id_cache[key]

        probe = self._raw.cursor()
        probe.execute(
            "SELECT EXISTS ("
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s AND column_name='id'"
            ")",
            (schema, table),
        )
        row = probe.fetchone()
        has_id = bool(row and row[0])
        self._table_has_id_cache[key] = has_id
        return has_id

    def _prepare_returning_id(self, sql: str) -> tuple[str, bool]:
        if not _can_append_returning(sql):
            return sql, False
        target = _extract_insert_target(sql)
        if target is None or not self._table_has_id(*target):
            return sql, False
        return sql.strip().rstrip(';') + ' RETURNING id', True

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> PostgresCursorCompat:
        translated = translate_sql(sql)

        if re.match(r"\s*(?:CREATE|ALTER|DROP)\s+(?:TABLE|VIEW)\b", translated, flags=re.I):
            self._table_has_id_cache.clear()

        try:
            translated, captures_id = self._prepare_returning_id(translated)
        except Exception as exc:
            try:
                self._raw.rollback()
            except Exception:
                pass
            try:
                import psycopg
                if isinstance(exc, psycopg.Error):
                    raise sqlite3.Error(str(exc)) from exc
            except ImportError:
                pass
            raise

        cur = self._raw.cursor()
        try:
            cur.execute(translated, params)
        except Exception as exc:
            # PostgreSQL marks the entire transaction as failed after one bad
            # statement. Legacy SQLite routes often catch optional-query errors
            # and continue, so clear the failed transaction before translating
            # the exception. Without this rollback, every later query fails with
            # InFailedSqlTransaction and hides the original, recoverable error.
            try:
                self._raw.rollback()
            except Exception:
                pass

            # Keep the existing route error handling useful while running on PostgreSQL.
            try:
                import psycopg
                if isinstance(exc, psycopg.IntegrityError):
                    raise sqlite3.IntegrityError(str(exc)) from exc
                if isinstance(exc, psycopg.Error):
                    raise sqlite3.Error(str(exc)) from exc
            except ImportError:
                pass
            raise
        compat = PostgresCursorCompat(self, cur)
        if captures_id:
            row = cur.fetchone()
            compat._lastrowid = int(row[0]) if row else None
        return compat

    def execute_insert(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] = (),
        *,
        returning: str = "id",
    ) -> int:
        """Execute one INSERT and return the requested column from that row."""
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', returning):
            raise ValueError("Invalid RETURNING column")
        translated = translate_sql(sql).strip().rstrip(';')
        if not re.match(r"\s*INSERT\s+INTO\b", translated, flags=re.I):
            raise ValueError("execute_insert accepts INSERT statements only")
        if re.search(r"\bRETURNING\b", translated, flags=re.I):
            raise ValueError("INSERT already contains RETURNING")
        cur = self._raw.cursor()
        try:
            cur.execute(f'{translated} RETURNING {returning}', params)
            row = cur.fetchone()
            if row is None:
                raise sqlite3.Error("INSERT did not return a generated key")
            return int(row[0])
        except Exception as exc:
            try:
                self._raw.rollback()
            except Exception:
                pass
            try:
                import psycopg
                if isinstance(exc, psycopg.IntegrityError):
                    raise sqlite3.IntegrityError(str(exc)) from exc
                if isinstance(exc, psycopg.Error):
                    raise sqlite3.Error(str(exc)) from exc
            except ImportError:
                pass
            raise

    def executemany(self, sql: str, seq_of_params: Any) -> PostgresCursorCompat:
        """SQLite-compatible batch execution for PostgreSQL.

        Translates the legacy SQL once, executes every parameter set using
        psycopg's executemany(), and restores the connection after an error so
        later requests are not left in an aborted transaction.
        """
        translated = translate_sql(sql)
        cur = self._raw.cursor()
        try:
            cur.executemany(translated, seq_of_params)
        except Exception as exc:
            try:
                self._raw.rollback()
            except Exception:
                pass

            try:
                import psycopg
                if isinstance(exc, psycopg.IntegrityError):
                    raise sqlite3.IntegrityError(str(exc)) from exc
                if isinstance(exc, psycopg.Error):
                    raise sqlite3.Error(str(exc)) from exc
            except ImportError:
                pass
            raise
        return PostgresCursorCompat(self, cur)

    def executescript(self, script: str) -> None:
        for statement in split_sql_script(script):
            translated = translate_ddl(statement)
            if translated.strip():
                self.execute(translated)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()

    def cursor(self):
        return self._raw.cursor()


_QMARK_RE = re.compile(r"\?")


def _replace_qmarks(sql: str) -> str:
    # Project SQL does not use literal question marks in SQL string literals.
    return _QMARK_RE.sub("%s", sql)


def translate_sql(sql: str) -> str:
    stripped = sql.strip()
    upper = stripped.upper()

    # DDL is executed through db.execute() throughout the legacy application,
    # not only through executescript(). Translate SQLite identity syntax here
    # so every CREATE TABLE statement is PostgreSQL-safe.
    sql = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
        sql,
        flags=re.I,
    )
    sql = re.sub(r"\bAUTOINCREMENT\b", "", sql, flags=re.I)
    stripped = sql.strip()
    upper = stripped.upper()

    if upper.startswith(("PRAGMA JOURNAL_MODE", "PRAGMA BUSY_TIMEOUT", "PRAGMA SYNCHRONOUS", "PRAGMA FOREIGN_KEYS")):
        return "SELECT 1"
    if upper.startswith("PRAGMA QUICK_CHECK"):
        return "SELECT 'ok' AS quick_check"

    match = re.match(r"PRAGMA\s+table_info\(([^)]+)\)", stripped, flags=re.I)
    if match:
        table = match.group(1).strip().strip("'\"")
        return (
            "SELECT ordinal_position - 1 AS cid, column_name AS name, data_type AS type, "
            "CASE WHEN is_nullable='NO' THEN 1 ELSE 0 END AS notnull, column_default AS dflt_value, "
            "CASE WHEN column_name IN (SELECT kcu.column_name FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage kcu ON tc.constraint_name=kcu.constraint_name "
            "AND tc.table_schema=kcu.table_schema WHERE tc.constraint_type='PRIMARY KEY' "
            f"AND tc.table_schema='public' AND tc.table_name='{table}') THEN 1 ELSE 0 END AS pk "
            "FROM information_schema.columns "
            f"WHERE table_schema='public' AND table_name='{table}' ORDER BY ordinal_position"
        )

    # SQLite metadata probes used by startup migrations. Existing Supabase schema is already migrated.
    if re.search(r"SELECT\s+sql\s+FROM\s+sqlite_master", sql, flags=re.I):
        return "SELECT NULL::text AS sql"
    sql = re.sub(
        r"SELECT\s+1\s+FROM\s+sqlite_master\s+WHERE\s+type='table'\s+AND\s+name=\?",
        "SELECT 1 FROM information_schema.tables WHERE table_schema='public' AND table_name=%s",
        sql, flags=re.I,
    )

    had_ignore = bool(re.search(r"INSERT\s+OR\s+IGNORE\s+INTO", sql, flags=re.I))
    sql = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, flags=re.I)
    if had_ignore and "ON CONFLICT" not in sql.upper():
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    sql = re.sub(r"\bBEGIN\s+IMMEDIATE\b", "BEGIN", sql, flags=re.I)
    sql = re.sub(r"date\('now'\)", "CURRENT_DATE", sql, flags=re.I)
    sql = re.sub(r"datetime\('now'\)", "CURRENT_TIMESTAMP", sql, flags=re.I)
    sql = re.sub(r"GROUP_CONCAT\(([^)]+)\)", r"STRING_AGG(\1::text, ',')", sql, flags=re.I)

    # SQLite treats boolean expressions as integers (0/1), so legacy SQL often
    # uses SUM(column='value'). PostgreSQL has a real boolean type and rejects
    # SUM(boolean). Convert the project's simple comparison aggregates to a
    # portable numeric CASE expression. This intentionally targets only simple
    # SQL comparisons and leaves SUM(CASE ...), SUM(amount), and Python code
    # untouched.
    sql = re.sub(
        r"\bSUM\(\s*([A-Za-z_][A-Za-z0-9_.]*\s*(?:=|<>|!=|<=|>=|<|>)\s*(?:'[^']*'|\"[^\"]*\"|%s|\?|[-+]?\d+(?:\.\d+)?|[A-Za-z_][A-Za-z0-9_.]*))\s*\)",
        r"SUM(CASE WHEN \1 THEN 1 ELSE 0 END)",
        sql,
        flags=re.I,
    )
    # SQLite date(base_date, '+' || days_expression || ' days') -> PostgreSQL interval math.
    # The days expression may be a placeholder, COALESCE(...), column, or subquery.
    sql = re.sub(
        r"date\(\s*([^,]+?)\s*,\s*'\+'\s*\|\|\s*(.+?)\s*\|\|\s*' days'\s*\)",
        r"(((\1)::date + ((\2)::text || ' days')::interval)::date)::text",
        sql,
        flags=re.I | re.S,
    )
    return _replace_qmarks(sql)


def translate_ddl(sql: str) -> str:
    sql = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
        sql,
        flags=re.I,
    )
    sql = re.sub(r"\bAUTOINCREMENT\b", "", sql, flags=re.I)
    sql = re.sub(r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, flags=re.I)
    if "INSERT INTO" in sql.upper() and "ON CONFLICT" not in sql.upper() and "OR IGNORE" in sql.upper():
        sql = sql.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    return translate_sql(sql)


def split_sql_script(script: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    for ch in script:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        if ch == ";" and not in_single and not in_double:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(ch)
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements



def table_columns(db: Any, table_name: str) -> set[str]:
    """Return table columns without SQLite PRAGMA dependencies."""
    if isinstance(db, PostgresConnectionCompat):
        rows = db.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=? ORDER BY ordinal_position",
            (table_name,),
        ).fetchall()
        return {str(row[0]) for row in rows}
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()}


def table_exists(db: Any, table_name: str) -> bool:
    """Portable table existence check; PostgreSQL uses information_schema."""
    if isinstance(db, PostgresConnectionCompat):
        return db.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=?",
            (table_name,),
        ).fetchone() is not None
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None




def execute_insert(
    db: Any,
    sql: str,
    params: tuple[Any, ...] | list[Any] = (),
    *,
    returning: str = "id",
) -> int:
    """Execute an INSERT and return its generated key for either backend.

    PostgreSQL uses ``RETURNING`` on the same statement. SQLite's legacy key
    handling is intentionally contained in this adapter module.
    """
    if hasattr(db, "execute_insert"):
        return int(db.execute_insert(sql, params, returning=returning))
    cur = db.execute(sql, params)
    value = cur.lastrowid
    if value is None:
        raise sqlite3.Error("INSERT did not return a generated key")
    return int(value)

def table_has_unique_constraint(db: Any, table_name: str, columns: tuple[str, ...]) -> bool:
    """Return whether *table_name* has a UNIQUE constraint on exactly *columns*.

    PostgreSQL is inspected through pg_catalog. SQLite support is isolated here
    for the optional local legacy backend, keeping application code engine-native.
    """
    identifier = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    if not identifier.fullmatch(table_name) or not columns or any(not identifier.fullmatch(c) for c in columns):
        raise ValueError("Invalid table or column identifier")

    if isinstance(db, PostgresConnectionCompat):
        rows = db.execute(
            """
            SELECT array_agg(att.attname ORDER BY key_cols.ordinality) AS columns
            FROM pg_constraint con
            JOIN pg_class rel ON rel.oid = con.conrelid
            JOIN pg_namespace ns ON ns.oid = rel.relnamespace
            JOIN unnest(con.conkey) WITH ORDINALITY AS key_cols(attnum, ordinality) ON TRUE
            JOIN pg_attribute att ON att.attrelid = rel.oid AND att.attnum = key_cols.attnum
            WHERE ns.nspname='public' AND rel.relname=? AND con.contype='u'
            GROUP BY con.oid
            """,
            (table_name,),
        ).fetchall()
        expected = tuple(columns)
        return any(tuple(row[0] or ()) == expected for row in rows)

    for index_row in db.execute(f"PRAGMA index_list({table_name})").fetchall():
        # sqlite3 PRAGMA index_list: seq, name, unique, origin, partial
        if not bool(index_row[2]):
            continue
        index_name = str(index_row[1]).replace('"', '""')
        found = tuple(str(row[2]) for row in db.execute(f'PRAGMA index_info("{index_name}")').fetchall())
        if found == tuple(columns):
            return True
    return False

def database_healthcheck(db: Any) -> str:
    """Run a lightweight engine-native connection and catalog health check."""
    if isinstance(db, PostgresConnectionCompat):
        row = db.execute(
            "SELECT CASE WHEN current_database() IS NOT NULL THEN 'ok' ELSE 'error' END"
        ).fetchone()
        return str(row[0]) if row else 'error'
    return str(db.execute("PRAGMA quick_check").fetchone()[0])

def connect(database_url: str | None, sqlite_path: Path, timeout: int = 30):
    if database_url and database_url.startswith(("postgresql://", "postgres://")):
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("PostgreSQL mode requires: pip install psycopg[binary]") from exc
        raw = psycopg.connect(database_url, connect_timeout=timeout, sslmode="require")
        return PostgresConnectionCompat(raw)

    db = sqlite3.connect(sqlite_path, timeout=timeout)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA journal_mode = WAL")
    db.execute("PRAGMA busy_timeout = 30000")
    db.execute("PRAGMA synchronous = NORMAL")
    return db


def using_postgres() -> bool:
    value = os.environ.get("DATABASE_URL", "")
    return value.startswith(("postgresql://", "postgres://"))
