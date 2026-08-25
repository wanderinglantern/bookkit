-- 015 — what a market SAID, about WHICH line of coverage.
--
-- ADDITIVE ONLY. Two new tables; `submission` is not altered, not read and
-- not constrained differently by this file. Every submission written before
-- today simply has no response rows, which is exactly "nobody recorded what
-- came back line by line" — not a guess, and not an empty answer.
--
-- WHY A CHILD TABLE AND NOT A COLUMN ON `submission`. A market is approached
-- ONCE with a whole submission package and answers LINE BY LINE: quoting the
-- GL, declining the Auto, silent on the Umbrella. One submission per line
-- would invent three records for one email and make "who did we approach"
-- unanswerable; one row per submission cannot hold three different answers
-- (Grant, 2026-08-25). `submission.status` becomes a ROLL-UP of these rows
-- rather than a separately typed field — two hand-maintained copies of one
-- fact disagree, and then nobody knows which is right.
--
-- WHY THE CARRIER IS NULLABLE. You send to RT Specialty and THEY come back
-- with CNA: at the moment the row is created the intermediary is known and
-- the paper is not. `market_org_id` is the paper, `via_org_id` the
-- wholesaler or MGA, and the CHECK requires only that one of them be there.
-- A row with only `via_org_id` reads "out to RT Specialty, carrier TBD",
-- which is the truth and prints as exactly that. Filling the paper in later
-- does not change the row's identity, so its event_log history survives —
-- which re-pointing a row created against the wholesaler would not.
--
-- WHY BOTH ORGS AND NOT ONE. Recording both is what lets the book SEE that
-- RT Specialty and Amwins are both reaching for CNA on the same placement
-- and line — the clearance collision that gets one of them shut out. It is
-- surfaced as a WARNING and never a refusal, for the same reason `line-gap`
-- is: the double approach is sometimes deliberate, and a hard block makes a
-- legitimate entry impossible.

CREATE TABLE market_response (
    id            TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL REFERENCES submission (id),
    line_id       TEXT NOT NULL REFERENCES line_of_coverage (id),
    market_org_id TEXT REFERENCES org (id),   -- the paper; NULL = carrier TBD
    via_org_id    TEXT REFERENCES org (id),   -- wholesaler / MGA in the chain

    -- WHICH SLAB THIS ANSWER IS ABOUT. An excess market quotes "$10M xs $10M
    -- at $95K", and the same carrier can answer twice on one line at two
    -- attachments. NULL attach reads as primary / the whole line, which is
    -- the ordinary case; without these two columns the report cannot
    -- describe a TOWER being marketed, which is most of the work.
    attach        INTEGER,   -- cents
    lim           INTEGER,   -- cents ("limit" is fine in SQLite, confusing in code)

    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'indicated', 'quoted',
                                    'declined', 'non_response', 'bound')),
    responded_on  TEXT,      -- DATE

    -- THE RATING BASIS IS THREE FACTS. What is MEASURED (rating_basis), the
    -- DENOMINATOR the rate is quoted per (rate_per: 100, 1000, or 1), and
    -- the exposure figure. Left implied, a rate column is uninterpretable
    -- the first time a reader assumes the wrong convention — and the
    -- conventions genuinely differ by line (GL per $1,000 of sales, WC per
    -- $100 of payroll, auto per power unit).
    --
    -- All three are OVERRIDES of the figures on placement_line: a carrier
    -- that used its own audit assumption can say so, and one that did not
    -- leaves them NULL rather than repeating the same $48.5M six times.
    --
    -- `exposure_amount` holds CENTS when the basis is monetary and a whole
    -- COUNT when it is not. models.RatingBasis.monetary is the ONE place
    -- that decides which, so no read site has to judge it: a fleet is 42
    -- power units, and 42 cannot be cents.
    rating_basis    TEXT,
    rate_per        INTEGER,
    exposure_amount INTEGER,
    rate_micros     INTEGER,   -- rate per unit x 1,000,000; a rate is not money

    -- THE PREMIUM AS THE CARRIER STATED IT, never normalised on entry.
    -- Carriers round, apply minimum premiums and expense constants, and add
    -- TRIA on their own terms; a premium this book computed will disagree
    -- with the carrier's own number and somebody will be explaining the
    -- difference to a client. Grant's convention (2026-08-25): the figure is
    -- INCLUSIVE of commission and NET of fees and TRIA — so "net" here means
    -- net of fees, not net of commission, and `commission_included` carries
    -- the exception rather than a flag that means two things.
    --
    -- The three siblings are NULL when nobody has quoted them, which is the
    -- state most of a marketing cycle is in. NULL IS NOT ZERO: a report that
    -- prints $0 of surplus lines tax is making a claim nobody made, and on
    -- E&S business through a wholesaler that tax is what decides which
    -- placement is actually cheaper.
    premium             INTEGER,   -- cents
    commission_bps      INTEGER,
    commission_included INTEGER NOT NULL DEFAULT 1,
    tria_premium        INTEGER,   -- cents
    policy_fees         INTEGER,   -- cents
    surplus_lines_tax   INTEGER,   -- cents

    -- TWO DECLINE REASONS, NOT ONE FIELD WITH A "SAFE TO SHARE" FLAG. Real
    -- reasons are routinely unusable verbatim ("underwriter doesn't like the
    -- loss runs, off the record"), and a checkbox guarding a single field
    -- fails the first time somebody forgets to tick it — a failure whose
    -- consequence is a client reading an underwriter's private opinion.
    -- `decline_reason_public` takes models.PUBLIC_DECLINE_REASONS and is
    -- OPTIONAL: blank says nothing, which is safer than a sentence anyone
    -- will wish they had not written.
    decline_reason        TEXT,   -- internal; NEVER rendered to a client
    decline_reason_public TEXT,   -- controlled vocabulary; client-facing

    notes      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT,

    CHECK (market_org_id IS NOT NULL OR via_org_id IS NOT NULL)
);

