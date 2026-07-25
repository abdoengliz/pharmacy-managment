# Pharma ERP Enterprise v5.7.2.1 Startup Hotfix

- Fixed startup crash in `app/core.py` by importing `os` before reading `PHARMA_DB_PATH`.
- Added `run_windows.bat` for reliable Windows startup through `python app.py`.
- No database schema, route, business-logic, or stored-data changes.
- For Flask CLI, use `flask --app app.py run`; plain `flask run` may auto-detect the `app` package instead of the root launcher and start without registered routes.
