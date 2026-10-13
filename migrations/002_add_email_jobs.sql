-- 002_add_email_jobs.sql — New job tracking tables
-- Created automatically by SQLAlchemy on startup (db.create_all()).
-- Kept here for manual/emergency schema management.

CREATE TABLE IF NOT EXISTS uploads (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    filename       TEXT NOT NULL,
    uploaded_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    row_count      INTEGER DEFAULT 0,
    status         TEXT DEFAULT 'ok',
    invoice_month  INTEGER,
    invoice_year   INTEGER,
    error_message  TEXT
);

CREATE TABLE IF NOT EXISTS email_jobs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key  TEXT UNIQUE NOT NULL,
    status           TEXT DEFAULT 'pending',
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    started_at       DATETIME,
    completed_at     DATETIME,
    attempt_count    INTEGER DEFAULT 0,
    error_code       TEXT,
    error_message    TEXT,
    customer         TEXT NOT NULL,
    recipients_json  TEXT DEFAULT '[]',
    cc_json          TEXT DEFAULT '[]',
    subject          TEXT NOT NULL,
    body_html        TEXT NOT NULL,
    invoice_month    INTEGER,
    invoice_year     INTEGER,
    batch_id         TEXT
);

CREATE TABLE IF NOT EXISTS email_attempts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        INTEGER NOT NULL REFERENCES email_jobs(id),
    attempted_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    status        TEXT,
    error_code    TEXT,
    error_message TEXT,
    smtp_response TEXT
);

CREATE TABLE IF NOT EXISTS send_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        INTEGER REFERENCES email_jobs(id),
    customer      TEXT NOT NULL,
    to_emails     TEXT DEFAULT '',
    cc_emails     TEXT DEFAULT '',
    subject       TEXT NOT NULL,
    sent_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    status        TEXT DEFAULT 'sent',
    attempts      INTEGER DEFAULT 1,
    error_code    TEXT,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT DEFAULT '',
    name        TEXT NOT NULL,
    email       TEXT NOT NULL,
    branch      TEXT DEFAULT '',
    sales_rep   TEXT DEFAULT '',
    department  TEXT DEFAULT '',
    company     TEXT DEFAULT '',
    title       TEXT DEFAULT '',
    raw_row     TEXT DEFAULT '',
    imported_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   DATETIME DEFAULT CURRENT_TIMESTAMP,
    user        TEXT DEFAULT '',
    action      TEXT NOT NULL,
    entity_type TEXT,
    entity_id   TEXT,
    old_value   TEXT,
    new_value   TEXT
);
