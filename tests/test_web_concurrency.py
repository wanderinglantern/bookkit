"""Concurrent requests must not share a sqlite3.Connection.

The READ routes in web/routes/ are sync `def`, so FastAPI runs each in an
anyio worker thread and two browser requests are two threads. web/app.py used
to hand all of them one connection parked on app.state.conn; measured on this
app that returned wrong answers for ~21-28% of requests at 6 concurrent
workers — 404 "no such account" for accounts that exist, saves that vanished
behind a 404, and event_log rows recording `old_value = NULL` for fields that
had a value, which a revert then pastes back over live data. Diagnosis:
.superpowers/sdd/2026-08-17-web-account-page/flaky-batch-test-investigation.md

The eight WRITE routes are `async def` (they await request.form()), so they
run on the event loop and DO share one connection — the loop thread's. This
file cannot see that: TestClient drives the loop from a portal thread of its
own, so under test the async routes get a per-thread connection like every
other request, while under uvicorn they all get app.state.conn. What keeps
them correct is that asyncio does not preempt and nothing awaits inside a
transaction, which is asserted where it can be —
tests/test_conventions.py::test_no_await_inside_a_transaction.

Both tests below were run against the shared-connection code before being
committed; each fails there. Read their docstrings for which assertion is the
one that fires — a concurrency test that also passes on the broken code is
not a test.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit import db
from bookkit.repo import contacts as contacts_repo
from bookkit.repo import orgs
from bookkit.web.app import create_app

WORKERS = 6


@pytest.fixture
def app_and_refs(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app) as client:
        refs = [o.ref for o in orgs.list_orgs(app.state.conn, kind="client")]
        assert len(refs) >= 2, "the seed must supply several accounts to hammer"
        yield app, client, refs


def test_concurrent_requests_each_get_their_own_connection(
    app_and_refs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seam, asserted directly: hold WORKERS requests inside the handler at
    once and record the connection each was actually handed.

    A green suite proves nothing broke, not that the new path is taken — so
    this reads the connection out of production code (`orgs.find`, called by
    routes/account.py's `_org`, which every route funnels through) rather than
    poking ThreadConnections. On a shared connection every thread reports the
    same id and the second assertion fails 6 != 1, every run, deterministically:
    the barrier removes the timing dependence.
    """
    app, client, refs = app_and_refs
    barrier = threading.Barrier(WORKERS, timeout=20)
    seen: list[tuple[int, int]] = []
    lock = threading.Lock()
    original = orgs.find
    waited = 0

    def recording_find(conn, ref):  # type: ignore[no-untyped-def]
        nonlocal waited
        with lock:
            first = waited < WORKERS
            if first:
                waited += 1
        # Only the first WORKERS calls wait. Each of those blocks its request
        # inside the handler, so they are guaranteed to come from WORKERS
        # DISTINCT in-flight requests — a request cannot reach a second
        # orgs.find while it is parked here.
        if first:
            barrier.wait()
        with lock:
            seen.append((threading.get_ident(), id(conn)))
        return original(conn, ref)

    monkeypatch.setattr(orgs, "find", recording_find)

    def worker(n: int) -> None:
        client.get(f"/accounts/{refs[n % len(refs)]}/relationship")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    held = seen[:WORKERS]
    assert len({ident for ident, _ in held}) == WORKERS, (
        "the requests did not run on distinct threads, so this test proved "
        f"nothing about sharing: {held}"
    )
    assert len({conn_id for _, conn_id in held}) == WORKERS, (
        "concurrent requests shared a sqlite3.Connection — that is the defect "
        f"this app was fixed for: {held}"
    )
    # Scoped to the SYNC routes this test drives, and only those. It holds
    # for them everywhere: a worker thread is never the thread that called
    # create_app. It does NOT hold for the async write routes under uvicorn —
    # they run on the loop thread, which IS the creating thread there, so they
    # are handed exactly this object. Under TestClient the loop lives on a
    # portal thread, which is the only reason a broader claim would look true
    # here; see the module docstring.
    assert id(app.state.conn) not in {conn_id for _, conn_id in held}, (
        "a sync route was handed app.state.conn, the connection the creating "
        "thread (and every test) uses — for threadpool routes the two must "
        "never meet"
    )


