"""The Markets surface: carrier list, one market's dossier, and the writes
the TUI's MarketsScreen / MarketDetailScreen make — create/edit (org_form),
appetite add/edit/remove, underwriters (contacts on the market org), aliases,
merge (services.merge.merge_markets, alias-preserving) and nesting under a
master company.

The web submission form dead-ended against "no markets on file" because no
web surface could create one; this module is that surface. It mirrors
tui/screens/markets.py action for action — the ledger is
web/parity.py's SCREENS["markets"].

House rules observed here:
- No SQL and no field lists: reads go through repo/, forms come from
  bookkit.forms.entities, writes go through the same repo/service calls the
  TUI's commit callbacks make, inside one batch each (services.batches).
- Refusals are HTTP 200 with the sentence in the page — htmx swaps neither
  4xx nor 5xx, so an HTTPException on a refusal renders as silence.
- Destructive/consequential writes confirm first, in place, naming the blast
  radius: appetite removal and the market merge both render a confirm step
  whose GET writes NOTHING.
- Page-level writes (edit market, nest, merge) answer with a redirect to the
  page they changed — a rename moves the header, the list row, and every
  alias line at once, so no single panel swap is honest (the same call
  routes/changes.py records for revert). Panel-scoped writes answer with
  their own panel as an out-of-band swap, the _contacts_panel pattern.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from ... import money
from ...forms.entities import (
    appetite_form,
    apply_contact,
    apply_org,
    contact_form,
    org_form,
    org_form_initial_profile,
)
from ...forms.spec import BatchSpec, Field, FieldError, FormSpec, dropped, parse_values
from ...models import Org
from ...repo import aliases as aliases_repo
from ...repo import contacts as contacts_repo
from ...repo import orgs as orgs_repo
from ...repo import submissions as submissions_repo
from ...services import batches as batches_svc
from ...services import exposure as exposure_svc
from ...services import hit_rate
from ...services.merge import MergeError, merge_markets
from ..app import TEMPLATES
from ..forms_render import render_form
from .account import _conn

router = APIRouter()

# appetite vocabulary → ink, same grammar as the TUI's APPETITE_STYLES map
# (tui/screens/markets.py). Colour is signal, not decoration: the word itself
# is always printed beside it.
_APPETITE_CLASS: dict[str, str] = {
    "target": "appetite-target",
    "will_consider": "appetite-will-consider",
    "selective": "appetite-selective",
    "no": "appetite-no",
}


def _pretty(value: str | None) -> str:
    """snake_case prettified ('will consider'), or an em dash."""
    return value.replace("_", " ") if value else "—"


def _pct(value: float | None) -> str:
    """A rate, or an em dash. 0% and "nothing has come back yet" are
    different facts and must not render alike (tui/screens/markets.py)."""
    return f"{value:.0%}" if value is not None else "—"


def _compact(cents: int | None) -> str:
    """Money in a read-only column displays compact; these cells are edited
    through the whole appetite form, whose pre-fill is exact (initial_text),
    so compaction can never round-trip into the stored number."""
    return money.format_cents_compact(cents) if cents is not None else "—"


def _market(request: Request, ref: str) -> Org:
    """Resolve {ref} to a live MARKET org, or 404. A client's ref must not
    answer here: every write below assumes market semantics (appetite,
    aliases, merge), and orgs.find alone would hand it a client."""
    org = orgs_repo.find(_conn(request), ref)
    if org is None or org.kind != "market":
        raise HTTPException(status_code=404, detail=f"no market matches {ref!r}")
    return org


def _form_save(
    request: Request,
    spec: FormSpec,
    action: str,
    raw: dict[str, str],
    write: Callable[[dict[str, Any]], Any],
    org_id: str | None = None,
) -> HTMLResponse | None:
    """routes/account._save, minus the requirement that the org already
    exists — creating a market has no org id until the write runs. Same
    contract: parse, then `write` inside ONE batch whose tool/summary derive
    from the form title exactly as the TUI's FormModal derives them, so both
    surfaces' changes group under the same tool name. Returns the re-rendered
    form on refusal (input intact, nothing written), None on success."""
    try:
        values = parse_values(spec, raw)
    except FieldError as exc:
        return HTMLResponse(render_form(request, spec, action, exc.message, raw))

    batch = BatchSpec.for_title(spec.title, org_id=org_id)
    try:
        with batches_svc.open_batch(
            _conn(request), source="web", tool=batch.tool,
            summary=batch.sentence(values), org_id=org_id,
        ):
            write(values)
    except Exception as exc:  # a refused save is a message, never a 500
        return HTMLResponse(render_form(request, spec, action, str(exc), raw))
    return None


def _fragment(response: HTMLResponse) -> str:
    """A rendered panel's HTML, for embedding into the full page — the same
    helper renders the panel for its OOB write responses, so the two can
    never disagree about a panel's shape."""
    body = response.body
    return bytes(body).decode("utf-8")


