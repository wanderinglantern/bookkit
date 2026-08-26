"""The marketing grid on the Program tab — THE PANEL IS THE REPORT.

The first half holds that the grid RENDERS the same report the workbook
downloads, on a placement with a program file and on one without, and that the
three things a marketing grid can silently lie about cannot happen here: a
count printed as money, an empty line printed as an empty table, and a rate
movement printed as a blank where the composer had a sentence to say instead.

The second half holds that it is EDITABLE where it prints, and that the four
ways that could go wrong do not: a cell that writes when nothing was typed, a
status select the browser answers for you, a clearance conflict that refuses a
legitimate entry, and an underwriter's private opinion reaching a client.
"""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.models import MARKET_RESPONSE_STATUSES
from bookkit.money import format_cents
from bookkit.web.app import create_app

GL = "general-liability"
AUTO = "auto"


@pytest.fixture
def client_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs, placements

    conn = app.state.conn
    org = next(
        o
        for o in orgs.list_orgs(conn, kind="client")
        if any(p.program_path for p in placements.for_org(conn, o.id))
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org


def _linked(client, org):
    from bookkit.repo import placements

    return next(
        p for p in placements.for_org(client.app.state.conn, org.id) if p.program_path
    )


def _market(conn, name: str, best: str | None = None):
    """The market this book knows by that name, created only if it does not
    already know one.

    NOT an unconditional create. The seeded book already carries Travelers,
    Berkley, Amwins and friends, so a second org with the same name left two
    rows a name-lookup could land on — and the routes resolve a typed carrier
    by NAME, so the test would then assert against the org it made while the
    app wrote against the one the seed made. That is the same ambiguity
    repo/team.py's duplicate guard exists to stop, arriving through a
    fixture."""
    from bookkit.repo import orgs

    org = orgs.find_by_name(conn, name)
    if org is None or org.kind != "market":
        org = orgs.create(conn, kind="market", name=name, status="active")
    if best:
        conn.execute(
            "INSERT OR REPLACE INTO market_profile"
            " (org_id, am_best_rating, market_type) VALUES (?, ?, 'carrier')",
            (org.id, best),
        )
    return org


def _approach(conn, placement_id: str, market, line_id: str = GL, **fields):
    from bookkit.repo import marketing, submissions

    sub = submissions.create(
        conn, market_org_id=market.id, sent_on="2026-08-01", placement_id=placement_id
    )
    return marketing.create_response(
        conn, sub.id, line_id, market_org_id=market.id, **fields
    )


def _tab(client, org) -> str:
    got = client.get(f"/accounts/{org.ref}/program")
    assert got.status_code == 200
    return got.text


def test_the_grid_renders_beneath_the_workbench_on_a_linked_placement(client_and_org):
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    marketing.set_placement_line(
        conn, placement.id, GL,
        expiring_premium=41_200_000, expiring_exposure=4_100_000_000,
        expiring_rate_micros=10_048_780, expiring_basis="gross_sales",
        expected_exposure=4_850_000_000, rating_basis="gross_sales", rate_per=1000,
        limit_sought=100_000_000,
    )
    _approach(
        conn, placement.id, _market(conn, "Travelers", "A++ XV"),
        status="quoted", responded_on="2026-08-12", rate_micros=8_100_000,
        premium=39_285_000, tria_premium=785_000, policy_fees=390_000,
        surplus_lines_tax=0,
    )

    html = _tab(client, org)

    assert f'id="marketing-{placement.id}"' in html
    assert "General Liability" in html
    assert "Travelers" in html
    # The block header carries the basis, the exposure and what expired.
    assert "Gross sales" in html
    assert "$48,500,000" in html, "the expected exposure is not on the header"
    assert "$412,000" in html, "the expiring premium is not on the header"
    # The premium and every component of the total are cells of their own,
    # because you cannot type a total (Grant, 2026-08-25).
    assert "$392,850" in html
    assert "$7,850" in html, "TRIA is not its own cell"
    assert "$3,900" in html, "fees are not their own cell"
    assert "$404,600" in html, "the total is not printed"
    # And the section is BELOW the workbench, not above it.
    #
    # `id="marketing-…"` and not the bare id: the program band above the
    # workbench carries an ANCHOR to this section (its Submission control, which
    # stopped writing a line-less submission on 2026-08-26), so the bare string
    # now appears earlier on the page and would find the link rather than the
    # section it points at.
    assert (
        html.index("program-workbench") < html.index(f'id="marketing-{placement.id}"')
    )


def test_it_renders_on_a_placement_with_no_program_file(client_and_org):
    """Marketing happens BEFORE a tower exists. Gating this on `program_path`
    would put it out of reach on exactly the placements it is for."""
    client, org = client_and_org
    conn = client.app.state.conn
    from bookkit.repo import placements as placements_repo

    bare = placements_repo.create(
        conn, org.id, "Casualty renewal", "2026-11-01", "2027-11-01"
    )
    _approach(
        conn, bare.id, _market(conn, "Chubb", "A++ XV"),
        status="indicated", responded_on="2026-08-11", premium=12_500_000,
    )

    html = _tab(client, org)

    assert "This placement has no program file." in html, "the fixture is wrong"
    assert f'id="marketing-{bare.id}"' in html
    assert "Chubb" in html
    assert "Indicated" in html


def test_a_count_basis_exposure_is_a_count_and_never_money(client_and_org):
    """350 power units is not $3.50 — the decision belongs to
    RatingBasis.monetary and is read, never re-judged here."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    marketing.set_placement_line(
        conn, placement.id, AUTO,
        expected_exposure=350, rating_basis="power_units", rate_per=1,
    )

    html = _tab(client, org)

    assert "350 power units" in html
    assert "$3.50" not in html


def test_a_line_with_no_markets_says_so_in_words(client_and_org):
    """An empty table cannot be told from a rendering bug."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    marketing.set_placement_line(conn, placement.id, GL, expected_exposure=1_000_000)

    html = _tab(client, org)

    assert "No markets approached on this line yet." in html


def test_a_header_figure_nobody_recorded_reads_not_set(client_and_org):
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    marketing.set_placement_line(conn, placement.id, GL, expected_exposure=1_000_000)

    html = _tab(client, org)

    assert "not set" in html, "a blank header figure reads as a rendering fault"


def test_every_status_renders_as_its_own_pill(client_and_org):
    """One pill per status in models.MARKET_RESPONSE_STATUSES, printing the
    composer's own label — the vocabulary has ONE home."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.services import marketing_report

    for status in MARKET_RESPONSE_STATUSES:
        _approach(
            conn, placement.id, _market(conn, f"Market {status}"), status=status
        )

    html = _tab(client, org)

    report = marketing_report.compose(
        conn, placement.id, __import__("datetime").date(2026, 8, 14)
    )
    labels = {row.status for block in report.blocks for row in block.rows}
    assert len(labels) == len(MARKET_RESPONSE_STATUSES)
    for label in labels:
        assert f">{label}</span>" in html, f"{label} did not render as a pill"
    # The pill is the EDITABLE cell's own value span now (status is corrected
    # where it prints), so what is counted is the pill-carrying cell — one per
    # response, tinted by the cell's tone class and still printing its word.
    #
    # PLUS ONE PER PROVISIONAL ROW, ON EVERY PLACEMENT THE TAB RENDERS. The
    # seeded book carries submissions with no response rows at all, and those
    # render in their own block with the PACKAGE's status pill
    # (models.SUBMISSION_STATUS_LABELS is a different vocabulary from this one).
    # Counted over every placement on the account because the Program tab
    # renders a marketing section for each — the same reason `_marketing_section`
    # in the gates file scopes its walk — and off the composer rather than
    # hard-coded, so the number cannot go stale with the seed.
    from bookkit.repo import placements as placements_repo

    provisional = sum(
        len(
            marketing_report.compose(
                conn, other.id, __import__("datetime").date(2026, 8, 14)
            ).provisional
        )
        for other in placements_repo.for_org(conn, org.id)
    )
    assert html.count("pill-cell") == len(MARKET_RESPONSE_STATUSES) + provisional


def test_a_rate_movement_with_no_number_prints_the_reason(client_and_org):
    """NEVER a bare blank: the reader cannot tell a missing comparison from a
    figure that failed to render."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    marketing.set_placement_line(
        conn, placement.id, GL, expected_exposure=4_850_000_000,
        rating_basis="gross_sales", rate_per=1000,
    )
    _approach(
        conn, placement.id, _market(conn, "Zurich"),
        status="quoted", responded_on="2026-08-10", rate_micros=8_100_000,
        premium=39_285_000,
    )

    html = _tab(client, org)

    assert "no expiring rate recorded" in html


def test_a_total_is_blank_while_any_component_is_unknown(client_and_org):
    """NULL is not zero. A total that treats an unquoted surplus lines tax as
    zero recommends the wrong placement."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    _approach(
        conn, placement.id, _market(conn, "Liberty Mutual"),
        status="quoted", responded_on="2026-08-09", premium=10_000_000,
        tria_premium=200_000,
    )

    html = _tab(client, org)

    assert "$100,000" in html, "the quoted premium is not printed"
    assert "$102,000" not in html, "a total was printed from an unknown tax"


# --- editing it where it prints --------------------------------------------


def _cell_url(org, placement, response, key: str) -> str:
    return (
        f"/accounts/{org.ref}/program/{placement.id}"
        f"/marketing/responses/{response.id}/cell/{key}"
    )


def _events(conn, response_id: str) -> int:
    from bookkit.repo import events

    return len(events.history(conn, "market_response", response_id, limit=500))


def test_a_cell_commits_and_an_unchanged_value_writes_nothing(client_and_org):
    """BLUR COMMITS, and an UNCHANGED value writes NOTHING.

    The blur half is inline-cell.js's; the half a route can be held to is the
    second one, and it is the one with teeth — opening a cell to READ it must
    not cost an event-log row and an undo batch per glance, and the guard has
    to hold even when the JS one is bypassed (a slow click, a keyboard user, a
    replayed POST). What makes it hold on both sides is that the editor
    pre-fills exactly what the parser accepts back: if `initial_text` and
    `parse_value` disagreed about a premium, every re-save would look like a
    change to `base.update` and the JS comparison against `data-opened-with`
    would fail too.
    """
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    response = _approach(conn, placement.id, _market(conn, "Travelers"), status="quoted")
    # TRIA rather than the premium: TRIA is one of the four cells that feed the
    # derived Total and nothing else, so it is the cell the ROW-shaped answer is
    # for. The premium moves the block's bridge as well and answers one level up
    # (`_BLOCK_CELLS`), which is a different rule, tested on its own below.
    url = _cell_url(org, placement, response, "tria_premium")

    saved = client.post(url, data={"tria_premium": "7,850.00"})
    assert saved.status_code == 200
    # ONE RESPONSE, ONE TOP-LEVEL ELEMENT: the row, and it says where it goes.
    assert saved.text.lstrip().startswith("<tr")
    assert saved.headers["HX-Retarget"] == f"#mrow-{response.id}"
    assert saved.headers["HX-Reswap"] == "outerHTML"
    assert marketing.get_response(conn, response.id).tria_premium == 785_000
    # the caret goes back to the cell the swap replaced
    assert 'data-refocus="cell:tria_premium"' in saved.text

    before = _events(conn, response.id)
    editor = client.get(url + "/edit")
    assert editor.status_code == 200
    prefilled = re.search(r'name="tria_premium" value="([^"]*)"', editor.text)
    assert prefilled, "the editor did not pre-fill the stored TRIA"

    client.post(url, data={"tria_premium": prefilled.group(1)})

    assert _events(conn, response.id) == before, (
        "re-saving the value the editor itself pre-filled wrote to the event log"
    )


def test_an_expiry_typed_on_the_panel_reaches_the_chase_queue(client_and_org):
    """THE MONEY-LOSING HALF OF THE SECOND-HOME DEFECT (Grant, 2026-08-26).

    `services.quotes` is the queue whose own module header calls this gap "the
    only one that loses money rather than time", and it is keyed on
    `submission.quote_expires_on`. A quote recorded on this panel — premium,
    terms and all — could not reach it, because the panel had no expiry cell
    at all and the submission's column was written by one form on another tab.

    This drives the whole chain in one act: the cell writes the RESPONSE, the
    response rolls up onto the submission, and the submission is what the
    book-wide queue reads.
    """
    from datetime import date

    from bookkit.repo import submissions
    from bookkit.services import quotes

    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    response = _approach(
        conn, placement.id, _market(conn, "Travelers"),
        status="quoted", premium=1_400_000_00,
    )
    today = date(2026, 8, 14)
    queued = {q.submission.id for q in quotes.expiring(conn, today=today)}
    assert response.submission_id not in queued, (
        "the fixture is wrong: this quote has no expiry yet and cannot be on a "
        "clock"
    )

    assert ">Expires<" in _tab(client, org)
    saved = client.post(
        _cell_url(org, placement, response, "quote_expires_on"),
        data={"quote_expires_on": "2026-09-04"},
    )
    assert saved.status_code == 200

    rolled = submissions.get(conn, response.submission_id)
    assert rolled.quote_expires_on == "2026-09-04"
    assert rolled.quoted_premium == 1_400_000_00

    chased = {q.submission.id for q in quotes.expiring(conn, today=today)}
    assert response.submission_id in chased


def test_an_expiry_before_the_reply_is_refused_where_it_was_typed(client_and_org):
    """A REFUSAL SAYS SOMETHING, and keeps what was typed. The year is the
    failure — 2025 for 2026 is one keystroke, and it files a live quote in the
    expired bucket where it reads as terms already lost."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    response = _approach(
        conn, placement.id, _market(conn, "Berkley"),
        status="quoted", responded_on="2026-08-10",
    )
    refused = client.post(
        _cell_url(org, placement, response, "quote_expires_on"),
        data={"quote_expires_on": "2025-09-04"},
    )
    assert refused.status_code == 200
    assert "cannot lapse before the market quoted them" in refused.text
    assert "2025-09-04" in refused.text, "the refusal threw away what was typed"
    from bookkit.repo import marketing

    assert marketing.get_response(conn, response.id).quote_expires_on is None


def test_a_status_cell_offers_nothing_chosen_first(client_and_org):
    """A select with no empty option pre-selects its first, and `required` is
    then satisfied by a value nobody chose — which on THIS field filed an
    untouched market response as 'quoted'."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    response = _approach(conn, placement.id, _market(conn, "Zurich"), status="quoted")

    editor = client.get(_cell_url(org, placement, response, "status") + "/edit").text

    options = re.findall(r"<option\b[^>]*>", editor)
    assert options, "the status cell rendered no options at all"
    assert options[0] == '<option value="">', options[:3]
    # and it still offers every real status
    assert len(options) == len(MARKET_RESPONSE_STATUSES) + 1


def test_a_rate_cell_is_not_read_through_the_money_parser(client_and_org):
    """1.42 is 1.42 per unit of exposure. Through the money parser it would be
    142 cents, and the grid would print a rate a millionfold out."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    # A LINE WITH A DENOMINATOR, because a rate typed against a line that has
    # none is refused now — 1.42 per $100 and 1.42 per $1,000 differ by a
    # factor of ten and nothing in the figure says which (D4, 2026-08-26).
    marketing.set_placement_line(conn, placement.id, GL, rate_per=100)
    response = _approach(conn, placement.id, _market(conn, "AIG"), status="quoted")

    client.post(_cell_url(org, placement, response, "rate_micros"), data={"rate_micros": "8.10"})

    assert marketing.get_response(conn, response.id).rate_micros == 8_100_000


