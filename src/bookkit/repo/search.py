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


# A floor on the SHORTEST query worth scanning every stored address for, not
# a relevance guarantee — no length can be one, since a whole book can share a
# domain ("example", 7 characters, matches every seeded contact). What it does
# buy is the fragment that is in almost everybody's address by construction:
# at 3 it let "com", "net" and "org" through, and `_by_email("com")` came back
# with the entire limit. Raised to 4, and applied to the LONGEST term rather
# than each one (see _by_email), so "raman com" still runs and still requires
# both — it is only a query made entirely of noise that is declined.
EMAIL_MIN_TERM = 4

# The rank an email hit carries. NOT 0.0, twice over. SQLite bm25 scores are
# negative and this module's contract is "lower sorts first", so any positive
# number puts an address match after every FTS hit — which is the intent, and
# what 0.0 also happened to do. What 0.0 additionally did was be FALSY, and a
# consumer written as `hit.rank or <default>` silently swapped in its default:
# tui/commands.py did exactly that and floated email hits to the TOP of the
# command palette, above every name match. A sentinel that means "unranked"
# must not also read as "no value".
EMAIL_RANK = 1.0

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
    terms = text.split()
    # EVERY term goes into the conjunction, including the short ones. Dropping
    # them answered a different question than the one asked: "zz p.raman" fell
    # back to "p.raman" alone and returned an address with no "zz" in it, a
    # hit the FTS pass — which ANDs its terms — would never have produced. The
    # length floor decides whether the pass RUNS, not which terms count.
    if not terms or max(len(t) for t in terms) < EMAIL_MIN_TERM:
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
                EMAIL_RANK,
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
    # Grouped by type, then by rank — and grouped is the load-bearing half.
    # A flat sort on rank interleaves the kinds, and both readers (the search
    # screen and the CLI) print a header on every change of kind, so a query
    # matching an org BETWEEN two contact hits printed two CONTACTS sections.
    # The groups themselves are ordered by their own best hit, so the kind
    # that answered the query best still leads.
    # NB the primary key is the kind's POSITION, not its best rank. Sorting on
    # (best_rank, rank) looks equivalent and is not: two kinds whose best hits
    # tie — which the tiny bm25 spreads on a short query do constantly — fall
    # through to the secondary key and interleave again.
    best_of_kind: dict[str, float] = {}
    for hit in hits:
        if hit.rank < best_of_kind.get(hit.kind, float("inf")):
            best_of_kind[hit.kind] = hit.rank
    order = {
        kind: position
        for position, kind in enumerate(
            sorted(best_of_kind, key=lambda k: (best_of_kind[k], k))
        )
    }
    hits.sort(key=lambda h: (order[h.kind], h.rank))
    return hits[:limit]
