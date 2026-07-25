import importlib.util
from pathlib import Path

path = Path(__file__).parent / "app" / "db_compat.py"
spec = importlib.util.spec_from_file_location("db_compat_verify", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

cases = [
    "UPDATE supplier_invoices SET due_date=date(invoice_date, '+' || ? || ' days') WHERE supplier_id=?",
    "UPDATE supplier_invoices SET due_date=date(invoice_date, '+' || COALESCE((SELECT grace_days FROM suppliers s WHERE s.id=supplier_invoices.supplier_id),30) || ' days') WHERE due_date IS NULL OR due_date=''",
]
for sql in cases:
    translated = module.translate_sql(sql)
    assert "date(invoice_date" not in translated.lower(), translated
    assert "::interval" in translated.lower(), translated
print("SUCCESS: Phase 5 PostgreSQL date translation verified.")
