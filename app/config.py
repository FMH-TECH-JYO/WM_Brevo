"""
config.py — All configuration from environment variables.
"""
import os

# Absolute path to the project root (the directory that contains this file's
# parent package).  Every file-system resource (DB, mappings.json) is
# anchored here so the app works regardless of where gunicorn/Python is
# started from — critical for reliable deployment.
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DEFAULT_DB = f"sqlite:///{os.path.join(_BASE_DIR, 'wm.db')}"
_DEFAULT_MAPPINGS_JSON = os.path.join(_BASE_DIR, "mappings.json")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-in-prod")
    # Use DATABASE_URL env var if set (e.g. postgres:// on Render/Heroku),
    # otherwise default to an absolute-path SQLite file inside the project.
    DATABASE_URL = os.environ.get("DATABASE_URL", _DEFAULT_DB)
    APP_USERNAME = os.environ.get("APP_USERNAME", "admin")
    APP_PASSWORD = os.environ.get("APP_PASSWORD", "changeme")
    SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
    SMTP_APP_PASSWORD = os.environ.get("SMTP_APP_PASSWORD", "")
    SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
    MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))
    MAX_CONTENT_LENGTH = MAX_UPLOAD_MB * 1024 * 1024
    MAX_EMAIL_RETRIES = int(os.environ.get("MAX_EMAIL_RETRIES", "3"))
    EMAIL_TIMEOUT = int(os.environ.get("EMAIL_TIMEOUT", "30"))
    N8N_WEBHOOK_URL = os.environ.get("N8N_WEBHOOK_URL", "")
    SEND_MODE = os.environ.get("SEND_MODE", "smtp")
    # Brevo (formerly Sendinblue) — HTTPS email API, works on Render free tier.
    # Sign up at https://app.brevo.com — free plan allows 300 emails/day.
    BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
    # Sender email must be a verified sender in your Brevo account.
    BREVO_SENDER_EMAIL = os.environ.get("BREVO_SENDER_EMAIL", "")
    # Company name shown in all email previews and sent emails.
    COMPANY_NAME = os.environ.get("COMPANY_NAME", "FORBES MARSHALL HYD PVT LTD")
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600
    # Absolute path to mappings.json — overrideable via env var.
    MAPPINGS_JSON_PATH = os.environ.get("MAPPINGS_JSON_PATH", _DEFAULT_MAPPINGS_JSON)


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True


def get_config():
    env = os.environ.get("FLASK_ENV", "development")
    if env == "production":
        return ProductionConfig
    return DevelopmentConfig