def test_no_charges_writes_three_zeros_as_one_undo_unit(client_and_org):
    """NULL is 'nobody has told us'; 0 is 'we asked, there is none'. Only 0
    reaches a Total, so without this the Total column stays blank forever on
    admitted domestic business — or a broker types three zeros."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import marketing

    response = _approach(
        conn, placement.id, _market(conn, "Hartford"),
        status="quoted", premium=10_000_000,
    )
    assert response.total_cost is None, "the fixture already has a total"

    before = batches_repo.most_recent(conn)
    got = client.post(
        f"/accounts/{org.ref}/program/{placement.id}"
        f"/marketing/responses/{response.id}/no-charges"
    )
    assert got.status_code == 200

    fresh = marketing.get_response(conn, response.id)
    assert (fresh.tria_premium, fresh.policy_fees, fresh.surplus_lines_tax) == (0, 0, 0)
    assert fresh.total_cost == 10_000_000, "zeroes did not make the total possible"

    after = batches_repo.most_recent(conn)
    assert after is not None and (before is None or after.id != before.id)
    events = [
        e.field
        for e in __import__(
            "bookkit.repo.events", fromlist=["events"]
        ).history(conn, "market_response", response.id)
        if e.batch_id == after.id
    ]
    assert set(events) == {"tria_premium", "policy_fees", "surplus_lines_tax"}, (
        "the three zeroes are not one undo unit"
    )


def test_a_market_can_be_added_through_a_wholesaler_alone(client_and_org):
    """A submission out to a wholesaler whose carrier is not yet known is a
    REAL row. 'RT Specialty — carrier TBD' is the truth, not a gap."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    _market(conn, "RT Specialty")
    got = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/marketing/lines/{GL}/approaches",
        data={
            "market": "", "via": "RT Specialty", "attach": "", "lim": "10m",
            "sent_on": "2026-08-10", "status": "pending",
        },
    )
    assert got.status_code == 200

    rows = [
        r for r in marketing.responses_for_placement(conn, placement.id)
        if r.line_id == GL
    ]
    assert len(rows) == 1
    assert rows[0].market_org_id is None
    assert rows[0].via_org_id is not None
    assert rows[0].lim == 1_000_000_000

    html = _tab(client, org)
    # TWO COLUMNS NOW, one fact each: the Market cell says the paper is not
    # named yet and the Access cell says who we went through. The workbook
    # still collapses them into "RT Specialty — carrier TBD"
    # (`ReportRow.market_cell`) because a client only reads it.
    assert "carrier TBD" in html
    assert "RT Specialty" in html


def test_the_add_row_offers_nothing_chosen_first(client_and_org):
    """The add row's own status select, held to the same rule as the cell's."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    marketing.set_placement_line(conn, placement.id, GL, expected_exposure=1_000_000)

    html = _tab(client, org)

    # `marketing-add-row`, not `market-add-row`: the participation table in the
    # workbench above carries the latter, and slicing from it would measure the
    # wrong row entirely.
    add = html[html.index("marketing-add-row") :]
    add = add[: add.index("</tr>")]
    # The SELECT's own options — the carrier inputs carry <datalist> options of
    # their own, and a scan over the whole row would read one of those first
    # and pass while the select was still pre-selecting row one.
    select = add[add.index("<select") : add.index("</select>")]
    options = re.findall(r"<option\b[^>]*>", select)
    assert options[0] == '<option value="">', options[:3]
    # ...and the visible default is one a reader can SEE and change, not one
    # the browser picked.
    assert 'value="pending" selected' in add


def test_an_approach_joins_the_live_submission_rather_than_opening_a_second(
    client_and_org,
):
    """THE SUBMISSION IS THE PACKAGE. One email to a market carrying two lines
    is one submission, or 'who did we approach' stops being answerable — and
    the web must not answer that question differently from MCP."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import submissions

    _market(conn, "Chubb")
    base = f"/accounts/{org.ref}/program/{placement.id}/marketing/lines"
    for line in (GL, AUTO):
        got = client.post(
            f"{base}/{line}/approaches",
            data={"market": "Chubb", "via": "", "attach": "", "lim": "",
                  "sent_on": "2026-08-10", "status": "pending"},
        )
        assert got.status_code == 200

    packages = [
        s for s in submissions.for_placement(conn, placement.id)
        if s.market_org_id
    ]
    chubb = [s for s in packages if s.market_org_id]
    assert len([s for s in chubb if s.sent_on == "2026-08-10"]) == 1, (
        "two lines to one market opened two submissions"
    )


def test_a_clearance_conflict_warns_and_the_row_is_still_written(client_and_org):
    """WARNED, NEVER REFUSED — the `line-gap` rule on a different fact. The
    double approach is sometimes deliberate, and a hard block would make a
    legitimate entry impossible."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    carrier = _market(conn, "Berkley")
    wholesaler = _market(conn, "Amwins")
    _approach(conn, placement.id, carrier, status="quoted")

    got = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/marketing/lines/{GL}/approaches",
        data={"market": "Berkley", "via": "Amwins", "attach": "", "lim": "",
              "sent_on": "2026-08-10", "status": "pending"},
    )
    assert got.status_code == 200
    assert "cell-error-msg" not in got.text, got.text[:2000]

    rows = [
        r for r in marketing.responses_for_placement(conn, placement.id)
        if r.line_id == GL and r.market_org_id == carrier.id
    ]
    assert len(rows) == 2, "the second approach was refused rather than warned"
    assert any(r.via_org_id == wholesaler.id for r in rows)

    html = _tab(client, org)
    assert "clearance" in html
    assert "Berkley" in html and "Amwins" in html


def test_the_public_decline_reason_reaches_the_client_and_the_internal_one_never_does(
    client_and_org,
):
    """Two fields, never one with a 'safe to share' tick.

    Both are written through the grid's own cells, because the cells are the
    only entry path on this surface — so this is the round trip that matters:
    what a broker picks from the constrained list is what the client's workbook
    prints, and what they type in the free-text one goes nowhere near it.

    The panel is asserted on too, and specifically that the two cells address
    DIFFERENT columns. A grid whose two reason cells both pointed at
    `decline_reason_public` would look right, save without complaint, and make
    the private note unreachable — which is how a broker ends up typing the
    off-the-record sentence into the field that IS the record.
    """
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.services import marketing_report

    response = _approach(
        conn, placement.id, _market(conn, "Sompo"), status="declined"
    )
    private = "underwriter hates the loss runs, off the record"
    public = client.post(
        _cell_url(org, placement, response, "decline_reason_public"),
        data={"decline_reason_public": "loss_history"},
    )
    assert public.status_code == 200, "the client-facing reason is not editable"
    internal = client.post(
        _cell_url(org, placement, response, "decline_reason"),
        data={"decline_reason": private},
    )
    assert internal.status_code == 200, "the internal reason is not editable"

    from bookkit.repo import marketing

    fresh = marketing.get_response(conn, response.id)
    assert fresh.decline_reason_public == "loss_history"
    assert fresh.decline_reason == private, "the two reasons share one column"

    today = __import__("datetime").date(2026, 8, 25)

    def words(audience: str) -> str:
        report = marketing_report.compose(conn, placement.id, today, audience=audience)
        return " | ".join(
            cell
            for section in marketing_report.to_sections(report)
            for row in section.rows
            for cell in row
        )

    client_words = words(marketing_report.CLIENT)
    assert "Loss history" in client_words, "the public reason did not reach the client"
    assert private not in client_words, "an underwriter's private words reached a client"
    assert private in words(marketing_report.INTERNAL)

    # ...and on the broker's own screen: both, each marked at the column that
    # is the only label an inline cell has.
    html = _tab(client, org)
    assert "Reason (to client)" in html and "Internal (never sent)" in html
    assert 'data-field="decline_reason_public"' in html
    assert 'data-field="decline_reason"' in html
    assert private in html, "the internal note is not readable on the broker's grid"


# --- the vocabulary, and what a line of coverage is expected to do ----------


def _line_cell_url(org, placement, line_id: str, key: str) -> str:
    return (
        f"/accounts/{org.ref}/program/{placement.id}"
        f"/marketing/lines/{line_id}/cell/{key}"
    )


def _line_url(org, placement) -> str:
    return f"/accounts/{org.ref}/program/{placement.id}/marketing/lines"


def _line_named(conn, name: str):
    from bookkit.repo import lines as lines_repo

    return lines_repo.by_name(conn, name)


def test_the_picker_opens_an_empty_block_on_a_line_nobody_has_marketed(
    client_and_org,
):
    """A block exists because a placement_line row or a response names the
    line, so on a fresh placement there is nothing to add a market TO. This is
    the way in — and it writes the row and NOTHING else: no exposure, no
    basis, no expiring figure, because every one of those comes off a document
    that is not in front of anybody yet."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    got = client.post(_line_url(org, placement), data={"line_id": AUTO})

    assert got.status_code == 200
    assert got.text.lstrip().startswith("<section")
    assert got.headers["HX-Retarget"] == f"#marketing-{placement.id}"
    row = marketing.placement_line(conn, placement.id, AUTO)
    assert row is not None
    assert (row.expected_exposure, row.rating_basis, row.expiring_premium) == (
        None, None, None,
    ), "the picker guessed a figure nobody gave it"
    assert "No markets approached on this line yet." in got.text


def test_the_picker_renders_a_blank_option_first(client_and_org):
    """Without it the browser pre-selects option one and the picker answers
    the question — and here it would answer it OVER a name typed into the box
    beside it, which is the worse half: the line that opened would not be the
    one the broker named."""
    client, org = client_and_org

    html = _tab(client, org)

    form = html[html.index('class="marketing-line-add"') :]
    form = form[: form.index("</form>")]
    select = form[form.index("<select") : form.index("</select>")]
    options = re.findall(r"<option\b[^>]*>", select)
    assert options, "the line picker rendered no options at all"
    assert options[0] == '<option value="">', options[:3]


def test_a_line_the_book_has_never_carried_can_be_created(client_and_org):
    """The vocabulary has to be able to GROW, or a broker meets a picker that
    cannot say what they are placing. Nothing close by, so nothing to ask."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    assert _line_named(conn, "Kidnap and Ransom") is None, "the fixture is wrong"

    got = client.post(_line_url(org, placement), data={"line_name": "Kidnap and Ransom"})

    assert got.status_code == 200
    made = _line_named(conn, "Kidnap and Ransom")
    assert made is not None, "a line the book does not have was not offered a create"
    assert marketing.placement_line(conn, placement.id, made.id) is not None
    assert "Kidnap and Ransom" in got.text


def test_a_near_match_asks_and_is_never_a_veto(client_and_org):
    """ADVISORY, NEVER A REFUSAL.

    `General Liability (Products)` is 90% like `General Liability`, 85% like
    `Excess Liability` and 85% like `Cyber Liability`, and it is none of them —
    a products-only tower is its own line of coverage. That is the shape
    repo/lines.py describes in its own docstring (`Excess Liability` and
    `Employers Liability` share four letters and are different cover): a
    warning a broker cannot override makes a correct entry impossible, so both
    answers are offered and neither is taken here.
    """
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import lines as lines_repo

    typed = "General Liability (Products)"
    assert lines_repo.by_name(conn, typed) is None, "the fixture is wrong"
    gl = lines_repo.get(conn, GL)

    asked = client.post(_line_url(org, placement), data={"line_name": typed})

    assert asked.status_code == 200
    assert lines_repo.by_name(conn, typed) is None, (
        "the near match created the line without asking"
    )
    # BOTH answers are offered, and the score is printed so the broker can
    # judge the resemblance rather than be told about it.
    assert gl.name in asked.text
    assert re.search(r"\d+% alike", asked.text), "a match was shown without its score"
    assert f"&#34;line_id&#34;: &#34;{gl.id}&#34;" in asked.text, (
        "there is no way to use the line that already exists"
    )
    assert "anyway" in asked.text, "the create half of the question is missing"

    # ...and taking the second answer creates it, which is what makes this a
    # question and not a veto.
    made = client.post(
        _line_url(org, placement), data={"line_name": typed, "create": "yes"}
    )
    assert made.status_code == 200
    fresh = lines_repo.by_name(conn, typed)
    assert fresh is not None, "the guard refused a line it may only warn about"
    assert fresh.id != gl.id
    from bookkit.repo import marketing

    assert marketing.placement_line(conn, placement.id, fresh.id) is not None, (
        "the line was created and never put on the placement"
    )


def test_an_exact_duplicate_is_refused_and_the_refusal_names_the_line(
    client_and_org,
):
    """A REFUSAL SAYS SOMETHING. repo.lines.DuplicateLine carries the row that
    already exists precisely so a caller can offer to USE it rather than only
    saying no — otherwise the broker is sent back to a picker they have
    already looked past."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import lines as lines_repo

    before = len(lines_repo.all_lines(conn))
    gl = lines_repo.get(conn, GL)

    got = client.post(_line_url(org, placement), data={"line_name": gl.name})

    assert got.status_code == 200
    assert len(lines_repo.all_lines(conn)) == before, "a duplicate line was created"
    assert gl.name in got.text
    assert f'&#34;line_id&#34;: &#34;{gl.id}&#34;' in got.text, (
        "the refusal did not offer to use the line that already exists"
    )
    assert "anyway" not in got.text, "an exact duplicate was still offered a create"


def test_the_add_line_control_refuses_in_words_when_nothing_was_given(
    client_and_org,
):
    """A control that refuses in silence reads as a broken app."""
    client, org = client_and_org
    placement = _linked(client, org)

    got = client.post(_line_url(org, placement), data={"line_id": "", "line_name": ""})

    assert got.status_code == 200
    assert "cell-error-msg" in got.text
    assert "type a new name" in got.text


# --- the block header's own cells ------------------------------------------


