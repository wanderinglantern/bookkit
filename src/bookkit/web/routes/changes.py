"""Reverting one recent change — the account page's undo surface.

Two controls, one route: the right rail's per-change `Revert` and the top
bar's `Undo <last change>` pill both POST here. Both were rendered and inert
from Task 7 until now (`aria-disabled="true"`, a title saying so); the rules
they drive have existed the whole time in services/batches.revert, which is
also what the TUI's `R` calls. The exception handling and the message wording
below deliberately mirror navigator.py's `_apply_batch_revert`: two surfaces
disagreeing about what a revert SAYS is how a user learns to distrust both.

Three things this module owns that the service does not:

**Authorization.** The batch is looked up by ref and refused with a 404 when
`batch.org_id` is not this account's. `batch_ref` is a guessable sequence
(MCP-0001, MCP-0002, ...), so without the check a crafted URL on account A
reverts a write that happened on account B — the service has no idea which
account page the request came from and cannot make this call for us.

**A redirect, not a fragment swap.** Every other web write swaps one panel by
id. That idiom is wrong here: a revert can move a panel, the header badge, the
tab counts and the whole right rail at once, and swapping one of them would
leave the rest of the page reading the pre-revert book. The response is
`204 + HX-Redirect`, so htmx does a full navigation and nothing can be stale.

**The message.** The URL carries a short token, never the sentence: a message
passed through a query string is a message a crafted link can put on the page.
`toast_for` below is the one place the text lives, and an unknown or absent
token renders no toast at all.

Force is deliberately not offered here — see parity.IMPLEMENTED["undo"].
"""

from __future__ import annotations

from collections.abc import Mapping
from sqlite3 import Connection
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request, Response

from ...models import EventBatch, Org
from ...repo import batches as batches_repo
from ...services import batches as batches_svc
from .account import DEFAULT_TAB, TABS, _conn, _org

router = APIRouter()

# The redirect target is a tab PAGE url, so the valid set is the tab bar's own
# ids — NOT routes.account._PANEL_TEMPLATE, which deliberately omits
# "relationship" (routes/relationship.py registers that page itself; see
# account.py's module docstring). Validating against _PANEL_TEMPLATE would 404
# every revert issued from the default tab, which is the one the rail's Revert
# link is most often clicked on.
_TAB_IDS = frozenset(tab_id for tab_id, _label in TABS)

_REMEDY = "revert it from the TUI with R to force past the conflict"

# How many conflicts the refusal toast names before it starts counting.
_NAMED_CONFLICTS = 3


def _redirect(ref: str, tab: str, batch_ref: str, outcome: str, n: int | None = None) -> Response:
    query: dict[str, str] = {"undo": batch_ref, "outcome": outcome}
    if n is not None:
        query["n"] = str(n)
    return Response(
        status_code=204,
        headers={"HX-Redirect": f"/accounts/{ref}/{tab}?{urlencode(query)}"},
    )


@router.post("/accounts/{ref}/changes/{batch_ref}/revert")
def revert_change(
    request: Request, ref: str, batch_ref: str, tab: str = DEFAULT_TAB
) -> Response:
    """`tab` only ever builds the redirect target — it is validated here so a
    crafted value cannot reach a template name, and never used for anything
    else."""
    from ... import db

    conn = _conn(request)
    org = _org(request, ref)
    if tab not in _TAB_IDS:
        raise HTTPException(status_code=404, detail=f"no such tab {tab!r}")

    try:
        batch = batches_repo.get_by_ref(conn, batch_ref)
    except KeyError:
        # An unknown ref is a stale page, not an intrusion — the TUI says the
        # same thing when a row key goes stale mid-rebuild.
        return _redirect(ref, tab, batch_ref, "gone")
    if batch.org_id != org.id:
        raise HTTPException(
            status_code=404, detail=f"{batch_ref} is not a change on {ref}"
        )

    try:
        result = batches_svc.revert(conn, batch_ref, now=db.utc_now())
    except batches_svc.AlreadyReverted:
        # someone else (the TUI, the MCP server, a second tab) got there first
        return _redirect(ref, tab, batch_ref, "already")
    except KeyError:
        return _redirect(ref, tab, batch_ref, "gone")
    except ValueError:
        # a program_* batch wrote a towerkit FILE; the sentence is re-derived
        # in toast_for from the same service function that raised here
        return _redirect(ref, tab, batch_ref, "program")

    if result.applied:
        return _redirect(ref, tab, batch_ref, "reverted", len(result.reverted))
    return _redirect(ref, tab, batch_ref, "refused", len(result.refused))


def _refusal_text(conn: Connection, batch_ref: str, batch: EventBatch) -> str:
    """A REFUSAL SAYS SOMETHING (CLAUDE.md): the toast names WHAT conflicts,
    not just how many. The batch is still unreverted — that is what refused
    means — so re-planning it here yields the same conflicts the POST hit."""
    conflicts = batches_svc.plan_revert(conn, batch).conflicts
    named = ", ".join(
        f"{c.change.entity_type} {c.change.field} changed since"
        for c in conflicts[:_NAMED_CONFLICTS]
    )
    extra = len(conflicts) - _NAMED_CONFLICTS
    if extra > 0:
        named = f"{named}, +{extra} more"
    return f"{batch_ref} refused — {named}"


def _int(params: Mapping[str, str], key: str) -> int | None:
    raw = params.get(key)
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def toast_for(
    conn: Connection, org: Org, params: Mapping[str, str]
) -> dict[str, str | None] | None:
    """The one home for what a revert says, read back off the redirect's own
    query string. None renders nothing at all — an unknown token, a missing
    count, or a batch that is not this account's is silently no toast rather
    than a message a crafted link chose.

    `remedy` is rendered as a second line: an error says how to fix it, and
    the fix for a conflict (force) lives on the other surface this slice."""
    outcome = params.get("outcome")
    batch_ref = params.get("undo", "")

    if outcome == "already":
        return {"text": "already reverted", "remedy": None}
    if outcome == "gone":
        return {"text": "that change no longer exists", "remedy": None}
    if outcome == "reverted":
        count = _int(params, "n")
        if count is None:
            return None
        return {"text": f"{batch_ref} reverted — {count} change(s)", "remedy": None}
    if outcome in ("refused", "program"):
        try:
            batch = batches_repo.get_by_ref(conn, batch_ref)
        except KeyError:
            return None
        if batch.org_id != org.id:
            return None
        if outcome == "program":
            # the sentence services/batches.revert itself raises, never a
            # second copy of it living in the web layer
            return {"text": batches_svc.program_file_refusal(batch_ref), "remedy": None}
        return {"text": _refusal_text(conn, batch_ref, batch), "remedy": _REMEDY}
    return None
