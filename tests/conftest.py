"""Pytest configuration and fixtures."""
import io
import os
import sys
import pytest

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(scope="session")
def app():
    """Create a test Flask application."""
    from app import create_app
    from app.config import Config

    class TestConfig(Config):
        TESTING = True
        DATABASE_URL = "sqlite:///:memory:"
        SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
        WTF_CSRF_ENABLED = False
        SECRET_KEY = "test-secret"
        APP_USERNAME = "testuser"
        APP_PASSWORD = "testpass"
        SMTP_USERNAME = "test@example.com"
        SMTP_APP_PASSWORD = "testpass"
        MAX_UPLOAD_MB = 10

    application = create_app(TestConfig)
    return application


@pytest.fixture()
def app_ctx(app):
    """Push/pop an application context."""
    ctx = app.app_context()
    ctx.push()
    yield app
    ctx.pop()


@pytest.fixture()
def db(app_ctx):
    """Database session with all tables created fresh for each test."""
    from app.extensions import db as _db
    from app import models as _models  # noqa: F401 — register all models
    _db.create_all()
    # Also create legacy tables
    from sqlalchemy import text
    with _db.engine.connect() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS mappings (key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '{}')"
        ))
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS customer_emails (customer_name TEXT PRIMARY KEY, emails TEXT NOT NULL DEFAULT '')"
        ))
        conn.commit()
    yield _db
    _db.session.remove()
    _db.drop_all()


@pytest.fixture()
def client(app, db):
    """Test client — always backed by a fresh db."""
    with app.test_client() as c:
        yield c


@pytest.fixture()
def sample_invoice_bytes():
    """Return bytes of sample_invoice.xlsx fixture."""
    path = os.path.join(os.path.dirname(__file__), "fixtures", "sample_invoice.xlsx")
    with open(path, "rb") as f:
        return f.read()


@pytest.fixture()
def contacts_csv_bytes():
    """Return bytes of contacts_normal.csv fixture."""
    path = os.path.join(os.path.dirname(__file__), "fixtures", "contacts_normal.csv")
    with open(path, "rb") as f:
        return f.read()


@pytest.fixture()
def contacts_unusual_bytes():
    path = os.path.join(os.path.dirname(__file__), "fixtures", "contacts_unusual_headers.csv")
    with open(path, "rb") as f:
        return f.read()


FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
