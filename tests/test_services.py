from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from bookkit import seed
from bookkit.repo import contacts, events, opportunities, orgs, placements, rfi, submissions, tasks
from bookkit.repo import projects as projects_repo
from bookkit.services import book, capture, hit_rate, pipeline, renewals, sla, staleness, undo
from bookkit.services.export_open_items import compose

TODAY = date(2026, 8, 11)


@pytest.fixture
def seeded(conn: sqlite3.Connection) -> sqlite3.Connection:
    counts = seed.seed(conn, today=TODAY)
    assert counts["orgs"] == 35
    return conn


def test_seed_is_realistic(seeded) -> None:
    assert len(orgs.list_orgs(seeded, kind="client")) == 20
    assert len(orgs.list_orgs(seeded, kind="market")) == 15


def test_renewal_buckets(seeded) -> None:
    buckets = renewals.bucketed(seeded, TODAY)
    assert set(buckets) == {"overdue", "0-30", "31-60", "61-90", "91-120"}
    items = renewals.upcoming(seeded, TODAY)
    assert items, "seed should have renewals inside 120 days"
    for item in items:
        if item.bucket == "overdue":  # expired, never renewed — stays visible
            assert item.days_remaining < 0
            continue
        assert 0 <= item.days_remaining <= 120
        lo, hi = (int(x) for x in item.bucket.split("-"))
        assert lo <= item.days_remaining <= hi
    upcoming_only = [i.days_remaining for i in items if i.days_remaining >= 0]
    assert upcoming_only == sorted(upcoming_only)


def test_staleness_weighted_by_premium(seeded) -> None:
    stale = staleness.stale_accounts(seeded, TODAY, threshold_days=60)
    assert stale, "seed deliberately leaves accounts stale"
    weights = [s.weight for s in stale]
    assert weights == sorted(weights, reverse=True)
    for account in stale:
        assert account.days_stale > 60
        assert account.org.status == "active"


def test_stage_transitions_enforced(seeded) -> None:
    org = orgs.list_orgs(seeded, kind="client")[0]
    opp = opportunities.create(seeded, org.id, "Test opp", target_premium=100_000_00)
    with pytest.raises(pipeline.StageError):
        pipeline.move_stage(seeded, opp.id, "quoted")  # can't skip gates
    pipeline.move_stage(seeded, opp.id, "qualified")
    moved = pipeline.move_stage(seeded, opp.id, "lost", loss_reason="budget")
    assert moved.stage == "lost" and moved.outcome == "lost" and moved.probability_pct == 0
    with pytest.raises(pipeline.StageError):
        pipeline.move_stage(seeded, opp.id, "won")  # closed is closed
    log = pipeline.stage_history(seeded, opp.id)
    assert [(e.old_value, e.new_value) for e in log] == [
        ("qualified", "lost"), ("identified", "qualified"),
    ]


def test_pipeline_metrics(seeded) -> None:
    rows = pipeline.metrics(seeded)
    assert [r.stage for r in rows] == list(pipeline.STAGES)
    by_stage = {r.stage: r for r in rows}
    total = sum(r.count for r in rows)
    assert total == len(opportunities.by_stage(seeded))
    for r in rows:
        assert r.weighted_cents <= r.total_cents
    assert by_stage["qualified"].avg_days_in_stage is not None  # seed advanced through it


def test_sla(seeded) -> None:
    overdue = sla.past_sla(seeded, TODAY, sla_days=10)
    assert overdue, "seed leaves submissions out past SLA"
    assert all(o.days_out >= 10 for o in overdue)
    assert all(o.submission.status == "out" for o in overdue)
    days = [o.days_out for o in overdue]
    assert days == sorted(days, reverse=True)


def test_hit_rate(seeded) -> None:
    rows = hit_rate.by_market(seeded)
    assert rows
    for r in rows:
        assert 0 <= r.quote_rate <= 1
        assert r.bound <= r.quoted <= r.sent
    total = hit_rate.overall(seeded)
    assert total.sent == sum(r.sent for r in rows)


def test_book_summary(seeded) -> None:
    summary = book.summary(seeded)
    assert summary.accounts > 0
    assert summary.total_premium > 0
    assert 0 < summary.total_commission < summary.total_premium
    assert summary.by_program


def test_undo_field_change(seeded) -> None:
    """`u` reverts a TUI write. It is batch-granular now, so the write has to
    be made the way the TUI makes it — inside a batch."""
    from bookkit.services import batches as batches_svc

    org = orgs.list_orgs(seeded, kind="client")[0]
    with batches_svc.open_batch(
        seeded, source="tui", tool="edit_account", summary="set status"
    ):
        orgs.update(seeded, org.id, status="dormant")

    result = undo.undo_last(seeded)
    assert result is not None and result.applied
    assert orgs.get(seeded, org.id).status == org.status


def test_undo_soft_delete(seeded) -> None:
    from bookkit.services import batches as batches_svc

    org = orgs.list_orgs(seeded, kind="client")[0]
    task = tasks.create(seeded, "Ephemeral", org_id=org.id)
    with batches_svc.open_batch(
        seeded, source="tui", tool="task_delete", summary="deleted a task"
    ):
        tasks.delete(seeded, task.id)

    result = undo.undo_last(seeded)
    assert result is not None and result.applied
    assert tasks.get(seeded, task.id).status == "open"


def test_undo_ignores_a_write_this_app_did_not_make(seeded) -> None:
    """Unbatched writes are sync projections and imports. `u` used to revert
    placement.synced_at on a freshly opened app; now it finds nothing."""
    org = orgs.list_orgs(seeded, kind="client")[0]
    orgs.update(seeded, org.id, status="dormant")      # no batch = not ours

    assert undo.undo_last(seeded) is None
    assert orgs.get(seeded, org.id).status == "dormant"


def test_capture_suggests_task() -> None:
    got = capture.suggest_task("Spoke to Alice. Follow up Tuesday about loss runs.", TODAY)
    assert got is not None
    assert got.due_on > TODAY and got.due_on.weekday() == 1
    got = capture.suggest_task("call him next week re: quote", TODAY)
    assert got is not None and got.due_on > TODAY
    assert capture.suggest_task("Bound the renewal, all done.", TODAY) is None


def test_event_log_answers_when_did_this_move(seeded) -> None:
    org = orgs.list_orgs(seeded, kind="client")[0]
    placement = placements.for_org(seeded, org.id)[0]
    placements.update(seeded, placement.id, period_to="2027-03-01", note="insured pushed expiry")
    history = events.field_history(seeded, "placement", placement.id, "period_to")
    assert history[0].new_value == "2027-03-01"
    assert history[0].note == "insured pushed expiry"


def test_renewal_date_survives_timezones(seeded, monkeypatch) -> None:
    """DATE columns are text and never converted: reading under a different
    TZ yields the same date (§3.1)."""
    import os
    import time

    org = orgs.list_orgs(seeded, kind="client")[0]
    placement = placements.for_org(seeded, org.id)[0]
    before = placement.period_to
    monkeypatch.setenv("TZ", "Pacific/Auckland")
    time.tzset()
    try:
        assert placements.get(seeded, placement.id).period_to == before
        assert date.fromisoformat(before) == date.fromisoformat(
            placements.get(seeded, placement.id).period_to
        )
    finally:
        os.environ.pop("TZ", None)
        time.tzset()


