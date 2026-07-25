from pathlib import Path
import re
ROOT=Path(__file__).resolve().parent/'app'
patterns={
 'GROUP_CONCAT':r'GROUP_CONCAT\s*\(',
 'AUTOINCREMENT':r'\bAUTOINCREMENT\b',
 'INSERT OR IGNORE':r'INSERT\s+OR\s+IGNORE',
 'SQLite date modifier':r"date\([^\n]+\|\|[^\n]+' days'",
 'sqlite_master':r'\bsqlite_master\b',
 'PRAGMA':r'\bPRAGMA\b',
}
print('PostgreSQL compatibility audit')
for label,pat in patterns.items():
    hits=[]
    for path in ROOT.rglob('*.py'):
        for n,line in enumerate(path.read_text(encoding='utf-8').splitlines(),1):
            if re.search(pat,line,re.I): hits.append(f'{path.relative_to(ROOT.parent)}:{n}')
    print(f'{label}: {len(hits)} (handled by db_compat where applicable)')
    for h in hits[:8]: print('  ',h)
