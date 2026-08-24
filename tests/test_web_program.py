"""The Program tab: placements, their layers, and the markets on them.

Writes arrive in phase 2 (docs/superpowers/plans/2026-08-19-programs-on-the-web.md).
What this file asserts first is that the panel stops contradicting the badge
above it: the stub printed the addable-list empty state unconditionally while
the tab counted the placements it claimed did not exist, and an account with
two programs read as an account with none.

The `snapshot_db` fixture seeds a book AND projects real towerkit files, so
the layers asserted here are read off disk rather than invented.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit import sync
from bookkit.money import format_cents
from bookkit.web.app import create_app


@pytest.fixture
def app_and_org(snapshot_db: Path):
    """An account that has at least one placement. base_url is loopback
    because web/origin.py refuses TestClient's default Host of "testserver" —
    exactly the forged name the guard exists to catch."""
    app = create_app(snapshot_db)
    from bookkit.repo import orgs, placements

    conn = app.state.conn
    org = next(
        o for o in orgs.list_orgs(conn, kind="client") if placements.for_org(conn, o.id)
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org


def _linked(conn, org):
    """This account's placements that actually have a program file."""
    from bookkit.repo import placements

    return [p for p in placements.for_org(conn, org.id) if p.program_path]


def _file_of(conn, placement) -> Path:
    """The placement's towerkit file on disk.

    NOT `Path(placement.program_path)`: the stored value is relative to a
    program root wherever one contains the file (bookkit.programpath), which
    is the whole point — Grant's paths broke on 2026-08-20 because they were
    absolute. A test that reaches for the raw column is asserting against the
    old storage rule and will pass or fail depending on whether its fixture
    happens to configure roots."""
    from bookkit import sync as _sync

    return _sync.program_file(conn, placement)


def _top_level_tags(html: str) -> list[str]:
    """The element names at the TOP level of a response body.

    htmx picks its HTML parse context from the response's FIRST tag
    (`makeFragment`), so a response opening with `<td>` is parsed inside
    `<table><tbody><tr>…</tr></tbody></table>`. Anything in that response that
    is not table content — a `<section>`, say — is then FOSTER-PARENTED out of
    the fragment by the HTML tree builder and never reaches htmx at all. That
    is not a theory: on 2026-08-20 saving a layer premium in Chrome left
    `section.program` standing with its table emptied and all 14 rows gone,
    while the write itself succeeded.

    One top-level element per response is the invariant that makes the parse
    context irrelevant, so that is what the tests below assert.
    """
    from html.parser import HTMLParser

    VOID = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    class Scanner(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.depth = 0
            self.tops: list[str] = []

        def handle_starttag(self, tag: str, attrs: object) -> None:
            if tag in VOID:
                if self.depth == 0:
                    self.tops.append(tag)
                return
            if self.depth == 0:
                self.tops.append(tag)
            self.depth += 1

        def handle_endtag(self, tag: str) -> None:
            if tag not in VOID:
                self.depth = max(0, self.depth - 1)

    scanner = Scanner()
    scanner.feed(html)
    return scanner.tops


def _assert_panel_swap(response, placement_id: str) -> None:
    """A program write answers with the WHOLE panel, as ONE element, and says
    so in the swap headers rather than by riding a second element out of band
    behind a `<td>`. See `_top_level_tags` for what the old shape did."""
    tags = _top_level_tags(response.text)
    assert tags == ["section"], f"expected one <section>, got {tags}"
    assert response.headers.get("HX-Retarget") == f"#program-{placement_id}"
    assert response.headers.get("HX-Reswap") == "outerHTML"
    assert 'hx-swap-oob' not in response.text


# --- the tab tells the truth --------------------------------------------------


def test_the_program_tab_lists_the_placements(app_and_org):
    client, org = app_and_org
    from bookkit.repo import placements

    page = client.get(f"/accounts/{org.ref}/program")

    assert page.status_code == 200
    for placement in placements.for_org(client.app.state.conn, org.id):
        assert placement.program_name in page.text
        assert placement.ref in page.text


def test_the_panel_never_contradicts_the_tab_badge(app_and_org):
    """The one assertion this whole task exists for."""
    client, org = app_and_org

    page = client.get(f"/accounts/{org.ref}/program").text

    assert "empty — add the first row" not in page


def test_an_account_with_no_placements_says_so_honestly(snapshot_db: Path):
    from bookkit.repo import orgs

    app = create_app(snapshot_db)
    bare = orgs.create(app.state.conn, kind="client", name="No Programs Co")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        page = client.get(f"/accounts/{bare.ref}/program")

    assert page.status_code == 200
    assert "no programs on this account" in page.text


# --- the layers, which are the surface phase 2 edits ---------------------------


def test_every_layer_of_a_linked_program_is_listed(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    linked = _linked(conn, org)
    assert linked, "the seeded book has no placement with a program file"

    page = client.get(f"/accounts/{org.ref}/program").text

    for placement in linked:
        for layer in sync.layer_details(conn, placement.id):
            assert layer["name"] in page, f"{placement.ref} is missing layer {layer['name']}"


def test_the_markets_on_a_layer_are_named(app_and_org):
    """A tower without its carriers is a picture of capacity nobody is on.
    Phase 2 edits these; phase 1 has to show them."""
    client, org = app_and_org
    conn = client.app.state.conn

    page = client.get(f"/accounts/{org.ref}/program").text

    seen = 0
    for placement in _linked(conn, org):
        for layer in sync.layer_details(conn, placement.id):
            for participant in layer["participants"]:
                assert participant["carrier"] in page
                seen += 1
    assert seen, "no participants anywhere in the seeded book — test proves nothing"


def test_an_unplaced_layer_says_to_be_placed(app_and_org):
    """towerkit's own word for it. An empty participant list and a missing one
    are different facts: "nobody is on this layer" is the fact a reader most
    needs, and a blank cell asserts nothing."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]

    # ABOVE the existing tower. towerkit refuses an overlapping layer, which
    # is the validator doing its job — the first draft of this test attached
    # at $10M, inside the existing Umbrella, and was refused with
    # "OVERLAP Umbrella→Excess Test Layer at $27,000,000 vs $10,000,000".
    top = max(
        layer["attach_cents"] + layer["limit_cents"]
        for layer in sync.layer_details(conn, placement.id)
    )
    added = sync.add_layer(
        conn, placement.id, "Excess Test Layer", [], top, 5_000_000_00
    )
    assert added.ok, [d.message for d in added.errors]

    page = client.get(f"/accounts/{org.ref}/program").text

    assert "To be placed" in page


def test_a_file_that_will_not_load_says_so_instead_of_claiming_to_be_empty(
    app_and_org, tmp_path
):
    """THE BUG THIS WHOLE BRANCH STARTED FROM (Grant, 2026-08-20).

    Five of his placements pointed at a towerkit tree he had moved. Every read
    swallowed the FileNotFoundError and returned [], and the panel printed
    "the linked file has no layers yet" — so the web asserted the programs
    were empty while the same files opened fine in the TUI. The panel now
    prints the reason, and the reason names the file.
    """
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    from bookkit.repo import placements as placements_repo

    placements_repo.update(conn, placement.id, program_path=str(tmp_path / "vanished.json"))

    page = client.get(f"/accounts/{org.ref}/program").text

    assert "has no layers yet" not in page, "an unreadable file was called empty"
    assert "will not open" in page
    assert "vanished.json" in page, "the message does not name the file"


def test_an_unreadable_file_still_shows_what_the_last_sync_recorded(
    app_and_org, tmp_path
):
    """The layers were in proj_layer the whole time — Grant's five broken
    placements held 12, 1, 8, 14 and 10 rows between them while the panel
    showed nothing. Read-only and dated, because a stale figure a broker can
    quote from is worse than a blank."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    expected = len(sync.layer_details(conn, placement.id))
    assert expected, "fixture placement has no layers to strand"
    from bookkit.repo import placements as placements_repo

    placements_repo.update(conn, placement.id, program_path=str(tmp_path / "vanished.json"))

    page = client.get(f"/accounts/{org.ref}/program").text

    assert "Last synced" in page
    assert "is-stale" in page
    # read-only: no editors over data that cannot be written back
    stale = page[page.index("is-stale"):]
    assert "data-cell-action" not in stale[: stale.index("</table>")]


def test_a_moved_file_is_read_and_says_it_moved(app_and_org, tmp_path):
    """Recovery is not silence. The read succeeds from a new location; a
    program answering from a path the book does not record is how one file
    quietly ends up serving two placements."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    real = sync.program_file(conn, placement)
    moved_root = tmp_path / "moved"
    moved_root.mkdir()
    (moved_root / real.name).write_bytes(real.read_bytes())
    from bookkit.repo import placements as placements_repo
    from bookkit.repo import settings as settings_repo

    settings_repo.set_program_roots(conn, [str(moved_root)])

    placements_repo.update(
        conn, placement.id, program_path=str(tmp_path / "old" / real.name)
    )

    page = client.get(f"/accounts/{org.ref}/program").text

    assert "different location than the book records" in page
    assert "has no layers yet" not in page
    assert "bookctl relink" in page


def test_the_program_tab_opens_each_file_once(app_and_org):
    """Layers, lines, terms and the tower all want the SAME parsed file. Read
    per consumer, a nine-chip terms strip alone re-parsed it nine times."""
    from bookkit.web.routes import account as account_routes

    client, org = app_and_org
    real = account_routes.sync.linked_program
    calls: list[str] = []

    def counting(conn, placement_id):
        calls.append(placement_id)
        return real(conn, placement_id)

    account_routes.sync.linked_program = counting
    try:
        assert client.get(f"/accounts/{org.ref}/program").status_code == 200
    finally:
        account_routes.sync.linked_program = real

    assert calls, "the program tab read no program file at all"
    assert len(calls) == len(set(calls)), f"a file was parsed more than once: {calls}"


def test_a_placement_with_no_file_says_so_rather_than_looking_empty(app_and_org):
    """Three different facts — no file linked, a file with no layers, layers
    present — and collapsing the first two is how the stub came to lie."""
    client, org = app_and_org
    from bookkit.repo import placements

    conn = client.app.state.conn
    placements.create(
        conn, org.id, "Unlinked Program", "2026-03-01", "2027-03-01",
        status="prospective",
    )

    page = client.get(f"/accounts/{org.ref}/program").text

    assert "This placement has no program file." in page
    assert "Create a program file" in page


# --- money reads as money -----------------------------------------------------


def test_layer_money_is_formatted_not_raw_cents(app_and_org):
    """A layer with a $5M limit must not render as 500000000.

    The first version of this asserted `str(cents) not in page or cents == 0`
    against the FIRST layer — which attaches at 0 in the seeded book, so the
    right-hand side was always true and the test passed with the formatter
    replaced by str(). Review caught it by mutation. It now checks a non-zero
    figure, in a named cell, and asserts the formatted form is present as well
    as the raw one absent.

    DISPLAY IS COMPACT AS OF D5 (2026-08-19): the cell shows "$5M", matching
    the tower drawing above it, and the editor pre-fills the exact figure —
    see test_money_editor_prefill_stays_exact for the other half."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    priced = next(
        layer for layer in sync.layer_details(conn, placement.id)
        if layer["limit_cents"]
    )

    cell = _cell_text(
        _worksheet_page(client, org, placement, priced["id"]),
        placement, priced["id"], "limit_cents",
    )

    assert cell != str(priced["limit_cents"]), "raw cents rendered"
    from bookkit.money import format_cents_compact

    assert cell == format_cents_compact(priced["limit_cents"])


def test_money_editor_prefill_stays_exact(app_and_org):
    """The half of D5 that keeps the old invariant alive: the editor's
    pre-fill is the exact figure, because a compact string ("$50M") parses
    back lossily and an unedited save would destroy the odd dollars."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    priced = next(
        layer for layer in sync.layer_details(conn, placement.id)
        if layer["limit_cents"]
    )

    editor = client.get(_cell(org, placement, priced, "limit_cents") + "/edit").text

    exact = format_cents(priced["limit_cents"]).lstrip("$")
    assert f'value="{exact}"' in editor


def test_a_primary_layer_attaching_at_zero_says_zero(app_and_org):
    """An em dash means UNRECORDED. A primary layer attaches at $0 and that is
    a fact about the tower — rendering it as a dash tells the reader the
    attachment is unknown when it is known and is zero.

    The worksheet redesign (2026-08-24) replaced the attachment FIELD with
    the position sentence, so the fact moves there: a ground layer says it
    sits on the ground, with the $0 stated — never an em dash, never a
    figure invented for display."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    primaries = [
        layer for layer in sync.layer_details(conn, placement.id)
        if layer["attach_cents"] == 0 and not layer["statutory"]
    ]
    assert primaries, "the seeded book has no ground-up layer — test proves nothing"

    page = _worksheet_page(client, org, placement, primaries[0]["id"])

    assert "the ground ($0)" in page, "a $0 attachment is not stated as the ground"


# --- phase 2: the write seam --------------------------------------------------


def test_the_write_wrapper_refuses_with_the_diagnostics_intact():
    """A flat string is enough for an MCP client and not enough for the web.

    The route has to tell an ordinary validation refusal — re-render the cell
    with the message, keep the typed value — from a CONFLICT, which offers
    Reload / Overwrite / Keep editing. Only the diagnostics carry the code
    that distinguishes them, and `sync._mutate` is what puts it there."""
    from towerkit.validate import Diagnostics

    from bookkit.services.program_files import ProgramWriteRefused, raise_on_errors

    diags = Diagnostics()
    diags.error("conflict", "the file moved under this write")

    with pytest.raises(ProgramWriteRefused) as refused:
        raise_on_errors(diags)

    assert refused.value.diags is diags
    assert any(d.code == "conflict" for d in refused.value.diags.errors)
    assert "moved under" in str(refused.value)


def test_a_clean_write_returns_its_warnings_rather_than_raising():
    """Warnings ride along: an unplaced layer is a warning, not a refusal, and
    a write that refused on one would make a half-built tower unsaveable."""
    from towerkit.validate import Diagnostics

    from bookkit.services.program_files import raise_on_errors

    diags = Diagnostics()
    diags.warn("layer-unplaced", "Excess: 0% placed")

    assert raise_on_errors(diags) == ["Excess: 0% placed"]


def test_a_refusal_is_still_a_value_error():
    """The ordinary case has to keep working with no new code: ValueError is
    what open_batch already rolls back on, and what every web form already
    renders."""
    from bookkit.services.program_files import ProgramWriteRefused

    assert issubclass(ProgramWriteRefused, ValueError)


# --- phase 2: editing a layer where it is read --------------------------------


def _latest_batch(conn):
    from bookkit.repo import batches as batches_repo

    found = batches_repo.recent(conn, since="", limit=1)
    return found[0] if found else None


def _first_layer(conn, org):
    placement = _linked(conn, org)[0]
    return placement, sync.layer_details(conn, placement.id)[0]


def _cell_text(page: str, placement, layer_id: str, key: str) -> str:
    """The rendered text of one layer cell, found by its own action URL rather
    than by slicing near a name — a fixed-width slice missed the value and
    failed for the wrong reason once already."""
    import re

    action = f"/accounts/[^/]+/program/{placement.id}/layers/{layer_id}/cell/{key}"
    match = re.search(
        rf'data-cell-action="{action}".*?>(.*?)</(?:td|span)>', page, re.S
    )
    assert match, f"no {key} cell rendered for layer {layer_id}"
    return re.sub(r"<[^>]+>", "", match.group(1)).strip()


def _cell(org, placement, layer, key):
    return f"/accounts/{org.ref}/program/{placement.id}/layers/{layer['id']}/cell/{key}"


def test_a_layer_cell_offers_an_editor(app_and_org):
    client, org = app_and_org
    placement, layer = _first_layer(client.app.state.conn, org)

    editor = client.get(_cell(org, placement, layer, "name") + "/edit")

    assert editor.status_code == 200
    assert layer["name"] in editor.text
    assert "<input" in editor.text


def test_editing_a_layer_writes_the_file_and_leaves_a_revertible_batch(app_and_org):
    """THE SEAM, not the outcome. A response that merely looks right passes
    even when the route wrote outside a batch and left no pre-image — which is
    exactly what makes a program write unrevertible. So: the batch exists, it
    is this surface's, it carries the MCP server's own tool name, the snapshot
    is on disk, and the bytes changed."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    path = Path(placement.program_path)
    before = path.read_bytes()

    saved = client.post(_cell(org, placement, layer, "name"), data={"name": "Primary Casualty"})

    assert saved.status_code == 200
    assert path.read_bytes() != before, "the file on disk did not change"
    batch = _latest_batch(conn)
    assert batch is not None
    assert batch.source == "web"
    assert batch.tool == "program_layer_edit"
    snapshot = path.parent / ".mcp-snapshots" / f"{batch.ref}.json"
    assert snapshot.exists(), "no pre-image captured — this write cannot be reverted"
    assert snapshot.read_bytes() == before


def test_the_saved_value_comes_back_in_the_cell(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    saved = client.post(_cell(org, placement, layer, "name"), data={"name": "Renamed Layer"})

    assert "Renamed Layer" in saved.text
    assert sync.layer_details(conn, placement.id)[0]["name"] == "Renamed Layer"


def test_a_sub_dollar_premium_is_refused_not_rounded(app_and_org):
    """towerkit files carry whole dollars. Rounding here silently changes a
    client's premium; refusing says so, and keeps what was typed."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    path = Path(placement.program_path)
    before = path.read_bytes()

    refused = client.post(
        _cell(org, placement, layer, "premium_cents"), data={"premium_cents": "1,234.56"}
    )

    assert refused.status_code == 200
    assert "1,234.56" in refused.text, "the typed value was not kept for correction"
    assert path.read_bytes() == before, "the file was written anyway"


