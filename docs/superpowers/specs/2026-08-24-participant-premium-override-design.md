# Stated per-market premium on a shared layer

Grant, 2026-08-24. Status: awaiting approval.

## The problem

A shared-participation layer splits its premium by capacity: `premium_share(
layer.premium, participant.share_bps)`, floor-divided, in six places. That is
right until it isn't. Two carriers at 50/50 do not always charge the same
money — a differential, surplus-lines tax and stamping fees on one paper, a
non-concurrent quote. Today the file cannot say so, so the worksheet shows a
figure the broker knows is wrong and no surface can be corrected.

The deeper mismatch Grant named: **premium is assigned to a policy, not to a
layer**, and on a subscription layer each participant *is* its own policy.
towerkit puts `policyNumber` and `period` on the LAYER, which is the same
mismatch in two more fields. This change fixes the premium half only; the
per-seat policy number and period are explicitly out of scope and stay a
later ticket.

## The rule (Grant's call, 2026-08-24)

**Stating one market's premium states them all, and the layer premium is
their sum.**

    Layer premium  $1,000,000                Layer premium  $1,020,000
      Swiss Re  50%   $500,000  derived   →    Swiss Re  50%   $500,000  stated
      Chubb     50%   $500,000  derived        Chubb     50%   $520,000  stated

Typing $520,000 into Chubb writes three numbers in ONE undo unit: Chubb's
stated premium, Swiss Re FROZEN at the figure it was already showing, and the
layer premium recomputed as the sum. Nothing moves behind the user's back
afterwards, and `Program.total_premium()` — a sum over `layer.premium` — stays
true with no change to it.

Consequences, stated plainly because they follow from the rule rather than
being separate decisions:

- **Clearing is all-or-nothing too.** Clearing any seat clears every seat on
  that layer, back to splitting. Leaving one derived seat beside stated ones
  makes it derive from a base that already contains their money.
- **`layer.premium` is not typable while seats are stated.** It is the sum;
  typing over it would make one of the two lie. `_GUARDS[("layer","premium")]`
  refuses with a message naming the way out ("clear the market premiums to go
  back to splitting"). A refusal is safe here — it refuses a WRITE, not a
  validation, so nothing wedges the file the way `render.theme` did.
- **Stating a seat on a layer with no premium at all is refused** when other
  seats would be left underived: there is no base to freeze them at, and
  freezing them at zero would state a figure nobody knows. The message says to
  set the layer premium first, or to state every market. A layer with ONE seat
  has nothing to freeze and is allowed.

## towerkit (phase 1)

**Model.** `Participant.premium: Money | None = None`. Whole dollars on disk
like every other money field; `None` writes no key (`model.py`'s existing skip
rule), so every file that does not use the feature re-saves byte-identical.

**One definition of a seat's premium.** `Layer.premium_for(participant) ->
int | None`: stated if stated, else the share split, else None. Six call sites
stop doing the arithmetic themselves — `compare.py:72`, `soi.py`,
`render/schematic_xlsx.py:233` (via a `premium` on `layout.Block`), and the
three in `tui/screens/editor.py`. This is the DRY rule as written in CLAUDE.md:
when the bug is in one of N copies, fix the N.

**The setter.** `edit.set_participant_premium(program, layer_id, index,
premium)` — modelled on `set_statutory`, which is the precedent for a field
whose write carries consequences: it owns the freeze and the sum, and returns
write-time ADVISORIES naming the seats it froze and the layer premium it set.
`participant.premium` goes on `mcpsurface.DENIED` with that reason, so the
generic setter cannot write one seat and leave the layer lying.

**Validator.** `premium-split`, a WARNING: seats stated but not summing to
`layer.premium`, or some seats stated and others not. Reachable only by a
hand-edited file or an older writer; a warning and never an error, for the
`line-gap` reason — an error refuses every later write to that file, including
the one that would fix it.

**The chain CLAUDE.md names.** `sync_schema.py` plus the hand-written
`description` in both `program.schema.json` copies; `mcpsurface.SURFACE` picks
the field up unaided and the reviewed-count gate goes red on purpose — raise
it and say why in the docstring; `mcpparity.MUTATIONS` gains
`set_participant_premium` naming the tool that reaches it; a towerkit MCP tool
`participant_premium` so an agent editing a program file directly can do this
too.

## bookkit (phase 2)

**sync.** `set_participant_premium(conn, placement_id, layer_id, carrier,
premium_cents | None)` through `write_through` (load → mutate → validate →
canonical dump → re-project, sha256-guarded), and `premium_preview(...)`
through the existing `preview()` helper. Cents in, whole dollars to the file
via `cents_to_dollars`, which refuses sub-dollar amounts — the existing
write-through rule, unchanged.

**Projection and reads.** `sync.py:258` (into `proj_participant.premium`) and
`layer_details_of` both take their figure from `premium_for`. No migration:
`proj_participant.premium` already exists as a cents column. `layer_details_of`
adds `premium_stated: bool` per seat so a surface can say which figures are
typed and which are arithmetic.

**parity.** `TOWERKIT_MODEL_FIELDS` gains `participant.premium`; `SYNC_VERBS`
gains a row for the new verb saying what reaches it on web and mcp. Both are
red until done, and the red test is the ticket.

**Web — the participation table.** The derived premium column becomes an
inline-editable cell under the ordinary cell grammar (blur commits, Escape
discards, unchanged closes without writing). Derived figures keep the muted
`derived` styling; stated figures render normal with a `stated` marker, so the
table says at a glance which money is typed. A `split again` control appears
only while seats are stated and clears them all.

**Web — the preview.** The FIRST override on a layer touches rows the user did
not type in, so it previews before it commits — the `share-preview` pattern
exactly (`_share_preview.html`, `sync.share_preview`), showing the seats about
to be frozen and the layer premium about to change, with Save and Discard.
Later edits to an already-stated layer commit in place: every seat is already
stated and only the sum moves.

**Web — the layer premium stat.** While seats are stated it renders as derived
with `from markets`, and the guard's refusal is what a typed value gets.

**MCP.** `program_market_premium(placement_ref, layer_id, carrier, premium)` —
null clears the layer's overrides. Without it the field is built and invisible
to the one user who works through tools.

## Testing

- towerkit: byte-identical round trip for a file with no seat premium; the
  freeze; the clear-all; the `layer.premium` guard; the no-base refusal; the
  `premium-split` warning; each of the six derived call sites reading a stated
  figure rather than the split.
- bookkit: the write-through verb and its re-projection; `premium_stated` in
  `layer_details_of`; the cell route and the first-override preview; the two
  parity rows; the MCP tool.
- Per `verifying-tests-can-fail`: mutate `premium_for` to always split and
  confirm the call-site tests go red. A green suite proves nothing broke, not
  that the new seam is taken.

## Rejected

- **Override one seat, warn on mismatch.** The warning stays lit until the
  layer premium is hand-fixed, and fixing it re-derives the untouched carrier
  to a new number. The user ends up stating both anyway, by hand.
- **`layer.premium` derived whenever seats are stated.** Truest to "premium
  belongs to policies", but makes a stored towerkit field sometimes-writable,
  which the renderer, SOI, MCP and TUI all have to learn.
- **Premium and taxes/fees as two figures.** One number; its composition is
  the broker's business. Splitting them forces every downstream total to
  declare which one it means, for a distinction nothing computes on yet.
- **Per-seat policy number and period.** The honest shape for a non-concurrent
  subscription layer, and out of scope: renewals count to the earliest LINE
  end, and one layer carrying several expiries is a change to the renewal
  date rule itself.
