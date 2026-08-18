<!-- DRAFT — NOT APPROVED. Read the verification report at the bottom before building from this. -->

> **Status: DRAFT — NEEDS REVISION** (2026-08-18).
> Produced by a drafting pass, then checked by an independent adversarial pass that
> opened every citation and challenged the load-bearing claims.
> **64 citations checked · 6 failed · 11 claims challenged.**
> Kind: `spec`.
> The verification report at the bottom is PART OF THIS DOCUMENT — some of its findings
> would break an implementation built from the body above it.

---

# Amendment R66 — the two renderers must agree about facts, not fit

Date: 2026-08-18
Amends: `docs/superpowers/specs/2026-08-17-towerkit-web-conversion.md`
Status of this amendment: ready to splice. Every claim below was checked
against the code before it was written; the checks are logged at the end.

---

## What this amendment does

Grant approved D2's two-renderer split on 2026-08-18. That approval creates
one constraint (R66) that D2 as written does not satisfy, and one paragraph
inside D2 that now contradicts it. This amendment:

1. changes the spec's status line;
2. inserts a new subsection **D2.1** recording the approval and the
   agreement constraint, and **replaces spec lines 201–243** — the
   "Explicit decision: label *fit* is not guaranteed…" paragraph and the
   "Two ways to close it…" paragraph that follows it;
3. corrects D4's `label_visibility` signature, which cannot produce a layer
   heading at all as declared;
4. replaces one Testing bullet and adds the agreement test;
5. adds one exit condition to D8 slice 1.

---

## The resolution, stated once

**The two renderers must agree about the FACTS a block asserts. They are
allowed to differ about which candidate string a fitter picked to assert
them.**