def test_a_refused_layer_edit_leaves_no_batch_and_no_snapshot(app_and_org):
    """A refusal that logged an undo unit would offer to revert a write that
    never happened — and a refusal that left a snapshot would offer to restore
    a pre-image of nothing.

    The name promised the snapshot half and the body never checked it: moving
    `capture` above `raise_on_errors` in services/program_files.py left this
    green while every refused edit wrote a .mcp-snapshots file. Review found
    it by exactly that mutation."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    snapdir = Path(placement.program_path).parent / ".mcp-snapshots"
    before_batch = _latest_batch(conn)
    before_snaps = set(snapdir.glob("*.json")) if snapdir.exists() else set()

    client.post(
        _cell(org, placement, layer, "premium_cents"), data={"premium_cents": "1,234.56"}
    )

    after = _latest_batch(conn)
    assert (after.ref if after else None) == (before_batch.ref if before_batch else None)
    after_snaps = set(snapdir.glob("*.json")) if snapdir.exists() else set()
    assert after_snaps == before_snaps, "a refused write left snapshot debris"


def test_a_layer_under_another_account_is_not_reachable(app_and_org):
    """Both ids are checked: the placement is this account's AND the layer is
    that placement's. Without the second, a layer could be edited under a
    placement it does not belong to."""
    client, org = app_and_org
    from bookkit.repo import orgs, placements

    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    stranger = orgs.create(conn, kind="client", name="Someone Else Ltd")
    # POINTED AT THE SAME FILE, so the layer id really does exist under their
    # placement too. Without that the route 404s because the layer is missing
    # rather than because it is not ours, and the test passes with the
    # ownership check deleted — which it did, until a mutation showed it
    # (2026-08-19). A guard test that cannot fail is not a guard.
    theirs = placements.create(
        conn, stranger.id, "Their Program", "2026-01-01", "2027-01-01",
        program_path=placement.program_path,
    )

    poked = client.post(
        f"/accounts/{org.ref}/program/{theirs.id}/layers/{layer['id']}/cell/name",
        data={"name": "not mine"},
    )

    assert poked.status_code == 404
    # and nothing was written to the file they share
    assert sync.layer_details(conn, placement.id)[0]["name"] != "not mine"


def test_a_derived_column_offers_no_editor(app_and_org):
    """signed_pct is the sum of the participants' shares. A cell that offered
    to edit it would write nothing and read as broken."""
    client, org = app_and_org
    placement, layer = _first_layer(client.app.state.conn, org)

    assert client.get(_cell(org, placement, layer, "signed_pct") + "/edit").status_code == 404


# --- phase 2: adding a layer, and working the markets on one ------------------


def _markets(org, placement, layer_id):
    return f"/accounts/{org.ref}/program/{placement.id}/layers/{layer_id}/markets"


def test_adding_a_layer_appends_it_pending(app_and_org):
    """A new layer is created unplaced — markets join as they bind. towerkit's
    word for that state is 'To be placed'."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)
    line = _first_line(conn, placement)

    added = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={
            "name": "3rd Excess",
            "line": line,
            "limit_cents": "5,000,000",
            "premium_cents": "",
        },
    )

    assert added.status_code == 200
    names = [layer["name"] for layer in sync.layer_details(conn, placement.id)]
    assert "3rd Excess" in names
    fresh = next(
        layer for layer in sync.layer_details(conn, placement.id)
        if layer["name"] == "3rd Excess"
    )
    assert fresh["participants"] == []
    batch = _latest_batch(conn)
    assert batch.source == "web" and batch.tool == "program_layer_add"


# test_a_layer_that_would_leave_a_gap_saves_and_says_so RETIRED (whole-branch
# review finding 2, 2026-08-21): it forced a gap by typing a far-above attach
# on the "Add layer" form, the exact mechanism finding 2 removed from that
# form (`sync.add_layer` no longer takes an attach a caller can aim anywhere
# it likes — `_layer_add_fields` has no attach field, and the route always
# calls `attach_cents=None`, which leaves towerkit's own contiguous
# suggested-attach standing). Typing an attach into `layer_add` can no longer
# create a gap at all. The property this test protected — line-gap is a
# WARNING, the write SUCCEEDS, and the message is stated in the panel, not a
# refusal — is still covered, through the mechanism that can actually still
# produce a gap: removing a mid-stack layer.  See
# test_a_layer_removal_that_leaves_a_gap_saves_in_place, below.


def test_binding_a_market_onto_a_layer(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)
    # A layer of our own with nothing on it, so the share is ours to give. The
    # first seeded layer may be fully signed, and skipping on that would make
    # this test protect nothing on the day it mattered.
    top = max(
        ly["attach_cents"] + ly["limit_cents"]
        for ly in sync.layer_details(conn, placement.id)
    )
    assert sync.add_layer(
        conn, placement.id, "Bind Target", [], top, 5_000_000_00
    ).ok
    layer = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["name"] == "Bind Target"
    )

    bound = client.post(
        _markets(org, placement, layer["id"]),
        data={"carrier": "Berkshire", "share_pct": "40"},
    )

    assert bound.status_code == 200
    fresh = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["id"] == layer["id"]
    )
    assert "Berkshire" in [p["carrier"] for p in fresh["participants"]]
    assert _latest_batch(conn).tool == "program_bind"


def test_over_signing_a_layer_is_refused_and_the_file_is_untouched(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    path = Path(placement.program_path)
    before = path.read_bytes()

    refused = client.post(
        _markets(org, placement, layer["id"]),
        data={"carrier": "Overshare Re", "share_pct": "100"},
    )

    assert refused.status_code == 200
    assert path.read_bytes() == before
    assert "Overshare Re" not in path.read_text()


def test_a_markets_share_is_corrected_in_place(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    assert layer["participants"], "the first layer has no market to correct"
    was = layer["participants"][0]["share_pct"]

    saved = client.post(
        f"{_markets(org, placement, layer['id'])}/0/cell/share_pct",
        data={"share_pct": str(was / 2)},
    )

    assert saved.status_code == 200
    fresh = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["id"] == layer["id"]
    )
    assert fresh["participants"][0]["share_pct"] == was / 2
    # a share moves the layer's signed % and can re-seat other rows, so the
    # answer is the whole panel, retargeted onto itself
    _assert_panel_swap(saved, placement.id)


def test_a_market_is_taken_off_a_layer_and_the_layer_survives(app_and_org):
    """Losing a layer because its last market fell away would destroy the
    tower's shape."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    assert layer["participants"], "the first layer has no market to remove"
    carrier = layer["participants"][0]["carrier"]

    removed = client.post(f"{_markets(org, placement, layer['id'])}/0/remove")

    assert removed.status_code == 200
    fresh = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["id"] == layer["id"]
    )
    assert carrier not in [p["carrier"] for p in fresh["participants"]]
    assert fresh["name"] == layer["name"], "the layer went with the market"


def _first_seat(conn, org):
    """A (placement, layer, index, seat) with at least one market bound."""
    for placement in _linked(conn, org):
        for layer in sync.layer_details(conn, placement.id):
            if layer["participants"]:
                return placement, layer, 0, layer["participants"][0]
    raise AssertionError("the seeded book has no bound market anywhere")


def _market_cell(org, placement, layer_id, index, key):
    return (
        f"/accounts/{org.ref}/program/{placement.id}"
        f"/layers/{layer_id}/markets/{index}/cell/{key}"
    )


def test_share_editor_prefills_the_actual_percent(app_and_org):
    """40% must pre-fill '40', not '0.4'. The old mini-form fed the seat's
    PERCENT into initial_text, whose share kind formats BPS — so a 40% seat
    pre-filled 0.4 and an unedited save would have cut the share 100x. The
    same silent-destruction class as the cents rule in CLAUDE.md."""
    client, org = app_and_org
    placement, layer, index, seat = _first_seat(client.app.state.conn, org)

    editor = client.get(
        _market_cell(org, placement, layer["id"], index, "share_pct") + "/edit"
    ).text

    assert f'value="{seat["share_pct"]:g}"' in editor


def test_carrier_editor_offers_existing_market_names(app_and_org):
    """Vocabulary completes from existing records (CLAUDE.md): freehand
    carrier spelling is how 'Zurich Insurance Group' vs 'Zurich' drift
    starts, and the TUI already wires Field.suggestions for this."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index, seat = _first_seat(conn, org)
    from bookkit.repo import vocab

    editor = client.get(
        _market_cell(org, placement, layer["id"], index, "carrier") + "/edit"
    ).text

    names = vocab.market_names(conn)
    assert names, "the seeded book names no markets — test proves nothing"
    assert "<datalist" in editor
    assert names[0] in editor


def test_market_remove_asks_first_and_the_get_writes_nothing(app_and_org):
    """The remove control fetches an IN-PLACE confirm; only the confirm's
    POST writes. Contacts and interactions already confirm — a market seat
    is the same severity of removal."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index, seat = _first_seat(conn, org)
    path = Path(placement.program_path)
    before = path.read_bytes()

    page = _worksheet_page(client, org, placement, layer["id"])
    base = _market_cell(org, placement, layer["id"], index, "carrier").rsplit(
        "/cell", 1
    )[0]
    assert f'hx-get="{base}/remove"' in page, "remove is not a confirm fetch"
    assert f'hx-post="{base}/remove"' not in page, "remove still writes on one click"

    confirm = client.get(f"{base}/remove")

    assert confirm.status_code == 200
    assert seat["carrier"] in confirm.text
    assert "the layer stays" in confirm.text
    assert path.read_bytes() == before, "the confirm GET wrote to the file"


def _first_line(conn, placement):
    lines = sync.program_lines(conn, placement.id)
    assert lines, f"{placement.ref} has no lines"
    return lines[0][0]


def _two_line_placement(client, org, tmp_path):
    """A linked placement whose program has TWO lines (gl + cy), so the
    applies-to choice is real."""
    from datetime import date

    from test_linking_flow import write_program
    from towerkit.model import Layer, Line, Participant, Period, Program
    from towerkit.model import Placement as TkPlacement

    conn = client.app.state.conn
    program = Program(
        insured=org.name,
        program="Two Line Program",
        placement=TkPlacement.BOUND,
        period=Period(start=date(2026, 1, 1), end=date(2027, 1, 1)),
        lines=[
            Line(id="gl", name="General Liability", abbr="GL"),
            Line(id="cy", name="Cyber", abbr="CY"),
        ],
        # equal tops on purpose: an "__all__" layer attaching at the shared
        # top is valid on both lines, so the test refuses only for the
        # reason under test
        layers=[
            Layer(id="primary-gl", name="Primary GL", applies_to=["gl"],
                  attach=0, limit=2_000_000, premium=900_000,
                  participants=[Participant(carrier="Zurich", share_bps=10_000)]),
            Layer(id="primary-cy", name="Primary Cyber", applies_to=["cy"],
                  attach=0, limit=2_000_000, premium=400_000, participants=[]),
        ],
    )
    path = write_program(tmp_path / "two-line.json", program)
    assert sync.confirm_link(conn, path, org.id).ok
    from bookkit.repo import placements as placements_repo

    return placements_repo.by_program_path(conn, str(path))


def test_the_layer_add_form_asks_which_lines(app_and_org, tmp_path):
    """The web used to pass line_ids=[] and towerkit silently defaulted to
    the FIRST line — same keystroke, different data per surface (F5). The
    TUI asks; now the web does too."""
    client, org = app_and_org
    placement = _two_line_placement(client, org, tmp_path)

    form = client.get(f"/accounts/{org.ref}/program/{placement.id}/layers/new").text

    assert "<select" in form
    assert "General Liability" in form
    assert "Cyber" in form
    assert "all lines" in form


def test_an_added_layer_lands_on_the_chosen_line(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)

    added = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={"name": "1st Excess Cyber", "line": "cy",
              "limit_cents": "5,000,000", "premium_cents": ""},
    )

    assert added.status_code == 200
    layer = next(
        ly for ly in sync.layer_details(conn, placement.id)
        if ly["name"] == "1st Excess Cyber"
    )
    assert layer["applies_to"] == ["cy"], f"landed on {layer['applies_to']}"


def test_all_lines_means_all_lines(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)

    added = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={"name": "Umbrella Everything", "line": "__all__",
              "limit_cents": "10,000,000",
              "premium_cents": ""},
    )

    assert added.status_code == 200
    layer = next(
        ly for ly in sync.layer_details(conn, placement.id)
        if ly["name"] == "Umbrella Everything"
    )
    assert sorted(layer["applies_to"]) == ["cy", "gl"]


def test_a_made_up_line_is_refused(app_and_org, tmp_path):
    client, org = app_and_org
    placement = _two_line_placement(client, org, tmp_path)

    refused = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={"name": "Nowhere", "line": "marine",
              "limit_cents": "1,000,000", "premium_cents": ""},
    )

    assert refused.status_code == 200
    assert "Nowhere" not in [
        ly["name"] for ly in sync.layer_details(client.app.state.conn, placement.id)
    ]


def test_scaffold_honours_a_typed_destination(app_and_org, tmp_path):
    """The TUI's `t` lets you change where the file lands; the web confirm
    showed the path and could not (parity gap, phase 2)."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import placements as placements_repo

    bare = placements_repo.create(
        conn, org.id, "Scaffold Here", "2026-04-01", "2027-04-01"
    )
    custom = tmp_path / "elsewhere" / "custom-name.json"

    made = client.post(
        f"/accounts/{org.ref}/program/{bare.id}/scaffold", data={"path": str(custom)}
    )

    assert made.status_code == 200
    assert custom.exists(), "the typed destination was ignored"
    assert placements_repo.get(conn, bare.id).program_path == str(custom)


def test_the_scaffold_confirm_offers_the_path_as_an_input(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    _configure_roots(conn, tmp_path)
    from bookkit.repo import placements as placements_repo

    bare = placements_repo.create(
        conn, org.id, "Input Check", "2026-04-01", "2027-04-01"
    )

    confirm = client.get(f"/accounts/{org.ref}/program/{bare.id}/scaffold").text

    assert 'name="path"' in confirm


def _submission_url(org, placement):
    return f"/accounts/{org.ref}/program/{placement.id}/submissions"


def test_a_submission_is_sent_from_the_program_section(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import submissions as submissions_repo

    market = orgs_repo.list_orgs(conn, kind="market")[0]

    page = client.get(f"/accounts/{org.ref}/program").text
    assert f'hx-get="{_submission_url(org, placement)}/new"' in page

    form = client.get(f"{_submission_url(org, placement)}/new").text
    assert "<select" in form and market.name in form

    sent = client.post(
        _submission_url(org, placement),
        data={"market_org_id": market.id, "underwriter_contact_id": "",
              "sent_on": "2026-08-19", "notes": ""},
    )

    # 204 + HX-Redirect, the same shape the revert POST uses: htmx follows
    # the header; there is no body to swap
    assert sent.status_code == 204
    subs = [
        sub for sub in submissions_repo.for_placement(conn, placement.id)
        if sub.market_org_id == market.id
    ]
    assert subs, "no submission landed on the placement"
    # success redirects to where the submission is VISIBLE — the pipeline tab
    assert sent.headers.get("HX-Redirect", "").endswith(f"/accounts/{org.ref}/pipeline")


def test_a_refused_submission_keeps_the_typed_notes(app_and_org):
    client, org = app_and_org
    placement = _linked(client.app.state.conn, org)[0]

    refused = client.post(
        _submission_url(org, placement),
        data={"market_org_id": "", "underwriter_contact_id": "",
              "sent_on": "2026-08-19", "notes": "half-typed context"},
    ).text

    assert "half-typed context" in refused
    assert "required" in refused


def _terms_base(org, placement, kind):
    return f"/accounts/{org.ref}/program/{placement.id}/{kind}"


def test_the_terms_strip_renders_retentions_and_sublimits(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    assert sync.add_retention(
        conn, placement.id, ["gl"], "deductible", amount_cents=250_000_00
    ).ok
    assert sync.add_sublimit(conn, placement.id, "Flood", 1_000_000_00, ["gl"]).ok
    assert sync.project(conn, Path(placement.program_path), placement_id=placement.id).ok

    page = client.get(f"/accounts/{org.ref}/program").text

    assert "DEDUCTIBLE" in page.upper()
    assert "$250K" in page
    assert "Flood" in page
    assert f'hx-get="{_terms_base(org, placement, "retentions")}/new"' in page
    assert f'hx-get="{_terms_base(org, placement, "sublimits")}/new"' in page


def test_a_retention_edits_in_row_with_subset_honest_lines(app_and_org, tmp_path):
    """The edit form's applies-to is CHECKBOXES: a retention can span a
    subset of lines, and a single-select would silently widen it."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    assert sync.add_retention(
        conn, placement.id, ["gl"], "deductible", amount_cents=250_000_00
    ).ok
    assert sync.project(conn, Path(placement.program_path), placement_id=placement.id).ok
    index = sync.program_terms(conn, placement.id)["retentions"][-1]["index"]

    form = client.get(f"{_terms_base(org, placement, 'retentions')}/{index}/edit").text
    assert 'type="checkbox"' in form
    assert 'checked' in form

    saved = client.post(
        f"{_terms_base(org, placement, 'retentions')}/{index}",
        data={"type": "sir", "amount": "500,000", "line": ["gl", "cy"]},
    )

    assert saved.status_code == 200
    fresh = sync.program_terms(conn, placement.id)["retentions"][index]
    assert fresh["type"] == "sir"
    assert fresh["amount_cents"] == 500_000_00
    assert sorted(fresh["applies_to"]) == ["cy", "gl"]


def test_a_retention_remove_confirms_in_place(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    assert sync.add_retention(
        conn, placement.id, ["gl"], "deductible", amount_cents=250_000_00
    ).ok
    assert sync.project(conn, Path(placement.program_path), placement_id=placement.id).ok
    index = sync.program_terms(conn, placement.id)["retentions"][-1]["index"]
    path = Path(placement.program_path)
    before = path.read_bytes()

    confirm = client.get(f"{_terms_base(org, placement, 'retentions')}/{index}/remove")
    assert confirm.status_code == 200
    assert path.read_bytes() == before

    removed = client.post(f"{_terms_base(org, placement, 'retentions')}/{index}/remove")
    assert removed.status_code == 200
    assert sync.program_terms(conn, placement.id)["retentions"] == []


def test_a_sublimit_adds_in_row(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)

    added = client.post(
        f"{_terms_base(org, placement, 'sublimits')}",
        data={"name": "Wind", "amount": "750,000", "line": ["cy"]},
    )

    assert added.status_code == 200
    sub = sync.program_terms(conn, placement.id)["sublimits"][-1]
    assert sub["name"] == "Wind"
    assert sub["amount_cents"] == 750_000_00
    assert sub["applies_to"] == ["cy"]


def test_a_bad_terms_amount_refuses_in_place(app_and_org, tmp_path):
    client, org = app_and_org
    placement = _two_line_placement(client, org, tmp_path)
    path = Path(placement.program_path)
    before = path.read_bytes()

    refused = client.post(
        f"{_terms_base(org, placement, 'retentions')}",
        data={"type": "deductible", "amount": "250,000.50", "line": ["gl"]},
    )

    assert refused.status_code == 200
    assert path.read_bytes() == before
    assert "amount" in refused.text or "dollar" in refused.text


def test_hand_built_refusals_never_reflect_markup(app_and_org):
    """_panel_refusal and _refusal_page are hand-built HTML — no template,
    no autoescape — and refusal messages can quote user-typed content
    (towerkit diagnostics quote line and layer names verbatim). htmx
    re-executes swapped script tags, so an unescaped message is reflected
    XSS (fresh-eyes review, phase 4). The unknown-line URL vector the review
    named is pre-empted by _line_name's 404, but the seam itself must
    escape: any future caller inherits the safety, not the hole."""
    from bookkit.web.routes.program import _panel_refusal, _refusal_page

    client, org = app_and_org
    payload = "<script>alert(1)</script>"

    for response in (
        _panel_refusal(None, org.ref, org, "x", payload),
        _refusal_page(None, payload, "/accounts/x/program"),
    ):
        body = bytes(response.body).decode()
        assert "<script>" not in body
        assert "&lt;script&gt;" in body


def test_line_chips_reorder_the_columns(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    order = [lid for lid, _ in sync.program_lines(conn, placement.id)]

    page = client.get(f"/accounts/{org.ref}/program").text
    assert f'hx-post="{_lines_base(org, placement)}/{order[0]}/move"' in page

    moved = client.post(
        f"{_lines_base(org, placement)}/{order[0]}/move", data={"delta": "1"}
    )

    assert moved.status_code == 200
    fresh = [lid for lid, _ in sync.program_lines(conn, placement.id)]
    assert fresh[0] == order[1] and fresh[1] == order[0]


def _merge_url(org, placement):
    return f"/accounts/{org.ref}/program/{placement.id}/merge"


def test_the_merge_form_offers_only_same_account_siblings(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import placements as placements_repo

    mine = placements_repo.for_org(conn, org.id)
    source = mine[0]
    other_org = next(
        o for o in orgs_repo.list_orgs(conn, kind="client") if o.id != org.id
    )
    foreign = placements_repo.for_org(conn, other_org.id)

    page = client.get(f"/accounts/{org.ref}/program").text
    assert f'hx-get="{_merge_url(org, source)}"' in page, "no merge control"

    form = client.get(_merge_url(org, source)).text

    assert f'value="{source.id}"' not in form, "the form offers merging into itself"
    for sibling in mine[1:]:
        assert f'value="{sibling.id}"' in form
    for stranger in foreign:
        assert f'value="{stranger.id}"' not in form, "a foreign placement is offered"


def test_a_merge_moves_the_children_and_retires_the_source(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import placements as placements_repo
    from bookkit.repo import submissions as submissions_repo

    mine = placements_repo.for_org(conn, org.id)
    # merge the UNLINKED duplicate into the linked one — two file-backed refuse
    source = next(p for p in mine if not p.program_path)
    target = next(p for p in mine if p.program_path)
    from bookkit.repo import orgs as orgs_repo

    market = orgs_repo.list_orgs(conn, kind="market")[0]
    moved = submissions_repo.create(
        conn, market.id, "2026-08-01", placement_id=source.id
    )

    merged = client.post(_merge_url(org, source), data={"target_id": target.id})

    assert merged.status_code == 200
    assert source.ref not in merged.text, "the retired source still shows"
    fresh = submissions_repo.get(conn, moved.id)
    assert fresh.placement_id == target.id, "the submission did not move"


def test_merging_two_file_backed_placements_refuses_with_the_panel_intact(
    app_and_org, tmp_path
):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import placements as placements_repo

    linked_one = next(
        p for p in placements_repo.for_org(conn, org.id) if p.program_path
    )
    linked_two = _two_line_placement(client, org, tmp_path)

    refused = client.post(
        _merge_url(org, linked_one), data={"target_id": linked_two.id}
    )

    assert refused.status_code == 200
    assert 'id="programs-panel"' in refused.text
    assert "two sources of truth" in refused.text
    assert placements_repo.get(conn, linked_one.id).deleted_at is None


def test_every_drawn_layer_lands_on_its_worksheet(app_and_org):
    """The tower is a SURFACE (F11): a drawn block carries its layer id and a
    select URL, so a click opens that layer's worksheet. Attributes only —
    every string and rect still comes off the renderer, so the agreement
    rule is untouched."""
    import re

    client, org = app_and_org
    conn = client.app.state.conn

    page = client.get(f"/accounts/{org.ref}/program").text

    drawn = set(re.findall(r'data-layer-id="([^"]+)"', page))
    assert drawn, "the tower carries no hit targets"
    for placement in _linked(conn, org):
        for layer in sync.layer_details(conn, placement.id):
            drawn.discard(layer["id"])
            assert f"layer={layer['id']}" in page, (
                f"no way to select {layer['id']} from the page"
            )
    assert not drawn, f"drawn layers no placement owns: {drawn}"


def test_the_tower_click_handler_exists_and_selects(app_and_org):
    """Clicking a drawn block selects that layer's worksheet — the drawing is
    a surface, and the handler rides the section's own data-select-base so
    the JS composes no URL of its own."""
    client, org = app_and_org

    js = client.get("/static/form-host.js").text
    css = client.get("/static/app.css").text

    assert "data-layer-id" in js, "no click handler for the tower"
    assert "data-select-base" in js, "the click no longer selects the layer"
    assert "prefers-reduced-motion" in css, "animations have no motion guard"


def _layer_base(org, placement, layer_id):
    return f"/accounts/{org.ref}/program/{placement.id}/layers/{layer_id}"


def test_the_details_row_offers_applies_to_chips(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    layer = sync.layer_details(conn, placement.id)[0]

    row = _worksheet_page(client, org, placement, layer["id"])

    for _line_id, name in sync.program_lines(conn, placement.id):
        assert name in row, f"line {name} not offered"
    assert f'hx-post="{_layer_base(org, placement, layer["id"])}/applies-to"' in row


def test_toggling_a_line_on_rescopes_the_layer(app_and_org, tmp_path):
    """First caller ever for sync.set_applies_to — dead code since the sync
    layer was built. Valid move: an excess spanning both equal-top primaries
    narrows to one, then widens back."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    added = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={"name": "Umbrella Both", "line": "__all__",
              "limit_cents": "5,000,000", "premium_cents": ""},
    )
    assert added.status_code == 200
    layer_id = next(
        ly["id"] for ly in sync.layer_details(conn, placement.id)
        if ly["name"] == "Umbrella Both"
    )

    narrowed = client.post(
        f"{_layer_base(org, placement, layer_id)}/applies-to", data={"line": "cy"}
    )

    assert narrowed.status_code == 200
    layer = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["id"] == layer_id
    )
    assert layer["applies_to"] == ["gl"], f"toggle off left {layer['applies_to']}"

    widened = client.post(
        f"{_layer_base(org, placement, layer_id)}/applies-to", data={"line": "cy"}
    )
    assert widened.status_code == 200
    layer = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["id"] == layer_id
    )
    assert sorted(layer["applies_to"]) == ["cy", "gl"]


