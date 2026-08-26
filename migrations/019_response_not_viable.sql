-- 019 — "their minimum premium is more than this account spends" is an answer.
--
-- THE SAME REBUILD 018 IS, and for the same reason: `market_response.status`
-- carries a CHECK, SQLite cannot widen one in place, and the only way to let a
-- new word into the vocabulary is the twelve-step table rebuild. Read 018's
-- header for what makes it safe — it is all still true here. This is a
-- WIDENING, so no row already on disk can be refused by the copy; nothing in
-- the schema references market_response; the column list is unchanged and the
-- INSERT names every column on both sides.
--
-- WHY THE WORD IS NEEDED. Every other status records what a MARKET said.
-- `not_viable` records what the BROKER decided about a market — the minimum
-- premium is above what the account spends, or the economics do not work at
-- any rate it would quote (Grant, 2026-08-26). Filing that as `declined`
-- credits a carrier with a refusal it never made, and filing it as
-- `non_response` says nobody looked. models.MARKET_RESPONSE_STATUSES carries
-- the rest of the reasoning.
--
-- TWO REBUILDS IN TWO DAYS IS THE ARGUMENT, NOT AN ACCIDENT. A vocabulary a
-- broker keeps needing to extend does not belong behind a CHECK that only a
-- migration can widen; the user-editable list this book is heading for is
-- where the next word should land, and this file is the last one that should
-- have to look like this.

CREATE TABLE market_response_new (
    id            TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL REFERENCES submission (id),
    line_id       TEXT NOT NULL REFERENCES line_of_coverage (id),
    market_org_id TEXT REFERENCES org (id),   -- the paper; NULL = carrier TBD
    via_org_id    TEXT REFERENCES org (id),   -- wholesaler / MGA in the chain

    attach        INTEGER,   -- cents
    lim           INTEGER,   -- cents

    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'indicated', 'quoted',
                                    'declined', 'declined_open_elsewhere',
                                    'not_viable', 'non_response', 'bound')),
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

    quote_expires_on TEXT,

    CHECK (market_org_id IS NOT NULL OR via_org_id IS NOT NULL)
);

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

CREATE INDEX idx_response_submission ON market_response (submission_id);
CREATE INDEX idx_response_line       ON market_response (line_id);
CREATE INDEX idx_response_market     ON market_response (market_org_id);
CREATE INDEX idx_response_clearance  ON market_response (market_org_id, line_id, status);
CREATE INDEX idx_response_expiry     ON market_response (submission_id, quote_expires_on);
