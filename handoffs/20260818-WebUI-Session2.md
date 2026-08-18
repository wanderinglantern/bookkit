# Handoff — bookkit web front end, 2026-08-18 (afternoon session)

Supersedes nothing; **read `handoffs/20260818-WebUI.md` first**, then this. Everything
below is verified state, not intention. Written mid-flight because the session was
running into a usage limit.


> ## UPDATE — later the same afternoon. Read this before §1; it supersedes it.
>
> - **`main` = `dca327b`, pushed. 955 passed, mypy clean, ruff clean, wheel builds
>   with all 9 font files inside.**
> - **Task 16b (fonts) is MERGED.** Reviewed, one fix round applied, all four gates
>   re-run by the controller before the merge. Nothing outstanding on it.
>   `.claude/worktrees/web-work` / branch `web-fonts` can be reused or removed.
> - **`web-snapshot` (Tasks 9 + 17) is NOT merged and must not be merged as it stands.**
>   Its review found a **CRITICAL that is green in the suite and invisible in Chrome**:
>   `app.css:694` uses `border-left: 1px solid var(--rule)`, and `--rule` is the *TUI*
>   palette name. The web tokens are `--hairline`/`--hairline-2`/`--border`
>   (`web/theme_css.py:_VARIABLES`). An invalid `var()` makes the whole shorthand compute
>   to `unset`, so the scope rule the commit message calls load-bearing **does not render
>   at all**. It is the only undefined custom property in the stylesheet.
>   **Task 9 is approved and ships as-is** — every mutation red, the strongest-pinned work
>   on the branch.
> - **A fix round for `web-snapshot` was dispatched and was still live when this update was
>   written.** If its commit is not on the branch, re-dispatch it. The items were:
>   (A) the `var(--rule)` fix **plus a test that every `var()` in `app.css` resolves** —
>   that class of silent CSS death is otherwise unguarded, and the rule also renders as
>   three disconnected segments with the caption outside its own bracket;
>   (B) two zeros that are lies — `program premium $0` when every layer's premium is
>   `None`, and `top of tower $0` for a statutory-only program (towerkit forces
>   `limit == 0` on `Layer.statutory`), because the omit guard tests for *no program*, not
>   *no data*; (C) the scoped group's membership is unpinned — an ACCOUNT-scoped row can
>   be smuggled between `program premium` and `top of tower`, render under the program
>   caption, and the suite stays green; (D) `layer_details` is called once per render but
>   nothing holds it; (E) minors, including `assert added.ok, added.messages` where
>   `sync.Diagnostics` has no `.messages`.
> - **Expect a merge conflict** between `web-snapshot` and merged `web-fonts` in
>   `tests/test_web_account.py` and `src/bookkit/web/static/app.css` — both touch them, in
>   different regions. The fonts branch wrapped the overdue badge's `◆` in a
>   `<span class="badge-glyph">` (so it can take the mono family, which is the only
>   vendored face that has that glyph), which changed an exact-string assertion around
>   `test_web_account.py:154`. Keep both sides.
>

---

## 1. Where things stand (as of the morning; see the UPDATE above)

`main` = **`b169621`**, pushed. Contains, in order:
- `6bab7f8` — merge of `web-interactions` (the interactions timeline). Gated by the
  controller: **947 passed**, mypy clean (114 files), ruff clean.
- `b169621` — merge of `docs/roadmap-specs` (six spec drafts, docs only, no code).

### Branches NOT merged, both fully gated by the controller

| branch | worktree | contents | gates |
|---|---|---|---|
| `web-snapshot` | `.claude/worktrees/web-account` | Tasks 9 + 17 | **954 passed**, mypy clean, ruff clean |
| `web-fonts` | `.claude/worktrees/web-work` | Task 16b (fonts) | **950 passed**, mypy clean, ruff clean, `uv build --wheel` OK with all 9 font/licence files inside |

**Both were under review when this handoff was written.** Two reviewer agents were
live — one per branch. If their reports never landed, **re-dispatch review before
merging**: this build's whole record is that gates do not catch the defects and review
does. Do not merge either branch on the strength of the gates alone.

`web-batch-join` and `web-defects` are merged and their worktrees are free to reuse or
remove. `.claude/worktrees/web-work` was itself a reuse of the merged `web-work`
worktree — safe only because `uv.lock`/`pyproject.toml` were byte-identical between its
old head and main, which was checked before reuse, not assumed.

