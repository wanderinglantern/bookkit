---
name: surface-consequence-review
description: Use after a change that adds a data shape, moves where something is read or done, or lets a thing exist more than once — to find the surfaces the change BROKE WITHOUT TOUCHING. Read it before declaring such a change done, and after a diff review, never instead of one.
---

# Surface consequence review

A diff review reads the lines you changed. This one reads the surfaces your
change REACHED. They find different bugs, and the second kind is the kind that
gets reported by the user.

## Why this exists

Two defects shipped past a full review, a green suite and a mutation check on
2026-08-24, in one afternoon:

- Lines of coverage each arrive with a layer named "To be placed". A program
  with two of them gave the "same policy as" picker **two identical options**.
  The picker's code was not in the diff — it was correct before and correct
  after. What changed was the DATA it renders.
- The structure rail moved from a flat pile to groups per line of coverage.
  Reordering a line stayed on the chips in the band above, so the place the
  structure is now read could not reorder it. Nothing was deleted, so nothing
  looked wrong.

Both are the same shape: **the broken code was not in the diff.** No amount of
care reading the hunks would have found either one. That is the gap this
closes.

## The three questions

Each one produces a LIST, and the review is walking it. Answer them about the
change, not about the code.

### 1. What can now exist that could not before?

A second thing with the same name. A layer with no participants. A premium
that is STATED rather than derived. A line with no layers. Two rows sharing a
date. A period that spans a year end.

For each new shape, `grep` for every surface that RENDERS it, CHOOSES between
instances of it, KEYS on it, SORTS it or TOTALS it — then ask of each:

- **Distinguishable?** Two options, rows or chips that print the same text are
  a control nobody can use, however correct the id underneath.
- **Still totals?** A figure that could only be derived and can now be stated
  is double counted by any reader that still derives it.
- **Still keys?** A table keyed on something that is no longer unique raises
  on the second row — and blanks the screen.
- **Still sorts?** Order that was incidental becomes visible when a second
  instance appears.

### 2. What MOVED?

If the change relocates where something is read, worked or decided, list every
affordance available at the OLD home and check each one exists at the new one.

Deletion is visible in a diff; relocation is not. The old home still works, so
nothing fails — the user simply cannot do at the new place what the new place
is now for.

### 3. What became derived, or plural?

Every reader that computes the thing itself is now wrong. `grep` for the
ARITHMETIC, not the field name: `* share`, `// BPS_SCALE`, `sum(`, the
division you just replaced. A stated value is invisible to a surface that
keeps multiplying.

## Then: test or note?

For every finding, decide which it is, and prefer the first:

- **Statable as an invariant over all surfaces → a TEST.** "No select renders
  two options with the same label" holds forever, over pages nobody has
  written yet. This project's whole culture is converting a finding into a
  gate (`test_dead_keys`, `web/parity.py`, the mcpsurface reviewed-count).
- **Only true of this surface → a NOTE**, with the reason, in the place the
  next person will look — which is usually a comment beside the code, not a
  review document nobody opens twice.

## Evidence, not suspicion

Reproduce every finding against the RUNNING app before reporting it. On
2026-08-24 a "panel not refreshing" report was diagnosed from the code, fixed,
and the fix was wrong: the panel refreshes correctly, and driving the real
route proved it in one call. A review that reports plausible bugs costs more
than one that reports fewer real ones.

If a report cannot be reproduced, say so and ask for the exact click. Do not
ship a fix for a bug you have not seen.
