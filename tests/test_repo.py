from __future__ import annotations

import ast
import re
import sqlite3
from pathlib import Path

import pytest

from bookkit.repo import (
    base,
    contacts,
    documents,
    events,
    interactions,
    opportunities,
    orgs,
    placements,
    search,
    submissions,
    tasks,
)

REPO_DIR = Path(__file__).resolve().parents[1] / "src" / "bookkit" / "repo"


def make_client(conn: sqlite3.Connection, name: str = "Atomic Industries, Inc."):
    return orgs.create(conn, kind="client", name=name, status="active", owner="grant")


def make_market(conn: sqlite3.Connection, name: str = "Swiss Re"):
    return orgs.create(conn, kind="market", name=name, status="active")


def test_org_round_trip(conn) -> None:
    org = make_client(conn)
    assert org.ref == "ACC-0001"
    got = orgs.get(conn, org.id)
    assert got.name == "Atomic Industries, Inc."
    assert orgs.find(conn, "ACC-0001").id == org.id
    assert orgs.find_by_name(conn, "Atomic Industries, Inc.").id == org.id


def test_update_writes_event_log(conn) -> None:
    org = make_client(conn)
    orgs.update(conn, org.id, status="dormant", note="gone quiet")
    log = events.field_history(conn, "org", org.id, "status")
    assert len(log) == 1
    assert (log[0].old_value, log[0].new_value, log[0].note) == (
        "active", "dormant", "gone quiet",
    )


def test_soft_delete_hides_everywhere(conn) -> None:
    org = make_client(conn)
    orgs.delete(conn, org.id)
    with pytest.raises(KeyError):
        orgs.get(conn, org.id)
    assert orgs.find(conn, org.ref) is None
    assert orgs.list_orgs(conn) == []
    assert search.search(conn, "Atomic") == []
    base.undelete(conn, "org", org.id)
    assert orgs.get(conn, org.id).name == "Atomic Industries, Inc."
    assert len(search.search(conn, "Atomic")) == 1


# Functions that query a soft-delete table on purpose without the filter.
# Keyed by (module, function) with the reason, and asserted to still exist —
# an exception that quietly stops applying is how the rule rots.
_ALIVE_EXEMPT = {
    ("orgs.py", "set_parent"): (
        "the cycle walk must follow soft-deleted ancestors: a deleted org can "
        "be undeleted, and stopping at one would let a cycle through it pass"
    ),
    ("lines.py", "_free_slug"): (
        "the slug is a PRIMARY KEY and soft-delete leaves it occupying the "
        "table, so a retired line's id must still count as taken — filtering "
        "it out would mint an id the table already holds and raise on INSERT"
    ),
}


def _functions_with_entity_selects(module: Path):
    """(name, source) for every function in `module` whose body SELECTs from a
    soft-delete table. Per FUNCTION, because the alive() filter is often built
    into a `where` fragment a few lines above the query string."""
    src = module.read_text()
    tree = ast.parse(src)
    tables = set(base.ENTITY_TABLES.values())
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        seg = ast.get_source_segment(src, fn) or ""
        if "SELECT" not in seg.upper():
            continue
        hit = {t for t in re.findall(r"(?:FROM|JOIN)\s+(\w+)", seg, re.I) if t in tables}
        if hit:
            yield fn.name, seg, sorted(hit)


def test_no_raw_soft_delete_bypass_in_repo() -> None:
    """Every SELECT in repo/ against a soft-delete table must go through
    alive(); a raw 'deleted_at' comparison outside base.py is a bug.

    This asked `"base.alive(" in text` PER FILE, so submissions.py's fifteen
    entity SELECTs were all satisfied by any ONE surviving occurrence
    (2026-08-18). Scoped to the function that owns the query instead."""
    checked = 0
    exempt_seen: set[tuple[str, str]] = set()
    for module in sorted(REPO_DIR.glob("*.py")):
        if module.name in ("base.py", "search.py"):
            continue  # base defines the filter; search filters explicitly per join
        for name, seg, tables in _functions_with_entity_selects(module):
            checked += 1
            if (module.name, name) in _ALIVE_EXEMPT:
                exempt_seen.add((module.name, name))
                continue
            assert "base.alive(" in seg, (
                f"{module.name}:{name} queries {tables} without the alive() filter"
            )
    assert checked > 50, f"only {checked} repo queries found — the scan broke"
    assert exempt_seen == set(_ALIVE_EXEMPT), (
        f"stale alive() exemption: {set(_ALIVE_EXEMPT) - exempt_seen}"
    )


