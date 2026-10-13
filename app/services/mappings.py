"""
Mapping CRUD service layer.
Provides a compatibility shim for the legacy dict-based API and
clean helpers for the new SQLAlchemy models.

Persistence strategy (dual-write):
  • Primary store  – the 'mappings' table in the SQLite/Postgres DB
  • Backup store   – mappings.json on disk (absolute path, project root)

Every call to save_mappings_legacy() writes to BOTH stores so that:
  1. The DB is the live source at runtime.
  2. mappings.json is always an up-to-date snapshot that survives DB
     wipes, container rebuilds, and fresh deployments.
  3. On startup _migrate_json() loads mappings.json → DB when the DB is
     empty, restoring all previous configuration automatically.
"""
from __future__ import annotations
import json
import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

# ── JSON file path helpers ────────────────────────────────────────────────────
# Computed once at import time using the file's own __file__ so it is
# always absolute and independent of the working directory.
_SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))   # .../app/services/
_APP_DIR     = os.path.dirname(_SERVICE_DIR)                 # .../app/
_PROJECT_ROOT = os.path.dirname(_APP_DIR)                    # .../wm-project/


def get_mappings_json_path() -> str:
    """Return the absolute path to mappings.json.

    Checks (in order):
      1. Flask app config key MAPPINGS_JSON_PATH (set from env var)
      2. MAPPINGS_JSON_PATH environment variable directly
      3. Default: <project_root>/mappings.json
    """
    try:
        from flask import current_app
        cfg_path = current_app.config.get("MAPPINGS_JSON_PATH", "")
        if cfg_path:
            return cfg_path
    except RuntimeError:
        pass  # outside app context — fall through to env / default
    env_path = os.environ.get("MAPPINGS_JSON_PATH", "")
    if env_path:
        return env_path
    return os.path.join(_PROJECT_ROOT, "mappings.json")


def save_mappings_to_json(data: dict) -> None:
    """Write the full mappings dict to mappings.json (atomic via tmp file)."""
    path = get_mappings_json_path()
    tmp_path = path + ".tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)   # atomic on POSIX; best-effort on Windows
        log.debug("mappings.json written to %s", path)
    except Exception as exc:
        log.warning("Could not write mappings.json: %s", exc)
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def load_mappings_from_json() -> dict | None:
    """Load mappings from mappings.json; return None if file is absent or corrupt."""
    path = get_mappings_json_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        log.info("Loaded mappings from %s", path)
        return data
    except Exception as exc:
        log.warning("Could not read mappings.json (%s): %s", path, exc)
        return None


def load_mappings_legacy(db_engine) -> dict:
    """
    Read all mappings from legacy 'mappings' table (key/value store).
    Used for backward-compat during transition.
    """
    from sqlalchemy import text
    _DEFAULT = {
        "sales": {}, "branch": {}, "invoice": "", "config": {},
        "customer_emails": {}, "contacts": {},
    }
    result = dict(_DEFAULT)
    try:
        with db_engine.connect() as conn:
            rows = conn.execute(text("SELECT key, value FROM mappings")).fetchall()
        for key, raw in rows:
            if key in result:
                try:
                    result[key] = json.loads(raw)
                except json.JSONDecodeError:
                    result[key] = raw
    except Exception as exc:
        log.error("load_mappings_legacy error: %s", exc)
    return result


def save_mappings_legacy(db_engine, data: dict):
    """Write all mapping sections to the 'mappings' DB table AND mappings.json."""
    from sqlalchemy import text
    KEYS = ["sales", "branch", "invoice", "config", "customer_emails", "contacts"]
    try:
        with db_engine.connect() as conn:
            for key in KEYS:
                if key not in data:
                    continue
                value = data[key]
                serialised = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
                try:
                    conn.execute(
                        text("INSERT INTO mappings (key, value) VALUES (:k, :v) "
                             "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value"),
                        {"k": key, "v": serialised},
                    )
                except Exception:
                    # SQLite fallback (no ON CONFLICT … DO UPDATE support in old sqlite)
                    conn.execute(
                        text("INSERT OR REPLACE INTO mappings (key, value) VALUES (:k, :v)"),
                        {"k": key, "v": serialised},
                    )
            conn.commit()
    except Exception as exc:
        log.error("save_mappings_legacy error: %s", exc)
        raise

    # Dual-write: keep mappings.json in sync so fresh deployments can
    # restore all settings from the file even when the DB is brand-new.
    try:
        save_mappings_to_json(data)
    except Exception as exc:
        log.warning("mappings.json dual-write skipped: %s", exc)


def get_sales_rep_email(db_engine, rep_name: str) -> str:
    """Return email string for a sales rep from legacy table."""
    data = load_mappings_legacy(db_engine)
    return data.get("sales", {}).get(rep_name, "")


def get_branch_email(db_engine, branch: str) -> str:
    data = load_mappings_legacy(db_engine)
    return data.get("branch", {}).get(branch, "")


def get_customer_email(db_engine, customer: str) -> str:
    data = load_mappings_legacy(db_engine)
    return data.get("customer_emails", {}).get(customer, "")


def upsert_customer_email(db_engine, customer: str, emails: str):
    """Upsert a single customer email in legacy tables."""
    from sqlalchemy import text
    try:
        with db_engine.connect() as conn:
            try:
                conn.execute(
                    text("INSERT INTO customer_emails (customer_name, emails) VALUES (:n, :e) "
                         "ON CONFLICT (customer_name) DO UPDATE SET emails = EXCLUDED.emails"),
                    {"n": customer, "e": emails},
                )
            except Exception:
                conn.execute(
                    text("INSERT OR REPLACE INTO customer_emails (customer_name, emails) VALUES (:n, :e)"),
                    {"n": customer, "e": emails},
                )
            conn.commit()
    except Exception as exc:
        log.error("upsert_customer_email error: %s", exc)
        raise


def bulk_upsert_customer_emails(db_engine, mapping: dict):
    """Bulk upsert customer emails."""
    if not mapping:
        return
    from sqlalchemy import text
    try:
        with db_engine.connect() as conn:
            for cname, emails in mapping.items():
                try:
                    conn.execute(
                        text("INSERT INTO customer_emails (customer_name, emails) VALUES (:n, :e) "
                             "ON CONFLICT (customer_name) DO UPDATE SET emails = EXCLUDED.emails"),
                        {"n": cname, "e": emails},
                    )
                except Exception:
                    conn.execute(
                        text("INSERT OR REPLACE INTO customer_emails (customer_name, emails) VALUES (:n, :e)"),
                        {"n": cname, "e": emails},
                    )
            conn.commit()
    except Exception as exc:
        log.error("bulk_upsert_customer_emails error: %s", exc)
        raise


def get_all_customer_emails(db_engine) -> dict:
    from sqlalchemy import text
    result = {}
    try:
        with db_engine.connect() as conn:
            rows = conn.execute(
                text("SELECT customer_name, emails FROM customer_emails")
            ).fetchall()
        for name, emails in rows:
            result[name] = emails
    except Exception as exc:
        log.error("get_all_customer_emails error: %s", exc)
    return result
