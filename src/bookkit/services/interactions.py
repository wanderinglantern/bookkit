"""Taking a logged interaction off the timeline — the rules both surfaces ask.

Deleting a relationship-log entry is how a WRONG entry gets corrected, most
often one the MCP server filed against the wrong account. It is a SOFT delete
(`repo.interactions.delete` -> `base.soft_delete`), reversible through
`base.undelete` and through `revert_batch`, and the confirm has to say so:
"delete" that is really "take it off the list" must not read as destruction.

The rules live here rather than in a surface for the reason
`services/contacts.py` gives at length: a guard — or a sentence — that lives in
one caller is one the other callers write straight past. The TUI's
`ConfirmDeleteInteraction` and the web's `_interaction_confirm_delete.html`
both render `consequences()`, so the two cannot promise different things about
one write, and both call `delete()`, so the batch (its tool AND its summary) is
identical whichever surface asked.

Like `services/contacts.py` this opens its own batch rather than leaving that
to each surface: `db.transaction` nests by JOINING, so a caller wrapping this
in its own `open_batch` would leave a second, permanently empty batch row in
the changes list. `source` is the only thing the surfaces differ on, and
`services.batches.open_batch` already takes it.
"""

from __future__ import annotations

import sqlite3

from ..repo import base
from ..repo import interactions as interactions_repo
from ..repo import orgs as orgs_repo
from . import batches as batches_svc


def _summary(subject: str, org_name: str) -> str:
    """The batch's one-liner — what `u`, `R` and the web rail all show. One
    home for it, so the TUI's changes list and the web's cannot describe the
    same write differently."""
    return f"deleted {subject} from {org_name}"


def consequences(conn: sqlite3.Connection, interaction_id: str) -> list[str]:
    """What deleting this entry will do, for the confirm step to show.

    A confirm that only asks "are you sure?" is not a confirm — the things
    worth saying are the ones invisible from the row vanishing:

    * that the delete is SOFT and undo puts it back, attendee links and all
      (the `interaction_contact` rows are never touched, so a restore brings
      back exactly the record that went);
    * that this entry is the account's LAST TOUCH, which is a number printed
      in the right rail of the very page the confirm opens on — deleting it
      moves that row to an older date, or empties it.

    Both confirming surfaces read this list rather than composing their own
    sentences (tests pin each string against BOTH renders).
    """
    interaction = interactions_repo.get(conn, interaction_id)  # KeyError if gone
    notes: list[str] = []

    # limit=2 is all this needs: the newest entry, and whatever the rail would
    # fall back to once it goes. for_org orders occurred_on DESC, created_at
    # DESC — the same read `last_for_org` and the snapshot's "last touch" use.
    recent = interactions_repo.for_org(conn, interaction.org_id, limit=2)
    if recent and recent[0].id == interaction_id:
        org = orgs_repo.get(conn, interaction.org_id)
        if len(recent) > 1:
            notes.append(
                f"this is the account's last touch — it falls back to "
                f"{recent[1].occurred_on} once this goes"
            )
        else:
            notes.append(
                f"this is the only interaction on {org.name} — the account "
                f"will show no last touch"
            )

    notes.append("soft and revertible — undo puts it back, attendees and all")
    return notes


def already_deleted(conn: sqlite3.Connection, interaction_id: str) -> str | None:
    """`delete`'s "already deleted" refusal, asked as a READ — or None.

    The web's confirm step is a GET on a url the timeline rendered while the
    entry was alive, and a stale tab clicks it after the row is gone: that GET
    has to SAY so in the page, and it cannot find out by calling `delete()` —
    a write behind a GET. So the sentence lives here, once, and `delete` raises
    this exact string.

    None means there is nothing to say: the row is alive, or there is no row at
    all — whose refusal is `delete`'s other one, and which the web's ownership
    guard answers with a 404 long before this is reached.
    """
    row = base.raw_row(conn, "interaction", interaction_id)
    if row is None or row["deleted_at"] is None:
        return None
    org = orgs_repo.get(conn, str(row["org_id"]))
    return f"{row['subject']} is already deleted from {org.name}"


def delete(conn: sqlite3.Connection, interaction_id: str, *, source: str) -> str:
    """Take one entry off the timeline, as one revertible batch. Returns the
    summary the surface can repeat back.

    Refuses by name rather than by traceback, exactly as `contacts.remove`
    does — `raw_row`, not `get`, so an entry deleted a minute ago gets
    "already deleted" rather than "not found", which is a different and less
    true thing to tell the one person most likely to ask twice.

    `source` is the surface: 'mcp' | 'tui' | 'web'.
    """
    row = base.raw_row(conn, "interaction", interaction_id)
    if row is None:
        raise ValueError(
            f"no interaction {interaction_id!r} — read the account's timeline for exact ids"
        )
    gone = already_deleted(conn, interaction_id)
    if gone is not None:
        raise ValueError(gone)

    org = orgs_repo.get(conn, str(row["org_id"]))
    summary = _summary(str(row["subject"]), org.name)
    with batches_svc.open_batch(
        conn, source=source, tool="interaction_delete", summary=summary, org_id=org.id,
    ):
        interactions_repo.delete(conn, interaction_id)
    return summary
