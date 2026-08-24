"""Pipeline tab: quotes in hand, submissions out, opportunities — now writable.

Quotes LEAD the tab, above the submissions that produced them. That ordering
is the whole point of the original task: the AE review found that bookkit
tracked work sent and work bound and nothing in between, so the moment a
market answered, the row left the past-SLA queue and entered no queue anywhere
— and the three weeks of comparing terms and chasing subjectivities happened
off the tool.

WRITES (gap 4, 2026-08-20) mirror the TUI's pipeline tab, through the same
shared seams so the surfaces cannot drift:

- Recording a market response is forms.entities.response_form/apply_response —
  the same builder the TUI's `e` on a submission uses — saved through
  account._save, so it is one batch (tool `record_market_response`, the same
  slug FormModal derives from the same title) and a refusal keeps the typed
  input. When the recorded outcome is BOUND and the placement is file-linked,
  the response to the save is the bind-to-layer offer instead of a bare panel
  refresh: layer select labeled the way the TUI's Picker labels them, share
  input, Cancel skips — the write goes through services.program_files.write
  with tool="program_bind", the exact seam the Program tab's + market uses.
- Opportunities: create/edit are forms.entities.opportunity_form; stage moves
  call services.pipeline.move_stage, which OWNS the rules (forward one gate,
  won/lost from any open stage, won → probability 100 / lost → 0 per
  DECISIONS.md) — nothing here re-implements them. Won/lost get a confirm
  step rendered in place (closed is closed; a GET confirm writes nothing);
  advance moves one FORWARD gate only, so the unconfirmed button can never
  close a deal — from `presented` it refuses and points at won/lost.
- Subjectivities: add to a quote's submission and whole-form edit, via
  forms.entities.subjectivity_form/apply_subjectivity. Status ("met",
  "waived") is a field ON that form, exactly as it is in the TUI — there is
  no d-style mark-met action on any surface.

THE FORM HOST SITS OUTSIDE #pipeline-panel (see pipeline.html). Every write
here answers with the whole panel out-of-band — a response moves a row across
three sections at once, so no single-row swap is honest — and an OOB swap of
a panel that CONTAINED the form host would detach the very element the
primary content (a refusal, or the bind offer) is being swapped into.

The global kanban (tui/screens/pipeline.py) stays TUI: this module is the
ACCOUNT's pipeline. web/parity.py carries the note.

Registered BEFORE routes/account.py's generic GET /accounts/{ref}/{tab}
(see web/app.py): Starlette resolves in registration order, not by
specificity, so this module's explicit paths are what serve the tab.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from starlette.requests import Request

from ... import sync
from ...dates import days_until
from ...forms.entities import (
    apply_opportunity,
    apply_response,
    apply_subjectivity,
    opportunity_form,
    response_form,
    subjectivity_form,
)
from ...forms.spec import Field, FieldError, FormSpec, parse_values
from ...models import Opportunity, Org, Subjectivity, Submission
from ...money import format_cents_compact
from ...repo import opportunities as opportunities_repo
from ...repo import orgs as orgs_repo
from ...repo import placements as placements_repo
from ...repo import submissions as submissions_repo
from ...services import batches as batches_svc
from ...services import pipeline as pipeline_svc
from ...services import program_files
from ...services import quotes as quotes_svc
from ..app import TEMPLATES
from ..forms_render import render_form
from .account import _conn, _context, _org, _save

router = APIRouter()


def _money(cents: int | None) -> str:
    """An em dash for nothing, never "$0.00" — the same reading the TUI's
    theme.money_text gives, and the difference matters here: a quote with no
    premium recorded yet is not a quote for nothing."""
    return format_cents_compact(cents) if cents else "—"


def _quote_rows(request: Request, org: Org) -> list[dict[str, object]]:
    """Every quote in hand for this account — expiry or not.

    `services.quotes.for_org`, not `expiring`: the 120-day queue is the
    book-wide chase list, while an account's own tab must show a quote whose
    expiry nobody has recorded yet. That one reads "no expiry", which is a
    prompt to go and ask, not an omission.
    """
    today = date.today()
    rows: list[dict[str, object]] = []
    for quote in quotes_svc.for_org(_conn(request), org.id, org.name, today=today):
        rows.append({
            "submission_id": quote.submission.id,
            "market": quote.market_name,
            "about": quote.about,
            "underwriter": quote.underwriter_name,
            "underwriter_email": quote.underwriter_email,
            "premium": _money(quote.submission.quoted_premium),
            "limit": _money(quote.submission.quoted_limit),
            # date and word are read off ONE object: QuoteItem.expires_on and
            # QuoteItem.expiry_word both resolve submission.quote_expires_on,
            # so the template cannot print a date beside a count from
            # somewhere else (CLAUDE.md's "70d over" defect).
            "expires_on": quote.expires_on,
            "expiry_word": quote.expiry_word,
            "expiry_state": quote.expiry_state,
            "open_subjectivities": quote.open_subjectivities,
            "total_subjectivities": quote.total_subjectivities,
        })
    return rows


def _subjectivity_rows(request: Request, org: Org) -> list[dict[str, object]]:
    """What this client still owes a market before a quote can be bound."""
    today = date.today()
    rows: list[dict[str, object]] = []
    for row in submissions_repo.outstanding_subjectivity_rows_for_org(
        _conn(request), org.id
    ):
        days = days_until(row["due_on"], today) if row["due_on"] else None
        rows.append({
            "id": row["id"],
            "description": row["description"],
            "market": row["market_name"],
            "about": row["about"],
            "due_on": row["due_on"],
            "days": days,
            "overdue": days is not None and days < 0,
            "status": row["status"],
        })
    return rows


def _submission_rows(request: Request, org: Org) -> list[dict[str, object]]:
    """Still out at market, no answer yet — the queue services/sla.py counts
    days against. An answer is recorded off this row (the TUI's `e`), which
    is what moves it up into the quotes section."""
    today = date.today()
    rows: list[dict[str, object]] = []
    for row in submissions_repo.outstanding_for_org(_conn(request), org.id):
        rows.append({
            "id": row["id"],
            "market": row["market_name"],
            "about": row["about"],
            "sent_on": row["sent_on"],
            "days_out": -days_until(row["sent_on"], today),
        })
    return rows


def _opportunity_rows(request: Request, org: Org) -> list[dict[str, object]]:
    """Open opportunities carry their controls; closed ones carry NONE —
    closed is closed, and rendering a disabled stage button would be the
    inert-control defect (D4).

    `next_stage` is the one FORWARD gate, or None at `presented`: the
    unconfirmed advance button must never be able to close a deal, so won
    is deliberately not offered as an "advance" even though
    services.pipeline.allowed_next lists it — won and lost go through the
    confirm step instead, from any open stage."""
    rows: list[dict[str, object]] = []
    for opp in opportunities_repo.for_org(_conn(request), org.id, open_only=False):
        stage = str(opp.stage)
        is_open = stage not in pipeline_svc.CLOSED
        next_stage: str | None = None
        if is_open:
            idx = pipeline_svc.OPEN_STAGES.index(stage)
            if idx + 1 < len(pipeline_svc.OPEN_STAGES):
                next_stage = pipeline_svc.OPEN_STAGES[idx + 1]
        rows.append({
            "id": opp.id,
            "ref": opp.ref,
            "title": opp.title,
            "stage": stage,
            "target_premium": _money(opp.target_premium),
            "probability_pct": opp.probability_pct,
            "open": is_open,
            "next_stage": next_stage,
        })
    return rows


def _rows_context(request: Request, org: Org) -> dict[str, Any]:
    return {
        "quote_rows": _quote_rows(request, org),
        "subjectivity_rows": _subjectivity_rows(request, org),
        "submission_rows": _submission_rows(request, org),
        "opportunity_rows": _opportunity_rows(request, org),
    }


@router.get("/accounts/{ref}/pipeline", response_class=HTMLResponse)
def pipeline_tab(request: Request, ref: str) -> HTMLResponse:
    conn = _conn(request)
    org = _org(request, ref)
    context = _context(conn, org, "pipeline", request)
    context.update(_rows_context(request, org))
    return TEMPLATES.TemplateResponse(request, "account/pipeline.html", context)


# --- the write plumbing -------------------------------------------------------


def _panel(request: Request, ref: str, org: Org) -> HTMLResponse:
    """The whole panel, out of band. A pipeline write moves rows BETWEEN
    sections — a recorded response leaves "out at market" and enters "quotes
    in hand"; a met subjectivity leaves its own table and changes a quote's
    count — so no single-section swap is honest."""
    context: dict[str, Any] = {"header": {"org": org}, "oob": True}
    context.update(_rows_context(request, org))
    return TEMPLATES.TemplateResponse(
        request, "account/_pipeline_panel.html", context
    )


def _text(response: Any) -> str:
    """A rendered response's body as text. Starlette types `.body` as
    bytes | memoryview, and only one of those decodes."""
    body = response.body
    return bytes(body).decode()


def _refusal(request: Request, message: str) -> HTMLResponse:
    """A refused write with no form behind it — the advance button.

    Deliberately 200: htmx swaps 2xx only, and a refusal the user cannot
    read is the silent failure this codebase keeps finding. It lands in the
    tab's form host, never over the panel. Hand-built HTML sees no
    autoescape, so the message is escaped by hand (the phase-4 rule —
    _program_refusal.html, which this branch's older base still had, was
    retired for exactly this shape)."""
    from markupsafe import escape

    return HTMLResponse(f'<p class="form-error" role="alert">{escape(message)}</p>')


def _not_here(kind: str, entity_id: str, org: Org) -> HTTPException:
    """Unknown id and someone else's id are the same 404, deliberately:
    telling the two apart is how a guessable id becomes a membership
    oracle (the same rule as account._owned)."""
    return HTTPException(status_code=404, detail=f"no {kind} {entity_id} on {org.ref}")


def _owned_submission(
    conn: sqlite3.Connection, org: Org, submission_id: str
) -> Submission:
    """A submission carries no org_id of its own — it belongs to whoever its
    placement or opportunity does, the same join outstanding_for_org makes."""
    try:
        sub = submissions_repo.get(conn, submission_id)
    except KeyError:
        raise _not_here("submission", submission_id, org) from None
    owners: set[str] = set()
    if sub.placement_id:
        try:
            owners.add(placements_repo.get(conn, sub.placement_id).org_id)
        except KeyError:  # a deleted placement carries nothing onto the page
            pass
    if sub.opportunity_id:
        try:
            owners.add(opportunities_repo.get(conn, sub.opportunity_id).org_id)
        except KeyError:
            pass
    if org.id not in owners:
        raise _not_here("submission", submission_id, org)
    return sub


def _owned_opportunity(
    conn: sqlite3.Connection, org: Org, opp_id: str
) -> Opportunity:
    try:
        opp = opportunities_repo.get(conn, opp_id)
    except KeyError:
        raise _not_here("opportunity", opp_id, org) from None
    if opp.org_id != org.id:
        raise _not_here("opportunity", opp_id, org)
    return opp


def _owned_subjectivity(
    conn: sqlite3.Connection, org: Org, subj_id: str
) -> Subjectivity:
    """Reached through TWO records: the subjectivity, and the submission it
    belongs to — which is what ties it to this account."""
    try:
        subj = submissions_repo.get_subjectivity(conn, subj_id)
    except KeyError:
        raise _not_here("subjectivity", subj_id, org) from None
    _owned_submission(conn, org, subj.submission_id)
    return subj


def _open_batch_web(conn: sqlite3.Connection, **kwargs: Any) -> Any:
    """This surface's stamp on the shared program-write seam. The tool name
    is the MCP server's own (`program_bind`), so the changes list reads the
    same whichever surface made the edit."""
    return batches_svc.open_batch(conn, source="web", **kwargs)


# --- recording a market response ----------------------------------------------


def _response_action(ref: str, submission_id: str) -> str:
    return f"/accounts/{ref}/pipeline/submissions/{submission_id}/response"


@router.get(
    "/accounts/{ref}/pipeline/submissions/{submission_id}/response",
    response_class=HTMLResponse,
)
def response_form_route(request: Request, ref: str, submission_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    sub = _owned_submission(conn, org, submission_id)
    spec = response_form(sub, conn)
    return HTMLResponse(render_form(request, spec, _response_action(ref, submission_id)))


@router.post(
    "/accounts/{ref}/pipeline/submissions/{submission_id}/response",
    response_class=HTMLResponse,
)
async def response_save(request: Request, ref: str, submission_id: str) -> HTMLResponse:
    """apply_response inside one batch (account._save derives the same
    `record_market_response` slug FormModal does from the shared title), then
    EITHER the refreshed panel or — when the outcome is bound and there is a
    tower to put the market on — the bind offer, exactly where the form was.

    The TUI's response_saved callback does the same two things in the same
    order: apply_response, then _offer_bind_to_layer when status == bound."""
    org = _org(request, ref)
    conn = _conn(request)
    sub = _owned_submission(conn, org, submission_id)
    spec = response_form(sub, conn)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = _response_action(ref, submission_id)
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_response(conn, sub.id, values),
    )
    if refused:
        return refused

    fresh = submissions_repo.get(conn, sub.id)
    if str(fresh.status) == "bound":
        offer = _bind_offer(request, ref, fresh)
        if offer is not None:
            # the offer is the primary content (it lands in the form host,
            # which lives OUTSIDE the panel); the panel refresh rides along
            # out of band, so the row already reads "bound" behind the offer.
            return HTMLResponse(offer + _text(_panel(request, ref, org)))
    return _panel(request, ref, org)


def _bind_spec(
    market: Any, layers: list[dict[str, Any]], line_named: dict[str, str]
) -> FormSpec:
    """The bind-to-layer offer. Layer options are labeled name, limit xs
    attach, signed % — because "Umbrella" alone does not say which of two
    umbrella layers is short of its share.

    AND THE NAME IS QUALIFIED BY ITS LINE OF COVERAGE WHERE IT HAS TO BE
    (`sync.qualified_layer_names`, 2026-08-24). Lines of coverage each arrive
    with a layer called "To be placed", so a program with three of them offered
    three byte-identical options here — `To be placed  $5M xs $0  (0% placed)`
    for Workers Compensation, Crime and Fidelity. The id is correct for
    whichever one is clicked, which is what makes it worse than the "same
    policy as" collision it shares a rule with: a mis-click writes a real
    participation on the wrong line of coverage, through sync.add_participant,
    in a revertible batch nobody knows to revert.

    `share_bps` is kind="share": ONE percent→bps rule, towerkit's own
    (money.parse_share_bps delegates), so '25' and '12.5%' both land right."""
    named = sync.qualified_layer_names(layers, line_named)
    options = tuple(
        (
            f"{named[str(ly['id'])]}  {format_cents_compact(ly['limit_cents'])} xs "
            f"{format_cents_compact(ly['attach_cents'])}  ({ly['signed_pct']:g}% placed)",
            str(ly["id"]),
        )
        for ly in layers
    )
    return FormSpec(
        f"add {market.name} to which layer? (cancel skips)",
        [
            Field("layer_id", "layer", "select", options, required=True),
            Field("share_bps", "share % ('25', '12.5%')", "share", required=True),
        ],
    )


def _bind_parts(
    conn: sqlite3.Connection, sub: Submission
) -> tuple[Any, Any, list[dict[str, Any]], dict[str, str]] | None:
    """(placement, market, layers, line names) when a bind can be offered, else
    None — the same three gates the TUI's _offer_bind_to_layer walks: a
    placement, a linked file, at least one layer to put the market on.

    The line names ride along because the offer's labels need them to tell two
    "To be placed" layers apart, and they come off the same read."""
    if sub.placement_id is None:
        return None
    try:
        placement = placements_repo.get(conn, sub.placement_id)
    except KeyError:
        return None
    if not placement.program_path:
        return None
    layers = sync.layer_details(conn, placement.id)
    if not layers:
        return None
    market = orgs_repo.get(conn, sub.market_org_id)
    line_named = dict(sync.program_lines(conn, placement.id))
    return placement, market, layers, line_named


def _bind_offer(request: Request, ref: str, sub: Submission) -> str | None:
    parts = _bind_parts(_conn(request), sub)
    if parts is None:
        return None
    _, market, layers, line_named = parts
    action = f"/accounts/{ref}/pipeline/submissions/{sub.id}/bind"
    return render_form(request, _bind_spec(market, layers, line_named), action)


@router.post(
    "/accounts/{ref}/pipeline/submissions/{submission_id}/bind",
    response_class=HTMLResponse,
)
async def bind_to_layer(request: Request, ref: str, submission_id: str) -> HTMLResponse:
    """The accepted offer: sync.add_participant through
    services.program_files.write — tool="program_bind", the same seam and
    the same name as the Program tab's + market, so one batch with a
    pre-image snapshot, revertible either way. A refusal (an over-sign, in
    towerkit's own words) re-renders the offer with the input intact."""
    org = _org(request, ref)
    conn = _conn(request)
    sub = _owned_submission(conn, org, submission_id)
    parts = _bind_parts(conn, sub)
    if parts is None:
        return _refusal(
            request,
            "nothing to bind to any more — the placement has no linked "
            "program file with layers",
        )
    placement, market, layers, line_named = parts
    spec = _bind_spec(market, layers, line_named)
    action = f"/accounts/{ref}/pipeline/submissions/{submission_id}/bind"
    raw = {k: str(v) for k, v in (await request.form()).items()}
    try:
        values = parse_values(spec, raw)
    except FieldError as exc:
        return HTMLResponse(render_form(request, spec, action, exc.message, raw))
    layer = next(ly for ly in layers if str(ly["id"]) == values["layer_id"])
    try:
        program_files.write(
            conn, placement,
            tool="program_bind",
            summary=f"{market.name} on {layer['name']}",
            mutate=lambda: sync.add_participant(
                conn, placement.id, layer["id"], market.name, values["share_bps"]
            ),
            open_batch=_open_batch_web,
        )
    except Exception as exc:  # a refused write is a message, never a 500
        return HTMLResponse(render_form(request, spec, action, str(exc), raw))
    return _panel(request, ref, org)


# --- opportunities ------------------------------------------------------------


@router.get("/accounts/{ref}/pipeline/opportunities/new", response_class=HTMLResponse)
def opportunity_new_form(request: Request, ref: str) -> HTMLResponse:
    _org(request, ref)  # the 404 guard; this form needs nothing else off it
    spec = opportunity_form(conn=_conn(request))
    action = f"/accounts/{ref}/pipeline/opportunities/new"
    return HTMLResponse(render_form(request, spec, action))


@router.post("/accounts/{ref}/pipeline/opportunities/new", response_class=HTMLResponse)
async def opportunity_create(request: Request, ref: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    spec = opportunity_form(conn=conn)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/pipeline/opportunities/new"
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_opportunity(conn, values, org.id),
    )
    return refused or _panel(request, ref, org)


@router.get(
    "/accounts/{ref}/pipeline/opportunities/{opp_id}/edit", response_class=HTMLResponse
)
def opportunity_edit_form(request: Request, ref: str, opp_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    opp = _owned_opportunity(conn, org, opp_id)
    spec = opportunity_form(opp, conn=conn)
    action = f"/accounts/{ref}/pipeline/opportunities/{opp_id}/edit"
    return HTMLResponse(render_form(request, spec, action))


@router.post(
    "/accounts/{ref}/pipeline/opportunities/{opp_id}/edit", response_class=HTMLResponse
)
async def opportunity_update(request: Request, ref: str, opp_id: str) -> HTMLResponse:
    """Plain field edits only: repo.opportunities.update refuses `stage`
    (services.pipeline.move_stage owns transitions), and opportunity_form
    carries no stage field to begin with — both surfaces share that shape."""
    org = _org(request, ref)
    conn = _conn(request)
    opp = _owned_opportunity(conn, org, opp_id)
    spec = opportunity_form(opp, conn=conn)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/pipeline/opportunities/{opp_id}/edit"
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_opportunity(conn, values, opp.org_id, opp),
    )
    return refused or _panel(request, ref, org)


@router.post(
    "/accounts/{ref}/pipeline/opportunities/{opp_id}/advance",
    response_class=HTMLResponse,
)
def opportunity_advance(request: Request, ref: str, opp_id: str) -> HTMLResponse:
    """One FORWARD gate, unconfirmed — the TUI's `>` with one deliberate
    narrowing: at `presented` the TUI's `>` closes the deal won, but this
    button is a click, not a keystroke on a focused card, and closing is the
    consequential write the confirm step exists for. So it refuses there and
    points at won/lost. The service still guards the move itself."""
    org = _org(request, ref)
    conn = _conn(request)
    opp = _owned_opportunity(conn, org, opp_id)
    stage = str(opp.stage)
    if stage in pipeline_svc.CLOSED:
        return _refusal(request, f"{opp.ref} is closed — closed is closed")
    idx = pipeline_svc.OPEN_STAGES.index(stage)
    if idx + 1 >= len(pipeline_svc.OPEN_STAGES):
        return _refusal(
            request,
            f"{opp.ref} is at {stage}, the last gate — close it won or lost instead",
        )
    nxt = pipeline_svc.OPEN_STAGES[idx + 1]
    try:
        with batches_svc.open_batch(
            conn, source="web", tool="advance_card",
            summary=f"advanced {opp.ref} to {nxt}", org_id=opp.org_id,
        ):
            pipeline_svc.move_stage(conn, opp.id, nxt)
    except Exception as exc:  # a refused move is a message, never a 500
        return _refusal(request, str(exc))
    return _panel(request, ref, org)


def _checked_outcome(outcome: str) -> str:
    if outcome not in ("won", "lost"):
        raise HTTPException(status_code=404, detail=f"{outcome!r} is not a close")
    return outcome


@router.get(
    "/accounts/{ref}/pipeline/opportunities/{opp_id}/close/{outcome}",
    response_class=HTMLResponse,
)
def opportunity_close_confirm(
    request: Request, ref: str, opp_id: str, outcome: str
) -> HTMLResponse:
    """The confirm step. Writes NOTHING — it names the blast radius (closed
    is closed, the probability side-effect) and hands the POST to a button."""
    _checked_outcome(outcome)
    org = _org(request, ref)
    opp = _owned_opportunity(_conn(request), org, opp_id)
    if str(opp.stage) in pipeline_svc.CLOSED:
        return _refusal(request, f"{opp.ref} is already closed ({opp.stage})")
    return TEMPLATES.TemplateResponse(
        request, "account/_opp_close_confirm.html",
        {"header": {"org": org}, "opp": opp, "outcome": outcome},
    )


@router.post(
    "/accounts/{ref}/pipeline/opportunities/{opp_id}/close/{outcome}",
    response_class=HTMLResponse,
)
def opportunity_close(
    request: Request, ref: str, opp_id: str, outcome: str
) -> HTMLResponse:
    """move_stage owns everything that matters: won/lost allowed from any
    open stage, closed_at, outcome, and probability → 100/0 (DECISIONS.md).
    Tool names are the TUI kanban's own — `close_lost` for lost; won reuses
    `advance_card`, the tool the TUI's only won-marking key writes under."""
    _checked_outcome(outcome)
    org = _org(request, ref)
    conn = _conn(request)
    opp = _owned_opportunity(conn, org, opp_id)
    tool = "close_lost" if outcome == "lost" else "advance_card"
    try:
        with batches_svc.open_batch(
            conn, source="web", tool=tool,
            summary=f"marked {opp.ref} {outcome}", org_id=opp.org_id,
        ):
            pipeline_svc.move_stage(conn, opp.id, outcome)
    except Exception as exc:
        return _refusal(request, str(exc))
    return _panel(request, ref, org)


# --- subjectivities -----------------------------------------------------------


@router.get(
    "/accounts/{ref}/pipeline/submissions/{submission_id}/subjectivities/new",
    response_class=HTMLResponse,
)
def subjectivity_new_form(request: Request, ref: str, submission_id: str) -> HTMLResponse:
    org = _org(request, ref)
    _owned_submission(_conn(request), org, submission_id)
    spec = subjectivity_form()
    action = f"/accounts/{ref}/pipeline/submissions/{submission_id}/subjectivities/new"
    return HTMLResponse(render_form(request, spec, action))


@router.post(
    "/accounts/{ref}/pipeline/submissions/{submission_id}/subjectivities/new",
    response_class=HTMLResponse,
)
async def subjectivity_create(
    request: Request, ref: str, submission_id: str
) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    sub = _owned_submission(conn, org, submission_id)
    spec = subjectivity_form()
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/pipeline/submissions/{submission_id}/subjectivities/new"
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_subjectivity(conn, values, sub.id),
    )
    return refused or _panel(request, ref, org)


@router.get(
    "/accounts/{ref}/pipeline/subjectivities/{subj_id}/edit",
    response_class=HTMLResponse,
)
def subjectivity_edit_form(request: Request, ref: str, subj_id: str) -> HTMLResponse:
    org = _org(request, ref)
    subj = _owned_subjectivity(_conn(request), org, subj_id)
    spec = subjectivity_form(subj)
    action = f"/accounts/{ref}/pipeline/subjectivities/{subj_id}/edit"
    return HTMLResponse(render_form(request, spec, action))


@router.post(
    "/accounts/{ref}/pipeline/subjectivities/{subj_id}/edit",
    response_class=HTMLResponse,
)
async def subjectivity_update(request: Request, ref: str, subj_id: str) -> HTMLResponse:
    """Marking one met or waived IS this route: status is a field on the
    shared form (with satisfied_on beside it), exactly as the TUI has it —
    no separate mark-done action exists on any surface."""
    org = _org(request, ref)
    conn = _conn(request)
    subj = _owned_subjectivity(conn, org, subj_id)
    spec = subjectivity_form(subj)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/pipeline/subjectivities/{subj_id}/edit"
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_subjectivity(conn, values, subj.submission_id, subj),
    )
    return refused or _panel(request, ref, org)
