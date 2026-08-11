-- 003 — key/value settings (program file roots, future preferences).

CREATE TABLE setting (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,      -- JSON
    updated_at TEXT NOT NULL
);
