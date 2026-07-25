from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PATTERNS = {
    "autoincrement": re.compile(r"\bAUTOINCREMENT\b", re.I),
    "insert_or_ignore": re.compile(r"INSERT\s+OR\s+IGNORE", re.I),
    "pragma": re.compile(r"\bPRAGMA\b", re.I),
    "sqlite_master": re.compile(r"sqlite_master", re.I),
    "lastrowid": re.compile(r"\.lastrowid\b"),
    "group_concat": re.compile(r"GROUP_CONCAT\s*\(", re.I),
    "sqlite_date_function": re.compile(r"date\s*\(\s*['\"]now['\"]", re.I),
}

hits = []
for path in sorted((ROOT / "app").rglob("*.py")):
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, pattern in PATTERNS.items():
            if pattern.search(line):
                hits.append({"kind": name, "file": str(path.relative_to(ROOT)), "line": lineno, "text": line.strip()})

summary = {}
for hit in hits:
    summary[hit["kind"]] = summary.get(hit["kind"], 0) + 1
report = {"status": "needs_conversion" if hits else "native", "summary": summary, "hits": hits}
(ROOT / "POSTGRES_NATIVE_AUDIT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"status": report["status"], "summary": summary}, ensure_ascii=False, indent=2))