def test_filling_the_expiring_rate_turns_every_rate_delta_into_a_number(
    client_and_org,
):
    """THE WHOLE POINT OF THE HEADER, end to end.

    Until the expiring rate is recorded the composer refuses to compute a rate
    movement and says so in words — it never assumes exposure was flat, because
    that puts a figure in front of a client that looks like rate change and is
    not. Recording it is a HEADER cell, and the rows beneath it are what the
    figure changes, which is why a header save answers with the whole block
    rather than with the cell it swapped."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    marketing.set_placement_line(
        conn, placement.id, GL,
        expected_exposure=4_850_000_000, rating_basis="gross_sales", rate_per=1000,
        # THE EXPIRING SIDE'S OWN BASIS. A rate movement is refused while
        # either basis is merely UNKNOWN, not only when the two disagree —
        # silence is not agreement (G2, 2026-08-26) — so a line that states
        # what it is rated on this term and nothing about last term gets
        # "basis not stated" where the percentage would be, and the header
        # cell that closes it is one click away.
        expiring_basis="gross_sales",
    )
    _approach(
        conn, placement.id, _market(conn, "Travelers"),
        status="quoted", responded_on="2026-08-10", rate_micros=8_100_000,
        premium=39_285_000,
    )
    _approach(
        conn, placement.id, _market(conn, "Zurich"),
        status="quoted", responded_on="2026-08-11", rate_micros=12_060_000,
        premium=58_491_000,
    )

    before = _tab(client, org)
    assert before.count("no expiring rate recorded") == 2, "the fixture is wrong"

    saved = client.post(
        _line_cell_url(org, placement, GL, "expiring_rate_micros"),
        data={"expiring_rate_micros": "10.05"},
    )

    assert saved.status_code == 200
    # ONE RESPONSE, ONE TOP-LEVEL ELEMENT, and it says where it goes: the
    # BLOCK, because the write moved a figure on every row inside it.
    assert saved.text.lstrip().startswith("<article")
    assert saved.headers["HX-Retarget"] == f"#mblock-{placement.id}-{GL}"
    assert saved.headers["HX-Reswap"] == "outerHTML"
    assert 'data-refocus="cell:expiring_rate_micros"' in saved.text

    assert marketing.placement_line(conn, placement.id, GL).expiring_rate_micros == (
        10_050_000
    )
    # 8.10 against 10.05 is -19.4%; 12.06 against 10.05 is +20.0%. Both rows,
    # in the response the save itself answered with...
    assert "no expiring rate recorded" not in saved.text
    assert "-19.4%" in saved.text and "+20.0%" in saved.text
    # ...and on the next full render of the tab.
    after = _tab(client, org)
    assert "no expiring rate recorded" not in after
    assert "-19.4%" in after and "+20.0%" in after


def test_a_count_basis_exposure_refuses_a_fraction_rather_than_flooring_it(
    client_and_org,
):
    """42 power units and $0.42 are the same digits, and models.RatingBasis
    .monetary is the one place that decides which. A fraction typed against a
    count basis is REFUSED with a sentence — flooring it would file 1,234
    power units against a figure somebody typed as an amount, and every rate
    per unit on the line would then be computed off a number nobody gave."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    marketing.set_placement_line(conn, placement.id, AUTO, rating_basis="power_units")
    url = _line_cell_url(org, placement, AUTO, "expected_exposure")

    refused = client.post(url, data={"expected_exposure": "1,234.56"})

    assert refused.status_code == 200
    assert "cell-error-msg" in refused.text, "a fraction was accepted in silence"
    assert "whole count" in refused.text
    # COMMIT IN PLACE: the editor comes back with what was typed still in it.
    assert 'value="1,234.56"' in refused.text
    assert marketing.placement_line(conn, placement.id, AUTO).expected_exposure is None

    client.post(url, data={"expected_exposure": "350"})

    assert marketing.placement_line(conn, placement.id, AUTO).expected_exposure == 350
    html = _tab(client, org)
    assert "350 power units" in html
    assert "$3.50" not in html


def test_a_monetary_exposure_is_cents_on_the_same_cell(client_and_org):
    """The other half of the one decision: the SAME cell key, against a
    monetary basis, parses as money."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    marketing.set_placement_line(conn, placement.id, GL, rating_basis="gross_sales")

    client.post(
        _line_cell_url(org, placement, GL, "expected_exposure"),
        data={"expected_exposure": "48.5m"},
    )

    assert marketing.placement_line(conn, placement.id, GL).expected_exposure == (
        4_850_000_000
    )
    assert "$48,500,000" in _tab(client, org)


def test_an_exposure_is_refused_while_nothing_says_what_it_means(client_and_org):
    """Refused, and the refusal NAMES THE FIX. Storing it against no basis at
    all would leave an integer nothing on the page could read as either money
    or a count."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    marketing.set_placement_line(conn, placement.id, GL, limit_sought=100_000_000)

    got = client.post(
        _line_cell_url(org, placement, GL, "expected_exposure"),
        data={"expected_exposure": "48.5m"},
    )

    assert "cell-error-msg" in got.text
    assert "rating basis" in got.text, "the refusal did not name the field to set first"
    assert marketing.placement_line(conn, placement.id, GL).expected_exposure is None


def test_a_basis_cannot_be_swapped_out_from_under_a_stored_exposure(
    client_and_org,
):
    """Nothing marks the stored integer as cents or as a count — the basis
    beside it IS the marking. Moving the basis re-reads $48,500,000 as
    48,500,000 power units without touching a byte, and the rate printed on a
    client's report is then a hundredfold out with no bad value to find."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    marketing.set_placement_line(
        conn, placement.id, GL,
        rating_basis="gross_sales", expected_exposure=4_850_000_000,
    )

    got = client.post(
        _line_cell_url(org, placement, GL, "rating_basis"),
        data={"rating_basis": "power_units"},
    )

    assert got.status_code == 200
    assert "cell-error-msg" in got.text, "the basis moved under the figure in silence"
    line = marketing.placement_line(conn, placement.id, GL)
    assert line.rating_basis == "gross_sales"
    assert line.expected_exposure == 4_850_000_000
    # ...and the same guard holds for MCP, which is why it lives in repo/.
    with pytest.raises(ValueError, match="MEANS"):
        marketing.set_placement_line(
            conn, placement.id, GL, rating_basis="power_units"
        )


def test_the_rate_per_cell_is_a_picker_and_not_free_text(client_and_org):
    """`rate_per` is what makes a rate mean anything — 1.42 per $100 of payroll
    and 1.42 per $1,000 of sales are ten times apart — and the conventions in
    use are a knowable set, which is exactly when the rule says a picker."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.models import RATE_PER_CHOICES
    from bookkit.repo import marketing

    marketing.set_placement_line(conn, placement.id, GL, limit_sought=100_000_000)

    editor = client.get(_line_cell_url(org, placement, GL, "rate_per") + "/edit").text

    assert "<select" in editor, "rate per is free text"
    options = re.findall(r"<option\b[^>]*>", editor)
    assert options[0] == '<option value="">', options[:3]
    assert len(options) == len(RATE_PER_CHOICES) + 1

    client.post(_line_cell_url(org, placement, GL, "rate_per"), data={"rate_per": "1000"})
    stored = marketing.placement_line(conn, placement.id, GL).rate_per
    assert stored == 1000, "the picker's string was stored instead of the number"
    assert "$1,000" in _tab(client, org)


def test_the_basis_cell_is_a_picker_over_the_declared_vocabulary(client_and_org):
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.models import RATING_BASES
    from bookkit.repo import marketing

    marketing.set_placement_line(conn, placement.id, GL, limit_sought=100_000_000)

    editor = client.get(
        _line_cell_url(org, placement, GL, "rating_basis") + "/edit"
    ).text

    options = re.findall(r"<option\b[^>]*>", editor)
    assert options[0] == '<option value="">', options[:3]
    assert len(options) == len(RATING_BASES) + 1
    # A value outside the vocabulary is refused SERVER-SIDE: markup constrains
    # a mouse and nothing else.
    refused = client.post(
        _line_cell_url(org, placement, GL, "rating_basis"),
        data={"rating_basis": "vibes"},
    )
    assert "cell-error-msg" in refused.text
    assert marketing.placement_line(conn, placement.id, GL).rating_basis is None


def test_a_header_cell_re_saved_unchanged_writes_nothing(client_and_org):
    """Opening a header cell to READ it must not cost an event-log row and an
    undo batch per glance. What makes that hold is that the editor pre-fills
    exactly what the parser accepts back."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import events, marketing

    marketing.set_placement_line(conn, placement.id, GL, rating_basis="gross_sales")
    line = marketing.placement_line(conn, placement.id, GL)
    url = _line_cell_url(org, placement, GL, "expiring_premium")
    client.post(url, data={"expiring_premium": "412,000.00"})
    assert marketing.placement_line(conn, placement.id, GL).expiring_premium == (
        41_200_000
    )

    before = len(events.history(conn, "placement_line", line.id, limit=500))
    editor = client.get(url + "/edit").text
    prefilled = re.search(r'name="expiring_premium" value="([^"]*)"', editor)
    assert prefilled, "the editor did not pre-fill the stored premium"

    client.post(url, data={"expiring_premium": prefilled.group(1)})

    assert len(events.history(conn, "placement_line", line.id, limit=500)) == before, (
        "re-saving the value the editor itself pre-filled wrote to the event log"
    )


def test_every_header_expectation_is_a_cell_where_it_prints(client_and_org):
    """Nine facts, nine cells, no second form. Walked off the field builder
    rather than a hand-written list, so a field added there and not rendered
    here turns this red."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.forms.inline import PLACEMENT_LINE_KEYS
    from bookkit.repo import marketing

    marketing.set_placement_line(conn, placement.id, GL, limit_sought=100_000_000)

    html = _tab(client, org)

    for key in PLACEMENT_LINE_KEYS:
        assert f'data-field="{key}"' in html, f"{key} is not editable where it prints"
    assert f'id="mblock-{placement.id}-{GL}"' in html


# --- three ways a wrong number reached the client, and the guards now on them


def test_a_denominator_cannot_be_swapped_out_from_under_a_stored_rate(
    client_and_org,
):
    """`rate_per` has the same property `_basis_guard` protects the exposure
    columns from, and had no guard: 1.42 per $100 is ten times 1.42 per
    $1,000, and nothing inside the stored rate says which one it was. Moving
    the picker re-labelled the expiring rate without touching a byte — the
    header printed it under the new denominator and the client's premium
    bridge came out $9,000 short of the quote it sat beneath.
    """
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    marketing.set_placement_line(
        conn, placement.id, GL,
        rating_basis="payroll", expiring_basis="payroll",
        expected_exposure=1_000_000_000, expiring_exposure=1_000_000_000,
        expiring_premium=10_000_000, expiring_rate_micros=1_000_000, rate_per=100,
    )

    refused = client.post(
        _line_cell_url(org, placement, GL, "rate_per"), data={"rate_per": "1000"}
    )

    assert refused.status_code == 200
    assert "cell-error-msg" in refused.text, "the denominator moved in silence"
    assert "MEANS" in refused.text
    assert marketing.placement_line(conn, placement.id, GL).rate_per == 100
    # ...and the same guard holds for MCP, which is why it lives in repo/.
    with pytest.raises(ValueError, match="MEANS"):
        marketing.set_placement_line(conn, placement.id, GL, rate_per=1000)
    # The ordinary correction — the denominator AND the rate it belongs to,
    # restated in one act — is not refused.
    marketing.set_placement_line(
        conn, placement.id, GL, rate_per=1000, expiring_rate_micros=10_000_000
    )
    line = marketing.placement_line(conn, placement.id, GL)
    assert line.rate_per == 1000 and line.expiring_rate_micros == 10_000_000


def test_the_header_says_why_there_is_no_exposure_comparison(client_and_org):
    """A line rated on sales last term and marketed on power units this term
    is two pickers apart, and the header printed "-100.0%" for it — 350 power
    units over $41,000,000. The percentage is refused in the composer's own
    words, in the place the percentage would have been."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    marketing.set_placement_line(
        conn, placement.id, GL,
        expiring_basis="gross_sales", expiring_exposure=4_100_000_000,
        rating_basis="power_units", expected_exposure=350,
    )

    html = _tab(client, org)

    assert "vs expiring" in html
    assert "basis changed" in html
    assert "-100.0%" not in html, "a comparison across two bases is on the panel"


def test_a_replied_date_outside_the_window_prints_its_year(client_and_org):
    """2001, 2026 and 2099 all rendered "12 Aug" — on the grid the broker
    types into and in the workbook the client is sent. The year prints where
    it is news, and the cell a save swaps back agrees with the row it lands
    in."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)

    ordinary = _approach(
        conn, placement.id, _market(conn, "Travelers"),
        status="quoted", responded_on="2026-08-12", premium=39_285_000,
    )
    mistyped = _approach(
        conn, placement.id, _market(conn, "Zurich"),
        status="quoted", responded_on="2099-08-12", premium=41_000_000,
    )

    html = _tab(client, org)

    assert "12 Aug 2099" in html, "a mistyped year is invisible on the grid"
    assert re.search(r">\s*12 Aug\s*<", html), "an ordinary date grew a year"
    # The cell the save swaps back must not judge the year differently from
    # the row it lands in — the same window, or it is the copy that differs,
    # on the same screen, one swap apart.
    plain = client.get(_cell_url(org, placement, ordinary, "responded_on"))
    assert plain.status_code == 200
    assert "12 Aug" in plain.text
    assert "2026" not in plain.text, "the cell grew a year the row beside it has not"
    loud = client.get(_cell_url(org, placement, mistyped, "responded_on"))
    assert "12 Aug 2099" in loud.text


