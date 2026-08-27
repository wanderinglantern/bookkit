-- 021 — a market's condition can name the ask that will answer it.
--
-- ONE ASK, THREE MARKETS (Grant, 2026-08-27). Three markets asking for
-- five-year loss runs is ONE question to the client, and until now it was
-- three: a subjectivity and an RFI item are the same shape — the Subjectivity
-- model's own docstring says so, "shaped on RfiItem, the other chaseable line
-- item in the book" — with nothing joining them. "What is blocking this
-- placement" therefore had no single answer: the conditions sat under the
-- marketing packages, the asks sat under the requests panel, and the only
-- thing holding the two together was the broker.
--
-- ADDITIVE ONLY. One nullable column on one table. Nothing existing is read,
-- rewritten or constrained differently by this file, and every row starts
-- NULL — which reads as "nobody has been asked for this yet", exactly where
-- every subjectivity recorded before today honestly stands. The same shape 014
-- used for `line_id`.
--
-- THE COLUMN IS ON THE **MANY** SIDE, and that is the whole design rather than
-- a storage detail. Many subjectivities point at ONE rfi_item, because the
-- duplication worth removing is the duplication the CLIENT experiences: AIG,
-- Chubb and Travelers each want loss runs, and the broker asks once. A
-- one-to-one link, or a column on rfi_item, would model the tidier thing and
-- solve nothing — it is the DRY rule pointed at the person rather than at the
-- code (CLAUDE.md: "a fact the user has already given is not asked for
-- twice").
--
-- NOTHING IS MERGED. The two rows keep their own vocabularies and must: an RFI
-- item is outstanding / received / waived, because a DOCUMENT ARRIVES; a
-- subjectivity is outstanding / met / waived, because a CONDITION IS
-- SATISFIED — several are satisfied by an inspection happening or a warranty
-- being signed, with nothing to receive. Collapsing them into one table with
-- an `asked_by` column was considered and rejected: it forces one status
-- vocabulary onto two genuinely different facts, and it is a destructive
-- migration across two live tables for a join a nullable FK already gives.
--
-- RECEIVED IS NOT MET, and no constraint here pretends otherwise. The client
-- sending loss runs does not satisfy AIG's condition — AIG having them and
-- accepting them does. `services/rfi.py` owns that rule; this column only
-- records which ask a condition is waiting on.
--
-- NO CASCADE, DELIBERATELY. There is no ON DELETE clause because both tables
-- are SOFT-deleted (`deleted_at`), so a removed item is still a row and the
-- link still resolves — which is what makes an undo put the pair back
-- together. `services.rfi.remove_item` unlinks in the same batch instead, so
-- a subjectivity is never left pointing at an ask nobody can see.

ALTER TABLE submission_subjectivity ADD COLUMN rfi_item_id TEXT
    REFERENCES rfi_item (id);

-- The read this exists for is "every subjectivity waiting on THIS ask", which
-- `rfi.mark_received` runs to name what an arriving answer unblocks.
CREATE INDEX idx_subjectivity_rfi_item
    ON submission_subjectivity (rfi_item_id);