class TestRenewalsScanAllPrograms:
    def test_overdue_unrenewed_program_stays_on_the_radar(self, conn) -> None:
        org = orgs.create(conn, kind="client", name="Multi Program Co")
        placements.create(conn, org.id, "Property", "2025-11-01", "2026-11-01",
                          status="bound")
        expired = placements.create(conn, org.id, "Casualty", "2025-06-01",
                                    "2026-06-01", status="bound")
        items = renewals.upcoming(conn, TODAY, days=120)
        by_id = {item.placement.id: item for item in items}
        assert expired.id in by_id  # expired 2 months ago, never renewed
        assert by_id[expired.id].bucket == "overdue"
        assert by_id[expired.id].days_remaining < 0

    def test_renewed_and_lapsed_programs_do_not_nag(self, conn) -> None:
        org = orgs.create(conn, kind="client", name="Tidy Co")
        placements.create(conn, org.id, "2025 Casualty Program", "2025-06-01",
                          "2026-06-01", status="bound")
        placements.create(conn, org.id, "2026 Casualty Program", "2026-06-01",
                          "2027-06-01", status="bound")  # renew-at-birth successor
        placements.create(conn, org.id, "Old Marine", "2024-01-01", "2025-01-01",
                          status="lapsed")  # deliberately let go
        items = renewals.upcoming(conn, TODAY, days=120)
        assert all(item.bucket != "overdue" for item in items)

    def test_next_for_org_prefers_overdue_across_programs(self, conn) -> None:
        org = orgs.create(conn, kind="client", name="Two Towers Co")
        placements.create(conn, org.id, "Property", "2025-10-01", "2026-10-01",
                          status="bound")
        placements.create(conn, org.id, "Casualty", "2025-06-01", "2026-06-01",
                          status="bound")
        nxt = renewals.next_for_org(conn, org.id, TODAY)
        assert nxt is not None
        assert nxt.placement.program_name == "Casualty"  # overdue beats upcoming
        assert nxt.days_remaining < 0


def test_renewal_items_carry_line_labels(tmp_path) -> None:
    from bookkit import db as db_mod
    from bookkit import sync as sync_mod

    connection = db_mod.connect(tmp_path / "t.db")
    try:
        seed.seed(connection, today=TODAY, programs_dir=tmp_path / "programs")
        sync_mod.project_all(connection, [tmp_path / "programs"])
        items = renewals.upcoming(connection, TODAY)
        linked = [i for i in items if i.placement.program_path]
        assert linked, "seed has file-linked placements in the window"
        assert all(item.lines for item in linked)  # e.g. "GL, AL, EL"
        unlinked = [i for i in items if not i.placement.program_path]
        assert all(item.lines == "" for item in unlinked)
    finally:
        connection.close()


def test_line_of_cover_drives_renewal_clock(conn, tmp_path) -> None:
    """A line whose layer expires before the program period must surface on
    ITS clock: the IM policy dying in 30 days pulls the placement into the
    renewal radar even though the program end is 180 days out."""
    from datetime import timedelta

    from towerkit.model import Layer, Line, Participant, Period, Program, dump_program
    from towerkit.model import Placement as TkPlacement

    org = orgs.create(conn, name="Line Clock Co", kind="client", status="active")
    start = TODAY - timedelta(days=185)
    end = TODAY + timedelta(days=180)
    p = placements.create(
        conn, org.id, "2026 Package Program",
        start.isoformat(), end.isoformat(), status="bound",
    )
    im_end = TODAY + timedelta(days=30)
    program = Program(
        insured=org.name, program="Package Program", placement=TkPlacement.BOUND,
        period=Period(start=start, end=end),
        lines=[
            Line(id="pr", name="Property", abbr="PR"),
            Line(id="im", name="Inland Marine", abbr="IM"),
        ],
        layers=[
            Layer(
                id="pr1", name="Primary Property", applies_to=["pr"],
                attach=0, limit=1_000_000,
                participants=[Participant(carrier="Zurich", share_bps=10_000)],
            ),
            Layer(
                id="im1", name="Primary IM", applies_to=["im"],
                period=Period(start=start, end=im_end),
                attach=0, limit=1_000_000,
                participants=[Participant(carrier="CNA", share_bps=10_000)],
            ),
        ],
    )
    path = tmp_path / "line-clock.json"
    dump_program(program, path)
    placements.update(conn, p.id, program_path=str(path))

    items = [i for i in renewals.upcoming(conn, TODAY) if i.org.id == org.id]
    assert len(items) == 1, "the IM line's clock must pull the placement in"
    item = items[0]
    assert item.renewal_on == im_end.isoformat()
    assert item.days_remaining == 30
    assert item.bucket == "0-30"
    assert item.line_ends[0] == ("IM", im_end.isoformat())
    assert dict(item.line_ends)["PR"] == end.isoformat()

    nxt = renewals.next_for_org(conn, org.id, TODAY)
    assert nxt is not None and nxt.renewal_on == im_end.isoformat()


def test_flatten_markdown_strips_marks_keeps_bullets():
    from bookkit.services.export_open_items import flatten_markdown

    text = "## Head\n- **bold** item\n* second [link](http://x)\n`code`"
    assert flatten_markdown(text) == "Head\n- bold item\n- second link\ncode"


def test_compose_groups_by_program_project_and_general(conn):
    # build: client with an org-level task, a placement-attached task,
    # an outstanding submission on that placement, and a project need
    client = orgs.create(conn, kind="client", name="Acme", status="active", owner="grant")
    market = orgs.create(conn, kind="market", name="Zurich", status="active")

    org_task = tasks.create(
        conn, "Chase updated loss runs", org_id=client.id,
        description="waiting on brief line from the client",
        detail="**Please** call the broker",
    )

    p = placements.create(
        conn, client.id, "Acme Property 25-26", "2025-10-01", "2026-10-01"
    )
    placement_task = tasks.create(conn, "Confirm bound terms", placement_id=p.id)
    sub = submissions.create(conn, market.id, "2026-07-01", placement_id=p.id)

    project = projects_repo.create_project(conn, client.id, "Warehouse Expansion")
    need = projects_repo.add_need(conn, project.id, "Builder's Risk", "2026-09-01")

    today = date.today()
    sections = compose(conn, client.id, today)
    labels = [s.label for s in sections]
    assert labels[0].startswith("General")
    assert any(lbl.startswith("Acme Property") for lbl in labels)
    assert any(lbl.startswith("Project — ") for lbl in labels)
    task_row = sections[0].rows[0]
    assert task_row.kind == "Task"
    assert task_row.description == "waiting on brief line from the client"
    assert task_row.detail == "Please call the broker"  # markdown flattened
    # per-client rows carry a ref — the exact id an MCP caller reads and
    # later hands to task_complete (task_complete has no ref of its own to
    # offer from the export path otherwise)
    assert task_row.ref == org_task.id

    # a placement-only task (org_id NULL, placement_id set — legal per
    # repo/tasks.py) must still reach the workbook, in the placement's section
    placement_section = next(s for s in sections if s.label.startswith("Acme Property"))
    assert any(r.item == "Confirm bound terms" for r in placement_section.rows)
    placement_task_row = next(
        r for r in placement_section.rows if r.item == "Confirm bound terms")
    assert placement_task_row.ref == placement_task.id
    submission_row = next(r for r in placement_section.rows if r.kind == "Submission")
    assert submission_row.ref == sub.id

    # need status is client-facing prose, not raw vocab ("identified", not
    # the DB's underscored form)
    project_section = next(s for s in sections if s.label.startswith("Project — "))
    assert project_section.rows[0].status == "Identified"
    assert project_section.rows[0].ref == need.id