def test_a_reply_dated_before_the_submission_went_out_is_refused(client_and_org):
    """The cross-field rule the Replied cell needed the moment it became
    typeable: a market cannot answer a package it has not been sent, and a
    mistyped year is the ordinary way to record one that did."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    response = _approach(  # the submission went out 2026-08-01
        conn, placement.id, _market(conn, "Travelers"), status="quoted",
        premium=39_285_000,
    )

    refused = client.post(
        _cell_url(org, placement, response, "responded_on"),
        data={"responded_on": "2025-08-12"},
    )

    assert refused.status_code == 200
    assert "cell-error-msg" in refused.text, "a reply before its submission was taken"
    assert "cannot answer" in refused.text
    assert marketing.get_response(conn, response.id).responded_on is None
    # COMMIT IN PLACE: what was typed is still under the caret.
    assert 'value="2025-08-12"' in refused.text

    client.post(
        _cell_url(org, placement, response, "responded_on"),
        data={"responded_on": "2026-08-12"},
    )
    assert marketing.get_response(conn, response.id).responded_on == "2026-08-12"


# --- what a save re-renders, and what a refusal says ------------------------
#
# A cell answers with the smallest thing its write can change — and never with
# something SMALLER than that. Three of the response cells feed facts printed
# above the grid, and answering those with the row left the panel stating two
# different things about one market at once (all four found in a browser,
# 2026-08-25).


def _quoting_line(conn, placement_id: str):
    """The worked example the bridge was written against: an expiring year that
    reconciles to the quote, so the block prints a four-line premium walk.

    412,000 − 79,899.98 (rate) + 60,750 (exposure) = 392,850.02, a couple of
    cents off what Travelers quoted and well inside the bridge's own slack —
    which is the ordinary case, because the expiring rate is a figure somebody
    wrote down rounded."""
    from bookkit.repo import marketing

    marketing.set_placement_line(
        conn, placement_id, GL,
        expiring_premium=41_200_000, expiring_exposure=4_100_000_000,
        expiring_rate_micros=10_048_780, expiring_basis="gross_sales",
        expected_exposure=4_850_000_000, rating_basis="gross_sales", rate_per=1000,
    )


def _bridge_of(html: str) -> str:
    """The premium walk as it stands on the page, markup and all."""
    if "marketing-bridge" not in html:
        return ""
    start = html.index('<dl class="marketing-bridge">')
    return html[start : html.index("</dl>", start)]


def test_the_cells_the_premium_bridge_is_built_from_answer_with_the_block(
    client_and_org,
):
    """A ROW ANSWER CANNOT CARRY A BLOCK FACT.

    The bridge decomposes the LEADING quote's premium through its rate, so both
    cells move it — and a row-shaped answer left it standing at the old figures.
    A broker correcting Travelers' premium to $500,000 read $500,000 in the row
    and "Travelers $392,850" in the walk four inches below it: two premiums for
    one market, on the panel whose whole purpose is comparing quotes.
    """
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    _quoting_line(conn, placement.id)
    response = _approach(
        conn, placement.id, _market(conn, "Travelers"), status="quoted",
        responded_on="2026-08-12", rate_micros=8_100_000, premium=39_285_000,
    )

    standing = _bridge_of(_tab(client, org))
    assert "$392,850" in standing, "the fixture does not print a bridge at all"

    saved = client.post(
        _cell_url(org, placement, response, "premium"), data={"premium": "500,000"}
    )

    assert saved.status_code == 200
    # ONE RESPONSE, ONE TOP-LEVEL ELEMENT, and it says where it goes.
    assert saved.text.lstrip().startswith("<article")
    assert saved.headers["HX-Retarget"] == f"#mblock-{placement.id}-{GL}"
    assert saved.headers["HX-Reswap"] == "outerHTML"
    assert marketing.get_response(conn, response.id).premium == 50_000_000
    # The walk that explained $392,850 does not survive the figure it explained.
    assert standing not in saved.text
    assert "$392,850" not in saved.text
    assert "$500,000" in saved.text
    # The caret goes back to THIS row's cell — a block holds one premium cell
    # per market, so the token has to name the row and not just the field.
    assert f'data-refocus="{response.id}:premium"' in saved.text
    assert f'data-layer-row="{response.id}"' in saved.text, (
        "the row carries no record hook, so the refocus token resolves nowhere"
    )


def test_correcting_the_rate_re_renders_the_walk_the_rate_effect_is_in(
    client_and_org,
):
    """The rate effect is (quoted rate − expiring rate) × the expiring exposure.
    Answered with the row, editing the rate left the OLD rate effect printed —
    and it is the one figure on the panel that changes SIGN, so the walk went on
    saying the rate had taken the premium down while the new rate had put it up.
    """
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)

    _quoting_line(conn, placement.id)
    response = _approach(
        conn, placement.id, _market(conn, "Travelers"), status="quoted",
        responded_on="2026-08-12", rate_micros=8_100_000, premium=39_285_000,
    )
    standing = _bridge_of(_tab(client, org))
    assert "-$79,899.98" in standing, "the fixture's rate effect is not negative"

    saved = client.post(
        _cell_url(org, placement, response, "rate_micros"),
        data={"rate_micros": "12.00"},
    )

    assert saved.status_code == 200
    assert saved.headers["HX-Retarget"] == f"#mblock-{placement.id}-{GL}"
    assert "-$79,899.98" not in saved.text, (
        "the rate effect of the rate that was replaced is still on the panel"
    )
    assert standing not in saved.text


def test_declining_a_market_takes_its_clearance_warning_with_it(client_and_org):
    """DECLINING THE DUPLICATE APPROACH IS THE ACT THAT RESOLVES THE CONFLICT.

    The strip counts LIVE approaches (repo.marketing.clearance_conflicts), and
    a status is the only cell that can move it — so a row-shaped answer left the
    panel warning about a collision that no longer existed until the tab was
    reloaded.
    """
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)

    carrier = _market(conn, "Berkley")
    wholesaler = _market(conn, "Amwins")
    _approach(conn, placement.id, carrier, status="quoted")
    doubled = _approach(conn, placement.id, carrier, status="quoted")
    from bookkit.repo import marketing

    marketing.edit_response(conn, doubled.id, {"via_org_id": wholesaler.id})

    assert _tab(client, org).count("also reached via") == 2, "the fixture does not clash"

    saved = client.post(
        _cell_url(org, placement, doubled, "status"), data={"status": "declined"}
    )

    assert saved.status_code == 200
    assert saved.headers["HX-Retarget"] == f"#mblock-{placement.id}-{GL}"
    # BOTH warnings go, and the second one is the point. A clearance conflict
    # is two LIVE approaches: the live row stops warning because the market it
    # was clashing with has said no, and the DECLINED row stops warning because
    # it is no longer in the fight. It used to keep its own — the strip said the
    # carrier was being reached twice while one live approach remained — because
    # `clearance_conflicts` filtered the OTHER rows by open status and never
    # looked at the status of the row asking (found 2026-08-26).
    assert saved.text.count("also reached via") == 0, saved.text
    assert saved.text.count("also reached via") == _tab(client, org).count(
        "also reached via"
    ), "the block answered with a different count from a full re-render"


def _scope_of(html: str, needle_class: str) -> list[str]:
    """The classes of every ancestor of the first element carrying
    `needle_class` — what inline-cell.js's `closest(ERROR_SCOPE)` walks."""
    from html.parser import HTMLParser

    void = {"input", "br", "img", "meta", "link", "hr", "source", "col"}

    class Walker(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.stack: list[tuple[str, str]] = []
            self.found: list[str] | None = None

        def handle_starttag(self, tag, attrs):  # type: ignore[no-untyped-def]
            classes = dict(attrs).get("class") or ""
            if self.found is None and needle_class in classes.split():
                self.found = [c for _, c in self.stack]
            if tag not in void:
                self.stack.append((tag, classes))

        def handle_endtag(self, tag):  # type: ignore[no-untyped-def]
            # POP TO THE MATCH, not one entry per close: an unclosed or void
            # element otherwise shifts every ancestor after it, which reads as
            # a message sitting at the top of the document.
            for index in range(len(self.stack) - 1, -1, -1):
                if self.stack[index][0] == tag:
                    del self.stack[index:]
                    return

    walker = Walker()
    walker.feed(html)
    assert walker.found is not None, f"no .{needle_class} in the markup"
    return walker.found


def test_the_add_row_refusal_sits_where_a_keystroke_can_clear_it(client_and_org):
    """VALIDATE ON BLUR, CLEAR ON KEYSTROKE — the researched rule the listener
    exists to enforce.

    inline-cell.js walks UP from the input being corrected to an ERROR_SCOPE and
    clears the messages inside it. The refusal was rendered as a SIBLING of
    `.market-add-form`, so nothing ever reached it: a broker mistyped a carrier,
    was refused, retyped "Zurich", and the red "no market matching 'Zzzz
    Mutual'" was still under the box. A message that survives its own correction
    makes a valid entry read as broken.
    """
    client, org = client_and_org
    placement = _linked(client, org)

    # A MONEY FIELD, not the carrier: a carrier the book has never carried is
    # no longer refused at all — it is a question with an "add it" button
    # (2026-08-26) — and this test is about where a REFUSAL sits.
    refused = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/marketing/lines/{GL}/approaches",
        data={"market": "", "via": "", "attach": "banana", "lim": "",
              "sent_on": "2026-08-10", "status": "pending"},
    )

    assert refused.status_code == 200
    assert "attaches at" in refused.text
    scope = _scope_of(refused.text, "cell-error-msg")
    assert any("market-add-form" in classes for classes in scope), (
        "the refusal is outside the scope the clear-on-keystroke listener walks: "
        f"{scope}"
    )
    # COMMIT IN PLACE: what was typed is still in the row.
    assert 'value="banana"' in refused.text


def test_the_add_row_refuses_a_blank_status_rather_than_filing_pending(
    client_and_org,
):
    """A DECLARED GUARD THAT DOES NOT HOLD IS WORSE THAN NO GUARD.

    `status` is declared required (forms/inline.py, citing the response that
    filed itself as "quoted"), and the markup `required` cannot fire here: the
    add row is inside a table with no <form> ancestor and its Save is a
    type="button". Cleared back to blank, an approach filed itself as "pending"
    — a status nobody chose — while the cell route one door over refused the
    same empty value.
    """
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    _market(conn, "Zurich")
    refused = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/marketing/lines/{GL}/approaches",
        data={"market": "Zurich", "via": "", "attach": "", "lim": "",
              "sent_on": "2026-08-10", "status": ""},
    )

    assert refused.status_code == 200
    assert "status is required" in refused.text, refused.text[:2000]
    assert [
        r for r in marketing.responses_for_placement(conn, placement.id)
        if r.line_id == GL
    ] == [], "a blank status filed an approach anyway"
    # COMMIT IN PLACE, and then the correction goes through.
    assert 'value="Zurich"' in refused.text
    accepted = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/marketing/lines/{GL}/approaches",
        data={"market": "Zurich", "via": "", "attach": "", "lim": "",
              "sent_on": "2026-08-10", "status": "pending"},
    )
    assert accepted.status_code == 200
    assert len([
        r for r in marketing.responses_for_placement(conn, placement.id)
        if r.line_id == GL
    ]) == 1


def test_a_figure_too_large_to_store_is_refused_in_this_books_own_words(
    client_and_org,
):
    """A REFUSAL SAYS SOMETHING, and what it says is OURS.

    A pasted 20-digit premium parsed cleanly, was multiplied into cents and then
    failed inside the INSERT — so the sentence in the premium cell was "Python
    int too large to convert to SQLite INTEGER". The ceiling belongs in the
    parser, ahead of every writer, where MCP and the importers inherit it too.
    """
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.money import parse_money_cents
    from bookkit.repo import marketing

    response = _approach(conn, placement.id, _market(conn, "Travelers"), status="quoted")

    refused = client.post(
        _cell_url(org, placement, response, "premium"),
        data={"premium": "99999999999999999999"},
    )

    assert refused.status_code == 200
    assert "SQLite" not in refused.text and "Python int" not in refused.text
    assert "is not an amount" in refused.text
    assert "this book can record" in refused.text
    assert marketing.get_response(conn, response.id).premium is None
    # ONE HOME: the same refusal reaches MCP and the importers, because it is
    # the parser's and not this route's.
    with pytest.raises(Exception, match="this book can record"):
        parse_money_cents("99999999999999999999")
    # ...and the ordinary amounts either side of it are untouched.
    assert parse_money_cents("1,234.56") == 123_456


def test_a_failure_that_is_not_a_refusal_never_speaks_as_a_library(
    client_and_org, monkeypatch
):
    """The floor under the ceiling. Whatever else breaks inside a write, the
    sentence a broker reads is one this book wrote — a route that renders
    `str(exc)` for anything at all is one library upgrade away from putting a
    stack-trace sentence in a premium cell again."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.web.routes import marketing as marketing_routes

    response = _approach(conn, placement.id, _market(conn, "Travelers"), status="quoted")

    def boom(*args, **kwargs):
        raise OverflowError("Python int too large to convert to SQLite INTEGER")

    monkeypatch.setattr(marketing_routes.marketing_repo, "edit_response", boom)
    refused = client.post(
        _cell_url(org, placement, response, "premium"), data={"premium": "500,000"}
    )

    assert refused.status_code == 200, "a broken write became a 500"
    assert "Python int" not in refused.text and "SQLite" not in refused.text
    assert "nothing was written" in refused.text
    # COMMIT IN PLACE still holds on the way out.
    assert 'value="500,000"' in refused.text


def test_two_marketed_placements_do_not_share_a_dom_id(client_and_org):
    """One Program tab renders EVERY placement on the account, so an id built
    from the line of coverage alone is emitted twice the moment two placements
    market the same line — invalid HTML, and both carrier inputs resolve to the
    first datalist."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing
    from bookkit.repo import placements as placements_repo

    second = placements_repo.create(
        conn, org.id, "Casualty renewal", "2026-11-01", "2027-11-01"
    )
    for pid in (placement.id, second.id):
        marketing.set_placement_line(conn, pid, GL, expected_exposure=1_000_000)

    html = _tab(client, org)

    # Scoped to the ids this panel emits. The tower diagram above it emits its
    # own duplicates across two placements ('primary-gl', 'umbrella'), which is
    # the same bug one section up and not this one's to assert.
    ids = [
        i for i in re.findall(r'\bid="([^"]+)"', html)
        if i.startswith(("marketing-", "mblock-", "mrow-", "mk-"))
    ]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    assert duplicates == [], f"the marketing panel emits duplicate DOM ids: {duplicates}"
    assert any(i.startswith("mk-") for i in ids), "no datalist ids to check at all"


# --- the states with nothing in them ----------------------------------------
#
# Three ways a block can exist with something missing behind it, each of which
# had the panel rendering controls that did not work or facts that were not
# there. The rule under all three: what is missing is SAID, and what is still
# recorded is still reachable.


def _panel_html(html: str, placement_id: str) -> str:
    """ONE placement's marketing section. The Program tab renders every
    placement on the account, and each carries its own add-a-line picker."""
    start = html.index(f'id="marketing-{placement_id}"')
    rest = html[start:]
    end = rest.find('<section class="marketing"')
    return rest if end == -1 else rest[:end]


def _block_html(html: str, block_id: str) -> str:
    """One block's markup, from its own <article> to the next one."""
    start = html.index(f'id="{block_id}"')
    rest = html[start:]
    end = rest.find('<article class="marketing-block"')
    return rest if end == -1 else rest[:end]


def test_a_block_with_no_expectations_row_is_still_editable(client_and_org):
    """A BLOCK CAN EXIST WITH NO `placement_line` ROW BEHIND IT — a response
    named the line and nobody has stated an expectation about it. All nine
    header cells rendered as clickable, and all nine answered 500: reached by
    pressing `u` on "started marketing …", and by MCP's `market_approach`,
    which writes a response and no row. The cells are an upsert; that is what
    `set_placement_line` is for."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing
    from bookkit.web import marketing_grid

    _approach(
        conn, placement.id, _market(conn, "Chubb", "A++ XV"),
        status="quoted", responded_on="2026-08-12", premium=39_285_000,
    )
    assert marketing.placement_line(conn, placement.id, GL) is None, "fixture"

    html = _tab(client, org)
    assert f'id="mblock-{placement.id}-{GL}"' in html

    base = f"/accounts/{org.ref}/program/{placement.id}/marketing/lines/{GL}/cell"
    for key in marketing_grid.LINE_KEYS:
        opened = client.get(f"{base}/{key}/edit")
        assert opened.status_code == 200, f"{key} detonated on a block with no row"

    saved = client.post(f"{base}/expiring_premium", data={"expiring_premium": "412,000"})
    assert saved.status_code == 200
    row = marketing.placement_line(conn, placement.id, GL)
    assert row is not None and row.expiring_premium == 41_200_000
    assert "$412,000" in saved.text


def test_the_picker_offers_a_line_only_a_response_has_named(client_and_org):
    """AND THE WAY BACK IS NOT REFUSED EITHER. The picker dropped any line a
    RESPONSE named, so the line above was in neither half of the add control —
    not in the list, and refused by name as already carried — and the
    near-match card's own "use <line>" button posted an id the picker's own
    check did not recognise. Picking it creates the row."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    _approach(conn, placement.id, _market(conn, "Chubb"), status="quoted")
    assert marketing.placement_line(conn, placement.id, GL) is None, "fixture"

    assert f'<option value="{GL}">' in _panel_html(_tab(client, org), placement.id)

    added = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/marketing/lines",
        data={"line_id": GL},
    )
    assert added.status_code == 200
    assert "is not one of the choices offered" not in added.text
    assert marketing.placement_line(conn, placement.id, GL) is not None
    # And once the row exists it stops being offered: picking it again would
    # re-write a row that already exists and move nothing.
    assert f'<option value="{GL}">' not in _panel_html(_tab(client, org), placement.id)


