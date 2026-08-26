-- WHAT A MARKET *IS* BECOMES REVERTIBLE (Grant, 2026-08-26).
--
-- `market_type` and `am_best_rating` are the two facts that decide whether a
-- market is the right one to go to, and both live on `market_profile`. Every
-- write to that table went through raw SQL in repo/orgs.set_market_profile —
-- outside base.insert/base.update — so a rating typed on the web wrote NO
-- event_log row: nothing in the changes list, nothing for `u` or revert_batch
-- to take back, and no record of who set it or when. The hole was invisible
-- while nothing but a form could reach those fields; MCP's `market_edit`
-- made it reachable by an assistant, and the write-tool roster gate caught
-- it as "a batch with no events — nothing to undo".
--
-- base.get/update address a row by `id` and read `deleted_at`, and
-- base.insert stamps `created_at`/`updated_at`. This gives the table the four
-- columns that contract needs and nothing else.
--
-- THE ID IS THE ORG ID, deliberately. A market profile is 1:1 with its market
-- and has no life of its own — it is created when the first fact about the
-- market arrives and dies with the org. Minting a second identifier for it
-- would mean every writer had to look one up before it could write, and an
-- event_log row reading `market_profile / <the market's own id>` is the
-- truth about what changed.
--
-- ADDITIVE ONLY: four new nullable columns and a backfill of one of them.
-- Nothing is dropped, rewritten or narrowed, and db.snapshot_before_migrations
-- has already taken a copy of the file before this runs.
ALTER TABLE market_profile ADD COLUMN id TEXT;
ALTER TABLE market_profile ADD COLUMN created_at TEXT;
ALTER TABLE market_profile ADD COLUMN updated_at TEXT;
ALTER TABLE market_profile ADD COLUMN deleted_at TEXT;

UPDATE market_profile SET id = org_id WHERE id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_market_profile_id
    ON market_profile (id);
