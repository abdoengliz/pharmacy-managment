# Supabase Phase 6 — Transaction Recovery Fix

- Fixed PostgreSQL connections remaining in an aborted transaction after a caught SQL error.
- The compatibility layer now rolls back immediately before mapping PostgreSQL errors to the legacy SQLite exceptions.
- This allows optional dashboard probes to fail safely without poisoning all later queries.
- Added focused regression tests for rollback-on-error behavior.