That line is not a compromise invented for this amendment — it is the line
towerkit already drew, in code, the last time it had this exact problem.
`render/labels.py` exists and its own docstring calls itself "the single
authority both renderers (the matplotlib graphic and the xlsx schematic
worksheet) quote, so a block reads identically on the chart and in the
cells" (`towerkit/src/towerkit/render/labels.py:1-3`). Its test module
opens with "Shared block-label authority: both renderers must quote these
exactly" (`towerkit/tests/test_labels.py:1`). Both consumers import it
(`render/mpl_program.py:29-38`, `render/schematic_xlsx.py:82-93`, whose own
docstring says "label text from render/labels.py — both shared with the
graphic," `schematic_xlsx.py:7-8`).

And those two renderers **fit** by completely different means. The graphic
measures real text extents and walks a candidate ladder
(`mpl_program.py:332-370`). The worksheet quantizes onto a row grid and
floors each label's band to its own rendered line count
(`schematic_xlsx.py:15-27`), dropping through a narrow-merge ladder to
`carrier_only_label` (`labels.py:40-44`). Nobody has ever required those two
to drop the same labels. They are required to *say the same things*.

The web panel is the fourth renderer, not a new species. D2's approval does
not change that; it only raises the stakes, because for the first time two
renderings of the same program will sit in front of the same broker minutes
apart.

**Why the fit cannot be unified, as a matter of fact and not of budget.**
The export measures against matplotlib's own font metrics, inside a
13.5×9.5in figure, with bundled Noto faces registered explicitly for
determinism (`render/common.py:21-27`, `mpl_program.py:65`). The panel lays
out in a browser box roughly 240px tall, in whatever the browser resolves.
Matching *decisions* would require matching *metrics*, and there is no
server-side way to obtain browser metrics in the request path. A unified
fitter is therefore not expensive — it is unavailable. R66 is satisfied at
the facts layer or it is not satisfied at all.

**Why label presence is not a fact.** Take the harmful reading seriously:
the broker reads a name on screen, exports, and the client's SVG shows a
bare initial. That is real, and it is also what every towerkit user gets
today — the export is unmodified, deterministic, and has always been the
only client-facing renderer. Nothing regresses. The export never becomes
*wrong*; it becomes *terser*, by a rule that deliberately prefers the layer
name over everything else in the cell ("the layer NAME is the most important
line in the cell: every heading-bearing combination is tried — down to the
heading alone — before any headless fallback", `mpl_program.py:313-315`,
ladder at `312-325`, terminal fallbacks at `328`). The reverse direction —
panel drops a short block's name, export prints it — leaves the client
deliverable strictly more informative. Neither direction misleads a client.

**What WOULD mislead a client** is a fact divergence: a different money
figure, a different share, a different terms string, a different layer
carrying the name, a pending layer reading as an open remainder, or
different geometry. Those are all preventable, and four of them are not
prevented by anything currently in D2 or D4.

---

## Amendment 1 — the status line

**Replaces spec lines 3–7.**

> Date: 2026-08-17 (D2 amended 2026-08-18)
> Status: **D2 approved; the rest is draft, for review.** Grant approved the
> two-renderer split on 2026-08-18 — "HTML fine for the UI and interface
> rendering with SVG being able to be exported just as the TUI" — which
> unblocks towerkit slice 1 past `render/web.py`. Nothing in this document
> is implemented yet. It extends `2026-08-17-web-frontend-design.md` and is
> bound by `2026-08-17-web-visual-direction.md`. Where this document and
> either of those disagree unintentionally, they win and this document is
> wrong.

---

## Amendment 2 — new subsection D2.1

**Insert after spec line 199 (the end of the "Rejected: reimplement
`scale()`/`buildTower()`…" paragraph) and DELETE spec lines 201–243** — the
"Explicit decision: label *fit* is not guaranteed…" paragraph through
"…(see 'recommend against,' below)." D3's heading at line 245 follows
unchanged.

---

### D2.1 — Approved, and what the approval obliges (R66)

**Grant approved this split on 2026-08-18:** *"HTML fine for the UI and
interface rendering with SVG being able to be exported just as the TUI."*
D2 stops being a recommendation. towerkit slice 1 is unblocked past
`render/web.py`.

The approval creates the constraint. Two renderers now describe one program,
and the export is the one that reaches a client. A tower that reads
correctly on screen and exports differently is worse than either alone.

**The agreement rule. Both renderers must agree about the facts a block
asserts; they may differ only about which candidate string a fitter chose to
assert them with.**

Agreement is required on all five of these, and slice 1 must make each
structurally impossible to diverge rather than merely likely to match:

1. **Geometry.** Both consume one `TowerLayout` from `build_layout`
   (`layout.py:135`), at the same gamma. `TowerLayout` is a frozen dataclass
   of frozen dataclasses and float tuples (`layout.py:121-132`), so `==` is
   an exact test, not a tolerance. The panel and the export route must pass
   the same `gamma`; `render_program`'s default is `DEFAULT_GAMMA`
   (`mpl_program.py:54`, `scale.py:24`) and the panel must not acquire a
   gamma control (D8 already defers `render_options`).
2. **Label text.** `render/web.py` quotes `render/labels.py` for every
   string. It composes none of its own. This is the existing rule
   (`labels.py:1-3`, `tests/test_labels.py:1`), extended to a fourth
   consumer, not a new rule.
3. **Which block carries the layer heading.** `heading_blocks`
   (`labels.py:71-83`) already answers this and is already shared
   (`mpl_program.py:132`; tested at `tests/test_labels.py:62-70`). The web
   calls it. It does not pick the leftmost block, or the lead share.
4. **Whether a layer is pending.** `pending = {ly.layer_id for ly in
   tower.layers if ly.signed_bps == 0}` decides whether an unplaced block
   says "To be placed" or "N% open" (`labels.py:47-50`). That is a fact
   about the program, and it is **already copy-pasted three times** —
   `mpl_program.py:128`, `schematic_xlsx.py:241`, `schematic_xlsx.py:437`.
5. **Whether a layer follows underlying.** `follows = {ly.id for ly in
   program.layers if ly.follows_underlying}` decides whether the terms read
   "$4M xs $1M" or "$4M xs underlying" (`labels.py:24-33`). Also a fact,
   also **already copy-pasted three times** — `mpl_program.py:116`,
   `schematic_xlsx.py:240`, `schematic_xlsx.py:438`.

**Decision: lift (4) and (5) into `render/labels.py` as `pending_layers(tower)`
and `follows_layers(program)` before `render/web.py` is written, and make all
four call sites use them.** This is the codebase's own rule applied where it
was declared: an identity guard belongs where the thing is declared, not in
each caller that happens to need it (bookkit `CLAUDE.md:51-57`,
`CLAUDE.md:20-24`). A fourth hand-rolled copy of `signed_bps == 0` is a
landmine that only fires as a mislabelled client chart. The refactor is
output-identical by inspection — the same set comprehension moved — so the
export's determinism tests (`towerkit/CLAUDE.md:27`) are unaffected.

*Cost if wrong:* if these stay duplicated, a future change to what "pending"
means lands in one or two renderers and the fourth silently keeps the old
meaning. The failure surfaces as a client PDF that disagrees with the screen
about whether cover is placed — the exact R66 failure, arriving by the
cheapest possible route.

**What the two renderers MAY differ on, and only this:** which member of a
label's candidate ladder is finally drawn — the full form, a `textwrap`ped
form (`mpl_program.py:288-292`, `317-320`), the heading alone, a bare
initial (`mpl_program.py:328`), or nothing at all
(`mpl_program.py:344-346`, `353-354`: "An empty candidate means give up
silently"). The panel gates on block height (D4's 30/13/11px); the export
gates on measured extents. They will not always land on the same rung.

**One thing this permission does not extend to: silent loss on the
interactive surface.** A refusal that returns in silence is its own bug
(bookkit `CLAUDE.md:100-105`). A panel block that drops its name must still
carry the full `labels.py` string in a `title=` attribute, so the broker can
recover it by pointing at it. That is one attribute, and it converts the
only genuinely bad reading of a dropped label — information the broker
cannot get at — into a display choice the broker can undo by looking.

**Footnote markers are export chrome, not a fact.** `render_program` builds
superscript markers for layers carrying notes and prints the notes in a
footer (`mpl_program.py:41`, `60-63`, `395-415`); `schematic_xlsx` has no
markers at all. The precedent is already set: the marker may differ between
renderers. The panel omits markers and surfaces layer notes in the inspector
instead; the agreement test compares headings computed with `marker=""`.

**The mockup's CSS clipping fix stands** (it was the sound half of the
deleted paragraph): `text-overflow:ellipsis` never fires on an element that
is not `white-space:nowrap`, so the panel wraps at word boundaries and clips
whole lines with `overflow:hidden`. A clean line clip, not a mid-word cut.

**Rejected: give `render/web.py` a monospace-width estimate so it drops the
same labels as the export.** It cannot succeed. The export measures
matplotlib's metrics for bundled Noto faces inside a 13.5×9.5in figure
(`render/common.py:21-27`, `mpl_program.py:65`); the panel is a ~240px
browser box in a different font stack. Matching decisions requires matching
metrics, which are not obtainable server-side. The estimate would buy a
*different* wrong answer plus a constant to keep in sync with a font that is
not yet vendored.

**Rejected: make the export adopt the panel's pixel thresholds so the two
rules are literally one rule.** This regresses the only renderer that does
the harder, correct thing — D4's own third bullet argues this and is right.
It also silently changes what every existing client deliverable says, for
the benefit of a working panel the client never sees.

**Rejected: assert agreement by rendering an SVG and comparing its text to
the panel's.** This is not merely awkward, it is impossible: `svg.fonttype`
is `"path"` (`render/common.py:37`), so the exported SVG contains glyph
outlines and no text nodes. A test that grepped the SVG for "Zenith 25%"
would fail for the wrong reason, or — worse, and this is the shape of
failure this project keeps catching — pass vacuously against a normalisation
that made it always true. Agreement is asserted at the derivation seam and
against the export's `Text` artists on the axes, before rasterisation. See
Testing.

**Type weight, already resolved, recorded here so it is not re-litigated.**
Design's rule that anything rendered *for a client* stays Noto Serif 400
governs the SVG export, not the HTML chrome
(`2026-08-17-web-visual-direction.md:102-114`). The export already complies:
`_titles` sets the insured name with no `weight` argument, under the comment
"brand type rules: serif headings are Regular only, never bold"
(`mpl_program.py:379-382`). The panel's insured-name header is broker chrome
and takes 700, not the mockup's banned synthesised 600. **The two outputs
are allowed to look different. They are not allowed to say different
things.**

---

## Amendment 3 — D4's signature is wrong and must change

**Replaces the signature sketch at spec lines 355–360.**

D4 declares:

```python
def label_visibility(tower: TowerLayout, chart_height_px: float) -> WebLabels:
```

That function cannot produce a layer heading. `layer_heading` needs
`follows` (`labels.py:24-33`), and `LayerBlock` carries no
`follows_underlying` field — the layout drops it (`layout.py:86-97`). It is
read off the `Program` (`mpl_program.py:116`), which is why `draw_tower`
takes a `Program` and not just a layout (`mpl_program.py:82-94`). A
`TowerLayout`-only function can return *flags*, and flags alone leave the
Jinja template to compose the strings — which is precisely how the fourth
renderer would grow its own label vocabulary and break agreement rule (2).

Replace with:

```python
@dataclass(frozen=True)
class BlockLabels:
    layer_id: str
    carrier: str | None
    share_bps: int
    heading: str | None   # labels.layer_heading(..., marker=""), on the
                          # heading_blocks-chosen block only
    body: str             # labels.participant_label | labels.unplaced_label
    money: str | None     # labels.block_premium_label
    show_heading: bool    # 30px / 13px-if-spanning
    show_money: bool      # 11px

def web_labels(
    program: Program,
    tower: TowerLayout,
    chart_height_px: float,
    *,
    cell_premiums: bool = False,
) -> WebLabels:
    """Per-block label TEXT (quoted from render/labels.py) plus per-block
    visibility flags at the given rendered pixel height, and a thinned
    ref_lines list. Pure; no plotting import, no web framework."""
```

The text is carried, not just the flags, so the template interpolates a
string it did not compose. D4's reasoning for the module's *location* is
unchanged and correct, as is its `chart_height_px` correction (the ~240px
drawing region, not the 340px outer box) — that warning becomes strictly
cosmetic-only under this amendment, since the thresholds now decide
visible-or-not and never what a visible label says.

---

## Amendment 4 — Testing

**Replaces the "The label-drop constants applied consistently" bullet at
spec lines 809–816**, and keeps every other bullet in that section as
written.

> - **The renderer agreement test** (`towerkit/tests/test_render_agreement.py`,
>   towerkit's suite — both renderers are towerkit's; bookkit's web only
>   consumes them). One `Program` fixture, rendered both ways in one test:
>
>   *What it renders.* `draw_tower(ax, program, theme)` on a real figure for
>   the export side, and `web_labels(program, tower, chart_height_px)` for
>   the panel side.
>
>   *What it compares.*
>   1. **Geometry, exactly.** `draw_tower` returns the layout it built
>      (`mpl_program.py:94`, `252`); assert it `==` `build_layout(program,
>      gamma)`. Frozen dataclasses of floats make this exact.
>   2. **Facts, per block.** For every `ParticipantBlock`, assert
>      `web_labels`' `heading` / `body` / `money` equal the values the test
>      computes independently from `render/labels.py` plus
>      `heading_blocks`, `pending_layers`, `follows_layers`. This is the
>      seam assertion: it fails the moment the web composes a string
>      instead of quoting one.
>   3. **Export ties to the same facts.** Read `[t.get_text() for t in
>      ax.texts]` — the strings the export actually committed to, before
>      rasterisation — and assert every layer's heading string (computed
>      with `marker=""`) appears among them, and that every drawn
>      participant string is either the shared `body` string or a declared
>      shortening of it (a `textwrap`ped form, or the carrier initial).
>      The fixture is sized so nothing needs to fall through, making this
>      an equality rather than a membership-in-a-large-set.
>
>   *What it does not compare:* the visible glyphs. Which rung of the
>   ladder each fitter chose is the one permitted difference (D2.1).
>
>   *Non-vacuity, per `towerkit/CLAUDE.md:96-101` — "negative assertions and
>   'absent by default' assertions are the usual culprits."* The fixture
>   MUST contain: a layer whose lead share is narrow and whose second share
>   is wide (otherwise `heading_blocks` returns index 0 either way and
>   comparison 2's heading assertion is vacuous); one layer with
>   `signed_bps == 0` **and** one partially open with `signed_bps > 0`
>   (otherwise `pending` never differs from "open" and rule 4 is untested);
>   a `follows_underlying` layer (otherwise `follows` is empty and rule 5 is
>   untested); at least one layer with a non-`None` premium (otherwise
>   `block_premium_label` is `None` everywhere and the money assertion is
>   vacuous); a multi-run layer and a statutory layer.
>
>   *Mutations that must make it fail, run once and recorded in the commit
>   message:*
>   - flip the web's pending predicate to `signed_bps < 10_000` → a block
>     reads "To be placed" where the export reads "40% open";
>   - drop `follows` from the web's `layer_heading` call → "$4M xs $1M"
>     where the export says "$4M xs underlying";
>   - hand the heading to a layer's first block instead of
>     `heading_blocks`' widest → the heading lands on a different carrier;
>   - pass a different gamma to `web_labels` → comparison 1 fails;
>   - inline `f"{carrier} {bps // 100}%"` in the web instead of calling
>     `participant_label` → fails on a 3,333 bps share, where
>     `format_share` gives "33.33%" (`money.py:129-134`).
>
>   *What stays green under mutation, correctly:* changing `layer_terms` to
>   emit "xs $0" for a primary moves both sides together. That is
>   `tests/test_labels.py:29`'s job. This test asserts the web is a
>   *consumer* of the shared authority; it does not re-test the authority.

---

## Amendment 5 — D8 slice 1 exit condition

**Append to spec line 573**, at the end of slice 1's paragraph:

> Slice 1 is not done until `pending_layers` / `follows_layers` are lifted
> into `render/labels.py` and all four call sites use them, and the renderer
> agreement test passes with its five mutations recorded as having failed
> it. A green suite proves nothing broke, not that the new path is taken
> (bookkit `CLAUDE.md:171-175`) — and the seam this slice introduces is
> exactly the kind that goes green while nothing routes through it.

---

## Verification log

Checked before drafting; corrections to the spec's own citations are listed
separately below.

| Claim | Evidence |
| --- | --- |
| `render/web.py` does not exist | absent from `towerkit/src/towerkit/render/` |
| A shared label authority already exists | `labels.py:1-3`; `tests/test_labels.py:1` |
| Two renderers already fit differently over it | `mpl_program.py:332-370` vs `schematic_xlsx.py:15-27` |
| The export can draw nothing | `mpl_program.py:328`, `344-346`, `353-354` |
| The export prefers the layer name above all | `mpl_program.py:312-325`, comment `313-315` |
| Block existence is geometry, not labelling | `mpl_program.py:143-150` (rectangles drawn before any label logic) |
| `pending` is duplicated 3× | `mpl_program.py:128`, `schematic_xlsx.py:241`, `:437` |
| `follows` is duplicated 3× | `mpl_program.py:116`, `schematic_xlsx.py:240`, `:438` |
| `LayerBlock` has no `follows_underlying` | `layout.py:86-97` |
| The exported SVG has no text nodes | `render/common.py:37` (`svg.fonttype: "path"`) |
| The export already honours Noto Serif 400 | `mpl_program.py:379-382` |
| The docstring's "not-to-scale caveat" claim is stale | no caveat drawn anywhere in `mpl_program.py`; `towerkit/CLAUDE.md:38-39` — D2's existing note is correct |

### Corrections to the spec's own citations

These do not change any argument, but a reader following them lands in the
wrong place:

- `scale.py:22` for `DEFAULT_GAMMA` → it is `scale.py:24`.
- `towerkit/CLAUDE.md:110-112` for "never teach towerkit CRM concepts" → the
  sentence is on line 113.
- `mpl_program.py:4-6` and `:6-9` for the two docstring claims → they are
  lines 4-5 and 7-9.
- **All `web/parity.py` line citations are stale.** The spec cites
  `parity.py:50, 51-52, 53-58, 60-61, 63-69`; the entries now live at
  `parity.py:97` (`renew_placement`), `:98` (`edit_layer`), `:99`
  (`add_layer`), `:100` (`open_towerkit`), `:107` (`scaffold_tower`),
  `:110` (`export_open_items`). The file has grown ~47 lines since drafting.
  Cite the keys, not the lines.

### The one that matters: the design package is not in the repository

Every numeric threshold D4 depends on — 30px, 13px, 11px, the 12/170
ref-line gap, the `hPx = heightPct * 2.4` factor, the 340px outer box, the
broken ellipsis CSS — is cited to `BookKit Web.dc.html:NNN`. That file is
**not tracked and not present on main.** `.superpowers/` is gitignored
(`.gitignore:27`), and `.superpowers/sdd/2026-08-17-web-account-page/` in
the main worktree is empty. The four `.dc.html` files exist only inside the
`web-account` worktree's own ignored tree, and vanish when that worktree is
removed.

Nobody can verify D2's or D4's numbers from main today, and after the
worktree is cleaned up nobody will be able to verify them at all. Given this
project's recorded history of specs citing files that were never built, this
is the highest-value thing in this report.

**Recommended fix, cheap and immediate:** copy the four `.dc.html` files
into a tracked path — `docs/design/` — in the same commit that splices this
amendment, and rewrite D2's and D4's citations to point there. Alternatively,
transcribe the ~8 numbers D4 actually depends on into D4 itself as a table
with the mockup as provenance. Either works; leaving them as they are does
not.



---

## Verification report (independent adversarial pass, 2026-08-18)

**Verdict: needs-revision.** The document's factual spine is unusually solid — the triplicated `pending`/`follows` predicates, the labels.py authority and its test module, layout.py's period-blindness, the frozen-dataclass equality argument, `scale.py:24`, the stale `parity.py` line range, and above all the finding that no `.dc.html` is tracked in either repo all check out verbatim, and the ROADMAP corrections are right on substance. It is not safe to build from as it stands. Its weakest point is the testing section that is supposed to make R66 enforceable: comparison 3 asserts a bare heading string appears in `ax.texts` when mpl_program.py:321-323 only ever draws heading+stack concatenated on a fixture sized not to fall through; the money assertions are vacuous because both sides are called with `cell_premiums` defaulting False; mutation 4 names a `gamma` parameter `web_labels` does not have and targets a comparison it does not touch; and mutation 5 needs a fractional-bps share the fixture spec never requires. Two further items need fixing before splice — Amendment 3 cites spec lines 355-360 for a signature sketch that lives at 335-341 (applying it as written corrupts the spec and leaves the bad signature in place), and the "impossible to compare SVG text" rejection is contradicted by matplotlib's `writer.comment(s)` and by five of towerkit's own passing tests.


### Citations that did not check out

- **`spec lines 355–360 (Amendment 3: "Replaces the signature sketch at spec lines 355-360")`** — claimed: The D4 `label_visibility` signature sketch lives at spec lines 355–360 and is what Amendment 3 replaces.
  
  *Actually:* /Users/grantgreeson/Developer/bookkit/docs/superpowers/specs/2026-08-17-towerkit-web-conversion.md:355-360 is mid-paragraph prose about the 340px outer box ("that 340px also contains the header row, the left-gutter padding..."). The signature sketch is at 335–341 — `def label_visibility(tower: TowerLayout, chart_height_px: float) -> WebLabels:` is on line 338, inside a fenced block at 337–341. Applying Amendment 3 literally guts the chart_height_px paragraph and leaves the wrong signature standing. This is the one citation that breaks the amendment as an executable instruction.

- **`towerkit/CLAUDE.md:113 (drafter's "correction": "'Never teach towerkit CRM concepts' is on line 113, not 110-112 as the spec cites")`** — claimed: The spec's citation of towerkit/CLAUDE.md:110-112 is wrong; the sentence is on line 113.
  
  *Actually:* /Users/grantgreeson/Developer/towerkit/CLAUDE.md:112 is "Never teach towerkit CRM concepts (accounts, aliases, matching)." Line 113 is "Numeric dates are MDY with 20xx two-digit years via the fast path in". The spec's original citation (110-112) CONTAINS the sentence and was correct. The drafter's correction is itself the error — the exact failure mode this review exists to catch.

- **`towerkit/CLAUDE.md:96-101 (Testing section: "*Non-vacuity, per `towerkit/CLAUDE.md:96-101` — \"negative assertions and 'absent by default' assertions are the usual culprits.\"*")`** — claimed: The quoted warning about negative and absent-by-default assertions is at towerkit/CLAUDE.md:96-101.
  
  *Actually:* /Users/grantgreeson/Developer/towerkit/CLAUDE.md:96-101 is the section heading plus the "A test must be capable of failing" bullet. The quoted sentence is at lines 102–104. The claim in citation-list item 45 (that 96-101 covers both) is likewise a superset error. The substance is right; the line range attached to the direct quote is not.

- **`towerkit/src/towerkit/render/mpl_program.py:56 (ROADMAP correction #2: "`render_program(..., cell_dates=True)` already prints per-layer terms ... (mpl_program.py:56, :156-159, :255-259)")`** — claimed: mpl_program.py:56 is the cell_dates parameter.
  
  *Actually:* Line 56 is `show_premiums: bool = True`. `cell_dates: bool = False` is line 58. (`:156-159` and `:255-259` both check out exactly — `_term_text` is at 255–259.)

- **`towerkit/src/towerkit/render/mpl_program.py:353-354`** — claimed: An empty candidate causes _fit_text to return without drawing anything.
  
  *Actually:* 353 is `for candidate in candidates:`, 354 is `if not candidate:`. The `return` the claim is about is on line 355, outside the cited range. Behaviour is as described; the range stops one line short of the thing being asserted.

- **`towerkit/src/towerkit/render/mpl_program.py:379-382`** — claimed: _titles renders the insured name with no weight argument "under the comment 'brand type rules: serif headings are Regular only, never bold'".
  
  *Actually:* The comment is on line 378; 379–382 is the `fig.text(...)` call alone. The behavioural claim (no `weight=` on the insured name) is correct.


### Claims challenged (even where the citation resolved)

- **[CRITICAL]** "Rejected: assert agreement by rendering an SVG and comparing its text to the panel's. This is not merely awkward, it is impossible: `svg.fonttype` is `"path"` (render/common.py:37), so the exported SVG contains glyph outlines and no text nodes."
  
  *Evidence:* False, and the repo already proves it false. matplotlib's SVG backend writes the full string as an XML comment before the outlines: `.venv/lib/python3.13/site-packages/matplotlib/backends/backend_svg.py:1050` — `writer.comment(s)` at the top of `_draw_text_as_path`. towerkit's own suite depends on exactly this: tests/test_render.py:46 asserts `"Total limit" in out.read_text()`, :83 asserts `"Indian Harbor" in out.read_text()`, :91 asserts the footnote line, :40 asserts "NOT TO SCALE" is absent — all against SVGs rendered with `svg.fonttype: "path"`. The one caveat the code offers (test_render.py:92-93, "halo path-effects leave no text comment") is itself stale: `grep -rn halo src/towerkit/render/` shows `halo` is only a parameter and is passed by no caller, so every string the export draws today emits a comment. The rejection may still be right on other grounds (comments are an implementation detail; ax.texts is a cleaner seam) but the stated ground is untrue, and a reviewer following the citation will conclude the amendment does not check its own claims.

- **[CRITICAL]** Testing comparison 3: "Read `[t.get_text() for t in ax.texts]` ... and assert every layer's heading string (computed with `marker=""`) appears among them ... The fixture is sized so nothing needs to fall through, making this an equality rather than a membership-in-a-large-set."
  
  *Evidence:* The export never draws a heading as its own Text artist on a well-fitting fixture. mpl_program.py:321-323 builds `candidates = [f"{h}\n{stack}" for stack in stacks for h in headings]` — the winning candidate is heading + carrier + share in ONE artist; the bare heading only appears at mpl_program.py:324 (`candidates += headings`), i.e. only when the combined form does NOT fit. The unplaced path is the same shape (`f"{heading}\n{body}"`, mpl_program.py:280). So the two requirements are mutually exclusive: size the fixture so nothing falls through and the heading is never a standalone member of ax.texts; let it fall through and the "equality, not membership" claim dies. As written the test fails on the fixture the document prescribes. The same defect hits the next clause — "every drawn participant string is either the shared `body` string or a declared shortening of it" — since one block per layer draws heading+body concatenated.

- **[CRITICAL]** Mutation 4: "pass a different gamma to `web_labels` → comparison 1 fails."
  
  *Evidence:* `web_labels` as declared in Amendment 3 takes no gamma — its parameters are `(program, tower, chart_height_px, *, cell_premiums=False)`; it receives an already-built `TowerLayout`. The mutation cannot be performed against the document's own API. Worse, comparison 1 (`draw_tower`'s returned layout == `build_layout(program, gamma)`) does not touch `web_labels` at all, so it could not fail from that mutation even if the parameter existed. The real gamma-divergence risk D2.1 names — "the panel and the export route must pass the same gamma" — lives in two bookkit call sites (the panel route and the download route), which a test housed in towerkit's suite (the drafter's own open-decision) cannot see. Agreement rule 1 is therefore the one rule with no test and no structural guard, while the document presents it as the cheapest to secure.