def _refresh(request: Request, url: str) -> Response:
    """Answer a page-level write with the page it changed. An htmx request
    gets HX-Redirect (a full navigation, so header, panels and list rows all
    tell the new truth at once); a plain form post gets a 303."""
    if request.headers.get("HX-Request"):
        return HTMLResponse("", headers={"HX-Redirect": url})
    return RedirectResponse(url, status_code=303)


# --- the list (MarketsScreen) ------------------------------------------------


def _list_row(
    conn: sqlite3.Connection,
    org: Org,
    depth: int,
    rates: dict[str, hit_rate.MarketHitRate],
) -> dict[str, Any]:
    """One market row with the TUI list's own columns: name, type, rating,
    target lines, out, n (what the rates are out of — a bare 100% over one
    decided submission is not a hit rate anyone can act on), quote, bind."""
    profile = orgs_repo.get_market_profile(conn, org.id)
    appetite = orgs_repo.appetite_for_market(conn, org.id)
    targets = [a.line.replace("_", " ") for a in appetite if a.appetite == "target"]
    rate = rates.get(org.id)
    out = len(submissions_repo.for_market(conn, org.id, status="out"))
    return {
        "ref": org.ref,
        "name": org.name,
        "nested": depth > 0,
        "market_type": _pretty(profile.market_type if profile else None),
        "rating": (profile.am_best_rating if profile and profile.am_best_rating else "—"),
        "target_lines": ", ".join(targets) if targets else "—",
        "out": out,
        "decided": rate.decided if rate else 0,
        "quote_rate": _pct(rate.quote_rate) if rate else "—",
        "bind_rate": _pct(rate.bind_rate) if rate else "—",
    }


