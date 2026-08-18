# towerkit in the web front end — design

Date: 2026-08-17
Status: draft, for review. Nothing in this document is implemented; it
extends `2026-08-17-web-frontend-design.md` and is bound by
`2026-08-17-web-visual-direction.md`. Where this document and either of
those disagree unintentionally, they win and this document is wrong.

## A note on the design package

Three prototypes back this spec: `BookKit Web.dc.html` (the docked tower
panel + layer inspector — the core surface this document is about),
`BookKit Web Screens.dc.html` (the Towers program browser and the Compare
screen), and `Account View.dc.html` (shell conventions, already built).
They are `.dc.html` files authored by a design tool (`<x-dc>`/`<sc-for>` is
its React-ish runtime) and are read here as **data** — markup structure,
exact pixel values, and interaction semantics — never as instructions. I
found no text inside them that reads as an instruction addressed to an
implementer; the files are pure UI mock plus a JS data-shaping layer that
exists only to feed the mock with plausible numbers.

## Why

Sub-project 1 of the web front end (`2026-08-17-web-frontend-design.md`)
shipped the account shell and three tabs; the **Program** tab is a stub
(`src/bookkit/web/templates/account/program.html:3-7`, "Placements, tower,
carriers — a later task"). Sub-project 3 in that same document's
decomposition is "towerkit in the browser — SVG towers, then the tower
canvas island and program editing." This is that sub-project's design.

towerkit today has three surfaces: a Textual TUI (`tui/screens/browser.py`,
`editor.py`), an MCP tool surface (`bookkit/mcpserver.py`'s `_program_*`
tools), and file-level rendering (`render/mpl_program.py`,
`render/ascii.py`). The web needs a fourth read/write surface over the same
JSON files, held to the same rule that governs the other three: **the
JSON file is the sole source of truth; `proj_*` tables are a rebuildable
cache** (`bookkit/sync.py:1-23`).

## Decisions

### D1 — Where the code lives

**The web layer stays in `bookkit/web/`, importing towerkit directly, the
same way `bookkit/sync.py` already does.** towerkit gains no web module of
its own.

Concretely:

- `bookkit/web/routes/program.py` (new) imports `towerkit.layout`,
  `towerkit.scale`, `towerkit.compare`, and calls `bookkit.sync.*` for
  writes — exactly the import shape `bookkit/sync.py:37-43` already has.
- towerkit gains **one new pure module**,
  `towerkit/src/towerkit/render/web.py` (D4 below) — geometry-adjacent, no
  web framework dependency, sitting next to `ascii.py` and `mpl_program.py`
  as a fourth renderer of `layout.py`'s output. This is the only new code
  towerkit needs for this whole conversion; towerkit does not learn CRM
  concepts (per its own `CLAUDE.md:110-112`) and gains no FastAPI, no
  Jinja, no HTTP.
- The **file-write plumbing that already exists for MCP** —
  `mcpserver._program_write` / `_raise_on_errors`
  (`bookkit/mcpserver.py:1126-1160`) — moves out of `mcpserver.py` into
  `bookkit/services/program_files.py` (which already owns the snapshot
  half, `capture`/`restore`, `services/program_files.py:30-75`) so the web
  route and the MCP tool call **one function**, not two copies. See D5.

**Rejected: towerkit grows a `towerkit/web/` package that bookkit mounts.**
The design brief's framing — "towerkit becomes a workspace inside BookKit
rather than a second product" — argues directly against this. towerkit is
a path dependency (`pyproject.toml:34`, `path = "../towerkit"`); giving it
its own FastAPI routes would mean two web apps to wire together, a second
place field-cleaning/parsing rules could drift from bookkit's (the exact
failure the original SPA rejection was about — "a field the API does not
know about is dropped on save with no error," `2026-08-17-web-frontend-design.md:47-52`),
and a second `bookctl web`-shaped entrypoint question with no clear answer.
towerkit's TUI (`tui/screens/browser.py`, `editor.py`) stays exactly what
it is: the tool the broker uses when the account isn't the frame — nothing
here retires it.

**Convention tests.** `tests/test_conventions.py:31-45`
(`test_no_raw_sql_in_web`, `test_web_and_tui_never_import_each_other`)
already cover `web/` importing `towerkit.*` with no change needed — those
rules are about `bookkit.tui`, not towerkit. One test needs writing, not
changing: towerkit's own `tests/test_conventions.py:27-37`
(`test_tui_never_mutates_program_collections_directly`) scans only
`src/towerkit/tui/` for direct `.layers.append(...)`-style mutation,
banning it in favour of `towerkit.edit`. **`bookkit/sync.py` already
violates the spirit of that rule** — `add_layer`
(`bookkit/sync.py:824-855`) and `add_participant` (`sync.py:858-876`)
build a `TkLayer`/`TkParticipant` and `.append()` it directly rather than
calling `towerkit.edit.add_layer`/the equivalent, even though `edit.py`
exports exactly those functions (`edit.py:165` `add_layer`, no
`add_participant` — participants aren't in `edit.py` at all, which is its
own small gap). This spec does not fix that pre-existing deviation, but
flags it: a new bookkit-side convention test analogous to towerkit's,
scanning `bookkit/sync.py` and any new `bookkit/web/routes/program.py` for
the same banned patterns, would be worth adding in the slice that touches
this code — see Open questions.

### D2 — How the tower is drawn (the biggest technical question)

**Two different techniques for two different jobs, sharing one geometry
source.**

1. **Export (SVG/PDF/PNG for download, and any static "here's the tower"
   read view)** stays exactly `render/mpl_program.py`, untouched. This
   already produces deterministic, byte-identical output
   (`render/common.py:1-6`, tested) with **real measured text fitting** —
   `_fit_text` (`mpl_program.py:332-370`) tries a list of label candidates
   against matplotlib's actual rendered text extents (`bbox.width`,
   `bbox.height`) and picks the first that fits, falling back through
   wrapped forms down to a bare initial. There is no hardcoded size
   threshold anywhere in that path; the module's own docstring
   (`mpl_program.py:4-6`) says so directly. This is strictly better than
   any pixel-threshold heuristic and it is what "export parity" means —
   the web's "Export SVG" button (shown in the mockup,
   `BookKit Web.dc.html:155`) calls this function, unmodified, and streams
   the file back.