def test_status_label_prettifies_underscored_vocab():
    from bookkit.services.export_open_items import _status_label

    assert _status_label("identified") == "Identified"
    assert _status_label("not_needed") == "Not needed"


def test_compose_empty_book_returns_no_sections(conn):
    org = orgs.create(conn, kind="client", name="Empty Co", status="active", owner="grant")
    assert compose(conn, org.id, date(2026, 8, 12)) == []


def test_compose_sections_org_tasks_by_category(conn):
    org = orgs.create(conn, name="Cat Co", kind="client")
    tasks.create(conn, "renew GL", org_id=org.id, category="Renewal")
    tasks.create(conn, "renew AL", org_id=org.id, category="Renewal")
    tasks.create(conn, "send COI", org_id=org.id, category="Certificates")
    tasks.create(conn, "misc", org_id=org.id)
    labels = [s.label for s in compose(conn, org.id, date(2026, 8, 12))]
    assert labels == ["Certificates — Cat Co", "Renewal — Cat Co", "General — Cat Co"]


def test_compose_categories_bucket_case_insensitively(conn):
    # "renewal" and "Renewal" are the same section, case-insensitively
    # (repo/vocab.py's _dedupe rule); first-seen spelling wins the label
    org = orgs.create(conn, name="Case Co", kind="client")
    tasks.create(conn, "renew GL", org_id=org.id, category="renewal")
    tasks.create(conn, "renew AL", org_id=org.id, category="Renewal")
    sections = compose(conn, org.id, date(2026, 8, 12))
    renewal_sections = [s for s in sections if s.label.startswith(("renewal", "Renewal"))]
    assert len(renewal_sections) == 1
    assert renewal_sections[0].label == "renewal — Case Co"  # first-seen spelling
    assert len(renewal_sections[0].rows) == 2


# --- C7: overdue leads ------------------------------------------------------
#
# Alphabetical is the one ordering that serves nobody. Sections sort by their
# most-overdue member and rows sort inside their section; nothing is
# regrouped, so no item can appear twice.


TODAY = date(2026, 8, 18)


def test_the_overdue_section_leads_the_one_that_sorts_first_alphabetically(conn):
    """The reviewer's own copy: their only overdue item, 12 days late, sat
    below a certificate not due for three days, because C came before R."""
    org = orgs.create(conn, name="Order Co", kind="client")
    tasks.create(conn, "issue COI", org_id=org.id, category="Certificates",
                 due_on="2026-08-21")            # due in 3 days
    tasks.create(conn, "renewal submission", org_id=org.id, category="Renewal",
                 due_on="2026-08-06")            # 12 days overdue

    labels = [s.label for s in compose(conn, org.id, TODAY)]
    assert labels == ["Renewal — Order Co", "Certificates — Order Co"]


def test_the_most_overdue_section_leads_the_overdue_ones(conn):
    org = orgs.create(conn, name="Deep Co", kind="client")
    tasks.create(conn, "a", org_id=org.id, category="Audit", due_on="2026-08-15")
    tasks.create(conn, "b", org_id=org.id, category="Binder", due_on="2026-05-01")
    tasks.create(conn, "c", org_id=org.id, category="Claims", due_on="2026-07-01")

    labels = [s.label for s in compose(conn, org.id, TODAY)]
    assert labels == ["Binder — Deep Co", "Claims — Deep Co", "Audit — Deep Co"]


def test_overdue_rows_lead_inside_their_own_section(conn):
    """The same defect at smaller scale: a section that opens on a row due
    next month while holding one two weeks late."""
    org = orgs.create(conn, name="Inside Co", kind="client")
    tasks.create(conn, "later", org_id=org.id, category="Renewal", due_on="2026-09-30")
    tasks.create(conn, "undated", org_id=org.id, category="Renewal")
    tasks.create(conn, "late", org_id=org.id, category="Renewal", due_on="2026-08-04")
    tasks.create(conn, "latest", org_id=org.id, category="Renewal", due_on="2026-07-04")

    section = compose(conn, org.id, TODAY)[0]
    assert [r.item for r in section.rows] == ["latest", "late", "later", "undated"]


def test_a_placement_section_outranks_a_category_section_when_it_is_overdue(conn):
    """Sheet 1 carries TWO kinds of section. Both sort in one ordering, so an
    overdue placement item leads a merely-upcoming category item and vice
    versa — a rule applied to only one kind would leave the other alphabetical."""
    org = orgs.create(conn, name="Both Co", kind="client")
    p = placements.create(conn, org.id, "Both Co Property 25-26",
                          "2025-10-01", "2026-10-01")
    tasks.create(conn, "chase subjectivities", placement_id=p.id, due_on="2026-08-01")
    tasks.create(conn, "issue COI", org_id=org.id, category="Certificates",
                 due_on="2026-08-21")

    labels = [s.label for s in compose(conn, org.id, TODAY)]
    assert labels == ["Both Co Property 25-26", "Certificates — Both Co"]


def test_an_overdue_project_need_leads_although_its_status_is_not_overdue(conn):
    """`_overdue_on` reads the DUE date, not the status word. Only task rows
    ever say "Overdue"; a project need carries its own vocabulary status and
    reaches sheet 1 through a different branch entirely."""
    org = orgs.create(conn, name="Need Co", kind="client")
    project = projects_repo.create_project(conn, org.id, "Warehouse")
    projects_repo.add_need(conn, project.id, "Builder's Risk", "2026-06-01")
    tasks.create(conn, "issue COI", org_id=org.id, category="Certificates",
                 due_on="2026-08-21")

    sections = compose(conn, org.id, TODAY)
    assert [s.label for s in sections] == [
        "Project — Warehouse", "Certificates — Need Co"]
    assert sections[0].rows[0].status == "Identified"  # not the word "Overdue"


def test_nothing_appears_twice_when_overdue_leads(conn):
    """Reordering, never regrouping: every ref still appears exactly once
    across the whole sheet, and the row count is unchanged."""
    org = orgs.create(conn, name="Once Co", kind="client")
    p = placements.create(conn, org.id, "Once Co Package", "2025-10-01", "2026-10-01")
    project = projects_repo.create_project(conn, org.id, "Fitout")
    tasks.create(conn, "late cat", org_id=org.id, category="Renewal",
                 due_on="2026-07-01")
    tasks.create(conn, "soon cat", org_id=org.id, category="Certificates",
                 due_on="2026-09-01")
    tasks.create(conn, "loose", org_id=org.id)
    tasks.create(conn, "late placement", placement_id=p.id, due_on="2026-06-01")
    projects_repo.add_need(conn, project.id, "Builder's Risk", "2026-05-01")

    refs = [r.ref for s in compose(conn, org.id, TODAY) for r in s.rows]
    assert len(refs) == len(set(refs)) == 5