def _list_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Outline order, the TUI's own: master companies first, issuing
    companies nested beneath (orgs.market_families)."""
    rates = {r.market_org_id: r for r in hit_rate.by_market(conn)}
    rows: list[dict[str, Any]] = []
    for master, kids in orgs_repo.market_families(conn):
        rows.append(_list_row(conn, master, 0, rates))
        rows.extend(_list_row(conn, kid, 1, rates) for kid in kids)
    return rows


def _markets_panel(
    request: Request, *, oob: bool = False, error: str | None = None
) -> HTMLResponse:
    """The list's table + its form host, renderable standalone as an
    out-of-band swap — a successful "+ New market" replaces the panel while
    the primary swap clears .form-host (the _contacts_panel pattern; see that
    template for why success must be OOB and a refusal must not be)."""
    context = {"rows": _list_rows(_conn(request)), "oob": oob, "error": error}
    return TEMPLATES.TemplateResponse(request, "markets/_markets_panel.html", context)


@router.get("/markets", response_class=HTMLResponse)
def markets_list(request: Request) -> HTMLResponse:
    conn = _conn(request)
    rows = _list_rows(conn)
    context = {
        "rows": rows,
        "count": len(rows),
        "carriers": _unlinked_rows(request),
        "oob": False,
        "error": None,
    }
    return TEMPLATES.TemplateResponse(request, "markets/list.html", context)


# --- carriers on the towers that the book does not know ----------------------
#
# A carrier is a STRING in a towerkit file. It becomes part of the book only
# when a market org carries that name, or an alias points at one. Nothing on
# the web did that: `+ market` on a layer wrote the name into the program file
# and stopped, so a carrier bound in the browser never appeared on this tab and
# missed every cross-book join — exposure, hit rate, the market dossier (Grant,
# 2026-08-20). The terminal resolves these in the `y` sync review queue, which
# a browser does not have.
#
# The queue is REPLACED here rather than reproduced: the strings live on this
# page, beside the markets they are meant to become, with the same two answers
# the TUI offers — link it to a market that already exists, or add it as a new
# one — and nothing is ever chosen for the user. `sync.carrier_suggestions`
# already computed the fuzzy candidates for the TUI; this is its second caller.


def _unlinked_rows(request: Request) -> list[dict[str, Any]]:
    from ... import sync

    return [
        {
            "name": suggestion.carrier,
            "candidates": [
                {"ref": org.ref, "id": org.id, "name": org.name, "score": round(score)}
                for org, score in suggestion.candidates
            ],
        }
        for suggestion in sync.carrier_suggestions(_conn(request))
    ]


def _unlinked_panel(
    request: Request, *, oob: bool = False, error: str | None = None
) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request, "markets/_unlinked_panel.html",
        {"carriers": _unlinked_rows(request), "oob": oob, "error": error},
    )


@router.get("/markets/unlinked", response_class=HTMLResponse)
def unlinked_carriers(request: Request) -> HTMLResponse:
    return _unlinked_panel(request)


@router.post("/markets/unlinked/create", response_class=HTMLResponse)
async def unlinked_create(request: Request) -> HTMLResponse:
    """"This really is a market we do not have yet" — create it under the
    EXACT tower spelling, so it matches by name with no alias needed."""
    from ... import sync

    conn = _conn(request)
    carrier = str((await request.form()).get("carrier", "")).strip()
    if not carrier:
        return _unlinked_panel(request, error="no carrier named")
    if carrier not in aliases_repo.unresolved_carriers(conn):
        # Already resolved by another tab, or never unresolved. Re-render
        # rather than create a duplicate market for a name that now matches.
        return _both_panels(request)
    with batches_svc.open_batch(
        conn, source="web", tool="market_create",
        summary=f"added {carrier} to the book",
    ):
        sync.create_market_for_carrier(conn, carrier)
    return _both_panels(request)


@router.post("/markets/unlinked/link", response_class=HTMLResponse)
async def unlinked_link(request: Request) -> HTMLResponse:
    """"It is a market we already have, spelled differently" — an alias, so
    every tower carrying the old spelling joins to the right market."""
    from ... import sync

    conn = _conn(request)
    form = await request.form()
    carrier = str(form.get("carrier", "")).strip()
    org_id = str(form.get("org_id", "")).strip()
    if not carrier or not org_id:
        return _unlinked_panel(request, error="pick a market to link it to")
    try:
        market = orgs_repo.get(conn, org_id)
    except KeyError:
        return _unlinked_panel(request, error="that market no longer exists")
    if market.kind != "market":
        return _unlinked_panel(request, error=f"{market.name} is not a market")
    with batches_svc.open_batch(
        conn, source="web", tool="market_alias",
        summary=f"{carrier} is {market.name}", org_id=market.id,
    ):
        sync.alias_carrier(conn, carrier, market.id)
    return _both_panels(request)


def _both_panels(request: Request) -> HTMLResponse:
    """Resolving a carrier changes BOTH lists — the string leaves the unlinked
    panel and (when created) joins the market table above it. Answering with
    one of them leaves the other showing a state that is no longer true.

    ONE element, retargeted, not two glued together: see CLAUDE.md's
    "ONE RESPONSE, ONE TOP-LEVEL ELEMENT" and routes/program.py `_panel` for
    what the glued shape does to a response the moment its first tag decides
    the parse context."""
    conn = _conn(request)

    response = TEMPLATES.TemplateResponse(
        request, "markets/_markets_body.html",
        {
            "rows": _list_rows(conn),
            "carriers": _unlinked_rows(request),
            "error": None,
            "oob": False,
        },
    )
    response.headers["HX-Retarget"] = "#markets-body"
    response.headers["HX-Reswap"] = "outerHTML"
    return response


@router.get("/markets/new", response_class=HTMLResponse)
def market_new_form(request: Request) -> HTMLResponse:
    spec = org_form(default_kind="market", conn=_conn(request))
    return HTMLResponse(render_form(request, spec, "/markets/new"))


@router.post("/markets/new", response_class=HTMLResponse)
async def market_create(request: Request) -> HTMLResponse:
    """The TUI's `a` (action_new_market): org_form(default_kind="market"),
    committed through the same apply_org. org_id lands on the batch after the
    write — there is no org to name until apply_org returns — so the batch
    carries None, exactly as the TUI's FormModal-derived batch does here."""
    conn = _conn(request)
    spec = org_form(default_kind="market", conn=conn)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    refused = _form_save(
        request, spec, "/markets/new", raw, lambda values: apply_org(conn, values)
    )
    return refused or _markets_panel(request, oob=True)