---

## 2. Next step, exactly

```bash
cd /Users/grantgreeson/Developer/bookkit
# 1. adjudicate the two reviews (re-dispatch them if their reports were lost)
# 2. then, per branch:
git merge --no-ff web-snapshot
uv run --no-sync python -m pytest -q > "$SCRATCH/gate.txt" 2>&1; echo $?; tail -4 "$SCRATCH/gate.txt"
git merge --no-ff web-fonts
uv run --no-sync python -m pytest -q > "$SCRATCH/gate.txt" 2>&1; echo $?; tail -4 "$SCRATCH/gate.txt"
uv build --wheel      # fourth gate — web-fonts touches package data
git push origin main
```

`web-snapshot` and `web-fonts` both touch `src/bookkit/web/static/app.css`, in different
regions (snapshot rail rows vs `@font-face` at the top). Expect a trivial conflict at
most; resolve by keeping both.

### Then, in order

1. **The five spec drafts that need a revision round** (see §4). One is sound.
2. **towerkit slice 1** — now unblocked, and its amendment is drafted but NOT approved.

---

## 3. What was done this session

- Merged `web-interactions`.
- **Tasks 9 + 17** built on `web-snapshot`. Task 9 is the refusal contract (tests only —
  the contract was found to hold unchanged on `work.py:request_update` and
  `relationship.py:interaction_update`, both routing through the shared `_save`, so no
  production change was needed). Task 17 adds `program premium` / `top of tower` /
  `unplaced` to the snapshot rail from `sync.layer_details`.
- **Task 16b** built on `web-fonts`: Noto Sans, Noto Serif and JetBrains Mono vendored as
  subset WOFF2, ~520K for seven faces.
- **Six roadmap specs drafted and adversarially verified** (§4), merged to main as drafts.

---

## 4. The six spec drafts — read this before building from any of them

`docs/superpowers/specs/2026-08-18-*.md`. Each was drafted against the code, then checked
by an INDEPENDENT pass that opened every citation and challenged the load-bearing claims.
**435 citations checked, 34 did not resolve to what was claimed.** The verification report
is at the bottom of each file and is part of the document.

| file | kind | verdict |
|---|---|---|
| `2026-08-18-internal-tasks-export.md` | task-brief | **sound** — buildable |
| `2026-08-18-towerkit-r66-amendment.md` | spec | needs revision |
| `2026-08-18-deactivating-records.md` | spec | needs revision |
| `2026-08-18-program-identity-across-renewal.md` | spec | needs revision |
| `2026-08-18-web-refusal-contract.md` | task-brief | needs revision |
| `2026-08-18-mcp-interaction-delete.md` | task-brief | needs revision |

**The findings that would have broken an implementation** — these are the reason the
drafts are not approved:

- **program-identity, D7 rests on a false premise.** It puts the file-write guard in
  `sync.write_through` as "the single function every program-file write goes through".
  It is not: `dump_program` is called from four places — `sync.py:668` (renew, which dumps
  the clone directly and never re-validates), `sync.py:739` (scaffold_program), and
  `imports/commit.py:165`. A guard landed only in `write_through` leaves three writers
  unguarded. This is exactly the CLAUDE.md failure mode ("declare the name, don't patch
  the symptom") and it would have shipped.
- **towerkit-r66 rejects an approach on a false impossibility.** It says comparing the
  SVG export's text to the panel's is "impossible"; matplotlib's SVG backend writes the
  full string as an XML comment before the outlines, which the verifier found in the
  installed package. Its Testing section also names a mutation that cannot fire, and a
  comparison that reads `ax.texts` for a heading the export never draws as its own artist.
- **web-refusal-contract rules three incompatible things about `request_detail`**
  (`work.py:504`) — excluded in the sweep table, kept-with-a-404 in D5, and in D6's
  EXCLUDED map. Its route-walk test also over-selects by six routes.
- **deactivating-records' "decisive fact" is refuted** — it claims choosing a status
  field over a boolean forces a CHECK-constraint change; no alternative on the table does.
  Its `is_primary` argument is also refuted by the very line it cites
  (`repo/contacts.py:58-64`).
- **mcp-interaction-delete names a mutation that cannot turn its test red**
  (`services/batches.py:91,141-145` — the `yield` sits inside the `with db.transaction`).

**The best thing in any of them:** the R66 draft found that towerkit ALREADY solved the
two-renderers-must-agree problem once. `towerkit/src/towerkit/render/labels.py` calls
itself "the single authority both renderers quote, so a block reads identically on the
chart and in the cells", and the graphic and the xlsx schematic fit by completely
different means while being required to SAY the same things. That is the precedent slice 1
should extend rather than invent a rule for. Its proposed line — the two renderers must
agree about the FACTS a block asserts, and may differ about which candidate string a
fitter chose — is the right shape, and resolves the contradiction between R66 and D2's
accepted label-fit limitation. It needs the revision round before it is spliced in.

---

## 5. Rulings made this session (also in the gitignored ledger, R67-R70)

- **R67 — Noto Sans is vendored too.** The prior handoff said "Noto Serif + JetBrains
  Mono" and undercounted: `--sans` is ALL UI text, so leaving it on `system-ui` means the
  app still does not look like the design on any machine.
- **R68 — WOFF2 generated with an ephemeral `uvx` fonttools.** Nothing enters
  `pyproject.toml`/`uv.lock` (verified unchanged), so the wheelhouse republished this
  morning is not disturbed. `scripts/vendor_fonts.sh` records the provenance.
- **R69 — the font subset is driven by the glyphs the app ACTUALLY uses.** A scan of every
  template/CSS/JS found `— · … ◆ ★ ✕`; the visual-direction spec's stated vocabulary names
  `✓` and `···` (unused) and MISSES `✕` and the em dash (used 150+ times). Subsetting to
  the spec's list alone would have dropped glyphs off live pages.
- **R70 — approved deleting `test_snapshot_omits_rows_it_has_no_real_read_for`.** It
  asserted the exact opposite of Task 17 against the one account that now has a tower.
  Its replacement is strictly stronger (it searches for a genuinely unlinked renewal and
  asserts the page rendered, so the omission cannot be satisfied by a blank page).

---

## 6. Open, needing Grant

- **The `★` finding.** `★` (U+2605) exists in NONE of the three vendored families, and
  `◆ ✕` exist only in JetBrains Mono — while all three render in SANS contexts
  (`account/page.html:19,58`, `_contacts_panel.html:57`). So the overdue diamond, the
  primary-contact star and the toast close fall through to a system face. No worse than
  today, but the "glyphs are text" vocabulary does not render in the type just vendored.
  Options: accept, swap the glyphs for ones Noto has, or let those specific spans use the
  mono family. **Not yet decided.**
- **`font-weight: 600` appears in 8 CSS rules** while the spec bans 600 outright. With
  Noto declaring only 400/700, CSS matching resolves 600 to the real 700, so nothing is
  synthesised — but the source asks for a weight that does not exist. Worth rewriting to
  `700` so the stylesheet is honest. Cheap, no visual change.
- **JetBrains Mono Medium (500) ships but no rule uses it** — 43K of dead weight.
- **Timeline empty-state copy** ("no interactions logged") — carried over, still unanswered.
- Every batch ref is still prefixed `MCP-` regardless of source. Carried over, unfixed
  on purpose (refs are already written into the real book).

---

## 7. Gotchas — the ones that cost time TODAY, on top of the prior handoff's

- Gates are `uv run --no-sync python -m pytest -q`. A bare `uv run pytest` falls through
  to Anaconda's and reports a bogus `ModuleNotFoundError: No module named 'bookkit'`.
- Background-task notifications report the WRAPPING shell command's exit code, not
  pytest's. Redirect to a file, read the file.
- **Never `git add -A`.** The main worktree currently carries an unrelated dirty
  `snapshot_report.html` and untracked `.playwright-mcp/` and `fonts-account.png`; every
  commit this session staged explicit paths and left them alone.
- **Reusing a merged worktree is fine only if `uv.lock`/`pyproject.toml` have not moved.**
  Check with `git diff --stat <oldhead> main -- uv.lock pyproject.toml` before skipping
  `uv sync --group dev`.
- **A test that a fixture cannot exercise is worse than no test.** Task 17's required
  mutation would have passed vacuously against seeded data: only one seeded account has a
  file-linked next renewal, and it has a single bound placement, so program premium and
  account bound premium are the same number. Always check the fixture can tell the two
  values apart BEFORE trusting a mutation proof.