def test_a_retired_line_of_coverage_keeps_its_marketing_on_the_panel(client_and_org):
    """SURFACE IT, DO NOT HIDE IT. Retiring a line of coverage soft-deletes one
    vocabulary row and touches neither the responses nor the expectations — but
    the composer read the LIVING vocabulary, found no name, and dropped the
    whole block: a bound quote gone from the tab and from the client's own
    workbook, with the panel saying nothing was being marketed."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import base, lines, marketing

    cargo = lines.create(conn, "Ocean Cargo")
    marketing.set_placement_line(
        conn, placement.id, cargo, expiring_premium=41_200_000
    )
    _approach(
        conn, placement.id, _market(conn, "Chubb", "A++ XV"), line_id=cargo,
        status="bound", responded_on="2026-08-12", premium=39_285_000,
    )
    base.soft_delete(conn, "line_of_coverage", cargo)

    block = _block_html(_tab(client, org), f"mblock-{placement.id}-{cargo}")
    assert "Ocean Cargo" in block
    assert "Chubb" in block and "Bound" in block and "$392,850" in block
    assert "line of coverage retired" in block
    # NO NEW APPROACHES on a line the book no longer carries — the control is
    # withdrawn where the header can say why, not left to refuse.
    assert "/approaches" not in block
    # What is already recorded stays correctable where it prints.
    saved = client.post(
        f"/accounts/{org.ref}/program/{placement.id}"
        f"/marketing/lines/{cargo}/cell/expiring_premium",
        data={"expiring_premium": "500,000"},
    )
    assert saved.status_code == 200
    assert "$500,000" in saved.text


def test_a_market_the_book_has_deleted_is_still_named_on_its_row(client_and_org):
    """THE COLUMN THAT SAYS WHOSE ANSWER IT IS. A soft-deleted carrier org left
    the Market cell completely empty — a row with a premium, a status and an
    A.M. Best rating and nothing identifying it, which reads as a rendering
    fault. Deleting the org does not unmake the quote."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import orgs as orgs_repo

    chubb = _market(conn, "Chubb", "A++ XV")
    response = _approach(
        conn, placement.id, chubb, status="quoted", responded_on="2026-08-12",
        premium=39_285_000,
    )
    orgs_repo.delete(conn, chubb.id)

    html = _tab(client, org)
    row = html[html.index(f'id="mrow-{response.id}"') :]
    row = row[: row.index("</tr>")]
    first_cell = re.search(r"<td[^>]*>(.*?)</td>", row, re.S)
    assert first_cell is not None
    assert first_cell.group(1).strip() == "Chubb"


def test_the_marketing_panel_is_reachable_with_no_program_file_anywhere(snapshot_db):
    """THE PANEL IS REACHABLE FROM THE ACCOUNT PAGE on an account whose
    placements are all unlinked — the question the panel's author raised and
    nobody answered. Marketing happens before a tower exists, so an account
    that has never had a program file is the ordinary case, not the edge."""
    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import placements as placements_repo

    app = create_app(snapshot_db)
    conn = app.state.conn
    with TestClient(app, base_url="http://127.0.0.1") as client:
        org = next(
            o
            for o in orgs_repo.list_orgs(conn, kind="client")
            if placements_repo.for_org(conn, o.id)
            and not any(p.program_path for p in placements_repo.for_org(conn, o.id))
        )
        placements = placements_repo.for_org(conn, org.id)

        page = client.get(f"/accounts/{org.ref}")
        assert page.status_code == 200
        assert f'href="/accounts/{org.ref}/program"' in page.text, (
            "the Program tab is the only way to the marketing panel"
        )

        tab = client.get(f"/accounts/{org.ref}/program")
        assert tab.status_code == 200
        for placement in placements:
            assert f'id="marketing-{placement.id}"' in tab.text
        assert 'class="marketing-line-add"' in tab.text


# --- entry: what a broker types, and what the book does with it -------------
#
# Five defects a fresh-eyes pass found on 2026-08-26, all of them about the
# same thing from different sides: a surface that silently changes, discards or
# mis-states what somebody typed.


def _sent_url(org, placement, response) -> str:
    return (
        f"/accounts/{org.ref}/program/{placement.id}"
        f"/marketing/responses/{response.id}/sent"
    )


def _approaches_url(org, placement, line: str = GL) -> str:
    return (
        f"/accounts/{org.ref}/program/{placement.id}"
        f"/marketing/lines/{line}/approaches"
    )


def test_a_second_approach_on_another_day_opens_its_own_submission(client_and_org):
    """A DIFFERENT SENT DATE IS A DIFFERENT SUBMISSION.

    The reuse rule is about LINES — one email carries every line — and it was
    keyed on the market alone, so the second approach silently JOINED the first
    package and the `sent` date the broker typed was thrown away with no
    message. The block then printed a submission date nobody had entered.
    """
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import submissions

    _market(conn, "Chubb")
    for line, sent in ((GL, "2026-08-03"), (AUTO, "2026-08-11")):
        got = client.post(
            _approaches_url(org, placement, line),
            data={"market": "Chubb", "via": "", "attach": "", "lim": "",
                  "sent_on": sent, "status": "pending"},
        )
        assert got.status_code == 200, got.text[:800]

    dates = sorted(
        s.sent_on for s in submissions.for_placement(conn, placement.id)
        if s.sent_on in ("2026-08-03", "2026-08-11")
    )
    assert dates == ["2026-08-03", "2026-08-11"], (
        "the second approach joined the first package and lost its own date"
    )


def test_a_submission_dated_in_the_future_is_refused_in_words(client_and_org):
    """A SUBMISSION IS A RECORD OF SOMETHING THAT HAPPENED.

    One wrong year is one keystroke, nothing downstream ever objects, and
    `_reply_guard` then refuses every reply to that row for good — a wedge with
    a refusal on top of it naming a correction.
    """
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    _market(conn, "Chubb")
    refused = client.post(
        _approaches_url(org, placement),
        data={"market": "Chubb", "via": "", "attach": "", "lim": "5m",
              "sent_on": "2027-08-01", "status": "pending"},
    )

    assert refused.status_code == 200
    assert "has not happened yet" in refused.text, refused.text[:1200]
    # NEVER LOSE TYPING: the refused row comes back with what was in it.
    assert 'value="Chubb"' in refused.text
    assert 'value="2027-08-01"' in refused.text
    assert not [
        r for r in marketing.responses_for_placement(conn, placement.id)
        if r.line_id == GL
    ], "the refused approach was written anyway"


def test_the_date_a_submission_went_out_is_correctable_where_it_prints(
    client_and_org,
):
    """A REFUSAL MUST NOT NAME A FIX THAT DOES NOT EXIST.

    `_reply_guard` refuses a reply dated before its submission went out and
    names correcting the send date as the other way out. Until the Sent cell
    existed, no surface in the app could make that correction — so one
    transposed digit made the Replied cell on that row unanswerable. This is
    the whole wedge, driven end to end: refused, corrected, accepted.
    """
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing, submissions

    market = _market(conn, "Chubb")
    sub = submissions.create(
        conn, market_org_id=market.id, sent_on="2026-08-12", placement_id=placement.id
    )
    response = marketing.create_response(
        conn, sub.id, GL, market_org_id=market.id, status="quoted"
    )

    wedged = client.post(
        _cell_url(org, placement, response, "responded_on"),
        data={"responded_on": "2026-08-04"},
    )
    assert "did not go out until 2026-08-12" in wedged.text

    # THE CELL THE REFUSAL NAMED. It is on the row, addressed by the response,
    # and it writes the submission behind it.
    assert client.get(_sent_url(org, placement, response) + "/edit").status_code == 200
    fixed = client.post(_sent_url(org, placement, response), data={"sent_on": "2026-08-02"})
    assert fixed.status_code == 200
    # ONE RESPONSE, ONE TOP-LEVEL ELEMENT — the SECTION, because one submission
    # carries every line of coverage it was sent on.
    assert fixed.headers["HX-Retarget"] == f"#marketing-{placement.id}"
    assert fixed.headers["HX-Reswap"] == "outerHTML"
    assert submissions.get(conn, sub.id).sent_on == "2026-08-02"

    accepted = client.post(
        _cell_url(org, placement, response, "responded_on"),
        data={"responded_on": "2026-08-04"},
    )
    assert accepted.status_code == 200
    assert marketing.get_response(conn, response.id).responded_on == "2026-08-04"


def test_a_corrected_send_date_cannot_land_after_an_answer_already_recorded(
    client_and_org,
):
    """The Sent cell is the way out of `_reply_guard`, so it must not be a way
    INTO the state that guard refuses — the guard only ever looks at the reply
    being typed."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing, submissions

    market = _market(conn, "Chubb")
    sub = submissions.create(
        conn, market_org_id=market.id, sent_on="2026-08-01", placement_id=placement.id
    )
    response = marketing.create_response(
        conn, sub.id, GL, market_org_id=market.id,
        status="quoted", responded_on="2026-08-06",
    )

    refused = client.post(
        _sent_url(org, placement, response), data={"sent_on": "2026-08-09"}
    )
    assert refused.status_code == 200
    assert "answered on 2026-08-06" in refused.text, refused.text[:800]
    # COMMIT IN PLACE: the editor stays open with what was typed under the caret.
    assert 'value="2026-08-09"' in refused.text
    assert submissions.get(conn, sub.id).sent_on == "2026-08-01"


def test_a_block_answer_keeps_a_half_typed_approach(client_and_org):
    """NEVER LOSE TYPING TO SOMEBODY ELSE'S WRITE.

    Three cells answer with the whole BLOCK, and that swap rebuilt the add row
    from its defaults — so correcting a premium wiped a half-typed approach in
    the same block, silently. `hx-preserve` rides on the answer and htmx keeps
    the element already on the page; the add row's OWN successful save is the
    one answer that must not carry it, because there a cleared row is right.
    """
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)

    response = _approach(
        conn, placement.id, _market(conn, "Travelers"), status="quoted"
    )
    block_id = f"add-mblock-{placement.id}-{GL}"

    moved = client.post(
        _cell_url(org, placement, response, "premium"), data={"premium": "590,000"}
    )
    assert moved.status_code == 200
    assert moved.headers["HX-Retarget"] == f"#mblock-{placement.id}-{GL}"
    assert f'id="{block_id}"' in moved.text
    assert 'hx-preserve="true"' in moved.text, (
        "a block answer rebuilt the add row and threw away what was typed in it"
    )

    _market(conn, "Zurich")
    added = client.post(
        _approaches_url(org, placement),
        data={"market": "Zurich", "via": "", "attach": "", "lim": "",
              "sent_on": "2026-08-10", "status": "pending"},
    )
    assert added.status_code == 200
    assert f'id="{block_id}"' in added.text
    assert "hx-preserve" not in added.text, (
        "the add row's own save kept the form it had just saved"
    )


def test_a_carrier_cannot_be_reached_through_itself(client_and_org):
    """`Chubb (via Chubb)` — paper reached through itself, on the CLIENT's
    sheet. Plausible whenever a broker is unsure which of the two a name is,
    and the route only ever checked that AT LEAST ONE was given."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    _market(conn, "Chubb")
    refused = client.post(
        _approaches_url(org, placement),
        data={"market": "Chubb", "via": "Chubb", "attach": "", "lim": "",
              "sent_on": "2026-08-10", "status": "pending"},
    )

    assert refused.status_code == 200
    assert "not reached through itself" in refused.text, refused.text[:1200]
    assert not [
        r for r in marketing.responses_for_placement(conn, placement.id)
        if r.line_id == GL
    ]


# ===========================================================================
# A MARKET NEW TO THE BOOK IS ADDED FROM THE ROW (Grant, 2026-08-26)
# ===========================================================================
#
# "Being able to add a market easily if not in the market database vs. having
# to toggle over to the other tab to add. Default behavior in the program
# should be to easily create new records without having to navigate away from
# the work in progress — even if a stub record to come back to later."
#
# What was there before named /markets/new and stopped. That is a REFUSAL
# naming a fix, which is the house standard for a refusal — but the fix was on
# another page, and going there abandons a half-typed approach: the sent date,
# the attachment, the limit and the status are all gone when you come back.


def _clash_cards(html: str) -> list[str]:
    """Every near-match card in the markup, as its own slice.

    The card holds only `p`, `ul`, `li`, `span` and `button`, so the first
    `</div>` after it closes it. Sliced rather than parsed because what this
    asks about is the CLASS ATTRIBUTES on the buttons inside, which is the
    thing a parser would throw away.
    """
    cards = []
    start = 0
    while True:
        found = html.find('class="marketing-clash"', start)
        if found == -1:
            return cards
        end = html.index("</div>", found)
        cards.append(html[found:end])
        start = end


def _clash_buttons(card: str) -> list[str]:
    import re

    return [
        m.group(1)
        for m in re.finditer(r'<button[^>]*class="([^"]*)"', card)
    ]


def test_every_option_on_a_near_match_card_is_a_real_button(client_and_org):
    """AN OPTION HAS TO LOOK LIKE ONE (Grant, 2026-08-26). Both cards shipped
    with `.row-action-btn`, which is the deliberately quiet borderless
    treatment a table's ROW actions take — right for "take off" sitting in a
    grid, wrong here, where the buttons are the entire reason the card exists.

    AND NO OPTION IS LOUDER THAN THE OTHERS while there is a choice to make.
    Filling one would be picking, and neither card picks: 'Zurich' and 'Zurich
    American' are two real markets, 'Excess Liability' and 'Employers
    Liability' are genuinely different cover. `.btn-primary` is allowed only
    where the create is the ONLY action on the card — then there is nothing to
    choose between and it is simply the action.

    WHERE THIS LOOKS: both cards, driven for real — the market card in the add
    row and the line-of-coverage card in the header. They are two templates and
    they have to agree, because they appear on one screen.
    """
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    _market(conn, "Travelers")

    with_match = client.post(
        _approaches_url(org, placement),
        data={"market": "Travellers", "via": "", "attach": "", "lim": "",
              "sent_on": "2026-08-10", "status": "pending"},
    ).text
    alone = client.post(
        _approaches_url(org, placement),
        data={"market": "Quibdoxen", "via": "", "attach": "", "lim": "",
              "sent_on": "2026-08-10", "status": "pending"},
    ).text
    line_card = client.post(
        f"/accounts/{org.ref}/program/{placement.id}/marketing/lines",
        data={"line_id": "", "line_name": "Employer Liability"},
    ).text

    for label, html in (
        ("market card, near match", with_match),
        ("market card, none close", alone),
        ("line-of-coverage card", line_card),
    ):
        cards = _clash_cards(html)
        assert cards, f"{label}: no near-match card in the answer"
        for card in cards:
            classes = _clash_buttons(card)
            assert classes, f"{label}: a card with no option to press"
            for cls in classes:
                names = cls.split()
                assert "btn" in names, f"{label}: option is not a button ({cls!r})"
                assert "row-action-btn" not in names, (
                    f"{label}: option wears the quiet row-action treatment ({cls!r})"
                )
            loud = [c for c in classes if "btn-primary" in c.split()]
            uses = [c for c in card.split("<button") if ">use " in c]
            if uses:
                assert not loud, (
                    f"{label}: one option is filled while there is still a "
                    f"choice to make — the card does not pick"
                )
            else:
                assert len(loud) == 1, (
                    f"{label}: the only action on the card is not the primary "
                    f"one ({classes})"
                )