# --- the detail (MarketDetailScreen) -----------------------------------------


def _appetite_panel(
    request: Request, market: Org, *, oob: bool = False, error: str | None = None
) -> HTMLResponse:
    conn = _conn(request)
    rows = [
        {
            "id": a.id,
            "line": _pretty(a.line),
            "appetite": _pretty(a.appetite),
            "appetite_class": _APPETITE_CLASS.get(a.appetite, ""),
            "min_premium": _compact(a.min_premium),
            "max_limit": _compact(a.max_limit),
            "territories": a.territories or "—",
        }
        for a in orgs_repo.appetite_for_market(conn, market.id)
    ]
    context = {"market": market, "rows": rows, "oob": oob, "error": error}
    return TEMPLATES.TemplateResponse(request, "markets/_appetite_panel.html", context)


def _uw_panel(
    request: Request, market: Org, *, oob: bool = False, error: str | None = None
) -> HTMLResponse:
    conn = _conn(request)
    rows = [
        {
            "id": c.id,
            "name": c.name,
            "title": c.title or "—",
            "email": c.email or "—",
            "phone": c.phone or c.mobile or "—",
        }
        for c in contacts_repo.for_org(conn, market.id)
    ]
    context = {"market": market, "rows": rows, "oob": oob, "error": error}
    return TEMPLATES.TemplateResponse(request, "markets/_uw_panel.html", context)


def _aliases_panel(
    request: Request, market: Org, *, oob: bool = False, error: str | None = None
) -> HTMLResponse:
    context = {
        "market": market,
        "aliases": aliases_repo.for_market(_conn(request), market.id),
        "oob": oob,
        "error": error,
    }
    return TEMPLATES.TemplateResponse(request, "markets/_aliases_panel.html", context)


@router.get("/markets/{ref}", response_class=HTMLResponse)
def market_detail(request: Request, ref: str) -> HTMLResponse:
    """One market before a meeting, the TUI detail screen's dossier: header
    (type, rating, hit rate, aliases), appetite, underwriters, live
    submissions, and every tower it sits on in the next 90 days."""
    conn = _conn(request)
    market = _market(request, ref)
    profile = orgs_repo.get_market_profile(conn, market.id)
    rate = next(
        (r for r in hit_rate.by_market(conn) if r.market_org_id == market.id), None
    )
    # A dead parent (merged away before merges re-parented children) floats
    # the child free — the same call market_families makes for the list.
    parent = None
    if market.parent_org_id:
        try:
            parent = orgs_repo.get(conn, market.parent_org_id)
        except KeyError:
            parent = None
    subs = [
        {
            "sent_on": s.sent_on,
            "status": s.status,
            "quoted": _compact(s.quoted_premium),
            "response_on": s.response_on or "—",
        }
        for s in submissions_repo.for_market(conn, market.id)
    ]
    # The PLACEMENT's status rides along because a QUOTED tower is exposure
    # worth seeing but is not placed business (tui/screens/markets.py).
    exposure_rows = [
        {
            "org_ref": r.org_ref,
            "org_name": r.org_name,
            "period_to": r.period_to,
            "program_name": r.program_name,
            "layer_name": r.layer_name,
            "status": r.status,
            "carrier": r.carrier if r.carrier != market.name else "—",
            "share": f"{r.share_bps / 100:g}%",
            "premium": _compact(r.premium),
        }
        for r in exposure_svc.for_market(conn, market.id, days=90)
    ]
    context = {
        "market": market,
        "kind_label": _pretty(profile.market_type if profile else None),
        "rating": profile.am_best_rating if profile else None,
        "rate": rate,
        "quote_rate": _pct(rate.quote_rate) if rate else None,
        "bind_rate": _pct(rate.bind_rate) if rate else None,
        "parent": parent,
        "aliases": aliases_repo.for_market(conn, market.id),
        "submissions": subs,
        "exposure": exposure_rows,
        "oob": False,
        "error": None,
    }
    # the three panels render through their own helpers so a write's OOB
    # response and this page can never disagree about a panel's shape
    context["appetite_panel"] = _fragment(_appetite_panel(request, market))
    context["uw_panel"] = _fragment(_uw_panel(request, market))
    context["aliases_panel"] = _fragment(_aliases_panel(request, market))
    return TEMPLATES.TemplateResponse(request, "markets/detail.html", context)


