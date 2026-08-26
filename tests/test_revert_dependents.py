"""A batch cannot take away a row that somebody else's work still hangs off.

The defect this file exists for (2026-08-26): approaching one market on three
lines of coverage in one sitting opens ONE submission — the first approach
creates it inside its own batch, the second and third join it — and reverting
the FIRST approach reported zero conflicts and soft-deleted that shared
submission out from under two live responses. The client workbook then said
those two lines had never been marketed, and the orphans were unrecoverable:
re-approaching the market mints a NEW submission, so nothing could ever adopt
them again, while `repo/lines.usage()` went on counting them and blocking the
retire guard with rows no surface could show.

The planner could not see it because the event_log records a child against the
CHILD: a row created in a later batch leaves no event on the earlier batch's
plan. So the check is derived FROM THE SCHEMA — `repo/base.child_links` walks
`PRAGMA foreign_key_list` — and the gates at the bottom of this file are what
keep it derived. The first half of this file is the behaviour; the second half
is the gate that makes the next table inherit it.

WHY REFUSE RATHER THAN CASCADE. Reverting the parent's batch could have taken
its children with it, but a revert's own writes carry no batch_id and cannot
themselves be reverted (services/batches.revert), so a cascade would destroy
two approaches the user never named, permanently. Refusing is the house
'surface, don't guess' grammar and it is not a dead end: the children were
recorded AFTER this batch, so undoing them first frees the parent, which is
ordinary last-in-first-out undo. `test_undoing_them_newest_first_...` holds
that door open.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from bookkit import db
from bookkit.repo import base, orgs, placements, projects
from bookkit.repo import batches as batches_repo
from bookkit.services import batches as batches_svc
from bookkit.services import marketing_entry

NOW = "2026-08-26T10:00:00+00:00"


@pytest.fixture
def conn(tmp_path: Path):
    connection = db.connect(tmp_path / "dependents.db")
    yield connection
    connection.close()


def _alive(conn: sqlite3.Connection, table: str) -> int:
    return int(
        conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE deleted_at IS NULL"
        ).fetchone()[0]
    )


# --- the marketing shape that found it -------------------------------------


def _three_approaches(conn: sqlite3.Connection) -> tuple[str, list[str]]:
    """Chubb, three lines of coverage, same day, one batch each — the
    documented ordinary path ("three lines entered in one sitting")."""
    client = orgs.create(conn, kind="client", name="Acme", status="active")
    placement = placements.create(
        conn, org_id=client.id, program_name="2027 casualty",
        period_from="2027-01-01", period_to="2028-01-01",
    )
    market = orgs.create(conn, kind="market", name="Chubb", status="active")
    refs = []
    for line in ("general-liability", "auto", "property"):
        with batches_svc.open_batch(
            conn, source="web", tool="market_approach",
            summary=f"approached Chubb on {line}", org_id=client.id,
        ) as batch:
            marketing_entry.approach(
                conn, placement.id, line, sent_on="2026-08-20",
                market_org_id=market.id, today="2026-08-26",
            )
        refs.append(batch.ref)
    assert _alive(conn, "submission") == 1        # the reuse rule held
    return client.id, refs


def test_reverting_the_first_approach_refuses_while_two_others_share_it(conn):
    _client, refs = _three_approaches(conn)

    result = batches_svc.revert(conn, refs[0], now=NOW)

    assert not result.applied
    assert _alive(conn, "submission") == 1
    assert _alive(conn, "market_response") == 3
    assert [c.clause for c in result.refused] == [
        "submission still has 2 market response(s) recorded against it since "
        "— undo those first"
    ]


def test_undoing_them_newest_first_leaves_nothing_behind(conn):
    """The refusal names a way out and the way out has to work, or this is a
    row that can never be undone at all."""
    _client, refs = _three_approaches(conn)

    for ref in reversed(refs):
        assert batches_svc.revert(conn, ref, now=NOW).applied, ref

    assert _alive(conn, "market_response") == 0
    assert _alive(conn, "submission") == 0


def test_forcing_past_it_still_leaves_no_orphan(conn):
    """force applies the clean changes only, and the shared submission is now
    one of the conflicted ones — so the response goes and the package it hung
    off stays alive under the two approaches that still need it."""
    _client, refs = _three_approaches(conn)

    result = batches_svc.revert(conn, refs[0], now=NOW, force=True)

    assert result.applied
    assert _alive(conn, "market_response") == 2
    assert _alive(conn, "submission") == 1
    orphans = conn.execute(
        "SELECT COUNT(*) FROM market_response r JOIN submission s"
        " ON s.id = r.submission_id"
        " WHERE r.deleted_at IS NULL AND s.deleted_at IS NOT NULL"
    ).fetchone()[0]
    assert orphans == 0


# --- what must NOT be refused ----------------------------------------------


def test_a_parent_and_child_created_in_one_batch_revert_together(conn):
    """The children this batch made are going away with it, so they are not
    holders. Without this the whole `market_approach` flow would refuse its
    own undo the moment it created a submission and a response together."""
    client = orgs.create(conn, kind="client", name="Acme", status="active")
    placement = placements.create(
        conn, org_id=client.id, program_name="2027 casualty",
        period_from="2027-01-01", period_to="2028-01-01",
    )
    market = orgs.create(conn, kind="market", name="Chubb", status="active")
    with batches_svc.open_batch(
        conn, source="web", tool="market_approach", summary="approached Chubb",
        org_id=client.id,
    ) as batch:
        marketing_entry.approach(
            conn, placement.id, "general-liability", sent_on="2026-08-20",
            market_org_id=market.id, today="2026-08-26",
        )

    plan = batches_svc.plan_revert(conn, batches_repo.get_by_ref(conn, batch.ref))
    assert plan.clean, [c.clause or c.current_value for c in plan.conflicts]
    assert batches_svc.revert(conn, batch.ref, now=NOW).applied
    assert _alive(conn, "submission") == 0
    assert _alive(conn, "market_response") == 0


def test_a_link_this_batch_is_letting_go_of_does_not_hold_it(conn):
    """`need_to_opportunity` creates an opportunity and points an EXISTING
    need at it. Reverting writes `need.opportunity_id` back to NULL in the
    same act, so the need is being RELEASED, not orphaned — reading the raw
    row alone called that a conflict and made the flow unrevertible."""
    from bookkit.repo import opportunities

    client = orgs.create(conn, kind="client", name="Acme", status="active")
    project = projects.create_project(conn, org_id=client.id, name="Acme 2027")
    need = projects.add_need(
        conn, project_id=project.id, line="Property", needed_by="2027-01-01"
    )
    with batches_svc.open_batch(
        conn, source="web", tool="need_to_opportunity",
        summary="turned a need into an opportunity", org_id=client.id,
    ) as batch:
        opportunity = opportunities.create(
            conn, org_id=client.id, title="Property", stage="identified"
        )
        base.update(
            conn, "project_need", need.id, {"opportunity_id": opportunity.id}
        )

    assert batches_svc.revert(conn, batch.ref, now=NOW).applied
    assert projects.get_need(conn, need.id).opportunity_id is None
    assert _alive(conn, "opportunity") == 0


def test_a_soft_deleted_child_does_not_hold_its_parent(conn):
    """Nothing can be orphaned by a parent whose only child is already gone."""
    _client, refs = _three_approaches(conn)
    for response_id in [
        row[0] for row in conn.execute(
            "SELECT id FROM market_response WHERE line_id != 'general-liability'"
        )
    ]:
        base.soft_delete(conn, "market_response", response_id)

    assert batches_svc.revert(conn, refs[0], now=NOW).applied
    assert _alive(conn, "submission") == 0


# --- the fixpoint ----------------------------------------------------------


def test_a_child_this_batch_can_no_longer_delete_holds_its_parent(conn):
    """The two created-row checks feed each other. A child created by this
    batch but EDITED since is refused on its own account — which means it
    survives the revert, which means its parent cannot go either, or the same
    orphan appears by the back door. Settling to a fixpoint is what catches
    the second step."""
    client = orgs.create(conn, kind="client", name="Acme", status="active")
    placement = placements.create(
        conn, org_id=client.id, program_name="2027 casualty",
        period_from="2027-01-01", period_to="2028-01-01",
    )
    market = orgs.create(conn, kind="market", name="Chubb", status="active")
    with batches_svc.open_batch(
        conn, source="web", tool="market_approach", summary="approached Chubb",
        org_id=client.id,
    ) as batch:
        approach = marketing_entry.approach(
            conn, placement.id, "general-liability", sent_on="2026-08-20",
            market_org_id=market.id, today="2026-08-26",
        )
    base.update(conn, "market_response", approach.response.id, {"status": "quoted"})

    result = batches_svc.revert(conn, batch.ref, now=NOW)

    assert not result.applied
    blocked = {c.change.entity_type for c in result.refused}
    assert blocked == {"market_response", "submission"}
    assert _alive(conn, "submission") == 1
    assert _alive(conn, "market_response") == 1


# --- the gates -------------------------------------------------------------
#
# A GATE IS ONLY AS GOOD AS WHERE IT LOOKS. These two say, in the test itself,
# exactly which foreign keys the planner can see and which it cannot — so the
# next migration either inherits the protection or forces somebody to write
# down why it does not.

_JOIN_TABLE = "join table: no id, no deleted_at, no event of its own."
_PROJECTION = (
    "a rebuildable projection of a towerkit file, not book content — it is "
    "rewritten from the file by sync.project_all, never event-logged."
)

UNWALKED = {
    ("carrier_alias", "market_org_id"): (
        "keyed by the alias string — no id, no deleted_at, so base cannot read "
        "or write one. Reverts handle aliases in their own lane, "
        "services/batches._plan_alias_moves."
    ),
    ("event_batch", "org_id"): (
        "bookkeeping ABOUT reverts, not book content. A batch row is history; "
        "it is never orphaned by the revert of another batch."
    ),
    ("interaction_contact", "interaction_id"): _JOIN_TABLE,
    ("interaction_contact", "contact_id"): _JOIN_TABLE,
    ("opportunity_line", "opportunity_id"): _JOIN_TABLE,
    ("opportunity_line", "line_id"): _JOIN_TABLE,
    ("team_assignment_line", "team_assignment_id"): _JOIN_TABLE,
    ("team_assignment_line", "line_id"): _JOIN_TABLE,
    ("market_profile", "org_id"): (
        "an extension of the org row, keyed BY org_id — it is that org's own "
        "detail, not an independent record that could be left pointing at "
        "nothing."
    ),
    ("program_link", "org_id"): (
        "the towerkit file link. A program_* batch never reaches this planner "
        "at all — services/batches.revert refuses it by name and sends it to "
        "the file-side revert."
    ),
    ("proj_layer", "placement_id"): _PROJECTION,
    ("proj_participant", "placement_id"): _PROJECTION,
    ("proj_retention", "placement_id"): _PROJECTION,
}


def test_every_foreign_key_in_the_schema_is_walked_or_named(conn):
    """The gate. Every FK in the whole database is either one the revert
    planner walks, or one named above with the reason it cannot be. A new
    migration with a foreign key turns this red, and the red test IS the
    ticket: decide which side it falls on and say so here."""
    links = base.child_links(conn)
    walked = {
        (base.ENTITY_TABLES[child], column)
        for children in links.values()
        for child, column in children
    }
    tables = [
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    ]
    seen: set[tuple[str, str]] = set()
    for table in tables:
        for row in conn.execute(f"PRAGMA foreign_key_list({table})"):
            key = (table, str(row[3]))
            seen.add(key)
            assert key in walked or key in UNWALKED, (
                f"{table}.{row[3]} -> {row[2]} is a foreign key the batch-revert "
                f"planner neither walks nor excuses. Either it points between "
                f"two ENTITY_TABLES (and is walked automatically), or add it to "
                f"UNWALKED here with the reason a revert cannot orphan through it."
            )
    assert seen >= walked, "child_links invented a link the schema does not have"
    assert not (set(UNWALKED) - seen), (
        f"UNWALKED names foreign keys that no longer exist: "
        f"{sorted(set(UNWALKED) - seen)}"
    )


def test_the_planner_asks_every_link_a_valid_question(snapshot_db: Path):
    """The structural gate above proves the MAP is complete; this proves the
    QUERIES behind it run.

    Two halves, because seeded data cannot reach all of it. EVERY link is
    EXECUTED — `live_dependents` for each parent entity issues one statement
    per link, and asking about an id nothing points at must come back empty
    rather than raise, which is what catches a column or table name the query
    cannot address. Then every link the seeded book DOES exercise is checked
    for its answer.

    Seven of the entity tables are empty in seed.py — submission_subjectivity,
    document, project, project_need, rfi_request, rfi_item, market_response,
    placement_line — so the second half reaches 11 links of 37. That is a fact
    about the sample data, not about the planner, and it is written here rather
    than left for a reader to infer from a floor number.

    IT WENT FROM 10 TO 11 ON 2026-08-26, and the eleventh is
    market_profile.org_id -> org. What a market IS (its type, its Best rating)
    was written by raw SQL outside base, so the table was in no entity map at
    all and a rating changed on the web appeared in no changes list and could
    not be reverted; migration 017 gave it the id and timestamps base needs and
    the writes now go through base.insert/update. seed.py has always written
    market profiles — what changed is that the planner can finally see them. The marketing case at
    the top of this file is the worked end-to-end proof for the link that
    matters most (market_response.submission_id), which seed.py does not
    write."""
    conn = db.connect(snapshot_db)
    try:
        links = base.child_links(conn)

        for parent_entity in base.ENTITY_TABLES:
            assert base.live_dependents(conn, parent_entity, "no-such-id", links) == [], (
                f"a stranger id holds a {parent_entity} — the query is asking "
                f"the wrong question"
            )

        exercised = 0
        for parent_entity, children in links.items():
            for child_entity, column in children:
                table = base.ENTITY_TABLES[child_entity]
                row = conn.execute(
                    f"SELECT id, {column} FROM {table}"
                    f" WHERE {column} IS NOT NULL AND {base.alive()} LIMIT 1"
                ).fetchone()
                if row is None:
                    continue
                exercised += 1
                held = base.live_dependents(conn, parent_entity, str(row[1]), links)
                assert (child_entity, str(row[0]), column) in held, (
                    f"{table}.{column} -> {parent_entity} is in the map but "
                    f"live_dependents does not find the row through it"
                )
        assert exercised == 11, (
            f"{exercised} of the planner's links are exercised by seeded data, "
            f"not 10 — seed.py started or stopped writing a kind of row. Say "
            f"which in the docstring above rather than moving the number."
        )
    finally:
        conn.close()