def test_toggling_off_the_last_line_is_refused_with_the_file_untouched(
    app_and_org, tmp_path
):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    layer = next(
        ly for ly in sync.layer_details(conn, placement.id)
        if ly["applies_to"] == ["gl"]
    )
    path = Path(placement.program_path)
    before = path.read_bytes()

    refused = client.post(
        f"{_layer_base(org, placement, layer['id'])}/applies-to", data={"line": "gl"}
    )

    assert refused.status_code == 200
    assert path.read_bytes() == before
    assert "cover" in refused.text or "line" in refused.text


def test_statutory_asks_first_then_replaces_the_limit_with_the_word(
    app_and_org, tmp_path
):
    """Grant, 2026-08-19: statutory was modelled, rendered and never
    changeable from the browser — 'fully built but not accessible'."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    layer = sync.layer_details(conn, placement.id)[0]
    path = Path(placement.program_path)
    before = path.read_bytes()

    row = _worksheet_page(client, org, placement, layer["id"])
    assert f'hx-get="{_layer_base(org, placement, layer["id"])}/statutory"' in row

    confirm = client.get(f"{_layer_base(org, placement, layer['id'])}/statutory")
    assert confirm.status_code == 200
    assert "statutory" in confirm.text
    assert path.read_bytes() == before, "the confirm GET wrote"

    marked = client.post(
        f"{_layer_base(org, placement, layer['id'])}/statutory",
        data={"statutory": "true"},
    )
    assert marked.status_code == 200
    fresh = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["id"] == layer["id"]
    )
    assert fresh["statutory"] is True
    assert ">statutory<" in marked.text, "the limit column does not say the word"


def test_marking_statutory_clears_follows_and_attach(app_and_org, tmp_path):
    """The combination bug the phase-3 review caught: statutory cover
    attaches at nothing and follows nothing, and a set_statutory that left
    either behind made the validator refuse a follows-on layer every time,
    with a message that never named the toggle to clear."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    # statutory cover owns its whole column (towerkit refuses a statutory
    # layer sharing a line), so it lives on its own line — added here, which
    # arrives with the pending layer this test then marks
    added = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/lines",
        data={"name": "Workers Comp"},
    )
    assert added.status_code == 200
    wc_line = next(
        lid for lid, name in sync.program_lines(conn, placement.id)
        if name == "Workers Comp"
    )
    layer_id = next(
        ly["id"] for ly in sync.layer_details(conn, placement.id)
        if ly["applies_to"] == [wc_line]
    )
    assert client.post(
        f"{_layer_base(org, placement, layer_id)}/follows", data={"follows": "true"}
    ).status_code == 200

    marked = client.post(
        f"{_layer_base(org, placement, layer_id)}/statutory",
        data={"statutory": "true"},
    )

    assert marked.status_code == 200
    from towerkit.model import load_program

    layer = next(
        ly for ly in load_program(_file_of(conn, placement)).layers
        if ly.id == layer_id
    )
    assert layer.statutory is True
    assert layer.attach == 0
    assert layer.follows_underlying is False


def test_leaving_statutory_requires_the_replacing_limit(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    layer = sync.layer_details(conn, placement.id)[0]
    assert client.post(
        f"{_layer_base(org, placement, layer['id'])}/statutory",
        data={"statutory": "true"},
    ).status_code == 200

    restored = client.post(
        f"{_layer_base(org, placement, layer['id'])}/statutory",
        data={"statutory": "false", "limit": "3,000,000"},
    )

    assert restored.status_code == 200
    fresh = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["id"] == layer["id"]
    )
    assert fresh["statutory"] is False
    assert fresh["limit_cents"] == 3_000_000_00


def test_follows_underlying_toggles_from_the_details_row(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    added = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={"name": "GL Excess", "line": "gl", "attach_cents": "2,000,000",
              "limit_cents": "3,000,000", "premium_cents": ""},
    )
    assert added.status_code == 200
    layer_id = next(
        ly["id"] for ly in sync.layer_details(conn, placement.id)
        if ly["name"] == "GL Excess"
    )

    row = _worksheet_page(client, org, placement, layer_id)
    assert f'hx-post="{_layer_base(org, placement, layer_id)}/follows"' in row

    toggled = client.post(
        f"{_layer_base(org, placement, layer_id)}/follows", data={"follows": "true"}
    )

    assert toggled.status_code == 200
    from towerkit.model import load_program

    layer = next(
        ly for ly in load_program(_file_of(conn, placement)).layers
        if ly.id == layer_id
    )
    assert layer.follows_underlying is True
    # the returned row must SHOW the state, not just write it — and the
    # assertion must target the FOLLOWS button: a bare "is-on" substring was
    # satisfied by the layer's own applies-to chip (fresh-eyes review)
    assert "follows-toggle is-on" in toggled.text


def _lines_base(org, placement):
    return f"/accounts/{org.ref}/program/{placement.id}/lines"


def test_the_lines_strip_renders_every_line_as_a_cell(app_and_org, tmp_path):
    """D1: the browser can finally NAME the cover. A scaffolded program was
    stuck on 'Coverage TBD' forever because no web control could touch a
    line (review finding F4)."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)

    page = client.get(f"/accounts/{org.ref}/program").text

    for line_id, name in sync.program_lines(conn, placement.id):
        assert (
            f'data-cell-action="{_lines_base(org, placement)}/{line_id}/cell/name"'
            in page
        ), f"line {name} is not an editable cell"
    assert f'hx-get="{_lines_base(org, placement)}/new"' in page, "no + line control"


def test_renaming_a_line_cascades_and_rerenders_the_panel(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)

    saved = client.post(
        f"{_lines_base(org, placement)}/cy/cell/name", data={"name": "Cyber Liability"}
    )

    assert saved.status_code == 200
    lines = dict(sync.program_lines(conn, placement.id))
    assert "Cyber Liability" in lines.values()
    assert "cy" not in lines, "the id did not follow the name"
    new_id = next(lid for lid, nm in lines.items() if nm == "Cyber Liability")
    layer = next(
        ly for ly in sync.layer_details(conn, placement.id)
        if ly["name"] == "Primary Cyber"
    )
    assert new_id in layer["applies_to"], "the cascade left the layer stranded"
    _assert_panel_swap(saved, placement.id)


def test_line_remove_asks_first_naming_what_dies_with_it(app_and_org, tmp_path):
    client, org = app_and_org
    placement = _two_line_placement(client, org, tmp_path)
    path = Path(placement.program_path)
    before = path.read_bytes()

    confirm = client.get(f"{_lines_base(org, placement)}/cy/remove")

    assert confirm.status_code == 200
    assert "Primary Cyber" in confirm.text, "the confirm hides the dying layer"
    assert path.read_bytes() == before, "the confirm GET wrote"

    removed = client.post(f"{_lines_base(org, placement)}/cy/remove")

    assert removed.status_code == 200
    conn = client.app.state.conn
    assert "cy" not in dict(sync.program_lines(conn, placement.id))
    assert "Primary Cyber" not in [
        ly["name"] for ly in sync.layer_details(conn, placement.id)
    ]


def test_removing_the_last_line_is_refused_in_place(app_and_org, tmp_path):
    client, org = app_and_org
    placement = _two_line_placement(client, org, tmp_path)
    assert client.post(f"{_lines_base(org, placement)}/cy/remove").status_code == 200
    path = Path(placement.program_path)
    before = path.read_bytes()

    refused = client.post(f"{_lines_base(org, placement)}/gl/remove")

    assert refused.status_code == 200
    assert "only line" in refused.text
    assert path.read_bytes() == before


def test_adding_a_line_lands_with_a_pending_layer(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)

    form = client.get(f"{_lines_base(org, placement)}/new").text
    assert 'name="name"' in form

    added = client.post(
        f"{_lines_base(org, placement)}", data={"name": "Marine Cargo"}
    )

    assert added.status_code == 200
    lines = dict(sync.program_lines(conn, placement.id))
    assert "Marine Cargo" in lines.values()
    new_id = next(lid for lid, nm in lines.items() if nm == "Marine Cargo")
    covering = [
        ly for ly in sync.layer_details(conn, placement.id)
        if new_id in ly["applies_to"]
    ]
    assert covering, "the new line arrived empty — the validator forbids that"


def _renew_url(org, placement):
    return f"/accounts/{org.ref}/program/{placement.id}/renew"


def test_renew_asks_first_and_names_the_consequences(app_and_org):
    """The confirm writes nothing and says what renew DOES: next period
    dates, and for a linked placement the towerkit file cloned and linked at
    birth. (The account header's Renew button stayed unrendered under D4
    because it names no target; this control is placement-scoped.)"""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    from bookkit.repo import placements as placements_repo

    count_before = len(placements_repo.for_org(conn, org.id))

    page = client.get(f"/accounts/{org.ref}/program").text
    assert f'hx-get="{_renew_url(org, placement)}"' in page, "no renew control"

    confirm = client.get(_renew_url(org, placement))

    assert confirm.status_code == 200
    assert "cloned" in confirm.text or "clone" in confirm.text
    assert len(placements_repo.for_org(conn, org.id)) == count_before, (
        "the confirm GET created something"
    )


def test_renew_rolls_the_placement_and_clones_the_file(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    from bookkit.repo import placements as placements_repo

    before_ids = {p.id for p in placements_repo.for_org(conn, org.id)}

    renewed = client.post(_renew_url(org, placement))

    assert renewed.status_code == 200
    new = [p for p in placements_repo.for_org(conn, org.id) if p.id not in before_ids]
    assert len(new) == 1, "renew created no placement"
    assert new[0].period_from > placement.period_from
    assert new[0].program_path, "the renewal was not linked to a cloned file"
    assert Path(new[0].program_path).exists()
    assert 'id="programs-panel"' in renewed.text, "the new program is not shown"


def test_a_refused_renew_keeps_the_panel(app_and_org):
    """Renewing twice collides on the cloned file name; the second attempt
    must refuse with the panel intact and the message in its error slot."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]

    assert client.post(_renew_url(org, placement)).status_code == 200
    refused = client.post(_renew_url(org, placement))

    assert refused.status_code == 200
    assert 'id="programs-panel"' in refused.text
    assert "already exists" in refused.text


