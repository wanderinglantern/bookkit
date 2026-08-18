<!-- ROUND 2 DRAFT. Research verified; DESIGN NOT APPROVED. Read the bottom before building. -->

> **Status: research verified — design NOT approved** (2026-08-18, round 2 of 2).
>
> Drafted against the code, rejected by an adversarial pass, revised, and rejected again.
> **46 citations re-checked · 9 still failed ·
> 10 claims challenged · 4 regressions ·
> 8 decisions reversed from round 1.**
>
> **Iteration was stopped here deliberately, and that is a ruling, not an omission.** Round 2 fixed
> most of what round 1 got wrong and then committed the same defect class again — in one case, in
> the fixes themselves. These documents specify code that does not exist yet, and every added
> specificity is a fresh opportunity for a confident false claim. The build's own record is that
> the runtime reasoning holds and the speculative citations do not.
>
> **So use this for its RESEARCH, not its conclusions.** The verified findings about how the code
> actually behaves are the valuable part and were reproduced by two independent passes. Re-decide
> the design at build time, against the real code, and treat every design decision below as a
> starting proposal carrying a named cost — not a settled call.
>
> Kind: `task-brief`.

---


# The web's refusal contract — one rule for "not there, or not yours"

Date: 2026-08-18 (revised after adversarial verification)
Kind: task-brief — the design is settled below; this is buildable as written.
Covers: `ROADMAP.md:206` (the revert oracle) and `ROADMAP.md:238` (the stale Edit)

**Verified against the main worktree at `41d5ea1`, not at `6bab7f8`.** The earlier draft
was anchored at `6bab7f8`; `account.py` has moved 171 lines since (`git diff --stat
6bab7f8 HEAD`), so every `account.py` and `app.css` line number below is re-taken.
`relationship.py`, `work.py`, `changes.py`, `test_web_scoping.py` and
`test_contact_remove.py` are unchanged between those commits, so their anchors carry.

---

## Ruling: one branch, two commits

Both roadmap entries ask the same question — what does a web route answer when the row
named in the URL is **not there** or **not yours** — and the codebase answers it two ways.
The revert control tells the two misses apart (`changes.py:97-102` redirects, `:103-107`
raises 404) where `_owned` deliberately does not (`account.py:123-128`, `:164-183`); the
other eighteen routes tell nobody anything at all.

They are one **branch** for a mechanical reason. Six of the eighteen (`work.py:371`,
`:381`, `:430`, `:442`, `:459`, `:491`) sit on the request-detail page, whose *every*
element describes one request — `_items_context` reads the alive-filtered
`rfi_repo.get_request` at `work.py:352`, and `request_detail.html` renders the title, ref,
asker, scope and dates off that same row. When the gone record is the request, there is no
surviving container on that page at all, so the answer has to go one level up: a redirect
with a token, which is exactly the idiom entry 1 standardises. Building entry 2 first would
either invent a second toast vocabulary or leave that family silent.

They are two **commits**: entry 1 is a four-line route change plus tests; entry 2 is an
eighteen-route sweep. A reviewer should be able to read them apart.

**Counter-argument, recorded:** entry 1 alone is ~30 minutes and bundling it behind a sweep
delays it. I take the bundle anyway — shipping "a refusal says something, in the smallest
surviving container" while the revert control still answers a miss two different ways ships
a contract that contradicts itself on the day it lands. This is the one scope-vs-time call
worth Grant's yes/no before the branch opens.

---

## What the code actually says

**The revert route's two shapes are real.** `changes.py:97-102` answers an unknown
`batch_ref` with `_redirect(..., "gone")` — 204 + `HX-Redirect` (`changes.py:73-81`).
`changes.py:103-107` answers another account's `batch_ref` with `HTTPException(404)`. Refs
enumerate: `next_ref` formats `f"{kind}-{row[0] - 1:04d}"` off a per-kind counter
(`ids.py:34-45`) and every batch takes `BATCH_REF = "MCP"` (`repo/batches.py:12`).

**The render side of the collapse is already built.** `toast_for`'s `gone` branch already
returns "that change no longer exists" for a batch that resolves but belongs to another
account (`changes.py:221-222`). The rail cannot contradict it: `_context` filters
`batches_repo.recent` to `b.org_id == org.id` (`account.py:545-547`), and the rail prints
the ref only inside the Revert button's URL (`page.html:160`), so a foreign ref is never on
the page the redirect lands on. **The collapse needs no template and no toast change** —
only tests, because `changes.py:221-222` is currently unexercised:
`test_toast_for_refuses_a_batch_from_another_account` loops over
`reverted/already/refused/program` at `tests/test_web_writes.py:741-743` and skips `gone`.

**The silence is real.** htmx 2.0.4 ships
`responseHandling:[{code:"204",swap:false},{code:"[23]..",swap:true},{code:"[45]..",swap:false,error:true}]`
(`web/static/htmx.min.js`) — neither 4xx nor 5xx swaps — and `inline-cell.js` registers
listeners for `submit` (:55), `htmx:afterRequest` (:68), `focusout` (:82), `keydown` (:90,
:108) and `htmx:afterSwap` (:117), and **none** for `htmx:responseError`. Every
`_owned`/`_owned_item` refusal on a control the page itself rendered is a click into the
floor.

**The count is eighteen, and nine of them are cells.** `tests/test_web_scoping.py:107-153`
drives 22 routes that name an account and a row. Four already answer staleness correctly
through `_owns_raw_row` (`relationship.py:326`, `:356`, `:423`, `:456`). The other
**eighteen** go through `_owned`/`_owned_item`: five in `relationship.py` (`:283`, `:302`,
`:488`, `:495`, `:507`) and thirteen in `work.py` (`:168`, `:175`, `:185`, `:214`, `:270`,
`:280`, `:371`, `:381`, `:430`, `:442`, `:459`, `:491`, `:504`). That is the count
`account.py:113-117` already carries in prose. Nine are cell routes —
`relationship.py:488/:495/:507`, `work.py:168/:175/:185`, `work.py:430/:442/:459`.

**The htmx mechanism, corrected.** The earlier draft claimed an outerHTML swap on a
detached target is a verified silent no-op because `Me(t,e,n)` does `const i=c(t);
if(!i){return}` and `c` returns `parentElement`. Both functions say that, but the early
return fires **only when the primary target IS the element the OOB swap replaced**. `He`
(the OOB path) ends in `Me(livePanel, …)`, whose last statement is `t.remove()` — that
detaches the *old panel node*; the panel's descendants keep the old panel's subtree as
their ancestry, so `c(cell)` and `c(.form-host)` return a non-null **detached** parent and
`Me` runs the swap inside the orphan. The visible outcome the design wants still holds
(nothing lands on screen, because the swap happens off-document), but the mechanism is
detachment, not a null-parent early return. `_interactions_panel.html:14-16` and
`relationship.py:243-250` already state it correctly: *htmx applies out-of-band content
BEFORE the primary swap, so anything outside the OOB element lands in a node the OOB
replace has already detached.* Ordering confirmed in `$e`: `ze(n,l,i)` (OOB) runs, then
`_e(r.swapStyle, …)` (primary).

This correction **changes a decision**, not just a footnote — see D2.

---

## Decisions

### D1 — Both revert misses collapse to the redirect and the same "gone" toast

`changes.py:103-107` stops raising and returns `_redirect(ref, tab, batch_ref, "gone")` —
the same call `:102` already makes. The org check itself **stays**: it must still run
before `batches_svc.revert`, or a crafted URL on account A reverts account B's write. Only
the shape of its refusal changes.

