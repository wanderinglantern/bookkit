<!-- DRAFT — NOT APPROVED. Read the verification report at the bottom before building from this. -->

> **Status: DRAFT — NEEDS REVISION** (2026-08-18).
> Produced by a drafting pass, then checked by an independent adversarial pass that
> opened every citation and challenged the load-bearing claims.
> **73 citations checked · 4 failed · 9 claims challenged.**
> Kind: `task-brief`.
> The verification report at the bottom is PART OF THIS DOCUMENT — some of its findings
> would break an implementation built from the body above it.

---

# The web's refusal contract — one rule for "not there, or not yours"

Date: 2026-08-18
Kind: task-brief (the design is settled below; this is buildable as written)
Suggested path: `docs/superpowers/specs/2026-08-18-web-refusal-contract.md`
Covers: ROADMAP.md:206 (the revert oracle) and ROADMAP.md:238 (the stale Edit)

---

## Ruling first: these are one document, one branch, two commits

They are one problem. Both entries ask what a web route answers when the row
named in the URL is **not there** or **not yours**, and both currently answer it
two different ways in the same codebase. The revert control tells the two misses
apart (`changes.py:102` vs `:109-111`) where `_owned` deliberately does not
(`account.py:114-118`); the cell routes tell nobody anything at all
(`relationship.py:283`, `:302`, and fifteen more).

They are one **branch** for a mechanical reason, not a thematic one: six of the
routes in the entry-2 sweep cannot be answered with a panel, because the panel
they live in describes the very record that is gone (`_items_context` reads
`rfi_repo.get_request`, alive-filtered, at `work.py:352`). Those six need the
redirect-plus-token idiom that entry 1 is standardising. Building entry 2 first
would either invent a second toast vocabulary or leave that family silent.

They are two **commits** because entry 1 is a three-line route change plus tests
and entry 2 is a seventeen-route sweep, and a reviewer should be able to read
them apart.

Counter-argument, stated so it is retrievable: entry 1 alone is ~30 minutes of
work and bundling it behind a sweep delays it. I take the bundle anyway — a
sweep that establishes "a refusal says something, in the smallest surviving
container" while the revert control still answers a miss two different ways
ships a contract that contradicts itself on the day it lands.

---

## What the code actually says

Verified against the main worktree at 6bab7f8. Four claims in the ROADMAP
entries are wrong; they are listed in full under "Roadmap corrections" and
summarised here where they change the work.

**The revert route's two shapes are real and exactly as described.**
`web/routes/changes.py:97-102` answers an unknown `batch_ref` with
`_redirect(..., "gone")` — 204 + `HX-Redirect`. `:103-111` answers another
account's `batch_ref` with `HTTPException(404)`. Refs enumerate: `next_ref`
formats `f"{kind}-{row[0] - 1:04d}"` off a per-kind counter (`ids.py:34-45`)
and every batch takes `BATCH_REF = "MCP"` (`repo/batches.py:12`), so the space
is MCP-0001, MCP-0002, … regardless of which surface wrote it.

**The render side of the collapse is already built.** `toast_for`'s `gone`
branch already returns "that change no longer exists" for a batch that resolves
but belongs to another account (`changes.py:221-222`). And the rail cannot
contradict it: `_context` filters `batches_repo.recent` to `b.org_id == org.id`
(`account.py:391-393`), so a foreign ref is never listed on the page the
redirect lands on. The review-round-2-D contradiction the branch was written to
prevent cannot occur. **Collapsing the route needs no template and no toast
change** — only a test, because `changes.py:221-222` is currently unexercised
(`test_toast_for_refuses_a_batch_from_another_account`,
`tests/test_web_writes.py:691-693`, loops over `reverted/already/refused/program`
and skips `gone`).

**The silence is real, and it is not confined to cell routes.** htmx 2.0.4
ships `responseHandling:[{code:"204",swap:false},{code:"[23]..",swap:true},{code:"[45]..",swap:false,error:true}]`
(`web/static/htmx.min.js`) — neither 4xx nor 5xx swaps — and `inline-cell.js`
registers listeners for `submit`, `htmx:afterRequest`, `focusout`, `keydown`,
`htmx:afterSwap` and `click`, and **none** for `htmx:responseError`. Every
`_owned` / `_owned_item` refusal on a control the page itself rendered is a
click into the floor.

**The count is eighteen, not fourteen, and only nine are cell routes.**
22 routes under `/accounts/{ref}/` name an account and a row
(`tests/test_web_scoping.py:107-153` drives all 22). Four already answer
staleness correctly through `_owns_raw_row` (`relationship.py:326`, `:356`,
`:423`, `:456`). The other **eighteen** go through `_owned`/`_owned_item` and
answer 404: five in `relationship.py` (`:283`, `:302`, `:488`, `:495`, `:507`)
and thirteen in `work.py` (`:168`, `:175`, `:185`, `:214`, `:270`, `:280`,
`:371`, `:381`, `:430`, `:442`, `:459`, `:491`, `:504`). Of those eighteen,
nine are cell routes; one (`request_detail`, `work.py:504`) is a full page
navigation and is excluded below.

