-- 001_initial.sql — Legacy tables for backward compatibility
-- These are created automatically by the app on startup.
-- Kept here for documentation and for manual DB setup.

CREATE TABLE IF NOT EXISTS mappings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS customer_emails (
    customer_name TEXT PRIMARY KEY,
    emails        TEXT NOT NULL DEFAULT ''
);