def test_with_nothing_overdue_the_composition_order_survives(conn):
    """The stable-sort tiebreak, pinned: categories alphabetical, then
    General, then placements, then projects — unchanged from before C7."""
    org = orgs.create(conn, name="Calm Co", kind="client")
    p = placements.create(conn, org.id, "Calm Co Package", "2025-10-01", "2026-10-01")
    project = projects_repo.create_project(conn, org.id, "Fitout")
    tasks.create(conn, "renew", org_id=org.id, category="Renewal", due_on="2026-09-05")
    tasks.create(conn, "cert", org_id=org.id, category="Certificates")
    tasks.create(conn, "loose", org_id=org.id)
    tasks.create(conn, "bind", placement_id=p.id, due_on="2026-09-09")
    projects_repo.add_need(conn, project.id, "Builder's Risk", "2026-12-01")

    assert [s.label for s in compose(conn, org.id, TODAY)] == [
        "Certificates — Calm Co", "Renewal — Calm Co", "General — Calm Co",
        "Calm Co Package", "Project — Fitout",
    ]


def test_a_task_due_today_is_not_overdue(conn):
    """The same boundary C3 draws: today is not late."""
    org = orgs.create(conn, name="Edge Co", kind="client")
    tasks.create(conn, "due today", org_id=org.id, category="Zeta",
                 due_on=TODAY.isoformat())
    tasks.create(conn, "no date", org_id=org.id, category="Alpha")

    assert [s.label for s in compose(conn, org.id, TODAY)] == [
        "Alpha — Edge Co", "Zeta — Edge Co"]


# --- the Internal category: never leaves the building ------------------------
#
# The filter sits on the task list in compose(), before the split into
# category sections and placement sections, so an Internal task cannot ride
# out to the client through the placement half (which ignores category
# entirely). These tests pin both halves, the match rule, and the count the
# exporter reports back.


def test_compose_omits_the_internal_category_section(conn):
    org = orgs.create(conn, name="Internal Co", kind="client")
    tasks.create(conn, "renew GL", org_id=org.id, category="Renewal")
    tasks.create(conn, "chase our own file note", org_id=org.id, category="Internal")
    sections = compose(conn, org.id, date(2026, 8, 12))
    assert [s.label for s in sections] == ["Renewal — Internal Co"]
    items = [r.item for s in sections for r in s.rows]
    assert "chase our own file note" not in items


def test_internal_match_ignores_case_and_surrounding_space(conn):
    org = orgs.create(conn, name="Space Co", kind="client")
    tasks.create(conn, "a", org_id=org.id, category=" internal ")
    tasks.create(conn, "b", org_id=org.id, category="INTERNAL")
    tasks.create(conn, "c", org_id=org.id, category="Internal")
    tasks.create(conn, "d", org_id=org.id, category="Renewal")
    sections = compose(conn, org.id, date(2026, 8, 12))
    assert [s.label for s in sections] == ["Renewal — Space Co"]


def test_internal_prefix_is_not_internal(conn):
    """D1: exact equality, not a prefix. "Internal Review" is a real
    client-facing broking category; excluding it would drop a task from the
    deliverable with no signal anywhere. A wrong INCLUSION is loud (the
    client sees a section header naming it); a wrong exclusion is silent."""
    org = orgs.create(conn, name="Prefix Co", kind="client")
    tasks.create(conn, "walk the client through the audit", org_id=org.id,
                 category="Internal Review")
    sections = compose(conn, org.id, date(2026, 8, 12))
    assert [s.label for s in sections] == ["Internal Review — Prefix Co"]
    assert sections[0].rows[0].item == "walk the client through the audit"


def test_internal_task_on_a_placement_is_withheld_too(conn):
    """Sheet 1 carries a section per PLACEMENT, built from the same task list
    with category IGNORED. A filter applied inside the category branch leaves
    that half untouched and the Internal task ships anyway."""
    org = orgs.create(conn, name="Placement Co", kind="client")
    p = placements.create(conn, org.id, "Placement Co Property 25-26",
                          "2025-10-01", "2026-10-01")
    tasks.create(conn, "Confirm bound terms", placement_id=p.id)
    tasks.create(conn, "our own reserve note", placement_id=p.id, category="Internal")
    # also reachable through a category section, so a category-branch-only
    # filter looks like it works while the placement half leaks
    tasks.create(conn, "our own file note", org_id=org.id, category="Internal")
    tasks.create(conn, "renew GL", org_id=org.id, category="Renewal")

    sections = compose(conn, org.id, date(2026, 8, 12))
    items = [r.item for s in sections for r in s.rows]
    assert "our own reserve note" not in items, "an Internal placement task shipped"
    assert "our own file note" not in items
    placement_section = next(
        s for s in sections if s.label.startswith("Placement Co Property"))
    assert [r.item for r in placement_section.rows] == ["Confirm bound terms"]


def test_all_internal_account_exports_the_no_open_items_sheet(conn, tmp_path):
    """The pinned empty-section answer: compose() never emits an empty
    section, so an account whose only open item is Internal composes to
    nothing and write() falls back to its placeholder row."""
    from bookkit.services.export_open_items import write

    org = orgs.create(conn, name="Quiet Co", kind="client")
    tasks.create(conn, "our own file note", org_id=org.id, category="Internal")
    assert compose(conn, org.id, date(2026, 8, 12)) == []
    path = write(conn, org.id, tmp_path / "q.xlsx", date(2026, 8, 12))
    from openpyxl import load_workbook
    assert load_workbook(path).active["A2"].value == "No open items as of 2026-08-12"


def test_compose_can_be_asked_for_the_internal_rows(conn):
    """The exclusion is a default, not a law: MCP asks for them explicitly."""
    from bookkit.services.export_open_items import compose as compose_fn

    org = orgs.create(conn, name="Ask Co", kind="client")
    tasks.create(conn, "our own file note", org_id=org.id, category="Internal")
    sections = compose_fn(conn, org.id, date(2026, 8, 12), include_internal=True)
    rows = [r for s in sections for r in s.rows]
    assert [r.item for r in rows] == ["our own file note"]
    assert rows[0].internal is True


def test_export_rows_flag_internal_but_the_workbook_cannot_print_it(conn, tmp_path):
    """ExportRow.internal follows `ref`: carried for MCP, absent from the
    writer's explicit column tuple, so it can never reach the client."""
    from bookkit.services.export_open_items import compose as compose_fn
    from bookkit.services.export_open_items import write

    org = orgs.create(conn, name="Flag Co", kind="client")
    tasks.create(conn, "our own file note", org_id=org.id, category="Internal")
    tasks.create(conn, "renew GL", org_id=org.id, category="Renewal")
    rows = {r.item: r.internal
            for s in compose_fn(conn, org.id, date(2026, 8, 12), include_internal=True)
            for r in s.rows}
    assert rows == {"our own file note": True, "renew GL": False}

    path = write(conn, org.id, tmp_path / "f.xlsx", date(2026, 8, 12))
    from openpyxl import load_workbook
    sheet = load_workbook(path).active
    values = [c.value for row in sheet.iter_rows() for c in row]
    assert "our own file note" not in values

    # `True not in values` cannot fail here: write() composes with the default,
    # so every surviving row already has internal=False and no True exists to
    # find. These two can. A bool in ANY cell is the flag having reached the
    # workbook, and the Status column is where a wrong column tuple would put
    # it — so pin what that column actually says.
    assert not any(isinstance(v, bool) for v in values), (
        "a bool reached a workbook cell — ExportRow.internal is in write()'s "
        "column tuple"
    )
    # header, the "Renewal — Flag Co" section label (column A only), the one
    # surviving row. A wrong column tuple puts a False here.
    status_column = [row[5].value for row in sheet.iter_rows()]
    assert status_column == ["Status", None, "Open"], status_column