def _layer_remove_url(org, placement, layer_id):
    return f"/accounts/{org.ref}/program/{placement.id}/layers/{layer_id}/remove"


def test_layer_remove_asks_first_naming_the_seats(app_and_org):
    """D2. The confirm writes nothing and names the markets going with the
    layer — the blast radius is the fact a person needs before answering."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index, seat = _first_seat(conn, org)
    path = Path(placement.program_path)
    before = path.read_bytes()

    details = _worksheet_page(client, org, placement, layer["id"])
    assert f'hx-get="{_layer_remove_url(org, placement, layer["id"])}"' in details

    confirm = client.get(_layer_remove_url(org, placement, layer["id"]))

    assert confirm.status_code == 200
    assert layer["name"] in confirm.text
    assert seat["carrier"] in confirm.text, "the confirm hides the seats going with it"
    assert path.read_bytes() == before, "the confirm GET wrote to the file"


def test_a_removed_layer_is_gone_seats_and_all(app_and_org):
    """A seated EXCESS layer: removing a line's only layer is refused by
    towerkit (line-empty), so the doomed layer must not be a lone primary."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    doomed = next(
        ly for ly in sync.layer_details(conn, placement.id)
        if ly["participants"] and ly["attach_cents"] > 0
    )

    removed = client.post(_layer_remove_url(org, placement, doomed["id"]))

    assert removed.status_code == 200
    fresh = sync.layer_details(conn, placement.id)
    assert doomed["id"] not in [ly["id"] for ly in fresh]
    _assert_panel_swap(removed, placement.id)


def test_a_layer_removal_that_leaves_a_gap_saves_in_place(app_and_org, tmp_path):
    """Removing a middle layer strands the one above over an open band.
    line-gap is a WARNING, not a refusal (2026-08-21): the write SUCCEEDS —
    sliding '2nd Excess' down to close the tower is not done, since that
    would silently change what the client is covered for — and the gap is
    stated where the question was asked, file changed."""
    client, org = app_and_org
    placement = _two_line_placement(client, org, tmp_path)
    for name, attach in (("1st Excess", "2,000,000"), ("2nd Excess", "7,000,000")):
        added = client.post(
            f"/accounts/{org.ref}/program/{placement.id}/layers",
            data={"name": name, "line": "gl", "attach_cents": attach,
                  "limit_cents": "5,000,000", "premium_cents": ""},
        )
        assert added.status_code == 200
    path = Path(placement.program_path)
    before = path.read_bytes()

    removed = client.post(_layer_remove_url(org, placement, "1st-excess"))

    assert removed.status_code == 200
    assert path.read_bytes() != before
    assert "gap" in removed.text.lower()


def _placement_cell(org, placement, key):
    return f"/accounts/{org.ref}/program/{placement.id}/cell/{key}"


def test_placement_header_facts_are_cells(app_and_org):
    """Name, period, status and commission were static text beside editable
    layer cells (the web could not edit a placement's own facts at all —
    parity gap, phase 2)."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]

    page = client.get(f"/accounts/{org.ref}/program").text

    for key in ("program_name", "period_from", "period_to", "status", "commission_bps"):
        assert f'data-cell-action="{_placement_cell(org, placement, key)}"' in page, key


def test_a_placement_name_saves_through_the_file(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    path = Path(placement.program_path)

    saved = client.post(
        _placement_cell(org, placement, "program_name"),
        data={"program_name": "Renamed From The Header"},
    )

    assert saved.status_code == 200
    assert "Renamed From The Header" in path.read_text()
    from bookkit.repo import placements as placements_repo

    assert (
        placements_repo.get(conn, placement.id).program_name
        == "Renamed From The Header"
    )
    _assert_panel_swap(saved, placement.id)


def test_a_placement_status_saves_to_the_row_only(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    path = Path(placement.program_path)
    sha_before = sync.file_sha256(path)
    new_status = "quoted" if placement.status != "quoted" else "submitted"

    saved = client.post(
        _placement_cell(org, placement, "status"), data={"status": new_status}
    )

    assert saved.status_code == 200
    from bookkit.repo import placements as placements_repo

    assert placements_repo.get(conn, placement.id).status == new_status
    assert sync.file_sha256(path) == sha_before, "a book-owned edit wrote the file"


def test_a_refused_placement_date_keeps_the_editor_and_the_file(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    path = Path(placement.program_path)
    before = path.read_bytes()

    refused = client.post(
        _placement_cell(org, placement, "period_to"), data={"period_to": "1999-01-01"}
    )

    assert refused.status_code == 200
    assert path.read_bytes() == before
    assert "cell-editing" in refused.text, "the refusal did not keep the editor open"
    assert 'value="1999-01-01"' in refused.text


def test_the_status_editor_is_a_select_of_the_real_statuses(app_and_org):
    client, org = app_and_org
    placement = _linked(client.app.state.conn, org)[0]

    editor = client.get(_placement_cell(org, placement, "status") + "/edit").text

    assert "<select" in editor
    for status in ("prospective", "submitted", "quoted", "bound", "lapsed"):
        assert status in editor


def _worksheet_page(client, org, placement, layer_id) -> str:
    """The section with this layer's worksheet selected — the redesign's
    replacement for the details-row GET: selection is a section render."""
    got = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/worksheet?layer={layer_id}"
    )
    assert got.status_code == 200
    return got.text


def test_layer_details_row_carries_the_three_hidden_fields(app_and_org):
    """policy number and the policy dates were editable by URL and invisible
    in the UI — the exact 'route nothing can reach' smell the 2026-08-19
    review round already caught once on the share edit (F6)."""
    client, org = app_and_org
    placement, layer = _first_layer(client.app.state.conn, org)

    row = _worksheet_page(client, org, placement, layer["id"])

    for key in ("policy_number", "period_from", "period_to"):
        assert f'data-field="{key}"' in row, f"{key} still unreachable"


def test_every_layer_row_offers_its_details(app_and_org):
    """Every layer in the file is one click away: the index row's select GET
    opens its worksheet — the redesign's replacement for the chevron."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]

    page = client.get(f"/accounts/{org.ref}/program").text

    for layer in sync.layer_details(conn, placement.id):
        assert f"layer={layer['id']}" in page, f"{layer['id']} unreachable"


def test_a_detail_cell_saves_as_a_span_not_a_td(app_and_org):
    """The details row's cells live in a <td>, so their own element is a
    <span> — a <td> swapped back in its place would be dropped outright by
    the HTML parser (no table-row ancestor at the swap point), taking the
    saved value's display with it."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    saved = client.post(
        _cell(org, placement, layer, "policy_number"), data={"policy_number": "POL-777"}
    )

    assert saved.status_code == 200
    assert saved.text.lstrip().startswith("<span"), "detail cell came back as a td"
    fresh = next(
        ly for ly in sync.layer_details(conn, placement.id) if ly["id"] == layer["id"]
    )
    assert fresh["policy_number"] == "POL-777"


def _markets_base(org, placement, layer_id):
    return f"/accounts/{org.ref}/program/{placement.id}/layers/{layer_id}/markets"


def test_market_add_row_is_always_the_tables_last_row(app_and_org):
    """Binding a market is a first-class act (design 1C): the add row is the
    participation table's permanent last row — visible labels, the carrier
    completing from the book's market names, the bind button posting the
    row's own inputs."""
    client, org = app_and_org
    placement, layer = _first_layer(client.app.state.conn, org)

    page = _worksheet_page(client, org, placement, layer["id"])

    base = _markets_base(org, placement, layer["id"])
    assert 'class="market-add-row"' in page
    assert f'hx-post="{base}"' in page
    assert "<datalist" in page, "the carrier input offers no completion"
    assert 'hx-include="closest tr"' in page


def test_market_add_refusal_keeps_the_typing_in_the_row(app_and_org):
    """Commit in place: a refused bind re-renders the add row with the
    message and everything typed still in it — never a fragment somewhere
    else on the page."""
    client, org = app_and_org
    placement, layer = _first_layer(client.app.state.conn, org)

    refused = client.post(
        _markets_base(org, placement, layer["id"]),
        data={"carrier": "Chubb", "share_pct": "not a share"},
    )

    assert refused.status_code == 200
    assert refused.text.lstrip().startswith("<tr"), "the refusal left the row"
    assert 'value="not a share"' in refused.text
    assert 'value="Chubb"' in refused.text
    assert "cell-error-msg" in refused.text


def test_a_refused_market_add_keeps_the_typed_carrier_in_place(app_and_org):
    """Commit in place, at the anchor: the refusal re-renders the inline form
    with the input intact — never a panel swap, never a fragment somewhere
    else on the page."""
    client, org = app_and_org
    placement, layer = _first_layer(client.app.state.conn, org)

    refused = client.post(
        _markets_base(org, placement, layer["id"]),
        data={"carrier": "Half Typed Re", "share_pct": ""},
    ).text

    assert 'value="Half Typed Re"' in refused
    assert "share is required" in refused
    assert f'id="program-{placement.id}"' not in refused, "the refusal swapped a panel"


def test_market_keep_restores_the_chip(app_and_org):
    client, org = app_and_org
    placement, layer, index, seat = _first_seat(client.app.state.conn, org)
    base = _market_cell(org, placement, layer["id"], index, "carrier").rsplit(
        "/cell", 1
    )[0]

    chip = client.get(base)

    assert chip.status_code == 200
    assert seat["carrier"] in chip.text
    assert f'data-cell-action="{base}/cell/carrier"' in chip.text


# --- what the review caught -----------------------------------------------
#
# Six defects, three of which the tests above were too weak to see. Each one
# gets an assertion here rather than a comment.


def test_a_refused_add_keeps_the_panel_and_the_typed_values(app_and_org):
    """The refusal used to be a bare message, and every control that could
    trigger it swapped the whole <section class="program"> — so a refused add
    deleted the layers table, the add controls and the panel's own id, leaving
    the placement unusable until a full page reload.

    A sub-dollar limit forces the refusal now (2026-08-21, whole-branch
    review finding 2): the form used to type an attach INSIDE an existing
    layer's span to force a `line-overlap`, but that typed attachment is
    exactly what finding 2 removed from this form — `sync.add_layer` no
    longer takes one a caller can aim, so it can no longer be made to overlap
    from here. towerkit's whole-dollar rule on the LIMIT still refuses
    (`_require_dollars`, unaffected by this branch), and it is refused
    inside the mutation exactly like the stack editor's own version of this
    same test, so this still proves the write-side refusal path, not just
    the parser's."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)

    refused = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={
            "name": "Floating Excess",
            "line": _first_line(conn, placement),
            "limit_cents": "1.50",
            "premium_cents": "",
        },
    )

    assert refused.status_code == 200
    assert "<form" in refused.text, "the form did not come back"
    assert "Floating Excess" in refused.text, "the typed name was thrown away"
    assert "dollar" in refused.text.lower() or "limit" in refused.text.lower()


def test_the_add_form_posts_into_the_form_host_not_over_the_panel(app_and_org):
    """The swap target IS the bug. A form that targets the panel replaces the
    panel with whatever comes back, and a refusal is not a panel."""
    client, org = app_and_org
    placement, _ = _first_layer(client.app.state.conn, org)

    form = client.get(f"/accounts/{org.ref}/program/{placement.id}/layers/new").text

    assert 'hx-target="closest .form-host"' in form
    assert 'hx-swap="innerHTML"' in form
    assert "closest .program" not in form


# test_a_blank_attachment_is_refused_in_the_broker_s_language RETIRED
# (whole-branch review finding 2, 2026-08-21): it posted a blank
# `attach_cents` to prove a broker never saw the raw
# "unsupported operand type(s) for %: 'NoneType' and 'int'" the field used to
# produce. The field itself is gone from this form now — the route always
# calls `sync.add_layer(..., attach_cents=None, ...)` on purpose, which is a
# deliberate value, never a blank one a broker typed — so there is nothing
# left on THIS surface that can reach the crash. The same regression is now
# guarded directly against `sync.add_layer` itself, where the None really
# comes from: see
# test_add_layer_with_no_typed_attach_seats_on_the_existing_top in
# test_program_edits.py.
def test_a_blank_limit_is_refused_in_the_broker_s_language(app_and_org):
    """LIMIT is still a required, typed field on this form (attach is not,
    since finding 2 above) — the same historical class of bug (a blank
    required money field reaching towerkit as a raw type error) is still
    reachable through it, so it is still guarded here."""
    client, org = app_and_org
    placement, _ = _first_layer(client.app.state.conn, org)

    refused = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={"name": "No Money", "line": _first_line(client.app.state.conn, placement),
              "limit_cents": "", "premium_cents": ""},
    )

    assert "limit is required" in refused.text
    assert "NoneType" not in refused.text


def test_a_blank_share_is_refused_in_the_broker_s_language(app_and_org):
    client, org = app_and_org
    placement, layer = _first_layer(client.app.state.conn, org)

    refused = client.post(
        _markets(org, placement, layer["id"]), data={"carrier": "Nobody Re", "share_pct": ""}
    )

    assert "share is required" in refused.text


def test_a_placement_with_no_file_says_so_rather_than_an_errno(app_and_org):
    """Path(str(None)) is the literal path "None", so the first thing a broker
    saw was "[Errno 2] No such file or directory: 'None'"."""
    client, org = app_and_org
    from bookkit.repo import placements

    conn = client.app.state.conn
    bare = placements.create(
        conn, org.id, "Unlinked Program", "2026-03-01", "2027-03-01",
    )

    refused = client.post(
        f"/accounts/{org.ref}/program/{bare.id}/layers",
        data={"name": "Nowhere", "attach_cents": "0", "limit_cents": "1,000,000",
              "premium_cents": ""},
    )

    assert "no program file linked" in refused.text
    assert "Errno" not in refused.text


def test_a_market_can_be_reached_for_editing_from_the_page(app_and_org):
    """market_cell_save existed and nothing rendered a way to it: the share
    test passed by POSTing the URL directly, which is not evidence a broker
    can correct a share. The carrier rides the CELL contract; the share is
    the worksheet's PREVIEW INPUT (the deliberate blur-commit exception) —
    both must be reachable from the rendered page."""
    import re

    client, org = app_and_org
    placement, _ = _first_layer(client.app.state.conn, org)

    page = client.get(f"/accounts/{org.ref}/program").text

    assert re.search(
        r'data-cell-action="[^"]*markets/\d+/cell/carrier"', page
    ), "no way in to the carrier from the page"
    assert re.search(
        r'hx-post="[^"]*markets/\d+/share-preview"', page
    ), "no way in to the share from the page"
    assert re.search(r'data-cell-action="[^"]*markets/\d+/cell/carrier"', page)


def test_a_layer_edit_refreshes_the_rows_it_moved(app_and_org):
    """write_through heals follows-underlying layers, so a limit change re-seats
    OTHER rows. Returning one cell left them showing the pre-write number — a
    tower on screen that does not exist in the file."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    saved = client.post(
        _cell(org, placement, layer, "name"), data={"name": "Refreshed Layer"}
    )

    _assert_panel_swap(saved, placement.id)


