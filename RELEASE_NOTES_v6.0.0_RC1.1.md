# Pharma ERP Enterprise v6.0.0 RC1.1

## Startup Compatibility Hotfix

- Fixed startup failure in `init_db()` caused by reading a tuple with named SQLite column indexes.
- `init_db()` now uses `sqlite3.Row`, matching the application's normal database connection behavior.
- Added `SETUP_AND_RUN_WINDOWS.ps1` to create the virtual environment, install requirements, and start the application in one step.
- No business workflow, database schema, or user data was removed.
