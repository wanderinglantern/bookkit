"""The missing middle: a quote that can lapse, and the work under it.

The AE review's finding (ROADMAP, 2026-08-18): bookkit tracked work SENT and
work BOUND and nothing between them. `submissions.outstanding()` filters
status='out', so the moment a market answered, the row left the past-SLA
queue and entered no queue anywhere — three weeks of comparing terms and
chasing subjectivities, invisible on every surface. Rated the only gap that
loses money rather than time.

Every test here was mutation-proven: the production behaviour was broken, the
named test was watched to fail, and the code restored.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

from bookkit import db
from bookkit.forms import entities as ef
from bookkit.forms.spec import parse_values
from bookkit.models import SUBJECTIVITY_STATUSES, SubmissionStatus
from bookkit.repo import contacts, orgs, placements, submissions
from bookkit.services import batches as batches_svc
from bookkit.services import quotes as quotes_svc
from bookkit.services import sla
from bookkit.tui import theme

TODAY = date(2026, 8, 14)
MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


# --- fixtures ----------------------------------------------------------------


def _book(conn: sqlite3.Connection) -> tuple[str, str, str]:
    """One client, one market, one placement. Returns (org, market, placement)."""
    client = orgs.create(conn, name="Atomic Industries", kind="client")
    market = orgs.create(conn, name="Travelers", kind="market")
    placement = placements.create(
        conn, client.id, "Property Program", "2026-01-01", "2027-01-01"
    )
    return client.id, market.id, placement.id


def _quote(
    conn: sqlite3.Connection,
    market_id: str,
    placement_id: str,
    expires_on: str | None,
    sent_on: str = "2026-07-01",
    **fields: object,
):
    sub = submissions.create(
        conn, market_id, sent_on, placement_id=placement_id, **fields
    )
    return submissions.update(
        conn,
        sub.id,
        status=SubmissionStatus.QUOTED.value,
        quote_expires_on=expires_on,
    )


def _iso(offset_days: int) -> str:
    return (TODAY + timedelta(days=offset_days)).isoformat()


# --- the migration -----------------------------------------------------------


def test_migration_012_is_additive_only() -> None:
    """CLAUDE.md's rule and Grant's standing ruling for the night: additive
    migrations only, nothing rewrites existing rows. A DROP, an UPDATE or a
    table rebuild in this file would take data with it and there is no undo.
    """
    sql = (MIGRATIONS / "012_quote_terms.sql").read_text()
    statements = [
        line.strip().upper()
        for line in sql.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    body = " ".join(statements)
    for forbidden in ("DROP ", "DELETE ", "UPDATE ", "RENAME ", "INSERT "):
        assert forbidden not in body, f"012 is not additive: it contains {forbidden!r}"
    assert "ALTER TABLE SUBMISSION ADD COLUMN QUOTE_EXPIRES_ON" in body
    assert "CREATE TABLE SUBMISSION_SUBJECTIVITY" in body


def _newest_migration() -> int:
    """The highest migration on disk. Every assertion below is about the
    snapshot's ORDER, never about which version happens to be newest."""
    conn = db.connect(":memory:", migrate=False)
    try:
        return max(v for v, _ in db.pending_migrations(conn))
    finally:
        conn.close()


def test_an_existing_book_is_snapshotted_before_a_migration_runs(
    tmp_path: Path,
) -> None:
    """A schema change is the same bet a bulk import makes, with worse odds:
    a half-applied one is not something a user can unpick by hand. The
    importers snapshot first; migrations now do too, at db.connect, which is
    where they actually run for every surface.
    """
    path = tmp_path / "book.db"
    # a book at the PREVIOUS schema version, holding real rows — which is what
    # every one of Grant's databases is the first time it meets 012
    conn = db.connect(path, migrate=False)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version"
        " (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for version, sql_path in sorted(db.pending_migrations(conn))[:11]:
        conn.executescript(sql_path.read_text())
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (version, db.utc_now()),
        )
    org = orgs.create(conn, name="Atomic Industries", kind="client")
    conn.close()
    assert db.schema_version(db.connect(path, migrate=False)) == 11

    conn = db.connect(path)          # this is the call that migrates
    # the NEWEST migration, not a literal: this test is about the snapshot,
    # and pinning 012 here made it fail the day 013 was written — which reads
    # as the snapshot breaking rather than as the number moving.
    assert db.schema_version(conn) == _newest_migration()
    conn.close()

    backups = sorted((tmp_path / "backups").glob("book.db.*.bak"))
    assert backups, "no snapshot was taken before the pending migration"
    copy = sqlite3.connect(backups[-1])
    try:
        names = [r[0] for r in copy.execute("SELECT name FROM org")]
    finally:
        copy.close()
    assert org.name in names, "the snapshot does not contain the book's rows"


def test_a_fresh_database_is_not_snapshotted(tmp_path: Path) -> None:
    """001_initial on an empty file cannot destroy data that is not there, and
    a .bak beside every newly created book is noise that teaches people to
    ignore the directory."""
    db.connect(tmp_path / "new.db").close()
    assert not (tmp_path / "backups").exists()


def test_an_up_to_date_book_is_not_snapshotted_on_every_open(tmp_path: Path) -> None:
    """The TUI, the CLI, the web app and the MCP server all call connect().
    A snapshot per open would fill the disk with copies of an unchanged file.
    """
    path = tmp_path / "book.db"
    db.connect(path).close()
    before = len(list((tmp_path / "backups").glob("*"))) if (tmp_path / "backups").exists() else 0
    db.connect(path).close()
    db.connect(path).close()
    after = len(list((tmp_path / "backups").glob("*"))) if (tmp_path / "backups").exists() else 0
    assert after == before


def _book_at_schema_11(path: Path) -> str:
    """A book at the PREVIOUS schema version holding real rows — which is what
    every one of Grant's databases is the first time it meets 012. Returns the
    org name written into it."""
    conn = db.connect(path, migrate=False)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version"
        " (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    for version, sql_path in sorted(db.pending_migrations(conn))[:11]:
        conn.executescript(sql_path.read_text())
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (version, db.utc_now()),
        )
    org = orgs.create(conn, name="Atomic Industries", kind="client")
    conn.close()
    assert db.schema_version(db.connect(path, migrate=False)) == 11
    return org.name


