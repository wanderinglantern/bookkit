# Handoff — bookkit + towerkit, overnight run 2026-08-18

Written to be resumed cold, mid-flight. Grant is asleep; he has answered everything that blocks.
**Read `ROADMAP.md`'s entry "Overnight plan, 2026-08-18" first** — it carries his four answers and
my standing rulings, and it is the authority if this file and it disagree.

---

## 1. State

| repo | main | suite |
|---|---|---|
| bookkit | `578c3ca`, pushed | **1079 passed**, mypy clean (114 files), ruff clean |
| towerkit | `0db93b9`, pushed | **615 passed** + 1 known environmental failure |

The one towerkit failure is `tests/test_connector.py::test_roots_fall_back_to_bookkits_configuration`
— it fails on `main` too (no `bookctl` on PATH from that checkout). **Verified. Do not chase it.**

Everything queued before tonight is merged in both repos. `git branch --no-merged main` was empty in
both at the start of the run.

## 2. In flight when this was written

Three implementers, deliberately in separate worktrees because their files collide:

| branch | worktree | what |
|---|---|---|
| `submission-quotes` | `.claude/worktrees/web-work` | the missing middle — `quoted` status, quote expiry, subjectivities, wiring `submission.underwriter_contact_id` |
| `soi-status-and-statutory` | `/Users/grantgreeson/Developer/towerkit` | per-row SOI status, separate unbound subtotal, the statutory package |
| `mcp-derived-surface` | `.claude/worktrees/web-account` | derive the editable field table from the form builders, the denylist, `describe()`, the parity ledger |

**If a report was lost, re-dispatch rather than guess.** Each brief is reconstructible from
`ROADMAP.md`.

**Sequencing constraints that produced this split, and that still apply:**
- towerkit is **one shared checkout** resolved by every bookkit worktree via `../towerkit`. Only one
  agent may work there at a time, and a broken towerkit breaks every bookkit test run.
- `submission-quotes` and the assignee work both touch `models.py`, migrations and `forms/` — they
  must not run concurrently. The assignee waits.
- `mcp-derived-surface` was told **not** to edit `forms/entities.py`, and `submission-quotes` was
  told **not** to touch any MCP tool, so those two do not collide.

## 3. The queue after these three — REPRIORITISED 2026-08-18 late

**Grant's instruction: move the program-editing friction up.** His morning goal is to *build*
programs, not only review them, so the surface he will sit in front of comes first. Everything below
was resequenced around that.

**NEXT IN towerkit, the moment `soi-status-and-statutory` lands:**

**0. The editor's share-editing friction.** The AE measured **42 keystrokes to change one carrier's
share**, and found `v` — the fast sheet — carries no participants. Verified: `v` is
`action_layers_sheet` (`editor.py:205, :886`), and a participants sheet *does* exist
(`_participants_sheet`, `editor.py:697`, with `CellEdited` handling at `:948`) — so the cost is
**navigation, not editing**. The likely shape of the fix is a participants sheet reachable directly,
the way `v` reaches layers. **Measure the current path before changing it**, and report the count
before and after; a fix that does not move the number is not a fix.

Then, in order, and none of it blocked on Grant:

1. **Assignee (C4), task only.** His call, against the AE's advice to span task/RFI/submission —
   recorded as his. **But the AE's storage correction stands**: `assignee_kind` + `assignee_id` when
   the picker resolved, freeform `assignee_name` when it did not, and the export reads the KIND.
   String-matching a name to derive You/Us means typing "Sam" for "Sam Garcia" silently flips a
   client-facing column. Suggestions come from team members, the account's contacts, **and contacts
   on market orgs** — underwriters ARE records in the book; an earlier ROADMAP entry of mine said
   otherwise and is corrected in place.
2. **The `states` model (C15)** — optional list on a statutory layer, plus the monopolistic check
   (ND, OH, WA, WY cannot be covered privately). Canonical file-format change: additive and
   optional, and `model._ordered`'s hand-written key order must learn about it or the zero-diff
   round trip breaks. **Trap:** `limits_text` short-circuits on `if layer.limits_detail`, so a file
   carrying both prose and a states list needs a rule or the prose silently wins.
3. **C9** — suppress client-visible section labels beginning with "Internal". Prefix match (exact
   match is what already withholds). **Ruling: the de-labelled rows go into the uncategorised /
   General section** — `compose()` cannot express a headerless section without changing its own
   guards, and a neutral invented heading is a second vocabulary to keep straight.
