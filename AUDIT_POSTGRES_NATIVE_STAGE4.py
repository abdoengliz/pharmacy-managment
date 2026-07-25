from pathlib import Path
import re, json
ROOT=Path(__file__).resolve().parent
checks={
 "app_insert_or_ignore": re.compile(r"INSERT\s+OR\s+IGNORE",re.I),
 "app_sqlite_master": re.compile(r"sqlite_master",re.I),
 "app_pragma": re.compile(r"\bPRAGMA\b",re.I),
 "python_greatest_call": re.compile(r"(?m)^[^\n#]*\bGREATEST\s*\("),
 "lastrowid": re.compile(r"\.lastrowid\b"),
}
results={k:[] for k in checks}
for p in (ROOT/'app').rglob('*.py'):
    if p.name=='db_compat.py': continue
    text=p.read_text(encoding='utf-8')
    for k,rx in checks.items():
        for m in rx.finditer(text):
            results[k].append({"file":str(p.relative_to(ROOT)),"line":text.count("\n",0,m.start())+1})
print(json.dumps({k:len(v) for k,v in results.items()},ensure_ascii=False,indent=2))
(ROOT/'POSTGRES_NATIVE_STAGE4_AUDIT.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