2. **The interactive docked tower panel** (click a layer to select it,
   click a participant to edit its share, drag a layer's top edge to
   resize) is **not** drawn as SVG at all. It is absolutely-positioned
   HTML, computed server-side from `layout.py`'s geometry and rendered
   into percentages by Jinja — exactly the technique the prototype already
   uses (`BookKit Web.dc.html:171-178`, every block a `<div
   style="position:absolute;left:{{ }}%;...">`), just computed in Python
   from real data instead of re-derived in the prototype's throwaway JS.

**Why not SVG for the interactive panel too, given the handoff's stated
preference for SVG "for export parity"?** Two concrete reasons, checked
against the code rather than assumed:

- **Text wrapping.** `mpl_program.py`'s `_fit_text` wraps candidate strings
  with `textwrap.wrap` *before* measuring them
  (`mpl_program.py:288-292, 317-320`) because `<text>` does not wrap on its
  own. This is not a matplotlib quirk to work around — it is a property of
  the SVG format itself: no SVG writer, hand-rolled or matplotlib's, gets
  automatic word-wrap, because SVG has no text-layout box, only positioned
  glyph runs. Any SVG-based interactive renderer, whoever writes it, would
  have to pre-compute line breaks server-side and lay them out as separate
  `<tspan>`s — the same wrap-and-measure problem `_fit_text` already solves
  for export, reimplemented a second time for a target with no browser, no
  matplotlib canvas, and no font-metrics access in the request path. HTML
  text wraps for free, natively, at word boundaries — which is exactly the
  design's own rule, "never cut mid-word" — because HTML text runs inside a
  real layout box; SVG text does not.
- **Type weight for the docked panel is app chrome, not client output —
  and the mockup gets this specific value wrong.** The visual-direction
  spec resolved (after this spec was drafted) that Noto Serif ships only
  400 and 700, that a synthesised 600 renders smeared, and that the split
  is: app chrome uses 700, anything the app renders *for a client* (export
  headers, the PDF/SVG render, any shareable read-only program view) stays
  400 — explicitly named as "load-bearing for the towerkit conversion,
  where both live in one codebase"
  (`2026-08-17-web-visual-direction.md:102-114`). The docked tower panel is
  broker-only working chrome, never handed to a client, so it takes **700**,
  matching every other screen title/section name in the app. The mockup
  itself hardcodes `font-weight:600` on the tower panel's insured-name
  header (`BookKit Web.dc.html:149`) — a weight the visual-direction spec
  now bans outright. Building slice 1 straight off the mockup's CSS ships a
  browser-synthesised weight on day one; the panel's insured-name header
  must be built at 700, not copied verbatim from the prototype.
- **Interactivity.** The panel needs per-block click targets (`pickLayer`,
  `data-layer="{{ b.id }}"`, `BookKit Web.dc.html:171`), a chip toggle for
  `applies_to` (`toggleLine`, line 228), and eventually a drag handle on a
  layer's top edge. All of that is plain DOM event wiring on HTML
  elements. SVG can carry `data-*` attributes and click handlers too, but
  matplotlib's SVG writer does not expose a way to stamp a given
  `layer_id` onto a given `<rect>`'s `id`/`data-*` short of patching
  `mpl_program.py` to call `Artist.set_gid(...)` on every patch — a real
  option, but one that couples the *export* renderer's internals to a
  *web interaction* requirement it has no other reason to carry.

**What both paths already share, and can't help sharing:** `layout.py` and
`scale.py`. Per towerkit's own `CLAUDE.md:26`, `layout.py`/`scale.py`
"never import plotting libraries (tested)" — they are pure dataclasses and
floats. `build_layout(program, gamma=...)` (`layout.py:135`) returns the
same `TowerLayout` — `Column`, `LayerBlock`, `ParticipantBlock`,
`RetentionBlock`, `GroupBand`, `ref_lines` — that `ascii.py` and
`mpl_program.py` already both consume. The web's HTML renderer is a
**third consumer of that same geometry**, following the module's own
stated rule (`layout.py` module docstring, and repeated at
`ascii.py:4-6`): "if a drawing decision needs geometry that is not already
in the layout, it belongs in `layout.py`, not here." Nothing about a `Rect`
in `[0,1]` tower space needs to change; only the last mile — turning that
`Rect` into `left/width/bottom/height` CSS percentages instead of ANSI
cells or matplotlib patches — is new.

**Rejected: reimplement `scale()`/`buildTower()` in client-side
JavaScript**, as the prototype necessarily does for its own standalone
demo (`BookKit Web.dc.html:723-820`). This is the same duplication the
parent spec already rejected once, at a different layer: "every form field
must be declared three times... in two languages, with nothing forcing
agreement" (`2026-08-17-web-frontend-design.md:44-46`). A JS reimplementation
of `scale.py`'s gamma-compression math is exactly that risk one level
down — a chart that silently renders wrong the day someone tunes
`DEFAULT_GAMMA` or fixes a rounding edge in `scale.py` and forgets the JS
twin. The server computes every percentage; the browser only paints what
it's given and (later) posts drag deltas back for the server to
re-resolve. See D4 for the one JS "island" this still leaves.

**Explicit decision: label *fit* is not guaranteed to match between the
two renderers, and this spec accepts that as a scoped, known limitation
rather than closing it in slice 1.** Geometry parity between the two paths
is real and load-bearing — both consume the identical `TowerLayout`, so a
block's boundaries and coordinates cannot diverge. Label *fit* is a
separate property `layout.py`'s geometry says nothing about, and the two
renderers solve it by genuinely different means. `mpl_program.py`'s
`_fit_text` (`mpl_program.py:332-370`) measures each candidate string's
real rendered width and height against the target rectangle before
committing to it. `render/web.py`'s `label_visibility` (D4) does not — it
gates a label on the block's *height* alone (the 30/13/11px thresholds)
and says nothing about whether the shown text fits the block's *width* at
its rendered font. The prototype's own CSS for the label
(`overflow:hidden;text-overflow:ellipsis`, `BookKit Web.dc.html:779`) does
not actually protect against this: `text-overflow:ellipsis` only takes
effect on a `white-space:nowrap` element, which this one is not, so a long
carrier or program name wraps (correctly, per the word-boundary point
above) and then simply gets clipped by `overflow:hidden` at whatever pixel
row the box ends on — no ellipsis, no guarantee the cut lands between
words, the exact "never cut mid-word" failure this document argues HTML
avoids relative to raw SVG. So a long name can legitimately render
differently — wrapped-and-clipped in the interactive panel, correctly
shrunk-or-abbreviated in the export — on the one axis shared geometry does
not protect.

