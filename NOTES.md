# NOTES — towerkit observations

Things in towerkit that turned out wrong, unclear, or worth changing while
building bookkit against it.

- towerkit money is integer **whole dollars**, not minor units; the bookkit
  brief (§1) says "integer minor units… same as towerkit", which is not quite
  what towerkit does. Handled at the sync boundary (see DECISIONS.md).
- `towerkit.render.ascii.render_ascii` needs a `Theme`; `load_theme(None)`
  gives the built-in default, which is what the bookkit tower preview uses.
- towerkit is mypy-strict internally but shipped no `py.typed` marker, so
  downstream imports degraded to `Any`. Added `src/towerkit/py.typed` (empty
  marker file) in the towerkit repo — worth committing there.
- towerkit has no public "validate a Program instance and raise" helper aimed
  at embedders; `validate_program(program)` + `Diagnostics.ok` is the intended
  surface and works fine for write-through.
- Reviewed towerkit's in-flight `Line.group` work (2026-08-11 working tree:
  schema + canonical keys + adjacency warning + banded layout). No bookkit
  changes needed: programs only pass through towerkit's own model/serialiser,
  and the projection is carrier/limit-based, so `group` is display-only from
  bookkit's point of view. Revisit only if cross-book queries ever need
  per-entity (group) premium splits.
