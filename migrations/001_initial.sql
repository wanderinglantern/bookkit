-- 001_initial — full §3 data model.
-- Conventions: ULID text primary keys; money INTEGER cents; DATE columns are
-- YYYY-MM-DD text (no timezone); timestamps are UTC ISO-8601 text; soft delete
-- via deleted_at.

CREATE TABLE ref_counter (
    kind TEXT PRIMARY KEY,          -- 'ACC' | 'OPP' | 'PLC'
    next INTEGER NOT NULL
);

CREATE TABLE org (
    id          TEXT PRIMARY KEY,
    ref         TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL CHECK (kind IN ('client', 'market', 'other')),
    name        TEXT NOT NULL,
    legal_name  TEXT,
    domain      TEXT,
    status      TEXT NOT NULL DEFAULT 'prospect'
                CHECK (status IN ('prospect', 'active', 'dormant', 'lost', 'declined')),
    industry    TEXT,
    naics       TEXT,
    owner       TEXT,
    hq_city     TEXT,
    hq_country  TEXT,
    website     TEXT,
    notes       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    deleted_at  TEXT
);
CREATE INDEX idx_org_kind_status ON org (kind, status);
CREATE INDEX idx_org_name ON org (name);

CREATE TABLE market_profile (
    org_id         TEXT PRIMARY KEY REFERENCES org (id),
    am_best_rating TEXT,
    naic_number    TEXT,
    market_type    TEXT CHECK (market_type IN
                       ('carrier', 'mga', 'wholesaler', 'reinsurer', 'lloyds')),
    notes          TEXT
);

CREATE TABLE appetite (
    id                TEXT PRIMARY KEY,
    market_org_id     TEXT NOT NULL REFERENCES org (id),
    line              TEXT NOT NULL,
    class_of_business TEXT,
    appetite          TEXT NOT NULL
                      CHECK (appetite IN ('target', 'will_consider', 'selective', 'no')),
    min_premium       INTEGER,        -- cents
    max_limit         INTEGER,        -- cents
    territories       TEXT,
    notes             TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    deleted_at        TEXT
);
CREATE INDEX idx_appetite_market ON appetite (market_org_id);
CREATE INDEX idx_appetite_line ON appetite (line, appetite);

