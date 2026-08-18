"""Taking a contact off an account — the inverse the write never had.

Grant, 2026-08-18: the MCP server filed a wholesaler as a CLIENT contact and
no surface could correct it. `repo/contacts.delete` (a soft delete) existed and
nothing called it.

REMOVE, not deactivate. This row should never have existed on the account, so
the answer is the soft delete, reversible through `base.undelete` and through
`revert_batch`. Deactivating a contact who genuinely left the company is a
different need with its own ROADMAP entry — do not blur the two behind one
control.

The rules live here rather than in any surface for the reason team name
uniqueness had to move into repo/team.py: a guard that lives in one caller is a
guard the other two write straight past. MCP, the TUI and the web all call
`remove()`; none of them calls `repo.contacts.delete` directly.

This service opens its own batch — the one deliberate departure from the
`mcpserver._member_deactivate` precedent, where each surface opens the batch
around the repo calls. Two things force it: the is_primary clear and the delete
have to be ONE undo unit no matter which surface asked, and `db.transaction`
nests by JOINING, so a surface wrapping this in its own `open_batch` would
create a second, permanently empty batch row and leave it in the changes list.
`source` is the only thing the surfaces still differ on, which is exactly what
`services.batches.open_batch` already takes. The batch row records that source,
so MCP's per-entity `_provenance` stamp is not repeated here: it would have to
be conditional on the surface, inside a service, to stay truthful.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..repo import base
from ..repo import contacts as contacts_repo
from ..repo import interactions as interactions_repo
from ..repo import orgs as orgs_repo
from . import batches as batches_svc


def _kept(count: int, verb: str) -> str:
    """"3 interactions keep their records, without them on the attendee list".

    One home for the sentence, in the two tenses its callers need: the confirm
    step asks about a write that has not happened yet ("keep"), the result
    reports one that has ("kept"). Removing a contact does not touch a single
    interaction — attendees() is alive-filtered, so the person simply stops
    appearing on the list while the interaction_contact rows survive for an
    undelete to restore."""
    if count == 1:
        return (
            f"1 interaction {'keeps' if verb == 'keep' else verb} its record, "
            f"without them on the attendee list"
        )
    return (
        f"{count} interactions {verb} their records, "
        f"without them on the attendee list"
    )


@dataclass(frozen=True)
class Removal:
    """What a removal did, in the words every surface repeats."""

    contact_id: str
    name: str
    org_id: str
    org_name: str
    was_primary: bool
    interactions: int
    batch: str

    @property
    def summary(self) -> str:
        """The batch's own one-liner — what `u`, `R` and the web rail show."""
        return f"removed {self.name} from {self.org_name}"

    @property
    def message(self) -> str:
        """The sentence a surface says back. It names the two consequences a
        user cannot see from the row disappearing: that the account may now
        have NO primary contact (nobody is promoted in their place — that is a
        judgment, not a side effect of a delete), and that the interactions
        they attended are still there, minus their name."""
        parts = [self.summary]
        if self.was_primary:
            parts.append(f"{self.org_name} now has no primary contact")
        if self.interactions:
            parts.append(_kept(self.interactions, "kept"))
        return " — ".join(parts)


def consequences(conn: sqlite3.Connection, contact_id: str) -> list[str]:
    """What removing this contact will do, for the confirm step to show.

    Both confirming surfaces (the TUI's ConfirmRemoveContact, the web's
    _contact_confirm_remove.html) read this rather than composing their own
    sentences, so the two cannot promise different things about the same
    write. A confirm that only asks "are you sure?" is not a confirm: the
    consequences that matter are invisible from the row itself.

    That claim used to be made by three comments about each other and enforced
    by nothing — every note loop was deletable with the suite still green. It
    is pinned now: tests/test_contact_remove.py asserts each string in this
    list appears in BOTH confirms (fix round 1, 2026-08-18)."""
    contact = contacts_repo.get(conn, contact_id)          # KeyError if gone
    org = orgs_repo.get(conn, contact.org_id)
    notes: list[str] = []
    if contact.is_primary:
        notes.append(
            f"they are the PRIMARY contact — {org.name} will have none until "
            f"you set one, and nobody is promoted automatically"
        )
    attended = interactions_repo.attended_count(conn, contact_id)
    if attended:
        notes.append(_kept(attended, "keep"))
    # "undo puts them back" is true of THIS write and of nothing done after it.
    # A revert replays the batch's own field values, and plan_revert only sees
    # the entities that batch touched (services/batches.py) — so promoting
    # somebody else in the meantime and THEN undoing hands this contact their
    # star back beside the new one: two primaries until `p` settles it. The
    # promise is qualified rather than dropped, because reversibility is the
    # whole reason this control can be a soft delete; an overstated version is
    # how a user learns to stop believing the sentence at all (fix round 1).
    if contact.is_primary:
        notes.append(
            "soft and revertible — undo puts them back primary and all; make "
            "someone else primary first and undo leaves TWO primaries until "
            "you pick one"
        )
    else:
        notes.append("soft and revertible — undo puts them back")
    return notes


def remove(conn: sqlite3.Connection, contact_id: str, *, source: str) -> Removal:
    """Take one contact off their account, as one revertible batch.

    Refuses by name rather than by traceback, the way `_member_deactivate`
    does. Nothing cascades: `interactions.attendees` is alive-filtered, so a
    removed contact simply stops appearing on attendee lists while the
    interactions and the `interaction_contact` rows survive intact — which is
    what makes an undelete put the relationship back exactly as it was.

    `source` is the surface: 'mcp' | 'tui' | 'web'.
    """
    # raw_row, not contacts_repo.get: a contact removed a minute ago is dead to
    # the alive() view, and "already removed" is a far better answer than "not
    # found" for the one person most likely to ask twice.
    row = base.raw_row(conn, "contact", contact_id)
    if row is None:
        raise ValueError(
            f"no contact {contact_id!r} — read the account's contacts for exact ids"
        )
    contact = contacts_repo.get(conn, contact_id) if row["deleted_at"] is None else None
    name = f"{row['first_name']} {row['last_name']}".strip()
    org = orgs_repo.get(conn, str(row["org_id"]))
    if contact is None:
        raise ValueError(f"{name} is already removed from {org.name}")

    was_primary = bool(contact.is_primary)
    attended = interactions_repo.attended_count(conn, contact_id)
    result = Removal(
        contact_id=contact_id, name=name, org_id=org.id, org_name=org.name,
        was_primary=was_primary, interactions=attended, batch="",
    )

    with batches_svc.open_batch(
        conn, source=source, tool="contact_remove",
        summary=result.summary, org_id=org.id,
    ) as batch:
        if was_primary:
            # BEFORE the delete, and inside the same batch. base.update is
            # alive-filtered, so clearing it afterwards is impossible — and
            # leaving is_primary = 1 on a dead row makes set_primary's "exactly
            # one primary per org" quietly false while the account shows none.
            # Nobody is promoted in their place, on purpose.
            contacts_repo.update(conn, contact_id, note="removed", is_primary=0)
        contacts_repo.delete(conn, contact_id)

    return Removal(
        contact_id=contact_id, name=name, org_id=org.id, org_name=org.name,
        was_primary=was_primary, interactions=attended, batch=batch.ref,
    )
