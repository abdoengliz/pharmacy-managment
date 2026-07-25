from __future__ import annotations

import os
import sys


def main() -> int:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("ERROR: DATABASE_URL is not set.")
        return 1
    try:
        import psycopg
        with psycopg.connect(database_url, connect_timeout=15, sslmode="require") as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT current_database(), current_user, version()")
                database, user, version = cur.fetchone()
        print("SUCCESS: Supabase PostgreSQL connection works.")
        print(f"Database: {database}")
        print(f"User: {user}")
        print(f"Server: {version.split(',')[0]}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