def test_contacts_primary_flag(conn) -> None:
    org = make_client(conn)
    alice = contacts.create(conn, org.id, first_name="Alice", last_name="Ng", is_primary=1)
    bob = contacts.create(conn, org.id, first_name="Bob", last_name="Reyes")
    contacts.set_primary(conn, bob.id)
    roster = contacts.for_org(conn, org.id)
    assert [c.is_primary for c in roster] == [True, False]
    assert roster[0].id == bob.id
    assert contacts.get(conn, alice.id).is_primary is False


def test_interactions_with_attendees(conn) -> None:
    org = make_client(conn)
    alice = contacts.create(conn, org.id, first_name="Alice", last_name="Ng")
    meeting = interactions.log(
        conn, org.id, "meeting", "Renewal strategy", "2026-08-01",
        body="Discussed cyber tower options", contact_ids=[alice.id],
    )
    assert [c.id for c in interactions.attendees(conn, meeting.id)] == [alice.id]
    assert interactions.last_for_org(conn, org.id).id == meeting.id


def test_task_lifecycle(conn) -> None:
    org = make_client(conn)
    task = tasks.create(conn, "Chase loss runs", org_id=org.id, due_on="2026-08-20")
    assert tasks.open_tasks(conn, due_by="2026-08-25")[0].id == task.id
    assert tasks.open_tasks(conn, due_by="2026-08-15") == []
    done = tasks.complete(conn, task.id)
    assert done.status == "done" and done.completed_at is not None
    assert tasks.open_tasks(conn) == []
    reopened = tasks.reopen(conn, task.id)
    assert reopened.status == "open" and reopened.completed_at is None


def test_placement_queries(conn) -> None:
    org = make_client(conn)
    placements.create(
        conn, org.id, "Casualty", "2025-10-01", "2026-10-01",
        status="bound", total_premium=1_000_000_00,
    )
    hit = placements.expiring_between(conn, "2026-09-01", "2026-12-31")
    assert len(hit) == 1 and hit[0].ref == "PLC-0001"
    assert placements.expiring_between(conn, "2026-11-01", "2026-12-31") == []
    nxt = placements.next_renewal_for_org(conn, org.id, "2026-08-11")
    assert nxt is not None and nxt.period_to == "2026-10-01"


def test_submission_exclusive_parent(conn) -> None:
    org = make_client(conn)
    market = make_market(conn)
    placement = placements.create(conn, org.id, "Casualty", "2025-10-01", "2026-10-01")
    with pytest.raises(ValueError):
        submissions.create(conn, market.id, "2026-08-01")
    opp = opportunities.create(conn, org.id, "New cyber line")
    with pytest.raises(ValueError):
        submissions.create(
            conn, market.id, "2026-08-01",
            placement_id=placement.id, opportunity_id=opp.id,
        )
    sub = submissions.create(conn, market.id, "2026-08-01", placement_id=placement.id)
    assert submissions.for_placement(conn, placement.id)[0].id == sub.id
    assert submissions.outstanding(conn)[0].id == sub.id


def test_opportunity_stage_locked_in_repo(conn) -> None:
    org = make_client(conn)
    opp = opportunities.create(conn, org.id, "New cyber line")
    with pytest.raises(ValueError):
        opportunities.update(conn, opp.id, stage="qualified")


