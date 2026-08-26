-- 020 — the vocabularies a broker can edit.
--
-- ADDITIVE ONLY, and deliberately inert. Two new tables; not one existing
-- column is altered, read differently or constrained differently by this file,
-- and nothing in the app reads these tables to make a decision yet. That is
-- phase 1 of the plan Grant approved on 2026-08-26: the shape lands and can
-- sit on main while it is looked at, and the destructive half — swapping each
-- enumerated CHECK for a referential trigger — is a later migration of its own.
--
-- WHY A TABLE AT ALL. Twelve controlled vocabularies live as tuples in
-- models.py and eleven columns pin one behind a `CHECK`, which SQLite cannot
-- widen in place — so adding one word costs a twelve-step table rebuild.
-- Migrations 018 and 019 are two of those in two days, for one word each, and
-- that is the argument rather than an accident.
--
-- WHY THE STORED VALUE DOES NOT MOVE. Every raw column keeps its own TEXT
-- value exactly as it is today: `market_response.status` still holds
-- 'quoted', still filters, still indexes. What arrives is a place to say what
-- 'quoted' READS as, what colour it takes, where it sorts — and, for a value a
-- broker adds, which built-in it BEHAVES AS.

CREATE TABLE list_definition (
    -- '<table>.<column>', which is the one name a definition can have that a
    -- reader and a trigger can both derive rather than look up.
    id          TEXT PRIMARY KEY,
    label       TEXT NOT NULL,      -- 'Market response'
    -- WHAT THIS LIST IS FOR, in the broker's words, shown on the page that
    -- edits it. A vocabulary with no note is one somebody will guess at.
    note        TEXT,
    -- THIS LIST'S WORDS REACH A CLIENT. `public_decline_reason` is printed on
    -- the workbook that leaves the building and `market_response.status` is
    -- printed beside it; an internal note is not. The page that edits a list
    -- has to say which, because the cost of getting it wrong is a client
    -- reading something nobody meant them to (the same split the grid's two
    -- reason columns make in their own headers).
    client_facing INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE list_value (
    -- A SURROGATE id, like every other entity in this book, and not the
    -- composite (list_id, value) it would be natural to key on. `event_log`
    -- addresses a row by `id` and `base.undo` writes a field back to it, so a
    -- table with no `id` cannot be edited revertibly — and an editable
    -- vocabulary whose edits are the one thing `u` cannot take back would be
    -- the exception that proves nothing.
    id          TEXT PRIMARY KEY,
    list_id     TEXT NOT NULL REFERENCES list_definition (id),
    -- WHAT IS STORED IN THE RAW COLUMN, and it is IMMUTABLE. The code keys off
    -- this; a person edits `label`. Renaming a key would be a data migration
    -- across every row that holds it, so the surface will not offer one —
    -- retire the value and add another, the way a line of coverage is merged.
    value       TEXT NOT NULL,
    label       TEXT NOT NULL,      -- what a person reads; editable
    -- ONE OF THE APP'S DECLARED TONES, never a hex. Colour is signal rather
    -- than decoration and palette.py is the one home for what a tone IS, so
    -- this is a choice among the tones the design system already has — and
    -- every tinted value still prints its own word beside the tint.
    tone        TEXT NOT NULL DEFAULT '',
    -- WHERE IT SORTS. Lower first. This is the order a client reads a block
    -- in, so reordering a list reorders the workbook (Grant, 2026-08-26,
    -- confirmed before it was built).
    rank        INTEGER NOT NULL DEFAULT 0,
    -- WHICH BUILT-IN THIS BEHAVES AS — the load-bearing column of the whole
    -- change.
    --
    -- These values are not labels; several of them are RULES. 'quoted' decides
    -- which row leads the premium bridge, 'pending' decides what is chased and
    -- what raises a clearance conflict, 'bound' is the top of the roll-up
    -- ladder, 'outstanding' is what the open-subjectivity count counts. A word
    -- a broker adds cannot teach the book a new rule — so it names one it
    -- inherits, and 'Referred to underwriting' behaves as 'pending'.
    --
    -- A built-in points at ITSELF, and the surface will neither re-point nor
    -- retire one. The self-reference is what makes the foreign key below able
    -- to hold the whole shape with no second table.
    behaves_as  TEXT NOT NULL,
    -- DECLARED IN CODE, seeded from it, and not editable. models.py is still
    -- the one home for the vocabulary a rule is written against; this table
    -- says how it reads and what may be added beside it.
    is_builtin  INTEGER NOT NULL DEFAULT 0,
    -- RETIRE, NEVER DELETE — the rule repo/lines.py already follows for lines
    -- of coverage, and for the same reason: rows recorded against a value do
    -- not stop being true because the book will not take new work under it. A
    -- retired value is still storable, still printed where it was already
    -- used, and simply not offered.
    retired_at  TEXT,
    -- TWO DIFFERENT ENDINGS, and they are not the same fact. `retired_at` is
    -- "the book will not take new work under this word" — the value is still
    -- storable and still printed wherever it was already used, which is what a
    -- vocabulary needs and what a line of coverage already does.
    -- `deleted_at` is "this row should never have existed", which is only ever
    -- true of a value somebody added and nothing ever used. It is here because
    -- every entity in this book carries it: `base.alive()`, `soft_delete`,
    -- the undo replay and the revert planner all read it, and a table in
    -- `ENTITY_TABLES` without one is a row the revert machinery cannot ask
    -- about (found by tests/test_revert_dependents.py the day this landed).
    deleted_at  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    -- The pair is still what the code and the triggers key on, so it is
    -- UNIQUE rather than PRIMARY — which is all a foreign key needs to point
    -- at it.
    UNIQUE (list_id, value),
    -- The self-reference: a value may only behave as another value of the SAME
    -- list, and the pair it names has to exist.
    FOREIGN KEY (list_id, behaves_as) REFERENCES list_value (list_id, value)
);

-- The two reads this table exists for: "what does this list offer" (the
-- pickers, ordered) and "what does this stored value behave as" (every rule),
-- the second of which is the primary key and needs no index.
CREATE INDEX idx_list_value_offer ON list_value (list_id, retired_at, rank);