def test_a_failed_snapshot_aborts_the_migration(tmp_path: Path) -> None:
    """THE test the snapshot exists for, and the one thing nothing pinned.

    A snapshot that cannot be written and a migration that runs anyway is
    strictly worse than no snapshot at all: the user believes there is a
    rollback and the schema has already changed under them. The behaviour was
    correct — `snapshot_before_migrations` raises and `apply_migrations` never
    runs — but wrapping the call in `try/except Exception: pass` left the whole
    suite green, and this sits on the one choke point every surface passes
    through (TUI, CLI, web, MCP all reach migrations via db.connect).
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    path = tmp_path / "book.db"
    _book_at_schema_11(path)

    backups = tmp_path / "backups"
    backups.mkdir()
    os.chmod(backups, 0o500)          # readable, listable, NOT writable
    try:
        with pytest.raises(sqlite3.Error):
            db.connect(path)
    finally:
        os.chmod(backups, 0o700)      # or tmp_path cannot be cleaned up

    # the file is untouched: no half-migrated book behind a rollback that
    # does not exist
    conn = db.connect(path, migrate=False)
    try:
        assert db.schema_version(conn) == 11
    finally:
        conn.close()
    assert not list(backups.glob("*.bak"))


def test_the_snapshot_is_taken_before_the_migration_not_after(
    tmp_path: Path,
) -> None:
    """A snapshot of the ALREADY-migrated file is not a rollback, it is a
    second copy of the thing you wanted to undo. The existing test only asked
    that a backup exist and contain the book's rows, both of which stay true
    if the two calls swap order — so the order itself is pinned here, by the
    schema version inside the copy."""
    path = tmp_path / "book.db"
    _book_at_schema_11(path)

    conn = db.connect(path)
    assert db.schema_version(conn) == _newest_migration()
    conn.close()

    backups = sorted((tmp_path / "backups").glob("book.db.*.bak"))
    assert backups, "no snapshot was taken before the pending migration"
    copy = db.connect(backups[-1], migrate=False)
    try:
        assert db.schema_version(copy) == 11, (
            "the snapshot was taken AFTER the migration — it cannot roll one back"
        )
    finally:
        copy.close()


class _TornVacuum:
    """A connection whose `VACUUM INTO` lands only PART of the copy — the disk
    filled, the process was killed, the network mount dropped. It runs the real
    VACUUM INTO and then truncates the result, so what the filesystem is left
    holding is a genuine prefix of a genuine database.

    Nothing about the integrity check is stubbed. That is the whole point: the
    previous version of this test monkeypatched `integrity_check` to return
    False, and SQLite never does that on a malformed file — it RAISES
    `sqlite3.DatabaseError`. A cleanup that lives inside `if not ok:` is
    therefore never reached, so the old test passed while the bug was live."""

    KEPT_BYTES = 216  # the size the reviewer's orphaned copy actually landed at

    def __init__(self, source: sqlite3.Connection) -> None:
        self._source = source

    def execute(self, sql: str, params: tuple[str, ...] = ()) -> None:
        assert "VACUUM INTO" in sql, sql
        dest = Path(params[0])
        scratch = dest.with_name(dest.name + ".whole")
        self._source.execute("VACUUM INTO ?", (str(scratch),))
        dest.write_bytes(scratch.read_bytes()[: self.KEPT_BYTES])
        scratch.unlink()


def test_a_torn_backup_does_not_survive_on_disk(tmp_path: Path) -> None:
    """A torn VACUUM INTO leaves a file that looks exactly like a good backup:
    same directory, same timestamped name, same mode. The caller is told it
    failed; the person who comes looking for a rollback three weeks later is
    not. Absence is the only state that distinguishes it.

    This mechanism is the rollback for every migration and every `seed --force`,
    so the branch that deletes the bad copy has to fire on what actually
    happens, not on a case SQLite cannot produce."""
    path = tmp_path / "book.db"
    conn = db.connect(path)
    orgs.create(conn, name="Atomic Industries", kind="client")

    dest = tmp_path / "backups" / "book.db.torn.bak"
    with pytest.raises(RuntimeError, match="integrity check"):
        db.backup(_TornVacuum(conn), dest)  # type: ignore[arg-type]
    conn.close()

    left = dest.stat().st_size if dest.exists() else 0
    assert not dest.exists(), (
        f"a corrupt backup was left where a good one goes — {left} bytes under "
        f"an ordinary backup name, indistinguishable from a usable rollback"
    )


def test_a_backup_that_merely_reports_not_ok_is_also_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other branch. Some damage does come back as a row rather than a
    raise, and both paths have to end with no file — kept as a second test
    rather than folded in, because they are two different code paths."""
    path = tmp_path / "book.db"
    conn = db.connect(path)
    orgs.create(conn, name="Atomic Industries", kind="client")

    monkeypatch.setattr(db, "integrity_check", lambda _conn: False)
    dest = tmp_path / "backups" / "book.db.notok.bak"
    with pytest.raises(RuntimeError, match="integrity check"):
        db.backup(conn, dest)
    conn.close()
    assert not dest.exists(), "a corrupt backup was left where a good one goes"


# --- the expiry boundary -----------------------------------------------------


def test_a_quote_expiring_today_is_not_expired(conn: sqlite3.Connection) -> None:
    """A quote is good for the whole of its last day. A broker who binds at
    4pm on the expiry date has bound in time, and a surface that calls that
    quote dead sends them to re-market terms they still hold."""
    _, market, placement = _book(conn)
    _quote(conn, market, placement, _iso(0))
    item = quotes_svc.expiring(conn, TODAY)[0]
    assert item.days_remaining == 0
    assert item.is_expired is False
    assert item.is_urgent is True
    assert item.expiry_word == "expires today"


