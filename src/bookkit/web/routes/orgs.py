"""Account create (from /book) and account edit (from the account header).

Registered BEFORE account.router in app.py, exactly like program.py and for
the same reason: GET /accounts/{ref}/edit and account.py's generic
GET /accounts/{ref}/{tab} match the same two-segment path, and Starlette
resolves across routers by registration order rather than by specificity.

Both forms are bookkit.forms.entities' own org builders — the same FormSpec
the TUI's book screen pushes, so a Field added to org_form appears on both
surfaces with no second list to update. Both writes are one batch each, with
the same tool slug the TUI's FormModal derives from the same title
(BatchSpec.for_title: "new account" -> new_account, "edit account" ->
edit_account). The create door runs services.orgs.find_duplicate — the ONE
near-name guard MCP's client_create and both TUI create forms share — and a
refusal re-renders the form in its .form-host, input intact, naming the
match. htmx swaps neither 4xx nor 5xx, so every refusal here is HTTP 200
with the message in the page.

Success is a NAVIGATION, not a panel swap: a create lands on the account it
just made, and an org edit can change the header, the breadcrumb, the title
and the book row at once, so no single panel swap is honest — the same
reasoning as routes/changes.py's revert. 204 + HX-Redirect / HX-Refresh for
htmx, a plain 303 for the form tag's no-JS fallback.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ...forms.entities import apply_org, org_form, org_form_initial_profile
from ...forms.spec import BatchSpec, FieldError, parse_values
from ...models import Org
from ...services import batches as batches_svc
from ...services import orgs as orgs_svc
from ..forms_render import render_form
from .account import _conn, _org, _save

router = APIRouter()

_CREATE_ACTION = "/book/accounts"


@router.get("/book/accounts/new", response_class=HTMLResponse)
def account_new_form(request: Request) -> HTMLResponse:
    """The blank org form, fetched into the book header's .form-host."""
    spec = org_form(conn=_conn(request))
    return HTMLResponse(render_form(request, spec, _CREATE_ACTION))


@router.post("/book/accounts", response_class=HTMLResponse)
async def account_create(request: Request) -> Response:
    conn = _conn(request)
    spec = org_form(conn=conn)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    try:
        values = parse_values(spec, raw)
    except FieldError as exc:
        return HTMLResponse(render_form(request, spec, _CREATE_ACTION, exc.message, raw))

    # The create-door duplicate guard, BEFORE anything is written — the same
    # services.orgs.find_duplicate call the TUI's two create forms and MCP's
    # client_create make, never a local WRatio copy. Client creates only:
    # the guard's candidate list is the book (see the service's docstring).
    if values.get("kind") == "client":
        dup = orgs_svc.find_duplicate(conn, values["name"])
        if dup is not None:
            return HTMLResponse(render_form(
                request, spec, _CREATE_ACTION,
                f"looks like {dup.name} ({dup.ref}) — rename, or open that "
                f"account instead", raw,
            ))

    # org_id=None on the batch row, like mcpserver's client_create: the org
    # is created INSIDE the batch, so its id is not known when the row is
    # written. The tool slug is the TUI's own (BatchSpec.for_title on the
    # same "new account" title FormModal batches under).
    batch = BatchSpec.for_title(spec.title)
    created: list[Org] = []
    try:
        with batches_svc.open_batch(
            conn, source="web", tool=batch.tool,
            summary=batch.sentence(values), org_id=None,
        ):
            created.append(apply_org(conn, values))
    except Exception as exc:  # a refused save is a message, never a 500
        return HTMLResponse(render_form(request, spec, _CREATE_ACTION, str(exc), raw))

    target = f"/accounts/{created[0].ref}/relationship"
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)


@router.get("/accounts/{ref}/edit", response_class=HTMLResponse)
def account_edit_form(request: Request, ref: str) -> HTMLResponse:
    """The prefilled org form — org_form_initial_profile, the same builder
    the TUI's `e` (falling through to _edit_org) pushes, market-profile
    fields included."""
    org = _org(request, ref)
    spec = org_form_initial_profile(_conn(request), org)
    return HTMLResponse(render_form(request, spec, f"/accounts/{ref}/edit"))


@router.post("/accounts/{ref}/edit", response_class=HTMLResponse)
async def account_update(request: Request, ref: str) -> Response:
    """One batch via _save (tool edit_account, source web), refusals in place.

    A rename onto an EXACT existing name is refused by repo/orgs.guard_name
    inside orgs.update — the guard every surface inherits — and comes back
    here as the form's error line, input intact. Near-name renames are not
    guarded on either surface (the fuzzy guard is a create-door rule).
    """
    org = _org(request, ref)
    conn = _conn(request)
    spec = org_form_initial_profile(conn, org)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    action = f"/accounts/{ref}/edit"
    refused = _save(
        request, org, spec, action, raw,
        lambda values: apply_org(conn, values, org),
    )
    if refused is not None:
        return refused
    # A successful edit can change the header, breadcrumb, title and tab
    # badges at once — reload the page the user is on (ref is stable across
    # renames, so the URL still resolves).
    if request.headers.get("HX-Request"):
        return Response(status_code=204, headers={"HX-Refresh": "true"})
    return RedirectResponse(f"/accounts/{ref}/relationship", status_code=303)