def test_a_market_new_to_the_book_is_offered_rather_than_refused(client_and_org):
    """The question, not the wall — and NOTHING is written while it is open."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing, orgs

    asked = client.post(
        _approaches_url(org, placement),
        data={"market": "Zzzz Mutual", "via": "", "attach": "", "lim": "",
              "sent_on": "2026-08-10", "status": "pending"},
    )

    assert asked.status_code == 200
    assert "is not a market this book carries" in asked.text
    assert "add “Zzzz Mutual” to the book" in asked.text, asked.text[:1500]
    # THE QUESTION IS NOT THE ANSWER: no market, no approach.
    assert orgs.find_market(conn, "Zzzz Mutual") is None
    assert not [
        r for r in marketing.responses_for_placement(conn, placement.id)
        if r.line_id == GL
    ]
    # COMMIT IN PLACE: the rest of the row survives the question.
    assert 'value="2026-08-10"' in asked.text


def test_confirming_mints_the_market_and_records_the_approach(client_and_org):
    """One click, one write: the stub and the approach it was needed for."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing, orgs

    saved = client.post(
        _approaches_url(org, placement),
        data={"market": "Zzzz Mutual", "via": "", "attach": "", "lim": "",
              "sent_on": "2026-08-10", "status": "pending",
              "create_market": "yes"},
    )

    assert saved.status_code == 200
    minted = orgs.find_market(conn, "Zzzz Mutual")
    assert minted is not None
    # A STUB IS A REAL RECORD: the defaults org_form gives a new market.
    assert (minted.kind, minted.name) == ("market", "Zzzz Mutual")
    assert str(getattr(minted.status, "value", minted.status)) == "active"
    rows = [
        r for r in marketing.responses_for_placement(conn, placement.id)
        if r.line_id == GL and r.market_org_id == minted.id
    ]
    assert len(rows) == 1


def test_the_market_and_the_approach_revert_together(client_and_org):
    """ONE WRITER ACTION IS ONE UNDO UNIT. A revert that took the approach
    back and left the market behind would leave a record nobody asked for, on
    the one table where a duplicate is the thing every guard here prevents."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit import db
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import marketing, orgs
    from bookkit.services import batches as batches_svc

    client.post(
        _approaches_url(org, placement),
        data={"market": "Zzzz Mutual", "via": "", "attach": "", "lim": "",
              "sent_on": "2026-08-10", "status": "pending",
              "create_market": "yes"},
    )
    assert orgs.find_market(conn, "Zzzz Mutual") is not None

    batch = batches_repo.recent(conn, since="2000-01-01T00:00:00+00:00", limit=1)[0]
    assert batch.tool == "market_approach"
    result = batches_svc.revert(conn, batch.ref, now=db.utc_now())
    assert result.reverted, result

    assert orgs.find_market(conn, "Zzzz Mutual") is None
    assert not [
        r for r in marketing.responses_for_placement(conn, placement.id)
        if r.line_id == GL
    ]


def test_the_question_offers_the_markets_the_typo_looks_like(client_and_org):
    """ADVISORY, NEVER A VETO — and the cheap half of the anti-duplicate rule
    is that the market already on the book is one click away."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import orgs

    existing = _market(conn, "Travelers")

    asked = client.post(
        _approaches_url(org, placement),
        data={"market": "Travellers", "via": "", "attach": "", "lim": "",
              "sent_on": "2026-08-10", "status": "pending"},
    )

    assert "use Travelers" in asked.text, asked.text[:1500]
    assert "% alike" in asked.text

    used = client.post(
        _approaches_url(org, placement),
        data={"market": "Travellers", "via": "", "attach": "", "lim": "",
              "sent_on": "2026-08-10", "status": "pending",
              "use_market": existing.id},
    )
    assert used.status_code == 200
    # THE ID IS AUTHORITATIVE: nothing was minted under the misspelling.
    assert orgs.find_market(conn, "Travellers") is None
    from bookkit.repo import marketing

    assert [
        r for r in marketing.responses_for_placement(conn, placement.id)
        if r.line_id == GL and r.market_org_id == existing.id
    ]


def test_a_market_the_book_already_carries_is_never_asked_about(client_and_org):
    """Case is not a difference. `find_by_name` is a bare `WHERE name = ?`, so
    "travelers" missed "Travelers" and the broker was asked to create a second
    one — the duplicate this question exists to prevent, minted by the question
    itself."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    existing = _market(conn, "Travelers")

    saved = client.post(
        _approaches_url(org, placement),
        data={"market": "travelers", "via": "", "attach": "", "lim": "",
              "sent_on": "2026-08-10", "status": "pending"},
    )

    assert "is not a market this book carries" not in saved.text
    assert [
        r for r in marketing.responses_for_placement(conn, placement.id)
        if r.line_id == GL and r.market_org_id == existing.id
    ]


def test_a_client_of_the_same_name_does_not_hide_the_market(client_and_org):
    """`find_by_name` reads the whole org table and returns the FIRST match, so
    a client sharing a market's name answered first, the caller saw
    `kind != "market"` and refused — naming, as the nearest market, the very
    market it had just failed to find. One click from real now that a market
    can be minted from this row."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing, orgs

    # THE CLIENT FIRST, deliberately. `find_by_name` is a bare
    # `WHERE name = ?` with no ORDER BY, so it answers with whichever row
    # SQLite reaches first — and with the market created first this test
    # passes against the OLD code by luck of row order rather than because the
    # resolver is right (checked by mutation, 2026-08-26).
    orgs.create(conn, kind="client", name="Hartwell Mutual", status="prospect")
    market = _market(conn, "Hartwell Mutual")

    saved = client.post(
        _approaches_url(org, placement),
        data={"market": "Hartwell Mutual", "via": "", "attach": "", "lim": "",
              "sent_on": "2026-08-10", "status": "pending"},
    )

    assert "is not a market this book carries" not in saved.text
    assert [
        r for r in marketing.responses_for_placement(conn, placement.id)
        if r.line_id == GL and r.market_org_id == market.id
    ]


def test_both_names_new_are_asked_one_at_a_time_and_written_once(client_and_org):
    """Both boxes can be new to the book. The answer to the first question has
    to survive the second, or the two ping-pong forever — and NOTHING is
    written until every name on the row has an answer, so abandoning the second
    question cannot leave a market behind with no approach."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing, orgs

    row = {"market": "Zzzz Mutual", "via": "Qqqq Brokers", "attach": "",
           "lim": "", "sent_on": "2026-08-10", "status": "pending"}

    first = client.post(_approaches_url(org, placement), data=row)
    assert "add “Zzzz Mutual” to the book" in first.text

    second = client.post(
        _approaches_url(org, placement), data={**row, "create_market": "yes"}
    )
    # The SECOND question, with the first answer carried as a hidden input.
    assert "add “Qqqq Brokers” to the book" in second.text, second.text[:1500]
    assert 'name="create_market" value="yes"' in second.text
    assert orgs.find_market(conn, "Zzzz Mutual") is None

    saved = client.post(
        _approaches_url(org, placement),
        data={**row, "create_market": "yes", "create_via": "yes"},
    )
    assert saved.status_code == 200
    carrier = orgs.find_market(conn, "Zzzz Mutual")
    intermediary = orgs.find_market(conn, "Qqqq Brokers")
    assert carrier is not None and intermediary is not None
    assert [
        r for r in marketing.responses_for_placement(conn, placement.id)
        if r.line_id == GL and r.market_org_id == carrier.id
        and r.via_org_id == intermediary.id
    ]


def test_one_new_name_in_both_boxes_is_refused_and_mints_nothing(client_and_org):
    """PAPER IS NOT REACHED THROUGH ITSELF, and the guard compares two IDS —
    so confirming the same new name in both boxes would have minted it twice,
    handed that guard two different ids, and left the book with a duplicate the
    merge tool has to clean up."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import orgs

    refused = client.post(
        _approaches_url(org, placement),
        data={"market": "Zzzz Mutual", "via": "Zzzz Mutual", "attach": "",
              "lim": "", "sent_on": "2026-08-10", "status": "pending",
              "create_market": "yes", "create_via": "yes"},
    )

    assert "not reached through itself" in refused.text, refused.text[:1200]
    # THE WHOLE BATCH ROLLED BACK, the create included.
    assert orgs.find_market(conn, "Zzzz Mutual") is None


# ===========================================================================
# THE MARKETING THAT HAS NO LINE OF COVERAGE YET
# ===========================================================================
#
# A submission with no `market_response` row is REAL MARKETING THAT HAPPENED,
# and the panel printed "No line of coverage on this placement is being
# marketed yet" straight over it — on fourteen seeded placements, four of them
# live and two quoted at $1.4M — while the workbook downloaded one header row
# and nothing under it (Grant, 2026-08-26). A broker could send that to a
# client.
#
# THESE RUN AGAINST THE SEEDED BOOK ON PURPOSE. Every check below could be
# written against a fixture built for it and would then prove nothing about the
# state the book is actually in: the defect survived a full suite for a week
# because no test asked what the real data renders as. `snapshot_db` is what
# the whole file already uses, and finding the placement by QUERY rather than
# by name is what keeps these honest as the seed changes.


def _bare_placement(conn):
    """A seeded placement carrying submissions and NOT ONE response row.

    Found by query, never named: a hard-coded ULID changes every seed and a
    hard-coded account name makes the test a statement about the fixture rather
    than about the book.
    """
    from bookkit.repo import placements

    row = conn.execute(
        "SELECT s.placement_id AS pid FROM submission s"
        " WHERE s.placement_id IS NOT NULL AND s.deleted_at IS NULL"
        "   AND s.quoted_premium IS NOT NULL"
        "   AND NOT EXISTS (SELECT 1 FROM market_response mr"
        "                   WHERE mr.submission_id = s.id AND mr.deleted_at IS NULL)"
        " LIMIT 1"
    ).fetchone()
    assert row is not None, (
        "the seeded book no longer carries a quoted submission with no response "
        "rows — this whole block is about that state and now asks nothing"
    )
    return placements.get(conn, row["pid"])


def _section_of(html: str, placement_id: str) -> str:
    """Just this placement's marketing section.

    NOT sliced at the first `</section>`: the section holds nested `<section>`
    elements (the block header's fact groups), so that cut lands inside the
    first block header and every check downstream of it passes or fails on the
    wrong text. The next `id="marketing-` is the next placement's section — the
    Program tab renders one per placement — and the band's link to this one is
    an `href="#marketing-…"`, which this token does not match.
    """
    start = html.index(f'id="marketing-{placement_id}"')
    # The SECTION's own id and not the controls inside it: the add-a-line form
    # is `marketing-<placement>-line-add` and its datalist `…-lines`, both of
    # which a plain `id="marketing-` search finds first and truncates the
    # section at.
    nxt = next(
        (
            m.start()
            for m in re.finditer(r'id="marketing-[0-9A-Za-z]+"', html)
            if m.start() > start
        ),
        len(html),
    )
    return html[start:nxt]


@pytest.fixture
def seeded(snapshot_db: Path):
    """The whole seeded book, and a placement in the state this block is
    about — not the `client_and_org` fixture, which picks the one account with
    a linked program file and would silently stop covering this the day that
    account's marketing gets answered."""
    app = create_app(snapshot_db)
    from bookkit.repo import orgs

    conn = app.state.conn
    placement = _bare_placement(conn)
    org = orgs.get(conn, placement.org_id)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org, placement


def test_a_seeded_placement_with_no_responses_renders_its_markets(seeded):
    """THE TEST THAT WOULD HAVE CAUGHT THIS A WEEK AGO.

    Four live submissions on the seeded book, and the panel said nothing on the
    placement was being marketed.
    """
    client, org, placement = seeded
    conn = client.app.state.conn
    from bookkit.repo import orgs, submissions

    packages = submissions.for_placement(conn, placement.id)
    # NAMED DEAD OR ALIVE, the reading the composer takes: a market deleted
    # from the book after it was sent a submission is still the market it went
    # to.
    named = orgs.names_for_any(conn, {p.market_org_id for p in packages})
    markets = {named[p.market_org_id] for p in packages}
    assert markets, "the fixture found a placement with no submissions"

    section = _section_of(_tab(client, org), placement.id)

    assert "No line of coverage on this placement is being marketed yet" not in section
    assert "Line of coverage not recorded" in section
    for market in markets:
        assert market in section, f"{market} was approached and is not on the panel"
    # The figures the submission itself recorded, printed rather than lost.
    quoted = next(p for p in packages if p.quoted_premium is not None)
    assert format_cents(quoted.quoted_premium) in section


def test_the_seeded_workbook_carries_the_marketing_that_has_no_line(seeded):
    """AN EMPTY WORKBOOK OVER LIVE MARKETING is the failure this exists to end.

    Driven through the DOWNLOAD ROUTE and read back out of the rendered .xlsx,
    not off `to_sections`: the composer producing rows proves nothing about
    what a client opens, and the file is what leaves the building.
    """
    import openpyxl

    client, org, placement = seeded
    conn = client.app.state.conn
    from bookkit.repo import submissions

    got = client.get(f"/accounts/{org.ref}/program/{placement.id}/export/marketing.xlsx")
    assert got.status_code == 200

    book = openpyxl.load_workbook(BytesIO(got.content))
    sheet = book.active
    rows = [
        [c for c in row if c is not None]
        for row in sheet.iter_rows(values_only=True)
        if any(row)
    ]
    printed = " | ".join(str(c) for row in rows for c in row)

    assert "Line of coverage not recorded" in printed
    quoted = next(
        p for p in submissions.for_placement(conn, placement.id)
        if p.quoted_premium is not None
    )
    assert format_cents(quoted.quoted_premium) in printed, (
        "the client workbook carries no row for a submission quoted at "
        f"{format_cents(quoted.quoted_premium)}"
    )


