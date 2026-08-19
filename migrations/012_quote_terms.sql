-- 012 — the missing middle: a quote that can lapse, and the subjectivities
-- that have to be chased before it does.
--
-- ADDITIVE ONLY. One new nullable column and one new table; no existing row
-- is read, rewritten or constrained differently by this file. Every
-- submission written before today reads quote_expires_on = NULL, which is
-- exactly "nobody told us when this lapses" — not a guess, and not a date.
--
-- `status` needs NO change: 001_initial's CHECK already admits 'quoted', and
-- models.SubmissionStatus.QUOTED already exists. What was missing was never
-- the state, it was the DATE the state runs out on and the work it implies.
-- (SQLite cannot alter a CHECK in place anyway; it would need a table
-- rebuild, which is not additive and would not be done here.)

ALTER TABLE submission ADD COLUMN quote_expires_on TEXT;   -- DATE, nullable

-- (status, quote_expires_on) in that order: every read of this column filters
-- status = 'quoted' first and then ranges over the date.
CREATE INDEX idx_submission_quote_expiry
    ON submission (status, quote_expires_on);

-- Subjectivities: what a market requires before its quote is bindable.
-- Shaped on rfi_item, which is the same kind of thing (a chaseable line item
-- with a due date and a settled/unsettled status) and whose repo, forms and
-- surfaces are the pattern this follows.
CREATE TABLE submission_subjectivity (
    id             TEXT PRIMARY KEY,
    submission_id  TEXT NOT NULL REFERENCES submission (id),
    description    TEXT NOT NULL,
    due_on         TEXT,            -- DATE
    status         TEXT NOT NULL DEFAULT 'outstanding',
    satisfied_on   TEXT,            -- DATE
    notes          TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    deleted_at     TEXT
);
CREATE INDEX idx_subjectivity_submission ON submission_subjectivity (submission_id);
CREATE INDEX idx_subjectivity_status ON submission_subjectivity (status);