- **[IMPORTANT]** Fixture non-vacuity list is sufficient for the five mutations, including: "inline `f\"{carrier} {bps // 100}%\"` in the web instead of calling `participant_label` → fails on a 3,333 bps share, where `format_share` gives '33.33%' (money.py:129-134)."
  
  *Evidence:* money.py:129-134 is correct (`format_share(3333) == '33.33%'`, `3333 // 100 == 33`), but nothing in the fixture requirements guarantees a non-round share exists. The required list is: narrow-lead/wide-second layer, a `signed_bps == 0` layer, a partially-open layer, a `follows_underlying` layer, a layer with a non-None premium, a multi-run layer, a statutory layer. Every one of those can be satisfied with shares that are exact multiples of 100 bps, in which case `bps // 100` and `format_share` agree and the mutation passes green. The document's own discipline (towerkit/CLAUDE.md:98-101, mutate and confirm the failure) is what would surface this — but the fixture spec is what implementers copy, and it is missing the one property this mutation needs.

- **[IMPORTANT]** The fixture must contain "at least one layer with a non-`None` premium (otherwise `block_premium_label` is `None` everywhere and the money assertion is vacuous)", and comparison 2 asserts `web_labels`' `money` against an independent labels.py computation.
  
  *Evidence:* The premium being non-None is necessary but not sufficient, and the document's own call sites make it insufficient. The export side is invoked as `draw_tower(ax, program, theme)` — `cell_premiums` defaults to False (mpl_program.py:88), and mpl_program.py:152-154 computes a premium label ONLY when `cell_premiums` is set, so the export draws no money at all. The panel side is invoked as `web_labels(program, tower, chart_height_px)` — `cell_premiums` defaults to False in Amendment 3's own signature, so `money` is `None` on every block. `None == None` passes for a reason adjacent to the one claimed. The fixture requirement addresses the wrong half of the problem; the calls need `cell_premiums=True` on both sides, or the money agreement is untested by construction.

