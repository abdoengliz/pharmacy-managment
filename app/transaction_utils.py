from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator


@contextmanager
def atomic(db: Any) -> Iterator[Any]:
    """Run a unit of work as one database transaction.

    The caller must not commit inside the block. Any exception rolls back all
    writes; a clean exit commits exactly once. Works with both the SQLite and
    PostgreSQL compatibility connections used by the application.
    """
    try:
        yield db
    except BaseException:
        db.rollback()
        raise
    else:
        db.commit()
