-- 005 — internal team: who to go to for what, and who covers which account.
-- Team members are colleagues, not org contacts; assignments attach them to a
-- client (account team) or a specific placement (deal team) with a role and
-- the lines they're placing.

CREATE TABLE team_member (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    title      TEXT,
    specialty  TEXT,               -- lines/expertise: "cyber, tech E&O"
    email      TEXT,
    phone      TEXT,
    active     INTEGER NOT NULL DEFAULT 1,
    notes      TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE team_assignment (
    id             TEXT PRIMARY KEY,
    team_member_id TEXT NOT NULL REFERENCES team_member (id),
    org_id         TEXT REFERENCES org (id),
    placement_id   TEXT REFERENCES placement (id),
    role           TEXT,           -- account_lead, placement_specialist, claims_advocate…
    lines          TEXT,           -- which lines they're placing here
    notes          TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    deleted_at     TEXT,
    CHECK ((org_id IS NULL) != (placement_id IS NULL))
);
CREATE INDEX idx_team_assignment_member ON team_assignment (team_member_id);
CREATE INDEX idx_team_assignment_org ON team_assignment (org_id);
CREATE INDEX idx_team_assignment_placement ON team_assignment (placement_id);