- **[IMPORTANT]** Open decision: the lifted predicates go in `render/labels.py`; "`layout.py` is wrong because it would have to import nothing but geometry (its own docstring rule, layout.py:15-17) and `follows_layers` needs the Program."
  
  *Evidence:* layout.py's docstring at :15-17 says "This module imports nothing but the stdlib, model.py and scale.py — no plotting library, ever" — model.py is explicitly permitted, and layout.py:24 already reads `from .model import Layer, Program`. It also already computes a follows_underlying set-like traversal at layout.py:153-159. So the stated disqualifier for layout.py is factually inverted. Meanwhile the chosen destination is the one with the purity problem: labels.py's own docstring ends "Pure: layout + money only" (labels.py:3) and its imports are `..layout` and `..money` only (labels.py:8-10); `follows_layers(program)` forces a new `..model` import and makes that docstring false. The conclusion (labels.py, beside heading_blocks) may still be the right call, but it is currently justified by a claim the code contradicts, and it silently amends a module contract without saying so.

- **[IMPORTANT]** "Agreement is required on all five of these, and slice 1 must make each structurally impossible to diverge rather than merely likely to match."
  
  *Evidence:* Only rules 3, 4 and 5 get structure. Rule 3 is a function call (heading_blocks, already shared at mpl_program.py:132 / schematic_xlsx.py:242, 439). Rules 4 and 5 get the lift — genuinely structural, and the triplication is real and verified verbatim at mpl_program.py:116,128 / schematic_xlsx.py:240,241,437,438. Rule 1 (gamma) is enforced by the sentence "the panel must not acquire a gamma control" and by nothing else; rule 2 ("`render/web.py` quotes `render/labels.py` for every string. It composes none of its own") is enforced only by comparison 2, which recomputes the same expressions the implementation runs — a mirror, not a constraint. Nothing prevents a future contributor inlining an f-string in a template, which is exactly the failure the document names. "Structurally impossible" is delivered for two of five and asserted for five.

