"""Quick capture on the web — the highest-frequency CRM write, logged from
anywhere via the top bar's "+ Log" (GET /capture), or from an account header's
"+ Log interaction" pill (GET /capture?org={ref}, preselecting the account).

This is a FULL PAGE with a plain form POST, not an htmx fragment: capture is
entered from every page, belongs to no panel, and its refusals re-render the
whole form — every field intact, the message at the top — as an HTTP 200.
The business rules are the TUI QuickCapture modal's, shared where they could
drift: attendee resolution ("who was there" → contact ids, refusing typos and
ambiguity rather than guessing) is services.capture.resolve_attendees, the
same call the TUI makes; the follow-up-task OFFER is capture.suggest_task —
offered on a page of its own after a successful log, never silently created.

Two deliberate divergences from the TUI, both on the strict side:
- an unparseable date is REFUSED with forms.spec.date_refusal's sentence,
  where the TUI modal silently substitutes today ("ambiguous entry is
  refused, never guessed" — CLAUDE.md; the TUI's fallback predates the rule).
- the accepted follow-up task is created INSIDE a batch (tool="task_add"),
  where the TUI's ConfirmTask writes it unbatched — a latent TUI bug (`u`
  cannot reach that task), not a precedent to copy.

/capture shares no path prefix with /accounts/..., so this router's
registration position in app.py is free."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ...dates import parse_human_date
from ...forms.spec import date_refusal
from ...models import InteractionType, Org
from ...normalize import clean_text
from ...repo import contacts as contacts_repo
from ...repo import interactions as interactions_repo
from ...repo import orgs as orgs_repo
from ...repo import tasks as tasks_repo
from ...services import batches as batches_svc
from ...services import capture as capture_svc
from ..app import TEMPLATES
from .account import _conn

router = APIRouter()

_FORM_KEYS = ("org_id", "type", "occurred", "subject", "who", "note")

_TYPES = tuple(t.value for t in InteractionType)


def _blank_form() -> dict[str, str]:
    # "today" pre-filled, matching the TUI modal's date field default.
    return {
        "org_id": "",
        "type": "note",
        "occurred": "today",
        "subject": "",
        "who": "",
        "note": "",
    }


def _render(
    request: Request,
    conn: sqlite3.Connection,
    form: dict[str, str],
    error: str | None = None,
) -> HTMLResponse:
    """The capture page, from whatever is currently typed. A refusal comes
    back through here with `error` set and EVERY field intact — the web's
    commit-in-place: nothing written, nothing retyped."""
    roster: list[Any] = []
    if form["org_id"]:
        # the selected account's roster: a datalist under "who" (the
        # vocabulary-fields rule — these names already exist somewhere) and
        # the hint line naming who the field will match, because you cannot
        # type a name you do not know (the TUI modal renders the same line).
        roster = contacts_repo.for_org(conn, form["org_id"])
    # the TUI's account picker fuzzy-matches over every org; a select has no
    # typing to match, so it lists the client book (the overwhelmingly common
    # target). A preselection that is NOT a client — a market's account header
    # carries the same "+ Log interaction" pill — is appended rather than
    # dropped, or the pill that sent us here would arrive at a form that
    # cannot keep its promise.
    selectable = orgs_repo.list_orgs(conn, kind="client")
    if form["org_id"] and form["org_id"] not in {o.id for o in selectable}:
        preselected = orgs_repo.find(conn, form["org_id"])
        if preselected is not None:
            selectable = [*selectable, preselected]
    context = {
        "form": form,
        "error": error,
        "clients": selectable,
        "types": _TYPES,
        "roster": [c.name for c in roster],
    }
    return TEMPLATES.TemplateResponse(request, "capture.html", context)


@router.get("/capture", response_class=HTMLResponse)
def capture_page(request: Request) -> HTMLResponse:
    """The form. ?org={ref} preselects the account (the account header's
    "+ Log interaction" arrives this way); an unknown ref renders the form
    unselected with a sentence saying so, not a 404 — the page is still
    usable and a refusal says something."""
    conn = _conn(request)
    form = _blank_form()
    error: str | None = None
    ref = request.query_params.get("org")
    if ref:
        org = orgs_repo.find(conn, ref)
        if org is None:
            error = f"no account matches {ref!r} — pick one below"
        else:
            form["org_id"] = org.id
    return _render(request, conn, form, error)


@router.post("/capture", response_class=HTMLResponse)
async def capture_save(request: Request) -> Response:
    """Log the interaction — refusals re-render the form (200, fields intact),
    success writes ONE batch (the interaction and its attendee links land
    together or not at all) and then either offers the follow-up task
    suggest_task spotted, or 303s to the account's relationship tab where the
    new interaction is visible.

    `await` only touches the request body, before any write — nothing awaits
    inside the batch (tests/test_conventions.py)."""
    conn = _conn(request)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    form = {key: raw.get(key, "") for key in _FORM_KEYS}
    if form["type"] not in _TYPES:
        # a crafted or stale value, not one the select offered — refused, and
        # never echoed into the stored record
        form["type"] = "note"
        return _render(request, conn, form, "pick an interaction type")
    if not form["org_id"]:
        return _render(request, conn, form, "pick an account first")
    org = orgs_repo.find(conn, form["org_id"])
    if org is None:
        return _render(
            request, conn, {**form, "org_id": ""}, "that account no longer exists"
        )
    if not clean_text(form["subject"]) and not form["note"].strip():
        return _render(request, conn, form, "nothing to save")
    occurred = parse_human_date(form["occurred"] or "today")
    if occurred is None:
        return _render(request, conn, form, date_refusal(form["occurred"]))
    attendees, refusal = capture_svc.resolve_attendees(conn, org.id, form["who"])
    if refusal is not None:
        return _render(request, conn, form, refusal)

    subject = clean_text(form["subject"]) or form["note"].splitlines()[0][:60]
    # one writer action, one undo unit: the interaction and its attendee
    # links land together or not at all — same batch shape as the TUI's save
    with batches_svc.open_batch(
        conn,
        source="web",
        tool="log_interaction",
        summary=f"logged {form['type']} — {subject}",
        org_id=org.id,
    ):
        interaction = interactions_repo.log(
            conn,
            org.id,
            form["type"],
            subject,
            occurred.isoformat(),
            body=form["note"] or None,
            contact_ids=attendees,
        )

    suggestion = capture_svc.suggest_task(f"{form['subject']} {form['note']}")
    if suggestion is None:
        return RedirectResponse(
            f"/accounts/{org.ref}/relationship", status_code=303
        )
    # OFFER, never silently create: the logged interaction is already saved;
    # this page only proposes the follow-up. [Skip] is a plain link — a GET
    # that writes nothing.
    return TEMPLATES.TemplateResponse(
        request,
        "capture_task.html",
        {
            "org": org,
            "interaction": interaction,
            "suggestion": suggestion,
            "title": suggestion.phrase.capitalize(),
        },
    )


def _org_for_task(conn: sqlite3.Connection, org_id: str) -> Org:
    org = orgs_repo.find(conn, org_id)
    if org is None:
        raise HTTPException(status_code=404, detail=f"no account matches {org_id!r}")
    return org


@router.post("/capture/task", response_class=HTMLResponse)
async def capture_task(request: Request) -> Response:
    """The accepted follow-up. Hidden fields carry org/interaction/due from
    the offer page, so each is re-proven server-side: the interaction must be
    this account's (the compound-claim rule routes/account._owned enforces
    under /accounts/) and the due date must still parse. A mismatch is a 404 —
    these values are never rendered into a control a user could produce a bad
    one from, so a page-shaped refusal has nobody to talk to.

    INSIDE a batch (tool="task_add"), deliberately unlike the TUI's
    ConfirmTask, which creates its task unbatched — a latent bug that leaves
    the task unreachable by `u`, not a behavior to mirror."""
    conn = _conn(request)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    org = _org_for_task(conn, raw.get("org_id", ""))
    interaction_id = raw.get("interaction_id", "")
    try:
        interaction = interactions_repo.get(conn, interaction_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"no interaction {interaction_id!r}"
        ) from None
    if interaction.org_id != org.id:
        raise HTTPException(
            status_code=404, detail=f"no interaction {interaction_id!r} on {org.name}"
        )
    try:
        due_on = date.fromisoformat(raw.get("due_on", ""))
    except ValueError:
        raise HTTPException(status_code=404, detail="that offer is stale") from None
    title = clean_text(raw.get("title", "")) or clean_text(raw.get("phrase", ""))
    if not title:
        raise HTTPException(status_code=404, detail="that offer is stale")
    with batches_svc.open_batch(
        conn,
        source="web",
        tool="task_add",
        summary=f"task — {title}",
        org_id=org.id,
    ):
        tasks_repo.create(
            conn,
            title,
            org_id=org.id,
            due_on=due_on.isoformat(),
            source_interaction_id=interaction.id,
        )
    return RedirectResponse(f"/accounts/{org.ref}/relationship", status_code=303)
