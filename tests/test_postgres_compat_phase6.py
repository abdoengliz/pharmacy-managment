import sqlite3
from unittest.mock import Mock

import pytest

from app.db_compat import PostgresConnectionCompat


def test_failed_statement_rolls_back_connection_before_mapping_error(monkeypatch):
    class FakePsycopgError(Exception):
        pass

    class FakePsycopg:
        Error = FakePsycopgError
        IntegrityError = type("IntegrityError", (FakePsycopgError,), {})

    import sys
    monkeypatch.setitem(sys.modules, "psycopg", FakePsycopg)

    cursor = Mock()
    cursor.execute.side_effect = FakePsycopgError("bad optional query")
    raw = Mock()
    raw.cursor.return_value = cursor
    db = PostgresConnectionCompat(raw)

    with pytest.raises(sqlite3.Error, match="bad optional query"):
        db.execute("SELECT broken")

    raw.rollback.assert_called_once_with()


def test_successful_statement_does_not_rollback():
    cursor = Mock()
    raw = Mock()
    raw.cursor.return_value = cursor
    db = PostgresConnectionCompat(raw)

    db.execute("SELECT 1")

    raw.rollback.assert_not_called()