- **[IMPORTANT]** Amendment 4 "Replaces the 'The label-drop constants applied consistently' bullet at spec lines 809-816, and keeps every other bullet in that section as written"; and D4's chart_height_px warning "becomes strictly cosmetic-only under this amendment."
  
  *Evidence:* 809–816 is confirmed as that bullet's exact extent. But that bullet is the ONLY test in the spec covering D4's loudest warning — spec:348-372 says passing 340 instead of ~240 "makes every block's computed hPx look ~42% taller", under-fires all three thresholds, "and would only be caught by eyes-on comparison against the mockup, not by any geometry test". The replacement test asserts facts agreement and explicitly does not compare visible glyphs, so the amendment deletes that coverage and adds none. Calling the consequence "strictly cosmetic" is also doing more work than it can bear: under-firing the thresholds means the panel shows labels the design decided to drop, in a panel whose own 30/13/11px numbers are cited to a file that is not in the repository. Net effect of the amendment on this risk: one fewer test, same unverifiable constants.

- **[IMPORTANT]** Comparison 2 is "the seam assertion: it fails the moment the web composes a string instead of quoting one."
  
  *Evidence:* Comparison 2 compares `web_labels` output against the test's own inline re-execution of `labels.layer_heading` / `participant_label` / `unplaced_label` / `block_premium_label` + `heading_blocks` + the lifted predicates. It never touches the export. So the entire panel-vs-export claim — the thing R66 is about — rests on comparison 3 alone, which is broken (see above). What survives is a mirror test of web_labels plus an exact geometry equality (comparison 1, which is sound: TowerLayout is a frozen dataclass of frozen dataclasses, tuples and floats at layout.py:121-132, and YMap at scale.py:28-35 is likewise frozen tuples+float, so `==` really is exact). The document should say plainly that comparison 3 is the only cross-renderer assertion, because that is where all the risk concentrates.