# --- edit market (the TUI's `e` on the list) ---------------------------------


@router.get("/markets/{ref}/edit", response_class=HTMLResponse)
def market_edit_form(request: Request, ref: str) -> HTMLResponse:
    market = _market(request, ref)
    spec = org_form_initial_profile(_conn(request), market)
    return HTMLResponse(render_form(request, spec, f"/markets/{ref}/edit"))


@router.post("/markets/{ref}/edit", response_class=HTMLResponse)
async def market_edit(request: Request, ref: str) -> Response:
    conn = _conn(request)
    market = _market(request, ref)
    spec = org_form_initial_profile(conn, market)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    refused = _form_save(
        request, spec, f"/markets/{ref}/edit", raw,
        lambda values: apply_org(conn, values, market),
        org_id=market.id,
    )
    # a rename touches the header, the aliases line and every list row at
    # once — the whole page is the only honest swap
    return refused or _refresh(request, f"/markets/{market.ref}")


# --- appetite (detail `a`, `e`, `D`) -----------------------------------------


def _owned_appetite(conn: sqlite3.Connection, market: Org, appetite_id: str) -> Any:
    """{ref} plus an appetite id is TWO claims and both get checked — the
    routes/account.py ownership rule, applied to this module's one
    id-carrying row kind. Unknown and foreign ids get the same 404."""
    try:
        appetite = orgs_repo.get_appetite(conn, appetite_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"no appetite {appetite_id!r} on {market.name}"
        ) from None
    if appetite.market_org_id != market.id:
        raise HTTPException(
            status_code=404, detail=f"no appetite {appetite_id!r} on {market.name}"
        )
    return appetite


@router.get("/markets/{ref}/appetite/new", response_class=HTMLResponse)
def appetite_new_form(request: Request, ref: str) -> HTMLResponse:
    _market(request, ref)
    spec = appetite_form(conn=_conn(request))
    return HTMLResponse(render_form(request, spec, f"/markets/{ref}/appetite/new"))


@router.post("/markets/{ref}/appetite/new", response_class=HTMLResponse)
async def appetite_create(request: Request, ref: str) -> HTMLResponse:
    conn = _conn(request)
    market = _market(request, ref)
    spec = appetite_form(conn=conn)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    refused = _form_save(
        request, spec, f"/markets/{ref}/appetite/new", raw,
        lambda values: orgs_repo.add_appetite(conn, market.id, **dropped(values)),
        org_id=market.id,
    )
    return refused or _appetite_panel(request, market, oob=True)


@router.get("/markets/{ref}/appetite/{appetite_id}/edit", response_class=HTMLResponse)
def appetite_edit_form(request: Request, ref: str, appetite_id: str) -> HTMLResponse:
    conn = _conn(request)
    market = _market(request, ref)
    existing = _owned_appetite(conn, market, appetite_id)
    spec = appetite_form(existing, conn=conn)
    action = f"/markets/{ref}/appetite/{appetite_id}/edit"
    return HTMLResponse(render_form(request, spec, action))