Two ways to close it: give `render/web.py` a cheap monospace-width
estimate (`len(text) * avg_char_px` against the block's rendered width,
enough to pick a shorter candidate the way `_fit_text` does, without a
real text-measurement backend) and drop to a shorter label the same way
the export path does; or accept CSS-level clipping as a known
interactive-only limitation and fix the panel's CSS so a clip is at least
a clean line clip (`overflow-wrap: break-word` off, wrap at word
boundaries, `overflow: hidden` on the whole-lines box) rather than the
mockup's currently-broken ellipsis-that-never-fires. **Decision: the
second, for slice 1.** A monospace-width estimate is one more constant to
keep in sync with whatever font actually ships (`JetBrains Mono` per the
handoff, not yet vendored — visual-direction spec's own flagged
deviation), and it buys accuracy the docked panel's own audience (the
broker, working, not a client) doesn't obviously need on day one. Fix the
CSS so a clip is at least clean, ship it, and revisit with a real
width-estimate only if a genuinely garbled label shows up in practice —
consistent with this spec's general posture of not building infrastructure
ahead of evidence (see "recommend against," below).

### D3 — The compressed vertical scale: no y-axis, ever

`scale.py`'s docstring is explicit about what it buys: "$52M sits at the
same height in every column" (`scale.py:6-8`) via a single global,
gamma-compressed map (`DEFAULT_GAMMA = 0.35`, `scale.py:22`). `layout.py`
already exposes exactly what a linear-axis-free renderer needs:
`TowerLayout.ref_lines: tuple[tuple[int, float], ...]` — `(dollars, y)` at
every real breakpoint (`layout.py:128, 238`).

The web reproduces `mpl_program.py`'s rule verbatim
(`mpl_program.py:106-113`): for each `(dollars, y)` in `ref_lines`, draw a
horizontal guide at `y` and a dollar label in a left gutter at that same
`y` — never an axis with tick marks implying linear spacing. `YMap.y()`
(`scale.py:41-49`) is called server-side, directly, with zero
reimplementation: `TowerLayout.ymap.max_dollars` for "top of tower,"
`ymap.y(dollars)` for anywhere else the panel needs a height. The mockup's
own gutter column (`BookKit Web.dc.html:159-163`, a 54px `<div>` of
`ref.gutterStyle` labels) and caveat line
(`BookKit Web.dc.html:198`, `{{ tower.caveat }}`) are the target: "not to
scale — compressed vertical scale (γ = 0.35); reference lines at real
attachment points," always visible, never a toggle, whenever
`ymap.gamma != 1.0` — matching `ascii.py`'s own rule at
`ascii.py:237`.

Grant's flagged preference against a "no visible provenance footer and no
scale caveat on charts" (`towerkit/CLAUDE.md:38-39`) is about the
**export** chart (`mpl_program.py` has no caveat line — confirmed, it is
not drawn anywhere in that module) and should stay that way: the printed
deliverable a broker hands a client omits it. The **interactive** panel is
a working tool the broker reads to understand structure, not a client
deliverable, and the ASCII preview already carries the same caveat for the
same reason (`ascii.py:237`, "(not to scale)"). This is a real, narrow
tension between two of Grant's own stated rules and it resolves cleanly
because the two surfaces have always been different: `render_ascii` is a
working preview, `render_program` is print output, and the web tower panel
is unambiguously the former.

Note for whoever implements this: `mpl_program.py`'s own module docstring
claims "a visible not-to-scale caveat instead of a lying axis"
(`mpl_program.py:6-9`), which is **stale** — Grant deliberately removed
the caveat and the provenance footer from the export chart
(`towerkit/CLAUDE.md:38-39`), and the code above is correct about what the
module actually does. Don't "fix" the code to match its own docstring;
the docstring is the thing that's wrong, and it's out of scope for this
sub-project to correct it.

### D4 — The label-drop rule and where it lives

The prototype's `buildTower()` (`BookKit Web.dc.html:770-799`) already
encodes the exact numbers the design brief describes:

- name shown only if the block's rendered height in px clears **30px**
  single-column or **13px** spanning multiple columns
  (`nameOn = wide ? hPx >= 13 : hPx >= 30`, line 771);
- the money figure shown only if height clears **11px**
  (`hPx >= 11`, line 777);
- reference-line dollar labels are thinned to keep the top of the tower
  and, among ties within **~12px**, whichever attachment spans more lines
  (`minGap = 12 / 170`, lines 792-798, with the comment on 785-787
  explaining why: "under γ<1 the low breakpoints sit within a few px of
  each other").

This is **new logic that exists nowhere in towerkit today.** `ascii.py`
doesn't need it (integer character-cell rounding does its own coarse
dropping — `label_row` truncation at `ascii.py:161`). `mpl_program.py`
doesn't need it (it measures real extents instead of guessing from a
height threshold, D2 above). It is specific to a **fixed-pixel-height**
renderer that can't afford to run a text-measurement pass per request.

**Decision: it lives in a new towerkit module,
`towerkit/src/towerkit/render/web.py`**, as a pure function of
`TowerLayout` plus a caller-supplied pixel height — not in `bookkit/web/`.
Reasoning:

- It is a **geometry decision about a `TowerLayout`**, exactly the kind of
  thing `layout.py`'s own module docstring assigns to that layer: "a
  drawing decision needing geometry not already in the layout belongs
  here" — read one level down, a decision about *how much of that
  geometry to show* belongs beside the geometry, not in a CRM's web
  package that has no other reason to know what a breakpoint is.
- It is **testable the same way `test_scale.py` already tests `scale.py`**
  — pure input/output, no Jinja, no FastAPI, no fixtures beyond a
  `Program`. Housing it in `bookkit/web/` would mean pulling towerkit's
  domain types into a bookkit test just to check a threshold constant.
