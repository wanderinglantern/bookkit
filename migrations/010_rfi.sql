-- Information requests (RFIs): batches of questions and document requests
-- a client owes us. Additive only: two new tables, nothing existing touched.
CREATE TABLE rfi_request (
    id             TEXT PRIMARY KEY,
    ref            TEXT NOT NULL UNIQUE,
    org_id         TEXT NOT NULL REFERENCES org (id),
    placement_id   TEXT REFERENCES placement (id),
    project_id     TEXT REFERENCES project (id),
    market_org_id  TEXT REFERENCES org (id),
    title          TEXT NOT NULL,
    requested_on   TEXT NOT NULL,
    due_on         TEXT,
    notes          TEXT,
    cancelled_at   TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    deleted_at     TEXT,
    CHECK (placement_id IS NULL OR project_id IS NULL)
);
CREATE INDEX idx_rfi_request_org ON rfi_request (org_id);
CREATE INDEX idx_rfi_request_due ON rfi_request (due_on);

CREATE TABLE rfi_item (
    id           TEXT PRIMARY KEY,
    request_id   TEXT NOT NULL REFERENCES rfi_request (id),
    kind         TEXT NOT NULL DEFAULT 'question',
    prompt       TEXT NOT NULL,
    detail       TEXT,
    category     TEXT,
    due_on       TEXT,
    response     TEXT,
    received_on  TEXT,
    status       TEXT NOT NULL DEFAULT 'outstanding',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    deleted_at   TEXT
);
CREATE INDEX idx_rfi_item_request ON rfi_item (request_id);
CREATE INDEX idx_rfi_item_status ON rfi_item (status);