def test_a_quote_that_expired_yesterday_is_expired(conn: sqlite3.Connection) -> None:
    """The other side of the same boundary — one day apart, opposite answers."""
    _, market, placement = _book(conn)
    _quote(conn, market, placement, _iso(-1))
    item = quotes_svc.expiring(conn, TODAY)[0]
    assert item.days_remaining == -1
    assert item.is_expired is True
    assert item.expiry_word == "expired 1d ago"


def test_expiry_is_decided_by_the_countdown_not_by_a_bucket() -> None:
    """`days_remaining < 0`, the same rule renewals use for overdue — never
    by where a row lands in a grid (CLAUDE.md)."""
    assert quotes_svc.expiry_state(-1) == quotes_svc.EXPIRED
    assert quotes_svc.expiry_state(0) == quotes_svc.URGENT
    assert quotes_svc.expiry_state(1) == quotes_svc.URGENT
    assert quotes_svc.expiry_state(quotes_svc.URGENT_DAYS) == quotes_svc.URGENT
    assert quotes_svc.expiry_state(quotes_svc.URGENT_DAYS + 1) == quotes_svc.LIVE
    assert quotes_svc.expiry_state(None) == quotes_svc.UNDATED


def test_the_date_printed_is_the_date_counted_to(conn: sqlite3.Connection) -> None:
    """The defect four reviewers found independently: a date twenty days in
    the FUTURE rendered red as "70d over" because the date came off one object
    and the count off another. QuoteItem resolves both from the same stored
    column, so they cannot come apart."""
    _, market, placement = _book(conn)
    quote = _quote(conn, market, placement, _iso(30))
    item = quotes_svc.expiring(conn, TODAY)[0]
    assert item.expires_on == quote.quote_expires_on
    assert item.days_remaining == 30
    cell = theme.expiry_text(item.expires_on, item.days_remaining).plain
    assert item.expires_on in cell and "30d" in cell


# --- the attention window ----------------------------------------------------


def test_an_expired_quote_never_falls_off_the_window(conn: sqlite3.Connection) -> None:
    """Overdue items never fall off — CLAUDE.md's rule for the whole attention
    model, and money already lost is the worst possible thing to hide."""
    _, market, placement = _book(conn)
    _quote(conn, market, placement, _iso(-400))
    assert len(quotes_svc.expiring(conn, TODAY, days=120)) == 1


def test_a_quote_beyond_the_window_is_not_in_the_queue(
    conn: sqlite3.Connection,
) -> None:
    _, market, placement = _book(conn)
    _quote(conn, market, placement, _iso(121))
    assert quotes_svc.expiring(conn, TODAY, days=120) == []


def test_a_quote_with_no_expiry_is_not_a_clock(conn: sqlite3.Connection) -> None:
    """Undated quotes are excluded from the CHASE queue, exactly as
    rfi.outstanding_rows excludes an undated request. Inventing a date would
    be guessing at the one number the feature exists to be honest about — and
    a bare number is not a date anywhere in this codebase."""
    client, market, placement = _book(conn)
    _quote(conn, market, placement, None)
    assert quotes_svc.expiring(conn, TODAY, days=120) == []
    # but it IS a quote the account is holding, so its own tab still shows it
    on_account = quotes_svc.for_org(conn, client, today=TODAY)
    assert len(on_account) == 1
    assert on_account[0].expiry_word == "no expiry"


def test_only_quoted_submissions_reach_the_queue(conn: sqlite3.Connection) -> None:
    """A bound placement and one still out at market both have dates on them;
    neither is a quote whose terms are running out."""
    _, market, placement = _book(conn)
    still_out = submissions.create(
        conn, market, "2026-07-01", placement_id=placement
    )
    submissions.update(conn, still_out.id, quote_expires_on=_iso(5))
    bound = submissions.create(conn, market, "2026-07-02", placement_id=placement)
    submissions.update(
        conn, bound.id, status=SubmissionStatus.BOUND.value, quote_expires_on=_iso(5)
    )
    assert quotes_svc.expiring(conn, TODAY) == []


def test_a_quote_does_not_disturb_the_sla_queue(conn: sqlite3.Connection) -> None:
    """`outstanding()` still means "no answer yet". Widening it to cover
    quotes would have made every SLA day-count wrong, so the quote queries
    are new ones beside it rather than a change to it."""
    _, market, placement = _book(conn)
    _quote(conn, market, placement, _iso(5))
    submissions.create(conn, market, "2026-07-01", placement_id=placement)
    late = sla.past_sla(conn, TODAY, sla_days=10)
    assert len(late) == 1
    assert late[0].submission.status == SubmissionStatus.OUT


# --- subjectivities ----------------------------------------------------------


def test_subjectivities_are_counted_open_and_total(conn: sqlite3.Connection) -> None:
    """Both numbers, always: 0 of 0 (nobody recorded any) is a different fact
    from 0 of 4 (all cleared, ready to bind)."""
    client, market, placement = _book(conn)
    quote = _quote(conn, market, placement, _iso(5))
    assert submissions.subjectivity_counts(conn, quote.id) == (0, 0)
    a = submissions.add_subjectivity(conn, quote.id, "signed application")
    submissions.add_subjectivity(conn, quote.id, "loss runs through 8/1")
    assert submissions.subjectivity_counts(conn, quote.id) == (2, 2)
    submissions.update_subjectivity(conn, a.id, status="met", satisfied_on=_iso(-1))
    assert submissions.subjectivity_counts(conn, quote.id) == (1, 2)
    item = quotes_svc.for_org(conn, client, today=TODAY)[0]
    assert (item.open_subjectivities, item.total_subjectivities) == (1, 2)


def test_a_met_subjectivity_leaves_the_chase_list(conn: sqlite3.Connection) -> None:
    client, market, placement = _book(conn)
    quote = _quote(conn, market, placement, _iso(5))
    subj = submissions.add_subjectivity(
        conn, quote.id, "sprinkler certificate", due_on=_iso(3)
    )
    rows = submissions.outstanding_subjectivity_rows_for_org(conn, client)
    assert [r["description"] for r in rows] == ["sprinkler certificate"]
    submissions.update_subjectivity(conn, subj.id, status="met")
    assert submissions.outstanding_subjectivity_rows_for_org(conn, client) == []


