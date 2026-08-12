-- 006 — client projects with insurance needs. A project belongs to an
-- account; each need is its own row (line of cover, needed-by date) with
-- OPTIONAL links to the opportunity/placement it became — links form when
-- real, never auto-created. Unmet needed-by dates are attention, like
-- renewals.

CREATE TABLE project (
    id          TEXT PRIMARY KEY,
    ref         TEXT NOT NULL UNIQUE,
    org_id      TEXT NOT NULL REFERENCES org (id),
    name        TEXT NOT NULL,
    description TEXT,
    site        TEXT,               -- free-text location
    status      TEXT NOT NULL DEFAULT 'planned',  -- planned/active/completed/cancelled
    start_on    TEXT,               -- the project's own effective date
    end_on      TEXT,               -- and expiry
    notes       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    deleted_at  TEXT
);
CREATE INDEX idx_project_org ON project (org_id);

CREATE TABLE project_need (
    id                       TEXT PRIMARY KEY,
    project_id               TEXT NOT NULL REFERENCES project (id),
    line                     TEXT NOT NULL,   -- "Builder's Risk", "Wrap-up GL"
    needed_by                TEXT NOT NULL,   -- insurance-needed-by date
    limit_cents              INTEGER,
    premium_indication_cents INTEGER,
    status                   TEXT NOT NULL DEFAULT 'identified',
    opportunity_id           TEXT REFERENCES opportunity (id),
    placement_id             TEXT REFERENCES placement (id),
    notes                    TEXT,
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL,
    deleted_at               TEXT
);
CREATE INDEX idx_need_project ON project_need (project_id);
CREATE INDEX idx_need_needed_by ON project_need (needed_by);
