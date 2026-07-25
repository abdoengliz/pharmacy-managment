from __future__ import annotations
import getpass, os, sqlite3
from pathlib import Path
from urllib.parse import quote

import psycopg

sqlite_path = Path('pharmacy_finance.db').resolve()
if not sqlite_path.exists():
    raise SystemExit(f'Missing: {sqlite_path}')
url = os.environ.get('DATABASE_URL', '').strip()
if not url:
    password = getpass.getpass('Supabase database password: ')
    url = f'postgresql://postgres.udczeoltolukwxxmkkpa:{quote(password, safe="")}@aws-0-eu-west-1.pooler.supabase.com:5432/postgres'

src = sqlite3.connect(sqlite_path)
tables = [r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
errors = []
with psycopg.connect(url, sslmode='require', connect_timeout=30) as dst:
    with dst.cursor() as cur:
        for t in tables:
            source_count = src.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            cur.execute('SELECT COUNT(*) FROM "' + t.replace('"','""') + '"')
            target_count = cur.fetchone()[0]
            status = 'OK' if source_count == target_count else 'MISMATCH'
            print(f'{status:8} {t:35} SQLite={source_count} Supabase={target_count}')
            if status != 'OK': errors.append(t)
src.close()
raise SystemExit(1 if errors else 0)
