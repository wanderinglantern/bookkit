-- 008 — task description: brief one-line summary between title and long-form detail.
-- Additive-only column; existing rows read NULL.

ALTER TABLE task ADD COLUMN description TEXT;