def test_withheld_internal_lists_what_the_client_did_not_get(conn):
    from bookkit.services.export_open_items import withheld_internal

    org = orgs.create(conn, name="Held Co", kind="client")
    held = tasks.create(conn, "our own file note", org_id=org.id, category="Internal")
    tasks.create(conn, "renew GL", org_id=org.id, category="Renewal")
    tasks.create(conn, "misc", org_id=org.id)
    assert [t.id for t in withheld_internal(conn, org.id)] == [held.id]


def test_near_miss_internal_names_what_the_rule_did_not_catch(conn):
    """Exact equality is the rule, so "Internal Review" ships. The near-miss
    list is the positive signal that says so — an absence teaches nobody who
    has never seen the presence."""
    from bookkit.services.export_open_items import near_miss_internal

    org = orgs.create(conn, name="Near Co", kind="client")
    tasks.create(conn, "our own file note", org_id=org.id, category="Internal")
    review = tasks.create(conn, "audit support", org_id=org.id, category="Internal Review")
    lower = tasks.create(conn, "note", org_id=org.id, category=" internal note ")
    tasks.create(conn, "renew GL", org_id=org.id, category="Renewal")
    tasks.create(conn, "misc", org_id=org.id)
    assert {t.id for t in near_miss_internal(conn, org.id)} == {review.id, lower.id}


def test_withheld_note_says_only_what_was_withheld(conn):
    from bookkit.services.export_open_items import withheld_note

    org = orgs.create(conn, name="Held Only Co", kind="client")
    tasks.create(conn, "our own file note", org_id=org.id, category="Internal")
    tasks.create(conn, "renew GL", org_id=org.id, category="Renewal")
    note = withheld_note(conn, org.id)
    assert note == " — 1 internal task withheld"

    tasks.create(conn, "our own reserve note", org_id=org.id, category="internal")
    assert withheld_note(conn, org.id) == " — 2 internal tasks withheld"


def test_withheld_note_names_the_near_miss_that_was_exported(conn):
    """The ROADMAP's own stated worry — "a prefix match and an equality match
    behave very differently the first time someone types 'Internal Review'" —
    answered where both surfaces already read the same sentence."""
    from bookkit.services.export_open_items import withheld_note

    org = orgs.create(conn, name="Miss Co", kind="client")
    tasks.create(conn, "audit support", org_id=org.id, category="Internal Review")
    assert withheld_note(conn, org.id) == (
        ' — 1 task categorised "Internal Review" WAS exported '
        '(only the exact category "Internal" is withheld)'
    )

    # plural, and the same spelling twice names the category once
    tasks.create(conn, "more audit support", org_id=org.id, category="Internal Review")
    tasks.create(conn, "file note", org_id=org.id, category="internal note")
    assert withheld_note(conn, org.id) == (
        ' — 3 tasks categorised "internal note", "Internal Review" WERE exported '
        '(only the exact category "Internal" is withheld)'
    )


def test_withheld_note_says_both_when_both_happened(conn):
    from bookkit.services.export_open_items import withheld_note

    org = orgs.create(conn, name="Both Co", kind="client")
    tasks.create(conn, "our own file note", org_id=org.id, category="Internal")
    tasks.create(conn, "audit support", org_id=org.id, category="Internal Review")
    assert withheld_note(conn, org.id) == (
        ' — 1 internal task withheld; 1 task categorised "Internal Review" '
        'WAS exported (only the exact category "Internal" is withheld)'
    )


def test_withheld_note_is_empty_when_there_is_nothing_to_say(conn):
    """Silence is still correct for the ordinary account: nothing internal,
    nothing that reads internal. It is only silence about a NEAR MISS that
    taught nobody anything."""
    from bookkit.services.export_open_items import withheld_note

    org = orgs.create(conn, name="Quiet Co", kind="client")
    tasks.create(conn, "renew GL", org_id=org.id, category="Renewal")
    tasks.create(conn, "misc", org_id=org.id)
    assert withheld_note(conn, org.id) == ""


def test_is_internal_category_predicate():
    from bookkit.models import INTERNAL_CATEGORY, is_internal_category

    assert INTERNAL_CATEGORY == "Internal"
    assert is_internal_category("Internal")
    assert is_internal_category(" internal ")
    assert is_internal_category("INTERNAL")
    assert not is_internal_category("Internal Review")
    assert not is_internal_category("Renewal")
    assert not is_internal_category("")
    assert not is_internal_category(None)


def test_write_open_items_deterministic_and_styled(conn, tmp_path):
    from bookkit.services.export_open_items import write

    # reuse the fixture-building from test_compose_groups...
    client = orgs.create(conn, kind="client", name="Acme", status="active", owner="grant")
    market = orgs.create(conn, kind="market", name="Zurich", status="active")

    tasks.create(
        conn, "Chase updated loss runs", org_id=client.id,
        description="waiting on brief line from the client",
    )

    p = placements.create(
        conn, client.id, "Acme Property 25-26", "2025-10-01", "2026-10-01"
    )
    tasks.create(conn, "Confirm bound terms", placement_id=p.id)
    submissions.create(conn, market.id, "2026-07-01", placement_id=p.id)

    project = projects_repo.create_project(conn, client.id, "Warehouse Expansion")
    projects_repo.add_need(conn, project.id, "Builder's Risk", "2026-09-01")

    today = date.today()
    a = write(conn, client.id, tmp_path / "a.xlsx", today)
    b = write(conn, client.id, tmp_path / "b.xlsx", today)
    assert a.read_bytes() == b.read_bytes()
    from openpyxl import load_workbook  # test-only import; src never imports it
    ws = load_workbook(a).active
    assert [c.value for c in ws[1]] == [
        "Item", "Description", "Detail", "Type", "Due / Needed by", "Status"]
    # the placement-only task (org_id NULL) must actually be in the workbook,
    # not silently dropped by an org_id-only fetch
    values = [cell.value for row in ws.iter_rows() for cell in row]
    assert "Confirm bound terms" in values


def test_write_empty_book_says_so(conn, tmp_path):
    from bookkit.services.export_open_items import write

    org = orgs.create(conn, name="Empty Co", kind="client")
    path = write(conn, org.id, tmp_path / "e.xlsx", date(2026, 8, 12))
    from openpyxl import load_workbook
    assert load_workbook(path).active["A2"].value == "No open items as of 2026-08-12"


def test_write_three_tab_order_and_headers(conn, tmp_path):
    from bookkit.services.export_open_items import write

    client = orgs.create(conn, kind="client", name="Acme", status="active", owner="grant")
    tasks.create(conn, "Chase updated loss runs", org_id=client.id)
    placements.create(
        conn, client.id, "Acme Property 25-26", "2025-10-01", "2026-10-01",
        status="bound", total_premium=250_000_00,
    )
    live = projects_repo.create_project(conn, client.id, "Warehouse Expansion")
    projects_repo.add_need(conn, live.id, "Builder's Risk", "2026-09-01")

    path = write(conn, client.id, tmp_path / "w.xlsx", date(2026, 8, 13))
    from openpyxl import load_workbook  # test-only import; src never imports it
    wb = load_workbook(path)
    # spec-fixed sheet order; sheet 1 title unchanged from the single-sheet era
    assert wb.sheetnames == ["Open Items — Acme", "Projects", "Schedule of Insurance"]
    assert [c.value for c in wb["Projects"][1]] == [
        "Line", "Notes", "Needed by", "Status", "Limit"]
    assert [c.value for c in wb["Schedule of Insurance"][1]] == [
        "Insured", "Line of Coverage", "Carrier", "Policy Number", "Effective Date",
        "Expiration Date", "Limits", "Deductible / SIR / Retention", "Premium"]
    # show_premiums=True end to end: the book-data premium in whole dollars
    soi_values = [c.value for row in wb["Schedule of Insurance"].iter_rows() for c in row]
    assert 250_000 in soi_values