def test_a_deleted_subjectivity_is_not_counted(conn: sqlite3.Connection) -> None:
    _, market, placement = _book(conn)
    quote = _quote(conn, market, placement, _iso(5))
    subj = submissions.add_subjectivity(conn, quote.id, "signed application")
    submissions.delete_subjectivity(conn, subj.id)
    assert submissions.subjectivity_counts(conn, quote.id) == (0, 0)
    assert submissions.subjectivities_for(conn, quote.id) == []


def test_a_subjectivity_write_is_one_revertible_batch(
    conn: sqlite3.Connection,
) -> None:
    """One writer action is one undo unit, on every surface (CLAUDE.md). The
    event_log field must also be a real column or base.log_event refuses —
    which is what stops `u` from raising IndexError days later."""
    client, market, placement = _book(conn)
    quote = _quote(conn, market, placement, _iso(5))
    subj = submissions.add_subjectivity(conn, quote.id, "signed application")
    with batches_svc.open_batch(
        conn, source="tui", tool="edit_subjectivity",
        summary="met: signed application", org_id=client,
    ) as batch:
        submissions.update_subjectivity(conn, subj.id, status="met")
    assert submissions.get_subjectivity(conn, subj.id).status == "met"
    batches_svc.revert(conn, batch.ref, db.utc_now())
    assert submissions.get_subjectivity(conn, subj.id).status == "outstanding"


def test_the_subjectivity_status_vocabulary_is_controlled_and_extensible() -> None:
    """The TEAM_ROLES pattern: a tuple in models.py, rendered through
    theme.status_text — not a hard-coded picker and not an unbounded string."""
    assert SUBJECTIVITY_STATUSES == ("outstanding", "met", "waived")
    for status in SUBJECTIVITY_STATUSES:
        assert status in theme.STATUS_STYLES, f"{status} has no theme style"


# --- the underwriter, wired at last ------------------------------------------


def test_past_sla_names_the_person_not_only_the_carrier(
    conn: sqlite3.Connection,
) -> None:
    """`submission.underwriter_contact_id` was declared in models.py and
    001_initial.sql and read by NOTHING. Today reported six submissions past
    SLA and named only "Travelers" — which you cannot email."""
    _, market, placement = _book(conn)
    uw = contacts.create(
        conn, market, first_name="Dana", last_name="Reeve",
        email="dana@travelers.example",
    )
    submissions.create(
        conn, market, "2026-07-01", placement_id=placement,
        underwriter_contact_id=uw.id,
    )
    late = sla.past_sla(conn, TODAY, sla_days=10)[0]
    assert late.underwriter is not None
    assert late.underwriter_name == "Dana Reeve"
    assert late.underwriter.email == "dana@travelers.example"
    assert "Dana Reeve" in theme.market_text(late.market.name, late.underwriter_name).plain


def test_an_unnamed_submission_still_reaches_the_sla_queue(
    conn: sqlite3.Connection,
) -> None:
    """No submission written before today could carry an underwriter, because
    no form offered the field. None is honest; dropping the row would not be.
    """
    _, market, placement = _book(conn)
    submissions.create(conn, market, "2026-07-01", placement_id=placement)
    late = sla.past_sla(conn, TODAY, sla_days=10)[0]
    assert late.underwriter is None
    assert theme.market_text(late.market.name, None).plain == "Travelers"


def test_a_removed_underwriter_does_not_drop_the_chase(
    conn: sqlite3.Connection,
) -> None:
    """The chase is still late; you just have to find someone else to chase."""
    _, market, placement = _book(conn)
    uw = contacts.create(conn, market, first_name="Dana", last_name="Reeve")
    submissions.create(
        conn, market, "2026-07-01", placement_id=placement,
        underwriter_contact_id=uw.id,
    )
    contacts.delete(conn, uw.id)
    late = sla.past_sla(conn, TODAY, sla_days=10)
    assert len(late) == 1
    assert late[0].underwriter is None


def test_the_underwriter_picker_offers_market_people_with_their_market(
    conn: sqlite3.Connection,
) -> None:
    """Five identical "Chen" rows is the complaint the review made about
    search; a picker repeats it worse, because you cannot even hover it."""
    client, market, _ = _book(conn)
    contacts.create(conn, market, first_name="Wei", last_name="Chen")
    contacts.create(conn, client, first_name="Wei", last_name="Chen")
    options = ef.underwriter_options(conn)
    assert options == (("Wei Chen — Travelers", options[0][1]),)


# --- the forms ---------------------------------------------------------------


def test_the_response_form_asks_for_the_expiry_and_the_underwriter(
    conn: sqlite3.Connection,
) -> None:
    """Before this it captured premium, limit and decline reason and no expiry
    at all — so a quote arrived and immediately stopped being visible."""
    _, market, placement = _book(conn)
    sub = submissions.create(conn, market, "2026-07-01", placement_id=placement)
    keys = [f.key for f in ef.response_form(sub, conn).fields]
    assert "quote_expires_on" in keys
    assert "underwriter_contact_id" in keys


def test_the_expiry_goes_through_the_date_parser(conn: sqlite3.Connection) -> None:
    """`parse_human_date` refuses a bare number on purpose — dateparser reads
    "5" as a MONTH and future-biases it. The expiry is not routed around it.
    """
    _, market, placement = _book(conn)
    sub = submissions.create(conn, market, "2026-07-01", placement_id=placement)
    spec = ef.response_form(sub, conn)
    with pytest.raises(Exception) as refused:
        parse_values(spec, {"status": "quoted", "quote_expires_on": "5"})
    assert "not a date" in str(refused.value)


