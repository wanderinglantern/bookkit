-- 007 — market families: underwriting companies nest under a master company
-- (Indian Harbor Ins Co under AXA XL). Organizational only: aliases, layers,
-- and submissions keep pointing at the ISSUING entity; nesting shapes the
-- outline views. Additive column, no data rewritten.

ALTER TABLE org ADD COLUMN parent_org_id TEXT REFERENCES org (id);
CREATE INDEX idx_org_parent ON org (parent_org_id);