- It **does not become a fifth thing `mpl_program.py` has to stay
  consistent with.** `mpl_program.py` keeps measuring real text and is
  explicitly *not* changed to consume this module — seeding pixel
  thresholds into the one renderer that already does the correct,
  harder thing would be a regression, not a simplification.

Signature sketch (for the eventual plan, not binding here):

```python
def label_visibility(tower: TowerLayout, chart_height_px: float) -> WebLabels:
    """Per-LayerBlock show_name/show_money flags and a thinned ref_lines
    list, at the given rendered pixel height. Pure; no plotting import."""
```

`chart_height_px` is a parameter, not a constant, because the docked
Navigator panel and any later full-page tower view are different heights;
one function serves both without duplicating the 30/13/11/12 constants a
second time.

**`chart_height_px` is the chart-*drawing* area, not the panel's outer CSS
box — this is a distinct number and passing the wrong one silently
under-fires every threshold.** The prototype derives its px height from
the block's own height *percentage*, not the panel box: `hPx = heightPct *
2.4` (`BookKit Web.dc.html:770`), which is only correct if the drawing
area the percentages are relative to is **~240px** tall. The panel's outer
box is `height:340px;min-height:340px` (`BookKit Web.dc.html:146`), but
that 340px also contains the header row, the left-gutter padding, the
retention band below the zero line, and the caveat line under all of
it — none of which are part of the 100%-tall region a `LayerBlock`'s
`bottomPct`/`heightPct` are computed against. Passing 340 into
`label_visibility` instead of ~240 makes every block's computed `hPx`
look ~42% taller than it actually renders, which **under-fires** the
30/13/11px thresholds (drops fewer labels than the mockup shows, not
more) and would only be caught by eyes-on comparison against the mockup,
not by any geometry test that only checks the thresholds are *applied*
consistently (see Testing). The implementer must derive
`chart_height_px` from the actual CSS height of the element the tower's
`[0,1]` coordinate space is drawn into — the flex child at
`BookKit Web.dc.html:158` (`min-height:130px`, inside the `flex:1
min-height:186px` region above the retention/label rows), not the 340px
outer box at line 146 — and should not re-derive this number from the
panel's outer CSS by guessing later; it belongs measured or hardcoded
from the actual chart region, with a comment pointing at exactly this
paragraph.

In slice 1 the docked panel is a **fixed CSS layout** (not resizable), so
this pixel math is exact once derived from the right box, not a guess
about an unknown viewport — this spec deliberately does not attempt a
resizable/fluid tower canvas (see Scope).

### D5 — Writes: load → mutate → validate → dump → re-project, guarded

`bookkit/sync.py`'s `write_through` (`sync.py:1009-1038`) already is this
contract: load the file, run a mutation closure, validate, and only then
write — refusing outright if the on-disk hash moved since projection
(`WriteConflict`, `sync.py:1026-1029, 1005-1006`). `update_layer`,
`add_layer`, `add_participant`, `update_program` (`sync.py:760-876`) are
already the exact primitives a layer-editing web form needs, and
`mcpserver.py` already calls them this way — nothing towerkit- or
sync.py-side is new here. What's new is wiring the **web** onto the same
path the MCP server already validated in production.

**Extract, don't duplicate, the write-and-snapshot wrapper.**
`mcpserver._program_write` (`mcpserver.py:1137-1160`) already does exactly
what a web route needs: capture the file's pre-image
(`program_files.capture`), run the batched `sync.*` writer inside
`open_batch`, and raise on `Diagnostics` errors
(`_raise_on_errors`, `mcpserver.py:1126-1134`) — including a
`WriteConflict`, which `sync._mutate` (`sync.py:961-975`) already folds
into a `Diagnostics` error with `code="conflict"` rather than letting the
exception escape. This spec moves `_program_write`, `_raise_on_errors`,
and a **new** `ProgramWriteRefused(ValueError)` exception (see below) into
`bookkit/services/program_files.py`, next to `capture`/`restore`
(`services/program_files.py:30-64`). `mcpserver.py` becomes a thin caller
of the service; `bookkit/web/routes/program.py` becomes the second caller.
This is the same move the original web design already made once for forms
(`tui/widgets/entity_forms.py` → `forms/entities.py`,
`2026-08-17-web-frontend-design.md:129-136`) — one seam, two callers,
instead of a hand-copied twin.

`ProgramWriteRefused` carries the `Diagnostics`, not a flattened string:

```python
class ProgramWriteRefused(ValueError):
    def __init__(self, diags: Diagnostics) -> None:
        self.diags = diags
        super().__init__("; ".join(d.message for d in diags.errors))