def test_a_saved_layer_cell_cannot_destroy_its_own_table(app_and_org):
    """The 2026-08-20 regression, asserted at the level it actually broke.

    The old response was `<td>…</td><section hx-swap-oob>…</section>`. Both
    halves were correct HTML on their own and every string-matching test
    passed. In a browser the response never survived parsing: htmx reads the
    FIRST tag to choose its parse context, `<td>` puts the whole response
    inside `<table><tbody><tr>`, and the `<section>` — not table content — is
    foster-parented out before htmx can swap it. Chrome showed the program
    section with an empty `.table-scroll` and none of its 14 rows; the layer
    edit itself had saved, so a refresh made it look fine and the bug looked
    like a ghost.

    A single top-level element is what makes the parse context irrelevant.
    """
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    saved = client.post(
        _cell(org, placement, layer, "premium_cents"), data={"premium_cents": "123,456.00"}
    )

    assert saved.status_code == 200
    _assert_panel_swap(saved, placement.id)
    # and every layer is still reachable — the thing the browser lost
    for row in sync.layer_details(conn, placement.id):
        assert f"layer={row['id']}" in saved.text


def test_a_write_keeps_the_tower_it_just_changed(app_and_org):
    """The panel's answer must carry the drawing. It did not: `tower` was
    rendered only by the full-page builder, so every save returned a panel
    with no tower key and the chart disappeared — the one thing on the page
    that shows what the edit did to the shape of the program."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    assert 'class="tower"' in client.get(f"/accounts/{org.ref}/program").text

    saved = client.post(
        _cell(org, placement, layer, "name"), data={"name": "Still Drawn"}
    )

    assert 'class="tower"' in saved.text, "the write dropped the tower drawing"


def test_a_structure_write_keeps_its_own_worksheet_selected(app_and_org, tmp_path):
    """Statutory, follows-underlying and applies-to are all edited FROM the
    worksheet, and all answer with the whole section because they move the
    index and the tower too. The section must come back with the SAME layer
    selected — a write that throws the broker to the first layer costs them
    their place on every click."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={"name": "GL Excess", "line": "gl", "attach_cents": "2,000,000",
              "limit_cents": "3,000,000", "premium_cents": ""},
    )
    layer_id = next(
        ly["id"] for ly in sync.layer_details(conn, placement.id)
        if ly["name"] == "GL Excess"
    )

    toggled = client.post(
        f"{_layer_base(org, placement, layer_id)}/follows", data={"follows": "true"}
    )

    assert f'data-layer-row="{layer_id}"' in toggled.text, (
        "the write did not keep its own worksheet selected"
    )
    assert "follows-toggle is-on" in toggled.text, "the pane does not show the new state"
    _assert_panel_swap(toggled, placement.id)


def test_a_saved_cell_says_where_to_put_the_caret_back(app_and_org):
    """Replacing the whole section costs the user their place unless the
    answer names the cell they were in. inline-cell.js reads data-refocus."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    saved = client.post(
        _cell(org, placement, layer, "name"), data={"name": "Refocus Me"}
    )

    assert f'data-refocus="{layer["id"]}:name"' in saved.text


def test_a_refused_cell_edit_still_answers_with_just_the_cell(app_and_org):
    """The retarget belongs to the SUCCESS path only. A refusal has to land
    back in the cell the user is still typing in — commit-in-place — so it
    must not carry the panel's swap headers."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    refused = client.post(
        _cell(org, placement, layer, "premium_cents"), data={"premium_cents": "not money"}
    )

    assert refused.status_code == 200
    assert "HX-Retarget" not in refused.headers
    assert _top_level_tags(refused.text) == ["td"]


# --- phase 3: creating a program ----------------------------------------------


def test_a_new_placement_is_created_from_the_tab(app_and_org):
    client, org = app_and_org
    from bookkit.repo import placements

    conn = client.app.state.conn
    before = {p.id for p in placements.for_org(conn, org.id)}

    created = client.post(
        f"/accounts/{org.ref}/program/placements",
        data={
            "program_name": "2027 Casualty Program",
            "period_from": "2027-01-01",
            "period_to": "2028-01-01",
            "status": "prospective",
            "total_premium": "",
            "total_limit": "",
            "commission_bps": "",
        },
    )

    assert created.status_code == 200
    fresh = [p for p in placements.for_org(conn, org.id) if p.id not in before]
    assert [p.program_name for p in fresh] == ["2027 Casualty Program"]
    assert _latest_batch(conn).source == "web"


def test_a_placement_with_no_period_is_refused_with_the_form_intact(app_and_org):
    client, org = app_and_org

    refused = client.post(
        f"/accounts/{org.ref}/program/placements",
        data={"program_name": "Half Typed", "period_from": "", "period_to": "",
              "status": "prospective"},
    )

    assert refused.status_code == 200
    assert "Half Typed" in refused.text, "the typed name was thrown away"
    assert "required" in refused.text


def test_the_scaffold_confirm_shows_where_the_file_goes_and_writes_nothing(app_and_org, tmp_path):
    """Creating a file is exactly the kind of thing that gets a plan first."""
    client, org = app_and_org
    from bookkit.repo import placements

    conn = client.app.state.conn
    _configure_roots(conn, tmp_path)
    bare = placements.create(
        conn, org.id, "Needs A File", "2026-05-01", "2027-05-01",
    )

    confirm = client.get(f"/accounts/{org.ref}/program/{bare.id}/scaffold")

    assert confirm.status_code == 200
    assert ".json" in confirm.text, "the plan does not say where the file goes"
    assert placements.get(conn, bare.id).program_path is None
    # the destination NAMED in the plan, not "any json under tmp_path" — the
    # snapshot_db fixture projects the seeded programs into that same tree, so
    # the broad glob failed on files this test never touched
    from bookkit.web.routes.program import _scaffold_destination

    assert not _scaffold_destination(conn, org, bare).exists()


def test_scaffolding_writes_the_file_and_links_it(app_and_org, tmp_path):
    client, org = app_and_org
    from bookkit.repo import placements

    conn = client.app.state.conn
    _configure_roots(conn, tmp_path)
    bare = placements.create(
        conn, org.id, "Needs A File", "2026-05-01", "2027-05-01",
    )

    made = client.post(f"/accounts/{org.ref}/program/{bare.id}/scaffold")

    assert made.status_code == 200
    linked = placements.get(conn, bare.id)
    assert linked.program_path, "the placement was not linked to its new file"
    # stored relative to the configured root, resolved through programpath —
    # a bare Path() here would be asserting the old absolute storage rule
    assert not Path(linked.program_path).is_absolute()
    assert _file_of(conn, linked).exists()
    assert sync.layer_details(conn, bare.id), "the scaffold carries no layer"


def test_scaffolding_a_placement_that_already_has_a_file_is_refused(app_and_org, tmp_path):
    """And the refusal NAMES the file, so the answer is actionable rather than
    'no'."""
    client, org = app_and_org

    conn = client.app.state.conn
    _configure_roots(conn, tmp_path)
    placement, _ = _first_layer(conn, org)

    refused = client.post(f"/accounts/{org.ref}/program/{placement.id}/scaffold")

    assert refused.status_code == 200
    assert Path(placement.program_path).name in refused.text


def test_scaffolding_with_no_program_root_configured_points_at_the_setting(app_and_org):
    client, org = app_and_org
    from bookkit.repo import placements, settings

    conn = client.app.state.conn
    settings.set_program_roots(conn, [])
    bare = placements.create(
        conn, org.id, "Needs A File", "2026-05-01", "2027-05-01",
    )

    refused = client.post(f"/accounts/{org.ref}/program/{bare.id}/scaffold")

    assert refused.status_code == 200
    assert "program file location" in refused.text
    assert placements.get(conn, bare.id).program_path is None


def _configure_roots(conn, tmp_path):
    from bookkit.repo import settings

    root = tmp_path / "programs"
    root.mkdir(exist_ok=True)
    settings.set_program_roots(conn, [str(root)])
    return root


# --- phase 4: the drawn tower -------------------------------------------------


def test_the_page_prints_no_tower_string_the_renderer_did_not_choose(app_and_org):
    """R66 at the bookkit end.

    towerkit's own suite proves the renderer quotes labels.py and composes
    nothing. This proves bookkit PRINTS what the renderer handed it — the other
    half of the same rule, at the other side of the seam. What it deliberately
    does NOT do is compare the panel to the export: they are allowed to fit
    text differently, and a test that compared them would fail on a legitimate
    wrap and teach everyone to weaken it."""
    from pathlib import Path as _Path

    from towerkit.model import load_program

    from bookkit.web.tower import panel

    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    built = panel(load_program(_Path(placement.program_path)))
    assert built["blocks"], "the seeded program draws no blocks — test proves nothing"

    page = client.get(f"/accounts/{org.ref}/program").text

    for block in built["blocks"]:
        for line in block["lines"]:
            assert line in page, f"the panel dropped a line the renderer chose: {line!r}"


def test_the_tower_is_drawn_for_a_linked_placement(app_and_org):
    client, org = app_and_org

    page = client.get(f"/accounts/{org.ref}/program").text

    assert "tower-chart" in page
    assert "tower-block" in page


def test_a_placement_with_no_file_draws_no_tower(app_and_org):
    """None and an empty tower are different facts."""
    client, org = app_and_org
    from bookkit.repo import placements

    conn = client.app.state.conn
    placements.create(conn, org.id, "Unlinked", "2026-03-01", "2027-03-01")

    page = client.get(f"/accounts/{org.ref}/program").text
    after = page.split("Unlinked", 1)[1]

    assert "This placement has no program file." in after


def test_the_not_to_scale_caveat_is_printed(app_and_org):
    """The vertical scale is compressed so a $2M primary and a $50M excess can
    share one picture. Dropping the caveat leaves the drawing asserting a
    linear scale it does not have."""
    from pathlib import Path as _Path

    from towerkit.model import load_program

    from bookkit.web.tower import panel

    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    caveat = panel(load_program(_Path(placement.program_path)))["caveat"]
    assert caveat, "the seeded program is drawn to scale — test proves nothing"

    assert caveat in client.get(f"/accounts/{org.ref}/program").text


# --- phase 5: the file moved under this write ---------------------------------


def _touch_out_of_band(placement) -> str:
    """Simulate towerkit's editor (or an MCP call) writing the file while a
    browser tab is open on it. Returns the new layer name so a test can prove
    it SURVIVED an overwrite rather than being clobbered."""
    path = Path(placement.program_path)
    text = path.read_text()
    marker = "Touched Elsewhere"
    layers = sync.layer_details.__module__  # keep the import honest
    del layers
    import json

    data = json.loads(text)
    data["layers"][-1]["name"] = marker
    path.write_text(json.dumps(data, indent=2) + "\n")
    return marker


def test_a_conflict_offers_three_ways_out_rather_than_an_error(app_and_org):
    """The file moved under this write. That is not the same as a value the
    validator refused, and answering it with the same one-line message leaves
    the user with no way forward except retyping into a form that will refuse
    again."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    _touch_out_of_band(placement)

    refused = client.post(
        _cell(org, placement, layer, "name"), data={"name": "My Edit"}
    )

    assert refused.status_code == 200
    body = refused.text.lower()
    assert "reload" in body
    assert "overwrite" in body
    assert "keep editing" in body


def test_a_conflict_on_a_detail_key_is_a_span_with_the_three_ways(app_and_org):
    """The details-row cells are spans; the conflict fragment used to be a
    hardcoded <td>, which the parser DROPS at that swap point (no table-row
    ancestor) — the field silently blanked with no Reload/Overwrite/Keep at
    all (fresh-eyes review, 2026-08-19)."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    _touch_out_of_band(placement)

    refused = client.post(
        _cell(org, placement, layer, "policy_number"), data={"policy_number": "POL-1"}
    )

    assert refused.status_code == 200
    body = refused.text
    assert body.lstrip().startswith("<span"), "conflict came back as a parser-dropped td"
    assert "</span>" in body and "<td" not in body
    for choice in ("Reload", "Overwrite", "Keep editing"):
        assert choice in body
    assert 'hx-target="closest span"' in body


def test_a_refused_scaffold_keeps_the_programs_panel(app_and_org):
    """The scaffold confirm's POST targets #programs-panel with outerHTML, so
    a refusal that answers with a bare fragment REPLACES the whole panel —
    every placement's rows, the tower, the add control and the panel's own id
    (fresh-eyes review, 2026-08-19). The refusal must come back as the panel
    with the message in its error slot."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]  # already has a file -> guaranteed refusal

    refused = client.post(f"/accounts/{org.ref}/program/{placement.id}/scaffold")

    assert refused.status_code == 200
    assert 'id="programs-panel"' in refused.text, "the refusal swapped the panel away"
    assert "already has a program file" in refused.text
    assert placement.program_name in refused.text, "the panel came back without its rows"


def test_a_conflict_writes_nothing(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    _touch_out_of_band(placement)
    before = Path(placement.program_path).read_bytes()

    client.post(_cell(org, placement, layer, "name"), data={"name": "My Edit"})

    assert Path(placement.program_path).read_bytes() == before


def test_reload_catches_the_projection_up_and_shows_what_is_there_now(app_and_org):
    """Reload discards MY draft and takes THEIR file. After it, the page shows
    the out-of-band change."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    theirs = _touch_out_of_band(placement)

    reloaded = client.post(
        _cell(org, placement, layer, "name") + "/reload", data={"name": "My Edit"}
    )

    assert reloaded.status_code == 200
    names = [ly["name"] for ly in sync.layer_details(conn, placement.id)]
    assert theirs in names
    assert "My Edit" not in names


def test_overwrite_lands_my_edit_on_top_without_losing_theirs(app_and_org):
    """THE claim this whole design rests on. Overwrite is a RETRY, not a
    clobber: it re-projects and re-applies the ONE field I changed, so a
    structural change somebody else made survives underneath it. Reusing the
    towerkit TUI's force-write — which pushes a whole in-memory program — would
    discard their change instead."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    theirs = _touch_out_of_band(placement)

    written = client.post(
        _cell(org, placement, layer, "name") + "/overwrite", data={"name": "My Edit"}
    )

    assert written.status_code == 200
    names = [ly["name"] for ly in sync.layer_details(conn, placement.id)]
    assert "My Edit" in names, "my edit did not land"
    assert theirs in names, "their change was clobbered — this is a retry, not a force"


def test_keep_editing_leaves_the_field_open_with_what_was_typed(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    _touch_out_of_band(placement)

    still = client.post(
        _cell(org, placement, layer, "name") + "/keep", data={"name": "My Edit"}
    )

    assert still.status_code == 200
    assert "My Edit" in still.text
    assert "<input" in still.text


def test_an_ordinary_refusal_is_not_dressed_as_a_conflict(app_and_org):
    """The two are handled differently and must be told apart by the CODE, not
    by the fact that something went wrong."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    refused = client.post(
        _cell(org, placement, layer, "premium_cents"), data={"premium_cents": "1,234.56"}
    )

    assert "reload" not in refused.text.lower()
    assert "overwrite" not in refused.text.lower()


# --- phase 5: putting a program write back from the rail ----------------------


def test_a_program_write_is_reverted_from_the_rail(app_and_org):
    """File contents are not event_log rows, so batch undo cannot restore a
    program file — it refuses those batches outright. The rail used to stop
    there and say so, which is honest and useless. It now calls the file-side
    revert the MCP server has had all along."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    was = layer["name"]

    client.post(_cell(org, placement, layer, "name"), data={"name": "Renamed By Web"})
    batch = _latest_batch(conn)
    assert batch.tool == "program_layer_edit"

    reverted = client.post(
        f"/accounts/{org.ref}/changes/{batch.ref}/revert?tab=program"
    )

    assert reverted.status_code in (200, 204)
    names = [ly["name"] for ly in sync.layer_details(conn, placement.id)]
    assert was in names, "the pre-image was not put back"
    assert "Renamed By Web" not in names


def test_reverting_a_program_write_is_refused_once_the_file_moved_on(app_and_org):
    """The snapshot records the sha the write left behind. Anything newer —
    towerkit's editor, an MCP call, a later web edit — and putting the
    pre-image back would silently discard it."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    client.post(_cell(org, placement, layer, "name"), data={"name": "Renamed By Web"})
    batch = _latest_batch(conn)
    _touch_out_of_band(placement)
    after_theirs = Path(placement.program_path).read_bytes()

    refused = client.post(
        f"/accounts/{org.ref}/changes/{batch.ref}/revert?tab=program"
    )

    assert refused.status_code in (200, 204)
    assert Path(placement.program_path).read_bytes() == after_theirs, "their edit was lost"


