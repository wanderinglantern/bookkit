-- MCP batch undo: one tool call becomes one undoable unit. Additive only —
-- one nullable column on event_log (existing rows read as "unbatched", which
-- is correct) and one new table. No backfill, nothing rewritten.
ALTER TABLE event_log ADD COLUMN batch_id TEXT;
CREATE INDEX idx_event_batch ON event_log (batch_id);

CREATE TABLE event_batch (
    id          TEXT PRIMARY KEY,
    ref         TEXT NOT NULL UNIQUE,
    source      TEXT NOT NULL,
    tool        TEXT NOT NULL,
    summary     TEXT NOT NULL,
    org_id      TEXT REFERENCES org (id),
    created_at  TEXT NOT NULL,
    reverted_at TEXT
);
CREATE INDEX idx_event_batch_created ON event_batch (created_at);
