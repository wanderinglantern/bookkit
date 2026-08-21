"""Removing a program that should not exist — the record, and its file moved.

Grant made two programs for one client by mistake (2026-08-21): two DIFFERENT
towerkit files, one of which should never have existed. Merge is the wrong
tool — it folds two records of the same thing together and refuses two
file-backed placements on purpose — so there was no way to say "this one was a
mistake" at all.

The load-bearing assertions here are the safety ones, not the happy path:

* the file is MOVED, never unlinked — towerkit JSON is the sole authority for
  program structure and the only genuinely unrecoverable thing this app can
  touch;
* the DATABASE commits first and the file moves after, because only that order
  fails safe;
* nothing live may be left pointing at a removed placement;
* and a file another placement still reads is a hard stop.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit import db, sync
from bookkit.repo import placements as placements_repo
from bookkit.services import batches as batches_svc
from bookkit.services import program_remove
from bookkit.web.app import create_app


@pytest.fixture
def app_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs

    conn = app.state.conn
    org = next(
        o
        for o in orgs.list_orgs(conn, kind="client")
        if [p for p in placements_repo.for_org(conn, o.id) if p.program_path]
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org


def _linked(conn, org):
    return next(p for p in placements_repo.for_org(conn, org.id) if p.program_path)


def _clear_dependants(conn, placement_id: str) -> None:
    """Detach everything the seed filed against this program, so the fixture
    tests the removal rather than the refusal. Each is a real repo write."""
    from bookkit.repo import documents, rfi, submissions, tasks

    for row in conn.execute(
        "SELECT id FROM submission WHERE placement_id = ? AND deleted_at IS NULL",
        (placement_id,),
    ).fetchall():
        submissions.delete(conn, row[0])
    for row in conn.execute(
        "SELECT id FROM task WHERE placement_id = ? AND deleted_at IS NULL",
        (placement_id,),
    ).fetchall():
        tasks.delete(conn, row[0])
    for row in conn.execute(
        "SELECT id FROM rfi_request WHERE placement_id = ? AND deleted_at IS NULL",
        (placement_id,),
    ).fetchall():
        rfi.delete_request(conn, row[0])
    for row in conn.execute(
        "SELECT id FROM document WHERE placement_id = ? AND deleted_at IS NULL",
        (placement_id,),
    ).fetchall():
        documents.delete(conn, row[0])
    for table in ("team_assignment", "project_need"):
        conn.execute(
            f"UPDATE {table} SET deleted_at = ? WHERE placement_id = ?",
            (db.utc_now(), placement_id),
        )


def _removable(conn, org):
    placement = _linked(conn, org)
    _clear_dependants(conn, placement.id)
    return placement


def _blocked(conn, org):
    """A linked placement with real work filed against it. Created rather than
    assumed: the seeded book does not reliably attach anything to the first
    linked program, and a refusal test that passes because the fixture happens
    to be empty asserts nothing."""
    from bookkit.repo import tasks

    placement = _removable(conn, org)
    tasks.create(
        conn, "chase the binder", org_id=org.id, placement_id=placement.id
    )
    return placement


def _deleted_at(conn, placement_id: str) -> str | None:
    """`repo.placements.get` filters soft-deleted rows out, which is right for
    every caller but this assertion — reading the tombstone means going round
    it."""
    row = conn.execute(
        "SELECT deleted_at FROM placement WHERE id = ?", (placement_id,)
    ).fetchone()
    return None if row is None else row[0]


def _open_batch(conn, **kw):
    return batches_svc.open_batch(conn, source="web", **kw)


# --- the file ------------------------------------------------------------------


class TestTheFileIsMovedNotDeleted:
    def test_the_file_leaves_its_place_and_still_exists(self, app_and_org) -> None:
        """THE WHOLE SAFETY OF THIS OPERATION. towerkit JSON is the sole
        authority for program structure; proj_* is a rebuildable cache and the
        file is not."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement = _removable(conn, org)
        source = sync.program_file(conn, placement)
        before = source.read_bytes()

        result = program_remove.remove(
            conn, placement, open_batch=_open_batch, now=db.utc_now()
        )

        assert not source.exists(), "the file did not move"
        moved = Path(result.file_to)
        assert moved.exists(), "the file was DELETED, not moved"
        assert moved.read_bytes() == before, "the file changed on the way"

    def test_it_lands_in_a_removed_directory_beside_the_original(
        self, app_and_org
    ) -> None:
        """Beside it, not in backups/ — a rollback somebody has to go looking
        for in another tree is one they will not find."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement = _removable(conn, org)
        source = sync.program_file(conn, placement)

        result = program_remove.remove(
            conn, placement, open_batch=_open_batch, now=db.utc_now()
        )

        moved = Path(result.file_to)
        assert moved.parent == source.parent / program_remove.REMOVED_DIRNAME
        assert moved.name.endswith(source.name)

    def test_two_removals_of_same_named_files_do_not_collide(self) -> None:
        """The stamp is what keeps a second removal from overwriting the first
        one's rescue copy."""
        a = program_remove.retired_path(
            Path("/p/atomic-2026.json"), now="2026-08-21T10:00:00Z"
        )
        b = program_remove.retired_path(
            Path("/p/atomic-2026.json"), now="2026-08-21T11:30:00Z"
        )
        assert a != b
        assert a.name.endswith("atomic-2026.json")

    def test_a_placement_with_no_file_removes_cleanly(self, app_and_org) -> None:
        client, org = app_and_org
        conn = client.app.state.conn
        unlinked = next(
            p for p in placements_repo.for_org(conn, org.id) if not p.program_path
        )
        _clear_dependants(conn, unlinked.id)

        result = program_remove.remove(
            conn, unlinked, open_batch=_open_batch, now=db.utc_now()
        )

        assert result.file_to is None
        assert _deleted_at(conn, unlinked.id) is not None


