# Building a tower in the browser — design

Grant, 2026-08-21, with a screenshot of a garbled D&O tower: "trying to
diagnose what is going on here … it brings up a larger issue in the UX that
it's very clunky and not visible how to show what layer stacks on top of what
layer and what layers are shared with carriers."

## The problem, stated exactly

Two D&O excess layers were built at the SAME attachment — `$5M xs $5M` twice —
so the renderer drew them on top of each other and their labels overprinted.
towerkit had been reporting it all along:

    error  line-overlap  do: OVERLAP D&O Excess (Starr)→D&O Excess (Endurance)
                              at $10,000,000 vs $5,000,000

The same cover modelled correctly is ONE layer with TWO participants (Starr
50%, Endurance 50%), and validates clean.

**That is a design failure, not a user error.** Nothing in the UI teaches that
carriers sharing a slab are participants on one layer; the surface offered "add
a layer" and an attachment box, so a second layer is what got built. Two fixes
follow, and only one of them is this spec:

1. A file that is already wrong must SAY so — shipped 2026-08-21, the Program
   tab's diagnostics strip.
2. A wrong tower must be hard to BUILD — this spec.

## Decisions taken (Grant, 2026-08-21)

- **The web builds towers.** The standing rule "tower design stays in
  towerkit's editor; `o` jumps there" is dead: `o` was a bookkit TUI key, the
  TUI is retired and will be deleted, and a browser has no jump.
- **Approach A now, B closely after.** A is the stack editor; B is drag on the
  drawing. C (paste/parse a schedule) is DROPPED — he enters bulk data through
  MCP, so a parser earns nothing.
- **Attachment is computed from stack position, never typed.**
- **Removing a mid-stack slab leaves a GAP by default**, and asks.
- **A buffer layer is a real thing** and must be representable — a deliberate
  uninsured band, not an error.

## Section 1 — the stack editor

**A stack belongs to a LINE.** GL has a stack, AL has a stack. A layer whose
`applies_to` spans three lines appears in all three columns, because it does,
and editing it in one edits it everywhere. The row says so rather than hiding
it: a layer that silently changes two other columns is worse than one that
warns.

**Position IS the structure.** Rows run bottom-up. You insert ABOVE or BELOW an
existing slab. There is no attachment field. `attach` is computed — the top of
the slab beneath, `$0` for the first — which makes the reported overlap
UNCONSTRUCTIBLE rather than merely detectable.

**A layer spanning lines whose underlying stacks differ gets
`follows_underlying`** instead of a hardcoded attachment. That flag exists for
exactly this and `heal_follows` re-derives it on every write, so the tower
stays right when something below changes.

**`limit` is typed and NEVER pre-filled.** It comes off a document, and people
do not check prefills (data-entry rules, rule 8).

**Carriers are a sub-list ON the slab.** `+ carrier` sits on the slab; `+ layer`
sits on the stack. That is the whole fix: sharing a slab and adding a layer
become visibly different acts, and the shares sum in view so an over-sign shows
as it is typed rather than at save.

Explicitly NOT built: no attachment input, no "duplicate layer" control (that is
how a quota share becomes two layers), and lines/retentions stay where they are
— this is the stack, not the whole program.

## Section 2 — the write path

**No new towerkit verbs.** An insert is `edit.add_layer` plus field writes,
inside ONE mutation handed to `services.program_files.write` — the seam every
existing Program-tab write uses. Validated, snapshotted with a pre-image,
revertible; bookkit gains no second way to change a file.

**One insert recomputes the whole column, atomically.** Inserting mid-stack
pushes everything above it up and recalculates those attachments in the SAME
mutation, so `write_through` — which only accepts a file that validates — never
sees a half-shifted tower. This is also why towerkit's `restack` is not needed;
web/parity.py records it as unreachable through the guarded seam for precisely
that reason.

