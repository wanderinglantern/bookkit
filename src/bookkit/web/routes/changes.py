"""Reverting one recent change — the account page's undo surface.

Two controls, one route: the right rail's per-change `Revert` and the top
bar's `Undo <last change>` pill both POST here. Both were rendered and inert
from Task 7 until now (`aria-disabled="true"`, a title saying so); the rules
they drive have existed the whole time in services/batches.revert, which is
also what the TUI's `R` calls. The wording below tracks navigator.py's
`_apply_batch_revert` for `reverted`, `already` and `gone` — two surfaces
disagreeing about what a revert SAYS is how a user learns to distrust both.
The REFUSAL deliberately diverges: the TUI notifies a count ("N conflict(s)")
into a one-line toast, the web names the fields, because the web has room for
the sentence and a refusal says something. Claiming a mirror the code does not
keep is worse than stating the divergence (review round 1, F6).

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
token renders no toast at all — nor does a token naming a batch that does not
exist, is not this account's, or is not in the state the token claims. That
last set of checks is not belt-and-braces: `reverted` used to interpolate the
`undo` param straight into the sentence, so
`?outcome=reverted&undo=SECURITY+NOTICE+call+555-0100&n=9999` rendered
attacker-chosen prose plus a fabricated success claim inside BookKit's own
toast — the exact thing the paragraph above says the design forbids (review
round 1, F1).

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
from ...services import program_files
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
        # The same ownership rule account.py's `_owned` now applies to every
        # other {ref}-plus-an-id route, kept HERE rather than moved onto it: a
        # batch is not one of the four entities that guard resolves, and this
        # route answers an unknown ref with a redirect + toast (above) rather
        # than the guard's flat 404, because a stale ref is a stale page.
        raise HTTPException(
            status_code=404, detail=f"{batch_ref} is not a change on {ref}"
        )
    if batch.tool.startswith("program_"):
        # Decided from the BATCH, not from an exception class. This used to be
        # `except ValueError` around the revert call below, on the assumption
        # that the program-file refusal is the only ValueError revert can
        # raise. It is not: pydantic's ValidationError subclasses ValueError
        # and repo/base.py raises bare ones — one malformed event_log row under
        # an `edit_contact` batch was enough to make the page state, with
        # confidence, that a contact edit "wrote a towerkit program FILE"
        # (review round 1, F2).
        #
        # It used to STOP here and say batch undo cannot restore a file, which
        # was true and useless: the file-side revert has existed on the MCP
        # server since program writes shipped. The rail is the second caller of
        # that one service now (2026-08-19).
        try:
            program_files.revert_file(conn, batch)
        except ValueError:
            return _redirect(ref, tab, batch_ref, "filerefused")
        return _redirect(ref, tab, batch_ref, "filereverted")

    try:
        result = batches_svc.revert(conn, batch_ref, now=db.utc_now())
    except batches_svc.AlreadyReverted:
        # someone else (the TUI, the MCP server, a second tab) got there first
        return _redirect(ref, tab, batch_ref, "already")
    except KeyError:
        # NOT dead despite the successful get_by_ref above: the connection is
        # autocommit and the MCP server or a TUI session can hard-delete the
        # batch between those two reads. Rare, real, and cheaper to answer
        # than to lose.
        #
        # But KeyError is no more the batch's signature than ValueError was
        # the program file's (F2): base.update, base.undelete and
        # ENTITY_TABLES[entity_type] all raise KeyError from INSIDE revert
        # for a missing ENTITY, and answering those with "that change no
        # longer exists" says it about a batch that is still there (review
        # round 2, B). So decide from the data here too — re-read the ref,
        # and only claim it is gone when it actually is.
        try:
            batches_repo.get_by_ref(conn, batch_ref)
        except KeyError:
            return _redirect(ref, tab, batch_ref, "gone")
        raise  # the batch is still here; this KeyError came from elsewhere

    if result.applied:
        return _redirect(ref, tab, batch_ref, "reverted", len(result.reverted))
    return _redirect(ref, tab, batch_ref, "refused", len(result.refused))


def _refusal_text(conn: Connection, batch: EventBatch) -> str:
    """A REFUSAL SAYS SOMETHING (CLAUDE.md): the toast names WHAT conflicts,
    not just how many. The batch is still unreverted — that is what refused
    means — so re-planning it here yields the same conflicts the POST hit.

    Conflicts are deduped by (entity_type, field) first, in order. One clause
    per Conflict meant two contacts conflicting on `title` printed "contact
    title changed since, contact title changed since" — which reads as a
    rendering fault and still names neither record, so it was strictly worse
    than saying it once (review round 1, F5). The `+N more` count follows the
    deduped list, or it would promise clauses that do not exist."""
    pairs: list[tuple[str, str]] = []
    for conflict in batches_svc.plan_revert(conn, batch).conflicts:
        pair = (conflict.change.entity_type, conflict.change.field)
        if pair not in pairs:
            pairs.append(pair)
    named = ", ".join(
        f"{entity_type} {field} changed since"
        for entity_type, field in pairs[:_NAMED_CONFLICTS]
    )
    extra = len(pairs) - _NAMED_CONFLICTS
    if extra > 0:
        named = f"{named}, +{extra} more"
    return f"{batch.ref} refused — {named}"


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
    query string.

    Every token names a batch, and every one is checked against the book
    before a word is rendered: the batch must exist, must belong to THIS
    account, and must be in the state its token claims. `gone` is the same
    rule read backwards — it is the claim that the ref resolves to nothing,
    so it renders only when the ref resolves to nothing. None renders nothing
    at all — silence beats a sentence a crafted link chose.

    Nothing from the query string reaches the text. `batch.ref` is printed,
    not `params["undo"]`, so the ref in the toast is one the database
    confirmed; the only query value that survives is `n`, an int, on a batch
    already proven reverted (review round 1, F1).

    `remedy` is rendered as a second line: an error says how to fix it, and
    the fix for a conflict (force) lives on the other surface this slice."""
    outcome = params.get("outcome")

    if outcome == "gone":
        # Fixed prose, nothing interpolated into it — but the CLAIM still has
        # to be true. `?outcome=gone&undo=<a live ref>` used to render "that
        # change no longer exists" while the Recent changes rail listed that
        # very batch one screenful away: two contradictory sentences on one
        # page (review round 2, D). The ref genuinely failing to resolve — or
        # belonging to another account — is the condition the route redirects
        # on, so it is the condition rendered on.
        try:
            batch = batches_repo.get_by_ref(conn, params.get("undo", ""))
        except KeyError:
            return {"text": "that change no longer exists", "remedy": None}
        if batch.org_id != org.id:
            return {"text": "that change no longer exists", "remedy": None}
        return None
    if outcome not in (
        "reverted", "refused", "already", "program", "filereverted", "filerefused"
    ):
        return None

    try:
        batch = batches_repo.get_by_ref(conn, params.get("undo", ""))
    except KeyError:
        return None
    if batch.org_id != org.id:
        return None

    if outcome == "already":
        if batch.reverted_at is None:
            return None
        return {"text": "already reverted", "remedy": None}
    if outcome == "reverted":
        # reverted_at is what makes the count a report rather than a claim: a
        # crafted `n` can only inflate the tally of a revert that did happen,
        # on this account's own batch. Inflation is accepted; a NEGATIVE count
        # is not — "reverted — -9999 change(s)" describes no batch state that
        # can exist (review round 2, E).
        count = _int(params, "n")
        if count is None or count < 0 or batch.reverted_at is None:
            return None
        return {"text": f"{batch.ref} reverted — {count} change(s)", "remedy": None}
    if outcome in ("filereverted", "filerefused"):
        # Both claims are checked against the book before a word is rendered,
        # the same rule every other token here obeys: the batch must be a
        # program write, and must be in the state its token claims.
        if not batch.tool.startswith("program_"):
            return None
        if outcome == "filereverted":
            if batch.reverted_at is None:
                return None
            return {
                "text": f"{batch.ref} put back — the program file was restored",
                "remedy": None,
            }
        if batch.reverted_at is not None:
            return None
        return {
            "text": f"{batch.ref} was not put back",
            "remedy": (
                "bookkit keeps a copy of the program file as each change left "
                "it, and this one no longer matches what is on disk — something "
                "edited it since. Fix it in towerkit, or revert the newer "
                "changes first."
            ),
        }
    if outcome == "program":
        if not batch.tool.startswith("program_"):
            return None
        # the sentence services/batches.revert itself raises, never a
        # second copy of it living in the web layer
        return {"text": batches_svc.program_file_refusal(batch.ref), "remedy": None}
    # refused. Still unreverted is what refused MEANS, and _refusal_text
    # re-plans the batch to name the conflicts — replanning a reverted batch
    # would conflict on everything and print a sentence about nothing.
    if batch.reverted_at is not None:
        return None
    return {"text": _refusal_text(conn, batch), "remedy": _REMEDY}