**The fork the roadmap poses is already decided by the DOM.** The entry assumes
"a stale Edit has no panel of its own to refuse into on some routes (a cell
editor's target is the cell)". Every cell lives inside a panel with a stable id
(`#contacts-panel`, `#tasks-panel`, `#items-panel`), and htmx's outerHTML swap
is a **verified silent no-op on a detached target**: `Me(t,e,n)` does
`const i=c(t); if(!i){return}`, and `c` returns `parentElement`
(`web/static/htmx.min.js`). So a response carrying only an OOB panel replaces
the panel — which removes the stale cell — and the primary swap into the now
parentless cell returns immediately. The panel shape works for cell routes. The
cell shape does not work for the nine non-cell routes, whose targets are
`.form-host` (`_requests_panel.html:49-50`, `_interactions_panel.html:65-66`)
or the panel itself (`_tasks_panel.html:47-48`, `_items_panel.html:56-57`).
**Only one of the two candidate shapes generalises.**

---

## Decisions

### D1 — Both revert misses collapse to the redirect and the same "gone" toast

`changes.py:103-111` stops raising and returns `_redirect(ref, tab, batch_ref,
"gone")`, the same call `:102` already makes. The org check itself **stays** —
it must still run before `batches_svc.revert`, or a crafted URL on account A
reverts account B's write. Only the shape of its refusal changes.