def test_assigning_a_line_moves_the_row_and_leaves_the_pipeline_reading_the_same(
    seeded,
):
    """The write, end to end — and the invariant that makes it safe.

    ASSIGNING IS A NO-OP DRESSED AS A WRITE. Every submission status maps to a
    response status that rolls back up to itself, the premium is the only
    priced response so it sums to itself, and the reply date is the max of one
    date — so the Pipeline reads exactly what it read before while the
    Marketing panel gains a row it can edit.
    """
    client, org, placement = seeded
    conn = client.app.state.conn
    from bookkit.repo import marketing, submissions

    package = next(
        p for p in submissions.for_placement(conn, placement.id)
        if p.quoted_premium is not None
    )
    before = submissions.get(conn, package.id)

    saved = client.post(
        f"/accounts/{org.ref}/program/{placement.id}"
        f"/marketing/submissions/{package.id}/line",
        data={"line_id": GL},
    )

    assert saved.status_code == 200
    assert saved.headers.get("HX-Retarget") == f"#marketing-{placement.id}"

    rows = marketing.responses_for_submission(conn, package.id)
    assert len(rows) == 1
    assert rows[0].line_id == GL
    assert rows[0].premium == before.quoted_premium
    assert rows[0].lim == before.quoted_limit
    assert rows[0].responded_on == before.response_on
    # The market the package was addressed to, recorded as the carrier — no
    # second org, and nothing guessed.
    assert rows[0].market_org_id == before.market_org_id

    after = submissions.get(conn, package.id)
    for field in ("status", "quoted_premium", "quoted_limit", "response_on"):
        assert getattr(after, field) == getattr(before, field), (
            f"assigning a line restated the package's {field}"
        )

    # THE ROW MOVED. It is in the line's own grid now and out of the
    # provisional block, which is the whole point of the control.
    section = _section_of(saved.text, placement.id)
    provisional_at = section.index("Line of coverage not recorded")
    lines_grid, provisional = section[:provisional_at], section[provisional_at:]
    assert "General Liability" in lines_grid
    assert f"mrow-{rows[0].id}" in lines_grid, "the row did not move into its line"
    assert package.id not in provisional, (
        "the package is still listed as having no line of coverage"
    )


def test_a_line_is_never_guessed_for_a_package(seeded):
    """EVERY SELECT RENDERS A BLANK OPTION, and picking nothing is refused in
    words rather than filed against whichever line sorted first."""
    client, org, placement = seeded
    conn = client.app.state.conn
    from bookkit.repo import marketing, submissions

    package = submissions.for_placement(conn, placement.id)[0]

    refused = client.post(
        f"/accounts/{org.ref}/program/{placement.id}"
        f"/marketing/submissions/{package.id}/line",
        data={"line_id": ""},
    )

    assert refused.status_code == 200
    # The fragment stops before the apostrophe: Jinja escapes it to `&#39;`,
    # and a test that asserts against the raw sentence fails on the punctuation
    # rather than on the behaviour.
    assert "pick the line of coverage this market" in refused.text
    assert "never guessed" in refused.text
    assert not marketing.responses_for_submission(conn, package.id)


def test_the_assign_picker_offers_only_what_it_can_store(seeded):
    """The markup constrains a mouse and nothing else. A line of coverage this
    book does not carry is refused server-side, by the picker's OWN options —
    re-queried on the POST from the one function that rendered them."""
    client, org, placement = seeded
    conn = client.app.state.conn
    from bookkit.repo import marketing, submissions

    package = submissions.for_placement(conn, placement.id)[0]

    refused = client.post(
        f"/accounts/{org.ref}/program/{placement.id}"
        f"/marketing/submissions/{package.id}/line",
        data={"line_id": "not-a-line-this-book-carries"},
    )

    assert refused.status_code == 200
    assert not marketing.responses_for_placement(conn, placement.id)
    # THE PICKER'S OWN REFUSAL, not a foreign key crashing into the house
    # fallback. Without `checked_option` the write still fails — the FK holds
    # underneath — and the broker is told "that could not be saved and nothing
    # was written", which is the sentence `_house` exists as a LAST resort. A
    # guard whose only effect is the message is still the guard: the message is
    # what a person can act on.
    assert "is not one of the choices offered" in refused.text
    assert "General Liability" in refused.text, (
        "the refusal does not name what the picker offers"
    )


def test_a_withdrawn_package_is_refused_in_words_and_not_in_silence(seeded):
    """A REFUSAL SAYS SOMETHING, on the row that has no control.

    The withdrawn row renders no picker — an affordance that only ever answers
    no is worse than none — but the POST behind it is still reachable from a
    stale tab and from anything that can post, and the message used to be
    rendered INSIDE the picker branch. So the write was correctly refused and
    the browser was told nothing at all, which reads as a broken app (found by
    driving it, 2026-08-26).
    """
    client, org, placement = seeded
    conn = client.app.state.conn
    from bookkit.repo import marketing, submissions

    package = submissions.for_placement(conn, placement.id)[0]
    submissions.update(conn, package.id, status="withdrawn")

    refused = client.post(
        f"/accounts/{org.ref}/program/{placement.id}"
        f"/marketing/submissions/{package.id}/line",
        data={"line_id": GL},
    )

    assert refused.status_code == 200
    assert "this package was withdrawn" in refused.text
    assert not marketing.responses_for_submission(conn, package.id)
    # And the row keeps its place in the report: the marketing happened.
    section = _section_of(refused.text, placement.id)
    assert "withdrawn — not assigned to a line" in section


def test_a_provisional_row_offers_no_cell_to_edit(seeded):
    """PRINTED, NEVER EDITABLE. These figures live on `submission`, which is a
    CACHE of the response rows everywhere else in this app — a cell writing to
    a cache is the second home the roll-up exists to close. The one thing that
    can be done to one of these rows is to give it its line of coverage."""
    client, org, placement = seeded

    section = _section_of(_tab(client, org), placement.id)
    provisional = section[section.index("Line of coverage not recorded") :]

    assert "assign a line of coverage" in provisional
    assert "/marketing/responses/" not in provisional, (
        "a provisional row offers a cell that would post to a market response "
        "that does not exist"
    )


# --- and the Undo button on the rail beside it ------------------------------


def test_undo_on_the_rail_leaves_the_panel_and_the_pipeline_saying_the_same_thing(
    client_and_org,
):
    """THE BROWSER'S OWN UNDO, end to end — no force, no conflict, no stale
    tab.

    A revert replays a batch's events backwards, and the submission's six
    marketing columns were logged like any other field, so it restored the
    figure the cache HELD rather than recomputing it from the rows that
    survive. Recording the second market's answer moves no cached column (the
    package already rolled up to 'quoted'), so `plan_revert`'s guard is happy
    and the revert applies — leaving the Pipeline counting "quotes in hand 0"
    beside a Marketing panel printing the same market as Quoted, four inches
    apart (Grant, 2026-08-26).

    Driven through the ROUTES on both ends deliberately: the fix lives in
    services/batches.py, and a green service test says nothing about whether
    the browser's Revert link reaches it."""
    from bookkit.repo import batches as batches_repo
    from bookkit.repo import marketing, submissions

    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    market = _market(conn, "Chubb", best="A++")

    gl = _approach(conn, placement.id, market, GL)
    prop = marketing.create_response(
        conn, gl.submission_id, "property", market_org_id=market.id
    )

    def save_status(response) -> str:
        posted = client.post(
            _cell_url(org, placement, response, "status"), data={"status": "quoted"}
        )
        assert posted.status_code == 200, posted.text
        return batches_repo.recent(conn, since="", limit=1)[0].ref

    first = save_status(prop)
    save_status(gl)

    undone = client.post(
        f"/accounts/{org.ref}/changes/{first}/revert?tab=program"
    )
    assert undone.status_code == 204
    assert "outcome=reverted" in undone.headers["HX-Redirect"], undone.headers

    rows = {
        r.line_id: r.status
        for r in marketing.responses_for_submission(conn, gl.submission_id)
    }
    assert rows == {GL: "quoted", "property": "pending"}, rows

    fresh = submissions.get(conn, gl.submission_id)
    assert str(fresh.status) == "quoted", (
        "the Pipeline would say 'out at market' about a package with a live quote"
    )

    # the rule itself: re-deriving changes nothing, over all six columns
    derived = ("status", "quoted_premium", "quoted_limit", "response_on",
               "quote_expires_on", "decline_reason")
    before = conn.execute(
        "SELECT * FROM submission WHERE id = ?", (gl.submission_id,)
    ).fetchone()
    marketing.roll_up_submission(conn, gl.submission_id)
    after = conn.execute(
        "SELECT * FROM submission WHERE id = ?", (gl.submission_id,)
    ).fetchone()
    assert {f: before[f] for f in derived} == {f: after[f] for f in derived}


# --- how we reached the paper, corrected where it prints -------------------
#
# THE ACCESS POINT HAD NO FIX ON ANY SURFACE (Grant, 2026-08-26: "no way to
# update the access point … to turn back to a direct approach if needed to
# clean up"). It is the one fact on an approach a broker cannot know until the
# submission actually goes out — you record RT Specialty, and it goes direct —
# and it was not a cell, not a form field and not an MCP argument.


def _access(client, org, placement, response, typed: str):
    return client.post(
        _cell_url(org, placement, response, "via_org_id"),
        data={"via_org_id": typed},
    )


def test_an_access_point_is_cleared_back_to_a_direct_approach(client_and_org):
    """THE CORRECTION THE COLUMN EXISTS FOR. Emptying the cell records a direct
    approach — blank is an ANSWER here, not an unset value, which is why the
    display prints the word `direct` and the editor still pre-fills empty."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    carrier = _market(conn, "Zurich")
    wholesaler = _market(conn, "RT Specialty")
    response = _approach(conn, placement.id, carrier, via_org_id=wholesaler.id)

    got = _access(client, org, placement, response, "")

    assert got.status_code == 200
    assert marketing.get_response(conn, response.id).via_org_id is None
    assert "direct" in got.text, "a cleared access point must say so in words"


def test_an_access_point_is_named_and_changed_where_it_prints(client_and_org):
    """A NAME IN, AN ID OUT — the same resolution the add row two rows down
    makes, over the same vocabulary."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    carrier = _market(conn, "Zurich")
    amwins = _market(conn, "Amwins Brokerage")
    response = _approach(conn, placement.id, carrier)
    assert response.via_org_id is None

    got = _access(client, org, placement, response, "Amwins Brokerage")

    assert got.status_code == 200
    assert marketing.get_response(conn, response.id).via_org_id == amwins.id


def test_an_access_point_the_book_does_not_carry_is_refused_and_nothing_written(
    client_and_org,
):
    """A MISS IS REFUSED, NOT MINTED. `via_org_id` is a foreign key with no
    freeform half to degrade to (unlike an assignee), and a cell that created a
    market as a side effect of a correction is the wrong write the missing-verb
    rule is about. The refusal names the nearest, says blank means direct, and
    names the door that makes one."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing

    carrier = _market(conn, "Zurich")
    _market(conn, "RT Specialty")
    response = _approach(conn, placement.id, carrier)
    before = _events(conn, response.id)

    got = _access(client, org, placement, response, "RT Speciallty Grp")

    assert got.status_code == 200
    assert "no market on this book is called" in got.text
    assert "RT Specialty" in got.text, "the refusal must name the nearest match"
    assert "direct approach" in got.text, "blank means direct — say so"
    assert marketing.get_response(conn, response.id).via_org_id is None
    assert _events(conn, response.id) == before, "a refused save wrote something"


def test_the_access_cell_never_prints_the_id_it_stores(client_and_org):
    """THE ONE EDITABLE KEY STORED AS AN ID AND PRINTED AS A NAME. The generic
    display path would fall through to `str(value)` and put a ULID in a cell a
    broker reads, which looks like data rather than like a bug."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)

    carrier = _market(conn, "Zurich")
    wholesaler = _market(conn, "RT Specialty")
    _approach(conn, placement.id, carrier, via_org_id=wholesaler.id)

    html = _tab(client, org)

    assert wholesaler.id not in html
    assert "RT Specialty" in html


def test_correcting_the_access_point_answers_with_the_whole_block(client_and_org):
    """IT MOVES THE CLEARANCE STRIP, which lives in the BLOCK header above the
    row. A conflict is the same carrier reached twice on one line through
    DIFFERENT intermediaries, so making a wholesaler approach direct either
    raises the warning or takes it away — and a row-sized answer would leave
    the strip standing over a conflict that no longer exists."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)

    carrier = _market(conn, "Zurich")
    wholesaler = _market(conn, "RT Specialty")
    direct = _approach(conn, placement.id, carrier, status="pending")
    through = _approach(
        conn, placement.id, carrier, via_org_id=wholesaler.id, status="pending"
    )
    assert "clearance" in _tab(client, org)

    got = _access(client, org, placement, through, "")

    assert got.status_code == 200
    assert got.headers["HX-Retarget"].startswith("#mblock-"), (
        "the access cell must answer with the block that carries the strip"
    )
    assert "clearance" not in got.text, (
        "the very act that resolves the conflict left the warning standing"
    )
    assert direct.id  # both rows survive; only the route to the paper changed


def test_the_access_editor_prefills_the_name_and_offers_the_book(client_and_org):
    """WHAT A FORM PRE-FILLS MUST BE SOMETHING ITS OWN PARSER ACCEPTS BACK.
    The editor holds the NAME — the id is what is stored, and a ULID in the box
    is unsaveable without retyping the whole thing — and it carries the book's
    own market list as completion, which is the same anti-drift rule the add
    row two rows down follows over the same vocabulary."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)

    carrier = _market(conn, "Zurich")
    wholesaler = _market(conn, "RT Specialty")
    response = _approach(conn, placement.id, carrier, via_org_id=wholesaler.id)

    got = client.get(_cell_url(org, placement, response, "via_org_id") + "/edit")

    assert got.status_code == 200
    assert 'value="RT Specialty"' in got.text
    assert wholesaler.id not in got.text
    assert "<datalist" in got.text, "the access editor lost its completion list"


def test_the_access_display_cell_prints_direct_not_an_id(client_and_org):
    """The display half of the same contract, fetched on Escape and after a
    revert: `via_org_id` is the ONE editable key stored as an id, and the
    generic path would fall through to `str(value)`."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)

    carrier = _market(conn, "Zurich")
    response = _approach(conn, placement.id, carrier)

    got = client.get(_cell_url(org, placement, response, "via_org_id"))

    assert got.status_code == 200
    assert "direct" in got.text


# --- the grid is 22 columns and does not fit -------------------------------
#
# Measured on the running app (2026-08-26, 1600px window): the table asks for
# 1811px and gets 1064px, so 41% of it is off the right-hand edge. The columns
# are what the client's workbook prints and thinning them is not on the table,
# so the two columns that say WHOSE row it is pin to the left edge instead.


def test_the_two_columns_that_name_the_row_are_pinned(client_and_org):
    """MARKET AND ACCESS PIN, header and body cells alike. A pinned body cell
    under an unpinned header slides out from under its own label the moment the
    grid is scrolled sideways, so both halves carry the same class."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)

    carrier = _market(conn, "Zurich")
    wholesaler = _market(conn, "RT Specialty")
    _approach(conn, placement.id, carrier, via_org_id=wholesaler.id)

    html = _tab(client, org)

    assert 'class="num pin pin-1"' not in html, "Market is prose, not numeric"
    assert "pin pin-1" in html and "pin pin-2" in html
    # BOTH HALVES. Counting rather than asserting presence, because a header
    # that pinned and a body that did not would still satisfy `in`.
    assert html.count("pin pin-1") >= 2
    assert html.count("pin pin-2") >= 2


