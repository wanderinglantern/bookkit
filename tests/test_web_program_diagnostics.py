"""The Program tab prints what towerkit says about the file.

Grant, 2026-08-21, with a screenshot of a garbled tower: "trying to diagnose
what is going on here". Two D&O excess layers at the SAME attachment — a quota
share modelled as two layers instead of one layer with two participants — drawn
on top of each other with their labels overprinting.

towerkit had been reporting it the whole time:

    error  line-overlap  do: OVERLAP D&O Excess (Starr)->D&O Excess (Endurance)
                              at $10,000,000 vs $5,000,000

Diagnostics reached the browser ONLY when a WRITE was refused. A file that
already held a problem — written by towerkit's own editor, by MCP, or by an
import — drew a wrong picture and the page said nothing. The app knew and would
not say. Same class as the dead chevron, one level up.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit import sync
from bookkit.web.app import create_app


@pytest.fixture
def app_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs, placements

    conn = app.state.conn
    org = next(
        o
        for o in orgs.list_orgs(conn, kind="client")
        if [p for p in placements.for_org(conn, o.id) if p.program_path]
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org


def _linked(conn, org):
    from bookkit.repo import placements

    return next(p for p in placements.for_org(conn, org.id) if p.program_path)


def _break_it(conn, placement) -> None:
    """Reproduce Grant's file: duplicate the top layer at the SAME attach, the
    way a quota share gets modelled when nothing teaches you that carriers
    sharing a slab are PARTICIPANTS on one layer."""
    path = sync.program_file(conn, placement)
    data = json.loads(path.read_text())
    top = max(data["layers"], key=lambda ly: ly["attach"])
    twin = dict(top)
    twin["id"] = top["id"] + "-twin"
    twin["name"] = top["name"] + " (second carrier)"
    # THE TWIN DOES NOT FOLLOW UNDERLYING, and it has to be said out loud
    # because the seeded top layer now does (it spans GL and AL, and a
    # spanning slab carries no pinned attachment). towerkit's overlap check
    # EXEMPTS a follows layer as the upper of the pair — `_check_line_stack`,
    # `and not above.follows_underlying` — because such a layer's `attach` is
    # the maximum across its columns and comparing it naively would report
    # overlaps that are not there.
    #
    # THAT EXEMPTION IS TOO BROAD AND IS A REAL HOLE: two IDENTICAL
    # follows-underlying layers on one line validate clean and draw on top of
    # each other, which is precisely the failure `line-overlap` exists to
    # catch, in its follows costume (measured 2026-08-27; reported to Grant,
    # not fixed here — the fix is in `Program.underlying_tops`, which cannot
    # see follows layers at all, and that reaches every drawing).
    #
    # This fixture is about the PAGE saying what is wrong, so it makes an
    # overlap towerkit actually reports rather than riding on that hole.
    twin["followsUnderlying"] = False
    data["layers"].append(twin)
    path.write_text(json.dumps(data, indent=2))
    sync.project(conn, path, placement_id=placement.id)


class TestItSaysWhatIsWrong:
    def test_an_overlap_already_in_the_file_is_printed_on_the_page(
        self, app_and_org
    ) -> None:
        """THE REGRESSION. Nothing was written by the browser here — the file
        arrived broken, which is the case that produced no message at all."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement = _linked(conn, org)
        _break_it(conn, placement)

        page = client.get(f"/accounts/{org.ref}/program").text

        assert "OVERLAP" in page, "the page is silent about a broken tower"
        assert "program-diagnostics" in page

    def test_it_prints_towerkits_own_sentence(self, app_and_org) -> None:
        """This surface composes NO wording of its own: the words a broker
        reads here are the words the validator, the CLI and the MCP server
        use, so a rule cannot come to be described two ways."""
        from towerkit.validate import validate_program

        client, org = app_and_org
        conn = client.app.state.conn
        placement = _linked(conn, org)
        _break_it(conn, placement)
        expected = validate_program(
            sync.linked_program(conn, placement.id).program
        ).errors[0].message

        page = client.get(f"/accounts/{org.ref}/program").text

        assert expected.replace("&", "&amp;") in page or expected in page

    def test_it_names_the_layer_the_reader_has_to_look_at(
        self, app_and_org
    ) -> None:
        client, org = app_and_org
        conn = client.app.state.conn
        placement = _linked(conn, org)
        _break_it(conn, placement)

        page = client.get(f"/accounts/{org.ref}/program").text
        strip = page[page.index("program-diagnostics") :]
        strip = strip[: strip.index("</div>")]

        assert "line" in strip or "layer" in strip

    def test_errors_come_before_warnings(self, app_and_org) -> None:
        """An error says the tower is WRONG; a warning says it is incomplete,
        and the reader is hunting the first."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement = _linked(conn, org)
        _break_it(conn, placement)

        page = client.get(f"/accounts/{org.ref}/program").text
        strip = page[page.index("program-diagnostics") :]
        strip = strip[: strip.index("</div>")]
        if "diagnostic-warning" in strip and "diagnostic-error" in strip:
            assert strip.index("diagnostic-error") < strip.index("diagnostic-warning")


class TestItStaysQuietWhenThereIsNothingToSay:
    def test_a_clean_program_yields_nothing_to_print(self) -> None:
        """Reserve the signal for something real: a strip that is always there
        is chrome, and chrome is what a reader learns to skip.

        Asserted against the READER rather than a fixture, because the seeded
        programs carry warnings of their own and a test that skipped when they
        did asserted nothing at all (its first cut did exactly that).
        """
        from towerkit.model import Layer, Line, Participant, Period, Placement, Program

        from bookkit.web.routes.program import _diagnostics

        clean = Program(
            insured="Test Co", program="Casualty", placement=Placement.BOUND,
            period=Period(start="2026-01-01", end="2027-01-01"),
            lines=[Line(id="gl", name="General Liability")],
            layers=[
                Layer(
                    id="primary", name="Primary", applies_to=["gl"], attach=0,
                    limit=2_000_000,
                    participants=[Participant(carrier="Zurich", share_bps=10_000)],
                )
            ],
            retentions=[
                {"applies_to": ["gl"], "type": "deductible", "amount": 250_000}
            ],
        )
        linked = sync.LinkedProgram(path=None, program=clean)

        assert _diagnostics(linked) == []

    def test_a_program_with_something_to_say_yields_it(self) -> None:
        """The other half — the reader is not simply always empty."""
        from towerkit.model import Layer, Line, Participant, Period, Placement, Program

        from bookkit.web.routes.program import _diagnostics

        broken = Program(
            insured="Test Co", program="Casualty", placement=Placement.BOUND,
            period=Period(start="2026-01-01", end="2027-01-01"),
            lines=[Line(id="gl", name="General Liability")],
            layers=[
                Layer(id="a", name="A", applies_to=["gl"], attach=0, limit=2_000_000,
                      participants=[Participant(carrier="Zurich", share_bps=10_000)]),
                Layer(id="b", name="B", applies_to=["gl"], attach=0, limit=2_000_000,
                      participants=[Participant(carrier="Chubb", share_bps=10_000)]),
            ],
            retentions=[],
        )

        found = _diagnostics(sync.LinkedProgram(path=None, program=broken))

        assert any(d["code"] == "line-overlap" for d in found)
        assert all(d["message"] for d in found)

    def test_a_file_that_will_not_LOAD_is_not_reported_twice(
        self, app_and_org
    ) -> None:
        """`load_error` already prints that sentence. A file that cannot parse
        has no diagnostics to give, and the same failure under two headings
        reads as two problems."""
        client, org = app_and_org
        conn = client.app.state.conn
        placement = _linked(conn, org)
        sync.program_file(conn, placement).write_text("{ not json")

        page = client.get(f"/accounts/{org.ref}/program").text

        assert "program-diagnostics" not in page


class TestTheFixItPointsAt:
    def test_the_same_cover_as_ONE_layer_with_two_participants_is_clean(
        self, app_and_org
    ) -> None:
        """The answer the strip is meant to lead to: carriers sharing a slab
        are PARTICIPANTS on one layer, not two layers at the same attachment.
        Asserted here so the guidance the UI gives is guidance that works."""
        from towerkit.validate import validate_program

        client, org = app_and_org
        conn = client.app.state.conn
        placement = _linked(conn, org)
        path = sync.program_file(conn, placement)

        data = json.loads(path.read_text())
        top = max(data["layers"], key=lambda ly: ly["attach"])
        top["participants"] = [
            {"carrier": "Starr", "share": 0.5},
            {"carrier": "Endurance", "share": 0.5},
        ]
        path.write_text(json.dumps(data, indent=2))
        sync.project(conn, path, placement_id=placement.id)

        program = sync.linked_program(conn, placement.id).program
        overlaps = [
            d for d in validate_program(program).errors if d.code == "line-overlap"
        ]
        assert not overlaps
