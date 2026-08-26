-- 016 — a quote expires on the ROW that quoted it.
--
-- ADDITIVE ONLY. One nullable column; nothing is read, rewritten or dropped,
-- and every response written before today simply has no expiry — which is
-- exactly "nobody recorded when these terms die", the state `quotes.undated`
-- exists to carry.
--
-- WHY THE RESPONSE AND NOT THE SUBMISSION. `submission.quote_expires_on` is
-- where the expiry has lived since the quotes queue was built, and it is the
-- only one of the five quote facts on `submission` that had NO home on the
-- row that actually states it: a market quotes a LINE and its terms lapse on
-- its own date, and two carriers answering one package die on two different
-- days. So a premium typed on the Marketing panel could never reach
-- `services.quotes.expiring` — the chase queue whose own module header calls
-- this gap "the only one that loses money rather than time" — because the
-- panel had nowhere to put the date the queue is keyed on (2026-08-26).
--
-- `submission.quote_expires_on` STAYS, as the MIN of these (repo.marketing
-- .roll_up_submission): the earliest lapse is the package's deadline, and a
-- queue that took the latest would let the first quote die quietly. It is a
-- CACHE of the rows, the same standing this book gives proj_* against the
-- towerkit files — rebuildable, never the authority.
ALTER TABLE market_response ADD COLUMN quote_expires_on TEXT;

-- The queue reads MIN(quote_expires_on) per submission on every roll-up.
CREATE INDEX idx_response_expiry ON market_response (submission_id, quote_expires_on);