- **[MINOR]** "the export is unmodified, deterministic, and has always been the only client-facing renderer."
  
  *Evidence:* towerkit ships render/schematic_xlsx.py (786 lines, the SOI schematic worksheet), render/soi_xlsx.py and render/table_xlsx.py, and bookkit has a whole spec for the schematic worksheet (docs/superpowers/specs/2026-08-13-soi-schematic-worksheet-design.md). The xlsx outputs are plausibly client-facing too. This does not change the amendment's decision — the SVG is the renderer under discussion — but "the only client-facing renderer" is the kind of exclusivity claim this project's history says to distrust, and it is used to carry the load in the "nothing regresses" argument.

- **[MINOR]** ROADMAP correction #1: "`build_layout` reads only `attach`, `limit`, `premium`, `signed_bps`, `applies_to` and `statutory`."
  
  *Evidence:* The correction's conclusion is CONFIRMED and is the strongest thing in the document: `grep -n period src/towerkit/layout.py` returns nothing — layout.py never consults `Layer.period` (model.py:93), and LayerBlock (layout.py:86-97, constructed at 201-214) carries no period. The tower really is period-blind in both paths. But the enumeration is incomplete: build_layout also reads `layer.id`, `layer.name` (layout.py:203-204), `layer.follows_underlying` (layout.py:158) and `layer.top`, and calls `program.underlying_tops`. Tighten the list or drop the word "only" — an exhaustive-sounding list that is not exhaustive invites the next reader to distrust the correct conclusion sitting next to it.


