-- 014 — a line of coverage becomes a THING, not five spellings of a string.
--
-- ADDITIVE ONLY. One new table and one nullable column on each of the two
-- tables that name a single line; nothing existing is read, rewritten or
-- constrained differently by this file. The two tables that name SEVERAL
-- lines (opportunity.lines, team_assignment.lines — comma-joined blobs) get
-- join tables rather than a column, because a list is not a foreign key.
-- Every legacy text column stays exactly as it is and keeps being the value
-- the surfaces read until its review queue is empty; dropping them is a
-- separate, later, explicit act.
--
-- WHY THIS TABLE EXISTS AT ALL. `repo/vocab.py::lines()` is the confession:
-- it unions FOUR free-text columns to answer "what does this book call its
-- lines", and nothing reconciles them. "GL", "General Liability" and "Gen
-- Liab" are three different values in three tables, and towerkit's
-- `Program.lines[]` — the only structured one — is a fifth spelling in a
-- different store. A marketing report grouped BY LINE OF COVERAGE cannot be
-- built on that: the grouping key does not exist (Grant, 2026-08-25).
--
-- THE ID IS A SLUG, NOT A ULID. Every other entity here mints a ULID because
-- its rows are the user's data. These rows are VOCABULARY: they are the same
-- in every copy of the book, they are referenced from towerkit `Line.id`
-- strings that a human typed into a program file, and they need to be
-- greppable in a migration, a test and a seed alike. A stable slug is the
-- identity; `name` is what a client reads and may be renamed freely without
-- breaking a single reference.
--
-- ACORD CODES ARE FILLED ONLY WHERE THEY ARE UNAMBIGUOUS. CGL, WORK and
-- INMRC are the three this project has confirmed against ACORD's published
-- P&C codelists. The rest are left NULL on purpose: a guessed code is worse
-- than an absent one, because the whole point of carrying the code is stable
-- identity for interchange, and an invented one interchanges wrongly and
-- silently. `acord_code` is NEVER the display name.

CREATE TABLE line_of_coverage (
    id          TEXT PRIMARY KEY,   -- stable slug: 'general-liability'
    name        TEXT NOT NULL,      -- what a client reads: "General Liability"
    abbr        TEXT,               -- "GL" — the column header on a wide grid
    acord_code  TEXT,               -- "CGL" — identity for interchange, not display
    sort_order  INTEGER NOT NULL DEFAULT 0,
    notes       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    deleted_at  TEXT
);

-- Name uniqueness is enforced HERE and not in a caller, for the reason
-- repo/team.py already carries in its own comment: a guard on identity that
-- lives in one surface is a guard the other surfaces write straight past.
-- Case-insensitive, because "general liability" and "General Liability" are
-- the same line and admitting both recreates the exact mess this table
-- replaces. Partial on the soft-delete so a retired line's name frees up.
CREATE UNIQUE INDEX idx_line_name_unique
    ON line_of_coverage (LOWER(name)) WHERE deleted_at IS NULL;

CREATE INDEX idx_line_sort ON line_of_coverage (sort_order, name);

-- --- the two single-line columns ----------------------------------------
--
-- Nullable, beside the text column rather than replacing it. NULL reads as
-- "not yet mapped", which is honest and is where every row starts; the
-- backfill fills only what it is confident about and the web review queue
-- fills the rest by hand. No row is guessed and no string is destroyed.

ALTER TABLE appetite     ADD COLUMN line_id TEXT REFERENCES line_of_coverage (id);
ALTER TABLE project_need ADD COLUMN line_id TEXT REFERENCES line_of_coverage (id);

CREATE INDEX idx_appetite_line_id     ON appetite (line_id);
CREATE INDEX idx_project_need_line_id ON project_need (line_id);

-- --- the two multi-line columns -----------------------------------------
--
-- `opportunity.lines` and `team_assignment.lines` hold COMMA-JOINED LISTS.
-- A single FK column cannot hold a list, and splitting on commas at every
-- read site is how the current mess got here. Join tables, so a line can be
-- added to or removed from an opportunity without rewriting a blob and
-- without a parser.

CREATE TABLE opportunity_line (
    opportunity_id TEXT NOT NULL REFERENCES opportunity (id),
    line_id        TEXT NOT NULL REFERENCES line_of_coverage (id),
    PRIMARY KEY (opportunity_id, line_id)
);
CREATE INDEX idx_opportunity_line_line ON opportunity_line (line_id);

CREATE TABLE team_assignment_line (
    team_assignment_id TEXT NOT NULL REFERENCES team_assignment (id),
    line_id            TEXT NOT NULL REFERENCES line_of_coverage (id),
    PRIMARY KEY (team_assignment_id, line_id)
);
CREATE INDEX idx_team_assignment_line_line ON team_assignment_line (line_id);

-- --- the standard commercial set ----------------------------------------
--
-- Seeded here rather than in seed.py because this is VOCABULARY, not sample
-- data: a real book needs these rows on the day it migrates, and seed.py is
-- only ever run against a demo database. Timestamps are literal because a
-- migration has no clock of its own; the value is the date this shipped.
-- The book's OWN lines — whatever the four text columns already say — are
-- added by the backfill, not by this file, because they are data.

INSERT INTO line_of_coverage (id, name, abbr, acord_code, sort_order, created_at, updated_at) VALUES
    ('general-liability',      'General Liability',              'GL',   'CGL',   10, '2026-08-25', '2026-08-25'),
    ('auto',                   'Commercial Auto',                'AL',   NULL,    20, '2026-08-25', '2026-08-25'),
    ('workers-compensation',   'Workers'' Compensation',         'WC',   'WORK',  30, '2026-08-25', '2026-08-25'),
    ('employers-liability',    'Employers Liability',            'EL',   NULL,    40, '2026-08-25', '2026-08-25'),
    ('umbrella',               'Umbrella',                       'UMB',  NULL,    50, '2026-08-25', '2026-08-25'),
    ('excess-liability',       'Excess Liability',               'XS',   NULL,    60, '2026-08-25', '2026-08-25'),
    ('property',               'Property',                       'PROP', NULL,    70, '2026-08-25', '2026-08-25'),
    ('inland-marine',          'Inland Marine',                  'IM',   'INMRC', 80, '2026-08-25', '2026-08-25'),
    ('builders-risk',          'Builder''s Risk',                'BR',   NULL,    90, '2026-08-25', '2026-08-25'),
    ('crime',                  'Crime',                          'CR',   NULL,   100, '2026-08-25', '2026-08-25'),
    ('cyber',                  'Cyber Liability',                'CYB',  NULL,   110, '2026-08-25', '2026-08-25'),
    ('professional-liability', 'Professional Liability',         'PL',   NULL,   120, '2026-08-25', '2026-08-25'),
    ('directors-officers',     'Directors & Officers',           'D&O',  NULL,   130, '2026-08-25', '2026-08-25'),
    ('employment-practices',   'Employment Practices Liability', 'EPL',  NULL,   140, '2026-08-25', '2026-08-25'),
    ('fiduciary',              'Fiduciary Liability',            'FID',  NULL,   150, '2026-08-25', '2026-08-25'),
    ('pollution',              'Pollution Liability',            'POLL', NULL,   160, '2026-08-25', '2026-08-25'),
    ('surety',                 'Surety',                         'SUR',  NULL,   170, '2026-08-25', '2026-08-25');
