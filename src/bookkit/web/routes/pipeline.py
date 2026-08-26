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
  input. IT WRITES A `market_response`, not the submission's own columns:
  five of those columns had a second home on that row, and the submission is
  now derived from its rows the way its status already was (Grant,
  2026-08-26). So the form asks WHICH LINE OF COVERAGE the answer is about —
  never guessed, and never a reason the answer cannot be recorded: where the
  placement has declared none, the picker is the book's own vocabulary and
  saving declares the line it is given (`_no_line_here` carries the whole
  story, and what is left of the refusal). When the recorded outcome is
  BOUND and the placement is file-linked, the response to the save is the
  bind-to-layer offer instead of a bare panel refresh: layer select labeled
  the way the TUI's Picker labels them, share input, Cancel skips — the
  write goes through services.program_files.write with tool="program_bind",
  the exact seam the Program tab's + market uses.
  RE-OPENED, IT SHOWS WHAT THE PACKAGE HAS ALREADY ANSWERED (read-only prose,
  never a prefill), the picker says which lines saving would CORRECT, and one
  answered line arrives selected — it used to re-open completely blank with
  re-picking compulsory, which is how a mis-pick wrote a SECOND answer and
  printed a premium no market quoted onto a client's workbook. The change
  list's sentence names the market and the line and says whether the save
  recorded or corrected (forms.entities.response_batch), because that rail is
  the way back from a wrong pick.
- Withdrawing a package: a control on the submission's own row, confirm-gated
  (a STEP, not an hx-confirm — the plan is what stays reported and where the
  row reappears), with a Withdrawn list carrying Reinstate. It is a decision
  about the SUBMISSION rather than a summary of what a market said, which is
  why it is not an outcome on the Response form — it was one until 2026-08-26,
  and pointing that form at `market_response` left `withdrawn` with no writer
  on any surface. Rules in services.marketing_entry, shared with MCP's
  submission_withdraw / submission_reinstate.
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
    response_batch,
    response_form,
    response_line_options,
    subjectivity_form,
)
from ...forms.spec import Field, FieldError, FormSpec, parse_values
from ...models import Opportunity, Org, Subjectivity, Submission
from ...money import format_cents_compact
from ...repo import marketing as marketing_repo
from ...repo import opportunities as opportunities_repo
from ...repo import orgs as orgs_repo
from ...repo import placements as placements_repo
from ...repo import submissions as submissions_repo
from ...services import batches as batches_svc
from ...services import marketing_entry, program_files
from ...services import pipeline as pipeline_svc
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


def _withdrawn_rows(request: Request, org: Org) -> list[dict[str, object]]:
    """The packages we PULLED — the state that had no surface at all.

    A withdrawn submission is in no Pipeline queue: "out at market" is
    `status = 'out'` and "quotes in hand" is `status = 'quoted'`, so before
    this it left the tab entirely the moment it was withdrawn, taking the only
    control that could put it back with it. Same shape and same reason as the
    team panel's Retired list, and it carries Reinstate the way that one
    carries Reactivate.

    `days_out` is deliberately NOT printed here: it counts a clock that has
    stopped, and a package we pulled six weeks ago is not "42d out".
    """
    rows: list[dict[str, object]] = []
    for row in submissions_repo.withdrawn_for_org(_conn(request), org.id):
        rows.append({
            "id": row["id"],
            "market": row["market_name"],
            "about": row["about"],
            "sent_on": row["sent_on"],
        })
    return rows


