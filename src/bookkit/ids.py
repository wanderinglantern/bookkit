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
    """Allocate the next human ref for a type from the per-type counter."""
    row = conn.execute(
        """
        INSERT INTO ref_counter (kind, next) VALUES (?, 2)
        ON CONFLICT (kind) DO UPDATE SET next = next + 1
        RETURNING next
        """,
        (kind,),
    ).fetchone()
    return f"{kind}-{row[0] - 1:04d}"
