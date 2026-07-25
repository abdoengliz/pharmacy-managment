#!/usr/bin/env sh
export APP_ENV=production
exec gunicorn --workers 3 --bind 127.0.0.1:8000 --timeout 120 app:app