def _rows_context(request: Request, org: Org) -> dict[str, Any]:
    return {
        "quote_rows": _quote_rows(request, org),
        "subjectivity_rows": _subjectivity_rows(request, org),
        "submission_rows": _submission_rows(request, org),
        "withdrawn_rows": _withdrawn_rows(request, org),
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


def _no_line_here(conn: sqlite3.Connection, ref: str, sub: Submission) -> str | None:
    """The refusal to render INSTEAD of this form when THE BOOK carries no
    line of coverage to answer on, or None when it does — which, on any book
    with a vocabulary, is always.

    A REFUSAL SAYS SOMETHING, and it must be raised BEFORE the form: a select
    with no options is a required field nobody can satisfy, which reads as a
    broken app rather than as a thing to go and do.

    AND ITS WORDS HAVE TO BE TRUE, which the first version's were not. It read
    "nothing on this placement is being marketed yet" and fired wherever the
    placement had no `placement_line` row — which is EVERY placement on the
    seeded book and every one of Grant's own: forty submissions across fourteen
    placements, four markets approached and two quoting $1.4M on the very
    placement the sentence called unmarketed, printed four inches up the same
    screen. Worse, it refused a form that had recorded those answers before the
    responses moved onto their own rows. `response_line_options` now offers the
    book's vocabulary where the placement has said nothing, and the answer
    declares the line as it records it, so what is left here is the one state
    that is genuinely unrecordable: a book with no line of coverage in it at
    all. The fix it names creates one — the Marketing section's add-a-line
    control takes a name the book has never carried — and the sentence carries
    the link because the Pipeline tab is not where that control lives.
    """
    if response_line_options(conn, sub):
        return None
    where = (
        f"Open one under Marketing on the Program tab "
        f"(/accounts/{ref}/program) — the add-a-line control there takes a "
        f"name this book has never carried — then record the response."
        if sub.placement_id
        else "Open one under Marketing on any placement's Program tab first."
    )
    return (
        "a market answers a LINE OF COVERAGE, and this book has no line of "
        "coverage recorded yet — so there is nothing to record this answer "
        f"against. {where}"
    )


@router.get(
    "/accounts/{ref}/pipeline/submissions/{submission_id}/response",
    response_class=HTMLResponse,
)
def response_form_route(request: Request, ref: str, submission_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    sub = _owned_submission(conn, org, submission_id)
    nothing_to_answer = _no_line_here(conn, ref, sub)
    if nothing_to_answer:
        return _refusal(request, nothing_to_answer)
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
    nothing_to_answer = _no_line_here(conn, ref, sub)
    if nothing_to_answer:
        # The line was there when the page rendered and is not now (retired,
        # or removed by another session). The POST is refused with the same
        # sentence the GET gives rather than with an empty picker.
        return _refusal(request, nothing_to_answer)
    # REBUILT SERVER-SIDE from the same arguments that built it for the GET,
    # so `checked_option` on `line_id` is the account-scope check as well as
    # the vocabulary one (forms.spec.checked_option says why).
    spec = response_form(sub, conn)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = _response_action(ref, submission_id)
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_response(conn, sub.id, values),
        # THE SENTENCE THE CHANGE LIST PRINTS, and it names what happened:
        # "corrected what Chubb said about General Liability" rather than a
        # second `record market response` indistinguishable from the first.
        # The rail is on this tab, and it is the way back from a wrong pick.
        batch=response_batch(conn, sub),
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


# --- pulling a package, and putting it back ----------------------------------
#
# WITHDRAWING IS A DECISION ABOUT THE SUBMISSION, so it is a control on the
# submission and not an outcome on the Response form. It was one, until
# 2026-08-26: that form's picker offered the SUBMISSION statuses and was the
# only writer of `withdrawn` anywhere in the app, so re-pointing the form at
# `market_response` — which has no such word, and rightly, because no market
# said it — left three code paths refusing on a state nobody could enter
# (r6 blocker 2). The rules are services.marketing_entry's, shared with MCP's
# `submission_withdraw` / `submission_reinstate`.


def _withdraw_action(ref: str, submission_id: str) -> str:
    return f"/accounts/{ref}/pipeline/submissions/{submission_id}/withdraw"


@router.get(
    "/accounts/{ref}/pipeline/submissions/{submission_id}/withdraw",
    response_class=HTMLResponse,
)
def withdraw_confirm(request: Request, ref: str, submission_id: str) -> HTMLResponse:
    """The confirm STEP, which writes nothing — not an `hx-confirm`, for the
    reason web/parity.py records against the browser's own confirm(): it shows
    no plan, and the plan here is the whole point (what goes, what stays, and
    where the package reappears)."""
    org = _org(request, ref)
    conn = _conn(request)
    sub = _owned_submission(conn, org, submission_id)
    market = orgs_repo.names_for_any(conn, {sub.market_org_id or ""}).get(
        sub.market_org_id or "", "this market"
    )
    return TEMPLATES.TemplateResponse(
        request,
        "account/_submission_withdraw_confirm.html",
        {
            "ref": ref,
            "market": market,
            "sent_on": sub.sent_on,
            "action": _withdraw_action(ref, submission_id),
            "answers": len(marketing_repo.responses_for_submission(conn, sub.id)),
        },
    )


@router.post(
    "/accounts/{ref}/pipeline/submissions/{submission_id}/withdraw",
    response_class=HTMLResponse,
)
def withdraw_save(request: Request, ref: str, submission_id: str) -> HTMLResponse:
    org = _org(request, ref)
    conn = _conn(request)
    sub = _owned_submission(conn, org, submission_id)
    market = orgs_repo.names_for_any(conn, {sub.market_org_id or ""}).get(
        sub.market_org_id or "", "this market"
    )
    try:
        with batches_svc.open_batch(
            conn, source="web", tool="submission_withdraw",
            summary=f"withdrew the package to {market}", org_id=org.id,
        ):
            marketing_entry.withdraw(conn, sub.id)
    except Exception as exc:  # a refused write is a message, never a 500
        return _refusal(request, str(exc))
    return _panel(request, ref, org)


@router.post(
    "/accounts/{ref}/pipeline/submissions/{submission_id}/reinstate",
    response_class=HTMLResponse,
)
def reinstate_save(request: Request, ref: str, submission_id: str) -> HTMLResponse:
    """No confirm step: one column goes back to what the rows say, nothing
    cascades, and the change list reverts it — the same reading the team
    panel's Reactivate takes beside its confirm-gated Retire."""
    org = _org(request, ref)
    conn = _conn(request)
    sub = _owned_submission(conn, org, submission_id)
    market = orgs_repo.names_for_any(conn, {sub.market_org_id or ""}).get(
        sub.market_org_id or "", "this market"
    )
    try:
        with batches_svc.open_batch(
            conn, source="web", tool="submission_reinstate",
            summary=f"put the package to {market} back at market", org_id=org.id,
        ):
            marketing_entry.reinstate(conn, sub.id)
    except Exception as exc:
        return _refusal(request, str(exc))
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
