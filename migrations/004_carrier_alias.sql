-- 004 — carrier aliases: towerkit files name carriers as free strings; this
-- maps every spelling ("Swiss Reinsurance", "Swiss Re Corporate Solutions")
-- onto the one bookkit market org so cross-book joins never miss. An exact
-- org-name match never needs an alias row.

CREATE TABLE carrier_alias (
    alias         TEXT PRIMARY KEY,   -- the exact string as towerkit files write it
    market_org_id TEXT NOT NULL REFERENCES org (id),
    created_at    TEXT NOT NULL
);
CREATE INDEX idx_carrier_alias_market ON carrier_alias (market_org_id);