@router.post("/markets/{ref}/appetite/{appetite_id}/edit", response_class=HTMLResponse)
async def appetite_edit(request: Request, ref: str, appetite_id: str) -> HTMLResponse:
    conn = _conn(request)
    market = _market(request, ref)
    existing = _owned_appetite(conn, market, appetite_id)
    spec = appetite_form(existing, conn=conn)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    refused = _form_save(
        request, spec, f"/markets/{ref}/appetite/{appetite_id}/edit", raw,
        lambda values: orgs_repo.update_appetite(conn, existing.id, **dropped(values)),
        org_id=market.id,
    )
    return refused or _appetite_panel(request, market, oob=True)


@router.get("/markets/{ref}/appetite/{appetite_id}/remove", response_class=HTMLResponse)
def appetite_remove_confirm(request: Request, ref: str, appetite_id: str) -> HTMLResponse:
    """The confirm step — writes NOTHING, and refuses in the page. The TUI's
    `D` removes without asking (soft + `u`), but the web's undo pill lives on
    the account pages only, so here the confirm carries the weight `u` does
    there. "Already removed" (a double-clicked confirm, a second tab) answers
    as the refreshed panel with the sentence in it, never a 4xx htmx drops."""
    conn = _conn(request)
    market = _market(request, ref)
    try:
        existing = _owned_appetite(conn, market, appetite_id)
    except HTTPException:
        return _appetite_panel(
            request, market, oob=True, error="that appetite row no longer exists"
        )
    return TEMPLATES.TemplateResponse(
        request,
        "markets/_appetite_confirm_remove.html",
        {"market": market, "appetite": existing, "line": _pretty(existing.line)},
    )


@router.post("/markets/{ref}/appetite/{appetite_id}/remove", response_class=HTMLResponse)
def appetite_remove(request: Request, ref: str, appetite_id: str) -> HTMLResponse:
    """The confirmed removal — soft, one batch, the TUI's own tool name
    (tui/screens/markets.py action_delete_row)."""
    conn = _conn(request)
    market = _market(request, ref)
    try:
        _owned_appetite(conn, market, appetite_id)
    except HTTPException:
        return _appetite_panel(
            request, market, error="that appetite row no longer exists"
        )
    with batches_svc.open_batch(
        conn, source="web", tool="appetite_delete",
        summary="removed an appetite line", org_id=market.id,
    ):
        orgs_repo.delete_appetite(conn, appetite_id)
    return _appetite_panel(request, market)


# --- underwriters (detail `w`, `e`) ------------------------------------------


def _owned_contact(conn: sqlite3.Connection, market: Org, contact_id: str) -> Any:
    try:
        who = contacts_repo.get(conn, contact_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"no contact {contact_id!r} at {market.name}"
        ) from None
    if who.org_id != market.id:
        raise HTTPException(
            status_code=404, detail=f"no contact {contact_id!r} at {market.name}"
        )
    return who


@router.get("/markets/{ref}/underwriters/new", response_class=HTMLResponse)
def underwriter_new_form(request: Request, ref: str) -> HTMLResponse:
    _market(request, ref)
    spec = contact_form()
    return HTMLResponse(render_form(request, spec, f"/markets/{ref}/underwriters/new"))


@router.post("/markets/{ref}/underwriters/new", response_class=HTMLResponse)
async def underwriter_create(request: Request, ref: str) -> HTMLResponse:
    """The TUI's `w`: contact_form on the market org, role defaulting to
    underwriter when left blank — the same line its commit callback runs."""
    conn = _conn(request)
    market = _market(request, ref)
    spec = contact_form()
    raw = {k: str(v) for k, v in (await request.form()).items()}

    def write(values: dict[str, Any]) -> None:
        values["role"] = values.get("role") or "underwriter"
        apply_contact(conn, market.id, values)

    refused = _form_save(
        request, spec, f"/markets/{ref}/underwriters/new", raw, write,
        org_id=market.id,
    )
    return refused or _uw_panel(request, market, oob=True)


