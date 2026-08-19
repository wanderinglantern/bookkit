"""MCP policy-record tools: the guarded file cycle exposed to the assistant,
with the snapshot-based revert story batch undo cannot provide."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_linking_flow import make_program, write_program
from towerkit.model import load_program

from bookkit import db, mcpserver, sync
from bookkit.repo import batches as batches_repo
from bookkit.repo import orgs, placements


@pytest.fixture
def linked(tmp_path: Path):
    path_db = tmp_path / "mcp.db"
    db.connect(path_db).close()
    conn = db.connect(path_db)
    client = orgs.create(conn, kind="client", name="Test Client, Inc.", status="active")
    path = write_program(
        tmp_path / "p" / "test.json",
        make_program("Test Client, Inc.", "2026-01-01", "2027-01-01", tbd_line=True),
    )
    assert sync.confirm_link(conn, path, client.id).ok
    placement = placements.by_program_path(conn, str(path))
    return conn, client, placement, path


def test_program_layers_reads_ids_for_the_writes(linked):
    conn, _, placement, _ = linked
    out = mcpserver._program_layers(conn, placement.ref)
    names = [layer["name"] for layer in out["layers"]]
    assert "Primary GL" in names
    assert out["lines"] and all("id" in ln for ln in out["lines"])
    assert out["layers"][0]["id"]


def test_program_layers_shows_who_is_on_each_layer(linked):
    """The tool's description says it returns participants; before this it
    returned none, so an assistant asked "who is on the 2nd excess" would
    answer from a contract the data did not honour. The description was the
    honest half — program_summary is the tool that is deliberately slim, and
    its docstring says so — so the DATA moved."""
    conn, _, placement, _ = linked
    mcpserver._program_layer_add(
        conn, placement.ref, "1st Excess", line_ids=["gl"],
        attach="2m", limit="10m", premium="300k",
    )
    mcpserver._program_bind(conn, placement.ref, "1st-excess", "Chubb", "60%")
    mcpserver._program_bind(conn, placement.ref, "1st-excess", "AXA XL", "40%")

    layers = {ly["id"]: ly for ly in mcpserver._program_layers(conn, placement.ref)["layers"]}
    assert [p["carrier"] for p in layers["primary-gl"]["participants"]] == ["Zurich"]
    assert layers["primary-cy"]["participants"] == []   # 'To be placed', not absent
    assert layers["1st-excess"]["participants"] == [
        {"carrier": "Chubb", "share_pct": 60.0, "premium_cents": 180_000_00},
        {"carrier": "AXA XL", "share_pct": 40.0, "premium_cents": 120_000_00},
    ]


def test_program_layer_add_lands_in_file_and_cache(linked):
    conn, _, placement, path = linked
    out = mcpserver._program_layer_add(
        conn, placement.ref, "Excess GL", line_ids=["gl"],
        attach="2m", limit="5m", premium="150k",
    )
    assert out["batch"].startswith("MCP-")
    program = load_program(path)
    excess = next(ly for ly in program.layers if ly.name == "Excess GL")
    assert excess.attach == 2_000_000 and excess.limit == 5_000_000
    row = conn.execute(
        "SELECT COUNT(*) FROM proj_layer WHERE placement_id = ? AND name = 'Excess GL'",
        (placement.id,),
    ).fetchone()
    assert row[0] == 1


def test_validation_failure_writes_nothing(linked):
    """Over-signing a layer: towerkit's validator refuses, the file is
    byte-identical, and no batch row survives."""
    conn, _, placement, path = linked
    before = path.read_bytes()
    n_batches = len(batches_repo.recent(conn, since="2000-01-01"))

    # Zurich already holds 100% of primary-gl — 10% more over-signs it,
    # which is a VALIDATOR error, not a parse error
    with pytest.raises(ValueError) as err:
        mcpserver._program_bind(conn, placement.ref, "primary-gl",
                                carrier="Chubb", share="10%")
    assert "over-signed" in str(err.value)
    assert path.read_bytes() == before
    assert len(batches_repo.recent(conn, since="2000-01-01")) == n_batches


def test_write_conflict_is_surfaced_not_overwritten(linked):
    """The file moved since projection (towerkit's TUI, say): refuse and say
    re-sync, never clobber."""
    conn, _, placement, path = linked
    program = load_program(path)
    program.program = "Edited Behind Our Back"
    from towerkit.model import dump_program

    dump_program(program, path)

    with pytest.raises(ValueError) as err:
        mcpserver._program_layer_add(conn, placement.ref, "Excess GL",
                                     line_ids=["gl"], attach="2m", limit="5m")
    assert "re-sync" in str(err.value) or "changed on disk" in str(err.value)
    assert load_program(path).program == "Edited Behind Our Back"


def test_every_program_write_leaves_a_snapshot(linked):
    conn, _, placement, path = linked
    out = mcpserver._program_layer_add(
        conn, placement.ref, "Excess GL", line_ids=["gl"], attach="2m", limit="5m",
    )
    snapdir = path.parent / ".mcp-snapshots"
    assert (snapdir / f"{out['batch']}.json").exists()
    assert (snapdir / f"{out['batch']}.meta.json").exists()


def test_program_revert_file_restores_the_pre_image(linked):
    conn, _, placement, path = linked
    before = path.read_bytes()
    out = mcpserver._program_layer_add(
        conn, placement.ref, "Excess GL", line_ids=["gl"], attach="2m", limit="5m",
    )
    assert path.read_bytes() != before

    got = mcpserver._program_revert_file(conn, out["batch"])
    assert got["reverted"] is True
    assert path.read_bytes() == before                 # byte-identical
    row = conn.execute(
        "SELECT COUNT(*) FROM proj_layer WHERE placement_id = ? AND name = 'Excess GL'",
        (placement.id,),
    ).fetchone()
    assert row[0] == 0                                  # cache re-projected
    assert batches_repo.get_by_ref(conn, out["batch"]).reverted_at is not None


def test_a_revert_never_opens_the_program_file_for_writing(
    linked, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The undo path must not be able to destroy the file it exists to protect.

    `shutil.copyfile` OPENS THE DESTINATION "wb" — truncating it — before the
    first byte lands, so a crash, a full disk or a dropped network mount
    between those two moments left the broker's program file empty or
    half-written, with the pre-image still in .mcp-snapshots and nothing to say
    which of the two was real. Injecting ENOSPC part-way through that copy left
    a 64-byte program file where a 1,367-byte one had been (2026-08-18).

    This asserts the property, not the call: whatever writes the pre-image
    back, the destination itself is never opened for writing. towerkit's
    `atomicio` writes a same-directory temp, fsyncs it and `os.replace`s it
    into position, and towerkit's JSON is the declared source of truth for
    program structure — its undo path has no business being the one program
    write that is not durable. The revert here SUCCEEDS; there is no failure to
    inject, because a write that cannot half-happen needs none."""
    import builtins

    conn, _, placement, path = linked
    before = path.read_bytes()
    out = mcpserver._program_layer_add(
        conn, placement.ref, "Excess GL", line_ids=["gl"], attach="2m", limit="5m",
    )
    assert path.read_bytes() != before

    opened_for_writing: list[str] = []
    real_open = builtins.open

    def watch_open(file, mode="r", *args, **kwargs):
        try:
            same = Path(file) == path
        except TypeError:                 # a file descriptor, not a path
            same = False
        if same and any(c in mode for c in "wa+"):
            opened_for_writing.append(mode)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", watch_open)
    mcpserver._program_revert_file(conn, out["batch"])
    monkeypatch.undo()

    assert not opened_for_writing, (
        f"the program file itself was opened {opened_for_writing} — every "
        f"failure between that open and the last byte destroys it"
    )
    assert path.read_bytes() == before    # and the revert still did its job


def test_a_revert_that_fails_at_the_last_moment_leaves_the_file_untouched(
    linked, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of atomic: the failure lands on the swap, the last thing
    that can go wrong, and the file is byte-identical to what the batch wrote —
    neither empty, nor half a pre-image, nor a mixture."""
    import errno

    conn, _, placement, path = linked
    out = mcpserver._program_layer_add(
        conn, placement.ref, "Excess GL", line_ids=["gl"], attach="2m", limit="5m",
    )
    after_write = path.read_bytes()

    def no_space(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr("os.replace", no_space)
    with pytest.raises(OSError):
        mcpserver._program_revert_file(conn, out["batch"])
    monkeypatch.undo()

    now = path.read_bytes()
    assert now == after_write, (
        f"a failed revert changed the program file: {len(now)} bytes, not the "
        f"{len(after_write)} the batch wrote"
    )
    load_program(path)                    # and it is still a loadable program


def test_program_revert_file_refuses_if_the_file_moved_since(linked):
    conn, _, placement, path = linked
    out = mcpserver._program_layer_add(
        conn, placement.ref, "Excess GL", line_ids=["gl"], attach="2m", limit="5m",
    )
    # a LATER write (any writer) — the pre-image is stale now
    mcpserver._program_layer_add(conn, placement.ref, "Second Excess",
                                 line_ids=["gl"], attach="7m", limit="3m")

    with pytest.raises(ValueError) as err:
        mcpserver._program_revert_file(conn, out["batch"])
    assert "since" in str(err.value) or "newer" in str(err.value)
    program = load_program(path)
    assert any(ly.name == "Excess GL" for ly in program.layers)  # untouched


def test_revert_batch_refuses_program_batches_with_a_pointer(linked):
    conn, _, placement, _ = linked
    out = mcpserver._program_layer_add(
        conn, placement.ref, "Excess GL", line_ids=["gl"], attach="2m", limit="5m",
    )
    with pytest.raises(ValueError) as err:
        mcpserver._revert_batch(conn, out["batch"], now="2026-08-14T05:00:00Z")
    assert "program_revert_file" in str(err.value)


def test_program_tools_are_registered(tmp_path):
    from bookkit.mcpserver import build_server

    path_db = tmp_path / "reg.db"
    db.connect(path_db).close()
    server = build_server(path_db)
    names = {t.name for t in server._tool_manager.list_tools()}
    assert {
        "program_layers", "program_layer_add", "program_bind",
        "program_layer_edit", "program_edit", "program_revert_file",
    } <= names