### Decisions the draft left open

- **Where do the lifted `pending_layers` and `follows_layers` predicates live — `render/labels.py`, a new `render/facts.py`, or `layout.py`?**
  - Recommendation: `render/labels.py`. `heading_blocks` already lives there and is the same kind of thing: a derivation that decides what a label says, not where a rectangle goes. `layout.py` is wrong because it would have to import nothing but geometry (its own docstring rule, layout.py:15-17) and `follows_layers` needs the Program. A new module is a third file for two functions.
  - Cost if wrong: Low and reversible — it is a module move with four call sites. The cost of NOT lifting them at all is the real one: a fourth hand-rolled copy of `signed_bps == 0` that mislabels a client chart.

- **Does the renderer agreement test live in towerkit's suite or bookkit's?**
  - Recommendation: towerkit's (`towerkit/tests/test_render_agreement.py`). Both renderers are towerkit's; bookkit's web layer only consumes them, and the test needs no bookkit fixtures — the same argument D4 already makes for housing `render/web.py` in towerkit.
  - Cost if wrong: If it lives in bookkit, someone can change `labels.py` or `mpl_program.py`'s ladder, run towerkit's suite green, and ship a divergence that only bookkit's CI would have caught. The guard would sit outside the repo it guards.

- **Does the docked panel reproduce the export's footnote superscripts on layer headings?**
  - Recommendation: No. `schematic_xlsx` already omits them while `mpl_program` shows them (mpl_program.py:41, 60-63), so the precedent that markers are export chrome is already set. The panel surfaces layer notes in the inspector instead, and the agreement test compares headings computed with `marker=""`.
  - Cost if wrong: Small: a broker who has memorised the export's superscripts finds no counterpart on screen. The bad version of the alternative is worse — a superscript on the panel with no footnote to point at is a dangling reference.