*Rejected — both 404.* It answers the common case (a stale tab clicking Revert on a batch
the TUI already reverted) with a blank screen, on a control the page rendered. The route's
own comment argues the other way (`changes.py:99-101`: "an unknown ref is a stale page, not
an intrusion").

*Rejected — leave it, exposure is zero.* True and stated in the roadmap entry; irrelevant
to the reason to fix it. The value is one rule for one question, and `_owned`'s docstring
(`account.py:174-176`: "telling the two apart is how a guessable id becomes a membership
oracle") stops being contradicted by the one route written before it.

**Cost if wrong:** a user who genuinely pasted another account's ref lands on a page saying
the change does not exist, when it exists elsewhere. That is the intended lie — the same
one `_not_here` tells (`account.py:123-128`) — and the write is still refused. Four lines
to revert.

### D2 — ONE shape: 200, the surviving panel, out of band, sentence inside it

**The rule.** When a route refuses because the row is *this account's and gone*, it answers
**200 with the panel that listed the row, carrying `hx-swap-oob="true"`, and nothing else
in the body, with the sentence rendered INSIDE that panel element.** When the record the
page itself describes is the gone one, it answers one level up: a redirect to the tab that
lists it, carrying a token. When the route is a browser navigation, the redirect is a real
3xx instead of `HX-Redirect`. Never a silent 4xx on a control the page rendered.

**`oob=True` on every one of the fifteen panel-family routes — no per-route judgement.**
This reverses the draft's implicit "match the trigger" reading, and the corrected htmx
mechanism is why:

- Where the trigger targets something *inside* the panel (a cell, a `.form-host`) — routes
  1–8, 10–11, 14–16 — `oob=True` is the only correct answer. `oob=False` returns the panel
  as the primary swap into a `.form-host` the panel contains, nesting a second panel inside
  the first: the trap `contact_create` had to solve (`_contacts_panel.html:18-34`).
- Where the trigger targets the panel itself with `outerHTML` — `task_done`
  (`_tasks_panel.html:46-48`) and `item_received` (`_items_panel.html:55-57`) —
  **both** work. `oob=False` is a straight replace; `oob=True` works because this is the
  one case where `Me`'s `if(!i){return}` genuinely fires: `He` replaced the target node
  itself, so by the time the primary swap runs, `target.parentElement` is null.

Since `oob=True` is correct for all fifteen and `oob=False` is correct for only two, the
sweep uses `oob=True` everywhere. One branch, one shape, nothing for a future route to get
wrong.

*Rejected — the re-rendered cell saying "gone — refresh".* (a) It does not generalise: nine
of the eighteen have no cell. (b) A cell saying "gone" leaves the rest of a dead row on
screen still claiming to be live; the panel refresh removes the row, which
`tests/test_contact_remove.py:470` already asserts is the right outcome. (c) "refresh" is
the wrong instruction when the response *is* the refresh.

*Rejected — an `htmx:responseError` listener in `inline-cell.js`*, in all three roles
(replacement, safety net, belt-and-braces). The app has several **correct** 404s that must
never surface as a banner: a non-editable cell key (`tests/test_web_writes.py:154`,
`tests/test_web_work.py:141`, `:310`), a crafted `tab` (`tests/test_web_writes.py:379`),
and every cross-account refusal (`tests/test_web_scoping.py:178`). A global handler cannot
tell those from a stale click. Worse, it makes every future route's silence *look* handled:
the next handler that forgets the treatment gets a plausible banner instead of a failing
test. The safety net is D6, which fails at gate time.

*Rejected — flipping `contact_remove`'s existing `oob=False` (`relationship.py:461-463`) to
`True` for surface-wide uniformity.* It would work (same early-return case), but it churns
a green, tested destructive path for symmetry alone. Left as an optional two-line follow-up
behind `tests/test_contact_remove.py:413-431`; **not in this branch.**

**Cost if wrong:** if the panel shape is wrong for a family we have not foreseen, the blast
radius is one helper signature and one lambda per route — the sentence and the guard both
live in one place by construction. If the OOB reasoning is wrong, the symptom is *not* "a
stale cell surviving" (the swap lands off-document either way); it is that the panel does
not refresh at all, or that a second panel nests inside the first. Both are visible in the
first Playwright pass and both are asserted in D7's table.

### D3 — Staleness is decided in the guard, and the guard makes it mandatory

`_owned` (`account.py:164-183`) and `_owned_item` (`account.py:186-197`) grow **required
keyword parameters — no defaults**:

```python
def _owned(conn, org, kind, entity_id, fetch, *, stale: Callable[[str], Response]) -> _Owned
def _owned_item(conn, org, request_id, item_id, *,
                stale_request: Callable[[str], Response],
                stale_item: Callable[[str], Response]) -> RfiItem
```

`stale` takes the sentence and returns the already-rendered answer. mypy, already in the
gate, then fails any of the eighteen call sites that omits it. `_owned_item` demands two
because routes 14–17 genuinely have two answers (D2's fork), and a signature that forces
the caller to name both is what stops one of them being defaulted away.

**The guard raises, it does not render.** `account.py` is imported *by* `relationship.py`
(`:57`) and `work.py` (`:53`), and it already imports `changes.toast_for` inside a function
specifically to dodge the cycle (`account.py:562-565`). A guard in `account.py` therefore
cannot reference `_contacts_panel` / `_tasks_panel` / `_items_panel` at module level. So:

```python
class _Stale(Exception):          # carries an already-rendered Response
    def __init__(self, response: Response) -> None: ...
```

registered once in `create_app` (`web/app.py:167`, beside the router includes at
`:195-209`) as `app.add_exception_handler(_Stale, lambda request, exc: exc.response)`.
The caller's lambda closes over its own module's panel builder, so no new import edge
exists in either direction.

*Rejected — a `PANELS` registry in `account.py` keyed by a `Literal`, rendered by the
exception handler.* It type-checks the vocabulary, but it is late binding for a problem a
closure solves in one line, and it puts the panel choice one indirection away from the
route that makes it.

**The staleness question is asked of the RAW row**, and it needs its own owner rule:

```python
_RAW_KIND = {"contact": "contact", "interaction": "interaction", "task": "task",
             "request": "rfi_request", "item": "rfi_item"}

def _raw_owner_org_ids(conn, kind, row) -> set[str]: ...
```

*Rejected — the draft's `Model.from_row` + `_owner_org_ids`.* Two reasons, and the second
is fatal. (1) `_owned`'s `kind` is a bare `str` and `_Owned` is a TypeVar bound by the
caller's `fetch`; there is no kind→model map in `account.py` to build from. (2)
`_owner_org_ids`' item branch calls the **alive-filtered** `rfi_repo.get_request`
(`account.py:143-152`), so it returns `set()` — i.e. 404 — for precisely the container case
this brief exists to answer. Reusing it would defeat the design. `_raw_owner_org_ids` walks
`base_repo.raw_row(conn, "rfi_request", row["request_id"])` instead.

The decision table, once, in the guard (rows numbered so the reference below cannot drift):

| # | raw row | owner | `deleted_at` | answer |
| --- | --- | --- | --- | --- |
| 1 | missing | — | — | 404 `_not_here` |
| 2 | present | another account, or unresolvable | either | 404 `_not_here` |
| 3 | present | this account | NULL | the entity, as today |
| 4 | present | this account | set | **stale** — D2's shape |

**Row 2 is what keeps the oracle shut**: a foreign ref answers 404 whether it is alive or
dead, so nothing about another account's book leaks. That is a load-bearing property, not a
convenience, and D7 tests it directly.

**While in there: `_owns_raw_row` delegates to `_raw_owner_org_ids`.** Its current
`str(row["org_id"]) != org.id` (`account.py:215-217`) is correct only because its two
callers are contacts and interactions, which always carry `org_id`; a task owned solely
through its placement carries `org_id` NULL (`tests/test_web_scoping.py:68-76`) and would
be refused. One raw-row ownership rule, not two that agree by accident.

**Cost if wrong:** a required parameter on a shared guard is an 18-site edit, so a wrong
shape is an 18-site revert. The alternative — a default that falls back to 404 — costs one
forgotten route that looks broken for months, which is the defect being fixed.

### D4 — Where the sentence lives, and the one function that says it

`_gone_sentence(conn, org, kind, row) -> str`, in `account.py` beside the guard. One
function, two renderings: the panel family prints it inside the panel, the container family
re-derives it on the landing page.

- Contacts and interactions **reuse the service sentences they already have**:
  `contacts_svc.already_removed` (`services/contacts.py:137-155`) and
  `interactions_svc.already_deleted` (`services/interactions.py:81-98`). The web must not
  grow a second vocabulary for a fact a service already states — the defect
  `ROADMAP.md:269` records against `mcpserver._activity_delete`.
- Tasks, requests and items have **no delete writer on any surface**: `repo/tasks.py:112`,
  `repo/rfi.py:80`, `repo/rfi.py:145` have no production callers (only
  `tests/test_web_scoping.py:305`, `tests/test_rfi_repo.py:85`, `:170`,
  `tests/test_services.py:133`, `tests/test_tui.py:2297`). There is no service refusal to
  mirror, so the sentence is built once here:
  `f"{label} is no longer on {org.name}"` — `label` read off the raw row
  (`title` for task and request, `prompt` for item).

"No longer on", not "already removed": nothing removes a task on any surface today; a
**revert** soft-deletes it (`services/batches.py:374-378`). When `ROADMAP.md:76`
("Deactivating a record, generally") ships a real writer, the sentence moves to the service
and this helper delegates — write that instruction into its docstring.

No "— refresh" tail. The response is the refresh.

**Cost if wrong:** copy. One function.

### D5 — `request_detail` is IN the sweep. It redirects. The sweep is eighteen.

The earlier draft ruled this three incompatible ways (a named exclusion in the sweep table,
"keeps its 404" in its own heading, and "redirect" in its open decisions). One ruling:
**`work.py:500-511` redirects, exactly like routes 12–17, on the same guard.**

`work.py:504` is reached by a plain `<a href>` (`_requests_panel.html:46-47`), so the
transport differs — `RedirectResponse(status_code=303)` instead of 204 + `HX-Redirect`,
because htmx follows a 3xx transparently and would swap a whole page into a cell — but the
container and the token are identical.

Why redirect rather than keep the 404: **only a row that genuinely existed and is now gone
redirects.** Table row 1 (unknown id) and row 2 (foreign id) still 404, so a typo'd or
pasted URL is still reported as not-found. The debugging objection — "a 303 hides a bad
URL" — does not apply to the only case that changes.

Consequences, applied throughout: the sweep is **eighteen routes, not seventeen**; the
`EXCLUDED` map in D6 contains **no row route**; and the sweep set is exactly the set of
`_owned`/`_owned_item` call sites, which is exactly what mypy proves in D3. No asymmetry to
argue about later.

*Rejected — keep the bare 404.* Its failure mode is not silence (the browser shows
FastAPI's JSON `detail`), so it violates politeness rather than the refusal rule. But
keeping it costs the branch its one clean invariant — "the sweep set is the guard's call
sites" — for one route's worth of HTTP purity.

**Cost if wrong:** a developer pasting a genuinely-deleted request id lands on the work tab
with a sentence instead of a JSON 404. One branch to revert.

### D6 — The `gone` token: one word, verified before it is printed

Routes 12–17 (request gone) and 18 redirect to
`/accounts/{ref}/work?gone=request&id=<request_id>`.

`_gone_toast(conn, org, params)` in `account.py`; `_context` (`account.py:516-580`) renders
`"toast": toast_for(...) or _gone_toast(...)` at `:578`. No new import edge — `_gone_toast`
needs only `base_repo` and `orgs_repo`, which `account.py` already has.

`_GONE_KINDS = frozenset({"request"})` — one word, because the request is the only record
that is ever a *container*. The claim is checked before a word is rendered, the rule
`changes.py:209-222` established after review round 2 D: the raw row must exist, must be
this account's (`_raw_owner_org_ids`), and must have `deleted_at` set. **Nothing from the
query string reaches the text**: `id` is a lookup key, never printed; the label comes from
the row the database confirmed. That is review round 1 F1's rule, applied to a second token
family.

Collision: a crafted URL carrying both `outcome=` and `gone=` renders the revert toast,
because `toast_for` is evaluated first. Deterministic, and neither can print attacker prose.

*Rejected — reuse `outcome=gone`.* `toast_for`'s gone branch resolves `undo` as a **batch
ref** (`changes.py:218-222`); a request id fails to resolve and it renders "that change no
longer exists" — a true-shaped sentence about the wrong thing.

*Rejected — fixed prose with no id, so nothing has to be looked up.* A crafted
`?gone=request` would then print "… is no longer on …" on a page whose requests panel lists
everything, which is the two-contradictory-sentences bug review round 2 D already fixed
once.

**Cost if wrong:** it is the only new vocabulary in the branch. If it proves wrong, the six
container routes fall back to their current 404 by deleting one branch, and `_gone_toast`
deletes whole.

### D7 — The completeness guard walks the route table, and cannot pass by selecting nothing

New `tests/test_web_staleness.py`. The selector the earlier draft proposed — "every path
containing `{ref}` plus at least one further `{param}`" — over-selects by six routes beyond
`request_detail`, all absent from its table. Completing the map rather than narrowing the
selector, because the extra six are exactly the routes a reader would want accounted for:

```python
STALENESS   # 18 entries: (method, path) -> "#panel-id" | "redirect"
RAW_ROW     # 4: contact remove GET/POST (relationship.py:395, :436),
            #    interaction delete GET/POST (relationship.py:314, :343)
            #    — already correct via _owns_raw_row + the services' sentences
NOT_A_ROW   # 2: GET /accounts/{ref}/{tab}      (account.py:587) — names no row
            #    POST /accounts/{ref}/changes/{batch_ref}/revert (changes.py:83)
            #    — a batch is not one of the five entities; D1 owns its refusal
```

18 + 4 + 2 = **24**, and the test asserts the selected set equals the union *and* that
`len(selected) == 24`, with the number written down. A route added next month is red until
it is classified; a selector that quietly stops matching is red too.

*Rejected — an AST walk over `_owned`/`_owned_item` call sites instead.* It would pin the
same eighteen more directly, but it cannot catch the failure that actually matters: a new
route that names a row and forgets the guard **entirely** has no call site to find.

---

## The sweep, route by route — all eighteen

Panel = the OOB fragment the refusal returns. "Container" = the request itself is the gone
record, so the redirect applies.

**`relationship.py` — 5 routes. Both panels already take `error=`.**

| # | Route | Guard | Trigger target | Answer |
| --- | --- | --- | --- | --- |
| 1 | `GET  /accounts/{ref}/interactions/{id}/edit` | `:283` | `next .form-host` (`_interactions_panel.html:64-66`) | `#interactions-panel` OOB |
| 2 | `POST /accounts/{ref}/interactions/{id}/edit` | `:302` | `closest .form-host` (`macros/form.html:32`) | `#interactions-panel` OOB |
| 3 | `GET  /accounts/{ref}/contacts/{id}/cell/{key}` | `:488` | the cell (`macros/cell.html:47-50`) | `#contacts-panel` OOB |
| 4 | `GET  /accounts/{ref}/contacts/{id}/cell/{key}/edit` | `:495` | the cell | `#contacts-panel` OOB |
| 5 | `POST /accounts/{ref}/contacts/{id}/cell/{key}` | `:507` | `closest {tag}` (`macros/cell.html:73`) | `#contacts-panel` OOB |

`_contacts_panel` (`relationship.py:126-128`) and `_interactions_panel` (`:243-250`)
already take `error=`, and both templates render it (`_contacts_panel.html:42`,
`_interactions_panel.html:29`). **No template work for 1–5.**

**Reachable today with no revert involved, both halves:** remove a contact in one tab and
click its email cell in another (routes 3–5); `interaction_delete` (`relationship.py:343`)
is a live soft-delete writer on this surface, so routes 1–2 are equally reachable.

**`work.py` — 13 routes. Three panels need an `error` slot.**

| # | Route | Guard | Trigger target | Answer |
| --- | --- | --- | --- | --- |
| 6 | `GET  /accounts/{ref}/tasks/{id}/cell/{key}` | `:168` | the cell | `#tasks-panel` OOB |
| 7 | `GET  …/cell/{key}/edit` | `:175` | the cell | `#tasks-panel` OOB |
| 8 | `POST …/cell/{key}` | `:185` | the cell | `#tasks-panel` OOB |
| 9 | `POST /accounts/{ref}/tasks/{id}/done` | `:214` | `#tasks-panel` (`_tasks_panel.html:47-48`) | `#tasks-panel` OOB |
| 10 | `GET  /accounts/{ref}/requests/{id}/edit` | `:270` | `#requests-panel .form-host` (`_requests_panel.html:48-50`) | `#requests-panel` OOB |
| 11 | `POST /accounts/{ref}/requests/{id}/edit` | `:280` | `closest .form-host` | `#requests-panel` OOB |
| 12 | `GET  …/requests/{id}/items/new` | `:371` | `#items-panel .form-host` (`_items_panel.html:21-23`) | **container → redirect** |
| 13 | `POST …/requests/{id}/items/new` | `:381` | `closest .form-host` | **container → redirect** |
| 14 | `GET  …/items/{item_id}/cell/{key}` | `:430` | the cell | item gone → `#items-panel` OOB; request gone → redirect |
| 15 | `GET  …/items/{item_id}/cell/{key}/edit` | `:442` | the cell | as 14 |
| 16 | `POST …/items/{item_id}/cell/{key}` | `:459` | the cell | as 14 |
| 17 | `POST …/items/{item_id}/received` | `:491` | `#items-panel` (`_items_panel.html:55-57`) | as 14 |
| 18 | `GET  /accounts/{ref}/requests/{request_id}` | `:504` | browser navigation (`_requests_panel.html:46-47`) | **container → 303 redirect** |

`_tasks_panel` (`work.py:109`), `_requests_panel` (`:241`) and `_items_panel` (`:363`) take
no `error` and their templates render none — three signatures and three templates to
extend, using `_contacts_panel.html:42` verbatim. `.form-error` is already styled
(`web/static/app.css:1260`); no CSS.

Rows 14–17 branch because `_owned_item` checks the request first and the item second
(`account.py:193-194`): a gone item leaves `#items-panel` renderable, a gone request does
not (`work.py:352`). The opening ruling's "six routes cannot be answered with a panel" is
categorical only for 12–13; for 14–17 it is conditional, and the guard's ordering is what
makes the branch decidable.

**How rows 6–18 go stale today:** no surface deletes a task, request or item (D4), so the
live path is reverting the batch that created one — `services/batches.py:374-378`
soft-deletes on reverting a create. That is reachable from the web's own rail
(`page.html:159-161`), in the next tab.

**Unchanged:** the four `_owns_raw_row` routes; `account_tab` / `account_root`
(`account.py:582`, `:587`) name no row.

---

## Tests, and the mutation that must break each one

Every test below is run against a deliberately broken production line and observed failing
before it is believed. A green suite proves nothing broke, not that the new path is taken.

### Entry 1 — `tests/test_web_scoping.py`

- **`test_another_accounts_batch_ref_answers_exactly_like_an_unknown_one`** — status,
  `HX-Redirect` outcome token and the landing page's sentence are equal for an unknown ref
  and a foreign ref; and the foreign batch's `reverted_at` stays `None`.
  - *Mutation A:* restore `raise HTTPException(404, …)` at `changes.py:103-107` → the two
    answers differ → fails.
  - *Mutation B:* delete the `batch.org_id != org.id` check outright → the foreign batch is
    reverted → the `reverted_at` assertion fails. **B is the one that matters**: it proves
    the test guards authorization, not merely shape.

### Entry 1 — `tests/test_web_writes.py`

- **Rewrite `test_a_batch_from_another_account_is_not_revertible` (`:294-319`)**: `:311`'s
  404 becomes 204 + `outcome=gone`. **Keep** both existing invariants — the title unchanged
  (`:312`), `reverted_at is None` (`:313`) — and keep the control at `:318-319` (the same
  batch reverts under its own account), without which the test passes against a route that
  does not exist.
- **`test_gone_renders_for_a_batch_that_belongs_to_another_account`** — pins
  `changes.py:221-222`.
  - *Mutation:* delete those two lines → `toast_for` returns `None` for a foreign ref → no
    sentence on the landing page → fails. (Verified currently unexercised: the loop at
    `:741-743` covers `reverted/already/refused/program` only.)
- **One assertion that the rail cannot contradict the toast**: the foreign ref does not
  appear in the rendered page.
  - *Mutation:* drop the `b.org_id == org.id` filter at `account.py:545-547` → the ref
    appears in the Revert button's URL (`page.html:160`) beside a toast saying it does not
    exist → fails.

### Entry 2 — new `tests/test_web_staleness.py`

**`test_every_row_route_answers_a_stale_click_in_the_page`** — the table of 18, each driven
against a row of *this* account that has been soft-deleted.

Panel family (15 route-cases), copying `tests/test_contact_remove.py:457-470`:

- `status == 200`
- the sentence in the body
- `body.strip().startswith('<div id="{panel}"')`
- `"hx-swap-oob" in body`
- `body.index('class="form-error"') > body.index('id="{panel}"')`
- the stale row's own action URL absent from the refreshed panel
- for the POST routes, the raw-row fingerprint unchanged

Container family (7 route-cases): `status == 204` and
`HX-Redirect == "/accounts/{ref}/work?gone=request&id={request_id}"` for 12–17; for 18,
`status == 303` and the same `location` — **with `follow_redirects=False`**, the house
pattern at `tests/test_web_account.py:92` and `tests/test_web_book.py:38`. Without it httpx
follows to the work tab and a 200 reads as success even if the guard never fired.

Mutations:

- *Per family (run four):* replace one route's `stale=` lambda with one that raises
  `_not_here` — `contact_cell` (`relationship.py:488`), `interaction_update` (`:302`),
  `task_done` (`work.py:214`), `item_received` (`work.py:491`) → that row 404s → the table
  names it. Dropping `stale=` entirely fails at mypy instead, which is D3's point.
- *Shape:* change the sweep's panel call from `oob=True` to `oob=False` → **the
  `"hx-swap-oob" in body` assertion fails.** It is *not* caught by `startswith`:
  `_contacts_panel.html:35` renders `<div id="contacts-panel"{% if oob %} …{% endif %}>`,
  so `body.startswith('<div id="contacts-panel"')` (no closing angle bracket, as at
  `tests/test_contact_remove.py:462`) passes either way. The existing test carries both
  assertions for exactly this reason; **do not drop `:466` as redundant.**
- *Shape:* render the sentence outside the OOB element → the index assertion fails.
- *Transport:* make `request_detail` return 204 + `HX-Redirect` → the 303/`location`
  assertion fails, and the browser stays put on a body-less 204.
- *Owner rule:* delete the placement branch from `_raw_owner_org_ids` → a soft-deleted
  placement-owned task (`tests/test_web_scoping.py:74-76`) answers 404 instead of the panel
  → fails.

**`test_no_row_route_is_missing_from_the_staleness_table`** — the D7 walk.

- *Mutation:* delete one entry from `STALENESS` → fails naming that route.
- *Mutation:* narrow the selector to `"/cell/" in path` → `len(selected) == 24` fails.
  This is the assertion that stops the test passing by selecting nothing.

**`test_the_two_owner_rules_agree`** — for one live row of each of the five kinds,
`_owner_org_ids(entity) == _raw_owner_org_ids(conn, kind, raw_row)`.

- *Mutation:* drop the placement branch from either → the placement-owned task disagrees →
  fails. This is the anti-drift guard for keeping two rules at all.

**`test_the_gone_toast_only_renders_when_the_claim_is_true`**

- `?gone=request&id=<a LIVE request of mine>` → no toast.
  *Mutation:* drop the `deleted_at is None` check in `_gone_toast` → the toast appears
  beside the live request in the panel → fails. (Same class as
  `test_gone_is_not_rendered_about_a_batch_that_is_still_here`,
  `tests/test_web_writes.py:671`.)
- `?gone=request&id=<another account's deleted request>` → no toast.
  *Mutation:* drop the owner check → a crafted link confirms a request exists on another
  account → fails.

### Entry 2 — `tests/test_web_scoping.py`, extended

- **Widen `test_an_unknown_id_and_someone_elses_id_answer_the_same` (`:218-225`)** to four
  cases: unknown → 404, foreign-alive → 404, **foreign-deleted → 404**, own-deleted → 200
  with a sentence. `follow_redirects=False`.
  - *Mutation:* have the guard check `deleted_at` before ownership (swap table rows 2 and
    4) → the foreign-deleted case answers 200 → fails. **This is the test that keeps the
    oracle shut on the entry-2 side**, and it is the only one that does: the existing
    cross-account loop uses LIVE foreign rows, so it would not catch this.
- **`test_no_route_reaches_another_accounts_row` (`:178-202`) and
  `test_an_item_whose_request_was_removed_404s_instead_of_500ing` (`:291-318`) must stay
  green unedited.** Both were traced against the new guard and both hold:
  - the cross-account loop drives live foreign rows, which take table row 2 → 404,
    unchanged;
  - the orphan test names a **live** request and an item whose own request is dead
    (`:302-306`). `_owned(request=live)` passes, `rfi_repo.get_item` succeeds (the item
    itself is alive), and `_owner_org_ids`' alive-filtered item branch still returns
    `set()` → 404. The raw path is never reached.
  - If either needs editing, the guard has widened past its brief — stop and re-read D3.
- **While editing:** `:179`'s docstring says "One assertion for eighteen handlers" in a test
  that drives 22 calls. Same drift class this brief is correcting, in the file it extends.

### Gate and browser pass

`uv run --no-sync python -m pytest -q`, `uv run mypy src`, `uv run ruff check src tests` —
redirected to the scratchpad, gating on the command and never on a pipe. mypy is doing real
work: it is what proves all eighteen call sites were **swept**. It does not prove they were
swept **correctly** — eighteen sites passing the same wrong panel type-check clean. That is
what D7's per-route table is for; mypy and the table are two guarantees, not one.

**Playwright, two tabs on one account.** Markup-reading missed real bugs on this surface
twice. Cover:

1. Remove a contact in tab A; in tab B click that card's email cell → the panel refreshes
   with the sentence and without the card.
2. Same, but **with the editor already open** in tab B when the refusal lands. This is the
   case markup cannot answer: `inline-cell.js` holds live state across the request —
   `committing` reset on `htmx:afterRequest` (`:68-77`), `pendingHop` set by Tab
   (`:90-106`), and the `htmx:afterSwap` handler that focuses any newly-swapped
   `.cell-editing` (`:117-130`). Removing the focused input fires `focusout` (`:82-88`),
   which with `committing` false issues a second GET to the same stale route — one extra
   panel replace. Confirm it settles rather than loops.
3. Revert the batch that created a task, then click that task's Done button in the other
   tab → panel refresh with the sentence (route 9, the early-return case).
4. Revert the batch that created a request, then click its "Items" link in the other tab →
   lands on the work tab with the toast (route 18, the 303).

---

## Roadmap edits when this lands

Both entries collapse into one "SHIPPED" pointer in the style of `ROADMAP.md:56`. While
editing, fix the counts so `ROADMAP.md`, `account.py:113-117` and
`tests/test_web_scoping.py:179` agree: **22 routes name a ref and a row, 18 go through
`_owned`/`_owned_item`, 9 of those are cell routes.** `ROADMAP.md:238`'s "fourteen" matches
nothing in the code and does not follow from its own arithmetic (thirteen plus two is
fifteen); `:275`/`:293`, the line numbers it gives for the stale-Edit 404, are
`context["oob"] = False` and the second line of `interaction_update`'s docstring
respectively — the real guards are `relationship.py:283` and `:302`.

---

## Worktree and sequencing

`.claude/worktrees/web-refusal`, its own worktree per CLAUDE.md. `uv sync --group dev`
first, and do not delete `.claude/worktrees/towerkit`.

Accurate as of `41d5ea1`: four worktrees are checked out (`web-account` on `web-snapshot`,
`web-batch-join`, `web-defects`, `web-work` on `internal-tasks-export`) and **none has a
commit ahead of `main`**. The rebase risk is uncommitted work in those trees, not divergent
branches — check with their owners before the eighteen-site edit, which is the worst
possible thing to merge blind.

---

## Open, on purpose

- **The copy for task / request / item.** `f"{label} is no longer on {org.name}"` is the
  default this brief builds; it is one function to change. Grant's call — see
  `blocked_on_grant`.
- **Whether entry 1 should ship ahead of the sweep.** Ruled "bundle" above with the
  counter-argument stated; a one-line yes/no from Grant, not a blocker to starting.
- Flipping `contact_remove`'s `oob=False` for surface-wide uniformity — deliberately out of
  scope (D2).
- `ROADMAP.md:76` "Deactivating a record, generally" — when it ships a real delete writer
  for tasks, requests and items, D4's sentence moves to the service and this helper
  delegates.



---

## Verification report — round 2 (independent adversarial pass)

**Verdict: needs-revision.** The two headline reversals are real and check out against the code, and both are improvements over round 1. The htmx correction is right: `Me` is `function Me(t,e,n){…const i=c(t);if(!i){return}a(i,t,e,n);…if(t instanceof Element){t.remove()}}` and `c` is `parentElement`, so `t.remove()` detaches only the panel node — descendants keep a non-null DETACHED parent, and the early return genuinely fires only where the primary target IS the OOB-replaced node (routes 9 and 17, whose triggers are `hx-target="#tasks-panel"/"#items-panel"` with `outerHTML` at _tasks_panel.html:46-48 and _items_panel.html:55-57). `ze(n,l,i)` does precede `_e(r.swapStyle,…)` in `$e`; responseHandling at version 2.0.4 is verbatim as quoted; HX-Redirect is handled in `Dn` before `Pn(s)`. The D7 count reversal is exactly right — I enumerated the routers directly: 35 routes total, 24 matching `{ref}` plus one further param, splitting 18/4/2 precisely as the STALENESS/RAW_ROW/NOT_A_ROW maps claim. The eighteen guard call sites, the nine cell routes, the 22 rows of `_cross_account_calls`, the unexercised `changes.py:221-222`, the missing `htmx:responseError` listener, the three panels lacking an `error` slot, `.form-error` already styled at app.css:1260, `_items_context`'s alive-filtered read at work.py:352, `delete_request` not cascading, and the zero production callers of the three delete functions all verify. Every named mutation I traced can fire, including the two the brief reversed on (`"hx-swap-oob" in body` at test_contact_remove.py:466 does catch the oob flip that `startswith` at :462 misses, because _contacts_panel.html:35 emits `<div id="contacts-panel"` with no closing bracket before the conditional attribute) and the placement-branch mutation against the org_id-NULL task at test_web_scoping.py:74-76. What sinks it is a false factual claim with a decision hanging off it — `internal-tasks-export` is two commits ahead of `main` and edits `work.py` above line 165, invalidating all thirteen work.py anchors — plus one "traced, stays green" guarantee whose trace contradicts the decision it is defending, and a decision table whose row 3 leaves the guard's actual control flow unspecified for an eighteen-site edit. Fix those three and the sequencing paragraph; the rest is buildable.


### Decisions round 2 reversed from round 1

- **Was:** The panel-vs-cell fork is 'decided by the DOM' because htmx's outerHTML swap on a detached target is a verified silent no-op: Me(t,e,n) does `const i=c(t); if(!i){return}` and c returns parentElement.
  
  **Now:** The early return fires ONLY when the primary target IS the element the OOB swap replaced — routes 9 and 17. For the other sixteen the OOB swap detaches the old panel wholesale (He -> Me -> t.remove()), so a cell or .form-host still has a non-null DETACHED parent and Me runs the swap inside the orphan. The outcome (nothing visible) holds; the mechanism is detachment, which is what _interactions_panel.html:14-16 and relationship.py:243-250 already say correctly.
  
  **Why:** Read the shipped minified source directly: `function Me(t,e,n){...const i=c(t);if(!i){return}a(i,t,e,n);...if(t instanceof Element){t.remove()}}`, `function c(e){const t=e.parentElement;...}`, `function He(...){...if(r.shouldSwap){qe(t);_e(s,e,e,i);...}}`, and ordering in `$e`: ze(n,l,i) then _e(r.swapStyle,...). t.remove() detaches the panel node; its descendants keep their parents inside that detached subtree.

- **Was:** request_detail (work.py:504) is excluded from the sweep — three different ways: 'excluded — navigation' in the sweep table, 'keeps its 404, and says so out loud' in D5, and present in D6's EXCLUDED map, with the open decisions recommending the opposite.
  
  **Now:** request_detail is IN the sweep and redirects (303 + Location, since it is a browser navigation rather than an htmx swap). The sweep is eighteen routes; the EXCLUDED map contains no row route at all.
  
  **Why:** The redirect machinery must exist anyway for routes 12-17, so request_detail costs nothing extra. Decisively: only a row that existed and is now soft-deleted redirects — unknown ids (table row 1) and foreign ids (table row 2) still 404, so the 'a 303 hides a bad URL' objection does not apply to any case that changes. It also makes the sweep set exactly the set of _owned/_owned_item call sites, which is exactly what mypy proves.

- **Was:** The refusal shape per route is implicitly whatever the route's family suggests; D2 named 'the panel, out of band' but the sweep table left the OOB-ness of routes 9 and 17 (which target the panel itself) unstated.
  
  **Now:** oob=True on ALL fifteen panel-family routes, with no per-route judgement. oob=False is correct only for the two routes whose trigger targets the panel itself; oob=True is correct for all fifteen.
  
  **Why:** Derived from the corrected htmx mechanism. Where the trigger targets something inside the panel, oob=False nests a second panel (the trap _contacts_panel.html:18-34 documents). Where the trigger targets the panel itself (_tasks_panel.html:47-48, _items_panel.html:55-57), oob=True still works because that is the one case Me's null-parent early return genuinely fires. One shape correct everywhere beats two shapes chosen per route — which is how the behaviour drifted in the first place.

- **Was:** The staleness question is asked through `Model.from_row` + `_owner_org_ids`, and `_owns_raw_row` delegates to that same path.
  
  **Now:** A new `_raw_owner_org_ids(conn, kind, row)` beside `_owner_org_ids`, plus a `_RAW_KIND` map from web kind to ENTITY_TABLES key. `_owns_raw_row` delegates to the new function. `_owner_org_ids` is NOT reused.
  
  **Why:** `_owner_org_ids`' item branch calls the alive-filtered `rfi_repo.get_request` (account.py:143-152) and returns set() on a dead request — i.e. 404 — for precisely the container case this brief exists to answer. Reusing it defeats the design. Also, account.py has no kind->model map: `kind` is a bare str and `_Owned` is a TypeVar bound by the caller's fetch. And ENTITY_TABLES keys are 'rfi_request'/'rfi_item', not the web's 'request'/'item', so a mapping is required either way.

- **Was:** `_owned` grows a required keyword parameter 'naming what to answer with', and 'the decision table, once, in the guard' renders the answer.
  
  **Now:** The guard RAISES a `_Stale` exception carrying an already-rendered Response, produced by a caller-supplied `stale: Callable[[str], Response]` closure; one app-level exception handler registered in create_app returns it. `_owned_item` takes TWO such callables (stale_request, stale_item).
  
  **Why:** account.py is imported by relationship.py:57 and work.py:53, and already dodges the cycle by importing changes.toast_for inside a function (account.py:562-565). A guard in account.py cannot reference _contacts_panel/_tasks_panel/_items_panel at module level, so it literally cannot render. A closure supplied by the caller adds no import edge in either direction.

- **Was:** D6's route walk selects every path containing {ref} plus one further {param}, with an EXCLUDED map naming request_detail only.
  
  **Now:** Three named maps that must together equal the selected set — STALENESS (18), RAW_ROW (4), NOT_A_ROW (2) — plus an explicit `len(selected) == 24` assertion.
  
  **Why:** The original selector matches 24 routes, so as specified the test is red on day one and the obvious fix is padding EXCLUDED until CI goes quiet. Counted the routes by hand against every @router decorator in account.py, changes.py, relationship.py and work.py: /accounts/{ref}/{tab}, the revert POST, the four _owns_raw_row routes and request_detail are the seven beyond the seventeen the draft listed.

- **Was:** The container family's redirect token 'ships through _context's existing toast slot as toast_for(...) or gone_toast_for(...)', with the token vocabulary left as an open decision.
  
  **Now:** A distinct query key `?gone=request&id=<id>` (never `outcome=gone`), a frozenset of exactly one kind, and `_gone_toast` in account.py that verifies the raw row exists, is this account's, and is soft-deleted before rendering. The id is a lookup key and is never printed.
  
  **Why:** toast_for's gone branch resolves `undo` as a BATCH ref (changes.py:218-222); a request id fails to resolve and it renders 'that change no longer exists' — a true-shaped sentence about the wrong thing. And fixed prose with no id would let a crafted ?gone=request print a removal sentence on a page whose requests panel lists everything, which is the contradiction review round 2 D fixed once already.

- **Was:** 'Mutation, shape: return the panel not out of band -> the startswith assertion fails.'
  
  **Now:** That mutation is caught by `"hx-swap-oob" in body` (tests/test_contact_remove.py:466), not by startswith (:462). The brief now says so explicitly and instructs the builder not to drop :466 as redundant.
  
  **Why:** _contacts_panel.html:35 renders `<div id="contacts-panel"{% if oob %} hx-swap-oob="true"{% endif %}>`, and the prefix in the assertion has no closing angle bracket, so it matches either rendering. A per-route table built from the draft's reasoning would have dropped the assertion that actually guards the shape.


### Regressions the revision introduced

*This is the list that stopped the iteration: a fix reproducing its own defect class one level down.*

- The sequencing section closes round 1's silence with a confident, checkable falsehood and reasons from it. "Accurate as of 41d5ea1: … none has a commit ahead of main. The rebase risk is uncommitted work in those trees, not divergent branches." `internal-tasks-export` (checked out in .claude/worktrees/web-work) is 2 commits ahead and modifies src/bookkit/web/routes/work.py, inserting ~14 lines above line 165 — which moves every work.py line number the brief's own route table and citation 11 depend on. A section added to de-risk the merge now understates the risk and mis-anchors the deliverable.

- D2 reverses to "ONE shape … no per-route judgement" and then makes a per-route exception in the next paragraph (contact_remove stays oob=False, relationship.py:459-460), with D7's RAW_ROW map recording it as "already correct" and no assertion in the new test file covering its shape. The defect class the reversal exists to close — a shape decided per route, with nothing that goes red when the next one is decided wrong — survives, moved one route outside the sweep.

- D3 reverses away from `_owner_org_ids` on the grounds that its alive-filtered item branch "returns set() — i.e. 404 — for precisely the container case this brief exists to answer," and then justifies leaving tests/test_web_scoping.py:291-318 untouched by asserting that the same `_owner_org_ids` branch "still returns set() → 404" and "the raw path is never reached." The dependent claim is still keyed to the rejected premise. The test does stay green, but via `_owned_item`'s `item.request_id != request_id` check at account.py:195-196, which the trace never mentions — a footnote quietly re-pointed at the rule the decision above it just discarded.

- Two of the three citations behind D2's rejection of an `htmx:responseError` listener — the argument that the app has correct 404s a global banner would corrupt — do not resolve (test_web_writes.py:379 is a 204 assertion, test_web_work.py:310 is a section comment). The correct tests exist at :426 and :344, so the rejection stands; but this is the same shape as round 1's problem: a decision reaffirmed with references that were not re-opened, in a document whose own standard is that a re-checked citation is what separates it from the original.


### Citations that still did not check out

- **`src/bookkit/web/routes/relationship.py:429, :461-463 (citation 24, and D2's "Rejected — flipping contact_remove's existing oob=False")`** — claimed: contact_remove_confirm refuses with oob=True at :429; contact_remove refuses with oob=False at :461-463.
  
  *Actually:* :429 is `return TEMPLATES.TemplateResponse(` — the SUCCESS path of the confirm GET. The oob=True refusal is at :426 (`return _contacts_panel(request, org, oob=True, error=gone)`). :461 is `return _contacts_panel(request, org)` — the SUCCESS path of the POST; :462-463 are blank/`def`. The oob=False refusal is at :459-460 (`except ValueError as exc: return _contacts_panel(request, org, error=str(exc))`). Both cited ranges point at success paths, not refusals. The underlying claim (the precedent splits by trigger) is correct — confirmed at :426, :459-460, and _contact_confirm_remove.html:36 (`hx-target="#contacts-panel" hx-swap="outerHTML"`).

- **`tests/test_web_writes.py:379 (D2, "Rejected — an htmx:responseError listener": "a crafted `tab` (tests/test_web_writes.py:379)")`** — claimed: A correct 404 for a crafted `tab` is pinned at test_web_writes.py:379.
  
  *Actually:* :379 is `again = _revert(client, org.ref, batch.ref)` inside a test asserting a 204 + `outcome=already` — not a 404 and not about `tab`. The crafted-tab test is `test_a_crafted_tab_is_404_and_never_reaches_the_redirect` at :426, asserting 404 at :446. The claim survives at the corrected line.

- **`tests/test_web_work.py:310 (D2, same rejection: "a non-editable cell key (…, tests/test_web_work.py:141, :310)")`** — claimed: A correct 404 for a non-editable item cell key is pinned at test_web_work.py:310.
  
  *Actually:* :310 is the section-divider comment `# --- items: detail page, inline editing, mark received ---`. The test is `test_a_non_editable_item_key_is_404_not_a_write` at :344. (test_web_writes.py:154 and test_web_work.py:141 both resolve correctly.)

- **`src/bookkit/web/routes/account.py:143-152 (citation 26)`** — claimed: _owner_org_ids' RfiItem branch calls the alive-filtered rfi_repo.get_request and returns set() on KeyError at :143-152.
  
  *Actually:* :143 is `return {entity.org_id}` — the Contact/RfiRequest/Interaction branch. The RfiItem branch is :144-154: `if isinstance(entity, RfiItem):` at :144, `rfi_repo.get_request` at :146, `return set()` at :154 (outside the cited range). Claim holds; range is wrong at both ends.

- **`src/bookkit/web/templates/macros/cell.html:47-50 (citation 19, and sweep-table rows 3, 6, 14)`** — claimed: Rows 3/6/14 — `GET /…/cell/{key}` — are triggered from the display cell at cell.html:47-50.
  
  *Actually:* cell.html:47-50 is the DISPLAY cell, whose `hx-get="{{ action }}/edit"` triggers `/cell/{key}/edit` — i.e. rows 4/7/15, not 3/6/14. `/cell/{key}` (the display route) is triggered from the EDITOR element's Escape at cell.html:69-71 and from inline-cell.js:87 (`htmx.ajax("GET", action, { target: cell, swap: "outerHTML" })`). Both are still outerHTML on a node inside the panel, so the conclusion is unaffected.

- **`src/bookkit/web/static/inline-cell.js:55, :68, :82, :90, :108, :117 (citation 18)`** — claimed: inline-cell.js registers submit, htmx:afterRequest, focusout, two keydown and htmx:afterSwap listeners — and none for htmx:responseError.
  
  *Actually:* All six cited lines resolve exactly. But the file registers THREE more delegated listeners the inventory omits: `click` at :156 (form cancel), `click` at :168 (toast dismiss), and a third `keydown` at :187 (Escape closes .entity-form). The load-bearing half — no htmx:responseError listener — is confirmed by grep.

- **`src/bookkit/services/contacts.py:137-155 (citation 37)`** — claimed: contacts_svc.already_removed lives at services/contacts.py:137-155.
  
  *Actually:* `def already_removed` is at :135; the function ends at :153 (`return f"{name} is already removed from {org.name}"`). :155 is `def remove(...)`. Off by two at both ends. (services/interactions.py:81-98 for already_deleted resolves exactly.)

- **`src/bookkit/web/routes/changes.py:103-107 (citation 1, the "What the code actually says" section, and D1's opening sentence)`** — claimed: changes.py:103-107 answers another account's batch_ref with HTTPException(404), and is what "stops raising" under D1.
  
  *Actually:* :103 is the `if batch.org_id != org.id:` test; :104-108 are comment; the `raise HTTPException(status_code=404, …)` is :109-111. A builder editing exactly :103-107 would delete the guard's condition and its comment and leave the raise. Claim holds; the range excludes the statement it names.

- **`tests/test_web_scoping.py:68-76 (citation 28)`** — claimed: The fixture creates `via_placement` with org_id NULL at :68-76.
  
  *Actually:* :67-70 is `placements_repo.create(...)`; :71-73 is the explanatory comment; `via_placement = tasks_repo.create(conn, f"bind {name}", placement_id=placement.id, …)` is :74-76. Minor range slip; the row and its NULL org_id are real and the claim holds.


### Claims challenged

- **[CRITICAL]** "Accurate as of 41d5ea1: four worktrees are checked out … and none has a commit ahead of main. The rebase risk is uncommitted work in those trees, not divergent branches."
  
  *Evidence:* False, and checkably so. `git worktree list` at 41d5ea1: `.claude/worktrees/web-work` is on `internal-tasks-export` at 69c0914. `git rev-list --count main..internal-tasks-export` = **2** (69c0914 "feat: both surfaces say a task is not exported…", 79434d2 "feat: the Internal category never leaves the building"). Worse, `git diff --stat main...internal-tasks-export` shows it touches `src/bookkit/web/routes/work.py` (+20/-4) and `tests/test_web_work.py` (+52) — the exact file the eighteen-route sweep rewrites. The insertions land ABOVE line 165: a new `_task_category_suffix` at :86-95 and a suffix-dispatch dict at :155-159. That shifts EVERY one of the thirteen work.py guard line numbers the brief anchors to (citation 11 and the whole sweep table: 168, 175, 185, 214, 270, 280, 371, 381, 430, 442, 459, 491, 504) by roughly +14. The brief's conclusion — that the only merge risk is uncommitted work — is the opposite of the truth, on "the worst possible thing to merge blind".

- **[IMPORTANT]** Citation 39 / "Entry 2 — tests/test_web_scoping.py, extended": "test_an_item_whose_request_was_removed_404s_instead_of_500ing … addresses an orphan item under a LIVE request of the same account (:302-306), so the raw path is never reached and the test stays green under the new guard" — "`_owned(request=live)` passes, `rfi_repo.get_item` succeeds …, and `_owner_org_ids`' alive-filtered item branch still returns `set()` → 404."
  
  *Evidence:* The test does stay green, but not for the reason given, and the reason given contradicts D3. The brief's own D3 says the staleness question is "asked of the RAW row" and that `_raw_owner_org_ids` walks `base_repo.raw_row(conn, "rfi_request", row["request_id"])` — which, for this orphan, resolves to the raw (soft-deleted) orphan_request whose `org_id` IS the test account's. So on the raw rule the item's owner is THIS account, and table row 3 applies, not 404. The 404 the test asserts comes from a third check the trace never mentions: `_owned_item`'s `if item.request_id != request_id: raise _not_here("item", …)` at account.py:195-196 (the URL names `live.id`, the item's request is `orphan_request.id`). The brief then instructs "If either needs editing, the guard has widened past its brief — stop and re-read D3" — a builder following this trace will re-read D3 and find it says the opposite of what the trace assumes.

- **[IMPORTANT]** D3's decision table is the guard. Row 3: "present | this account | NULL → the entity, as today."
  
  *Evidence:* Row 3 is doing all the load-bearing work and is unspecified. Two readings of the table produce different behaviour on a real row. (a) The table REPLACES the ownership check: then `_owner_org_ids` is dead and the orphan-item case (an alive item whose request is soft-deleted and mine, addressed under a live request of mine) reaches `_owned_item`'s :195-196 rather than the alive rule. (b) The table is a PRE-check and row 3 falls through to today's `_owned` body at account.py:177-183: then `_owner_org_ids` still runs and still returns `set()`. D3's own `test_the_two_owner_rules_agree` implies (b) — but the table, read literally, says (a). Nothing in the brief pins which. This is the one thing an eighteen-site edit cannot leave ambiguous.

- **[IMPORTANT]** D2: "One shape correct everywhere beats two shapes chosen per route — which is how the behaviour drifted in the first place."
  
  *Evidence:* The only evidence in the repo of two shapes chosen per route is the four RAW_ROW routes, and they did NOT drift — every one is correct for its trigger. contact_remove_confirm oob=True (relationship.py:426) against `hx-target="next .form-host"` (_contacts_panel.html:83-85); contact_remove oob=False (:459-460) against `hx-target="#contacts-panel" hx-swap="outerHTML"` (_contact_confirm_remove.html:36); interaction_delete_confirm oob=True (:330) against `next .form-host` (_interactions_panel.html:67-69); interaction_delete oob=False (:361) against `#interactions-panel` outerHTML (_interaction_confirm_delete.html:35). The drift ROADMAP.md:238 actually records is between the destructive half (handled) and the read/edit half (silent 404) — nothing to do with oob shape. D2's conclusion (oob=True everywhere) is still defensible on "correct for all fifteen, one fewer decision per route"; its stated reason is not supported by the code.

- **[IMPORTANT]** D2: "`oob=True` on every one of the fifteen panel-family routes — no per-route judgement" … and then "Rejected — flipping contact_remove's existing oob=False … not in this branch."
  
  *Evidence:* The branch ships an invariant and an immediate exception to it, and D7 records the exception as settled: RAW_ROW is annotated "already correct via _owns_raw_row + the services' sentences", with no assertion anywhere in the new test file about its shape. So after this branch, sixteen panel refusals exist in two shapes, the completeness guard classifies four of them as out of scope, and nothing goes red if a future edit picks the wrong one there. That is the same defect class the reversal was made to close, moved one route to the left. Either flip it (two lines, and tests/test_contact_remove.py:466 asserts `"hx-swap-oob" in body` which would still pass) or drop the "no per-route judgement" framing.

- **[MINOR]** "Entry 1 — tests/test_web_writes.py … One assertion that the rail cannot contradict the toast: the foreign ref does not appear in the rendered page. Mutation: drop the b.org_id == org.id filter at account.py:545-547 → the ref appears in the Revert button's URL (page.html:160) beside a toast saying it does not exist → fails."
  
  *Evidence:* That assertion already exists: tests/test_web_writes.py:749 (`assert batch.ref not in page.text`) inside test_toast_for_refuses_a_batch_from_another_account, driven against exactly the foreign batch the mutation would surface. The named mutation therefore turns an EXISTING test red today, before this branch. The brief presents it as new coverage the branch adds. (The citations behind it all resolve: account.py:545-547 is the filter, page.html:160 prints `c.ref` in the hx-post URL.)

- **[MINOR]** The sweep gives "one rule for one question" across all eighteen; routes 14-17's answers are "item gone → #items-panel OOB; request gone → redirect."
  
  *Evidence:* Routes 14-17 retain a third answer the brief never names: `_owned_item`'s `if item.request_id != request_id: raise _not_here("item", item_id, org)` at account.py:195-196 — a bare 404 with no swap, on the same routes. It is defensible (that URL pairing was never rendered by the page, same doctrine as `_not_here`), and tests/test_web_scoping.py:256-272 pins it. But the brief's D7 table asserts the container family answers 204/303 and the panel family 200, and its "the sweep set is exactly the guard's call sites, which is exactly what mypy proves" invariant implies every path through those four routes is covered. Say so, or the next reader will read the table as exhaustive.

- **[MINOR]** "Roadmap edits when this lands … fix the counts so ROADMAP.md, account.py:113-117 and tests/test_web_scoping.py:179 agree."
  
  *Evidence:* The list is one short. changes.py:106 says "a batch is not one of the **four** entities that guard resolves", while `_Owned` (account.py:120) is bound over five (Contact, Task, RfiRequest, RfiItem, Interaction) and D7's own NOT_A_ROW comment writes "five". D1 edits that exact block (:103-111) — fix the count in the same touch. Everything else in this paragraph verifies: ROADMAP.md:242 does cite relationship.py:275 (`context["oob"] = False`) and :293 (interaction_update docstring line 2); ROADMAP.md:252/:262 do say "fourteen" against a stated thirteen-plus-two; test_web_scoping.py:179 does say "eighteen handlers" in a test driving 22 calls; test_web_concurrency.py owns exactly three tests (:53, :123, :190) and no stale-click test.

- **[MINOR]** Playwright case 2: "Removing the focused input fires focusout (:82-88), which with committing false issues a second GET to the same stale route — one extra panel replace."
  
  *Evidence:* Unconfirmed, and probably wrong. The focusout handler is delegated on `document.body` (inline-cell.js:82). Under the mechanism the brief itself corrects, `He`→`Me`→`t.remove()` detaches the panel FIRST, so the focused input is already outside the document when the primary swap removes it; a focus event dispatched on a detached node cannot bubble to document.body. The brief hedges it ("Confirm it settles rather than loops"), which is right — but it is written as a stated fact in a section headed by an assertion that markup-reading missed real bugs twice. State it as the hypothesis the browser pass tests.

- **[MINOR]** D6: "a distinct query key `?gone=request&id=<id>`."
  
  *Evidence:* `id` is the most generic possible key on a page whose query string already carries `undo`, `outcome`, `n` and `type` (relationship's filter pills, _interactions_panel.html:34-39). No collision exists today, and `_gone_toast` validates before printing so a collision is not a security issue — but `gone_id` costs nothing and keeps the token family self-describing, the same way `undo`/`outcome`/`n` do.


### Needs Grant

- COPY, for the three kinds with no service sentence (task, request, item). Options: (a) `f"{label} is no longer on {org.name}"` — mirrors already_removed/already_deleted's shape without claiming a deletion vocabulary those kinds do not have, since nothing 'removes' a task on any surface today and a revert is what makes one disappear (services/batches.py:374-378); (b) `f"{label} was removed from {org.name}"` — parallel wording, but it names an action no surface performs; (c) `f"{label} is no longer on {org.name} — it was reverted"` — accurate today, wrong the moment ROADMAP.md:76 ships a real delete writer. RECOMMENDATION: (a). The branch builds (a) as the default; it is one function to change (D4). Flagged because the last unanswered copy question on this surface — the timeline empty state — shipped on a guess and is recorded as still open in handoffs/20260818-WebUI.md.

- SEQUENCING, a scope-vs-time call. Entry 1 (the revert oracle) is a four-line route change plus tests, ~30 minutes; entry 2 is an eighteen-route sweep. Options: (a) one branch, two commits, entry 1 first — my ruling, because the sweep establishes 'a refusal says something, in the smallest surviving container' and shipping that while the revert control still answers a miss two different ways ships a contract that contradicts itself on day one; (b) ship entry 1 alone now and schedule the sweep — gets the oracle closed today, but the container family (routes 12-17, 18) then has no redirect idiom to reuse and would either invent a second toast vocabulary or stay silent. RECOMMENDATION: (a). Not a blocker to starting entry 1 either way, since it is the first commit in both plans — but if Grant wants (b), D6's `gone` token needs rethinking as a standalone piece.


### Deliberately not settled

- Whether contact_remove's existing `oob=False` refusal (relationship.py:461-463) should flip to `oob=True` so the whole surface has literally one shape. It would work (same early-return case as routes 9 and 17), but it churns a green, tested destructive path for symmetry alone. Deliberately excluded from this branch; recorded as an optional two-line follow-up guarded by tests/test_contact_remove.py:413-431.

- What happens when a stale refusal lands while a cell EDITOR is open. Traced through inline-cell.js (focusout at :82-88 with `committing` false issues a second GET to the same route, one extra panel replace) and reasoned to settle rather than loop — but this is browser behaviour, not markup, and browsers disagree about whether removing a focused element fires focusout at all. It is scenario 2 of the Playwright pass, not a settled claim.

- Whether the `gone` token family should ever grow past `{"request"}`. Today the request is the only record that is a container. If ROADMAP.md:76 ships a real delete writer for tasks and items, a task or item could become a container for something and the frozenset would need reopening — with the same 'the claim must be true' verification, not a wider vocabulary.

- The sweep does not touch the four _owns_raw_row routes, which answer staleness through the services rather than through the new guard. Two mechanisms answer one question after this branch, deliberately (the services' sentences are better and the routes are tested), but it is the seam a future reader will ask about — D3's `_owns_raw_row` delegation narrows it to one OWNERSHIP rule, not one refusal rule.

- Nothing in this brief addresses the TUI's behaviour on the same staleness. The TUI is not silent (it has its own stale-key handling), but the two surfaces' sentences for a gone task/request/item are not asserted equal anywhere, the way test_contact_remove.py asserts the confirm text string-for-string across surfaces. If D4's sentence later moves to a service, that assertion becomes possible and should be added then.