CREATE INDEX idx_response_submission ON market_response (submission_id);
CREATE INDEX idx_response_line       ON market_response (line_id);
CREATE INDEX idx_response_market     ON market_response (market_org_id);
-- The clearance check: "is anyone else already reaching for this carrier on
-- this line". Carrier first, because that is what is being cleared.
CREATE INDEX idx_response_clearance  ON market_response (market_org_id, line_id, status);

-- --- what the line is EXPECTED to do -------------------------------------
--
-- The client's comparator lives once per line, not once per market. Repeating
-- the expiring premium identically down a column is the duplication the DRY
-- rule names, and it belongs in the block header where it answers "is this
-- good?" for every row beneath it at once.
--
-- WHY THE EXPIRING RATE IS STORED AND NOT DERIVED FROM THE PREMIUM. Premium
-- moves because EXPOSURE moves. A book whose sales grew 18% while its rate
-- fell 19% shows a 4.6% premium saving and hides the whole story; rate is
-- what the market talks in and what the broker actually achieved. Deriving
-- the expiring rate needs the expiring EXPOSURE, which is a separate fact
-- nobody may have recorded — so it is its own column and the report leaves
-- the comparison BLANK when it is missing rather than assuming exposure was
-- flat. An unlabelled flat-exposure assumption puts a number in front of a
-- client that looks like rate change and is not.
--
-- `expiring_basis` is separate from `rating_basis` because last year may have
-- been rated differently; where the two disagree the report says "basis
-- changed" and prints no percentage. A rate change across incomparable bases
-- is the same lie as comparing two carriers on different bases.

CREATE TABLE placement_line (
    id           TEXT PRIMARY KEY,
    placement_id TEXT NOT NULL REFERENCES placement (id),
    line_id      TEXT NOT NULL REFERENCES line_of_coverage (id),

    expiring_premium     INTEGER,   -- cents
    expiring_exposure    INTEGER,
    expiring_rate_micros INTEGER,
    expiring_basis       TEXT,

    expected_exposure INTEGER,
    rating_basis      TEXT,
    rate_per          INTEGER,

    attach_sought INTEGER,   -- cents
    limit_sought  INTEGER,   -- cents

    notes      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

-- One row per line per placement. Partial on the soft-delete, so a line
-- removed from a placement and added back does not collide with its own
-- retired row.
CREATE UNIQUE INDEX idx_placement_line_unique
    ON placement_line (placement_id, line_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_placement_line_line ON placement_line (line_id);