def test_appetite_market_search(conn) -> None:
    swiss = make_market(conn, "Swiss Re")
    chubb = make_market(conn, "Chubb")
    orgs.add_appetite(
        conn, swiss.id, line="cyber", appetite="target",
        max_limit=50_000_000_00, min_premium=100_000_00,
    )
    orgs.add_appetite(conn, chubb.id, line="cyber", appetite="no")
    got = orgs.markets_for_line(conn, "cyber", min_limit=25_000_000_00)
    assert [o.name for o, _ in got] == ["Swiss Re"]
    assert orgs.markets_for_line(conn, "cyber", min_limit=75_000_000_00) == []


def test_documents(conn) -> None:
    org = make_client(conn)
    documents.add(conn, org.id, "2026 policy", "/docs/policy.pdf", kind="policy")
    assert documents.for_org(conn, org.id)[0].path == "/docs/policy.pdf"


def test_fts_search_grouped(conn) -> None:
    org = make_client(conn)
    contacts.create(conn, org.id, first_name="Atomic", last_name="Smith")
    interactions.log(conn, org.id, "note", "Atomic renewal kickoff", "2026-08-01")
    hits = search.search(conn, "atomic")
    kinds = {h.kind for h in hits}
    assert kinds == {"org", "contact", "interaction"}
    # prefix search works
    assert search.search(conn, "atom")


def test_a_contact_hit_says_which_org_the_person_belongs_to(conn) -> None:
    """Five people called Chen render as five identical rows, and the list is
    unusable the moment two of them share a surname. The org is the one thing
    that tells them apart, and it was never composed into the hit."""
    atomic = make_client(conn)
    borealis = make_client(conn, "Borealis Foods Group")
    contacts.create(
        conn, atomic.id, first_name="Sarah", last_name="Chen", title="CFO"
    )
    contacts.create(
        conn, borealis.id, first_name="David", last_name="Chen", title="Controller"
    )
    titles = [hit.title for hit in search.search(conn, "chen") if hit.kind == "contact"]
    assert len(titles) == 2
    assert any("Atomic Industries, Inc." in title for title in titles), titles
    assert any("Borealis Foods Group" in title for title in titles), titles
    # the person still leads — the org identifies, it does not replace
    assert all(title.startswith(("Sarah Chen", "David Chen")) for title in titles), titles


def test_a_contact_is_findable_by_email(conn) -> None:
    """Typing the address you have in front of you found nothing at all: the
    contact index carries first/last/title/notes and not email."""
    org = make_client(conn, "Harborview Utilities")
    contacts.create(
        conn, org.id, first_name="Priya", last_name="Raman",
        email="p.raman@harborview.example",
    )
    whole = search.search(conn, "p.raman@harborview.example")
    assert [hit.kind for hit in whole] == ["contact"], whole
    # the local part alone, and the domain alone — an address is not one word
    assert [hit.kind for hit in search.search(conn, "p.raman")] == ["contact"]
    assert [hit.kind for hit in search.search(conn, "harborview.example")] == ["contact"]
    # and the matched address is on the row, so the reason it matched is visible
    assert "p.raman@harborview.example" in whole[0].snippet


def test_an_email_hit_is_not_a_second_copy_of_a_name_hit(conn) -> None:
    """The email pass is a fallback, not a union: a contact whose name AND
    email both match must appear once."""
    org = make_client(conn, "Ironwood Timber")
    contacts.create(
        conn, org.id, first_name="Nadia", last_name="Ironwood",
        email="nadia@ironwood.example",
    )
    hits = [hit for hit in search.search(conn, "ironwood") if hit.kind == "contact"]
    assert len(hits) == 1, hits


def test_a_removed_contact_stays_out_of_the_email_index(conn) -> None:
    """The FTS pass filters deleted_at; the email pass has to as well, or a
    removed person comes back through the other door."""
    org = make_client(conn, "Quartz Financial")
    gone = contacts.create(
        conn, org.id, first_name="Tom", last_name="Quill", email="tom@quartz.example"
    )
    base.soft_delete(conn, "contact", gone.id)
    assert search.search(conn, "tom@quartz.example") == []


