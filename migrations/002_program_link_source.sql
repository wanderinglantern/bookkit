-- 002 — link provenance: how each file ↔ org link came to be.
--   'user'          confirmed by hand in the review queue
--   'insured_match' auto: insured string byte-identical to a prior user confirmation
--   'rename'        auto: file content hash identical to an already-linked file
--   'renewal'       created by the renew action (clone_as_renewal)
-- Additive only; existing rows were all user-confirmed.

ALTER TABLE program_link ADD COLUMN source TEXT NOT NULL DEFAULT 'user';