def test_recording_a_quote_puts_it_in_the_queue(conn: sqlite3.Connection) -> None:
    """End to end through the shared form path both surfaces use: the seam is
    actually taken, not merely present (CLAUDE.md — a green suite proves
    nothing broke, not that the new path is used)."""
    client, market, placement = _book(conn)
    sub = submissions.create(conn, market, "2026-07-01", placement_id=placement)
    assert quotes_svc.expiring(conn, TODAY) == []
    spec = ef.response_form(sub, conn)
    values = parse_values(spec, {
        "status": "quoted",
        "response_on": "2026-08-12",
        "quoted_premium": "125,000.00",
        "quote_expires_on": "2026-08-19",
    })
    ef.apply_response(conn, sub.id, values)
    queued = quotes_svc.expiring(conn, TODAY)
    assert len(queued) == 1
    assert queued[0].expires_on == "2026-08-19"
    assert queued[0].days_remaining == 5
    assert queued[0].org_id == client
    assert queued[0].submission.quoted_premium == 12_500_000


def test_the_subjectivity_form_saves_and_edits_in_place(
    conn: sqlite3.Connection,
) -> None:
    _, market, placement = _book(conn)
    quote = _quote(conn, market, placement, _iso(5))
    spec = ef.subjectivity_form()
    values = parse_values(
        spec, {"description": "signed application", "status": "outstanding",
               "due_on": "2026-08-20"}
    )
    created = ef.apply_subjectivity(conn, values, quote.id)
    assert created.description == "signed application"
    assert created.due_on == "2026-08-20"

    edit = ef.subjectivity_form(created)
    edited = parse_values(edit, {
        "description": "signed application", "status": "met",
        "satisfied_on": "2026-08-13",
    })
    updated = ef.apply_subjectivity(conn, edited, quote.id, created)
    assert updated.id == created.id
    assert updated.status == "met"


# --- how it reads ------------------------------------------------------------


def test_every_expiry_state_carries_a_word_not_only_a_colour() -> None:
    """CLAUDE.md: colour is signal, and every coloured state carries a glyph
    or a word too. Neither "expired" nor "urgent" may be inferable only from
    a date the reader has to subtract in their head."""
    assert "expired" in theme.expiry_text("2026-08-01", -13).plain
    assert "expires today" in theme.expiry_text("2026-08-14", 0).plain
    assert "5d left" in theme.expiry_text("2026-08-19", 5).plain
    assert "no expiry" in theme.expiry_text(None, None).plain
    # and the date is in the cell with its own countdown, every time
    for iso, days in (("2026-08-01", -13), ("2026-08-14", 0), ("2026-08-19", 5)):
        assert iso in theme.expiry_text(iso, days).plain


def test_the_two_surfaces_use_one_expiry_vocabulary() -> None:
    """The TUI renders through theme.expiry_text and the web through the Jinja
    template, and neither may import the other. Both take their WORDS from
    services.quotes, so one fact cannot grow two vocabularies."""
    for days in (-13, 0, 5, 90, None):
        assert quotes_svc.expiry_word(days) in theme.expiry_text("2026-08-14", days).plain


def test_a_cleared_subjectivity_count_reads_as_good_news() -> None:
    assert theme.subjectivity_text(0, 0).plain == "—"
    assert "0 of 3" in theme.subjectivity_text(0, 3).plain
    assert "2 of 3 open" in theme.subjectivity_text(2, 3).plain


# --- the surfaces ------------------------------------------------------------


async def test_the_navigator_carries_a_quotes_expiring_leaf(
    snapshot_db: Path,
) -> None:
    """A quote expiring inside the 120-day window is exactly the kind of dated
    thing the attention model exists to surface. It joins the set on the SAME
    window; the window itself is untouched."""
    from bookkit.tui.app import BookkitApp

    conn = db.connect(snapshot_db)
    org = orgs.list_orgs(conn, kind="client")[0]
    market = orgs.list_orgs(conn, kind="market")[0]
    placement = placements.for_org(conn, org.id)[0]
    _quote(conn, market.id, placement.id, _iso(-2))
    conn.close()

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        tree = app.screen.query_one("#nav-tree")
        labels = [str(node.label) for node in tree.root.children[0].children]
        quotes_leaf = [line for line in labels if "quotes expiring" in line]
        assert quotes_leaf, f"no quotes leaf among {labels}"
        # a LAPSED quote makes the leaf shout, the way overdue renewals do
        assert "◆" in quotes_leaf[0]


async def test_the_account_pipeline_tab_shows_the_expiry_and_the_chase(
    snapshot_db: Path,
) -> None:
    """Where the work happens: the submissions table gains the expiry and the
    subjectivity count, and the subjectivities themselves get their own
    master/detail table under it."""
    from bookkit.tui.app import BookkitApp
    from bookkit.tui.widgets.tables import ListTable

    conn = db.connect(snapshot_db)
    org = orgs.list_orgs(conn, kind="client")[0]
    market = orgs.list_orgs(conn, kind="market")[0]
    placement = placements.for_org(conn, org.id)[0]
    quote = _quote(conn, market.id, placement.id, _iso(3))
    submissions.add_subjectivity(conn, quote.id, "signed application")
    conn.close()

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("6")
        await pilot.pause()
        subs = app.screen.query_one("#pipeline-subs", ListTable)
        rendered = " ".join(
            str(cell) for row in subs.rows for cell in subs.get_row(row)
        )
        assert "3d left" in rendered, rendered
        assert "1 of 1 open" in rendered, rendered
        subjs = app.screen.query_one("#pipeline-subjs", ListTable)
        assert subjs.row_count == 1


def test_the_pipeline_badge_counts_placement_submissions_too(
    conn: sqlite3.Connection,
) -> None:
    """The tab badge summed only submissions hanging off an OPPORTUNITY, so a
    renewal marketed to six carriers showed a Pipeline count of 0 while the
    tab listed six rows. Found while building the tab that made the rows
    visible in the first place."""
    from bookkit.web.routes.account import _counts

    client, market, placement = _book(conn)
    org = orgs.get(conn, client)
    submissions.create(conn, market, "2026-07-01", placement_id=placement)
    submissions.create(conn, market, "2026-07-02", placement_id=placement)
    assert _counts(conn, org, open_work=0)["pipeline"] == 2