# --- the record ----------------------------------------------------------------


class TestTheRecord:
    def test_the_placement_is_soft_deleted_in_one_batch(self, app_and_org) -> None:
        client, org = app_and_org
        conn = client.app.state.conn
        placement = _removable(conn, org)

        program_remove.remove(
            conn, placement, open_batch=_open_batch, now=db.utc_now()
        )

        assert _deleted_at(conn, placement.id) is not None
        assert placement.id not in {
            p.id for p in placements_repo.for_org(conn, org.id)
        }

    def test_the_projection_cache_is_emptied(self, app_and_org) -> None:
        """proj_* rows mirror exactly one file. Left behind, the layers keep
        being counted in carrier exposure and market premiums for a program
        that is gone."""
        from bookkit.repo import projection

        client, org = app_and_org
        conn = client.app.state.conn
        placement = _removable(conn, org)
        assert projection.layers_for_placement(conn, placement.id)

        program_remove.remove(
            conn, placement, open_batch=_open_batch, now=db.utc_now()
        )

        assert not projection.layers_for_placement(conn, placement.id)

    def test_undo_brings_the_record_back(self, app_and_org) -> None:
        """Half the story, and the confirm says which half: the record returns,
        the file stays where this put it."""
        from bookkit.repo import batches as batches_repo

        client, org = app_and_org
        conn = client.app.state.conn
        placement = _removable(conn, org)
        result = program_remove.remove(
            conn, placement, open_batch=_open_batch, now=db.utc_now()
        )

        batch = batches_repo.recent(conn, "0000", limit=1)[0]
        assert batch.tool == "remove_program"
        batches_svc.revert(conn, batch.ref, now=db.utc_now())

        assert _deleted_at(conn, placement.id) is None
        assert Path(result.file_to).exists(), "the rescue copy went missing"


# --- the refusals ---------------------------------------------------------------