def test_write_omits_sheets_without_data(conn, tmp_path):
    from bookkit.services.export_open_items import write

    org = orgs.create(conn, name="Solo Co", kind="client")
    tasks.create(conn, "one open task", org_id=org.id)
    path = write(conn, org.id, tmp_path / "s.xlsx", date(2026, 8, 13))
    from openpyxl import load_workbook
    assert load_workbook(path).sheetnames == ["Open Items — Solo Co"]


def test_write_three_tab_deterministic(conn, tmp_path):
    from bookkit.services.export_open_items import write

    client = orgs.create(conn, kind="client", name="Det Co", status="active")
    placements.create(conn, client.id, "Det Program", "2025-01-01", "2026-01-01")
    live = projects_repo.create_project(conn, client.id, "Det Project")
    projects_repo.add_need(conn, live.id, "GL", "2026-09-01")
    a = write(conn, client.id, tmp_path / "a.xlsx", date(2026, 8, 13))
    b = write(conn, client.id, tmp_path / "b.xlsx", date(2026, 8, 13))
    assert a.read_bytes() == b.read_bytes()  # three sheets, one finalize, same bytes


def test_onboarding_completeness_derives_from_data(conn):
    from bookkit.services import onboarding

    org = orgs.create(conn, name="Newco", kind="client")
    states = {s.step.key: s.state for s in onboarding.completeness(conn, org.id)}
    assert states == {
        "org": onboarding.PARTIAL,       # name/kind exist by construction
        "contacts": onboarding.UNTOUCHED,
        "program": onboarding.UNTOUCHED,
        "projects": onboarding.UNTOUCHED,
        "followups": onboarding.UNTOUCHED,
    }
    assert onboarding.first_incomplete(conn, org.id) == "org"
    assert not onboarding.is_complete(conn, org.id)

    orgs.update(conn, org.id, owner="grant", industry="construction")
    contacts.create(conn, org.id, first_name="Ann", last_name="Lee",
                    email="ann@newco.com")
    placements.create(conn, org_id=org.id, program_name="Newco Package 26-27",
                      period_from="2026-09-01", period_to="2027-09-01")
    states = {s.step.key: s.state for s in onboarding.completeness(conn, org.id)}
    assert states["org"] == states["contacts"] == states["program"] == onboarding.COMPLETE
    assert onboarding.is_complete(conn, org.id)  # optional steps don't gate
    assert onboarding.first_incomplete(conn, org.id) == "projects"


def test_onboarding_contact_without_reach_is_partial(conn):
    from bookkit.services import onboarding

    org = orgs.create(conn, name="Newco2", kind="client")
    contacts.create(conn, org.id, first_name="Bo", last_name="Nil")  # no email/phone
    status = {s.step.key: s for s in onboarding.completeness(conn, org.id)}
    assert status["contacts"].state == onboarding.PARTIAL
    assert "email or phone" in status["contacts"].summary


def test_onboarding_followups_sees_placement_attached_task(conn):
    from bookkit.services import onboarding

    org = orgs.create(conn, name="Newco3", kind="client")
    p = placements.create(conn, org_id=org.id, program_name="Newco3 Package 26-27",
                          period_from="2026-09-01", period_to="2027-09-01")
    # placement-attached only: org_id NULL, placement_id set — legal per
    # tasks_repo.open_tasks_for_client's docstring. open_tasks(org_id=...)
    # alone drops this row; the followups step must not.
    tasks.create(conn, "Confirm bound terms", placement_id=p.id)

    status = {s.step.key: s for s in onboarding.completeness(conn, org.id)}
    assert status["followups"].state == onboarding.COMPLETE
    assert "1 open task" in status["followups"].summary


def test_incomplete_clients_lists_missing_labels(conn):
    from bookkit.services import onboarding

    org = orgs.create(conn, name="Fresh LLC", kind="client")  # status defaults to prospect
    got = onboarding.incomplete_clients(conn, date(2026, 8, 12))
    assert [o.id for o, _ in got] == [org.id]
    _, missing = got[0]
    assert "contacts" in missing and "program" in missing


def test_compose_projects_full_report_live_projects_only(conn):
    from bookkit.services.export_open_items import compose_projects

    org = orgs.create(conn, name="Proj Co", kind="client", status="active", owner="grant")
    live = projects_repo.create_project(
        conn, org.id, "Warehouse Expansion", status="active",
        start_on="2026-06-01", end_on="2027-06-01",
    )
    projects_repo.add_need(
        conn, live.id, "Builder's Risk", "2026-09-01",
        limit_cents=25_000_000_00, notes="GC requires evidence",
    )
    projects_repo.add_need(conn, live.id, "GL", "2026-09-15", status="placed")
    done = projects_repo.create_project(conn, org.id, "Old HQ Fit-out", status="completed")
    projects_repo.add_need(conn, done.id, "Property", "2025-01-01")
    projects_repo.create_project(conn, org.id, "Shelved", status="cancelled")

    sections = compose_projects(conn, org.id)
    assert len(sections) == 1  # completed and cancelled projects are not live
    section = sections[0]
    assert section.label == "Warehouse Expansion — Active (2026-06-01 → 2027-06-01)"
    # every need regardless of status; line, notes, needed-by, prettified
    # status, formatted limit — and NO days-open (five columns, no date math)
    assert section.rows == (
        ("Builder's Risk", "GC requires evidence", "2026-09-01", "Identified", "$25,000,000"),
        ("GL", "", "2026-09-15", "Placed", ""),
    )


def test_compose_projects_needless_live_project_still_sections(conn):
    from bookkit.services.export_open_items import compose_projects

    org = orgs.create(conn, name="Plan Co", kind="client")
    projects_repo.create_project(conn, org.id, "Planning Stage")  # status "planned"
    sections = compose_projects(conn, org.id)
    assert sections[0].label == "Planning Stage — Planned"
    assert sections[0].rows == ()


def test_compose_projects_empty_when_no_live_projects(conn):
    from bookkit.services.export_open_items import compose_projects

    org = orgs.create(conn, name="No Proj Co", kind="client")
    assert compose_projects(conn, org.id) == []
    projects_repo.create_project(conn, org.id, "Done", status="completed")
    assert compose_projects(conn, org.id) == []