@router.get("/markets/{ref}/underwriters/{contact_id}/edit", response_class=HTMLResponse)
def underwriter_edit_form(request: Request, ref: str, contact_id: str) -> HTMLResponse:
    conn = _conn(request)
    market = _market(request, ref)
    who = _owned_contact(conn, market, contact_id)
    spec = contact_form(who)
    action = f"/markets/{ref}/underwriters/{contact_id}/edit"
    return HTMLResponse(render_form(request, spec, action))


@router.post("/markets/{ref}/underwriters/{contact_id}/edit", response_class=HTMLResponse)
async def underwriter_edit(request: Request, ref: str, contact_id: str) -> HTMLResponse:
    conn = _conn(request)
    market = _market(request, ref)
    who = _owned_contact(conn, market, contact_id)
    spec = contact_form(who)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    refused = _form_save(
        request, spec, f"/markets/{ref}/underwriters/{contact_id}/edit", raw,
        lambda values: apply_contact(conn, who.org_id, values, who),
        org_id=market.id,
    )
    return refused or _uw_panel(request, market, oob=True)


# --- aliases (list `A`) ------------------------------------------------------


def _alias_spec(conn: sqlite3.Connection, market: Org) -> FormSpec:
    """The TUI's action_add_alias spec, verbatim: one required field whose
    suggestions are the carrier strings on projected towers that resolve to
    nothing — the spellings that silently miss every cross-book join."""
    unresolved = tuple(aliases_repo.unresolved_carriers(conn))
    return FormSpec(
        f"alias for {market.name}",
        [Field("alias", "tower spelling", required=True,
               placeholder="exactly as the file writes it",
               suggestions=unresolved)],
    )


@router.get("/markets/{ref}/aliases/new", response_class=HTMLResponse)
def alias_new_form(request: Request, ref: str) -> HTMLResponse:
    conn = _conn(request)
    market = _market(request, ref)
    spec = _alias_spec(conn, market)
    return HTMLResponse(render_form(request, spec, f"/markets/{ref}/aliases/new"))


@router.post("/markets/{ref}/aliases/new", response_class=HTMLResponse)
async def alias_create(request: Request, ref: str) -> Response:
    conn = _conn(request)
    market = _market(request, ref)
    spec = _alias_spec(conn, market)
    raw = {k: str(v) for k, v in (await request.form()).items()}
    refused = _form_save(
        request, spec, f"/markets/{ref}/aliases/new", raw,
        lambda values: aliases_repo.set_alias(conn, values["alias"], market.id),
        org_id=market.id,
    )
    # a new spelling changes the header's "also written as" line AND the
    # exposure table (exposure.for_market searches every alias) — whole page
    return refused or _refresh(request, f"/markets/{market.ref}")


# --- merge (list `x`) --------------------------------------------------------


def _merge_targets(conn: sqlite3.Connection, source: Org) -> list[Org]:
    return [
        o for o in orgs_repo.list_orgs(conn, kind="market") if o.id != source.id
    ]


def _merge_form(
    request: Request, source: Org, *, error: str | None = None
) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "markets/_merge_form.html",
        {
            "market": source,
            "targets": _merge_targets(_conn(request), source),
            "error": error,
        },
    )


def _merge_target(request: Request, source: Org) -> Org:
    """The target named in ?target=… — validated against the freshly queried
    list of OTHER live markets, the same rule checked_option applies to a
    scoped select: membership in the scoped query IS the check, and it also
    refuses a market merged or deleted since the page rendered."""
    target_id = request.query_params.get("target", "")
    for candidate in _merge_targets(_conn(request), source):
        if candidate.id == target_id:
            return candidate
    raise MergeError("pick which market to merge into")


@router.get("/markets/{ref}/merge", response_class=HTMLResponse)
def merge_form(request: Request, ref: str) -> HTMLResponse:
    return _merge_form(request, _market(request, ref))