def test_the_rail_says_which_of_the_two_happened(app_and_org):
    """A revert that reports nothing is indistinguishable from one that did
    nothing."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    client.post(_cell(org, placement, layer, "name"), data={"name": "Renamed By Web"})
    batch = _latest_batch(conn)
    done = client.post(f"/accounts/{org.ref}/changes/{batch.ref}/revert?tab=program")

    landing = done.headers.get("HX-Redirect")
    assert landing, "no redirect to carry the outcome"
    page = client.get(landing).text
    assert "put back" in page.lower() or "reverted" in page.lower()


# --- D6: towerkit's derived field surface, in the browser ----------------------
#
# Seventeen towerkit fields were writable only from towerkit's own editor,
# behind the TUI's `o`, which a browser does not have — "built but not
# accessible" (statutory, 2026-08-19). What is asserted here is not seventeen
# fields but the SEAM: that a field declared in `_PLACED` actually renders,
# actually saves through towerkit's guards, and actually reaches the file.


def _field_url(org, placement, kind, name, addr):
    return (
        f"/accounts/{org.ref}/program/{placement.id}/field/{kind}/{addr}/{name}"
    )


def _reload_program(conn, placement):
    from bookkit import sync as _sync

    return _sync.linked_program(conn, placement.id).program


def test_every_placed_field_actually_renders_on_the_page(app_and_org):
    """A GREEN SUITE PROVES NOTHING BROKE, NOT THAT THE NEW PATH IS TAKEN
    (CLAUDE.md, 2026-08-15): `_PLACED` is a table of intentions, and a table of
    intentions is what shipped `entity_actions.push_form` past 33 call sites
    that bypassed it. So every row is checked against the RENDERED page.

    The details-row fields are fetched with the row, because that is where they
    live; everything else is on the panel itself.
    """
    from bookkit.web.routes.program import _PLACED

    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    # A named limit's own two cells exist only where a named limit does, which
    # is right — an empty strip should print no cells. One is added so the row
    # is present and the two are genuinely checked rather than skipped.
    assert sync.add_named_limit(
        conn, placement.id, layer["id"], "Products", 2_000_000_00
    ).ok
    everywhere = _worksheet_page(client, org, placement, layer["id"])

    missing = [
        key for key in _PLACED
        if f'data-field="{key}"' not in everywhere
    ]
    assert not missing, f"declared in _PLACED and rendered nowhere: {missing}"


def test_a_derived_layer_field_saves_into_the_towerkit_file(app_and_org):
    """The prose a broker states on a quote and towerkit prints on the SOI."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    addr = f"{layer['id']}:_"

    saved = client.post(
        _field_url(org, placement, "layer", "limitsDetail", addr),
        data={"layer.limitsDetail": "$5M per occurrence / $10M aggregate"},
    )

    assert saved.status_code == 200
    assert saved.text.lstrip().startswith("<span"), "a detail cell came back as a td"
    fresh = next(
        ly for ly in _reload_program(conn, placement).layers if ly.id == layer["id"]
    )
    assert fresh.limits_detail == "$5M per occurrence / $10M aggregate"


def test_a_derived_field_is_cleared_by_emptying_it(app_and_org):
    """`clearable` is towerkit's own answer, read off the model — emptying the
    box drops the key from the file rather than writing an empty string."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    addr = f"{layer['id']}:_"
    url = _field_url(org, placement, "layer", "notes", addr)
    client.post(url, data={"layer.notes": "quota share above $25M"})

    client.post(url, data={"layer.notes": ""})

    fresh = next(
        ly for ly in _reload_program(conn, placement).layers if ly.id == layer["id"]
    )
    assert fresh.notes is None


def test_a_value_towerkit_refuses_comes_back_in_the_cell_with_the_typing_intact(
    app_and_org,
):
    """Commit-in-place, for a refusal that came from towerkit's guards rather
    than bookkit's parser — the user should not be able to tell which."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    refused = client.post(
        _field_url(org, placement, "named_limit", "amount", f"{layer['id']}:0"),
        data={"named_limit.amount": "1,234.56"},
    )

    assert refused.status_code == 200
    assert "1,234.56" in refused.text, "the typed value was thrown away"
    assert "cell-error" in refused.text


def test_a_line_whose_id_starts_with_i_keeps_its_address(app_and_org, tmp_path):
    """THE INLAND MARINE BUG. The address was first encoded as a bare id for a
    target and `i3` for a position, told apart by a leading "i" — so the line
    id "im", which every real book has, parsed as "index m", lost its target
    and took the whole Program tab down with it. There is no safe leading
    character to pick out of user-supplied ids; both halves are always present
    now, and this is the case that says so.
    """
    from bookkit.web.routes.program import _addr, _unaddr

    # THE INVARIANT, first: an address survives the round trip whatever the id
    # looks like. Asserted directly because neither half of the encoding is
    # individually wrong — the bug needed a target-shaped segment AND a reader
    # that guessed at it, so only the round trip can see it. "i3" is the nastiest
    # case: it is a plausible line id and a plausible position at once.
    for target, index in [
        ("im", None), ("i3", None), ("i", None), ("gl", None), (None, None),
        ("im", 0), ("i3", 2), (None, 3), (None, 0),
    ]:
        assert _unaddr(_addr(target, index)) == (target, index), (
            f"{target!r}/{index!r} does not survive the address round trip"
        )

    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    assert sync.add_line(conn, placement.id, "Inland Marine").ok
    assert sync.project(
        conn, Path(placement.program_path), placement_id=placement.id
    ).ok
    line_id = next(
        lid for lid, _ in sync.program_lines(conn, placement.id) if lid.startswith("i")
    )

    page = client.get(f"/accounts/{org.ref}/program")

    assert page.status_code == 200
    saved = client.post(
        _field_url(org, placement, "line", "abbr", f"{line_id}:_"),
        data={"line.abbr": "IM"},
    )
    assert saved.status_code == 200
    assert next(
        ln for ln in _reload_program(conn, placement).lines if ln.id == line_id
    ).abbr == "IM"


def test_a_column_label_answers_with_the_whole_panel(app_and_org):
    """Every layer table header re-letters, so the cell alone would leave the
    headers stale until a refresh."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    line_id = sync.program_lines(conn, placement.id)[0][0]

    saved = client.post(
        _field_url(org, placement, "line", "abbr", f"{line_id}:_"),
        data={"line.abbr": "XX"},
    )

    _assert_panel_swap(saved, placement.id)


def test_a_chart_option_materialises_the_render_block_it_needs(app_and_org):
    """`program.render` is absent on most files and is on towerkit's DENYLIST —
    no caller may set the container wholesale, because setting it blanks every
    sibling. It is built from its own defaults through `edit.set_container`,
    which is the one door, and the write then lands on the member asked for.
    """
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    assert _reload_program(conn, placement).render is None

    saved = client.post(
        _field_url(org, placement, "program", "render.showPremiums", "_:_"),
        data={"program.render.showPremiums": "false"},
    )

    assert saved.status_code == 200
    render = _reload_program(conn, placement).render
    assert render is not None
    assert render.show_premiums is False
    # every sibling still at ITS default, not blanked
    assert render.show_totals is True


def test_the_tower_download_honours_the_saved_chart_options(app_and_org):
    """Before D6 this route rendered with the library defaults and ignored the
    file's own settings, so a broker who had turned premiums off in towerkit's
    editor got them back on every download bookkit produced. Shipping the chart
    strip without this would have made it a set of controls that provably
    changed nothing."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    client.post(
        _field_url(org, placement, "program", "render.showTotals", "_:_"),
        data={"program.render.showTotals": "false"},
    )

    # SPY ON THE RENDERER, not on the load. The first version of this test
    # watched `_loaded_program` and asserted the file's own settings — which
    # only proved the SAVE worked, and stayed green with the hand-off deleted.
    # What has to be pinned is the argument the renderer actually receives.
    import towerkit.render.mpl_program as mpl

    seen = {}
    real = mpl.render_program

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    mpl.render_program = spy
    try:
        drawn = client.get(
            f"/accounts/{org.ref}/program/{placement.id}/export/tower.svg"
        )
    finally:
        mpl.render_program = real

    assert drawn.status_code == 200
    assert seen.get("show_totals") is False, (
        f"the renderer was handed {seen.get('show_totals')!r} — the file's own "
        "saved chart options are being ignored again"
    )
    assert b"<svg" in drawn.content


def _named_limits_base(org, placement, layer_id):
    return (
        f"/accounts/{org.ref}/program/{placement.id}/layers/{layer_id}/named-limits"
    )


def test_a_named_limit_is_added_edited_and_removed_from_the_row(app_and_org):
    """The several figures a policy states where `limit` states one. Adding a
    ROW is not a field write and has no set_field to derive from, so the two
    collection routes are hand-written; the row's own name and amount are
    ordinary derived cells."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    base = _named_limits_base(org, placement, layer["id"])

    added = client.post(base, data={"name": "Products & Completed Ops", "amount": "2m"})
    assert added.status_code == 200
    _assert_panel_swap(added, placement.id)
    limits = sync.named_limits_of(conn, placement.id, layer["id"])
    assert [(nl["name"], nl["amount_cents"]) for nl in limits] == [
        ("Products & Completed Ops", 2_000_000_00)
    ]

    client.post(
        _field_url(org, placement, "named_limit", "amount", f"{layer['id']}:0"),
        data={"named_limit.amount": "3m"},
    )
    assert sync.named_limits_of(conn, placement.id, layer["id"])[0][
        "amount_cents"
    ] == 3_000_000_00

    removed = client.post(f"{base}/0/remove")
    assert removed.status_code == 200
    assert sync.named_limits_of(conn, placement.id, layer["id"]) == []


def test_a_term_note_saves_with_the_figure_and_is_cleared_by_emptying_it(
    app_and_org, tmp_path
):
    """One write, one undo unit: the note travels in the same save as the
    amount it qualifies. Emptying the box CLEARS it — towerkit's own `notes=`
    keyword cannot express that, because None already means "leave alone"
    there, which is what `set_notes` is for."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    assert sync.add_retention(
        conn, placement.id, ["gl"], "deductible", amount_cents=250_000_00,
        notes="per claim, eroding",
    ).ok
    assert sync.project(
        conn, Path(placement.program_path), placement_id=placement.id
    ).ok
    index = sync.program_terms(conn, placement.id)["retentions"][-1]["index"]

    form = client.get(f"{_terms_base(org, placement, 'retentions')}/{index}/edit").text
    assert "per claim, eroding" in form, "the note is not prefilled — the next save clears it"

    client.post(
        f"{_terms_base(org, placement, 'retentions')}/{index}",
        data={"type": "sir", "amount": "500,000", "line": ["gl"], "notes": ""},
    )

    assert sync.program_terms(conn, placement.id)["retentions"][index]["notes"] is None


def test_a_derived_cell_offers_the_same_three_way_when_the_file_moves(app_and_org):
    """A conflict is not a refused value, and the answer is a CHOICE. The
    derived cells reuse the layer cells' dialog rather than growing a second
    one — two dialogs is how the same question comes to have two answers."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    _touch_out_of_band(placement)

    refused = client.post(
        _field_url(org, placement, "layer", "notes", f"{layer['id']}:_"),
        data={"layer.notes": "typed while the file moved"},
    )

    assert refused.status_code == 200
    assert "/reload" in refused.text and "/overwrite" in refused.text
    assert "typed while the file moved" in refused.text
    assert layer["name"] in refused.text, "the dialog does not say which row"


# --- the export theme, end to end ---------------------------------------------
#
# `render.theme` shipped in D6 as a free-text cell holding a file path, which is
# the open field mistake-proofing literature says to replace with a picker: a
# broker cannot discover which themes exist, and nothing stops them naming one
# that does not. Worse, towerkit REFUSES an absolute `render.theme` (program
# files are portable by contract), so the free-text cell could store a value
# that made the file fail validation — and because every later write
# re-validates, that wedged the file until somebody edited the JSON by hand.


def _theme_url(org, placement):
    return (
        f"/accounts/{org.ref}/program/{placement.id}"
        f"/field/program/_:_/render.theme"
    )


def test_the_theme_picker_offers_only_themes_a_program_file_may_name(app_and_org):
    """towerkit's validator refuses an absolute `render.theme`. The packaged
    themes are absolute, so offering them is offering a choice that makes the
    file invalid — the write is refused and the file is stuck.

    Filtering here rather than policing on the way in is the point: a control
    must not advertise a choice its own system rejects.
    """
    from bookkit.web.routes.program import _theme_choices

    for label, value in _theme_choices():
        assert not Path(value).is_absolute(), (
            f"the picker offers {label!r} as {value!r}, which towerkit refuses "
            "as non-portable"
        )


def test_an_absolute_theme_is_refused_before_it_can_wedge_the_file(app_and_org):
    """The server checks the picker's own options, because the markup only
    constrains a mouse and this route is reachable by anything that can POST."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]

    refused = client.post(
        _theme_url(org, placement),
        data={"program.render.theme": "/etc/themes/evil.json"},
    )

    assert refused.status_code == 200
    assert "cell-error" in refused.text
    assert _reload_program(conn, placement).render is None, "it was written anyway"


def test_the_blank_option_clears_the_theme_rather_than_being_refused(app_and_org, tmp_path):
    """THE BLANK OPTION IS A REAL ANSWER — a cleared theme is what "use
    towerkit's built-in" means, and the <select> offers it.

    It was refused for an afternoon: `checked_option` compares against the
    option VALUES and the blank one is "", which is not in that set. The
    control looked like it worked, because the cell re-renders either way.
    """
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    theme = _portable_theme(tmp_path)
    assert client.post(
        _theme_url(org, placement), data={"program.render.theme": theme}
    ).status_code == 200
    assert _reload_program(conn, placement).render.theme == theme

    cleared = client.post(_theme_url(org, placement), data={"program.render.theme": ""})

    assert cleared.status_code == 200
    assert "cell-error" not in cleared.text
    assert _reload_program(conn, placement).render.theme is None


def _portable_theme(tmp_path) -> str:
    """A real theme under a RELATIVE path, which is the only kind a program file
    may name. Copied from towerkit's own packaged set so it is a theme the
    renderer genuinely accepts, not a stub that would fail `theme_problems`."""
    import os
    import shutil
    from pathlib import Path as _P

    from towerkit.theme import available_themes

    source = next(p for p in available_themes() if p.stem == "default")
    themes = _P(os.getcwd()) / "themes"
    themes.mkdir(exist_ok=True)
    target = themes / "regression-theme.json"
    if not target.exists():
        shutil.copy(source, target)
    return "themes/regression-theme.json"


def test_the_download_is_rendered_with_the_theme_the_program_names(app_and_org, tmp_path):
    """The whole pathway: pick a theme, and the SVG comes out of the renderer
    with it. Spying on the renderer's argument, not on the saved value — the
    first version of the neighbouring test asserted the save and stayed green
    with the hand-off deleted."""
    import towerkit.render.mpl_program as mpl

    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    theme = _portable_theme(tmp_path)
    client.post(_theme_url(org, placement), data={"program.render.theme": theme})

    seen = {}
    real = mpl.render_program

    def spy(program, loaded_theme, *args, **kwargs):
        seen["theme"] = loaded_theme
        return real(program, loaded_theme, *args, **kwargs)

    mpl.render_program = spy
    try:
        drawn = client.get(
            f"/accounts/{org.ref}/program/{placement.id}/export/tower.svg"
        )
    finally:
        mpl.render_program = real

    assert drawn.status_code == 200
    from towerkit.theme import load_theme

    assert seen["theme"] == load_theme(theme), (
        "the export was rendered with a different theme than the file names"
    )


def test_a_theme_that_is_gone_refuses_the_download_instead_of_quietly_substituting(
    app_and_org, tmp_path
):
    """A client-facing chart rendered in the wrong brand, silently, is worse
    than no chart. The refusal names the theme and how to fix it."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    theme = _portable_theme(tmp_path)
    client.post(_theme_url(org, placement), data={"program.render.theme": theme})
    Path(theme).unlink()

    drawn = client.get(f"/accounts/{org.ref}/program/{placement.id}/export/tower.svg")

    assert drawn.status_code == 200
    assert "regression-theme" in drawn.text
    assert "themes" in drawn.text
    assert not drawn.content.startswith(b"<?xml"), "it rendered anyway"


# --- the chevron never looks dead (Grant, 2026-08-21) --------------------------


def test_selecting_an_unknown_layer_answers_with_the_section(app_and_org):
    """htmx swaps NOTHING on a 4xx or a 5xx, so a selection that refused with
    a status code would leave the index looking simply dead. An id this
    program does not hold (a stale URL, another placement's layer) falls back
    to the first layer — the section still renders, retargeted."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, _layer = _first_layer(conn, org)

    got = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/worksheet?layer=no-such-layer"
    )

    assert got.status_code == 200, "a bare 4xx swaps nothing and reads as dead"
    _assert_panel_swap(got, placement.id)
    assert 'class="worksheet"' in got.text, "no worksheet rendered at all"


def test_an_exception_in_the_details_row_is_shown_not_swallowed(
    app_and_org, monkeypatch
):
    """The row names the failure AND the exception still reaches the log: a 500
    is a bug somebody has to fix, and hiding it would trade one silence for
    another. What changes is that the person who clicked can see why."""
    from bookkit.web.routes import program as program_route

    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    def boom(*_args, **_kwargs):
        raise RuntimeError("the file moved under it")

    monkeypatch.setattr(program_route.sync, "policy_partners_of", boom)

    got = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/worksheet?layer={layer['id']}"
    )

    assert got.status_code == 200
    assert "the file moved under it" in got.text
    assert "RuntimeError" in got.text