class TestItRefusesRatherThanStranding:
    def test_live_dependants_refuse_the_removal_by_name(self, app_and_org) -> None:
        """A soft-deleted placement whose submissions still reference it leaves
        readers holding a dead foreign key — the hazard services/merge.py exists
        to move records away from."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement = _blocked(conn, org)
        assert program_remove.blockers(conn, placement.id)

        with pytest.raises(program_remove.ProgramRemoveRefused) as refusal:
            program_remove.remove(
                conn, placement, open_batch=_open_batch, now=db.utc_now()
            )

        assert placement.ref in str(refusal.value)
        assert _deleted_at(conn, placement.id) is None
        assert sync.program_file(conn, placement).exists()

    def test_the_refusal_names_the_way_out(self, app_and_org) -> None:
        """A refusal names the fix (the data-entry rules) — here, Merge, which
        moves the records for you."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement = _blocked(conn, org)

        with pytest.raises(program_remove.ProgramRemoveRefused) as refusal:
            program_remove.remove(
                conn, placement, open_batch=_open_batch, now=db.utc_now()
            )

        assert "Merge" in str(refusal.value)

    def test_a_file_another_placement_reads_is_a_hard_stop(
        self, app_and_org
    ) -> None:
        """Moving it would break the OTHER placement silently: it would keep
        its program_path, keep reading as linked, and fail at the next write."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement = _removable(conn, org)
        sibling = next(
            p
            for p in placements_repo.for_org(conn, org.id)
            if p.id != placement.id
        )
        # KEYWORD, not a positional dict: repo.placements.update's third
        # positional is `note`, so a dict there is silently filed as an
        # event-log note and nothing is written.
        placements_repo.update(
            conn, sibling.id, program_path=placement.program_path
        )

        with pytest.raises(program_remove.ProgramRemoveRefused) as refusal:
            program_remove.remove(
                conn, placement, open_batch=_open_batch, now=db.utc_now()
            )

        assert sibling.ref in str(refusal.value)
        assert sync.program_file(conn, placement).exists()
        assert _deleted_at(conn, placement.id) is None


# --- the browser ----------------------------------------------------------------


class TestTheConfirmStep:
    def _url(self, org, placement):
        return f"/accounts/{org.ref}/program/{placement.id}/remove"

    def test_the_confirm_writes_nothing(self, app_and_org) -> None:
        client, org = app_and_org
        conn = client.app.state.conn
        placement = _removable(conn, org)

        client.get(self._url(org, placement))

        assert _deleted_at(conn, placement.id) is None
        assert sync.program_file(conn, placement).exists()

    def test_it_shows_where_the_file_will_go_before_the_click(
        self, app_and_org
    ) -> None:
        """A promise about a path is worth nothing unless the path is shown —
        the same rule the scaffold confirm follows for a file it creates."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement = _removable(conn, org)
        source = sync.program_file(conn, placement)

        page = client.get(self._url(org, placement)).text

        assert str(source) in page
        assert program_remove.REMOVED_DIRNAME in page
        assert "moves to" in page

    def test_the_destination_path_is_allowed_to_wrap(self, app_and_org) -> None:
        """A path that runs off the right edge mid-way is the same failure as
        not printing it: the reader has to discover the overflow by dragging.
        Real program roots under a real home directory are long enough to hit
        this, and the destination is the entire point of the step."""
        client, org = app_and_org
        css = client.get("/static/app.css").text
        rule = css[css.index(".confirm-remove-paths dd") :][:200]

        assert "overflow-wrap: anywhere" in rule
        assert "white-space: nowrap" not in rule

    def test_the_confirm_says_which_half_of_undo_is_automatic(
        self, app_and_org
    ) -> None:
        client, org = app_and_org
        conn = client.app.state.conn
        placement = _removable(conn, org)

        page = client.get(self._url(org, placement)).text

        assert "undo brings the RECORD back" in page

    def test_the_confirm_offers_no_remove_button_when_it_would_be_refused(
        self, app_and_org
    ) -> None:
        """Said before the click, not after it: a button that always comes back
        as an error is worse than no button."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement = _blocked(conn, org)

        page = client.get(self._url(org, placement)).text

        assert "Remove program" not in page
        assert "Merge" in page

    def test_the_post_removes_and_answers_with_the_tab(self, app_and_org) -> None:
        client, org = app_and_org
        conn = client.app.state.conn
        placement = _removable(conn, org)

        done = client.post(self._url(org, placement))

        assert done.status_code == 200
        assert done.text.lstrip().startswith("<div id=\"programs-panel\"")
        assert _deleted_at(conn, placement.id) is not None

    def test_a_refused_post_comes_back_in_the_page(self, app_and_org) -> None:
        """Not a status code: an error response produces no swap and no message
        at all under htmx."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement = _blocked(conn, org)

        refused = client.post(self._url(org, placement))

        assert refused.status_code == 200
        assert "still has" in refused.text
        assert _deleted_at(conn, placement.id) is None

    def test_another_accounts_program_is_a_404(self, app_and_org) -> None:
        from bookkit.repo import orgs

        client, org = app_and_org
        conn = client.app.state.conn
        placement = _removable(conn, org)
        other = next(
            o for o in orgs.list_orgs(conn, kind="client") if o.id != org.id
        )

        got = client.post(f"/accounts/{other.ref}/program/{placement.id}/remove")

        assert got.status_code == 404
        assert _deleted_at(conn, placement.id) is None

    def test_the_section_renders_the_control_that_opens_this(
        self, app_and_org
    ) -> None:
        """D4: never draw an inert control, and never leave a built one
        unreachable — the other half of the same rule."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement = _linked(conn, org)

        page = client.get(f"/accounts/{org.ref}/program").text

        assert f'hx-get="{self._url(org, placement)}"' in page