def test_compose_soi_linked_placement_uses_towerkit_soi(conn, tmp_path):
    from towerkit.model import Layer, Line, Participant, Period, Program, dump_program
    from towerkit.model import Placement as TkPlacement

    from bookkit.services.export_open_items import compose_soi

    org = orgs.create(conn, name="Linked Co", kind="client", status="active")
    p = placements.create(
        conn, org.id, "2026 Package", "2025-10-01", "2026-10-01", status="bound"
    )
    program = Program(
        insured="Linked Co", program="Package Program", placement=TkPlacement.BOUND,
        period=Period(start=date(2025, 10, 1), end=date(2026, 10, 1)),
        lines=[Line(id="gl", name="General Liability", abbr="GL")],
        layers=[
            Layer(
                id="gl1", name="Primary GL", applies_to=["gl"],
                attach=0, limit=1_000_000, premium=52_000,
                participants=[Participant(carrier="Zurich", share_bps=10_000)],
            )
        ],
    )
    path = tmp_path / "package.json"
    dump_program(program, path)
    placements.update(conn, p.id, program_path=str(path))

    sections = compose_soi(conn, org.id, date(2026, 1, 1))
    assert len(sections) == 1
    section = sections[0]
    # build_soi's unlabeled section takes the program name as its label
    assert section.label == "2026 Package"
    row = section.rows[0]
    assert row.insured == "Linked Co"
    assert row.coverage == "General Liability"
    assert row.carrier == "Zurich"
    assert row.effective == date(2025, 10, 1)
    assert row.premium == 52_000  # whole dollars, straight from the file


def test_compose_soi_unlinked_placement_gets_book_data_section(conn):
    from bookkit.services.export_open_items import compose_soi

    org = orgs.create(conn, name="Paper Co", kind="client")
    placements.create(
        conn, org.id, "Legacy Property", "2025-01-01", "2026-01-01",
        status="bound", total_premium=12_345_00,
    )
    sections = compose_soi(conn, org.id, date(2025, 6, 1))
    assert len(sections) == 1
    assert sections[0].label == "Legacy Property (Bound)"
    row = sections[0].rows[0]
    assert row.insured == "Paper Co"
    assert row.coverage == "Legacy Property"
    assert row.carrier == "See policy documents"
    assert row.policy_number == ""
    assert row.effective == date(2025, 1, 1)
    assert row.expiration == date(2026, 1, 1)
    assert row.limits == "" and row.retention == ""
    assert row.premium == 12_345  # cents → whole dollars via the money boundary


def test_compose_soi_unreadable_file_falls_back_to_book_data(conn, tmp_path):
    from bookkit.services.export_open_items import compose_soi

    org = orgs.create(conn, name="Moved Co", kind="client")
    placements.create(
        conn, org.id, "Moved Program", "2025-01-01", "2026-01-01",
        program_path=str(tmp_path / "gone.json"),  # linked, but the file moved
    )
    sections = compose_soi(conn, org.id, date(2025, 6, 1))
    assert len(sections) == 1  # the policy list is never silently partial
    assert sections[0].rows[0].carrier == "See policy documents"


def test_compose_soi_empty_when_no_placements(conn):
    from bookkit.services.export_open_items import compose_soi

    org = orgs.create(conn, name="Bare Co", kind="client")
    assert compose_soi(conn, org.id, date(2026, 8, 18)) == []


# --- C3: expired policy years are not a current Schedule of Insurance --------


def test_compose_soi_excludes_an_expired_policy_year(conn):
    """A prior year renders IDENTICALLY to current cover — same columns, same
    styling, "(Bound)" true in the past tense. Exclusion is decided on
    period_to, the same date the Expiration column prints."""
    from bookkit.services.export_open_items import compose_soi

    org = orgs.create(conn, name="Two Years Co", kind="client")
    placements.create(conn, org.id, "2024 Casualty Program",
                      "2024-09-07", "2025-09-07", status="bound")
    placements.create(conn, org.id, "2025 Casualty Program",
                      "2025-09-07", "2026-09-07", status="bound")

    labels = [s.label for s in compose_soi(conn, org.id, date(2026, 8, 18))]
    assert labels == ["2025 Casualty Program (Bound)"]


def test_compose_soi_keeps_cover_expiring_today(conn):
    """Cover runs to the END of its last day: the comparison is strictly `<`.
    A `<=` here drops a client's live policy from their schedule on its final
    day, which is the day they are most likely to be reading it."""
    from bookkit.services.export_open_items import compose_soi

    org = orgs.create(conn, name="Last Day Co", kind="client")
    placements.create(conn, org.id, "Expiring Today", "2025-08-18", "2026-08-18",
                      status="bound")

    today = date(2026, 8, 18)
    assert [s.label for s in compose_soi(conn, org.id, today)] == [
        "Expiring Today (Bound)"]
    # and gone the next morning
    assert compose_soi(conn, org.id, date(2026, 8, 19)) == []


def test_compose_soi_keeps_a_program_whose_earliest_line_has_already_lapsed(
    conn, tmp_path
):
    """THE renewal_on TRAP. renewal_on is the EARLIEST line end, because
    attention must open when the first layer runs out. Exclusion asks the
    opposite question. This program's IM layer ended in March; its GL runs to
    October. Deciding exclusion on the earliest line end would take a live
    General Liability policy off the client's Schedule of Insurance."""
    from towerkit.model import Layer, Line, Participant, Period, Program, dump_program
    from towerkit.model import Placement as TkPlacement

    from bookkit.services.export_open_items import compose_soi

    org = orgs.create(conn, name="Staggered Co", kind="client", status="active")
    p = placements.create(
        conn, org.id, "2026 Package", "2025-10-01", "2026-10-01", status="bound"
    )
    program = Program(
        insured="Staggered Co", program="Package", placement=TkPlacement.BOUND,
        period=Period(start=date(2025, 10, 1), end=date(2026, 10, 1)),
        lines=[
            Line(id="gl", name="General Liability", abbr="GL"),
            Line(id="im", name="Inland Marine", abbr="IM"),
        ],
        layers=[
            Layer(
                id="gl1", name="Primary GL", applies_to=["gl"],
                attach=0, limit=1_000_000, premium=52_000,
                participants=[Participant(carrier="Zurich", share_bps=10_000)],
            ),
            Layer(
                id="im1", name="IM", applies_to=["im"],
                period=Period(start=date(2025, 10, 1), end=date(2026, 3, 1)),
                attach=0, limit=250_000, premium=4_000,
                participants=[Participant(carrier="Chubb", share_bps=10_000)],
            ),
        ],
    )
    path = tmp_path / "package.json"
    dump_program(program, path)
    placements.update(conn, p.id, program_path=str(path))

    from bookkit import sync
    # the trap is real: renewal_on for this placement IS the lapsed IM date
    assert sync.line_ends(str(path))[0][1] == date(2026, 3, 1)

    sections = compose_soi(conn, org.id, date(2026, 6, 1))
    coverages = [r.coverage for s in sections for r in s.rows]
    assert "General Liability" in coverages, (
        "live GL cover fell off the schedule — exclusion used the earliest "
        "line end instead of the program period"
    )