def test_an_email_hit_ranks_AFTER_every_fts_hit_and_says_so_in_the_rank(
    conn,
) -> None:
    """The email pass is unranked, and unranked must sort last — bm25 and "the
    string is in there" are not the same scale.

    The rank it carries must also be TRUTHY. It was 0.0, and tui/commands.py
    read it as `min(1.0, hit.rank) if hit.rank else 0.5`: 0.0 is falsy, so an
    address match took the 0.5 default and outranked every name match in the
    command palette. A sentinel meaning "unranked" that also reads as "no
    value" is a landmine for every consumer, not just that one."""
    org = make_client(conn, "Zephyr Marine Holdings")
    contacts.create(
        conn, org.id, first_name="Zephyr", last_name="Nakamura", title="CFO"
    )
    other = make_client(conn, "Other Co")
    contacts.create(
        conn, other.id, first_name="Bill", last_name="Smith",
        email="bill@zephyr.example",
    )
    interactions.log(conn, org.id, "note", "Zephyr kickoff call", "2026-08-01")

    hits = search.search(conn, "zephyr")
    email_hit = next(h for h in hits if h.snippet == "bill@zephyr.example")
    assert email_hit.rank, "the unranked sentinel is falsy — `or default` will fire"
    fts = [h for h in hits if h is not email_hit]
    assert fts, "fixture drifted: nothing matched the index"
    assert all(h.rank < email_hit.rank for h in fts), [h.rank for h in hits]
    # last of the contacts, not last of the list — the hits are grouped by
    # kind, so "after every FTS hit" is a statement about the section it
    # belongs to. Within CONTACTS, the ranked hits come first and the address
    # match brings up the rear.
    contacts_section = [h for h in hits if h.kind == "contact"]
    assert contacts_section[-1] is email_hit, [h.title for h in contacts_section]


def test_hits_of_one_kind_arrive_together(conn) -> None:
    """Both readers — the search screen and the CLI — print a header the
    moment the kind changes, so the hit list has to be grouped by kind or a
    query prints the same header twice. A flat sort on rank does not group:
    an org scoring between a contact and an unranked email hit produced
    CONTACTS, ORGS, CONTACTS, and the reader has no way to tell that is one
    list rather than two."""
    # the shapes matter: bm25 favours the shorter document, so the long org
    # name ranks BETWEEN the contact matched by name and the one matched only
    # by address — which is exactly the arrangement a flat sort splits
    org = make_client(
        conn, "Zephyr Marine Holdings International Group Limited Partnership"
    )
    contacts.create(conn, org.id, first_name="Zephyr", last_name="Nakamura")
    other = make_client(conn, "Other Co")
    contacts.create(
        conn, other.id, first_name="Bill", last_name="Smith",
        email="bill@zephyr.example",
    )
    interactions.log(
        conn, org.id, "note",
        "Zephyr kickoff call with the whole account team present", "2026-08-01",
    )
    ranks = {h.kind: h.rank for h in sorted(search.search(conn, "zephyr"), key=lambda h: h.rank)}
    assert ranks, "fixture drifted"

    kinds = [h.kind for h in search.search(conn, "zephyr")]
    assert set(kinds) == {"org", "contact", "interaction"}, kinds
    assert kinds.count("contact") == 2, kinds  # one by name, one by address
    # each kind occupies one contiguous run, so one header is printed per kind
    runs = [k for i, k in enumerate(kinds) if i == 0 or kinds[i - 1] != k]
    assert len(runs) == len(set(runs)), f"{kinds} — a kind is split in two"


def test_a_wildcard_typed_into_the_search_box_is_a_literal(conn) -> None:
    """LIKE has its own metacharacters and a search box is user input: without
    ESCAPE, "%" matches every stored address and "_" matches any character, so
    a typo turns a precise lookup into the whole book. Nothing asserted this,
    so the next edit to that WHERE clause would have dropped it in silence."""
    org = make_client(conn, "Percentile Analytics")
    literal = contacts.create(
        conn, org.id, first_name="Ada", last_name="Percy", email="a%b@percentile.test",
    )
    contacts.create(
        conn, org.id, first_name="Bo", last_name="Quist", email="axb@percentile.test",
    )
    contacts.create(
        conn, org.id, first_name="Cy", last_name="Rand", email="ab@percentile.test",
    )

    # '%' is the character typed, not "anything at all"
    found = [h.entity_id for h in search.search(conn, "a%b@percentile.test")]
    assert found == [literal.id], found
    # and a query made only of wildcards matches the addresses that contain
    # them, which is none of these — it does not return the book, and it does
    # not raise
    assert search.search(conn, "%%%") == []
    # '_' is a literal too, or "a_b" would answer for "axb"
    assert [h.entity_id for h in search.search(conn, "a_b@percentile.test")] == []


