"""
Flask application factory.
"""
import logging
import os
from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException


def create_app(config_object=None):
    app = Flask(__name__,
                template_folder="../templates",
                static_folder="../static")

    # Config
    if config_object is None:
        from app.config import get_config
        config_object = get_config()
    app.config.from_object(config_object)

    # Logging
    log_level = getattr(logging, app.config.get("LOG_LEVEL", "INFO"), logging.INFO)
    logging.basicConfig(level=log_level,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Fix postgres:// → postgresql://
    db_url = app.config.get("DATABASE_URL", "")
    if db_url.startswith("postgres://"):
        app.config["DATABASE_URL"] = db_url.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = app.config["DATABASE_URL"]
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Extensions
    from app.extensions import db, csrf
    db.init_app(app)
    csrf.init_app(app)

    # Security headers
    @app.after_request
    def set_security_headers(response):
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # Only set CSP on HTML responses to avoid breaking APIs
        if "text/html" in response.content_type:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "connect-src 'self';"
            )
        return response

    # Global error handlers
    @app.errorhandler(HTTPException)
    def handle_http(exc):
        if request.path.startswith("/api/"):
            return jsonify({
                "success": False,
                "error": {"code": str(exc.code), "message": exc.description or exc.name}
            }), exc.code
        return exc

    @app.errorhandler(Exception)
    def handle_uncaught(exc):
        import logging as _log
        _log.getLogger(__name__).exception("Unhandled error on %s %s", request.method, request.path)
        if request.path.startswith("/api/"):
            return jsonify({
                "success": False,
                "error": {"code": "INTERNAL_ERROR", "message": "Unexpected server error."}
            }), 500
        raise exc

    # Register blueprints
    from app.api import api_bp
    from app.auth import auth_bp
    # Exempt entire API blueprint from CSRF — endpoints use session auth + JSON
    csrf.exempt(api_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(auth_bp)

    # Main route
    from flask import render_template
    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/health")
    def health():
        return jsonify({"status": "ok", "database": "ok", "timestamp": __import__("datetime").datetime.utcnow().isoformat()})

    @app.route("/readiness")
    def readiness():
        smtp_configured = bool(
            app.config.get("SMTP_USERNAME") and app.config.get("SMTP_APP_PASSWORD")
        )
        return jsonify({"status": "ok", "database": "ok", "smtp_configured": smtp_configured})

    # DB init + legacy migration
    with app.app_context():
        _init_db(app, db)

    return app


def _init_db(app, db):
    """Create all tables and ensure legacy mappings table exists."""
    from sqlalchemy import text, inspect
    engine = db.engine

    # Import all models so SQLAlchemy metadata knows about their tables
    # before db.create_all() is called.  Without this, EmailJob and
    # related tables are never registered and their CREATE TABLE never runs.
    # Use importlib to avoid shadowing the local `app` (Flask) parameter.
    import importlib
    importlib.import_module("app.models")

    # Create SQLAlchemy models
    db.create_all()

    # Ensure legacy tables exist for backward compatibility
    legacy_ddl = [
        """CREATE TABLE IF NOT EXISTS mappings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '{}'
        )""",
        """CREATE TABLE IF NOT EXISTS customer_emails (
            customer_name TEXT PRIMARY KEY,
            emails TEXT NOT NULL DEFAULT ''
        )""",
    ]
    with engine.connect() as conn:
        for ddl in legacy_ddl:
            conn.execute(text(ddl))
        conn.commit()

    # Migrate from fm_mappings.json if present
    _migrate_json(engine)
    app.logger.info("DB initialised")


def _migrate_json(engine):
    """
    Restore mappings from a JSON backup file when the DB is empty.

    Search order for the backup file:
      1. mappings.json  (written on every save — the live backup)
      2. fm_mappings.json  (legacy seed file — kept for backward-compat)

    If the DB already has data this function is a no-op, so it is safe
    to call on every startup.  When the DB is empty and a backup file
    exists, it is loaded and written to the DB (and to mappings.json if
    it was the legacy file that was used).

    After restoration, if mappings.json does not exist but the DB has
    data, we write mappings.json so that future restarts can use it.
    """
    import json as _json
    from sqlalchemy import text
    from app.services.mappings import (
        save_mappings_legacy, load_mappings_from_json, save_mappings_to_json,
        load_mappings_legacy, get_mappings_json_path,
    )

    _log = logging.getLogger(__name__)
    project_root = os.path.dirname(os.path.abspath(__file__))   # …/app/
    project_root = os.path.dirname(project_root)                  # …/wm-project/

    # ── Check if DB is empty ─────────────────────────────────────────────
    try:
        with engine.connect() as conn:
            row_count = conn.execute(text("SELECT COUNT(*) FROM mappings")).scalar()
        db_has_data = bool(row_count and row_count > 0)
    except Exception as exc:
        _log.warning("Could not check mappings table: %s", exc)
        db_has_data = False

    if not db_has_data:
        # Try mappings.json first, then legacy fm_mappings.json
        data = load_mappings_from_json()
        used_source = get_mappings_json_path()
        if data is None:
            legacy_path = os.path.join(project_root, "fm_mappings.json")
            if os.path.exists(legacy_path):
                try:
                    with open(legacy_path, "r", encoding="utf-8") as f:
                        data = _json.load(f)
                    used_source = legacy_path
                    _log.info("Falling back to legacy fm_mappings.json")
                except Exception as exc:
                    _log.warning("Could not read fm_mappings.json: %s", exc)

        if data:
            try:
                save_mappings_legacy(engine, data)  # also writes mappings.json
                _log.info("Restored mappings from %s into DB", used_source)
            except Exception as exc:
                _log.warning("Could not restore mappings from JSON: %s", exc)
        return

    # ── DB has data: ensure mappings.json exists as a current backup ─────
    json_path = get_mappings_json_path()
    if not os.path.exists(json_path):
        try:
            current = load_mappings_legacy(engine)
            save_mappings_to_json(current)
            _log.info("Synced DB mappings → mappings.json (%s)", json_path)
        except Exception as exc:
            _log.warning("Could not sync DB → mappings.json: %s", exc)
