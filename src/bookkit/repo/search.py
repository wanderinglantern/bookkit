"""Global FTS5 search across orgs, contacts, interactions — grouped by type."""

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


def _fts_query(text: str) -> str:
    """Terms become prefix matches so search-as-you-type works; FTS operators
    from user input are neutralised by quoting each term."""
    terms = [t.replace('"', "") for t in text.split() if t.replace('"', "")]
    if not terms:
        return ""
    return " ".join(f'"{t}"*' for t in terms)


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
        SELECT c.id, c.org_id, c.first_name, c.last_name, c.title AS job_title, f.rank,
               snippet(fts_contact, -1, '', '', ' … ', 12) AS snip
        FROM fts_contact f JOIN contact c ON c.rowid = f.rowid
        WHERE fts_contact MATCH ? AND c.deleted_at IS NULL
        ORDER BY f.rank LIMIT ?
        """,
        (query, limit),
    ).fetchall()
    for r in rows:
        title = f"{r['first_name']} {r['last_name']}"
        if r["job_title"]:
            title += f" — {r['job_title']}"
        hits.append(SearchHit("contact", r["id"], r["org_id"], title, r["snip"], r["rank"]))
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