4. **C6** — renewal date on the programme, then a renewal calendar tab.
5. **C5 / C12** — the sheet header block and the format pass (towerkit renderer). **Ruling: build
   the header settings-backed** (`repo/settings` already exists and carries program roots), seeded
   with visible placeholders like `<set your firm name>`. bookkit has no "my firm" concept and a
   hardcoded guess would be worse than an honest placeholder.
6. **towerkit slice 1 phase B** — the bookkit Program tab consuming `render/web.py`.
7. **The review squad**, which Grant asked for once the rounds are done.

## 4. Rulings that are mine, so nothing stalls

- **FEIN (C13): capture it, do not print it** on the client-facing sheet unless he asks. It
  identifies a legal entity to a tax authority and its value to a schedule's reader is near zero.
- **MCP parity ledger destination: not declared.** The ledger's job is to make gaps visible and
  machine-checked; the destination is a decision the filled-in ledger lets him make in one sitting.
- **Nothing destructive.** Every migration additive and optional, a snapshot before any migration
  runs, no rewrite of existing data. If it cannot be additive, it waits for morning.

## 4b. RULING: towerkit work goes in its own git worktree, not the shared checkout

**This bit twice tonight.** bookkit resolves `../towerkit` to the single checkout at
`/Users/grantgreeson/Developer/towerkit`, so whatever branch an agent leaves checked out there is
what *every* bookkit worktree compiles against. Twice a bookkit gate came back red with
`tests/test_services.py::test_write_three_tab_order_and_headers` failing — both times purely because
a towerkit agent had the SOI branch checked out and bookkit was seeing a Status column its test did
not expect. Both times the bookkit branch under test was clean.

**From now on: towerkit implementers and reviewers work in a `git worktree`**, leaving
`/Users/grantgreeson/Developer/towerkit` itself parked on `main`. The SOI reviewer did exactly this
unprompted — it created a throwaway worktree to diff main against the branch — and it worked
perfectly.

Why this is right rather than merely convenient: during development bookkit *should not* see
towerkit's in-progress branch. It should see `main`, and pick up the change when it merges. The
shared checkout was accidentally coupling two repos' working states.

**How to tell a contaminated gate from a real one**, if it happens again before this is adopted
everywhere: `uv run --no-sync python -c "from towerkit import soi; print(hasattr(soi,'SoiStatus'))"`
from bookkit tells you what bookkit is actually compiling against, and
`git -C /Users/grantgreeson/Developer/towerkit branch --show-current` tells you why.

## 5. Process — the parts that have actually caught things

- **Gate every branch yourself**: full suite, `mypy src`, `ruff check src tests`, plus
  `uv build --wheel` where package data moves. **Gate the MERGE COMMIT too** — two branches that
  each pass can still be wrong together, which happened once tonight when a guard one branch added
  governed a rule the other wrote.
- **Review before merge, always.** A green suite has not once been sufficient on this build.
- **Mutation-prove every new test, and report the mutations that PASSED** — those are the
  behaviours nothing holds, and they have been the most valuable output of every review.
- `uv run --no-sync python -m pytest`, never a bare `uv run pytest` (falls through to Anaconda's and
  reports a bogus `ModuleNotFoundError`). Redirect to a file and read the file; background
  notifications report the wrapping shell's exit code, not pytest's.
- **Never `git add -A`.** The main worktree carries unrelated dirty files.
- **Commit before mutating.** `git checkout -- <file>` reverts your own uncommitted work along with
  the mutation. This destroyed a set of my own mutation proofs tonight, and separately an agent
  committed mutated code that passed both mypy and ruff.
- **A name imported into a module that already uses it as a local is a landmine** — the assignment
  makes it local for the whole function, so the call raises `UnboundLocalError`. It cost 33 red
  tests tonight. Module-qualify instead.

## 6. Waiting for Grant in the morning (nothing blocks on these)

- **The MCP denylist**, which he explicitly asked to read and edit. It is a deliverable, not an
  implementation detail — the branch is instructed to produce it as a document.
- **C14** — anything in the CFO review he thinks is wrong. Blank is a fine answer.
- The build log carries a feedback line on every section:
  https://claude.ai/code/artifact/18901ee0-3530-4ff6-9b2d-96325ee550f5
