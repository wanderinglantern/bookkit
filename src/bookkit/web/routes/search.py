"""GET /search — the TUI's `/` modal as a page (gap 3).

repo.search is the single query engine on both surfaces: FTS5 over orgs,
contacts and interactions plus the email LIKE pass, already grouped by kind.
This route adds only two things — links and a fixed group order — and holds
no SQL of its own.

FIXED ORDER, deliberately diverging from the repo's group sort. The repo
orders groups by their best hit because its readers (the TUI modal, the CLI)
print a header on every CHANGE of kind while the list scrolls under a
highlight. A page shows all three headers at once, and a header that moves
between two searches reads as a different page — so Accounts / Contacts /
Interactions, always, with rank order kept WITHIN each group.

Every hit links to the owning ACCOUNT's relationship tab, because that is
what the TUI's enter key opens too (tui/screens/search.py dismisses with
org_id, never the entity). Refs are resolved once per unique org through
repo.orgs.get, guarded: the email pass joins org without an alive filter, so
a contact of a merged-away org can still hit, and orgs.get raising KeyError
on it must yield an UNLINKED row, not a 500 and not a dead link.

Escaping is Jinja autoescape end to end. The query is echoed into the page's
own input (reflected-XSS bait — asserted by tests/test_web_search.py's
script-tag query), and the snippets are plain text by construction:
repo.search calls snippet() with EMPTY match markers, and the email pass uses
the address itself, so there is no markup to preserve and nothing to strip.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...repo import orgs as orgs_repo
from ...repo import search as search_repo
from ..app import TEMPLATES
from .account import _conn

router = APIRouter()

# kind -> the header the page prints, in the one order they render
_SECTIONS: tuple[tuple[str, str], ...] = (
    ("org", "Accounts"),
    ("contact", "Contacts"),
    ("interaction", "Interactions"),
)

# The TUI modal shows 20 in a box the height of the screen; a page scrolls,
# so it takes the repo default.
_LIMIT = 40


@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = "") -> HTMLResponse:
    """q defaults to "" so a bare GET /search is the page with the box, never
    FastAPI's 422 for a missing required param. Whitespace-only q is the same
    page: repo.search would return [] for it anyway, but "searched" drives the
    no-matches copy and a blank query has no matches to speak of."""
    conn = _conn(request)
    text = q.strip()
    hits = search_repo.search(conn, text, limit=_LIMIT) if text else []

    refs: dict[str, str] = {}
    for org_id in {h.org_id for h in hits}:
        try:
            refs[org_id] = orgs_repo.get(conn, org_id).ref
        except KeyError:
            # merged/deleted owner — the hit renders unlinked (see module doc)
            pass

    sections = []
    for kind, label in _SECTIONS:
        rows = [
            {
                "title": h.title,
                "snippet": h.snippet,
                "href": (
                    f"/accounts/{refs[h.org_id]}/relationship"
                    if h.org_id in refs
                    else None
                ),
            }
            for h in hits
            if h.kind == kind
        ]
        if rows:
            sections.append({"label": label, "rows": rows})

    context = {
        "q": q,
        "searched": bool(text),
        "sections": sections,
    }
    return TEMPLATES.TemplateResponse(request, "search.html", context)