def test_the_email_pass_declines_a_query_made_entirely_of_noise(conn) -> None:
    """EMAIL_MIN_TERM's whole job. It was 3, and reasoned about "co" and "io"
    — while "com", "net" and "org" are three characters each and sit in almost
    every address ever stored, so `_by_email("com")` came back with the entire
    limit. No length can make this a guarantee (a book sharing one domain
    matches on any term), which is why the floor is documented as a floor."""
    org = make_client(conn, "Kettleburn Logistics")
    contacts.create(
        conn, org.id, first_name="Ada", last_name="Kettle", email="ada@kettleburn.com",
    )
    assert search.search(conn, "com") == []
    assert search.search(conn, "org") == []
    # a distinctive fragment still answers
    assert [h.kind for h in search.search(conn, "kettleburn.com")] == ["contact"]


def test_every_term_has_to_be_in_the_address_not_just_the_long_ones(conn) -> None:
    """The conjunction dropped short terms instead of requiring them, so
    "zz p.raman" fell back to "p.raman" alone and returned an address with no
    "zz" anywhere in it — a hit the FTS pass, which ANDs its terms, would
    never have produced. The floor decides whether the pass RUNS; it does not
    decide which terms count."""
    org = make_client(conn, "Trellis Growers")
    contacts.create(
        conn, org.id, first_name="Ada", last_name="Trellis", email="ada@trellis.example",
    )
    assert search.search(conn, "zz ada@trellis.example") == []
    # ...and the same query without the term nobody's address contains works
    assert [h.kind for h in search.search(conn, "ada@trellis.example")] == ["contact"]


def test_fts_updates_on_edit(conn) -> None:
    org = make_client(conn)
    orgs.update(conn, org.id, name="Molecular Industries")
    assert search.search(conn, "Atomic") == []
    assert len(search.search(conn, "Molecular")) == 1


class TestProjects:
    def test_project_and_need_round_trip(self, conn) -> None:
        from bookkit.repo import projects

        org = orgs.create(conn, kind="client", name="Atomic Industries")
        project = projects.create_project(
            conn, org.id, "HQ Tower Build", site="Chicago, IL",
            status="active", start_on="2026-09-01", end_on="2028-03-01",
        )
        assert project.ref.startswith("PRJ-")
        need = projects.add_need(
            conn, project.id, "Builder's Risk", "2026-08-25",
            limit_cents=5_000_000_000, status="identified",
        )
        assert [n.id for n in projects.needs_for_project(conn, project.id)] == [need.id]
        projects.update_need(conn, need.id, status="placed")
        assert projects.get_need(conn, need.id).status == "placed"
        projects.delete_project(conn, project.id)
        assert projects.projects_for_org(conn, org.id) == []

    def test_needs_due_window_and_statuses(self, conn) -> None:
        from datetime import date

        from bookkit.repo import projects

        today = date(2026, 8, 12)
        org = orgs.create(conn, kind="client", name="Atomic")
        project = projects.create_project(conn, org.id, "Plant Expansion")
        overdue = projects.add_need(conn, project.id, "Wrap-up GL", "2026-08-01")
        soon = projects.add_need(conn, project.id, "Builder's Risk", "2026-09-15")
        projects.add_need(conn, project.id, "Marine Cargo", "2027-06-01")  # far out
        projects.add_need(conn, project.id, "Pollution", "2026-08-20", status="placed")
        rows = projects.needs_due(conn, today, days=90)
        assert [row["id"] for row in rows] == [overdue.id, soon.id]
        assert rows[0]["project_name"] == "Plant Expansion"
        assert rows[0]["org_name"] == "Atomic"