def test_all_expired_account_gets_no_soi_sheet_rather_than_an_empty_one(
    conn, tmp_path
):
    """The sheet-inclusion rule under C3: every placement historic ⇒ the tab
    is omitted, exactly as Information Requests and Projects are when empty.
    What must NEVER happen is a Schedule of Insurance sheet carrying column
    headers and no policies."""
    from openpyxl import load_workbook

    from bookkit.services.export_open_items import write

    org = orgs.create(conn, name="Lapsed Co", kind="client")
    placements.create(conn, org.id, "2024 Program", "2024-01-01", "2025-01-01",
                      status="bound")
    tasks.create(conn, "renew the whole account", org_id=org.id, category="Renewal")

    path = write(conn, org.id, tmp_path / "l.xlsx", date(2026, 8, 18))
    wb = load_workbook(path)
    assert "Schedule of Insurance" not in wb.sheetnames

    # and the same account, exported while that year was still running, does
    # get the sheet — so the absence above is C3 and not a broken writer
    path2 = write(conn, org.id, tmp_path / "l2.xlsx", date(2024, 6, 1))
    wb2 = load_workbook(path2)
    assert "Schedule of Insurance" in wb2.sheetnames
    assert wb2["Schedule of Insurance"]["A2"].value == "2024 Program (Bound)"


def test_premium_dollars_delegates_then_floors_for_display():
    from bookkit.services.export_open_items import _premium_dollars

    assert _premium_dollars(None) is None
    assert _premium_dollars(500_000_00) == 500_000
    # sub-dollar cents: money.cents_to_dollars refuses; the SOI's whole-dollar
    # display column floors instead (format_cents_compact's documented rule)
    assert _premium_dollars(500_000_50) == 500_000


# --- sheet 2 (assembler): Information Requests -----------------------------


def test_write_four_sheet_order_when_rfi_outstanding(conn, tmp_path):
    from bookkit.services.export_open_items import write

    client = orgs.create(conn, kind="client", name="Acme", status="active", owner="grant")
    tasks.create(conn, "Chase updated loss runs", org_id=client.id)
    placements.create(
        conn, client.id, "Acme Property 25-26", "2025-10-01", "2026-10-01",
        status="bound", total_premium=250_000_00,
    )
    live = projects_repo.create_project(conn, client.id, "Warehouse Expansion")
    projects_repo.add_need(conn, live.id, "Builder's Risk", "2026-09-01")

    market = orgs.create(conn, name="Sompo", kind="market")
    req = rfi.create_request(
        conn, client.id, "property questions", "2026-08-05", due_on="2026-08-19",
        market_org_id=market.id,
    )
    rfi.add_item(conn, req.id, "how many locations?", detail="Please **list** them all.")

    path = write(conn, client.id, tmp_path / "w.xlsx", date(2026, 8, 13))
    from openpyxl import load_workbook  # test-only import; src never imports it
    wb = load_workbook(path)
    assert wb.sheetnames == [
        "Open Items — Acme", "Information Requests", "Projects",
        "Schedule of Insurance",
    ]
    ws = wb["Information Requests"]
    assert [c.value for c in ws[1]] == ["Item", "Detail", "Type", "Needed by"]
    # row 2 is the leading merged header line
    assert ws["A2"].value == "Items we need from you"
    values = [cell.value for row in ws.iter_rows() for cell in row]
    assert "how many locations?" in values
    assert "Please list them all." in values
    assert "Sompo — property questions · asked 5 Aug · due 19 Aug" in values


def test_write_omits_information_requests_sheet_when_nothing_outstanding(conn, tmp_path):
    """An RFI request that exists but has no outstanding items (received or
    waived) must not add a 4th tab — omitted, not rendered blank, matching
    the Projects sheet rule."""
    from bookkit.services.export_open_items import write

    org = orgs.create(conn, name="Solo Co", kind="client")
    tasks.create(conn, "one open task", org_id=org.id)
    req = rfi.create_request(conn, org.id, "closed ask", "2026-08-05")
    item = rfi.add_item(conn, req.id, "already answered")
    rfi.update_item(conn, item.id, status="received", received_on="2026-08-12")

    path = write(conn, org.id, tmp_path / "s.xlsx", date(2026, 8, 13))
    from openpyxl import load_workbook
    assert load_workbook(path).sheetnames == ["Open Items — Solo Co"]


def test_write_four_sheet_deterministic(conn, tmp_path):
    from bookkit.services.export_open_items import write

    client = orgs.create(conn, kind="client", name="Det Co", status="active")
    placements.create(conn, client.id, "Det Program", "2025-01-01", "2026-01-01")
    live = projects_repo.create_project(conn, client.id, "Det Project")
    projects_repo.add_need(conn, live.id, "GL", "2026-09-01")
    req = rfi.create_request(conn, client.id, "onboarding docs", "2026-08-05")
    rfi.add_item(conn, req.id, "audited financials", category="Financials")
    rfi.add_item(conn, req.id, "anything else")

    a = write(conn, client.id, tmp_path / "a.xlsx", date(2026, 8, 13))
    b = write(conn, client.id, tmp_path / "b.xlsx", date(2026, 8, 13))
    assert a.read_bytes() == b.read_bytes()  # four sheets, one finalize, same bytes


# --- sheet 1 / sheet 2 row-height estimate (Grant scope-add, mid-flight) ---


def test_open_items_column_widths_widened(conn, tmp_path):
    from bookkit.services.export_open_items import write

    org = orgs.create(conn, name="Widths Co", kind="client")
    tasks.create(conn, "one open task", org_id=org.id)
    path = write(conn, org.id, tmp_path / "w.xlsx", date(2026, 8, 13))
    from openpyxl import load_workbook
    ws = load_workbook(path).active
    assert ws.column_dimensions["B"].width == 50.0  # Description
    assert ws.column_dimensions["C"].width == 75.0  # Detail


def test_open_items_long_unbroken_description_gets_three_line_height(conn, tmp_path):
    from bookkit.services.export_open_items import write

    org = orgs.create(conn, name="Long Co", kind="client")
    tasks.create(
        conn, "chase loss runs", org_id=org.id,
        description="x" * 101,  # ceil(101 / 50) == 3 lines at the new width
    )
    path = write(conn, org.id, tmp_path / "w.xlsx", date(2026, 8, 13))
    from openpyxl import load_workbook
    ws = load_workbook(path).active
    # row 1 header, row 2 the "General — Long Co" section label, row 3 the task
    assert ws.row_dimensions[3].height >= 54.0


def test_open_items_short_row_keeps_two_line_floor(conn, tmp_path):
    from bookkit.services.export_open_items import write

    org = orgs.create(conn, name="Short Co", kind="client")
    tasks.create(conn, "short one", org_id=org.id, description="brief")
    path = write(conn, org.id, tmp_path / "w.xlsx", date(2026, 8, 13))
    from openpyxl import load_workbook
    ws = load_workbook(path).active
    # row 1 header, row 2 the "General — Short Co" section label, row 3 the task
    assert ws.row_dimensions[3].height == 36.0


def test_information_requests_detail_column_matches_open_items_width(conn, tmp_path):
    from bookkit.services.export_open_items import write

    client = orgs.create(conn, kind="client", name="Family Co", status="active")
    req = rfi.create_request(conn, client.id, "onboarding docs", "2026-08-05")
    rfi.add_item(conn, req.id, "audited financials", detail="y" * 151)  # ceil(151/75)==3
    path = write(conn, client.id, tmp_path / "w.xlsx", date(2026, 8, 13))
    from openpyxl import load_workbook
    wb = load_workbook(path)
    ws = wb["Information Requests"]
    assert ws.column_dimensions["B"].width == 75.0  # Detail
    # row 1 header, row 2 the leading "Items we need from you" label,
    # row 3 the request's own section label, row 4 the item row
    assert ws.row_dimensions[4].height >= 54.0