def test_the_web_pipeline_tab_reads_the_expiry_in_words(
    tmp_path: Path, frozen_clock: date
) -> None:
    """The web tab was an "empty — add the first row" placeholder over a real
    pipeline. An expiring quote must read as urgent and an expired one as
    expired, in words, without the reader subtracting dates."""
    from fastapi.testclient import TestClient

    from bookkit.web.app import create_app

    path = tmp_path / "web.db"
    conn = db.connect(path)
    client_id, market, placement = _book(conn)
    org = orgs.get(conn, client_id)
    uw = contacts.create(
        conn, market, first_name="Dana", last_name="Reeve",
        email="dana@travelers.example",
    )
    lapsed = _quote(conn, market, placement, _iso(-4))
    submissions.update(conn, lapsed.id, underwriter_contact_id=uw.id)
    soon = _quote(conn, market, placement, _iso(6))
    submissions.add_subjectivity(conn, soon.id, "loss runs through 8/1")
    conn.close()

    with TestClient(create_app(path), base_url="http://127.0.0.1") as client:
        page = client.get(f"/accounts/{org.ref}/pipeline")
    assert page.status_code == 200
    body = page.text
    assert "expired 4d ago" in body
    assert "6d left" in body
    assert "Dana Reeve" in body
    assert "mailto:dana@travelers.example" in body
    assert "1 of 1 open" in body
    assert "loss runs through 8/1" in body
    # the tab that used to render nothing but "empty — add the first row"
    assert '<div id="quotes-panel">' in body
    assert "Quotes in hand" in body
    assert "Subjectivities outstanding" in body


# --- fix round 1: the undated quote, and the orderings nothing read ----------
#
# Refusing to INVENT an expiry is right and stays. Refusing to SHOW the item
# is a second decision, and the first round made it by accident of the first:
# an undated quote left submissions.outstanding() the moment its status
# flipped, was excluded from quotes_svc.expiring by `IS NOT NULL`, and so
# reached no leaf, no Today pane and nothing that counts. It appeared only if
# somebody opened that one account's tab. That is the missing middle put back
# for the case where the data is thinnest — and thin data correlates with
# sloppy handling, so it is over-represented among the quotes that lapse.


def test_an_undated_quote_is_surfaced_without_inventing_a_date(
    conn: sqlite3.Connection,
) -> None:
    """Both halves at once: it is NOT on the dated queue (no date is guessed)
    and it IS on a surface (no date is needed to say "go and ask")."""
    _, market, placement = _book(conn)
    _quote(conn, market, placement, None)
    assert quotes_svc.expiring(conn, TODAY, days=120) == []
    tail = quotes_svc.undated(conn, TODAY)
    assert len(tail) == 1
    assert tail[0].expires_on is None
    assert tail[0].days_remaining is None
    assert tail[0].expiry_word == "no expiry"
    assert tail[0].expiry_state == quotes_svc.UNDATED


def test_the_undated_tail_holds_only_undated_quotes(
    conn: sqlite3.Connection,
) -> None:
    """A dated quote belongs to the clock queue, wherever its date falls —
    including 200 days out, which arrives on its own. Nothing may reach both
    lists, or the leaf double-counts."""
    _, market, placement = _book(conn)
    _quote(conn, market, placement, _iso(5))
    _quote(conn, market, placement, _iso(200))
    undated = _quote(conn, market, placement, None)
    tail = quotes_svc.undated(conn, TODAY)
    assert [q.submission.id for q in tail] == [undated.id]


def test_the_undated_tail_is_quotes_only(conn: sqlite3.Connection) -> None:
    """A submission still OUT has no expiry either, and it is not a quote —
    it belongs to the past-SLA queue, whose clock is a different clock."""
    _, market, placement = _book(conn)
    submissions.create(conn, market, "2026-07-01", placement_id=placement)
    for status in (SubmissionStatus.DECLINED, SubmissionStatus.BOUND):
        sub = submissions.create(conn, market, "2026-07-01", placement_id=placement)
        submissions.update(conn, sub.id, status=status.value)
    assert quotes_svc.undated(conn, TODAY) == []


def test_a_quote_expiring_on_the_last_day_of_the_window_is_in_the_queue(
    conn: sqlite3.Connection,
) -> None:
    """M05: the horizon is inclusive. `<=` → `<` was green, because the suite
    asserted 121 excluded and never 120 included — a quote expiring on exactly
    the boundary day would have vanished from the queue in silence."""
    _, market, placement = _book(conn)
    _quote(conn, market, placement, _iso(120))
    queue = quotes_svc.expiring(conn, TODAY, days=120)
    assert len(queue) == 1
    assert queue[0].days_remaining == 120


def test_the_chase_queue_leads_with_the_soonest_expiry(
    conn: sqlite3.Connection,
) -> None:
    """M07: the docstring promises soonest-first and nothing read it. The
    queue is worked top-down, so an ordering nothing pins is an ordering that
    can silently put the lapsed quote at the bottom."""
    _, market, placement = _book(conn)
    # sent dates deliberately disagree with expiry dates, and insertion order
    # disagrees with both: an ordering that only LOOKS right because SQLite's
    # sorter is stable over the index scan is not an ordering anything holds
    late = _quote(conn, market, placement, _iso(90), sent_on="2026-06-01")
    lapsed = _quote(conn, market, placement, _iso(-10), sent_on="2026-07-15")
    soon = _quote(conn, market, placement, _iso(3), sent_on="2026-06-20")
    order = [q.submission.id for q in quotes_svc.expiring(conn, TODAY, days=120)]
    assert order == [lapsed.id, soon.id, late.id]