def test_concurrent_reads_and_writes_all_come_back_right(app_and_refs) -> None:
    """The behaviour, hammered: 6 workers, mixed GETs and POST cell edits.

    The failing assertion on shared-connection code is `bad == []`: at this
    concurrency the shared connection returned ~21-28% wrong — pydantic
    ValidationErrors from all-NULL rows, sqlite3.InterfaceError, IndexError,
    and silent 404s for accounts that exist. 120 GETs makes a clean run there
    a ~1-in-10^15 event, so this is not a flaky test in the other direction.

    The event_log assertion is the data-corruption invariant from §3d of the
    investigation and is deliberately kept, but be honest about it: at HTTP
    request rates the NULL old_value is rare (measured 0 in 1186 events), so
    it is a guard, not the trigger. The trigger is `bad`.
    """
    app, client, refs = app_and_refs
    conn = app.state.conn
    org = orgs.find(conn, refs[0])
    assert org is not None
    person = contacts_repo.for_org(conn, org.id)[0]
    with db.transaction(conn):
        contacts_repo.update(conn, person.id, title="Head of Risk 0")
    floor = conn.execute("SELECT MAX(rowid) FROM event_log").fetchone()[0] or 0

    tabs = ("relationship", "program", "work")
    bad: list[str] = []
    lock = threading.Lock()

    def worker(n: int) -> None:
        local: list[str] = []
        for i in range(20):
            ref, tab = refs[(n + i) % len(refs)], tabs[i % len(tabs)]
            try:
                response = client.get(f"/accounts/{ref}/{tab}")
                if response.status_code != 200:
                    local.append(f"GET {ref}/{tab} -> {response.status_code}")
            except BaseException as exc:  # noqa: BLE001
                local.append(f"GET {ref}/{tab} raised {type(exc).__name__}: {exc}")
            if n < 2:
                url = f"/accounts/{org.ref}/contacts/{person.id}/cell/title"
                try:
                    saved = client.post(url, data={"title": f"Head of Risk {n}-{i}"})
                    if saved.status_code != 200:
                        local.append(f"POST title -> {saved.status_code}")
                except BaseException as exc:  # noqa: BLE001
                    local.append(f"POST title raised {type(exc).__name__}: {exc}")
        with lock:
            bad.extend(local)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert bad == [], f"{len(bad)} of 120 concurrent requests came back wrong: {bad[:5]}"

    poisoned = conn.execute(
        "SELECT rowid, old_value, new_value FROM event_log WHERE rowid > ?"
        " AND entity_type = 'contact' AND field = 'title' AND old_value IS NULL",
        (floor,),
    ).fetchall()
    assert not poisoned, (
        "event_log recorded old_value = NULL for a title that had a value; a "
        f"revert would write that NULL back over live data: {[dict(r) for r in poisoned]}"
    )


def test_a_slow_writer_delays_a_save_it_does_not_refuse_it(
    snapshot_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """db._tx_lock is load-bearing NOW THAT connections are per-thread, and
    this is the test that stops it being deleted as newly redundant.

    Per-thread connections moved writer contention from "cannot start a
    transaction within a transaction" on one connection to the SQLite FILE
    lock across several. Ordinary concurrent saves do not care — each
    transaction is microseconds and busy_timeout absorbs the overlap, and a
    run without the lock came back clean. A writer that holds the transaction
    LONGER than busy_timeout does care: a bulk import or a 250-entity batch.
    _tx_lock makes the web save QUEUE; without it BEGIN IMMEDIATE burns the
    timeout and raises "database is locked", the route turns that into an
    error cell, and the user's edit is gone. Mutating _tx_lock to a no-op
    fails this test with exactly that message.

    busy_timeout is shortened to 250ms so the hold can be 1s instead of 6.
    """
    monkeypatch.setattr(db, "BUSY_TIMEOUT_MS", 250)
    app = create_app(snapshot_db)
    with TestClient(app) as client:
        conn = app.state.conn
        org = orgs.list_orgs(conn, kind="client")[0]
        people = contacts_repo.for_org(conn, org.id)
        target, blocked_by = people[0], people[1]
        holding = threading.Event()

        def hog() -> None:
            """A second in-process writer on its OWN connection — an import,
            or any batch bigger than one field."""
            own = db.connect(snapshot_db, migrate=False)
            try:
                with db.transaction(own):
                    contacts_repo.update(own, blocked_by.id, title="Blocking Writer")
                    holding.set()
                    time.sleep(1.0)
            finally:
                own.close()

        thread = threading.Thread(target=hog)
        thread.start()
        try:
            assert holding.wait(timeout=10), "the blocking writer never started"
            time.sleep(0.05)
            response = client.post(
                f"/accounts/{org.ref}/contacts/{target.id}/cell/title",
                data={"title": "Head of Risk"},
            )
        finally:
            thread.join(timeout=10)

        assert "database is locked" not in response.text, (
            "a save was refused because another in-process writer held the "
            "write lock — db._tx_lock is what makes it queue instead"
        )
        assert contacts_repo.get(conn, target.id).title == "Head of Risk", (
            "the save did not persist: the user's edit was lost to writer "
            "contention the lock exists to absorb"
        )
