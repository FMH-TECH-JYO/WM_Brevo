"""
extensions.py — Flask extension instances (SQLAlchemy, CSRF, etc.)
Initialised here to avoid circular imports; bound to the app in create_app().
"""
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect

db = SQLAlchemy()
csrf = CSRFProtect()
