from __future__ import annotations

import logging
import os
import secrets
from datetime import timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, session, url_for, send_from_directory


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


ENVIRONMENT = os.environ.get("APP_ENV", "development").strip().lower()
IS_PRODUCTION = ENVIRONMENT == "production"
IS_VERCEL = bool(os.environ.get("VERCEL"))
secret_key = os.environ.get("SECRET_KEY", "").strip()
if IS_PRODUCTION and len(secret_key) < 32:
    raise RuntimeError("Production requires a fixed SECRET_KEY of at least 32 characters.")

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config.update(
    SECRET_KEY=secret_key or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE=os.environ.get("SESSION_COOKIE_SAMESITE", "Lax"),
    SESSION_COOKIE_SECURE=_env_flag("SESSION_COOKIE_SECURE", IS_PRODUCTION),
    PERMANENT_SESSION_LIFETIME=timedelta(minutes=int(os.environ.get("SESSION_LIFETIME_MINUTES", "480"))),
    MAX_CONTENT_LENGTH=int(os.environ.get("MAX_CONTENT_LENGTH_MB", "16")) * 1024 * 1024,
)


def _configure_logging() -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    # Vercel's deployment bundle is read-only. Log to stdout/stderr there so
    # entries are available in Vercel Observability instead of opening files.
    if IS_VERCEL:
        handler = logging.StreamHandler()
    else:
        log_dir = Path(os.environ.get("LOG_DIR", Path(__file__).resolve().parent.parent / "logs"))
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / "pharma_erp.log",
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )

    handler.setFormatter(formatter)
    handler.setLevel(logging.INFO)
    if not any(type(existing) is type(handler) for existing in app.logger.handlers):
        app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)


def _csrf_token() -> str:
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


@app.before_request
def csrf_protect():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        expected = session.get("_csrf_token", "")
        supplied = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
        if not expected or not supplied or not secrets.compare_digest(expected, supplied):
            app.logger.warning("CSRF rejected: %s %s ip=%s", request.method, request.path, request.remote_addr)
            return render_template("400.html", message="انتهت صلاحية نموذج الحماية. حدّث الصفحة وحاول مرة أخرى."), 400


@app.before_request
def enforce_initial_password_change():
    if request.endpoint in {None, "static", "login", "logout", "change_initial_password", "attendance_portal"}:
        return None
    if session.get("user_id"):
        from .core import current_user
        user = current_user()
        if user and "must_change_password" in user.keys() and user["must_change_password"]:
            flash("يجب تغيير كلمة المرور الأولية قبل استخدام النظام.", "warning")
            return redirect(url_for("change_initial_password"))
    return None


@app.context_processor
def inject_security_helpers():
    return {"csrf_token": _csrf_token}


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if IS_PRODUCTION:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


@app.get("/service-worker.js")
def service_worker():
    response = send_from_directory(app.static_folder, "service-worker.js", mimetype="application/javascript")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.errorhandler(404)
def not_found(_error):
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_error(error):
    app.logger.exception("Unhandled server error: %s", error)
    return render_template("500.html"), 500


def create_app() -> Flask:
    _configure_logging()
    from .core import init_db
    from .db_compat import using_postgres
    from . import routes  # noqa: F401

    # Avoid schema/index DDL on every Vercel cold start. Concurrent serverless
    # instances can deadlock while creating indexes or altering PostgreSQL tables.
    force_db_init = os.environ.get("RUN_DB_INIT", "").strip().lower() in {"1", "true", "yes"}
    if not (IS_VERCEL and using_postgres()) or force_db_init:
        init_db()
    else:
        app.logger.info("Skipped automatic database initialization on Vercel/PostgreSQL")

    app.logger.info("Application initialized in %s mode", ENVIRONMENT)
    return app
