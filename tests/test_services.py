from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from bookkit import seed
from bookkit.repo import events, opportunities, orgs, placements, tasks
from bookkit.services import book, capture, hit_rate, pipeline, renewals, sla, staleness, undo

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
    org = orgs.list_orgs(seeded, kind="client")[0]
    orgs.update(seeded, org.id, status="dormant")
    result = undo.undo_last(seeded)
    assert result is not None and result.field == "status"
    assert orgs.get(seeded, org.id).status == org.status


def test_undo_soft_delete(seeded) -> None:
    org = orgs.list_orgs(seeded, kind="client")[0]
    task = tasks.create(seeded, "Ephemeral", org_id=org.id)
    tasks.delete(seeded, task.id)
    result = undo.undo_last(seeded)
    assert result is not None and result.field == "deleted_at"
    assert tasks.get(seeded, task.id).status == "open"


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