def test_a_worksheet_that_cannot_build_still_shows_the_index(app_and_org, monkeypatch):
    """A pane that fails to build must not take the section with it — the
    index and the band still render, so the broker can select another layer
    instead of reloading the page."""
    from bookkit.web.routes import program as program_route

    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    def boom(*_args, **_kwargs):
        raise RuntimeError("worksheet build failed")

    monkeypatch.setattr(program_route.sync, "policy_partners_of", boom)

    got = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/worksheet?layer={layer['id']}"
    )

    assert got.status_code == 200
    assert 'class="structure-index"' in got.text or "index-row" in got.text, (
        "the index went down with the worksheet"
    )


# --- the write preview and the rescope consequence (design 1C/3B) -------------


def test_a_share_edit_previews_before_it_saves(app_and_org):
    """The one deliberate exception to blur-commits: a share typed in the
    worksheet projects the write — signed figure, dollars still open, where
    it writes — and NOTHING lands until Save."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index, seat = _first_seat(conn, org)
    path = Path(placement.program_path)
    before = path.read_bytes()
    base = _market_cell(org, placement, layer["id"], index, "carrier").rsplit(
        "/cell", 1
    )[0]

    preview = client.post(f"{base}/share-preview", data={"share_pct": "80"})

    assert preview.status_code == 200
    assert "Signed becomes" in preview.text
    assert "one revertible batch" in preview.text
    assert path.read_bytes() == before, "the preview wrote"
    # Save commits through the PREVIEW route (commit=1): its refusal is
    # preview-shaped, which is what the host holds — never a bare <td>
    # editor with no retarget (review C1).
    assert f'hx-post="{base}/share-preview"' in preview.text
    assert '"share_pct": "80"' in preview.text
    assert '"commit": "1"' in preview.text

    saved = client.post(
        f"{base}/share-preview", data={"share_pct": "80", "commit": "1"}
    )
    assert saved.status_code == 200
    _assert_panel_swap(saved, placement.id)
    assert path.read_bytes() != before, "Save did not commit"
    fresh = next(
        ly for ly in sync.layer_details(conn, placement.id)
        if ly["id"] == layer["id"]
    )
    assert any(p["share_pct"] == 80.0 for p in fresh["participants"])


def test_a_save_refused_between_preview_and_commit_answers_preview_shaped(
    app_and_org,
):
    """The between-preview-and-save conflict: the file moves under the
    preview, Save refuses — and the refusal is the PREVIEW block again (the
    shape the host holds), 200, message in the page, no retarget to a cell
    fragment (review C1)."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index, seat = _first_seat(conn, org)
    path = Path(placement.program_path)
    base = _market_cell(org, placement, layer["id"], index, "carrier").rsplit(
        "/cell", 1
    )[0]

    preview = client.post(f"{base}/share-preview", data={"share_pct": "80"})
    assert "Signed becomes" in preview.text

    # tab B edits the file: the sha guard must refuse tab A's Save
    path.write_text(path.read_text().replace("{", "{ ", 1))

    refused = client.post(
        f"{base}/share-preview", data={"share_pct": "80", "commit": "1"}
    )

    assert refused.status_code == 200
    assert refused.text.lstrip().startswith("<div"), (
        "the refusal is not preview-shaped"
    )
    assert "cell-error-msg" in refused.text
    assert '"commit": "1"' not in refused.text, "a refused Save offers Save again"


def test_an_oversigned_share_preview_refuses_with_no_save(app_and_org):
    """Previewing an edit the commit would refuse, then refusing it on Save,
    would be the preview lying about the write — so the refusal shows
    towerkit's words and offers only Discard. The seat is made genuinely
    oversignable first: the review found the old guard's refusal branch
    never executed (C23)."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index, seat = _first_seat(conn, org)
    # make the seat genuinely oversignable: shrink it, seat a second market
    assert sync.update_participant(
        conn, placement.id, str(layer["id"]), seat["carrier"], share_bps=9_000
    ).ok
    assert sync.add_participant(
        conn, placement.id, str(layer["id"]), "Second Seat Co", 1_000
    ).ok
    base = _market_cell(org, placement, layer["id"], index, "carrier").rsplit(
        "/cell", 1
    )[0]

    preview = client.post(f"{base}/share-preview", data={"share_pct": "95"})

    assert preview.status_code == 200
    assert "Signed becomes" not in preview.text, (
        "an over-sign previewed as OK — the refusal branch is not reachable"
    )
    assert "cell-error-msg" in preview.text
    assert "over-signed" in preview.text, "not towerkit's own sentence"
    assert '"commit": "1"' not in preview.text, (
        "a refused preview still offers Save"
    )


def test_the_worksheet_share_is_a_preview_input_not_a_cell(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index, seat = _first_seat(conn, org)

    page = _worksheet_page(client, org, placement, layer["id"])

    assert 'class="share-input' in page
    assert "share-preview" in page
    # and NOT also a blur-commit cell — the one recorded exception must not
    # quietly reverse (review C30): no share cell action inside the
    # participation table
    table = page[page.index("ws-participation") : page.index("ws-covers")]
    assert 'data-cell-action' not in table or 'cell/share_pct"' not in table


def test_dropping_a_line_states_the_consequence_first(app_and_org, tmp_path):
    """Design 3B: turning a line off a spanning slab renders the consequence
    — what the line keeps, in dollars, that premium is not re-rated — and
    writes NOTHING until Drop."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    # A slab across both lines, seated where both top out together.
    added = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={"name": "Bridge", "line": "gl", "attach_cents": "2,000,000",
              "limit_cents": "3,000,000", "premium_cents": ""},
    )
    assert added.status_code == 200
    assert any(
        ly["name"] == "Bridge" for ly in sync.layer_details(conn, placement.id)
    )
    shared = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={"name": "Wide Excess", "line": "__all__", "attach_cents": "5,000,000",
              "limit_cents": "10,000,000", "premium_cents": "1,000,000"},
    )
    assert shared.status_code == 200
    layer_id = next(
        ly["id"] for ly in sync.layer_details(conn, placement.id)
        if ly["name"] == "Wide Excess"
    )
    path = Path(placement.program_path)
    before = path.read_bytes()

    # The worksheet's ON pills fetch the confirm, never post directly.
    page = _worksheet_page(client, org, placement, layer_id)
    confirm_url = (
        f"/accounts/{org.ref}/program/{placement.id}"
        f"/layers/{layer_id}/applies-to/confirm"
    )
    assert f'hx-get="{confirm_url}?line=' in page

    confirm = client.get(f"{confirm_url}?line=cy")

    assert confirm.status_code == 200
    assert "would be left with" in confirm.text
    assert "does not re-rate" in confirm.text
    assert "Keep it" in confirm.text
    assert path.read_bytes() == before, "the consequence GET wrote"


# --- the new-program worksheet (design 2B) ------------------------------------


def test_the_new_program_worksheet_renders_with_the_source_cards(app_and_org):
    client, org = app_and_org

    page = client.get(f"/accounts/{org.ref}/program/new")

    assert page.status_code == 200
    assert "New program for" in page.text
    assert "Copy last year" in page.text  # the seeded book has a linked program
    assert "Start empty" in page.text
    assert "What will be written" in page.text
    assert "towerkit validates before anything is saved" in page.text


def test_stacking_a_layer_keeps_the_typing_and_shows_the_running_attachment(app_and_org):
    """Each row seats on the last: the attachment is the running total,
    rendered as text — never an input."""
    client, org = app_and_org

    page = client.post(
        f"/accounts/{org.ref}/program/new",
        data={
            "source": "empty", "name": "Fresh Casualty",
            "period_from": "2027-01-01", "period_to": "2028-01-01",
            "status": "prospective", "lines": "General Liability",
            "act": "stack", "new_line": "General Liability",
            "new_name": "Primary GL", "new_limit": "2m",
        },
    )

    assert page.status_code == 200
    assert "Primary GL" in page.text
    assert "xs $0" in page.text
    # the next row's running total is the first layer's top
    assert "xs $2,000,000" in page.text
    assert 'name="attach' not in page.text, "an attachment became typeable"


def test_creating_an_empty_program_writes_a_validated_file_and_links_it(
    app_and_org, tmp_path
):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import placements as placements_repo

    _configure_roots(conn, tmp_path)
    before = {p.id for p in placements_repo.for_org(conn, org.id)}
    created = client.post(
        f"/accounts/{org.ref}/program/new",
        data={
            "source": "empty", "name": "Fresh Casualty",
            "period_from": "2027-01-01", "period_to": "2028-01-01",
            "status": "prospective", "lines": "General Liability, Cyber",
            "stk_line": "General Liability", "stk_name": "Primary GL",
            "stk_limit": "2m", "act": "create",
        },
        follow_redirects=False,
    )

    assert created.status_code == 303
    new = [p for p in placements_repo.for_org(conn, org.id) if p.id not in before]
    assert len(new) == 1
    placement = new[0]
    assert placement.program_name == "Fresh Casualty"
    assert placement.program_path, "the file was not linked"
    from towerkit.model import load_program

    program = load_program(sync.program_file(conn, placement))
    assert [line.name for line in program.lines] == ["General Liability", "Cyber"]
    gl = next(ly for ly in program.layers if ly.name == "Primary GL")
    assert gl.attach == 0 and gl.limit == 2_000_000
    # the layerless line arrived with its pending layer — an empty line is a
    # towerkit ERROR and could never have been written
    assert any(
        ly.name == "To be placed" and "cyber" in ly.applies_to[0]
        for ly in program.layers
    )


def test_copying_last_year_strips_premiums_and_bound_shares(app_and_org, tmp_path):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import placements as placements_repo

    _configure_roots(conn, tmp_path)
    latest = max(
        (p for p in placements_repo.for_org(conn, org.id) if p.program_path),
        key=lambda p: p.period_to,
    )
    before = {p.id for p in placements_repo.for_org(conn, org.id)}
    created = client.post(
        f"/accounts/{org.ref}/program/new",
        data={
            "source": "copy", "name": "Renewal Casualty",
            "period_from": latest.period_to, "period_to": "2099-01-01",
            "status": "prospective", "lines": "", "act": "create",
        },
        follow_redirects=False,
    )

    assert created.status_code == 303
    new = [p for p in placements_repo.for_org(conn, org.id) if p.id not in before]
    assert len(new) == 1
    from towerkit.model import load_program

    program = load_program(sync.program_file(conn, new[0]))
    source = load_program(sync.program_file(conn, latest))
    assert [ly.id for ly in program.layers] == [ly.id for ly in source.layers]
    assert all(ly.participants == [] for ly in program.layers), "shares came across"
    assert all(ly.premium is None for ly in program.layers), "premiums came across"