def test_an_account_lists_dated_quotes_before_undated_ones(
    conn: sqlite3.Connection,
) -> None:
    """M09: `for_org`'s documented undated-last ordering, read by nothing.
    Undated last is the point — a quote on a clock outranks one that is only
    a question."""
    client, market, placement = _book(conn)
    nodate = _quote(conn, market, placement, None)
    later = _quote(conn, market, placement, _iso(40))
    sooner = _quote(conn, market, placement, _iso(2))
    order = [q.submission.id for q in quotes_svc.for_org(conn, client, today=TODAY)]
    assert order == [sooner.id, later.id, nodate.id]


def test_subjectivities_list_outstanding_first(conn: sqlite3.Connection) -> None:
    """M13: outstanding-first, whatever the dates say — the ones being chased
    sit at the top. Documented, unread, and a met subjectivity with an early
    due date would otherwise head the list of work to do."""
    _, market, placement = _book(conn)
    quote = _quote(conn, market, placement, _iso(10))
    met = submissions.add_subjectivity(
        conn, quote.id, "signed application", due_on=_iso(-5)
    )
    submissions.update_subjectivity(conn, met.id, status="met")
    still_open = submissions.add_subjectivity(
        conn, quote.id, "loss runs", due_on=_iso(20)
    )
    order = [s.id for s in submissions.subjectivities_for(conn, quote.id)]
    assert order == [still_open.id, met.id]


def test_the_repo_query_behind_a_clients_quotes_is_scoped_to_quoted(
    conn: sqlite3.Connection,
) -> None:
    """M10: `status = 'quoted'` in `quoted_rows_for_org` could be dropped and
    nothing noticed. The service-level scoping IS held, but the repo owns
    every query here (CLAUDE.md) and the account tab reads this row set — a
    declined submission listed under "quotes in hand" is a quote that is not
    in hand."""
    client, market, placement = _book(conn)
    quoted = _quote(conn, market, placement, _iso(9))
    submissions.create(conn, market, "2026-07-01", placement_id=placement)  # out
    for status in (SubmissionStatus.DECLINED, SubmissionStatus.BOUND):
        other = submissions.create(conn, market, "2026-07-01", placement_id=placement)
        submissions.update(conn, other.id, status=status.value)
    rows = submissions.quoted_rows_for_org(conn, client)
    assert [r["id"] for r in rows] == [quoted.id]


def test_urgent_is_two_weeks(conn: sqlite3.Connection) -> None:
    """M32: the states were asserted against URGENT_DAYS itself, so raising it
    from 14 to 45 stayed green — the constant cannot pin its own value. Two
    weeks is the shortest turnaround a client decision realistically has;
    widening it would paint half the book urgent and retire the colour."""
    assert quotes_svc.URGENT_DAYS == 14
    assert quotes_svc.expiry_state(14) == quotes_svc.URGENT
    assert quotes_svc.expiry_state(15) == quotes_svc.LIVE
    assert quotes_svc.expiry_word(14) == "14d left"
    assert quotes_svc.expiry_word(15) == "15d"


def test_the_new_submission_form_asks_for_the_underwriter_too(
    conn: sqlite3.Connection,
) -> None:
    """M27: only the RESPONSE form's underwriter field was tested. You often
    know who picked the submission up before an answer comes back, and Today
    named "Travelers", which you cannot email."""
    _book(conn)
    spec = ef.submission_form(conn)
    fields = {f.key: f for f in spec.fields}
    assert "underwriter_contact_id" in fields
    # optional on purpose: a required field here pushes people to pick the
    # wrong name to get past it
    assert fields["underwriter_contact_id"].required is False


def test_the_underwriter_picker_leaves_out_retired_people(
    conn: sqlite3.Connection,
) -> None:
    """M31: `active = 1` in `at_market_orgs` could be dropped and nothing
    noticed. An underwriter who has left the carrier is exactly the name you
    must not be offered — the whole point of the field is that it is somebody
    you can email today."""
    _, market, _ = _book(conn)
    here = contacts.create(conn, market, first_name="Dana", last_name="Reeve")
    gone = contacts.create(conn, market, first_name="Sam", last_name="Ng")
    contacts.update(conn, gone.id, active=0)
    ids = [cid for _label, cid in ef.underwriter_options(conn)]
    assert ids == [here.id]


# --- fix round 1: the surfaces the undated quote now reaches ------------------


async def test_the_quotes_leaf_carries_the_undated_ones_as_a_tail(
    snapshot_db: Path,
) -> None:
    """`quotes expiring · 1 (+15 no expiry)`. Two numbers, never summed: the
    first counts clocks and the second counts quotes with no clock at all.
    Before this the undated ones were on no leaf anywhere — and the sample
    book alone holds fourteen of them, which is how big the hole was."""
    from bookkit.tui.app import BookkitApp

    conn = db.connect(snapshot_db)
    org = orgs.list_orgs(conn, kind="client")[0]
    market = orgs.list_orgs(conn, kind="market")[0]
    placement = placements.for_org(conn, org.id)[0]
    _quote(conn, market.id, placement.id, _iso(5))
    _quote(conn, market.id, placement.id, None)
    dated = len(quotes_svc.expiring(conn, TODAY, days=120))
    no_expiry = len(quotes_svc.undated(conn, TODAY))
    conn.close()
    assert no_expiry > 1, "the seeded book should already hold undated quotes"

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        tree = app.screen.query_one("#nav-tree")
        labels = [str(node.label) for node in tree.root.children[0].children]
        leaf = next(line for line in labels if "quotes expiring" in line)
        assert f"· {dated}" in leaf, leaf
        assert f"(+{no_expiry} no expiry)" in leaf, leaf


