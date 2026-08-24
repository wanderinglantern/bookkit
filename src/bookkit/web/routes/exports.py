"""The exports drawer — the scattered download links, in one place.

The system pass's finding (design 4A): four separate download anchors on
four surfaces made the LAST step of the morning the least designed part of
the app. This page gathers the EXISTING download routes — it mints no new
artifact and owns no renderer: every link below is the same anchor the Work
tab and the Program tab already serve, so a workbook downloaded from here
and one downloaded there are the same bytes from the same seam.

READ-ONLY, and plain anchors — a download is a navigation, not a swap
(DECISIONS.md). An account with no linked program simply lists no program
artifacts; that is a fact about the account, not an empty state to apologise
for.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...repo import orgs as orgs_repo
from ...repo import placements as placements_repo
from ..app import TEMPLATES

router = APIRouter()


@router.get("/exports", response_class=HTMLResponse)
def exports_page(request: Request) -> HTMLResponse:
    from .account import _conn

    conn = _conn(request)
    clients = sorted(orgs_repo.list_orgs(conn, kind="client"), key=lambda o: o.name)
    # AN ACCOUNT IS NAMED, NOT REFERENCED: the name and ref come TOGETHER
    # from labels_for and render through macros/account.html — never a
    # hand-written anchor (the Today ten-copies lesson; review S3).
    accounts = orgs_repo.labels_for(conn, {org.id for org in clients})
    rows: list[dict[str, Any]] = []
    for org in clients:
        linked = [
            p for p in placements_repo.for_org(conn, org.id) if p.program_path
        ]
        rows.append({
            "org_id": org.id,
            "ref": org.ref,
            "programs": [
                {"id": p.id, "name": p.program_name, "ref": p.ref}
                for p in linked
            ],
        })
    return TEMPLATES.TemplateResponse(
        request, "exports.html",
        {"rows": rows, "count": len(rows), "accounts": accounts},
    )
