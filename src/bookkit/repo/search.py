"""Global FTS5 search across orgs, contacts, interactions — grouped by type,
plus the one thing FTS cannot answer: an email address.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchHit:
    kind: str  # 'org' | 'contact' | 'interaction'
    entity_id: str
    org_id: str  # owning org (== entity_id for orgs)
    title: str
    snippet: str
    rank: float


# short enough to be a fragment of anybody's address ("co", "io"): matching
# those against every stored email returns the whole book, ranked last, and
# buries the hits that mean something.
EMAIL_MIN_TERM = 3

_LIKE_SPECIALS = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})


def _contact_title(row: sqlite3.Row) -> str:
    """A contact reads as a person AND the account they belong to. Five people
    called Chen render as five identical rows without it, which is a list you
    cannot pick from — and the org is what the enter key opens anyway."""
    title = f"{row['first_name']} {row['last_name']}  ({row['org_name']})"
    if row["job_title"]:
        title += f" — {row['job_title']}"
    return title


def _fts_query(text: str) -> str:
    """Terms become prefix matches so search-as-you-type works; FTS operators
    from user input are neutralised by quoting each term."""
    terms = [t.replace('"', "") for t in text.split() if t.replace('"', "")]
    if not terms:
        return ""
    return " ".join(f'"{t}"*' for t in terms)


def _by_email(
    conn: sqlite3.Connection, text: str, limit: int, already: set[str]
) -> list[sqlite3.Row]:
    """Contacts whose email contains every search term.

    Not in the FTS index, and deliberately not added to it: fts_contact is an
    external-content table, so giving it an email column means DROPping the
    virtual table and its three triggers and rebuilding — not an additive
    migration, and this project does not run those without a snapshot and a
    reason. A LIKE over one small table answers the question tonight, and
    answers it BETTER than the index would: FTS5 tokenises on punctuation, so
    "@harborview" and "p.raman" are not words it can be asked for, while a
    substring is a substring.
    """
    terms = [t for t in text.split() if len(t) >= EMAIL_MIN_TERM]
    if not terms:
        return []
    where = " AND ".join(
        [r"LOWER(c.email) LIKE '%' || LOWER(?) || '%' ESCAPE '\'"] * len(terms)
    )
    rows = conn.execute(
        f"""
        SELECT c.id, c.org_id, c.first_name, c.last_name, c.title AS job_title,
               c.email, o.name AS org_name
        FROM contact c JOIN org o ON o.id = c.org_id
        WHERE c.email IS NOT NULL AND c.deleted_at IS NULL AND {where}
        ORDER BY c.last_name, c.first_name, c.rowid
        LIMIT ?
        """,
        (*[t.translate(_LIKE_SPECIALS) for t in terms], limit),
    ).fetchall()
    return [r for r in rows if r["id"] not in already]


def search(conn: sqlite3.Connection, text: str, limit: int = 40) -> list[SearchHit]:
    query = _fts_query(text)
    if not query:
        return []
    hits: list[SearchHit] = []
    rows = conn.execute(
        """
        SELECT o.id, o.name, o.ref, o.kind AS org_kind, f.rank,
               snippet(fts_org, -1, '', '', ' … ', 12) AS snip
        FROM fts_org f JOIN org o ON o.rowid = f.rowid
        WHERE fts_org MATCH ? AND o.deleted_at IS NULL
        ORDER BY f.rank LIMIT ?
        """,
        (query, limit),
    ).fetchall()
    for r in rows:
        hits.append(
            SearchHit("org", r["id"], r["id"], f"{r['name']}  ({r['ref']})", r["snip"], r["rank"])
        )
    rows = conn.execute(
        """
        SELECT c.id, c.org_id, c.first_name, c.last_name, c.title AS job_title,
               o.name AS org_name, f.rank,
               snippet(fts_contact, -1, '', '', ' … ', 12) AS snip
        FROM fts_contact f
        JOIN contact c ON c.rowid = f.rowid
        JOIN org o ON o.id = c.org_id
        WHERE fts_contact MATCH ? AND c.deleted_at IS NULL
        ORDER BY f.rank LIMIT ?
        """,
        (query, limit),
    ).fetchall()
    found_contacts = {r["id"] for r in rows}
    for r in rows:
        hits.append(
            SearchHit("contact", r["id"], r["org_id"], _contact_title(r), r["snip"], r["rank"])
        )
    for r in _by_email(conn, text, limit, found_contacts):
        hits.append(
            SearchHit(
                "contact", r["id"], r["org_id"], _contact_title(r),
                # the address itself is the snippet: the row has to say WHY it
                # matched, or a hit on a name you did not type reads as a bug
                r["email"],
                # after every FTS hit, which score below zero. An address match
                # is precise but it is not ranked — bm25 and "the string is in
                # there" are not the same scale and must not be interleaved as
                # if they were.
                0.0,
            )
        )
    rows = conn.execute(
        """
        SELECT i.id, i.org_id, i.subject, i.occurred_on, f.rank,
               snippet(fts_interaction, -1, '', '', ' … ', 12) AS snip
        FROM fts_interaction f JOIN interaction i ON i.rowid = f.rowid
        WHERE fts_interaction MATCH ? AND i.deleted_at IS NULL
        ORDER BY f.rank LIMIT ?
        """,
        (query, limit),
    ).fetchall()
    for r in rows:
        hits.append(
            SearchHit(
                "interaction",
                r["id"],
                r["org_id"],
                f"{r['occurred_on']}  {r['subject']}",
                r["snip"],
                r["rank"],
            )
        )
    hits.sort(key=lambda h: h.rank)
    return hits[:limit]