**One gesture is one batch is one undo.** Insert a slab and `u` removes it and
puts every moved attachment back. Add a carrier and `u` takes only the carrier
off.

**A refusal keeps the typing.** towerkit refuses over-signed shares and gaps in
its own words; the row stays open with what was typed.

**Removal leaves a GAP and asks.** Closing the tower up silently would move
cover the client bought. Leaving the gap makes the diagnostics strip say `GAP`,
which is true — and the next move is often to convert it to a buffer.

## Section 3 — buffers

A **buffer** is a deliberate uninsured band in a tower. towerkit cannot express
one today, so it reports `line-gap` — a FALSE REFUSAL on a structure that is
really placed (Grant, 2026-08-21).

A buffer is a SLAB, not an absence:

- it has `attach` and `limit` like any layer;
- it has NO participants and no premium — nobody is on it;
- it is excluded from signed-limit and premium totals;
- it SUPPRESSES `line-gap` across its span;
- it draws hatched and labelled, reusing the `unplaced` capacity convention
  already in the palette.

In the editor, `insert buffer` sits beside `insert layer`. This is what makes
the gap-on-removal default good rather than merely safe: remove a slab, the
strip says `GAP`, and if the band is deliberate you convert it in one move —
the tower goes from wrong to explained.

**This is a towerkit model change** (`Layer.buffer` plus the validator rule) and
lands there first, with bookkit following.

## Section 4 — the drawing

Read-only, and generated by towerkit's renderer as now: bookkit composes no
geometry and no strings of its own (spec D2.1's agreement rule). Three
additions:

- **buffers draw hatched and labelled**, so an uninsured band reads as a
  decision;
- **shared slabs split within the slab at share width** — already how
  participants are drawn; it needs to survive at small sizes rather than
  overprinting;
- **selecting a stack row highlights its slab, and the reverse** — the cheapest
  possible answer to "what stacks on what".

No drag (that is B), no editing in the picture.

## Section 5 — refusals and testing

Refusals are towerkit's, rendered in place, typing intact. The diagnostics
strip is the standing display for a file that is already wrong.

**One inherited bug this must not carry:** the web's `.cell-error` persists
until the next POST instead of clearing on keystroke — a message that survives
its own correction, which makes a valid entry read as broken. It is queued from
Grant's inline-edit report; the builder is written against the FIXED behaviour.

The tests are the invariants, not the happy path:

1. **The overlap is unconstructible** — drive the editor, assert two slabs
   cannot share an attachment. Grant's screenshot, closed at the source.
2. **An insert never writes an invalid file** — the column recomputes inside one
   mutation.
3. **One gesture, one undo** — insert restores every attachment it moved.
4. **A buffer suppresses `line-gap` and is excluded from signed limits.**
5. **A full tower is buildable from the keyboard alone.** If this fails, B stops
   being polish and becomes a requirement.
6. **The drawing and the editor never disagree** — both read the same file.

Every one of these gets mutated before it is believed: a guard whose deletion
leaves the suite green is not a guard.

## Risks

- **`Layer.buffer` is a towerkit change**, so this is two-repo sequencing —
  the failure that cost 2026-08-21 an afternoon. The launcher's capability
  check now catches that at startup rather than in a route, which is exactly
  the protection this needs.
- **A layer spanning lines is the hard case.** It appears in several stacks and
  an edit in one moves all of them. The design says warn rather than hide; if
  that proves confusing in use, the fallback is to make multi-line layers
  editable only from a single canonical column.
- **Scope.** This is the stack. Lines, retentions and sublimits stay where they
  are; pulling them in would make one spec into four.

## Out of scope, tracked elsewhere

- drag on the drawing (approach B) — after this
- bound-vs-proposed per policy — handoffs/20260821-BoundPerPolicy.md
- renewal drift — the brainstorm at the end of
  handoffs/20260821-AssigneeDisplayAndMulti.md
- deleting the TUI — agreed, unscheduled