### Needs Grant

- Does the docked tower panel show a per-block premium figure that the client's SVG export does not? The export's `cell_premiums` defaults to False (mpl_program.py:55-58, 152-154), so the default client chart carries no per-block premium; D4's 11px money rule would put one on screen. Option A: the panel mirrors the export's defaults exactly, so agreement is total and the broker's working view loses a number the TUI already gives them. Option B: the panel always shows it, the export keeps its default off, and the agreement test's money assertion carries one documented exemption. Recommend B — it is the same asymmetry Grant already ruled on for the not-to-scale caveat (towerkit/CLAUDE.md:38-39, spec D3): the broker's working surface may carry context the client deliverable omits. Cost if wrong on A: the panel is less useful than the TUI it replaces. Cost if wrong on B: the agreement test has one documented exemption that a future reader must not mistake for a bug.


### Corrections this draft makes to the ROADMAP entry

- ROADMAP said: "A program spanning a renewal boundary is precisely the case the two renderers must agree about" (the R66 paragraph).
  - Code says: The tower geometry is period-blind, so the two renderers cannot disagree about a half-renewed program — they agree, and are equally wrong together. `build_layout` reads only `attach`, `limit`, `premium`, `signed_bps`, `applies_to` and `statutory`; `LayerBlock` carries no period field at all (layout.py:86-97, 201-214), and `Layer.period` (model.py:93) is never consulted by the layout. A staggered-renewal program renders as one undifferentiated tower in BOTH paths. This is not a two-renderer agreement problem; it is a single modelling gap neither renderer can express, and framing it as an R66 risk points the fix at the wrong layer. (`towerkit/src/towerkit/layout.py:86-97 and :201-214; towerkit/src/towerkit/model.py:93 (vs ROADMAP.md:192-196)`)

- ROADMAP said: "...so this question should be settled, or at least bounded, before the export half is built."
  - Code says: The export half is not being built. D2 (now approved) wires `render/mpl_program.py` through a download route unmodified; it already ships and already has this blindness today via the TUI and CLI. Gating the export route on a renewal-identity spec would block work that changes nothing about the risk. The one place the concern is real and actionable is `cell_dates`: `render_program(..., cell_dates=True)` already prints per-layer terms from `owner_model.period or program.period` (mpl_program.py:56, 156-159, `_term_text` at 255-259), so the export CAN already express staggered periods per cell — and the panel must gain the same option, or the two disagree the moment a broker turns it on. That is a concrete D8 line item, not a blocking spec. (`towerkit/src/towerkit/render/mpl_program.py:56, :156-159, :255-259 (vs ROADMAP.md:192-196)`)