class TestMarketFamilies:
    def test_nest_unnest_and_outline(self, conn) -> None:
        axa = orgs.create(conn, kind="market", name="AXA XL")
        indian = orgs.create(conn, kind="market", name="Indian Harbor Ins Co")
        orgs.create(conn, kind="market", name="Chubb")
        orgs.set_parent(conn, indian.id, axa.id)
        assert [c.id for c in orgs.children(conn, axa.id)] == [indian.id]
        families = dict(
            (top.name, [k.name for k in kids])
            for top, kids in orgs.market_families(conn)
        )
        assert families == {"AXA XL": ["Indian Harbor Ins Co"], "Chubb": []}
        orgs.set_parent(conn, indian.id, None)  # unnest
        assert orgs.children(conn, axa.id) == []

    def test_cycles_and_self_nesting_refused(self, conn) -> None:
        import pytest as _pytest

        a = orgs.create(conn, kind="market", name="A Co")
        b = orgs.create(conn, kind="market", name="B Co")
        orgs.set_parent(conn, b.id, a.id)
        with _pytest.raises(ValueError):
            orgs.set_parent(conn, a.id, b.id)  # cycle
        with _pytest.raises(ValueError):
            orgs.set_parent(conn, a.id, a.id)  # self

    def test_deleted_parent_floats_child_to_top(self, conn) -> None:
        axa = orgs.create(conn, kind="market", name="AXA XL")
        indian = orgs.create(conn, kind="market", name="Indian Harbor Ins Co")
        orgs.set_parent(conn, indian.id, axa.id)
        orgs.delete(conn, axa.id)
        tops = [top.name for top, _ in orgs.market_families(conn)]
        assert "Indian Harbor Ins Co" in tops


class TestVocab:
    def test_lines_union_and_dedupe(self, conn) -> None:
        from bookkit.repo import projects as projects_repo
        from bookkit.repo import vocab

        market = orgs.create(conn, kind="market", name="Chubb")
        orgs.add_appetite(conn, market.id, line="Cyber", appetite="target")
        org = orgs.create(conn, kind="client", name="Atomic")
        opportunities.create(conn, org.id, "Cyber+DO", lines="cyber, D&O")
        project = projects_repo.create_project(conn, org.id, "Build")
        projects_repo.add_need(conn, project.id, "Builder's Risk", "2026-10-01")
        assert vocab.lines(conn) == ["Builder's Risk", "Cyber", "D&O"]

    def test_owner_program_and_market_vocab(self, conn) -> None:
        from bookkit.repo import vocab

        org = orgs.create(conn, kind="client", name="Atomic", owner="grant")
        orgs.create(conn, kind="client", name="Borealis", owner="Grant")  # dupe, case
        orgs.create(conn, kind="market", name="AXA XL")
        placements.create(conn, org.id, "2026 Property", "2026-01-01", "2027-01-01")
        assert vocab.owners(conn) == ["grant"]
        assert vocab.program_names(conn) == ["2026 Property"]
        assert vocab.market_names(conn) == ["AXA XL"]


def test_task_description_round_trips(conn):
    task = tasks.create(
        conn, "chase GL quote",
        description="waiting on Zurich since Monday",
        detail="## Notes\n- called 8/10, no answer\n- try the London desk",
    )
    got = tasks.get(conn, task.id)
    assert got.description == "waiting on Zurich since Monday"
    assert got.detail.startswith("## Notes")


def test_outstanding_for_org_joins_market_and_subject(conn):
    client = orgs.create(conn, kind="client", name="Acme")
    market = orgs.create(conn, kind="market", name="Zurich")
    p = placements.create(conn, client.id, "Acme Property 25-26",
                          "2025-10-01", "2026-10-01")
    submissions.create(conn, market.id, "2026-08-01", placement_id=p.id)
    rows = submissions.outstanding_for_org(conn, client.id)
    assert len(rows) == 1
    assert rows[0]["market_name"] == "Zurich"
    assert rows[0]["about"] == "Acme Property 25-26"