async def test_the_undated_quote_reads_no_expiry_never_a_zero_countdown(
    snapshot_db: Path,
) -> None:
    """The lie this whole feature exists to prevent: `days_remaining or 0`
    renders an undated quote as "0d" — expires today. Now that the leaf
    carries undated quotes that branch is live, not defensive, so the cell is
    a dash and the expiry column says "no expiry"."""
    from bookkit.tui.app import BookkitApp
    from bookkit.tui.widgets.tables import ListTable

    conn = db.connect(snapshot_db)
    org = orgs.list_orgs(conn, kind="client")[0]
    market = orgs.list_orgs(conn, kind="market")[0]
    placement = placements.for_org(conn, org.id)[0]
    _quote(conn, market.id, placement.id, None)
    conn.close()

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        nav = app.screen
        nav._current = ("att", "quotes")
        nav._render_pane()
        await pilot.pause()
        table = nav.query_one("#nav-table", ListTable)
        rows = [[str(cell) for cell in table.get_row(key)] for key in table.rows]
        undated_rows = [r for r in rows if any("no expiry" in c for c in r)]
        assert undated_rows, rows
        for row in undated_rows:
            assert not any(c.strip() == "0d" for c in row), row
            assert "—" in row, row


async def test_open_items_carries_the_quote_and_its_subjectivities(
    snapshot_db: Path,
) -> None:
    """M39: the Open-items quote rows could be deleted whole and the suite
    stayed green. Open items answers "everything this client still owes or is
    owed" — a quote whose terms lapse in three days is the most expensive
    thing that can be missing from it."""
    from bookkit.tui.app import BookkitApp
    from bookkit.tui.widgets.tables import ListTable

    conn = db.connect(snapshot_db)
    org = orgs.list_orgs(conn, kind="client")[0]
    market = orgs.list_orgs(conn, kind="market")[0]
    placement = placements.for_org(conn, org.id)[0]
    quote = _quote(conn, market.id, placement.id, _iso(3))
    submissions.add_subjectivity(conn, quote.id, "signed application", due_on=_iso(1))
    conn.close()

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("8")
        await pilot.pause()
        context = app.screen.query_one("#open-items-context", ListTable)
        keys = [str(k.value) for k in context.rows]
        assert f"quote:{quote.id}" in keys, keys
        assert any(k.startswith("subjectivity:") for k in keys), keys
        rendered = " ".join(
            str(cell) for row in context.rows for cell in context.get_row(row)
        )
        assert "3d left" in rendered, rendered


async def test_j_down_the_submissions_repoints_the_subjectivities(
    snapshot_db: Path,
) -> None:
    """M38: the master/detail repoint could be replaced with `pass` and the
    suite stayed green — the detail table would keep showing the FIRST
    submission's subjectivities under whatever row the cursor is on, which is
    a chase list attributed to the wrong market."""
    from bookkit.tui.app import BookkitApp
    from bookkit.tui.widgets.tables import ListTable

    conn = db.connect(snapshot_db)
    org = orgs.list_orgs(conn, kind="client")[0]
    markets = orgs.list_orgs(conn, kind="market")
    placement = placements.for_org(conn, org.id)[0]
    first = _quote(conn, markets[0].id, placement.id, _iso(4))
    second = _quote(conn, markets[1].id, placement.id, _iso(8))
    submissions.add_subjectivity(conn, first.id, "signed application")
    submissions.add_subjectivity(conn, second.id, "loss runs through 8/1")
    conn.close()

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("6")
        await pilot.pause()
        subs = app.screen.query_one("#pipeline-subs", ListTable)
        subs.focus()
        await pilot.pause()

        def detail() -> str:
            subjs = app.screen.query_one("#pipeline-subjs", ListTable)
            return " ".join(
                str(cell) for row in subjs.rows for cell in subjs.get_row(row)
            )

        seen = {detail()}
        for _ in range(len(subs.rows)):
            await pilot.press("j")
            await pilot.pause()
            seen.add(detail())
        assert any("signed application" in text for text in seen), seen
        assert any("loss runs through 8/1" in text for text in seen), seen


def test_the_web_tab_tells_an_empty_pipeline_how_a_quote_gets_recorded(
    tmp_path: Path, frozen_clock: date
) -> None:
    """The note sat INSIDE `{% if quote_rows %}`, so the one reader who most
    needs it — the person looking at an empty tab wondering where quotes come
    from — was the only one not told. The tab is writable as of gap 4
    (2026-08-20), so the note now points at the tab's own Response control
    rather than at the terminal app — but it still has to be there, outside
    the if, for the empty-tab reader."""
    from fastapi.testclient import TestClient

    from bookkit.web.app import create_app

    path = tmp_path / "web.db"
    conn = db.connect(path)
    client_id, _market, _placement = _book(conn)
    org = orgs.get(conn, client_id)
    conn.close()

    with TestClient(create_app(path), base_url="http://127.0.0.1") as client:
        body = client.get(f"/accounts/{org.ref}/pipeline").text
    assert "no quotes in hand" in body
    # the note names the tab's own control, and says what recording does
    assert "<em>Response</em>" in body
    assert "moves the row up here" in body


async def test_e_on_an_empty_pipeline_table_refuses_out_loud(
    snapshot_db: Path,
) -> None:
    """A REFUSAL SAYS SOMETHING (CLAUDE.md). `e` on the subjectivities table
    with nothing selected returned in silence — no modal, no message, no
    change, which reads as a broken app — while `a` on the same table said so
    correctly. The submissions table beside it had the same silence, so both
    are fixed: an account with no submissions is the commonest way to get
    here."""
    from bookkit.tui.app import BookkitApp
    from bookkit.tui.widgets.tables import ListTable

    conn = db.connect(snapshot_db)
    org = orgs.create(conn, name="Quiet Holdings", kind="client")
    conn.close()

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45), notifications=True) as pilot:
        await pilot.pause()
        app.open_account(org.id)
        await pilot.pause()
        await pilot.press("6")
        await pilot.pause()
        for table_id in ("pipeline-subjs", "pipeline-subs"):
            table = app.screen.query_one(f"#{table_id}", ListTable)
            assert table.row_count == 0
            table.focus()
            await pilot.pause()
            app.clear_notifications()
            await pilot.press("e")
            await pilot.pause()
            messages = [str(n.message) for n in app._notifications]
            assert messages, f"e on #{table_id} did nothing, silently"
            assert any(
                w in m.lower() for m in messages for w in ("press", "tab", "add")
            ), f"e on #{table_id} said {messages!r}, which is not a next step"