```

This matters because a program write can fail for two *differently
handled* reasons and the web needs to tell them apart, while
`mcpserver.py` (which only ever surfaces a flat string to an MCP client)
does not:

- an ordinary **validation refusal** (over-signed layer, bad money, a
  layer id that no longer exists) — handled exactly like every other web
  form refusal already is: `ValueError` propagates out of `open_batch`,
  the transaction rolls back, and the route re-renders the form/cell
  partial with the submitted value and the message
  (`2026-08-17-web-frontend-design.md:221-228`, the existing `_Refused`
  contract). `ProgramWriteRefused` *is* a `ValueError`, so this happens
  for free with no new code in the common case.
- a **conflict** (`code == "conflict"` in `exc.diags.errors`) — the route
  checks for this one code specifically and renders a different partial:
  a three-way choice, matching the one towerkit's own TUI already offers
  on the identical situation (`StaleFileModal`, wired at
  `tui/screens/editor.py:1763-1776`, itself downstream of
  `EditSession.save`'s `StaleFileError`, `tui/session.py:123-138`):
  - **Reload** — re-project the file
    (`bookkit.sync.project(conn, path)`) so the placement's recorded
    `source_sha256` catches up with what's on disk now, discard the
    user's draft value, and re-render the field from the fresh read.
  - **Overwrite** — re-project (as above) **and then re-apply the same
    mutation** the user just submitted, by calling the identical
    `sync.update_layer(...)`/`add_layer(...)` a second time. Because the
    hash check now passes (the placement was just re-synced), this
    succeeds and lands the user's edit on top of whatever the file holds
    now — it is a *retry*, not a clobber of the other change.
  - **Keep editing** — do nothing; leave the field/form open with the
    typed value and the conflict message, exactly like an ordinary
    refusal.

  **This is a deliberately narrower "overwrite" than towerkit's own TUI
  offers**, and that narrowing is the point, not an oversight.
  `EditSession.save(force=True)` (`session.py:123-144`) force-writes the
  **entire in-memory program** the TUI has been editing, discarding
  whatever the conflicting write actually changed — correct there because
  the TUI holds one long-lived edit session and "overwrite" means "my
  whole session is authoritative now." The web has no such session: each
  POST is a single field, freshly loaded, mutated, and dumped. Reusing
  `EditSession`'s blunt force-write semantics on the web would silently
  discard a structural change (a new layer the towerkit TUI or another
  MCP call just added) that has nothing to do with the one field the user
  is editing. The web's "overwrite" reduces to "load fresh, reapply my one
  change" precisely because `write_through` already loads fresh on every
  call — **no new `force` parameter is needed on `sync.write_through`**,
  which is a smaller change than it first looks.

**Batch source and undo.** `open_batch` already accepts
`source='mcp' | 'tui'` and the parent spec adds `'web'`
(`2026-08-17-web-frontend-design.md:147`). Program writes route through
`_program_write` with `source="web"`, `tool="program_layer_edit"` etc.
(same `tool` names the MCP tools already use — `mcpserver.py:1191,
1214, 1235`), so the parity ledger's per-batch labelling stays uniform
across MCP and web. **Reverting a program-file batch never goes through
`services.batches.revert`** — `revert()` itself
(`services/batches.py:313-319`) explicitly refuses any batch whose
`tool.startswith("program_")`, before `plan_revert` (`services/batches.py:193-284`,
which computes the batch's net effect generically) is ever consulted,
because "file contents are not event_log rows"
(`services/program_files.py:1-9`). The right-rail `Revert` link
(visual-direction spec, "RECENT CHANGES," `2026-08-17-web-visual-direction.md:170-172`)
needs a dispatcher on program-tool batches identical to
`mcpserver._program_revert_file` (`mcpserver.py:1267-1300`) — this too
should move into `services/program_files.py` rather than be reimplemented
in `bookkit/web/routes/program.py`, for the same one-seam reason as
`_program_write`.

### D6 — Money stays cents-on-the-wire, dollars-in-the-file

No new rule is needed here; the existing boundary already does the right
thing and the web just has to use it, not reinvent it. Layer money fields
(`attach`, `limit`, `premium`) are entered as cents strings
(`bookkit.money.parse_money_cents`, `money.py:35-53`, already accepts
`"1,234.56"`) and passed to `sync.update_layer(..., attach_cents=...)`,
which internally calls `cents_to_dollars`
(`sync.py:985-989`, via `_require_dollars`) — **refusing**, not rounding,
a sub-dollar amount, and surfacing that refusal as a `ValueError` inside
the mutation closure, which `_mutate` turns into a `Diagnostics` error
(`sync.py:966-970`), which becomes a `ProgramWriteRefused` the web renders
in place. This is the exact chain `mcpserver._program_layer_edit`
(`mcpserver.py:1224-1264`) already exercises. The only new work is a
`Field(..., kind="money")` on the layer's inline-cell definition (D7),
which already exists as a documented `Field.kind` value
(`forms/spec.py:32`, `# text | textarea | select | date | money | int`)
backed by exactly this parser (`forms/spec.py:16`).