def test_open_tasks_for_client_covers_org_and_placement_ownership(conn):
    client = orgs.create(conn, kind="client", name="Acme")
    other = orgs.create(conn, kind="client", name="Other Co")
    p = placements.create(conn, client.id, "Acme Property 25-26",
                          "2025-10-01", "2026-10-01")
    other_p = placements.create(conn, other.id, "Other Co GL",
                                "2025-10-01", "2026-10-01")

    org_only = tasks.create(conn, "org-only task", org_id=client.id)
    placement_only = tasks.create(conn, "placement-only task", placement_id=p.id)
    both = tasks.create(conn, "org and placement task", org_id=client.id, placement_id=p.id)
    other_org_task = tasks.create(conn, "other client's task", org_id=other.id)
    other_placement_task = tasks.create(conn, "other client's placement task",
                                        placement_id=other_p.id)

    got = {t.id for t in tasks.open_tasks_for_client(conn, client.id)}
    assert got == {org_only.id, placement_only.id, both.id}
    assert other_org_task.id not in got
    assert other_placement_task.id not in got


def test_open_tasks_for_client_drops_tasks_held_only_by_a_dead_placement(conn):
    """A soft-deleted placement must not carry its tasks onto a client-facing
    surface (the export sheet, the account tab, MCP open_items). A task that
    ALSO names the org directly still belongs to the client and stays."""
    client = orgs.create(conn, kind="client", name="Acme")
    dead_p = placements.create(conn, client.id, "Acme Property 25-26",
                               "2025-10-01", "2026-10-01")
    placement_only = tasks.create(conn, "orphaned by the delete",
                                  placement_id=dead_p.id)
    also_org = tasks.create(conn, "still the client's",
                            org_id=client.id, placement_id=dead_p.id)
    placements.delete(conn, dead_p.id)

    got = {t.id for t in tasks.open_tasks_for_client(conn, client.id)}
    assert placement_only.id not in got
    assert also_org.id in got


def test_outstanding_for_org_drops_submissions_held_only_by_a_dead_placement(conn):
    """Same rule on the submissions side — the two ownership joins are meant
    to mirror each other."""
    client = orgs.create(conn, kind="client", name="Acme")
    market = orgs.create(conn, kind="market", name="Zurich")
    dead_p = placements.create(conn, client.id, "Acme Property 25-26",
                               "2025-10-01", "2026-10-01")
    submissions.create(conn, market.id, "2026-08-01", placement_id=dead_p.id)
    placements.delete(conn, dead_p.id)

    assert submissions.outstanding_for_org(conn, client.id) == []


def test_outstanding_for_org_drops_submissions_held_only_by_a_dead_opportunity(conn):
    client = orgs.create(conn, kind="client", name="Acme")
    market = orgs.create(conn, kind="market", name="Zurich")
    dead_o = opportunities.create(conn, client.id, "Acme GL 26-27")
    submissions.create(conn, market.id, "2026-08-01", opportunity_id=dead_o.id)
    opportunities.delete(conn, dead_o.id)

    assert submissions.outstanding_for_org(conn, client.id) == []


def test_task_category_round_trips_and_feeds_vocab(conn):
    from bookkit.repo import vocab

    tasks.create(conn, "chase quote", category="Renewal")
    tasks.create(conn, "send COI", category="Certificates")
    tasks.create(conn, "misc")  # no category
    # "Internal" is always offered, typed or not — it is the flag that keeps a
    # task out of the client export, and nobody discovers it otherwise
    assert vocab.task_categories(conn) == ["Certificates", "Internal", "Renewal"]


def test_task_categories_offers_internal_on_an_empty_book(conn):
    from bookkit.repo import vocab

    assert vocab.task_categories(conn) == ["Internal"]


def test_task_categories_does_not_double_an_existing_internal_spelling(conn):
    from bookkit.repo import vocab

    tasks.create(conn, "our own file note", category="internal")
    assert vocab.task_categories(conn) == ["internal"]  # first spelling wins


