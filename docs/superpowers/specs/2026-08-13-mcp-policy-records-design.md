# MCP policy records — design

Date: 2026-08-13 (overnight)
Status: DRAFT — implementation begun on feat/mcp-policy-records, held
unmerged for Grant's morning review. Everything else this night merged;
this one waits because Grant has not seen this design.

## Goal

The assistant helps build out a program: read the tower, add layers to a
stub, bind markets onto layers at shares, fix premiums and dates — through
the exact guarded cycle the TUI already uses. "AI assist in populating
layers" (Grant, 2026-08-13).

## What already exists (this spec exposes, it does not invent)

`sync.write_through` is the cycle: sha256 conflict guard (WriteConflict if
the file moved since projection — towerkit's TUI, say), load via towerkit,
mutate, `validate_program` — **a failed validation writes nothing** — then
canonical dump and re-project. On top of it, with TUI tests:
`update_program` (name/dates), `update_layer`, `add_layer` (pending layer,
participants join as markets bind), `add_participant` (over-signing refused
by towerkit's validator), and `layer_details` (cents-native read).

## The boundary this spec keeps

The house line (sync.py's own words): bookkit edits the facts that arise
from book events — premiums firming, markets binding, dates moving. Tower
DESIGN — lines, retentions, sublimits, structure — stays in towerkit's
editor. MCP inherits that line: no line/retention/sublimit tools in v1.
REVIEW POINT (morning): Grant said "walk through the process in towerkit" —
if he wants design-level assist too, that is a towerkit-side feature (its
editor, its session), not more bookkit file writers.

## Tools (thin over sync.*, all batched)

- `program_layers(placement_ref)` — read: layer_details + line ids/names,
  so exact layer_ids and line_ids exist to be read before any write.
- `program_layer_add(placement_ref, name, line_ids, attach, limit,
  premium=None)` — money in human dollars, parsed to cents at the edge.
- `program_layer_edit(placement_ref, layer_id, …update_layer fields…,
  expecting_name=None)` — compare-and-set lite: layer resolved by exact id
  from a read; `expecting_name` guards against id drift under a rename.
- `program_bind(placement_ref, layer_id, carrier, share)` — add_participant;
  share as "25%" or bps; over-signing refused by the validator.
- `program_edit(placement_ref, name=None, period_from=None, period_to=None)`
  — update_program.

Every tool refuses with towerkit's own diagnostics text when validation
fails (nothing written), and surfaces WriteConflict as "re-read and retry" —
the same contract compare-and-set trained the model on.

## The revert story — the part batch undo cannot cover

File contents are not event_log rows: revert_batch cannot restore a program
file. Pretending otherwise would be the worst kind of false safety. So:

1. **Pre-image snapshot per write.** Before dump, the file is copied to
   `<program dir>/.mcp-snapshots/<batch-ref>.json`, and a sidecar
   `<batch-ref>.meta.json` records the path and the POST-write sha256.
2. **`program_revert_file(batch_ref)`** restores the pre-image ONLY if the
   file's current sha equals that batch's post-write sha — i.e. nothing
   (TUI, towerkit, a later MCP write) has touched it since. Otherwise it
   refuses and says what to do (re-read; revert newer batches first).
   Restore re-projects, so proj_* follows.
3. **`revert_batch` refuses `program_*` batches** with a pointer to
   program_revert_file — never a half-revert of cache rows under an
   untouched file.
4. Snapshots are additive files in a dot-directory; nothing existing is
   rewritten. Retention: keep the last 20 per program (prune oldest).

## Testing

- Cycle: layer added via MCP lands in the file canonically and in proj_*;
  validation failure (over-signed layer) writes NOTHING (file byte-compare).
- WriteConflict: touch the file after projection → tool refuses, file
  untouched.
- Snapshot: every write leaves a pre-image + meta; revert restores
  byte-identical and re-projects; revert after a later edit refuses.
- revert_batch on a program batch refuses with the pointer.
- Round-trip one tool over the protocol.
- Real scaffolded program fixtures, never hand-built dicts.

## Out of scope (v1)

Line/retention/sublimit design tools (the boundary above); scaffolding new
program files from MCP; multi-file/bulk edits; towerkit-editor walk-through
features; deleting layers (towerkit editor owns structure removal).
