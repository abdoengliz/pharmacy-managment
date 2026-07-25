@echo off
set APP_ENV=production
waitress-serve --host=127.0.0.1 --port=8000 app:app
