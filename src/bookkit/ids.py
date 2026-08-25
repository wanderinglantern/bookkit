"""ULID generation and short human refs (ACC-0042, OPP-0117, PLC-0003).

ULIDs cost nothing now and mean records can merge or sync later without
collision; refs exist because nobody types a ULID.
"""

from __future__ import annotations

import os
import sqlite3
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

ORG_REF = "ACC"
OPPORTUNITY_REF = "OPP"
PLACEMENT_REF = "PLC"
PROJECT_REF = "PRJ"
RFI_REF = "RFI"
BATCH_REF = "MCP"

REF_TABLES = {
    ORG_REF: "org",
    OPPORTUNITY_REF: "opportunity",
    PLACEMENT_REF: "placement",
    PROJECT_REF: "project",
    RFI_REF: "rfi_request",
    BATCH_REF: "event_batch",
}
"""Which table each ref kind is UNIQUE in — a ref kind that does not name one
cannot be checked, and an unchecked ref is the bug below waiting to happen."""


def new_ulid() -> str:
    """26-char Crockford-base32 ULID: 48-bit ms timestamp + 80 random bits."""
    ts = int(time.time() * 1000) & (2**48 - 1)
    rand = int.from_bytes(os.urandom(10), "big")
    value = (ts << 80) | rand
    chars = []
    for _ in range(26):
        chars.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def next_ref(conn: sqlite3.Connection, kind: str) -> str:
    """Allocate the next human ref for a type from the per-type counter.

    THE COUNTER IS A CACHE, NOT THE AUTHORITY — the `ref` column is (Grant,
    2026-08-25). Making a program died with `UNIQUE constraint failed:
    placement.ref` on his book: `ref_counter` had fallen behind the highest
    `PLC-####` already on a row, so every mint handed back a ref that was
    taken and EVERY new placement was refused — a new program, a renewal, an
    import, an adoption — with a traceback rather than a message. The counter
    is only ever advanced here, so nothing in this code desyncs it; a restored
    backup, a hand-edited row or a copied database all can, and the surface
    that fails is nowhere near the cause.

    So this refuses to hand back a ref the table already holds: it catches the
    counter up past the highest existing one and mints again. Two attempts is
    the whole story — after the catch-up the next number is free by
    construction — and a third would mean the table changed under us, which is
    worth raising over, not looping on.

    The check ignores soft deletes on purpose: UNIQUE does, so a ref on a
    deleted row is still taken.
    """
    table = REF_TABLES.get(kind)
    if table is None:
        raise KeyError(
            f"ref kind {kind!r} names no table in ids.REF_TABLES — a ref whose "
            f"uniqueness cannot be checked will collide silently"
        )
    for _ in range(2):
        candidate = _mint(conn, kind)
        if not _taken(conn, table, candidate):
            return candidate
        _catch_up(conn, kind, table)
    raise RuntimeError(
        f"{kind}: cannot allocate a free ref — {table}.ref moved under the "
        f"counter twice in a row"
    )


def _mint(conn: sqlite3.Connection, kind: str) -> str:
    row = conn.execute(
        """
        INSERT INTO ref_counter (kind, next) VALUES (?, 2)
        ON CONFLICT (kind) DO UPDATE SET next = next + 1
        RETURNING next
        """,
        (kind,),
    ).fetchone()
    return f"{kind}-{row[0] - 1:04d}"


def _taken(conn: sqlite3.Connection, table: str, ref: str) -> bool:
    return (
        conn.execute(f"SELECT 1 FROM {table} WHERE ref = ? LIMIT 1", (ref,)).fetchone()
        is not None
    )


def _catch_up(conn: sqlite3.Connection, kind: str, table: str) -> None:
    """Set the counter to one past the highest ref of this kind on the table.

    Raises the counter only — `next < ?` — because a counter that is AHEAD is
    harmless (it leaves gaps in the numbering) while one that is behind refuses
    every write, and lowering it would be this bug's own footgun.
    """
    row = conn.execute(
        f"SELECT MAX(CAST(SUBSTR(ref, ?) AS INTEGER)) FROM {table} WHERE ref LIKE ?",
        (len(kind) + 2, f"{kind}-%"),
    ).fetchone()
    highest = row[0] or 0
    conn.execute(
        "UPDATE ref_counter SET next = ? WHERE kind = ? AND next < ?",
        (highest + 1, kind, highest + 1),
    )