# -- event batches (MCP undo units) ------------------------------------------


def test_event_batch_round_trips_and_lists_recent(conn):
    from bookkit.repo import batches

    client = orgs.create(conn, kind="client", name="Acme")
    made = batches.create(
        conn, batch_id="01BATCHONE", source="mcp", tool="log_activity",
        summary="logged a call", org_id=client.id,
    )
    assert made.ref.startswith("MCP-")
    assert made.reverted_at is None

    got = batches.get_by_ref(conn, made.ref)
    assert got.id == "01BATCHONE"
    assert got.tool == "log_activity"

    listed = batches.recent(conn, since="2000-01-01T00:00:00Z")
    assert [b.id for b in listed] == ["01BATCHONE"]


def test_event_batch_get_by_ref_raises_on_unknown(conn):
    from bookkit.repo import batches

    with pytest.raises(KeyError):
        batches.get_by_ref(conn, "MCP-9999")


def test_mark_reverted_stamps_the_batch(conn):
    from bookkit.repo import batches

    made = batches.create(
        conn, batch_id="01BATCHTWO", source="mcp", tool="task_create",
        summary="made a task", org_id=None,
    )
    batches.mark_reverted(conn, made.id, "2026-08-13T18:00:00Z")
    assert batches.get_by_ref(conn, made.ref).reverted_at == "2026-08-13T18:00:00Z"


def test_events_for_returns_only_that_batch_in_order(conn):
    from bookkit.repo import batches

    batches.create(conn, batch_id="01BATCHTHREE", source="mcp", tool="t",
                   summary="s", org_id=None)
    for eid, fld, old, new, bid in (
        ("e1", "title", "a", "b", "01BATCHTHREE"),
        ("e2", "due_on", None, "2026-09-01", "01BATCHTHREE"),
        ("e3", "title", "x", "y", None),
    ):
        conn.execute(
            "INSERT INTO event_log (id, entity_type, entity_id, field,"
            " old_value, new_value, changed_at, note, batch_id)"
            " VALUES (?, 'task', 't1', ?, ?, ?, '2026-08-13T10:00:00Z', NULL, ?)",
            (eid, fld, old, new, bid),
        )
    got = batches.events_for(conn, "01BATCHTHREE")
    assert [e.id for e in got] == ["e1", "e2"]
    assert got[0].batch_id == "01BATCHTHREE"


def test_appetite_can_be_corrected_and_removed(conn) -> None:
    """F18: add_appetite existed with no update and no delete, so a typo'd
    appetite row was permanent. The table is already in ENTITY_TABLES and
    already carries deleted_at, so both are event-logged and undoable."""
    from bookkit.repo import orgs

    market = orgs.create(conn, kind="market", name="Sompo")
    row = orgs.add_appetite(conn, market.id, line="cyber", appetite="selective")

    fixed = orgs.update_appetite(conn, row.id, appetite="target", min_premium=100_00)
    assert fixed.appetite == "target"
    assert fixed.min_premium == 100_00
    assert orgs.get_appetite(conn, row.id).appetite == "target"

    orgs.delete_appetite(conn, row.id)
    assert [a.id for a in orgs.appetite_for_market(conn, market.id)] == []


def test_deleting_an_appetite_is_undoable(conn) -> None:
    """It is a soft delete, so `u` puts it back — the same promise every other
    delete in the app makes."""
    from bookkit.repo import orgs
    from bookkit.services import batches as batches_svc
    from bookkit.services import undo

    market = orgs.create(conn, kind="market", name="Beazley")
    row = orgs.add_appetite(conn, market.id, line="marine", appetite="target")
    with batches_svc.open_batch(
        conn, source="tui", tool="appetite_delete", summary="removed an appetite line"
    ):
        orgs.delete_appetite(conn, row.id)
    assert orgs.appetite_for_market(conn, market.id) == []

    assert undo.undo_last(conn) is not None
    assert [a.id for a in orgs.appetite_for_market(conn, market.id)] == [row.id]
