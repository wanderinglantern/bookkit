-- 018 — "not for us on the primary, but show us the excess" is an ANSWER.
--
-- NOT ADDITIVE, and that is why this file is long. `market_response.status`
-- carries a CHECK, SQLite cannot widen one in place, so the only way to let a
-- new word into the vocabulary is the 12-step table rebuild below. Every
-- other migration in this book so far has been additive; this one MOVES DATA,
-- and the rollback is the snapshot `db.snapshot_before_migrations` takes into
-- backups/ before any pending migration runs — automatic on the first connect
-- of the TUI, the CLI, the web layer and the MCP server alike.
--
-- WHAT IS SAFE ABOUT IT. The change is a WIDENING: every row that satisfied
-- the old CHECK satisfies the new one, so the copy cannot be refused part-way
-- by a value already on disk (which is the failure mode a DB CHECK added
-- against existing rows has). Nothing in this schema REFERENCES
-- market_response, so dropping and renaming it cannot orphan another table.
-- Column list and order are unchanged — 016's `quote_expires_on` included —
-- so the INSERT ... SELECT is column-for-column.
--
-- WHY THE WORD IS WORTH A REBUILD. `declined` records only half of what that
-- market said, and recording only that half ENDS them: the row drops out of
-- the open set, off the clearance check, and reads on the client's workbook
-- as a market that looked and walked away — while the work of going back to
-- that carrier higher up the tower is still to do (Grant, 2026-08-26).
-- models.MARKET_RESPONSE_STATUSES carries the rest of the reasoning.

CREATE TABLE market_response_new (
    id            TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL REFERENCES submission (id),
    line_id       TEXT NOT NULL REFERENCES line_of_coverage (id),
    market_org_id TEXT REFERENCES org (id),   -- the paper; NULL = carrier TBD
    via_org_id    TEXT REFERENCES org (id),   -- wholesaler / MGA in the chain

    attach        INTEGER,   -- cents
    lim           INTEGER,   -- cents

    -- THE ONE LINE THIS FILE EXISTS FOR. `declined_open_elsewhere` is a no
    -- about THIS BAND (attach/lim above say which), not about this market.
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'indicated', 'quoted',
                                    'declined', 'declined_open_elsewhere',
                                    'non_response', 'bound')),
    responded_on  TEXT,      -- DATE

    rating_basis    TEXT,
    rate_per        INTEGER,
    exposure_amount INTEGER,
    rate_micros     INTEGER,

    premium             INTEGER,   -- cents
    commission_bps      INTEGER,
    commission_included INTEGER NOT NULL DEFAULT 1,
    tria_premium        INTEGER,   -- cents
    policy_fees         INTEGER,   -- cents
    surplus_lines_tax   INTEGER,   -- cents

    decline_reason        TEXT,   -- internal; NEVER rendered to a client
    decline_reason_public TEXT,   -- controlled vocabulary; client-facing

    notes      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,

    -- 016's column, kept in the position ALTER TABLE put it in so the copy
    -- below can name every column explicitly and still read straight across.
    quote_expires_on TEXT,

    CHECK (market_org_id IS NOT NULL OR via_org_id IS NOT NULL)
);

-- EVERY COLUMN NAMED ON BOTH SIDES. `INSERT INTO … SELECT *` would copy by
-- POSITION and silently shear the table sideways the next time somebody adds
-- a column to one definition and not the other.
INSERT INTO market_response_new (
    id, submission_id, line_id, market_org_id, via_org_id,
    attach, lim, status, responded_on,
    rating_basis, rate_per, exposure_amount, rate_micros,
    premium, commission_bps, commission_included,
    tria_premium, policy_fees, surplus_lines_tax,
    decline_reason, decline_reason_public,
    notes, created_at, updated_at, deleted_at, quote_expires_on
)
SELECT
    id, submission_id, line_id, market_org_id, via_org_id,
    attach, lim, status, responded_on,
    rating_basis, rate_per, exposure_amount, rate_micros,
    premium, commission_bps, commission_included,
    tria_premium, policy_fees, surplus_lines_tax,
    decline_reason, decline_reason_public,
    notes, created_at, updated_at, deleted_at, quote_expires_on
FROM market_response;

DROP TABLE market_response;

ALTER TABLE market_response_new RENAME TO market_response;

-- DROP TABLE took the indexes with it. All four are recreated exactly as
-- 015 and 016 declared them — an index quietly missing after a rebuild is
-- the classic 12-step casualty, and idx_response_clearance is what the
-- clearance check reads on every approach.
CREATE INDEX idx_response_submission ON market_response (submission_id);
CREATE INDEX idx_response_line       ON market_response (line_id);
CREATE INDEX idx_response_market     ON market_response (market_org_id);
CREATE INDEX idx_response_clearance  ON market_response (market_org_id, line_id, status);
CREATE INDEX idx_response_expiry     ON market_response (submission_id, quote_expires_on);