**One real gap, not previously needed:** participant share (`share_bps`)
has no matching `Field.kind`. The existing kinds — text, textarea, select,
date, money, int — don't correctly round-trip a percentage; `"33.33%"` /
`"33.33"` / `"1/3"`-adjacent input needs `towerkit.money.parse_share` via
`bookkit.money.parse_share_bps` (`money.py:82-88`), which enforces the
one bps rule bookkit already delegates to towerkit
(project `CLAUDE.md`: "Shares: one percent→bps rule owned by towerkit
`money.parse_share`; bookkit delegates"). This spec adds **one new
`Field.kind`, `"share"`**, wired to that parser and to
`towerkit.money.format_share` for display — a small, mechanical addition
to `forms/spec.py`, not a new concept.

### D7 — Layer/participant editing rides the inline-cell seam, not FormSpec

The prototype's layer inspector (`BookKit Web.dc.html:211-250`) is
click-a-value-to-edit-it, one field at a time, `f.isText`/`f.isInput`
swapping in place — the same shape as the contacts panel's already-built
cell contract, not a whole-record `FormModal`/`FormSpec` submit. That
contract is settled: `GET .../cell/{key}`, `GET .../cell/{key}/edit`,
`POST .../cell/{key}` (`web/routes/relationship.py:8-17`), backed by one
`Field` tuple per entity in `bookkit/forms/inline.py`
(`CONTACT_FIELDS`, `TASK_FIELDS`, `RFI_ITEM_FIELDS`, `forms/inline.py:17-36`)
shared with the TUI's own inline editing
(`tui/screens/navigator.py:82-83`, `tui/screens/account.py:127`).

**Decision: add `LAYER_FIELDS` and `PARTICIPANT_FIELDS` to
`forms/inline.py`**, and give `program.py` the same three-route cell
contract `relationship.py` already has, pointed at `sync.update_layer` /
`sync.add_participant` (through the `services.program_files` wrapper, D5)
instead of a plain `repo.*` call. `applies_to` (the chip-toggle,
`BookKit Web.dc.html:228-230`, `toggleLine`) is not a `Field` — it's a
multi-select over `program.lines`, closer to a checkbox group than an
inline cell — and gets its own small POST endpoint
(`sync.py` has no `set_applies_to` write-through yet; `towerkit.edit.set_applies_to`
exists, `edit.py:194`, but nothing in `sync.py` wraps it the way
`update_layer` wraps other field mutations — **this is new `sync.py`
surface the plan needs to add**, not something this spec found already
built).

Creating a new layer or participant does **not** fit the inline-cell
contract (there's no existing row to click into). It follows the visual
direction's general add pattern instead — a ghost `+ Add …` row that
appends and opens straight into edit mode on its first field
(`2026-08-17-web-visual-direction.md:211-215`), matching
`BookKit Web.dc.html:251-253` (`+ Add participant`) and the layer list's
own `+ {{ pane.addLabel }}` row (line 139-141 in the account page, same
pattern already built for contacts).

**The `forms/` FormSpec seam is untouched by this whole sub-project.**
Whole-record modal forms (`org_form`, `contact_form`, etc.,
`forms/entities.py`) have no towerkit equivalent to add — every towerkit
write here is single-field or single-row-append, which the inline-cell
and ghost-row patterns already cover completely.

### D8 — Scope and sequencing

Each slice below produces something that runs end-to-end and is
independently useful; risk (D2/D4's rendering question, D5's write
conflict) is front-loaded rather than saved for last.

1. **Read-only tower panel.** `layout.py`/`scale.py` piped through the new
   `render/web.py` into the docked panel HTML for a program's current
   state — no editing, no writes. Proves D2/D3/D4 end to end: geometry
   in, correctly-thinned percentages and labels out, "not to scale"
   caveat, gutter labels, chevron band for statutory layers, group bands.
   Placements list on the Program tab (replacing the stub) links a
   placement to this read. This slice alone retires the biggest technical
   unknown before any write-path work starts.
2. **Layer/participant inline editing.** `LAYER_FIELDS`/
   `PARTICIPANT_FIELDS` (D7), the extracted `_program_write` /
   `ProgramWriteRefused` (D5) with the plain-refusal path only (no
   conflict UI yet — a conflict is rare enough in slice-2 testing to defer
   its UI one slice and just show it as an ordinary refusal message
   initially). `add_layer` / `add_participant` via the ghost-row pattern.
   Flips `parity.py`'s `edit_layer`/`add_layer` entries
   (`web/parity.py:51-52`) from PENDING to IMPLEMENTED.
3. **The conflict three-way (Reload/Overwrite/Keep editing) and the
   right-rail Revert dispatcher** for program-tool batches (D5's second
   half). This is small in code but is exactly the kind of "surface,
   don't guess" correctness this codebase has been burned by skipping
   before — worth its own slice so it gets a fresh-eyes review on its
   own.
4. **`applies_to` chip toggle, restack, and the resize-drag island.** The
   one JS "island" the parent spec anticipated
   (`2026-08-17-web-frontend-design.md:37-40`). Drag the top edge of a
   layer, drop, POST, server calls `restack()`-equivalent and re-renders —
   see the "recommend against" section for why this slice does *not*
   attempt live drag-preview geometry. `towerkit.edit.restack`
   (`edit.py:212-220`) already exists and is already live in the TUI
   (`EditSession.restack`, `session.py:172-177`, calling
   `self.mutate(edit.restack)`; bound at `editor.py:1974-1975`,
   `action_restack`) — the only new code this slice needs is a `sync.py`
   write-through wrapper around the existing `towerkit.edit.restack`
   (the same five-line shape as `sync.py:785-821`), **not** a
   reimplementation of restacking itself.
5. **Export SVG/PDF, Towers browser page, Compare screen.**
   `render/mpl_program.py` wired to a `/accounts/{ref}/program/export`
   download route (unblocking the same file-download-response gap the
   parent spec's `export_open_items` ledger entry already names,
   `web/parity.py:63-69`); a `/towers` page listing
   `programs/*.json` with the validation badge already computed by
   `validate_file` (`browser.py:100-106`); `compare_programs`
   (`compare.py:86`) rendered as the delta table the mockup shows
   (`BookKit Web Screens.dc.html:240-261` for validation/writes,
   `:430-...` for the compare row shape) — read-only, no tower graphic
   (see "Recommend against SVG-in-Compare" below).
6. **`renew_placement`, `scaffold_tower`, `open_towerkit`** — the
   remaining PENDING towerkit-touching entries in `parity.py`
   (`web/parity.py:50, 60-61, 53-58`). Each wraps an existing `sync.py`
   function (`renew`, `scaffold_program`) that needs no new towerkit code,
   only a route and a confirmation screen (renew and scaffold are both
   destructive-ish enough — creating files — to want the server-rendered
   confirm-then-POST pattern the parent spec already established,
   `2026-08-17-web-frontend-design.md:247-250`).

**Explicitly not in this sub-project:**

- A resizable/fluid-height tower canvas. D4's pixel thresholds are exact
  because the panel height is fixed CSS; a resizable canvas would need
  either a client-side re-measure round-trip or a different, harder
  fitting strategy, and nothing in the current design package asks for
  one.
- Live drag-preview geometry during a resize gesture (see "recommend
  against").
- `render_options` (theme/totals/premiums/cell-extras toggles,
  `browser.py:392-412`) as a web control — the web has exactly one
  theme by design-direction decision already
  (`2026-08-17-web-visual-direction.md:81`, "Single theme by choice"), and
  totals/premiums/cell-extra toggles are a small enough win to defer
  behind evidence of demand.
- The layers-sheet bulk-edit view (`editor.py:886`, `action_layers_sheet`)
  — see "what should not be ported."
- xlsx SOI export (`editor.py:1844`, `action_export_soi`) — same deferred
  file-download-response gap as the parent spec's `export_open_items`
  entry (`web/parity.py:63-69`).

## What is hardest, and what could go wrong

**1. ~~Two independent file-staleness trackers~~ — checked against the
code, and this hole is already closed on both sides; the risk here is
smaller than it first looks, not absent.** It's tempting to assume
`EditSession`'s `self._disk_sha` (`session.py:154`) is a cached value
captured once at `open()` that a later web write could sneak past — it
isn't. `EditSession.save` re-reads the file's *current* hash at save
time, live, via `_file_sha(target)` compared inline
(`session.py:134`), and `bookkit.sync.write_through` does the identical
thing on the write side — `file_sha256(path)` is called live, at write
time, against the placement's recorded `source_sha256`
(`sync.py:1026-1029`). Neither side is comparing against a stale
snapshot from whenever it happened to open the file; both re-read disk
at the moment they're about to write. So the actual sequence — broker
opens a program in towerkit's TUI, edits the same layer's premium on the
web, then saves in the TUI — **does** get caught: the TUI's save re-reads
the file's hash at that moment, sees the web's write is not the hash it
last saw, and raises `StaleFileError`
(`session.py:135-138`), which the editor screen turns into
`StaleFileModal`'s three-way choice
(`editor.py:1763-1776`) exactly as D5 describes for the web's own
conflict handling. The reverse order (TUI writes, then the web writes)
is caught the same way by `write_through`'s own hash check. There is no
second, independent staleness cache anywhere in this path for the two
surfaces to disagree about.

What genuinely isn't covered — and is a much narrower gap than "two
trackers can silently diverge" — is a **live, in-editor notification**
that the file changed while a session is *still open and unsaved*: the
towerkit TUI (like the web's own inline-cell fields) only notices a
conflict at the moment it tries to write, not continuously while the
broker is mid-edit. That's the same "which surface wins a race, discovered
only at commit time" shape D5's own conflict handling already exists to
answer, applied a second time across processes rather than within one —
not an open hole, just the same mechanism operating at a coarser grain.
Given that, this document's recommendation stands unchanged: rely on the
conflict check that already exists on both sides rather than building any
new cross-process coordination — there was never a hole here needing one
built.

**2. The label-drop constants are a *design* judgment wearing pixel
clothes**, not a derived quantity. 30/13/11/12px came out of one
designer's eyeballing of one mockup at one panel size (340px, one column
width, `NotoSans`/`JetBrains Mono` at specific point sizes). They will
look wrong the day the panel's fixed height, font stack (still Georgia/
system-ui fallback per the visual-direction doc's flagged deviation,
`2026-08-17-web-visual-direction.md:91-92`), or column count changes
materially, and there's no automatic way to notice — `test_scale.py`-style
geometry tests can assert the *thresholds are applied consistently*, but
not that they're still *visually right*. This needs an eyes-on check
whenever the fonts actually ship (Task 16's packaging work) or the panel
height changes, not just a green suite.

**3. The write-conflict "Overwrite = reload-then-reapply" design (D5) is
correct for single-field edits and silently wrong the moment any future
write bundles more than one field mutation into one `mutation` closure.**
If a later slice adds a "bulk edit this layer's four fields at once" form
that calls one `mutate()` closure setting `layer.name`, `.attach`,
`.limit`, and `.premium` together, "reapply the same mutation on the
fresh file" reapplies *all four*, including any that the conflicting
writer didn't touch and the user didn't mean to re-assert — a narrower,
quieter version of the exact clobber this design was built to avoid.
Anyone adding a multi-field program write later needs to re-read D5's
reasoning, not just copy its shape.

## What should not be ported

- **The layers-sheet bulk-edit view** (`v` in the editor,
  `editor.py:886`, `action_layers_sheet`) is a keyboard-dense spreadsheet
  affordance with no obvious mouse-first equivalent. The inline-cell tower
  inspector already covers single-layer editing; a dedicated
  "spreadsheet of every layer" screen should stay a TUI-only tool until
  there's evidence a broker actually reaches for it, per the parity
  ledger's own PENDING-with-reason discipline rather than being silently
  dropped or blindly ported.
- **The `x`/mark-then-compare gesture** (press `x` on file A, then `x` on
  file B, `browser.py:372-390`) is a two-keystroke terminal idiom with no
  mouse-first translation. The mockup's "Compare renewal" pill
  (`BookKit Web Screens.dc.html:229`) implies a direct action from a
  selected program to whatever it renews from/into, not a marking
  gesture — but the mockup does not show how that pairing is resolved
  (see Open Questions).
- **`=` restack as a bare keystroke.** The action survives (a layer resize
  must still call `restack()`-equivalent afterwards, D8 slice 4) but never
  as a keyboard-only affordance — per the visual-direction stance, "Never
  keyboard-only... every action needs a visible control"
  (`2026-08-17-web-visual-direction.md:41-42`). It becomes an explicit
  confirm step in the resize-drag flow, not a standalone button a broker
  has to remember exists.
- **The editor's esc-with-unsaved-changes three-way** (Save & exit /
  Discard / Keep editing, `editor.py:1982-1997`) has no web equivalent to
  build, because it guards a *session-scoped* editor screen the web
  doesn't have — every web edit commits (or refuses) immediately per
  field, per the inline-cell contract, so there is no "unsaved changes on
  screen exit" state to protect. The one residual case — navigating away
  mid-drag on the resize gesture — is small and should be handled the
  simplest possible way (the drag has no server effect until drop, so
  navigating away mid-drag just abandons an uncommitted gesture, same as
  today's `blur cancels` rule for inline cells).
- **`t` render options** (theme/totals/premiums/cell-extras,
  `browser.py:392-412`) — see Scope; not ported this sub-project, single
  theme by design decision.

## Where the existing bookkit web work needs to change

- **Route split.** `bookkit/web/app.py:59-69` registers routers in an
  order that matters (`relationship` before `account` because both match
  `/accounts/{ref}/{tab}`, and Starlette resolves by registration order,
  not specificity — the comment there is explicit about this). A new
  `program.router` needs the same care: any more-specific pattern under
  `/accounts/{ref}/program/...` (layer cell routes, the add-participant
  ghost row, the export download) must register **before**
  `account.router`'s generic `GET /accounts/{ref}/{tab}`, exactly like
  `relationship`/`work`/`pipeline` already do.
- **`forms/inline.py`** gains `LAYER_FIELDS`, `PARTICIPANT_FIELDS` (D7).
- **`forms/spec.py`** gains one `Field.kind`, `"share"` (D6).
- **`services/program_files.py`** gains the write-and-snapshot wrapper and
  the revert dispatcher, both moved out of `mcpserver.py` (D5).
  `mcpserver.py` shrinks; nothing about its tool contract changes.
- **`sync.py`** gains two write-through wrappers that don't exist yet:
  one over `towerkit.edit.set_applies_to` (D7) and one over
  `towerkit.edit.restack` (D8 slice 4). Both are the same five-line shape
  every existing `sync.py` mutation wrapper already is
  (`sync.py:785-821` is the template).
- **`web/parity.py`** — `edit_layer`, `add_layer` flip to IMPLEMENTED in
  slice 2; `renew_placement`, `scaffold_tower`, `open_towerkit` flip in
  slice 6 (D8). No entry needs deleting; `open_towerkit`'s split-key note
  (`parity.py:53-58`, the projects-tab `_need_to_opportunity` half) stays
  PENDING on the projects side even after the towerkit half ships, and
  the ledger's own "stale entry" test (`test_web_parity.py`, not fully
  read here but referenced by name) should catch a mismatch either way.
- **The `forms/` (whole-record) seam itself does not change** — see D7.
  This sub-project adds zero new `FormSpec` builders.

## Testing

Follows the parent spec's existing pattern (`TestClient` against a seeded
temp DB, seam assertions over outcome assertions,
`2026-08-17-web-frontend-design.md:264-291`) with towerkit-specific
additions:

- **Geometry tests for `render/web.py` live in towerkit's own suite**,
  alongside `test_scale.py` and `test_layout.py` (referenced, not read in
  full here) — pure `TowerLayout` in, `WebLabels` out, no bookkit
  fixtures needed. Mutate a threshold constant, confirm the test fails,
  per towerkit's own testing-discipline rule
  (`towerkit/CLAUDE.md:96-101`, "a test must be capable of failing").
- **The seam test for a program write**: POST a layer edit, assert a
  batch row exists with `source='web'`, `tool='program_layer_edit'`, that
  `program_files.capture` wrote a snapshot keyed to that batch's ref, and
  that the file on disk actually changed — not merely that the response
  looked right. This is the same class of test the parent spec already
  calls out as necessary because a green *outcome* test would have missed
  33 bypassed `FormModal` call sites once before
  (`2026-08-17-web-frontend-design.md:270-277`).
- **The conflict three-way**: write the file out-of-band (simulating the
  towerkit TUI), then POST the same layer edit through the web; assert
  the response is the conflict partial (not a generic refusal), that
  nothing was written, and that "Reload" then "Overwrite" (in sequence)
  lands exactly the user's edit on top of the out-of-band change — not a
  clobber of it. Directly exercises the D5 "reload-then-reapply" claim,
  which is otherwise just a paragraph of reasoning until a test forces it.
- **The money boundary refusal**: POST a sub-dollar-cents layer premium
  (e.g. `"$1,234.56"`); assert `cents_to_dollars`'s refusal
  (`money.py:74-79`) surfaces as the field's error text, not a silent
  rounding, and that the file is untouched.
- **The label-drop constants applied consistently**: at the fixed
  chart-drawing-area height derived per D4 (not the 340px panel box —
  see D4's correction), assert a layer block whose computed height
  crosses the 30px/13px/11px lines flips its `show_name`/`show_money`
  flags exactly at the boundary — this cannot assert the thresholds are
  *visually* correct (see "what could go wrong" #2), only that the same
  numbers the design specified are the ones actually wired in, against
  the right base height.
- **Full round-trip determinism, unchanged**: `render/mpl_program.py`'s
  existing byte-identical-output tests are untouched by this sub-project
  and should stay green with zero modification — a strong, cheap check
  that the export path was in fact left alone.

## Recommend against

**Live drag-preview geometry during the layer-resize gesture (D8 slice
4).** The tempting version — as the mouse moves, recompute and repaint
the whole tower's percentages in real time so the drag *feels* like
manipulating a real tower — requires either (a) running `scale.py`'s
gamma-compression math in JavaScript on every `mousemove`, which is
exactly the client/server duplication D2 already rejected once, just
relocated to a hot path where the temptation to "just inline the formula,
it's small" is highest, or (b) a round-trip to the server on every
`mousemove`, which is a latency and load problem for a gesture that fires
dozens of times a second. Recommend instead: the drag handle moves a
**single unscaled visual affordance** (a plain pixel-offset line showing
where the pointer is, with no attempt to reflow the rest of the tower),
and only on **drop** does the client POST the new attach value and let
the server compute the real, `restack()`-corrected geometry and
re-render. This is a real loss of polish relative to the mockup's implied
smoothness, and it is the right trade for a slice that hasn't yet proven
the gesture is worth building at all — revisit once slice 4 ships and
someone has used it.

**Rendering the Compare screen's tower as a graphic at all, in this
sub-project.** The mockup's Compare screen (`BookKit Web Screens.dc.html`)
is a delta *table* (`compare.py`'s `DeltaRow`s), not two towers side by
side, and `render/mpl_renewal.py` (referenced but not read in detail here)
already exists as the print-renewal-comparison chart for exactly this
purpose. Building a second interactive rendering of two towers
side-by-side, on top of everything else in this sub-project, is scope
inflation the design package doesn't actually ask for — the delta table
plus a link to the existing print renderer for the graphic covers the
same ground at a fraction of the cost. If Grant wants a browser-native
side-by-side tower comparison later, it's a clean follow-on slice, not
part of this one.

## Open questions

- ~~The two-staleness-tracker hole~~ **— not actually open.** Earlier
  drafting of this document diagnosed this as an unclosed gap; checked
  against the code, it isn't one (see the rewritten item 1 under "what
  could go wrong"). Both `EditSession.save` and `sync.write_through`
  re-read the file's hash live, at write time, so a web write followed by
  a TUI save (or the reverse) is caught by the existing `StaleFileError`/
  `WriteConflict` machinery on whichever side writes second. No file-level
  lock or cross-process "who has this open" indicator is needed, and none
  is proposed. Left here, struck through rather than deleted, so a future
  reader who remembers this being called out doesn't have to re-derive
  that it was resolved rather than dropped.
- **How "Compare renewal" resolves its pair.** The mockup shows the
  button; nothing in the design package specifies whether it auto-detects
  the adjacent program (by org + period adjacency, mirroring
  `sync._placement_for`'s exact-period matching, `sync.py:286-294`) or
  opens a picker. This is a product call, not a technical one — my
  recommendation is auto-detect with a picker fallback when it's
  ambiguous, mirroring the exact ambiguity-handling posture
  `sync.AmbiguousPlacement` already takes elsewhere in this codebase
  (`sync.py:82-88`), but Grant should decide.
- **Whether `bookkit/sync.py`'s existing direct `.append()` calls (flagged
  under D1) should be brought in line with `towerkit.edit` as part of this
  sub-project or left as pre-existing debt.** I did not attempt to fix it
  here — it's orthogonal to the web conversion and touches code this spec
  otherwise leaves alone — but a reviewer should decide explicitly rather
  than this staying an unnoticed gap. My recommendation: fix it opportunistically
  in D8 slice 2, since that slice is already touching `sync.py`'s
  `add_layer`/`add_participant` neighbourhood and adding `set_applies_to`
  next to them — cheaper to fix while already there than as a separate
  pass later.