CREATE TABLE contact (
    id         TEXT PRIMARY KEY,
    org_id     TEXT NOT NULL REFERENCES org (id),
    first_name TEXT NOT NULL,
    last_name  TEXT NOT NULL,
    title      TEXT,
    role       TEXT,                  -- controlled vocabulary, extensible
    email      TEXT,
    phone      TEXT,
    mobile     TEXT,
    linkedin   TEXT,
    is_primary INTEGER NOT NULL DEFAULT 0,
    active     INTEGER NOT NULL DEFAULT 1,
    notes      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX idx_contact_org ON contact (org_id);

CREATE TABLE interaction (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES org (id),
    type        TEXT NOT NULL
                CHECK (type IN ('call', 'meeting', 'email', 'note', 'site_visit', 'event')),
    occurred_on TEXT NOT NULL,        -- DATE
    occurred_at TEXT,                 -- optional timestamp
    subject     TEXT NOT NULL,
    body        TEXT,
    sentiment   TEXT CHECK (sentiment IN ('pos', 'neu', 'neg') OR sentiment IS NULL),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    deleted_at  TEXT
);
CREATE INDEX idx_interaction_org_date ON interaction (org_id, occurred_on);

CREATE TABLE interaction_contact (
    interaction_id TEXT NOT NULL REFERENCES interaction (id),
    contact_id     TEXT NOT NULL REFERENCES contact (id),
    PRIMARY KEY (interaction_id, contact_id)
);

CREATE TABLE placement (
    id             TEXT PRIMARY KEY,
    ref            TEXT NOT NULL UNIQUE,
    org_id         TEXT NOT NULL REFERENCES org (id),
    program_name   TEXT NOT NULL,
    period_from    TEXT NOT NULL,     -- DATE
    period_to      TEXT NOT NULL,     -- DATE
    status         TEXT NOT NULL DEFAULT 'prospective'
                   CHECK (status IN ('prospective', 'submitted', 'quoted', 'bound', 'lapsed')),
    total_limit    INTEGER,           -- cents
    total_premium  INTEGER,           -- cents
    currency       TEXT NOT NULL DEFAULT 'USD',
    commission_bps INTEGER,
    program_path   TEXT,              -- towerkit JSON path, if linked
    source_sha256  TEXT,              -- hash of that file when last projected
    synced_at      TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    deleted_at     TEXT
);
CREATE INDEX idx_placement_org ON placement (org_id);
CREATE INDEX idx_placement_expiry ON placement (period_to, status);

CREATE TABLE task (
    id                    TEXT PRIMARY KEY,
    org_id                TEXT REFERENCES org (id),
    title                 TEXT NOT NULL,
    detail                TEXT,
    due_on                TEXT,       -- DATE
    status                TEXT NOT NULL DEFAULT 'open'
                          CHECK (status IN ('open', 'done', 'dropped')),
    priority              INTEGER NOT NULL DEFAULT 2,   -- 1 high / 2 normal / 3 low
    source_interaction_id TEXT REFERENCES interaction (id),
    placement_id          TEXT REFERENCES placement (id),
    completed_at          TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    deleted_at            TEXT
);
CREATE INDEX idx_task_due ON task (status, due_on);
CREATE INDEX idx_task_org ON task (org_id);

CREATE TABLE opportunity (
    id               TEXT PRIMARY KEY,
    ref              TEXT NOT NULL UNIQUE,
    org_id           TEXT NOT NULL REFERENCES org (id),
    title            TEXT NOT NULL,
    lines            TEXT,
    stage            TEXT NOT NULL DEFAULT 'identified'
                     CHECK (stage IN ('identified', 'qualified', 'submitted',
                                      'quoted', 'presented', 'won', 'lost')),
    target_premium   INTEGER,         -- cents
    target_effective TEXT,            -- DATE
    probability_pct  INTEGER NOT NULL DEFAULT 50 CHECK (probability_pct BETWEEN 0 AND 100),
    source           TEXT,
    incumbent_broker TEXT,
    competitor       TEXT,
    closed_at        TEXT,
    outcome          TEXT CHECK (outcome IN ('won', 'lost', 'no_decision') OR outcome IS NULL),
    loss_reason      TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    deleted_at       TEXT
);
CREATE INDEX idx_opportunity_org ON opportunity (org_id);
CREATE INDEX idx_opportunity_stage ON opportunity (stage);

CREATE TABLE submission (
    id                     TEXT PRIMARY KEY,
    placement_id           TEXT REFERENCES placement (id),
    opportunity_id         TEXT REFERENCES opportunity (id),
    market_org_id          TEXT NOT NULL REFERENCES org (id),
    underwriter_contact_id TEXT REFERENCES contact (id),
    sent_on                TEXT NOT NULL,   -- DATE
    status                 TEXT NOT NULL DEFAULT 'out'
                           CHECK (status IN ('out', 'quoted', 'declined', 'bound', 'withdrawn')),
    quoted_premium         INTEGER,         -- cents
    quoted_limit           INTEGER,         -- cents
    response_on            TEXT,            -- DATE
    decline_reason         TEXT,
    notes                  TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    deleted_at             TEXT,
    CHECK ((placement_id IS NULL) != (opportunity_id IS NULL))
);
CREATE INDEX idx_submission_market ON submission (market_org_id, status);
CREATE INDEX idx_submission_placement ON submission (placement_id);
CREATE INDEX idx_submission_opportunity ON submission (opportunity_id);

CREATE TABLE document (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES org (id),
    placement_id TEXT REFERENCES placement (id),
    kind         TEXT,
    title        TEXT NOT NULL,
    path         TEXT NOT NULL,
    added_at     TEXT NOT NULL,
    deleted_at   TEXT
);
CREATE INDEX idx_document_org ON document (org_id);

-- Append-only audit trail; answers "when did this renewal date move, and why".
CREATE TABLE event_log (
    id          TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id   TEXT NOT NULL,
    field       TEXT NOT NULL,
    old_value   TEXT,
    new_value   TEXT,
    changed_at  TEXT NOT NULL,
    note        TEXT
);
CREATE INDEX idx_event_entity ON event_log (entity_type, entity_id, changed_at);

-- Confirmed towerkit-file ↔ org links (§5.2). Never guessed silently.
CREATE TABLE program_link (
    path         TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES org (id),
    insured_name TEXT NOT NULL,
    confirmed_at TEXT NOT NULL
);

-- Projection cache from towerkit JSON (§5) — derived, never edited directly.
CREATE TABLE proj_layer (
    placement_id TEXT NOT NULL REFERENCES placement (id),
    layer_id     TEXT NOT NULL,
    name         TEXT NOT NULL,
    applies_to   TEXT NOT NULL,       -- comma-joined line ids
    attach       INTEGER NOT NULL,    -- cents
    lim          INTEGER NOT NULL,    -- cents ("limit" is fine in SQLite but confusing in code)
    premium      INTEGER,             -- cents
    synced_at    TEXT NOT NULL,
    PRIMARY KEY (placement_id, layer_id)
);

CREATE TABLE proj_participant (
    placement_id TEXT NOT NULL REFERENCES placement (id),
    layer_id     TEXT NOT NULL,
    carrier      TEXT NOT NULL,
    share_bps    INTEGER NOT NULL,
    premium      INTEGER,             -- cents, floor-divided share of layer premium
    synced_at    TEXT NOT NULL,
    PRIMARY KEY (placement_id, layer_id, carrier)
);
CREATE INDEX idx_proj_participant_carrier ON proj_participant (carrier);

CREATE TABLE proj_retention (
    placement_id TEXT NOT NULL REFERENCES placement (id),
    idx          INTEGER NOT NULL,
    applies_to   TEXT NOT NULL,
    type         TEXT NOT NULL,
    amount       INTEGER NOT NULL,    -- cents
    aggregate    INTEGER,             -- cents
    vehicle      TEXT,
    synced_at    TEXT NOT NULL,
    PRIMARY KEY (placement_id, idx)
);

-- Quick-capture drafts survive crashes (§6.2 "never lose typed text").
CREATE TABLE draft (
    id         TEXT PRIMARY KEY,
    screen     TEXT NOT NULL,
    payload    TEXT NOT NULL,         -- JSON of the half-typed form
    saved_at   TEXT NOT NULL
);

-- §3.3 global search. External-content FTS index per searchable table,
-- kept current by triggers; soft-deleted rows drop out of the index.
CREATE VIRTUAL TABLE fts_org USING fts5(
    name, legal_name, notes, content='org', content_rowid='rowid'
);
-- Index rows only while deleted_at IS NULL; every 'delete' command must match
-- an earlier insert exactly or FTS5 external content corrupts.
CREATE TRIGGER org_ai AFTER INSERT ON org WHEN new.deleted_at IS NULL BEGIN
    INSERT INTO fts_org(rowid, name, legal_name, notes)
    VALUES (new.rowid, new.name, new.legal_name, new.notes);
END;
CREATE TRIGGER org_ad AFTER DELETE ON org WHEN old.deleted_at IS NULL BEGIN
    INSERT INTO fts_org(fts_org, rowid, name, legal_name, notes)
    VALUES ('delete', old.rowid, old.name, old.legal_name, old.notes);
END;
CREATE TRIGGER org_au AFTER UPDATE ON org BEGIN
    INSERT INTO fts_org(fts_org, rowid, name, legal_name, notes)
    SELECT 'delete', old.rowid, old.name, old.legal_name, old.notes
    WHERE old.deleted_at IS NULL;
    INSERT INTO fts_org(rowid, name, legal_name, notes)
    SELECT new.rowid, new.name, new.legal_name, new.notes
    WHERE new.deleted_at IS NULL;
END;

CREATE VIRTUAL TABLE fts_contact USING fts5(
    first_name, last_name, title, notes, content='contact', content_rowid='rowid'
);
CREATE TRIGGER contact_ai AFTER INSERT ON contact WHEN new.deleted_at IS NULL BEGIN
    INSERT INTO fts_contact(rowid, first_name, last_name, title, notes)
    VALUES (new.rowid, new.first_name, new.last_name, new.title, new.notes);
END;
CREATE TRIGGER contact_ad AFTER DELETE ON contact WHEN old.deleted_at IS NULL BEGIN
    INSERT INTO fts_contact(fts_contact, rowid, first_name, last_name, title, notes)
    VALUES ('delete', old.rowid, old.first_name, old.last_name, old.title, old.notes);
END;
CREATE TRIGGER contact_au AFTER UPDATE ON contact BEGIN
    INSERT INTO fts_contact(fts_contact, rowid, first_name, last_name, title, notes)
    SELECT 'delete', old.rowid, old.first_name, old.last_name, old.title, old.notes
    WHERE old.deleted_at IS NULL;
    INSERT INTO fts_contact(rowid, first_name, last_name, title, notes)
    SELECT new.rowid, new.first_name, new.last_name, new.title, new.notes
    WHERE new.deleted_at IS NULL;
END;

CREATE VIRTUAL TABLE fts_interaction USING fts5(
    subject, body, content='interaction', content_rowid='rowid'
);
CREATE TRIGGER interaction_ai AFTER INSERT ON interaction WHEN new.deleted_at IS NULL BEGIN
    INSERT INTO fts_interaction(rowid, subject, body)
    VALUES (new.rowid, new.subject, new.body);
END;
CREATE TRIGGER interaction_ad AFTER DELETE ON interaction WHEN old.deleted_at IS NULL BEGIN
    INSERT INTO fts_interaction(fts_interaction, rowid, subject, body)
    VALUES ('delete', old.rowid, old.subject, old.body);
END;
CREATE TRIGGER interaction_au AFTER UPDATE ON interaction BEGIN
    INSERT INTO fts_interaction(fts_interaction, rowid, subject, body)
    SELECT 'delete', old.rowid, old.subject, old.body
    WHERE old.deleted_at IS NULL;
    INSERT INTO fts_interaction(rowid, subject, body)
    SELECT new.rowid, new.subject, new.body
    WHERE new.deleted_at IS NULL;
END;