@router.get("/markets/{ref}/merge/confirm", response_class=HTMLResponse)
def merge_confirm(request: Request, ref: str) -> HTMLResponse:
    """The confirm step — writes NOTHING. It names the blast radius: what
    moves (contacts, appetite, submissions, aliases), and the rule that makes
    the merge safe for towers — the duplicate's NAME becomes an alias of the
    survivor, so every tower spelling keeps resolving."""
    conn = _conn(request)
    source = _market(request, ref)
    try:
        target = _merge_target(request, source)
    except MergeError as exc:
        return _merge_form(request, source, error=str(exc))
    counts = {
        "contacts": len(contacts_repo.for_org(conn, source.id)),
        "appetite": len(orgs_repo.appetite_for_market(conn, source.id)),
        "submissions": len(submissions_repo.for_market(conn, source.id)),
        "aliases": len(aliases_repo.for_market(conn, source.id)),
    }
    return TEMPLATES.TemplateResponse(
        request,
        "markets/_merge_confirm.html",
        {"market": source, "target": target, "counts": counts},
    )


@router.post("/markets/{ref}/merge/confirm", response_class=HTMLResponse)
def merge_execute(request: Request, ref: str) -> Response:
    """The confirmed merge — services.merge.merge_markets in one batch under
    the TUI's own tool name and summary (tui/screens/markets.py
    action_merge_market). Success redirects to the SURVIVOR: the source page
    this confirm rendered on no longer exists."""
    conn = _conn(request)
    source = _market(request, ref)
    try:
        target = _merge_target(request, source)
        with batches_svc.open_batch(
            conn, source="web", tool="merge_markets",
            summary="merged two markets", org_id=target.id,
        ):
            result = merge_markets(conn, source.id, target.id)
    except MergeError as exc:
        return _merge_form(request, source, error=str(exc))
    return _refresh(request, f"/markets/{result.target.ref}")


# --- nest under a master (list `N`) ------------------------------------------


def _nest_options(conn: sqlite3.Connection, market: Org) -> list[Org]:
    return [
        o for o in orgs_repo.list_orgs(conn, kind="market") if o.id != market.id
    ]


def _nest_form(
    request: Request, market: Org, *, error: str | None = None
) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        request,
        "markets/_nest_form.html",
        {
            "market": market,
            "options": _nest_options(_conn(request), market),
            "error": error,
        },
    )


@router.get("/markets/{ref}/nest", response_class=HTMLResponse)
def nest_form(request: Request, ref: str) -> HTMLResponse:
    return _nest_form(request, _market(request, ref))


@router.post("/markets/{ref}/nest", response_class=HTMLResponse)
async def nest_execute(request: Request, ref: str) -> Response:
    """The TUI's `N`: nest under a master company — creating the master on
    the spot when it doesn't exist yet (the AXA XL case) — or unnest.
    Organizational only: aliases and towers keep the issuing entity
    (orgs.set_parent's contract). A cycle is refused with set_parent's own
    sentence, in the page."""
    conn = _conn(request)
    market = _market(request, ref)
    form = await request.form()
    new_master = str(form.get("new_master", "")).strip()
    parent_id = str(form.get("parent", ""))
    try:
        if new_master:
            # the TUI reaches this path through a FormModal titled
            # "new master company for <name>" — same derived batch here
            batch = BatchSpec.for_title(
                f"new master company for {market.name}", org_id=market.id
            )
            with batches_svc.open_batch(
                conn, source="web", tool=batch.tool,
                summary=batch.sentence({}), org_id=market.id,
            ):
                master = orgs_repo.create(
                    conn, kind="market", name=new_master, status="active"
                )
                orgs_repo.set_parent(conn, market.id, master.id)
        else:
            parent: str | None = None
            if parent_id:
                candidates = {o.id for o in _nest_options(conn, market)}
                if parent_id not in candidates:
                    raise ValueError("that market is not one of the choices offered")
                parent = parent_id
            summary = (
                f"unnested {market.name}"
                if parent is None
                else f"nested {market.name} under a master company"
            )
            with batches_svc.open_batch(
                conn, source="web", tool="nest_market",
                summary=summary, org_id=market.id,
            ):
                orgs_repo.set_parent(conn, market.id, parent)
    except ValueError as exc:
        return _nest_form(request, market, error=str(exc))
    return _refresh(request, f"/markets/{market.ref}")