def test_a_pinned_column_is_one_of_the_leftmost(client_and_org):
    """THE `left` OFFSETS ONLY MEAN SOMETHING IN ORDER. `pin-1` sits at 0 and
    `pin-2` at the width of `pin-1`, so a column pinned from the middle of the
    tuple would be positioned over columns it does not follow — and nothing in
    the stylesheet could detect it. The rule is checked here rather than
    written in a comment nobody re-reads."""
    from bookkit.web import marketing_grid

    pinned = [c for c in marketing_grid.COLUMNS if c.pin]
    assert [c.pin for c in pinned] == list(range(1, len(pinned) + 1)), (
        "pins must be numbered 1..N with no gaps — the CSS positions each one "
        "against the one before it"
    )
    assert marketing_grid.COLUMNS[: len(pinned)] == tuple(pinned), (
        "a pinned column must be among the leftmost columns, in pin order — "
        "pinning one from the middle of the tuple lays it over columns it "
        "does not follow"
    )
    # And the header cells say so, off the same declaration the body cells use.
    assert pinned[0].th_class == "pin pin-1"


# --- the order a reader asked for ------------------------------------------


def _sorted_html(client, org, placement, spec: str) -> str:
    got = client.get(
        f"/accounts/{org.ref}/program/{placement.id}/marketing/sort?sort={spec}"
    )
    assert got.status_code == 200
    return got.text


def _market_order(html: str, block_id: str) -> list[str]:
    """The Market cells of one block, top to bottom, off the rendered HTML.

    The FIRST `<td>` of each `marketing-row`: the Market column is printed
    rather than editable (`Column.field is None`), so it carries no
    `data-field` to match on — which is itself the thing that makes the read
    unambiguous."""
    block = html.split(f'id="{block_id}"', 1)[1].split("</article>", 1)[0]
    body = block.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    out = []
    for row in body.split('<tr id="mrow-')[1:]:
        cell = re.search(r"<td[^>]*>(.*?)</td>", row, re.S)
        if cell:
            out.append(re.sub(r"<[^>]+>", "", cell.group(1)).strip())
    return out


def _gl_block(placement) -> str:
    from bookkit.web.marketing_grid import block_id

    return block_id(placement.id, GL)


def _priced(client_and_org):
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    for name, premium in (("Zurich", 30_000_00), ("Chubb", 10_000_00), ("Berkley", 20_000_00)):
        _approach(conn, placement.id, _market(conn, name), status="quoted",
                  premium=premium)
    return client, org, placement


def test_the_grid_reads_live_first_until_somebody_asks_otherwise(client_and_org):
    """THE DEFAULT IS A REAL ORDER WITH A REASON — live options first, then
    cheapest — and it is what the client's workbook prints."""
    client, org, placement = _priced(client_and_org)

    order = _market_order(_tab(client, org), _gl_block(placement))

    assert order == ["Chubb", "Berkley", "Zurich"], "cheapest quote leads"


def test_a_header_click_orders_the_block(client_and_org):
    client, org, placement = _priced(client_and_org)

    html = _sorted_html(client, org, placement, f"{GL}:premium:desc")

    assert _market_order(html, _gl_block(placement)) == ["Zurich", "Berkley", "Chubb"]


def test_an_unknown_figure_is_last_in_both_directions(client_and_org):
    """NULL IS "NOBODY HAS TOLD US" — neither the smallest premium nor the
    largest. A plain `reverse=True` would flip it from the bottom of the
    ascending sort to the TOP of the descending one, putting the rows carrying
    no answer above every quote in hand."""
    client, org, placement = _priced(client_and_org)
    conn = client.app.state.conn
    _approach(conn, placement.id, _market(conn, "Hartford"), status="pending")

    for direction in ("asc", "desc"):
        order = _market_order(
            _sorted_html(client, org, placement, f"{GL}:premium:{direction}"),
            _gl_block(placement),
        )
        assert order[-1] == "Hartford", f"the unpriced row must sit last, {direction}"


def test_sorting_by_status_uses_the_reports_own_rank(client_and_org):
    """NEVER ALPHABETICAL. A-Z puts "Bound" above "Quoted" by accident and
    "Non-response" above "Pending" against every reading of the word; what a
    broker means by sorting on status is live first, closed last."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    for name, status in (
        ("Zurich", "declined"), ("Chubb", "bound"), ("Berkley", "pending")
    ):
        _approach(conn, placement.id, _market(conn, name), status=status)

    order = _market_order(
        _sorted_html(client, org, placement, f"{GL}:status:asc"), _gl_block(placement)
    )

    assert order == ["Chubb", "Berkley", "Zurich"]


def test_a_third_click_puts_the_default_back(client_and_org):
    """THE WAY BACK, not a fourth state. A reader who sorted to answer one
    question has to be able to restore the order the workbook prints without
    reloading the page."""
    from bookkit.web.marketing_grid import cycled, format_sorts

    assert format_sorts(cycled({}, GL, "premium")) == f"{GL}:premium:asc"
    assert format_sorts(cycled({GL: ("premium", False)}, GL, "premium")) == (
        f"{GL}:premium:desc"
    )
    assert format_sorts(cycled({GL: ("premium", True)}, GL, "premium")) == ""


def test_a_sort_survives_a_cell_save(client_and_org):
    """THE ONE THAT MATTERED. Four cells answer with the whole block and the
    Sent cell answers with the whole SECTION, so a sort held anywhere but on
    the section is thrown away by a write in a different block — a view
    silently resetting, which reads as broken."""
    client, org, placement = _priced(client_and_org)
    conn = client.app.state.conn
    from bookkit.repo import marketing as mrepo

    rows = [r for r in mrepo.responses_for_placement(conn, placement.id)
            if r.line_id == GL]

    # `status` is a block cell for its OWN reasons (it moves the clearance
    # strip and decides which row leads the bridge) and is NOT the column the
    # grid is sorted by — so what this measures is the sort surviving somebody
    # else's write, and not the re-order rule one test down.
    saved = client.post(
        _cell_url(org, placement, rows[0], "status"),
        data={"status": "indicated", "sort": f"{GL}:premium:desc"},
    )

    assert saved.status_code == 200
    assert _market_order(saved.text, _gl_block(placement)) == [
        "Zurich", "Berkley", "Chubb"
    ], "the block came back in the composer's default order, losing the sort"


def test_editing_the_sorted_column_re_orders_the_block(client_and_org):
    """THE FIFTH BLOCK CELL, AND IT IS NOT A FIXED ONE. With the grid ordered by
    premium, typing a premium that belongs three rows up and getting a
    row-sized answer leaves the row where it was, in a column that visibly is
    not in order — the grid lying about the one thing it was asked to do."""
    client, org, placement = _priced(client_and_org)
    conn = client.app.state.conn
    from bookkit.repo import marketing as mrepo

    rows = [r for r in mrepo.responses_for_placement(conn, placement.id)
            if r.line_id == GL]
    cheapest = min(rows, key=lambda r: r.premium or 0)

    saved = client.post(
        _cell_url(org, placement, cheapest, "responded_on"),
        data={"responded_on": "2026-08-10", "sort": f"{GL}:responded_on:desc"},
    )

    assert saved.status_code == 200
    assert saved.headers["HX-Retarget"] == f"#{_gl_block(placement)}", (
        "a save to the column the block is SORTED by must answer with the block"
    )


def test_a_sort_naming_a_column_the_grid_cannot_order_is_dropped(client_and_org):
    """REACHABLE FROM A URL, so anything can send anything. The parser drops
    what it cannot read and the panel re-formats from what it APPLIED, so the
    worst a hand-typed spec does is render the default order while saying so."""
    client, org, placement = _priced(client_and_org)

    html = _sorted_html(client, org, placement, f"{GL}:rate_move:asc")

    assert _market_order(html, _gl_block(placement)) == ["Chubb", "Berkley", "Zurich"]
    assert "rate_move:asc" not in html, "the page claimed an order it is not in"


def test_a_sortable_header_says_so_to_a_screen_reader(client_and_org):
    client, org, placement = _priced(client_and_org)

    plain = _tab(client, org)
    sorted_html = _sorted_html(client, org, placement, f"{GL}:premium:desc")

    assert 'aria-sort="none"' in plain, "a sortable-but-unsorted column says none"
    assert 'aria-sort="descending"' in sorted_html
    # The prose columns the composer cannot order carry no aria-sort at all —
    # "cannot be sorted" and "is not sorted" are two different claims.
    assert plain.count("col-sort") > 0


# --- a row recorded in error ------------------------------------------------


def test_removing_a_row_asks_first_and_writes_nothing(client_and_org):
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    response = _approach(conn, placement.id, _market(conn, "Zurich"))
    before = _events(conn, response.id)

    got = client.get(_cell_url(org, placement, response, "x").rsplit("/cell/", 1)[0]
                     + "/remove")

    assert got.status_code == 200
    assert "remove" in got.text.lower()
    assert _events(conn, response.id) == before, "the confirm wrote something"


def test_removing_a_row_takes_it_off_the_grid(client_and_org):
    """The ordinary case: the package carries another answer, so the approach
    survives and only this line's grid loses a row."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    from bookkit.repo import marketing as mrepo

    gone = _approach(conn, placement.id, _market(conn, "Zurich"))
    # A SECOND ANSWER ON THE SAME PACKAGE — a market is approached once with a
    # whole submission and answers line by line, which is the ordinary shape.
    kept = mrepo.create_response(
        conn, gone.submission_id, AUTO, market_org_id=gone.market_org_id
    )
    base = _cell_url(org, placement, gone, "x").rsplit("/cell/", 1)[0]

    done = client.post(base + "/remove")

    assert done.status_code == 200
    assert done.headers["HX-Retarget"] == f"#marketing-{placement.id}"
    live = [r.id for r in mrepo.responses_for_placement(conn, placement.id)]
    assert live == [kept.id]
    # AND THE BLOCK IS GONE WITH IT, because that line of coverage now has
    # nothing recorded against it and no expectation row either — the
    # composer's own rule, and the second reason a removal cannot answer with
    # a block: there would be no block to answer with.
    assert _gl_block(placement) not in done.text
    # Zurich itself is still on the page, correctly: the same package answered
    # on Auto and that row was not touched. What is gone is the GL answer.
    from bookkit.web.marketing_grid import block_id

    assert block_id(placement.id, AUTO) in done.text


def test_removing_the_last_answer_shows_the_approach_where_it_lands(client_and_org):
    """THE ANSWER KEEPS THE PROMISE THE CONFIRM MADE. The approach moves into
    the provisional block — a SIBLING of the one whose row just went — so a
    block-sized answer left the reader looking at a row that had gone and a
    promise that had not been kept until they reloaded."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    only = _approach(conn, placement.id, _market(conn, "Hanover"))
    base = _cell_url(org, placement, only, "x").rsplit("/cell/", 1)[0]

    done = client.post(base + "/remove")

    assert done.status_code == 200
    assert done.headers["HX-Retarget"] == f"#marketing-{placement.id}", (
        "the approach moved to a different block, so the section is the answer"
    )
    assert "Line of coverage not recorded" in done.text
    assert "Hanover" in done.text.split("marketing-provisional", 1)[1]


def test_the_confirm_says_what_becomes_of_the_approach(client_and_org):
    """THE ONE FACT A BROKER CANNOT WORK OUT FOR THEMSELVES. Removing the last
    answer leaves the package under "line of coverage not recorded" — it did
    go out — and a control whose consequence is only discoverable by undoing it
    is one nobody trusts."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    only = _approach(conn, placement.id, _market(conn, "Zurich"))
    base = _cell_url(org, placement, only, "x").rsplit("/cell/", 1)[0]

    got = client.get(base + "/remove")

    assert "only answer recorded against that approach" in got.text


def test_the_confirm_stands_in_for_the_row_it_replaced(client_and_org):
    """IT CARRIES THE ROW'S OWN id. Every row-sized answer in this module says
    `HX-Retarget: #mrow-<id>`, so a confirm without that id leaves [keep]
    fetching a row that lands nowhere — the question just sits there looking
    ignored (found by clicking it in a browser, 2026-08-26)."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    response = _approach(conn, placement.id, _market(conn, "Zurich"))
    base = _cell_url(org, placement, response, "x").rsplit("/cell/", 1)[0]

    confirm = client.get(base + "/remove")

    assert f'id="mrow-{response.id}"' in confirm.text


def test_keeping_a_row_costs_one_row(client_and_org):
    """Backing out of the confirm re-fetches the ROW, not the section — the
    same reason a participation's [keep] does."""
    client, org = client_and_org
    conn = client.app.state.conn
    placement = _linked(client, org)
    response = _approach(conn, placement.id, _market(conn, "Zurich"))
    base = _cell_url(org, placement, response, "x").rsplit("/cell/", 1)[0]

    got = client.get(base + "/row")

    assert got.status_code == 200
    assert got.headers["HX-Retarget"] == f"#mrow-{response.id}"


def test_a_sorted_header_offers_the_NEXT_order_not_the_one_it_is_in(client_and_org):
    """THE SECOND CLICK. The section publishes the CURRENT order as an
    inherited `hx-vals` so every write round-trips the sort, and htmx builds a
    GET's query string from that inherited value — which silently overwrote a
    `?sort=` the button had put in its own URL. Premium sorted ascending and
    then never moved again, however many times it was clicked (found in a
    browser, 2026-08-26).

    So the next order rides in the BUTTON's own `hx-vals`, which overrides an
    ancestor's for the same key, and the button's URL carries no `sort` at all
    — the two would be the same collision wearing a different hat.
    """
    client, org, placement = _priced(client_and_org)

    html = _sorted_html(client, org, placement, f"{GL}:premium:asc")

    block = html.split(f'id="{_gl_block(placement)}"', 1)[1].split("</thead>", 1)[0]
    button = next(
        b for b in block.split("<button")[1:] if ">Premium" in b.split("</button>")[0]
    )
    # UNESCAPED, because Jinja escapes the quotes inside the attribute and the
    # browser hands htmx the decoded value back — reading the raw source here
    # would be testing the escaping rather than the contract.
    from html import unescape

    assert f'{{"sort": "{GL}:premium:desc"}}' in unescape(button), (
        "an ascending column must offer descending, not the order it is in"
    )
    url = re.search(r'hx-get="([^"]*)"', button).group(1)
    assert "sort=" not in url, (
        "a `sort` in the button's URL is shadowed by the section's inherited "
        "hx-vals — the collision this test exists for"
    )


def test_the_section_publishes_the_order_it_is_actually_in(client_and_org):
    """The other side of the same contract: the SECTION says where the grid IS
    (so a cell save carries it) and the HEADER says where it is going."""
    client, org, placement = _priced(client_and_org)

    plain = _tab(client, org)
    sorted_html = _sorted_html(client, org, placement, f"{GL}:premium:desc")

    assert "hx-vals" not in plain.split('<section class="marketing"')[1][:400], (
        "an unsorted section must not publish an order at all"
    )
    section = sorted_html.split('<section class="marketing"', 1)[1][:400]
    assert f'{GL}:premium:desc' in section
