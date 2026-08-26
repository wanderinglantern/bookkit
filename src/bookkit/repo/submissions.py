"""Submissions — a request to a specific market for a placement or opportunity."""

from __future__ import annotations

import sqlite3
from typing import Any

from ..models import SUBJECTIVITY_OPEN_STATUS, Subjectivity, Submission
from . import base


def _sent_guard(
    conn: sqlite3.Connection, submission_id: str, sent_on: str | None
) -> None:
    """A PACKAGE CANNOT HAVE GONE OUT AFTER AN ANSWER IT ALREADY HAS.

    `repo.marketing._reply_guard` states this rule from the reply's side and
    its refusal names correcting the send date as the other way out. The
    marketing grid's Sent cell IS that way out (2026-08-26), and it must not be
    able to walk straight into the state the other guard exists to refuse —
    which it could, because that guard only ever looks at the reply being
    typed.

    The sentence differs from `_reply_guard`'s deliberately: the field being
    corrected is the other one, so the remedy it names is the other one too.

    HERE, in repo/, for the reason repo/team.py's duplicate guard is: every
    surface that can move this column lands on `update`, and a rule beside one
    of them is a rule the next one writes past. The FUTURE-date half of the
    same field's story is not here — a wall clock in repo/ cannot know the
    caller's today (`services.consistency.check_not_future` owns it, where
    today is a parameter, the way the composer's is).
    """
    if not sent_on:
        return
    row = conn.execute(
        "SELECT MIN(responded_on) AS first_reply FROM market_response"
        f" WHERE submission_id = ? AND responded_on IS NOT NULL AND {base.alive()}",
        (submission_id,),
    ).fetchone()
    replied = row["first_reply"] if row else None
    if replied and sent_on > str(replied):
        raise ValueError(
            f"this market answered on {replied} and a package sent {sent_on} "
            f"would not have reached them yet — a market cannot answer a "
            f"submission it has not been sent. Correct the reply date on the "
            f"row if that is the one that is wrong."
        )


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


def withdrawn_for_org(conn: sqlite3.Connection, org_id: str) -> list[sqlite3.Row]:
    """The packages this client PULLED, joined for display exactly as
    `outstanding_for_org` joins the live ones.

    IT HAS TO BE READABLE SOMEWHERE OR IT CANNOT BE UNDONE. A withdrawn
    submission drops out of every Pipeline queue — `outstanding_for_org` is
    `status = 'out'` and the quotes queue is `status = 'quoted'` — so before
    this there was no surface on which a pulled package appeared at all, and
    "we withdrew the wrong one" had nowhere to be corrected from. Same shape
    as the team panel's Retired list, which exists for the same reason and
    carries the same Reactivate.

    The aliveness of both possible subjects sits in the ON clause, the rule
    `outstanding_for_org` states: a submission whose only tie to the client is
    a soft-deleted placement drops out, one with a live subject keeps it.
    """
    return conn.execute(
        f"""
        SELECT s.*, m.name AS market_name,
               COALESCE(p.program_name, o.title) AS about
        FROM submission s
        JOIN org m ON m.id = s.market_org_id
        LEFT JOIN placement p ON p.id = s.placement_id AND {base.alive('p')}
        LEFT JOIN opportunity o ON o.id = s.opportunity_id AND {base.alive('o')}
        WHERE s.status = 'withdrawn' AND {base.alive('s')}
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
    if "sent_on" in changes:
        _sent_guard(conn, sub_id, changes["sent_on"])
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


def _book_quote_sql(expiry_clause: str, order_by: str) -> str:
    """The book-wide quote queue's one SELECT, shared by the dated queue and
    the undated tail beside it.

    One statement rather than two near-copies: the two differ ONLY in which
    side of `quote_expires_on IS NULL` they take, and a second copy of nine
    joins is a place for the aliveness rules to drift apart.
    """
    return f"""
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
          AND {expiry_clause}
          AND COALESCE(p.org_id, o.org_id) IS NOT NULL
        ORDER BY {order_by}
    """


def expiring_quote_rows(conn: sqlite3.Connection, horizon: str) -> list[sqlite3.Row]:
    """Every quote in hand across the book whose expiry falls on or before the
    horizon — or is already past, so a LAPSED quote never falls off the queue.

    Soonest expiry first: the queue is read top-down and the thing lapsing
    first is the thing to do first.

    Undated quotes are not here, but they are NOT unsurfaced — see
    `undated_quote_rows` below, which the same leaf renders as its tail. The
    rule is only that no date is invented for them; being invisible is a
    different decision from being undated, and this branch once made the
    second by accident of the first.

    NB the precedent is not exact: `rfi.outstanding_rows` also drops undated
    rows, but only AFTER a fallback — `MIN(COALESCE(i.due_on, r.due_on))`
    lets an undated item inherit its request's date, so what it excludes is a
    request with no date anywhere. A quote has no second date to fall back
    to, so the same-shaped clause excludes strictly more, which is exactly
    why the tail beside it has to exist."""
    return conn.execute(
        _book_quote_sql(
            "s.quote_expires_on IS NOT NULL AND s.quote_expires_on <= ?",
            "s.quote_expires_on",
        ),
        (horizon,),
    ).fetchall()


def undated_quote_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every quote in hand across the book whose expiry NOBODY RECORDED.

    The deliberate twin of `expiring_quote_rows`, and the difference is the
    point. That one is a clock; this one is the absence of a clock, which is
    its own piece of work: somebody has to go and ask the underwriter when
    these terms die. A dated quote 200 days out is not in the queue either,
    but it will arrive there on its own — an undated one never will, so
    leaving it to arrive means leaving it to lapse unseen. Thin data
    correlates with sloppy handling, so these are over-represented among the
    quotes that actually go away.

    No window applies: there is no date to compare a window to. Oldest
    submission first, so the one that has been unanswered longest leads."""
    return conn.execute(
        _book_quote_sql("s.quote_expires_on IS NULL", "s.sent_on, s.id")
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


def sent_dates_for_placement(
    conn: sqlite3.Connection, placement_id: str
) -> dict[str, str]:
    """{submission_id: sent_on} for every live submission on a placement.

    The marketing report puts the submission date in a BLOCK HEADER, because
    one submission goes out and repeating its date down a column is the
    duplication the DRY rule names — so it needs them all at once, keyed."""
    rows = conn.execute(
        f"SELECT id, sent_on FROM submission WHERE placement_id = ? AND {base.alive()}",
        (placement_id,),
    ).fetchall()
    return {str(r["id"]): str(r["sent_on"]) for r in rows}


def open_subjectivity_counts(
    conn: sqlite3.Connection, placement_id: str
) -> dict[str, int]:
    """{submission_id: how many subjectivities are still outstanding}.

    A LEFT JOIN, so a submission with none appears with 0 rather than being
    missing — the report prints a blank cell for zero, and a KeyError for a
    quiet market is not the same thing as a market with nothing outstanding."""
    rows = conn.execute(
        "SELECT s.id AS submission_id, COUNT(sub.id) AS open_count"
        " FROM submission s LEFT JOIN submission_subjectivity sub"
        "   ON sub.submission_id = s.id AND sub.status = 'outstanding'"
        f"   AND {base.alive('sub')}"
        f" WHERE s.placement_id = ? AND {base.alive('s')} GROUP BY s.id",
        (placement_id,),
    ).fetchall()
    return {str(r["submission_id"]): int(r["open_count"]) for r in rows}