def test_a_refused_create_keeps_the_worksheet_and_creates_nothing(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit.repo import placements as placements_repo

    before = {p.id for p in placements_repo.for_org(conn, org.id)}
    refused = client.post(
        f"/accounts/{org.ref}/program/new",
        data={
            "source": "empty", "name": "Broken", "period_from": "2027-01-01",
            "period_to": "2026-01-01",  # ends before it starts
            "status": "prospective", "lines": "General Liability", "act": "create",
        },
    )

    assert refused.status_code == 200
    assert 'value="Broken"' in refused.text, "the typing was lost"
    assert {p.id for p in placements_repo.for_org(conn, org.id)} == before, (
        "a refused create still made a placement"
    )


def test_move_up_and_the_top_edge_refusal_through_the_web(app_and_org, tmp_path):
    """The two new primary controls, driven end to end — the review found no
    web coverage for either direction or edge (C25)."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={"name": "GL Excess", "line": "gl", "attach_cents": "2,000,000",
              "limit_cents": "3,000,000", "premium_cents": ""},
    )
    layer_id = next(
        ly["id"] for ly in sync.layer_details(conn, placement.id)
        if ly["name"] == "GL Excess"
    )

    # move the PRIMARY up — it swaps with the excess and the whole column
    # reseats; the excess lands on the ground
    moved = client.post(
        f"{_layer_base(org, placement, 'primary-gl')}/move",
        data={"direction": "up"},
    )
    assert moved.status_code == 200
    _assert_panel_swap(moved, placement.id)
    rows = {ly["id"]: ly for ly in sync.layer_details(conn, placement.id)}
    assert rows[layer_id]["attach_cents"] == 0, "the swapped-down slab did not reseat"
    assert rows["primary-gl"]["attach_cents"] == 3_000_000_00

    # primary is now the top of gl — another up must refuse, in the page
    refused = client.post(
        f"{_layer_base(org, placement, 'primary-gl')}/move",
        data={"direction": "up"},
    )
    assert refused.status_code == 200
    _assert_panel_swap(refused, placement.id)
    assert "top" in refused.text and "cell-error-msg" in refused.text, (
        "the off-the-end refusal says nothing"
    )


def test_split_premium_mismatch_refuses_through_the_web_and_keeps_typing(
    app_and_org, tmp_path
):
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _two_line_placement(client, org, tmp_path)
    added = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={"name": "Wide", "line": "__all__", "attach_cents": "5,000,000",
              "limit_cents": "10,000,000", "premium_cents": "1,000,000"},
    )
    assert added.status_code == 200
    layer_id = next(
        ly["id"] for ly in sync.layer_details(conn, placement.id)
        if ly["name"] == "Wide"
    )
    path = Path(placement.program_path)
    before = path.read_bytes()

    refused = client.post(
        f"{_layer_base(org, placement, layer_id)}/split",
        data={"move_line": "cy", "new_name": "Cyber Wide",
              "kept_premium": "900,000", "moved_premium": "200,000"},
    )

    assert refused.status_code == 200
    assert "must total" in refused.text
    assert 'value="Cyber Wide"' in refused.text, "the refusal lost the typing"
    assert 'value="900,000"' in refused.text
    assert path.read_bytes() == before


def test_a_cell_save_recovers_selection_from_the_browser_url(app_and_org):
    """The HX-Current-URL seam (review C27): a cell save passes no selected=,
    so the browser URL is the ONLY thing keeping the broker on their layer.
    Deleting the fallback must fail a test, not just real browsers."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    rows = sync.layer_details(conn, placement.id)
    assert len(rows) > 1, "need a second layer to prove selection held"
    chosen = rows[-1]

    saved = client.post(
        _cell(org, placement, chosen, "name"),
        data={"name": "Held Selection"},
        headers={
            "HX-Current-URL": (
                f"http://127.0.0.1/accounts/{org.ref}/program"
                f"?layer={chosen['id']}"
            )
        },
    )

    assert saved.status_code == 200
    assert f'data-layer-row="{chosen["id"]}"' in saved.text, (
        "the save threw the broker off their layer"
    )


def test_collapse_state_survives_selection_and_writes(app_and_org):
    """Collapse lives in the URL and every select link carries it EXPLICITLY,
    empty included — and a link carrying only ?layer= recovers it from the
    browser URL instead of wiping it (review C2/C28)."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    layer = sync.layer_details(conn, placement.id)[0]
    page = client.get(f"/accounts/{org.ref}/program").text
    import re

    slug = re.search(r'closed=([^"&]+)"', page)
    assert slug, "no collapse toggle carries a closed param"
    closed = slug.group(1)

    collapsed = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/worksheet"
        f"?layer={layer['id']}&closed={closed}"
    )
    assert "▸" in collapsed.text, "the group did not collapse"

    # a select carrying only ?layer= (the tower click, a preview Discard)
    # recovers closed from the browser URL
    reselected = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/worksheet?layer={layer['id']}",
        headers={
            "HX-Current-URL": (
                f"http://127.0.0.1/accounts/{org.ref}/program"
                f"?layer={layer['id']}&closed={closed}"
            )
        },
    )
    assert "▸" in reselected.text, "a layer-only select wiped the collapse state"
    # and an EXPLICIT empty closed= still expands everything
    expanded = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/worksheet"
        f"?layer={layer['id']}&closed=",
        headers={
            "HX-Current-URL": (
                f"http://127.0.0.1/accounts/{org.ref}/program"
                f"?layer={layer['id']}&closed={closed}"
            )
        },
    )
    assert "▸" not in expanded.text, "expand-all no longer expands"


# --- a market's own premium ---------------------------------------------------
#
# A shared layer's premium is split by capacity, which is right until it is
# not: a differential, surplus-lines tax and stamping fees on one paper, a
# non-concurrent quote (Grant, 2026-08-24). towerkit owns the rule — stating
# one market's premium states them ALL, each at the figure it was already
# showing, and the layer's premium becomes their sum — and bookkit's job is to
# make that legible before it happens and correct afterwards.


def _shared_seat(conn, org):
    """A (placement, layer, seat index) where more than one market is bound —
    the shape the feature exists for."""
    for placement in _linked(conn, org):
        for layer in sync.layer_details(conn, placement.id):
            if len(layer["participants"]) > 1:
                return placement, layer, 1
    raise AssertionError("the seeded book has no shared layer")


def _premium_cell(org, placement, layer_id, index):
    return _market_cell(org, placement, layer_id, index, "premium_cents")


def test_a_derived_market_premium_is_marked_as_derived(app_and_org):
    """"This is what the market charges" and "this is the layer's premium
    divided by the share" are different claims, and a broker checking a split
    has to be able to tell which one a figure is."""
    client, org = app_and_org
    placement, layer, index = _shared_seat(client.app.state.conn, org)

    cell = client.get(_premium_cell(org, placement, layer["id"], index)).text

    assert "derived" in cell


def test_the_editor_does_not_prefill_a_derived_figure(app_and_org):
    """A derived premium is arithmetic, not an answer. Pre-filling it would
    make opening the cell to READ it a way of accidentally stating every other
    market on the layer — "never pre-fill a figure that comes off a document",
    with teeth."""
    client, org = app_and_org
    placement, layer, index = _shared_seat(client.app.state.conn, org)

    editor = client.get(
        _premium_cell(org, placement, layer["id"], index) + "/edit"
    ).text

    assert 'value=""' in editor


def test_the_first_override_previews_before_it_writes(app_and_org):
    """It moves three numbers and the broker typed one of them."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index = _shared_seat(conn, org)
    before = _file_of(conn, placement).read_bytes()

    posted = client.post(
        _premium_cell(org, placement, layer["id"], index),
        data={"premium_cents": "520000.00"},
    )

    assert posted.status_code == 200
    assert "unsaved edit" in posted.text
    assert _file_of(conn, placement).read_bytes() == before, "the preview wrote"


def test_the_preview_names_every_market_it_is_about_to_state(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index = _shared_seat(conn, org)

    posted = client.post(
        _premium_cell(org, placement, layer["id"], index),
        data={"premium_cents": "520000.00"},
    )

    for seat in layer["participants"]:
        assert seat["carrier"] in posted.text


def test_the_preview_is_one_top_level_element(app_and_org):
    """The response is retargeted onto the worksheet host rather than glued to
    a cell — the parse-context rule, which binds the whole response."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index = _shared_seat(conn, org)

    posted = client.post(
        _premium_cell(org, placement, layer["id"], index),
        data={"premium_cents": "520000.00"},
    )

    assert len(_top_level_tags(posted.text)) == 1
    assert posted.headers["HX-Retarget"] == f"#ws-host-{placement.id}"


def test_committing_states_every_seat_and_sums_the_layer(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index = _shared_seat(conn, org)

    saved = client.post(
        _premium_cell(org, placement, layer["id"], index),
        data={"premium_cents": "520000.00", "commit": "1"},
    )

    assert saved.status_code == 200
    _assert_panel_swap(saved, placement.id)
    fresh = next(
        row for row in sync.layer_details(conn, placement.id) if row["id"] == layer["id"]
    )
    assert all(seat["premium_stated"] for seat in fresh["participants"])
    assert fresh["participants"][index]["premium_cents"] == 52_000_000
    assert fresh["premium_cents"] == sum(
        seat["premium_cents"] for seat in fresh["participants"]
    )


def test_a_second_edit_commits_in_place_without_previewing(app_and_org):
    """Once every seat is stated only the sum moves, so the cell behaves like
    any other cell."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index = _shared_seat(conn, org)
    client.post(
        _premium_cell(org, placement, layer["id"], index),
        data={"premium_cents": "520000.00", "commit": "1"},
    )

    again = client.post(
        _premium_cell(org, placement, layer["id"], index),
        data={"premium_cents": "530000.00"},
    )

    assert "unsaved edit" not in again.text
    _assert_panel_swap(again, placement.id)
    fresh = next(
        row for row in sync.layer_details(conn, placement.id) if row["id"] == layer["id"]
    )
    assert fresh["participants"][index]["premium_cents"] == 53_000_000


def test_blank_clears_the_whole_layer_back_to_a_split(app_and_org):
    """towerkit's all-or-nothing rule, not a web decision."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index = _shared_seat(conn, org)
    client.post(
        _premium_cell(org, placement, layer["id"], index),
        data={"premium_cents": "520000.00", "commit": "1"},
    )

    cleared = client.post(
        _premium_cell(org, placement, layer["id"], index), data={"premium_cents": ""}
    )

    assert cleared.status_code == 200
    fresh = next(
        row for row in sync.layer_details(conn, placement.id) if row["id"] == layer["id"]
    )
    assert not any(seat["premium_stated"] for seat in fresh["participants"])


def test_the_layer_premium_says_it_comes_from_the_markets(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index = _shared_seat(conn, org)

    saved = client.post(
        _premium_cell(org, placement, layer["id"], index),
        data={"premium_cents": "520000.00", "commit": "1"},
    )

    assert "from markets" in saved.text


def test_typing_a_layer_premium_over_a_stated_one_is_refused(app_and_org):
    """It IS the markets' sum. towerkit refuses the write and the refusal
    lands in the cell the broker typed in, with what they typed still there."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index = _shared_seat(conn, org)
    client.post(
        _premium_cell(org, placement, layer["id"], index),
        data={"premium_cents": "520000.00", "commit": "1"},
    )
    before = _file_of(conn, placement).read_bytes()

    refused = client.post(
        _cell(org, placement, layer, "premium_cents"),
        data={"premium_cents": "1.00"},
    )

    assert "comes from its markets" in refused.text
    assert _file_of(conn, placement).read_bytes() == before


def test_the_stated_premium_reaches_the_projection(app_and_org):
    """proj_participant is what exposure, hit rate and the market pages read.
    A stated premium that stopped at the file would leave every one of them
    reporting a number the file disagrees with."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer, index = _shared_seat(conn, org)
    carrier = layer["participants"][index]["carrier"]

    client.post(
        _premium_cell(org, placement, layer["id"], index),
        data={"premium_cents": "520000.00", "commit": "1"},
    )

    row = conn.execute(
        "SELECT premium FROM proj_participant "
        "WHERE placement_id = ? AND layer_id = ? AND carrier = ?",
        (placement.id, layer["id"], carrier),
    ).fetchone()
    assert row["premium"] == 52_000_000


# --- lines of coverage hold layers --------------------------------------------
#
# Grant scaffolded a program, made its one placeholder layer a statutory
# Workers Compensation, and then had nowhere to say "Employers Liability is
# its own line of coverage" — so he typed that into the LAYER name and aimed
# it at the placeholder column, which towerkit refused, correctly. Three
# faults: the hierarchy was never named, the rail grouped by towerkit's
# unused bucket instead of by line, and a line could not be made from the
# form you were in (2026-08-24).


def _rail(html: str) -> str:
    """The rail alone. Asserting against the whole page passes with the rail's
    own control deleted, because the band above carries the same URLs."""
    return html.split('class="structure-index"', 1)[1].split("</nav>", 1)[0]


def _rail_groups(html: str) -> list[str]:
    """The line of coverage each group is headed by. The name is the chip's
    inline cell now — the rail renders the same `_line_chip.html` the band
    does, so renaming and removing a line are possible where it is read —
    and a group with no line (the 'unknown line' pile) still prints static
    text."""
    import re

    rail = _rail(html)
    named = [
        chunk.strip()
        for chunk in re.findall(
            r'line-name.*?<span class="is-editable">(.*?)</span>', rail, re.S
        )
    ]
    return named or re.findall(r'class="index-group-name mono">([^<]*)', rail)


def test_the_rail_groups_by_line_of_coverage_in_column_order(app_and_org):
    """One group per line, in the program's own order — which is COLUMN order
    in the drawing, never alphabetical."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)
    lines = sync.program_lines(conn, placement.id)

    page = client.get(f"/accounts/{org.ref}/program?layer=").text

    assert _rail_groups(page) == [name for _, name in lines]


def test_a_spanning_layer_is_listed_once(app_and_org):
    """Repeating it under every line it covers would make the group counts
    add to more than the tower holds."""
    import re

    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)
    spanning = next(
        (
            row for row in sync.layer_details(conn, placement.id)
            if len(row["applies_to"]) > 1
        ),
        None,
    )
    assert spanning, "the fixture has no spanning layer to test with"

    page = client.get(f"/accounts/{org.ref}/program").text

    rows = re.findall(r'class="index-name">([^<]*)', page)
    assert rows.count(spanning["name"]) == 1
    assert f"spans {len(spanning['applies_to'])} lines" in page


def test_the_rail_counts_sum_to_the_tower(app_and_org):
    import re

    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)

    page = client.get(f"/accounts/{org.ref}/program").text

    counts = [int(n) for n in re.findall(r'class="index-count mono">(\d+)<', page)]
    # the first is the total on the head; the rest are the groups
    assert counts[0] == sum(counts[1:]) == len(sync.layer_details(conn, placement.id))


def test_the_layer_form_offers_a_new_line_of_coverage(app_and_org):
    """The dead end, closed: the picker used to offer only lines that already
    existed."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)

    form = client.get(f"/accounts/{org.ref}/program/{placement.id}/layers/new").text

    assert 'value="__new__"' in form
    assert "new line of coverage" in form


def test_the_rail_can_open_that_form_directly(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)

    page = client.get(f"/accounts/{org.ref}/program").text
    form = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/lines/new-layer"
    ).text

    assert "+ line of coverage" in page
    assert 'value="__new__" selected' in form


def test_a_new_line_and_its_layer_land_together(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)
    before = len(sync.program_lines(conn, placement.id))

    saved = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={
            "name": "EL Primary", "line": "__new__",
            "new_line_name": "Employers Liability",
            "limit_cents": "1,000,000", "premium_cents": "",
        },
    )

    assert saved.status_code == 200
    lines = dict(sync.program_lines(conn, placement.id))
    assert len(lines) == before + 1
    new_id = next(lid for lid, name in lines.items() if name == "Employers Liability")
    layers = [
        row for row in sync.layer_details(conn, placement.id)
        if row["applies_to"] == [new_id]
    ]
    assert [row["name"] for row in layers] == ["EL Primary"]


def test_a_refused_layer_leaves_no_line_behind(app_and_org):
    """THE assertion that matters. The line and its layer are one mutation, so
    a refusal never reaches the dump — two sequential writes would strand a
    line on exactly the refusals this path has to survive."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)
    before = dict(sync.program_lines(conn, placement.id))

    refused = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={
            "name": "EL Primary", "line": "__new__",
            "new_line_name": "Employers Liability",
            # towerkit refuses a non-positive limit; the line must not survive it
            "limit_cents": "0", "premium_cents": "",
        },
    )

    assert refused.status_code == 200
    assert dict(sync.program_lines(conn, placement.id)) == before


def test_a_new_line_with_no_name_is_refused(app_and_org):
    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)
    before = dict(sync.program_lines(conn, placement.id))

    refused = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/layers",
        data={
            "name": "EL Primary", "line": "__new__", "new_line_name": "",
            "limit_cents": "1,000,000", "premium_cents": "",
        },
    )

    assert "name the line of coverage" in refused.text
    assert dict(sync.program_lines(conn, placement.id)) == before


def test_a_statutory_line_is_labelled_in_the_picker(app_and_org):
    """Statutory cover owns its whole column, so towerkit refuses any other
    layer on it — the exact refusal that started this. The picker says so
    before the broker aims there; towerkit still gives the refusal."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)
    marked = client.post(
        f"{_layer_base(org, placement, layer['id'])}/statutory",
        data={"statutory": "true"},
    )
    assert marked.status_code == 200
    assert next(
        row for row in sync.layer_details(conn, placement.id)
        if row["id"] == layer["id"]
    )["statutory"], "the fixture layer did not take the flag"

    form = client.get(f"/accounts/{org.ref}/program/{placement.id}/layers/new").text

    assert "statutory, whole column" in form


# --- the rail is where the structure is worked --------------------------------
#
# Testing on 2026-08-24 Grant hit two things the rail could not do. The
# "same policy as" picker offered two different layers under one label — both
# new lines of coverage arrive with a layer called "To be placed" — which read
# as a list that had not refreshed. And column order could only be changed from
# the chips in the band above, while the structure itself is read in the rail.


def _two_pending_lines(client, org, placement):
    """Two lines of coverage whose layers are BOTH called 'To be placed' —
    the shape that makes a name-only picker ambiguous."""
    base = f"/accounts/{org.ref}/program/{placement.id}"
    for name in ("Workers Compensation", "Employers Liability"):
        added = client.post(f"{base}/lines", data={"name": name})
        assert added.status_code == 200
    return base


def test_the_policy_picker_can_tell_two_layers_of_one_name_apart(app_and_org):
    """The write was always addressed by id, so nothing was ever linked
    wrongly — but a control that asks a question with two identical answers
    is a control nobody can use."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)
    base = _two_pending_lines(client, org, placement)
    pending = [
        row for row in sync.layer_details(conn, placement.id)
        if row["name"] == "To be placed"
    ]
    assert len(pending) == 2, "the fixture did not produce the ambiguous shape"

    page = client.get(f"{base.rsplit('/', 2)[0]}/program?layer={pending[0]['id']}").text

    # the OTHER pending layer is offered, qualified by its line of coverage
    assert "To be placed (Employers Liability)" in page


def test_a_distinct_name_is_not_qualified(app_and_org):
    """Only the ambiguous ones. Qualifying every option would make the common
    case noisier for a problem it does not have."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, layer = _first_layer(conn, org)

    page = client.get(f"/accounts/{org.ref}/program?layer={layer['id']}").text

    assert "Primary GL (" not in page


def test_the_rail_can_reorder_lines_of_coverage(app_and_org):
    """Column order in the drawing, edited where the structure is read."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)
    before = [name for _, name in sync.program_lines(conn, placement.id)]
    line_id = next(lid for lid, _ in sync.program_lines(conn, placement.id))

    page = client.get(f"/accounts/{org.ref}/program").text
    # In the RAIL, not merely somewhere on the page: the chips in the band
    # above have always carried this URL, so asserting the URL alone would
    # pass with the rail's own control deleted.
    rail = _rail(page)
    assert f"/lines/{line_id}/move" in rail, "the rail offers no move control"

    moved = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/lines/{line_id}/move",
        data={"delta": "1"},
    )

    assert moved.status_code == 200
    after = [name for _, name in sync.program_lines(conn, placement.id)]
    assert after == [before[1], before[0], *before[2:]]
    _assert_panel_swap(moved, placement.id)


def test_the_ends_are_disabled_not_hidden(app_and_org):
    """A control that vanishes makes the reader wonder where it went."""
    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)

    page = client.get(f"/accounts/{org.ref}/program").text

    assert page.count("chip-arrow") >= 2
    assert "disabled" in page


def test_a_program_with_lines_and_no_layers_still_says_what_is_wrong(app_and_org):
    """A GAP BETWEEN THE PLAN AND THE CODE (surface sweep, 2026-08-24).

    `_index_groups` returned None when a program had no layers, and the
    workbench gate in `_layers_panel.html` also required a worksheet — so a
    linked file with lines and nothing on them rendered neither the
    diagnostics block nor either terms strip, while towerkit reported one
    `line-empty` ERROR per line. The one file the app knows is broken was the
    one it said nothing about.

    The plan for the rail said the opposite in as many words: "a line with no
    layers still gets its group, with a count of zero: a rail that hid the
    line would hide the thing the diagnostics point at."

    A broker cannot reach this state from the app — removing the last layer of
    a line is refused by `line-empty` — so it takes a hand edit, towerkit's
    editor or MCP. It is exactly the state somebody arrives at the web to
    understand.
    """
    from towerkit.model import dump_program, load_program
    from towerkit.validate import validate_program

    client, org = app_and_org
    conn = client.app.state.conn
    placement = _linked(conn, org)[0]
    path = _file_of(conn, placement)

    program = load_program(path)
    program.layers = []
    dump_program(program, path)
    codes = {d.code for d in validate_program(load_program(path)).errors}
    assert "line-empty" in codes, "the fixture no longer produces the broken state"

    page = client.get(f"/accounts/{org.ref}/program").text
    section = page[page.index(f'id="program-{placement.id}"') :]

    assert "program-diagnostics" in section, (
        "the program says nothing about the errors towerkit reports on it"
    )
    assert "no layers cover" in section, "the line-empty errors are not printed"
    assert "structure-index" in section, "the rail is gone, so are its lines"
    for line in load_program(path).lines:
        assert line.name in section, f"{line.name} is missing from the rail"
    assert "+ retention" in section and "+ sublimit" in section, (
        "the terms strips went with the workbench — the retentions and "
        "sublimits are still on the file"
    )
    assert "Add the first layer" in section


def test_the_rail_can_rename_relabel_and_remove_a_line(app_and_org):
    """THE AFFORDANCES MOVED HOUSE WITH THE STRUCTURE (surface sweep,
    2026-08-24). The rail is where a broker works the tower, and it carried
    only the two move arrows: rename, column label and remove stayed on the
    chips in the band above, exactly the shape of the bug Grant reported about
    reordering. The routes existed the whole time.

    Asserted inside the RAIL, because the band above carries the same URLs and
    a page-wide scan would pass with every one of these deleted.
    """
    client, org = app_and_org
    conn = client.app.state.conn
    placement, _ = _first_layer(conn, org)
    line_id, name = sync.program_lines(conn, placement.id)[0]

    rail = _rail(client.get(f"/accounts/{org.ref}/program").text)

    assert f"/lines/{line_id}/cell/name/edit" in rail, "the rail cannot rename a line"
    assert f"/field/line/{line_id}:_/abbr/edit" in rail, (
        "the rail cannot set the column label"
    )
    assert f"/lines/{line_id}/remove" in rail, "the rail cannot remove a line"

    # and the removal actually works from here, confirm and all
    confirm = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/lines/{line_id}/remove"
    )
    assert confirm.status_code == 200
    assert f"remove {name}?" in confirm.text
    removed = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/lines/{line_id}/remove"
    )
    assert removed.status_code == 200
    assert line_id not in [lid for lid, _ in sync.program_lines(conn, placement.id)]