Rejected: **both 404.** It is the shape that answers the common case — a stale
tab clicking Revert on a batch the TUI already reverted — with a blank screen,
on a control the page rendered. The route's own docstring already argues the
other way (`changes.py:100-101`: "an unknown ref is a stale page, not an
intrusion").

Rejected: **leave it, since exposure is zero.** True and stated in the roadmap
entry; irrelevant to the reason to fix it. The value is that the codebase then
has one rule for one question instead of two, and `_owned`'s comment
(`account.py:169-172`, "telling the two apart is how a guessable id becomes a
membership oracle") stops being contradicted by the one route that was written
before it.

**Cost if wrong:** a user who genuinely pasted another account's ref now lands
on a page saying the change does not exist, when it does exist elsewhere. That
is the intended lie — the same one `_not_here` tells (`account.py:114-118`) —
and the write is still refused. Reversible in three lines.

### D2 — The refusal is rendered by the smallest surviving container

**The rule:** when a route refuses because the row is this account's and gone,
it answers **200 with the panel that listed the row, out of band and nothing
else, carrying the sentence inside it**. When the record the panel itself
describes is the gone one, it answers **204 + `HX-Redirect` to the tab page with
a `gone` token**, one level up. When the route is a browser navigation rather
than an htmx swap, it keeps its 404.

This is not two shapes; it is one decision — *never a silent 4xx on a control
the page rendered* — resolved at whichever level still exists.

The panel half is not new. It is `contact_remove_confirm`
(`relationship.py:396-433`) and `interaction_delete_confirm`
(`relationship.py:315-341`), already load-bearing, already tested
(`tests/test_contact_remove.py:435-470`), and already reasoned about in the
place the reasoning belongs (`relationship.py:243-255`: htmx applies OOB content
before the primary swap, so anything outside the OOB element lands in a node the
OOB replace has already detached).

Rejected: **the re-rendered cell saying "gone — refresh".** Three reasons, in
order of weight. (a) It does not generalise: nine of the eighteen routes have no
cell. (b) A cell that says "gone" leaves the rest of a dead row on screen still
claiming to be live — the panel refresh removes the row, which
`tests/test_contact_remove.py:470` already asserts as the right outcome. (c)
"refresh" is the wrong instruction when the response *is* the refresh.

Rejected: **an `htmx:responseError` listener in `inline-cell.js`,** in all three
roles the prompt names — replacement, safety net, and belt-and-braces. It is the
symptom patch the project's own rule forbids. Concretely: the app has several
**correct** 404s that must never surface as a banner — a non-editable cell key
(`tests/test_web_writes.py:154`, `tests/test_web_work.py:141`, `:310`), a
crafted `tab` (`tests/test_web_writes.py:379`), and every cross-account refusal
(`tests/test_web_scoping.py:178`). A global handler cannot tell those from a
stale click, so it either shouts about crafted URLs or says something so generic
it is worse than the panel it would sit under. Worse, it makes every future
route's silence *look* handled: the next handler that forgets the treatment gets
a plausible banner instead of a failing test. The safety net is D3, which fails
at gate time.

**Cost if wrong:** if the panel shape turns out wrong for a family we have not
foreseen, the blast radius is one helper signature and one branch per route
family — the sentence and the guard both live in one place by construction. If
the OOB-detached-target reasoning is wrong (it is verified against the shipped
minified source, not from memory), the symptom is a stale cell surviving a panel
refresh, visible in the first Playwright pass.

### D3 — Staleness is decided in the guard, and the guard makes it mandatory

`_owned` and `_owned_item` (`account.py:155-189`) grow a **required** keyword
parameter — no default — naming what to answer with. mypy, already in the gate,
then fails any call site that omits it. This is the direct application of the
2026-08-15 lesson recorded in CLAUDE.md: batching the shared `push_form` looked
right and went green while 33 call sites built `FormModal` directly. Opt-in
leaves whichever one you missed unreachable.

The staleness question is asked of the **raw** row, exactly as
`_owns_raw_row:191-209` asks it, but through `Model.from_row` +
`_owner_org_ids` rather than `_owns_raw_row`'s naive `str(row["org_id"]) !=
org.id`. That naive compare is correct only because its two callers are contacts
and interactions, which always carry `org_id`; a task owned solely through its
placement carries `org_id` NULL (`tests/test_web_scoping.py:71-76`) and would be
refused. `Row.from_row` is `model_validate(dict(row))` (`models.py:106-108`) and
`raw_row` is `SELECT *` (`repo/base.py:55-58`), so the models build from a dead
row unchanged.

The decision table, once, in the guard:

| raw row | owner | `deleted_at` | answer |
| --- | --- | --- | --- |
| missing | — | — | 404 `_not_here` |
| present | another account | either | 404 `_not_here` |
| present | this account | NULL | the entity, as today |
| present | this account | set | **stale** — 200/redirect per D2 |

The third row of that table is what keeps the oracle shut: a foreign ref answers
404 whether it is alive or dead, so nothing about another account's book leaks.
`tests/test_web_scoping.py:178-202` and `:218-225` stay green unchanged, and
that is a load-bearing fact about this design, not a convenience.

While in there: **`_owns_raw_row` should delegate to the same path**, so there is
one raw-row ownership rule rather than two that agree by accident.

**Cost if wrong:** a required parameter on a shared guard is an 18-site edit; if
the shape is wrong it is an 18-site revert. The alternative — a default that
falls back to 404 — costs one forgotten route that looks broken for months,
which is the defect being fixed.

### D4 — Where the sentence lives

Contacts and interactions **reuse the service sentences they already have**:
`contacts_svc.already_removed` (`services/contacts.py:137-155`) and
`interactions_svc.already_deleted` (`services/interactions.py:81-98`). The web
must not grow a second vocabulary for a fact a service already states — that is
precisely the defect ROADMAP.md:269 records against `mcpserver._activity_delete`.

Tasks, requests and items have **no delete writer on any surface**
(`repo/tasks.py:112`, `repo/rfi.py:80`, `:145` have no callers outside tests),
so there is no service refusal to mirror. Their sentence is built once in the
web layer, in the same family: `f"{label} is no longer on {org.name}"`. When
ROADMAP.md:76 ("Deactivating a record, generally") ships a real writer for those
kinds, the sentence moves to the service and the web helper delegates — write
that instruction into the helper's docstring so the next person does not fork it.

No "— refresh" tail. The response is the refresh.

**Cost if wrong:** copy. Change it in one function.

### D5 — `request_detail` keeps its 404, and says so out loud

`work.py:500-511` is reached by a plain `<a href>` (`_requests_panel.html:46-47`),
not htmx. Returning a bare panel fragment to a navigation renders a panel with
no shell. Its failure mode is also not silence — the browser shows FastAPI's
JSON `detail` — so the rule it violates is politeness, not the refusal rule.

**Recommendation, not a ruling** (see open decisions): redirect it to
`/accounts/{ref}/work` with the `gone` token, matching the container family.

It must appear in the sweep table as a **named exclusion with its reason**, so
the gap is visible to D6's completeness test rather than absent from it.

### D6 — The completeness guard is a test that walks the route table

New `tests/test_web_staleness.py`, two tests:

1. **The contract, per route.** A table of every route in the sweep, each
   driven against a row of *this* account that has been soft-deleted, asserting
   the D2 shape. For the panel family, the assertions the remove control already
   proves out (`tests/test_contact_remove.py:457-470`): status 200; the sentence
   present; `body.startswith('<div id="…-panel"')`; `hx-swap-oob` present; the
   error rendered *inside* the OOB element; and the stale row's own action URL
   absent from the refreshed panel.
2. **No route may be missing from the table.** Walk `app.routes`, select every
   path containing `{ref}` plus at least one further `{param}`, and assert each
   is either in the table or in a named `EXCLUDED` mapping (today:
   `request_detail`, with its reason as the value). A route added next month is
   red until it grows the treatment.

This is the safety net D2 rejected the JS listener for. It fails at gate time
instead of shrugging at runtime.

---

## The sweep, route by route

Panel column = the OOB fragment the refusal returns. "Container" = the request
itself is gone, so the redirect applies.

**`web/routes/relationship.py` — 5 routes, panel already carries `error`**

| # | Route | Guard | Answer |
| --- | --- | --- | --- |
| 1 | `GET  /accounts/{ref}/interactions/{id}/edit` | `:283` | `#interactions-panel` OOB |
| 2 | `POST /accounts/{ref}/interactions/{id}/edit` | `:302` | `#interactions-panel` OOB |
| 3 | `GET  /accounts/{ref}/contacts/{id}/cell/{key}` | `:488` | `#contacts-panel` OOB |
| 4 | `GET  /accounts/{ref}/contacts/{id}/cell/{key}/edit` | `:495` | `#contacts-panel` OOB |
| 5 | `POST /accounts/{ref}/contacts/{id}/cell/{key}` | `:507` | `#contacts-panel` OOB |

`_contacts_panel` and `_interactions_panel` already take `error=`
(`relationship.py:126-127`, `:243-250`) and both templates already render it
(`_contacts_panel.html:42`, `_interactions_panel.html:29`). Routes 1–5 need no
template work. Routes 3–5 are reachable **today** with no revert involved:
remove a contact in one tab, click its email cell in another.

**`web/routes/work.py` — 13 routes, three panels need an `error` slot**

| # | Route | Guard | Answer |
| --- | --- | --- | --- |
| 6 | `GET  /accounts/{ref}/tasks/{id}/cell/{key}` | `:168` | `#tasks-panel` OOB |
| 7 | `GET  /accounts/{ref}/tasks/{id}/cell/{key}/edit` | `:175` | `#tasks-panel` OOB |
| 8 | `POST /accounts/{ref}/tasks/{id}/cell/{key}` | `:185` | `#tasks-panel` OOB |
| 9 | `POST /accounts/{ref}/tasks/{id}/done` | `:214` | `#tasks-panel` OOB |
| 10 | `GET  /accounts/{ref}/requests/{id}/edit` | `:270` | `#requests-panel` OOB |
| 11 | `POST /accounts/{ref}/requests/{id}/edit` | `:280` | `#requests-panel` OOB |
| 12 | `GET  /accounts/{ref}/requests/{id}/items/new` | `:371` | container → redirect |
| 13 | `POST /accounts/{ref}/requests/{id}/items/new` | `:381` | container → redirect |
| 14 | `GET  …/items/{item_id}/cell/{key}` | `:430` | item gone → `#items-panel`; request gone → redirect |
| 15 | `GET  …/items/{item_id}/cell/{key}/edit` | `:442` | as 14 |
| 16 | `POST …/items/{item_id}/cell/{key}` | `:459` | as 14 |
| 17 | `POST …/items/{item_id}/received` | `:491` | as 14 |
| 18 | `GET  /accounts/{ref}/requests/{request_id}` | `:504` | **excluded** — navigation, D5 |

`_tasks_panel` (`work.py:109`), `_requests_panel` (`:241`) and `_items_panel`
(`:363`) take no `error` and their templates render none — three signatures and
three templates to extend, using `_contacts_panel.html:42` verbatim.
`.form-error` is already styled (`web/static/app.css:1077`); no CSS.

Routes 14–17 branch because `_owned_item` (`account.py:177-189`) checks the
request first and the item second: a gone item leaves `#items-panel` renderable,
a gone request does not (`work.py:352`).

**How rows 6–18 go stale today:** no surface deletes a task, request or item, so
the live path is reverting the batch that created one — `services/batches.py:373-377`
soft-deletes on reverting a create. That is reachable from the web's own rail,
in the next tab.

**Unchanged:** the four `_owns_raw_row` routes (`relationship.py:326`, `:356`,
`:423`, `:456`) already answer correctly; `changes.py`'s revert is D1;
`account_tab`/`account_root` name no row.

---

## Tests, with the mutation that must break each one

Every test below must be run against a deliberately broken production line and
observed failing before it is believed. Per CLAUDE.md, a green suite proves
nothing broke, not that the new path is taken.

**Entry 1 — `tests/test_web_scoping.py`** (the file that already owns "an unknown
id and someone else's id answer the same", `:218-225`)

- `test_another_accounts_batch_ref_answers_exactly_like_an_unknown_one` —
  status, `HX-Redirect` outcome token and the landing page's sentence are equal
  for an unknown ref and a foreign ref; and the foreign batch's `reverted_at`
  stays `None`.
  *Mutation A:* restore `raise HTTPException(404, …)` at `changes.py:109` → the
  two answers differ → fails.
  *Mutation B:* delete the `batch.org_id != org.id` check outright → the foreign
  batch is reverted → the `reverted_at` assertion fails. B is the one that
  matters: it proves the test still guards authorization and not merely shape.

**Entry 1 — `tests/test_web_writes.py`**

- Rewrite `test_a_batch_from_another_account_is_not_revertible` (`:247-273`):
  404 → 204 + `outcome=gone`. **Keep** both existing invariants — the title is
  unchanged, `reverted_at` is `None` — and keep the control at `:270-272` (the
  same batch reverts under its own account), without which the test passes
  against a route that does not exist.
- `test_gone_renders_for_a_batch_that_belongs_to_another_account` — pins
  `changes.py:221-222`, currently unexercised by anything.
  *Mutation:* delete those two lines → `toast_for` returns `None` for a foreign
  ref → no sentence on the landing page → fails.
- One assertion that the rail cannot contradict the toast: the foreign ref does
  not appear in the rendered page (`account.py:391-393`).
  *Mutation:* drop the `b.org_id == org.id` filter → the ref appears beside a
  toast saying it does not exist → fails.

**Entry 2 — new `tests/test_web_staleness.py`**

- `test_every_row_route_answers_a_stale_click_in_the_page` — the table of 17,
  each asserting the D2 shape (see D6).
  *Mutation, per family:* revert one route's guard to the plain `_owned` call →
  that row 404s → the test names it. Run it for one route in each of the four
  families (contact cell, interaction form, task panel-target, item container).
  *Mutation, shape:* return the panel **not** out of band → the `startswith`
  assertion fails (this is the fix shape that reproduced the bug it closed, on
  2026-08-18 — it must stay caught).
  *Mutation, shape:* render the sentence outside the OOB element → the index
  assertion fails.
- `test_no_row_route_is_missing_from_the_staleness_table` — the route-table walk.
  *Mutation:* delete one entry from the table → fails naming that route.
  *Mutation:* register a throwaway `/accounts/{ref}/widgets/{id}` route → fails.

**Entry 2 — `tests/test_web_scoping.py`, extended**

- Widen `test_an_unknown_id_and_someone_elses_id_answer_the_same` (`:218-225`)
  to four cases: unknown → 404, foreign-alive → 404, **foreign-deleted → 404**,
  own-deleted → 200 with a sentence.
  *Mutation:* have the new guard check `deleted_at` before ownership → the
  foreign-deleted case answers 200 → fails. This is the test that keeps the
  oracle shut on the entry-2 side.
- `test_no_route_reaches_another_accounts_row` (`:178-202`) and
  `test_an_item_whose_request_was_removed_404s_instead_of_500ing` (`:291-318`)
  must stay green **unedited**. If either needs editing, the guard has widened
  past its brief — stop and re-read D3.

**Gate:** `uv run --no-sync python -m pytest -q`, `uv run mypy src`,
`uv run ruff check src tests`, redirected to the scratchpad, gating on the
command and not on a pipe. mypy is doing real work here: it is what proves all
eighteen call sites were swept.

**Browser pass:** Playwright, two tabs on one account. Remove a contact in tab A,
click that card's email cell in tab B — the panel must refresh with the sentence
and without the card. Repeat for a task via Revert in the rail. Markup-reading
missed real bugs on this surface twice.

---

## Roadmap edits when this lands

Both entries collapse into one "SHIPPED" pointer, in the style of ROADMAP.md:56.
While editing, fix the counts — `docs`, `ROADMAP.md` and `account.py:96-109`
should agree that 22 routes name a ref and a row, 18 go through `_owned`, and 9
of those are cell routes.

---

## Worktree

`.claude/worktrees/web-refusal`, its own worktree per CLAUDE.md. `uv sync
--group dev` first, and do not delete `.claude/worktrees/towerkit`. Two
implementer sessions are live in `web-account` and `web-work` as of this
writing; both touch `relationship.py` and `work.py`, so **rebase before the
sweep, not after** — an 18-site edit is the worst possible thing to merge
blind.



---

## Verification report (independent adversarial pass, 2026-08-18)

**Verdict: needs-revision.** The factual spine is sound and unusually well checked: all seven roadmap corrections hold (I confirmed :275 is `context["oob"] = False`, :293 is inside interaction_update's docstring, the eighteen/thirteen counts are verbatim in account.py:104-109, test_web_concurrency.py has exactly three tests and no stale-click test, and — by tracing every gone-token test — that changes.py:221-222 is genuinely unexercised, so deleting those two lines leaves the suite green). Every entry-1 mutation is real and falsifiable, including the rail assertion, since _change_row carries `ref` and page.html:132 renders it. It is not yet safe to build from for three reasons: D6's completeness test as specified over-selects by six routes and its EXCLUDED map names only one, which makes the branch's only sweep guarantee red on day one and invites being padded quiet; the document rules three incompatible things about request_detail across D5, the sweep table and the resolved open decision; and the htmx mechanism the whole panel shape rests on is misread — the `if(!i){return}` early return fires only when the primary target IS the OOB-replaced element (routes 9 and 17), not for the cells and .form-hosts of the other sixteen, where the swap lands in a detached subtree instead. The weakest point is the shape mutation: "return the panel not out of band → the startswith assertion fails" is false against test_contact_remove.py:462, and that misattribution is exactly the class of near-miss this ledger exists to catch.


### Citations that did not check out

- **`src/bookkit/web/static/htmx.min.js (function Me / function c) — cited in the "fork is decided by the DOM" paragraph, in D2's cost-if-wrong, and in roadmap correction 5`** — claimed: "htmx's outerHTML swap is a verified silent no-op on a detached target: Me(t,e,n) does const i=c(t); if(!i){return}, and c returns parentElement. So a response carrying only an OOB panel replaces the panel — which removes the stale cell — and the primary swap into the now parentless cell returns immediately."
  
  *Actually:* The two functions say what is quoted, but they do not do what is claimed for 16 of the 17 sweep routes. He (the OOB swap) calls _e("outerHTML", e, e, t, i) -> Me(livePanel, ...), which ends in t.remove() — that detaches the OLD PANEL from the document, but the panel's descendants keep the old panel as their parentElement. So for a cell (inside a <td>/<tr>/card, several levels down) or for a .form-host (a direct child of the panel, _contacts_panel.html:51), c(target) returns a non-null detached ancestor and Me does NOT early-return: it runs a(i,t,e,n) and t.remove() inside the orphaned subtree. The early return fires only when the primary target IS the OOB-replaced element — i.e. exactly routes 9 (task_done, _tasks_panel.html:47-48) and 17 (item_received, _items_panel.html:56-57), the two the draft does not cite it for. The OUTCOME the draft wants (nothing visible happens) still holds, because the swap lands off-document; the stated mechanism, and therefore the claimed source-level verification, is wrong.

- **`src/bookkit/web/routes/account.py:169-172`** — claimed: "_owned's comment (account.py:169-172, 'telling the two apart is how a guessable id becomes a membership oracle')"
  
  *Actually:* That sentence is at account.py:165-167 (the closing lines of _owned's docstring). Lines 168-173 are the try/except fetch and the `if org.id not in _owner_org_ids(...)` ownership check — code, not the comment quoted. The enumerated citation 11 gives the correct range (155-174); only the in-body one drifts.

- **`tests/test_web_writes.py:691-693`** — claimed: "test_toast_for_refuses_a_batch_from_another_account, tests/test_web_writes.py:691-693, loops over reverted/already/refused/program and skips gone"
  
  *Actually:* :691 is `assert _revert(client, other.ref, batch.ref).status_code == 204`, :692 is blank, :693 is the comment `# the same ref, rendered on the OTHER account's page: no toast at all`. The loop is at :694-696. The claim itself is TRUE and I confirmed it by mutation reasoning (see below); only the line anchor is wrong.

- **`src/bookkit/services/batches.py:373-377`** — claimed: "Reverting a batch's creates calls base.soft_delete (services/batches.py:373-377)"
  
  *Actually:* :373 is the closing `)` of the preceding base.update call in the `updates` loop. The `for change in creates:` loop is :374-378 and base.soft_delete is :376-378. Off by one at both ends; the substance is correct.


### Claims challenged (even where the citation resolved)

- **[CRITICAL]** D6, test 2: "Walk app.routes, select every path containing {ref} plus at least one further {param}, and assert each is either in the table or in a named EXCLUDED mapping (today: request_detail, with its reason as the value)."
  
  *Evidence:* That selector over-selects by six routes beyond request_detail, all of which are absent from both the sweep table and the named EXCLUDED map: /accounts/{ref}/{tab} (account_tab, account.py:434), /accounts/{ref}/changes/{batch_ref}/revert (changes.py:83), and the four _owns_raw_row routes — contact remove GET/POST (relationship.py:395, :436) and interaction delete GET/POST (relationship.py:314, :343). The document knows about all six elsewhere ("account_tab/account_root name no row"; "the four _owns_raw_row routes ... already answer correctly") but D6 does not reconcile them. As specified the completeness test is red on day one, and the obvious fix — padding EXCLUDED until it goes green — silently converts the project's only real sweep guarantee into a list someone edits to make CI quiet. This is the safety net D2 explicitly rejects the JS listener in favour of, so it is load-bearing.

- **[CRITICAL]** request_detail (work.py:504) is "excluded — navigation, D5" and D6's EXCLUDED map contains it.
  
  *Evidence:* The document rules three incompatible things about the same route. D5's heading: "request_detail keeps its 404, and says so out loud." The sweep table row 18: "excluded — navigation, D5." D6: EXCLUDED = {request_detail}. The resolved open decision: "Does request_detail keep its bare 404, or redirect ... → Redirect." A builder following the body ships 17 routes plus a named exclusion; a builder following the open-decisions answer ships 18 and an empty EXCLUDED, which changes both the sweep table and D6's second test. Two readings, materially different work, and no ruling. It should be settled before the branch, not discovered at the D6 test.

- **[IMPORTANT]** "Mutation, shape: return the panel not out of band → the startswith assertion fails (this is the fix shape that reproduced the bug it closed, on 2026-08-18 — it must stay caught)."
  
  *Evidence:* It does not fail. _contacts_panel.html:35 renders `<div id="contacts-panel"{% if oob %} hx-swap-oob="true"{% endif %}>`, so a non-OOB panel's body is `<div id="contacts-panel">…` and `body.startswith('<div id="contacts-panel"')` (tests/test_contact_remove.py:462, no closing angle bracket in the prefix) still passes. The assertion that actually catches that mutation is the separate `assert "hx-swap-oob" in body` at :466 — which is why the existing test carries both. The draft attributes the catch to the wrong assertion. This is precisely the "a test that passes for a reason adjacent to what it claims" failure the ledger records; a per-route table built from the draft's reasoning could drop :466 as redundant and lose the guard.

- **[IMPORTANT]** D3: "_owned and _owned_item (account.py:155-189) grow a required keyword parameter — no default — naming what to answer with. mypy, already in the gate, then fails any call site that omits it." and "mypy is doing real work here: it is what proves all eighteen call sites were swept."
  
  *Evidence:* mypy proves the eighteen sites PASS the parameter, not that they pass a correct one — eighteen sites passing the same wrong panel type-check clean. That is the 2026-08-15 FormModal lesson the document itself cites, re-committed one level up. Second, the design has an unresolved import problem the document never sites: account.py is imported BY relationship.py and work.py, and account.py already imports changes.toast_for INSIDE a function specifically to avoid the cycle (the comment at account.py:408-412 spells this out). A guard living in account.py cannot reference _contacts_panel / _tasks_panel / _items_panel at module level, so "the decision table, once, in the guard" cannot literally render — the parameter must be a caller-supplied callable or spec, which the document does not say. Third, `Model.from_row` requires a kind→model mapping that does not exist in account.py today: `kind` is a bare str and `_Owned` is a TypeVar bound by the caller's fetch. New machinery with no named home.

- **[IMPORTANT]** D2's cost-if-wrong: "If the OOB-detached-target reasoning is wrong (it is verified against the shipped minified source, not from memory), the symptom is a stale cell surviving a panel refresh, visible in the first Playwright pass."
  
  *Evidence:* The reasoning is wrong in the way described in the citation-failures section, and the named symptom is not the one that would appear. Because Me proceeds rather than early-returning, htmx performs a real swap inside the orphaned subtree and then removes the target from it — nothing survives on screen, so a Playwright pass looking for "a stale cell surviving" sees nothing wrong. The interaction the browser pass should actually cover is the one the document does not name: a cell whose EDITOR IS OPEN when the save lands. inline-cell.js holds live state across that request — `committing` reset on htmx:afterRequest (:68-77), `pendingHop` set by Tab (:90-106), and the htmx:afterSwap handler that focuses any newly-swapped .cell-editing (:117-130). The prescribed Playwright script covers only "click the email cell" and "repeat for a task via Revert", neither of which has an editor open.

- **[MINOR]** "Six of the routes in the entry-2 sweep cannot be answered with a panel, because the panel they live in describes the very record that is gone" (work.py:371, :381, :430, :442, :459, :491).
  
  *Evidence:* True for routes 12-13 (item_new_form/item_create, guarded on the request alone). For routes 14-17 it is conditional, not categorical: _owned_item checks the request first and the item second (account.py:184-185, correctly cited), so when only the ITEM is gone #items-panel renders fine and the panel shape applies — which the sweep table itself says ("item gone → #items-panel; request gone → redirect"). The opening ruling states as a flat fact what the table states as a branch. It does not change the conclusion (the branch still needs entry 1's idiom, so the two entries still belong together), but a builder reading only the ruling would wire four routes to the redirect unconditionally.

- **[MINOR]** "Routes 3–5 are reachable today with no revert involved: remove a contact in one tab, click its email cell in another."
  
  *Evidence:* True, but it understates the entry-2 case by leaving out the routes the ROADMAP entry actually named. interaction_delete (relationship.py:343) is a live soft-delete writer on the web surface, so routes 1-2 (interaction_edit_form :283, interaction_update :302) are equally reachable stale today with no revert. The document frames only the contact half as reachable-now, which makes the interaction half look like the speculative part of the sweep when it is not.

- **[MINOR]** D3: "The third row of that table is what keeps the oracle shut: a foreign ref answers 404 whether it is alive or dead."
  
  *Evidence:* The row that says that is the second body row of the table ("present | another account | either | 404"). The third body row is "present | this account | NULL | the entity, as today". Only reads correctly if you count the header as row 1. In a document whose whole subject is which of two misses gets which answer, the off-by-one is worth fixing.

- **[MINOR]** "Roadmap edits when this lands ... fix the counts — docs, ROADMAP.md and account.py:96-109 should agree that 22 routes name a ref and a row, 18 go through _owned, and 9 of those are cell routes."
  
  *Evidence:* The counts are right (I counted 22 entries in tests/test_web_scoping.py:107-153, 18 _owned/_owned_item handler call sites — 5 in relationship.py, 13 in work.py — and 9 cell routes: relationship.py:488/:495/:507, work.py:168/:175/:185, work.py:430/:442/:459), but the list of places to fix misses one: tests/test_web_scoping.py:179 says "One assertion for eighteen handlers" in the docstring of the test that drives 22 calls. Same drift class the document is correcting, in the file it is extending.


### Decisions the draft left open

- **Does `request_detail` (`work.py:504`) keep its bare 404, or redirect to `/accounts/{ref}/work` with the `gone` token?**
  - Recommendation: Redirect. It is reached from a rendered `Items` link on a panel that may itself be stale (`_requests_panel.html:46-47`), so the click is the same stale-tab case as the rest of the sweep — the only difference is that the browser shows FastAPI's JSON `detail` rather than nothing. Redirecting also removes the sweep's only asymmetry. Recorded as a recommendation rather than a ruling because it is the one route where the current behaviour is ugly rather than invisible, so it is a taste call, not a defect.
  - Cost if wrong: Low and symmetric. If we redirect and shouldn't have, a genuinely bad URL is silently forgiven instead of reported — annoying while debugging, harmless in use. If we 404 and should have redirected, one route in eighteen answers a stale click with a JSON blob. Either is a one-line change behind the same guard.

- **Does the container family (routes 12-17 when the REQUEST is the gone record) ship in this branch, or defer to ROADMAP.md:76 'Deactivating a record, generally'?**
  - Recommendation: Ship it here. A gone request is reachable today by reverting the batch that created it (`services/batches.py:373-377`), and deferring leaves six of seventeen routes silent inside a branch whose whole claim is uniformity. It is the only part of the sweep that needs new machinery — a `gone` token that names no batch, with a frozen vocabulary, rendered through `_context`'s existing toast slot (`account.py:425`) as `toast_for(...) or gone_toast_for(...)`; the two cannot collide because they come from different redirects.
  - Cost if wrong: Moderate — it is the only new vocabulary in the branch, and a token family that grows carelessly is how the revert toast became an injection surface once already (`changes.py:29-39`). Contained by the same rule that fixed that: fixed prose keyed by a frozenset, nothing from the query string reaching the text. If it proves wrong, the six routes fall back to their current 404 by deleting one branch.

- **Exact wording for the three kinds with no service sentence (task, request, item).**
  - Recommendation: `f"{label} is no longer on {org.name}"`, mirroring the shape of `already_removed`/`already_deleted` (`services/contacts.py:155`, `services/interactions.py:98`) without claiming a deletion vocabulary those kinds do not have — nothing 'removes' a task on any surface today; a revert does. No '— refresh' tail: the response is the refresh, which `tests/test_contact_remove.py:470` already treats as the correct outcome.
  - Cost if wrong: Copy only, in one function. Worth flagging to Grant because the last unanswered copy question on this surface (the timeline empty state) shipped on a guess and is recorded as still open in `handoffs/20260818-WebUI.md`.


### Corrections this draft makes to the ROADMAP entry

- ROADMAP said: The stale-Edit route 'answers 404 (web/routes/relationship.py:275, :293)'.
  - Code says: Neither line is a 404 site. `:275` is `context["oob"] = False` inside `relationship_tab`; `:293` is the second line of `interaction_update`'s docstring. The real `_owned` calls for the two interaction routes are `:283` and `:302`. (`src/bookkit/web/routes/relationship.py:275, :292-299, :283, :302`)

- ROADMAP said: '...and the same in the thirteen pre-existing contact/task/request cell routes' — i.e. thirteen cell routes plus the two new interaction routes.
  - Code says: Thirteen is `work.py`'s count of `_owned`/`_owned_item` HANDLERS, which `account.py:107-109` states verbatim ('five handlers in relationship.py, thirteen in work.py'); it is not a count of cell routes and it includes `task_done`, `request_edit_form`, `request_update`, `request_detail`, `item_new_form`, `item_create` and `item_received`, none of which are cells. The actual cell routes are NINE — contact, task and item, three apiece (`relationship.py:488/:495/:507`; `work.py:168/:175/:185`; `work.py:430/:442/:459`). The sweep set is EIGHTEEN `_owned`/`_owned_item` handlers out of 22 routes that name a ref and a row. (`src/bookkit/web/routes/account.py:107-109; tests/test_web_scoping.py:107-153 (22 calls, counted programmatically)`)

- ROADMAP said: 'Pick one and apply it to all fourteen.'
  - Code says: Fourteen matches nothing in the code and does not even follow from the entry's own arithmetic (thirteen plus two is fifteen). The number is eighteen, of which seventeen are htmx routes and one (`request_detail`, `work.py:504`) is a browser navigation that must be excluded and named as an exclusion. (`src/bookkit/web/routes/relationship.py:283, :302, :488, :495, :507; src/bookkit/web/routes/work.py:168, :175, :185, :214, :270, :280, :371, :381, :430, :442, :459, :491, :504`)

- ROADMAP said: 'Touches ... tests/test_web_concurrency.py (which already owns "a stale click says so" for the remove control).'
  - Code says: test_web_concurrency.py owns three tests: per-thread connections, a mixed read/write hammer, and the `db._tx_lock` queueing test. It contains no stale-click test and no mention of the remove control. The stale-click tests are `tests/test_contact_remove.py:413` and `:435`, and the second of those is the exact assertion set the sweep should copy. (`tests/test_web_concurrency.py:1-249; tests/test_contact_remove.py:413-431, :435-470`)

- ROADMAP said: 'a stale Edit has no panel of its own to refuse into on some routes (a cell editor's target is the cell)' — presented as the reason the fork between a cell-level and a panel-level answer is hard.
  - Code says: Every cell lives inside a panel with a stable id, and htmx's outerHTML swap on a detached target is a silent no-op: `Me(t,e,n)` does `const i=c(t); if(!i){return}` and `c` returns `parentElement`. So a response carrying only the OOB panel replaces the panel (removing the stale cell) and the primary swap into the now-parentless cell returns immediately. The panel shape works for cell routes; the cell shape does not work for the nine non-cell routes, whose targets are `.form-host` or the panel itself. The fork is decided by the DOM, not by taste. (`src/bookkit/web/static/htmx.min.js (function Me / function c); src/bookkit/web/templates/account/_requests_panel.html:49-50; src/bookkit/web/templates/account/_tasks_panel.html:47-48`)

- ROADMAP said: Entry 2 frames the fix as 'a BRANCH-WIDE sweep across fourteen routes with ONE SHARED ANSWER'.
  - Code says: One shared RULE, but not one shared answer for every route: `_items_context` reads the alive-filtered `rfi_repo.get_request` at `work.py:352`, so when the gone record is the REQUEST rather than a row inside it, `#items-panel` cannot be rendered at all. Six routes (`work.py:371, :381, :430, :442, :459, :491`) therefore need the level-up answer — which is entry 1's redirect-plus-token idiom, and is the mechanical reason the two entries belong on one branch. (`src/bookkit/web/routes/work.py:343-360, :371, :381, :430, :442, :459, :491`)

- ROADMAP said: Entry 1: 'Touches. web/routes/changes.py only; tests/test_web_scoping.py is where the assertion belongs.'
  - Code says: Accurate as far as it goes, with one omission worth recording: the collapse's correctness depends on `toast_for`'s org check inside the `gone` branch (`changes.py:221-222`), which no test currently exercises — `test_toast_for_refuses_a_batch_from_another_account` loops over `reverted/already/refused/program` and skips `gone`. That line goes from crafted-URL-only to being the route's own output, so it needs a test in the same commit. (`src/bookkit/web/routes/changes.py:221-222; tests/test_web_writes.py:691-693`)
