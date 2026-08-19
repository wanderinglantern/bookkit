"""Submissions — a request to a specific market for a placement or opportunity."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..models import SUBJECTIVITY_OPEN_STATUS, Subjectivity, Submission
from . import base


def create(
    conn: sqlite3.Connection,
    market_org_id: str,
    sent_on: str,
    placement_id: str | None = None,
    opportunity_id: str | None = None,
    **fields: Any,
) -> Submission:
    if (placement_id is None) == (opportunity_id is None):
        raise ValueError("exactly one of placement_id / opportunity_id must be set")
    sub_id = base.insert(
        conn,
        "submission",
        {
            "market_org_id": market_org_id,
            "sent_on": sent_on,
            "placement_id": placement_id,
            "opportunity_id": opportunity_id,
            **fields,
        },
    )
    return get(conn, sub_id)


def get(conn: sqlite3.Connection, sub_id: str) -> Submission:
    row = base.get(conn, "submission", sub_id)
    if row is None:
        raise KeyError(f"submission {sub_id} not found")
    return Submission.from_row(row)


def for_placement(conn: sqlite3.Connection, placement_id: str) -> list[Submission]:
    rows = conn.execute(
        f"SELECT * FROM submission WHERE placement_id = ? AND {base.alive()} ORDER BY sent_on",
        (placement_id,),
    ).fetchall()
    return [Submission.from_row(r) for r in rows]


def for_opportunity(conn: sqlite3.Connection, opportunity_id: str) -> list[Submission]:
    rows = conn.execute(
        f"SELECT * FROM submission WHERE opportunity_id = ? AND {base.alive()} ORDER BY sent_on",
        (opportunity_id,),
    ).fetchall()
    return [Submission.from_row(r) for r in rows]


def for_market(
    conn: sqlite3.Connection, market_org_id: str, status: str | None = None
) -> list[Submission]:
    where = ["market_org_id = ?", base.alive()]
    params: list[Any] = [market_org_id]
    if status is not None:
        where.append("status = ?")
        params.append(status)
    rows = conn.execute(
        f"SELECT * FROM submission WHERE {' AND '.join(where)} ORDER BY sent_on DESC", params
    ).fetchall()
    return [Submission.from_row(r) for r in rows]


def outstanding(conn: sqlite3.Connection, sent_on_or_before: str | None = None) -> list[Submission]:
    """Everything still out at the market."""
    where = [base.alive(), "status = 'out'"]
    params: list[Any] = []
    if sent_on_or_before is not None:
        where.append("sent_on <= ?")
        params.append(sent_on_or_before)
    rows = conn.execute(
        f"SELECT * FROM submission WHERE {' AND '.join(where)} ORDER BY sent_on", params
    ).fetchall()
    return [Submission.from_row(r) for r in rows]


def reassign_market(conn: sqlite3.Connection, from_org_id: str, to_org_id: str) -> int:
    """Move every row to the surviving market on a merge."""
    # Row by row through base.update, not one bulk UPDATE: the move is a
    # field change like any other and must land in the event log, or the
    # merge cannot be reverted — the record would come back while the rows
    # that moved stayed moved. Same rule as rfi.reassign_market.
    rows = conn.execute(
        f"""SELECT id FROM submission
            WHERE market_org_id = ? AND {base.alive()}""",
        (from_org_id,),
    ).fetchall()
    for row in rows:
        base.update(conn, "submission", row[0], {"market_org_id": to_org_id}, "market merged")
    return len(rows)


def reassign_placement(conn: sqlite3.Connection, from_id: str, to_id: str) -> int:
    """Move every row to the surviving placement on a merge."""
    # Row by row through base.update, not one bulk UPDATE: the move is a
    # field change like any other and must land in the event log, or the
    # merge cannot be reverted — the record would come back while the rows
    # that moved stayed moved. Same rule as rfi.reassign_market.
    rows = conn.execute(
        f"""SELECT id FROM submission
            WHERE placement_id = ? AND {base.alive()}""",
        (from_id,),
    ).fetchall()
    for row in rows:
        base.update(conn, "submission", row[0], {"placement_id": to_id}, "placement merged")
    return len(rows)


def outstanding_for_org(conn: sqlite3.Connection, org_id: str) -> list[sqlite3.Row]:
    """Everything still out at market for ONE client, joined for display:
    market name plus what it's about (program name or opportunity title).

    Aliveness on both subjects sits in the ON clause (see the same rule in
    tasks.open_tasks_for_client): a submission whose only tie to the client
    is a soft-deleted placement or opportunity drops out, while one that
    still has a live subject keeps it."""
    return conn.execute(
        f"""
        SELECT s.*, m.name AS market_name,
               COALESCE(p.program_name, o.title) AS about,
               COALESCE(p.id, '') AS about_placement_id
        FROM submission s
        JOIN org m ON m.id = s.market_org_id
        LEFT JOIN placement p ON p.id = s.placement_id AND {base.alive('p')}
        LEFT JOIN opportunity o ON o.id = s.opportunity_id AND {base.alive('o')}
        WHERE s.status = 'out' AND {base.alive('s')}
          AND (p.org_id = ? OR o.org_id = ?)
        ORDER BY s.sent_on
        """,
        (org_id, org_id),
    ).fetchall()


def market_counts(
    conn: sqlite3.Connection, since: str | None = None, until: str | None = None
) -> list[sqlite3.Row]:
    """Per-market submission outcome counts over a sent_on window."""
    where = [base.alive("s")]
    params: list[Any] = []
    if since is not None:
        where.append("s.sent_on >= ?")
        params.append(since)
    if until is not None:
        where.append("s.sent_on <= ?")
        params.append(until)
    return conn.execute(
        f"""
        SELECT s.market_org_id, o.name AS market_name,
               COUNT(*) AS sent,
               SUM(CASE WHEN s.status IN ('quoted', 'bound', 'declined')
                        THEN 1 ELSE 0 END) AS decided,
               SUM(CASE WHEN s.status IN ('quoted', 'bound') THEN 1 ELSE 0 END) AS quoted,
               SUM(CASE WHEN s.status = 'bound' THEN 1 ELSE 0 END) AS bound,
               SUM(CASE WHEN s.status = 'declined' THEN 1 ELSE 0 END) AS declined
        FROM submission s JOIN org o ON o.id = s.market_org_id
        WHERE {' AND '.join(where)}
        GROUP BY s.market_org_id ORDER BY o.name
        """,
        params,
    ).fetchall()


def update(
    conn: sqlite3.Connection, sub_id: str, note: str | None = None, **changes: Any
) -> Submission:
    base.update(conn, "submission", sub_id, changes, note)
    return get(conn, sub_id)


def delete(conn: sqlite3.Connection, sub_id: str) -> None:
    base.soft_delete(conn, "submission", sub_id)


# --- quotes in hand -----------------------------------------------------------
#
# `outstanding()` above is deliberately NOT widened to include them. It answers
# "what has no answer yet", which is what services/sla.past_sla counts days
# against; a quote HAS an answer and its clock is a different clock, running to
# a different date. Widening one query to mean both would have made every SLA
# figure wrong. These are new queries beside it, not a change to it.


def quoted_rows_for_org(conn: sqlite3.Connection, org_id: str) -> list[sqlite3.Row]:
    """Every quote in hand for ONE client, joined for display, soonest expiry
    first and undated quotes last.

    Aliveness on both subjects sits in the ON clause, the same rule
    outstanding_for_org states: a quote whose only tie to the client is a
    soft-deleted placement or opportunity drops out."""
    return conn.execute(
        f"""
        SELECT s.*, m.name AS market_name,
               COALESCE(p.program_name, o.title) AS about,
               c.first_name AS uw_first, c.last_name AS uw_last, c.email AS uw_email,
               (SELECT COUNT(*) FROM submission_subjectivity sj
                 WHERE sj.submission_id = s.id AND sj.status = '{SUBJECTIVITY_OPEN_STATUS}'
                   AND {base.alive('sj')}) AS open_subjectivities,
               (SELECT COUNT(*) FROM submission_subjectivity sj
                 WHERE sj.submission_id = s.id AND {base.alive('sj')}) AS total_subjectivities
        FROM submission s
        JOIN org m ON m.id = s.market_org_id
        LEFT JOIN contact c ON c.id = s.underwriter_contact_id AND {base.alive('c')}
        LEFT JOIN placement p ON p.id = s.placement_id AND {base.alive('p')}
        LEFT JOIN opportunity o ON o.id = s.opportunity_id AND {base.alive('o')}
        WHERE s.status = 'quoted' AND {base.alive('s')}
          AND (p.org_id = ? OR o.org_id = ?)
        ORDER BY s.quote_expires_on IS NULL, s.quote_expires_on, s.sent_on
        """,
        (org_id, org_id),
    ).fetchall()


def expiring_quote_rows(conn: sqlite3.Connection, horizon: str) -> list[sqlite3.Row]:
    """Every quote in hand across the book whose expiry falls on or before the
    horizon — or is already past, so a LAPSED quote never falls off the queue.

    Undated quotes are excluded, exactly as rfi.outstanding_rows excludes an
    undated request: a quote nobody gave us an expiry for is not yet a clock,
    and inventing one would be guessing at the number the whole feature exists
    to be honest about."""
    return conn.execute(
        f"""
        SELECT s.*, m.name AS market_name,
               COALESCE(p.org_id, o.org_id) AS org_id,
               COALESCE(pc.name, oc.name)   AS org_name,
               COALESCE(p.program_name, o.title) AS about,
               c.first_name AS uw_first, c.last_name AS uw_last, c.email AS uw_email,
               (SELECT COUNT(*) FROM submission_subjectivity sj
                 WHERE sj.submission_id = s.id AND sj.status = '{SUBJECTIVITY_OPEN_STATUS}'
                   AND {base.alive('sj')}) AS open_subjectivities,
               (SELECT COUNT(*) FROM submission_subjectivity sj
                 WHERE sj.submission_id = s.id AND {base.alive('sj')}) AS total_subjectivities
        FROM submission s
        JOIN org m ON m.id = s.market_org_id
        LEFT JOIN contact c ON c.id = s.underwriter_contact_id AND {base.alive('c')}
        LEFT JOIN placement p ON p.id = s.placement_id AND {base.alive('p')}
        LEFT JOIN opportunity o ON o.id = s.opportunity_id AND {base.alive('o')}
        LEFT JOIN org pc ON pc.id = p.org_id AND {base.alive('pc')}
        LEFT JOIN org oc ON oc.id = o.org_id AND {base.alive('oc')}
        WHERE s.status = 'quoted' AND {base.alive('s')}
          AND s.quote_expires_on IS NOT NULL
          AND s.quote_expires_on <= ?
          AND COALESCE(p.org_id, o.org_id) IS NOT NULL
        ORDER BY s.quote_expires_on
        """,
        (horizon,),
    ).fetchall()


# --- subjectivities -----------------------------------------------------------
#
# Children of a submission, so they live in this module rather than one of
# their own — the same arrangement rfi_item has inside repo/rfi.py.


def add_subjectivity(
    conn: sqlite3.Connection, submission_id: str, description: str, **fields: Any
) -> Subjectivity:
    subj_id = base.insert(
        conn,
        "submission_subjectivity",
        {"submission_id": submission_id, "description": description, **fields},
    )
    return get_subjectivity(conn, subj_id)


def get_subjectivity(conn: sqlite3.Connection, subj_id: str) -> Subjectivity:
    row = base.get(conn, "submission_subjectivity", subj_id)
    if row is None:
        raise KeyError(f"subjectivity {subj_id} not found")
    return Subjectivity.from_row(row)


def subjectivities_for(
    conn: sqlite3.Connection, submission_id: str
) -> list[Subjectivity]:
    """Outstanding first, then by due date with undated last: the ones being
    chased sit at the top of the list whatever their dates say."""
    rows = conn.execute(
        f"""SELECT * FROM submission_subjectivity
            WHERE submission_id = ? AND {base.alive()}
            ORDER BY status <> '{SUBJECTIVITY_OPEN_STATUS}',
                     due_on IS NULL, due_on, created_at""",
        (submission_id,),
    ).fetchall()
    return [Subjectivity.from_row(r) for r in rows]


def update_subjectivity(
    conn: sqlite3.Connection, subj_id: str, note: str | None = None, **changes: Any
) -> Subjectivity:
    base.update(conn, "submission_subjectivity", subj_id, changes, note)
    return get_subjectivity(conn, subj_id)


def delete_subjectivity(conn: sqlite3.Connection, subj_id: str) -> None:
    base.soft_delete(conn, "submission_subjectivity", subj_id)


def subjectivity_counts(conn: sqlite3.Connection, submission_id: str) -> tuple[int, int]:
    """(still outstanding, total). Zero of zero means nobody has recorded any,
    which reads differently from 0 of 4 — every surface prints both numbers."""
    row = conn.execute(
        f"""SELECT SUM(status = '{SUBJECTIVITY_OPEN_STATUS}') AS open_count,
                   COUNT(*) AS total
            FROM submission_subjectivity
            WHERE submission_id = ? AND {base.alive()}""",
        (submission_id,),
    ).fetchone()
    return int(row["open_count"] or 0), int(row["total"] or 0)


def outstanding_subjectivity_rows_for_org(
    conn: sqlite3.Connection, org_id: str
) -> list[sqlite3.Row]:
    """Every subjectivity this client still owes a market, with enough context
    to name it on a chase list. Undated ones sort last, never out."""
    return conn.execute(
        f"""
        SELECT sj.*, m.name AS market_name,
               COALESCE(p.program_name, o.title) AS about
        FROM submission_subjectivity sj
        JOIN submission s ON s.id = sj.submission_id
        JOIN org m ON m.id = s.market_org_id
        LEFT JOIN placement p ON p.id = s.placement_id AND {base.alive('p')}
        LEFT JOIN opportunity o ON o.id = s.opportunity_id AND {base.alive('o')}
        WHERE sj.status = '{SUBJECTIVITY_OPEN_STATUS}'
          AND {base.alive('sj')} AND {base.alive('s')}
          AND (p.org_id = ? OR o.org_id = ?)
        ORDER BY sj.due_on IS NULL, sj.due_on, sj.created_at
        """,
        (org_id, org_id),
    ).fetchall()
