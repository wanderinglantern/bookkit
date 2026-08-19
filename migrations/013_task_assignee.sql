-- 013 — the assignee: WHO is chasing this task.
--
-- ADDITIVE ONLY. Three new nullable columns on `task` and one index; no
-- existing row is read, rewritten or constrained differently by this file.
-- Every task written before today reads all three NULL, which is exactly
-- "nobody has said whose this is" — and the client-facing export renders
-- that as `Us`, because unassigned work is ours until someone says
-- otherwise (Grant, 2026-08-18).
--
-- WHY THREE COLUMNS AND NOT ONE NAME. The client's workbook carries an
-- Owner column reading You / Us, derived from this fact. Deriving it by
-- string-matching a name means typing "Sam" instead of "Sam Garcia"
-- silently flips a CLIENT-FACING column to say our firm owns what the
-- client owes us — the silent-wrong-direction failure
-- models.is_internal_category spends fourteen lines refusing. So the
-- resolved case stores an IDENTITY (kind + id) and the export reads the
-- kind; the freeform case stores a name and can never be read as an
-- identity at all.
--
--   assignee_kind  'team' | 'contact' | NULL  (models.AssigneeKind)
--   assignee_id    team_member.id or contact.id — set iff kind is set
--   assignee_name  freeform, set iff kind is NULL — a third party who is
--                  genuinely not a record in this book
--
-- No REFERENCES clause on assignee_id: it points at one of two tables
-- depending on the kind, which SQLite cannot express, and both tables
-- soft-delete rather than delete, so a foreign key would not be the guard
-- it looks like. repo/assignees.py owns the lookup and falls back to the
-- safe side when the row has gone.
--
-- The kind is NOT 'team' | 'client_contact' | 'market_contact'. A contact
-- can be moved between orgs (contacts.reassign_org, on a market merge), so
-- a stored side would go stale and the client's column would then disagree
-- with the book. You / Us is decided at export time by comparing the
-- contact's org to the account being exported — an id comparison, never a
-- name, and never a copy that can drift.

ALTER TABLE task ADD COLUMN assignee_kind TEXT;   -- 'team' | 'contact'
ALTER TABLE task ADD COLUMN assignee_id   TEXT;
ALTER TABLE task ADD COLUMN assignee_name TEXT;   -- freeform fallback

-- (kind, id) in that order: "everything assigned to this person" filters the
-- kind first and then the id, and "everything assigned to anyone at all"
-- reads the leading column alone.
CREATE INDEX idx_task_assignee ON task (assignee_kind, assignee_id);
