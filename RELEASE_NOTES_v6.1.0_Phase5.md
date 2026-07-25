# Pharma ERP v6.1 Supabase Edition — Phase 5

- Fixed SQLite `date(base, '+' || days || ' days')` translation for PostgreSQL.
- Supports placeholders and calculated day expressions such as `COALESCE((SELECT ...), 30)`.
- Keeps the existing Supabase schema and data unchanged.
