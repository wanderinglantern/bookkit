"""The account page. The renewal-date assertion is named after the bug: Today,
Book, the account header and the calendar all printed placement.period_to
beside a countdown computed from renewal_on, so a date twenty days in the
future rendered red as '70d over'. The header badge and the right rail's
snapshot row are what carry that invariant now that the renewal rail is
gone (Grant, 2026-08-17)."""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app


def _snapshot_value(html: str, label: str) -> str:
    """The value rendered against ONE snapshot label. A bare `x in html` check
    cannot tell `program premium` from `bound premium` when both are money on
    the same panel — which is the whole thing the tower rows must not get
    wrong."""
    match = re.search(
        rf'<span class="snapshot-label">{re.escape(label)}</span>\s*'
        rf'<span class="snapshot-value[^"]*"[^>]*>([^<]*)</span>',
        html,
    )
    assert match, f"no snapshot row labelled {label!r} in response"
    return match.group(1)


def _snapshot_labels(html: str) -> list[str]:
    """Every snapshot row label in the rail, in render order."""
    return re.findall(r'<span class="snapshot-label">([^<]*)</span>', html)


def _scope_group(html: str) -> str:
    """The program-scoped bracket, whole — caption and rows.

    Depth-counted rather than regexed: the group holds `div`s, so a non-greedy
    match stops at the first row's closing tag and would call a group of one
    row "the whole group"."""
    start = html.index('<div class="snapshot-scope-group">')
    depth = 0
    for match in re.finditer(r"<div\b|</div>", html[start:]):
        depth += 1 if match.group().startswith("<div") else -1
        if depth == 0:
            return html[start : start + match.end()]
    raise AssertionError("unclosed snapshot-scope-group")


def _tab_badge(html: str, label: str) -> str:
    match = re.search(rf"{label}\s*<span class=\"tab-badge\">(\d+)</span>", html)
    assert match, f"tab {label!r} badge not found in response"
    return match.group(1)


@pytest.fixture
def app_and_org(snapshot_db: Path):
    app = create_app(snapshot_db)
    from bookkit.repo import orgs
    from bookkit.services import renewals

    with TestClient(app) as client:
        conn = app.state.conn
        clients = orgs.list_orgs(conn, kind="client")
        # The renewal test only has teeth if the picked account has a live
        # renewal where renewal_on != period_to — otherwise the assertion
        # passes no matter what the header prints and protects nothing.
        org = next(
            (
                o for o in clients
                if (item := renewals.next_for_org(conn, o.id)) is not None
                and item.renewal_on != item.placement.period_to
            ),
            None,
        )
        assert org is not None, (
            "no seeded client has a live renewal where renewal_on != "
            "period_to — the renewal-date test would be worthless"
        )
        yield client, org


def test_account_root_redirects_to_relationship(app_and_org):
    """Relationship is the default tab (Grant, 2026-08-17) — Program, Work
    and Pipeline all need writes this task doesn't build yet."""
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"].endswith("/relationship")


def test_relationship_names_the_account(app_and_org):
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert response.status_code == 200
    assert org.name in response.text


def test_unknown_account_is_404(app_and_org):
    client, _ = app_and_org
    assert client.get("/accounts/nope-does-not-exist/relationship").status_code == 404


def test_unknown_tab_is_404(app_and_org):
    client, org = app_and_org
    assert client.get(f"/accounts/{org.ref}/bogus-tab").status_code == 404


def test_header_prints_the_date_it_counts_to(app_and_org):
    """THE RENEWAL DATE IS RenewalItem.renewal_on, never placement.period_to.
    Print the same date you count to, or a future date renders as overdue.
    This is the smoke-test version against the real seeded fixture; the
    parametrized test below (`test_header_count_and_date_come_from_one_
    renewal_item`) is the one with teeth on the badge and on the exact day
    count — the seed has no account that is both divergent AND overdue, so
    that test uses a synthetic RenewalItem instead of fighting the fixture."""
    client, org = app_and_org
    from bookkit.services import renewals

    item = renewals.next_for_org(client.app.state.conn, org.id)
    assert item is not None, "fixture guarantees a live renewal"
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert item.renewal_on in response.text
    assert item.placement.period_to not in response.text or \
        item.placement.period_to == item.renewal_on


def test_overdue_badge_absent_on_the_seeded_non_overdue_fixture(app_and_org):
    """The seeded fixture (ACC-0004) is never overdue (days_remaining=38) —
    this only pins the negative case against real data. The positive case
    (badge present, exact count, boundary at 0) is covered by the
    synthetic-RenewalItem test below, because the seed cannot produce an
    account that is both overdue AND has renewal_on != period_to."""
    client, org = app_and_org
    from bookkit.services import renewals

    item = renewals.next_for_org(client.app.state.conn, org.id)
    assert item is not None
    assert item.days_remaining >= 0, "fixture is expected to be non-overdue"
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert "badge-overdue" not in response.text


@pytest.mark.parametrize(
    "days_remaining,renewal_on",
    [(-345, "2025-09-03"), (38, "2026-09-24"), (0, "2026-08-14")],
    ids=["overdue", "upcoming", "boundary-zero"],
)
def test_header_count_and_date_come_from_one_renewal_item(
    app_and_org, monkeypatch, days_remaining, renewal_on
):
    """The four-surface bug was printing period_to beside a count computed
    from renewal_on. Both the badge and the snapshot row must carry THIS
    item's date and THIS item's count — including the number, not just its
    sign. The seed can't give an account that is both overdue and
    date-divergent (only ACC-0004 diverges, and it's never overdue), so this
    builds a synthetic RenewalItem with `renewal_on`/`days_remaining`
    deliberately inconsistent with the real placement's `period_to`,
    monkeypatches `next_for_org` to return it, and checks the rendered page."""
    client, org = app_and_org
    from bookkit.services import renewals as renewals_service

    conn = client.app.state.conn
    real_item = renewals_service.next_for_org(conn, org.id)
    assert real_item is not None
    fake_item = replace(real_item, renewal_on=renewal_on, days_remaining=days_remaining)
    assert fake_item.placement.period_to != renewal_on, (
        "fixture's period_to must differ from the synthetic renewal_on or "
        "this test can't distinguish the two"
    )

    monkeypatch.setattr(
        "bookkit.web.routes.account.renewals.next_for_org",
        lambda conn, org_id: fake_item,
    )

    response = client.get(f"/accounts/{org.ref}/relationship")
    assert response.status_code == 200
    assert fake_item.placement.period_to not in response.text

    count = abs(days_remaining)
    if days_remaining < 0:
        assert f"renewal {count}d overdue" in response.text
        assert "badge-overdue" in response.text
        # The ◆ sits in its own span so that ONE glyph renders from the
        # vendored JetBrains Mono; Noto Sans' cmap has no geometric glyph at
        # all, so unwrapped it came from whatever the OS substituted.
        assert '<span class="badge-glyph">◆</span>' in response.text
        suffix = f"{count}d over"
    else:
        assert "badge-overdue" not in response.text
        assert "badge-glyph" not in response.text
        assert f"renewal {count}d overdue" not in response.text
        suffix = f"{count}d"
    # the snapshot row: exact date AND exact count, from the same item
    assert f"{renewal_on} · {suffix}" in response.text


def test_no_renewal_rail_markup_remains(app_and_org):
    """The renewal rail is gone (Grant, 2026-08-17) — replaced by the header
    badge and the snapshot's 'next renewal' row."""
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/relationship")
    for stale_class in ("rail-track", "rail-marker", "rail-scale", "rail-overrun"):
        assert stale_class not in response.text


def test_change_time_shows_time_for_today_and_date_otherwise():
    """A change from three days ago must not read as if it just happened."""
    from bookkit.web.routes.account import _change_time

    today = date(2026, 8, 14)
    assert _change_time("2026-08-14T09:12:00+00:00", today) == "09:12"
    assert _change_time("2026-08-11T16:30:00+00:00", today) == "2026-08-11"


def test_four_tabs_render_with_real_counts(app_and_org):
    client, org = app_and_org
    from bookkit.repo import contacts, interactions, placements

    conn = client.app.state.conn
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert response.status_code == 200

    expected_program = len(placements.for_org(conn, org.id))
    expected_relationship = (
        len(contacts.for_org(conn, org.id)) + len(interactions.for_org(conn, org.id, limit=200))
    )
    assert _tab_badge(response.text, "Program") == str(expected_program)
    assert _tab_badge(response.text, "Relationship") == str(expected_relationship)
    # Work and Pipeline pull in project needs / RFI requests / submissions —
    # just confirm they're real (non-negative integers), not that a route
    # exists that never wired counts at all.
    assert _tab_badge(response.text, "Work").isdigit()
    assert _tab_badge(response.text, "Pipeline").isdigit()


@pytest.mark.parametrize(
    "tab,heading_text",
    [
        ("program", "empty — add the first row"),
        ("relationship", "empty — add the first row"),
        ("work", "no open tasks — add one"),
        ("pipeline", "empty — add the first row"),
    ],
)
def test_each_tab_renders_its_empty_state_and_marks_itself_current(app_and_org, tab, heading_text):
    client, org = app_and_org
    if tab == "work":
        # Work tab (Tasks 11-13) now renders real open tasks and requests —
        # seed.py seeds ~25 tasks at random across client orgs (and never
        # seeds any RFI request), so this org's own tasks have to be cleared
        # for its empty state to be the thing actually under test here,
        # rather than accidentally passing only when the random draw missed
        # this org.
        from bookkit.repo import tasks as tasks_repo

        conn = client.app.state.conn
        for t in tasks_repo.open_tasks_for_client(conn, org.id):
            tasks_repo.complete(conn, t.id)
    response = client.get(f"/accounts/{org.ref}/{tab}")
    assert response.status_code == 200
    assert heading_text in response.text
    assert f'href="/accounts/{org.ref}/{tab}" class="tab is-current"' in response.text


def test_right_rail_sections_present(app_and_org):
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/relationship")
    for heading in ("Snapshot", "Team", "Documents", "Recent changes"):
        assert heading in response.text, f"missing right-rail section: {heading}"


def test_documents_empty_state_copy(app_and_org):
    """The design source (Account View.dc.html) wins over the visual-direction
    spec's paraphrase where they disagree — its copy is 'No documents yet'
    (capital N), not the spec doc's lowercase rendering."""
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert "No documents yet" in response.text
    assert "Drop a binder, loss run or SOV here — BookKit records the path, not the file." \
        in response.text


# --- the tower rows (Task 17) ------------------------------------------------
# `bound premium` is summed over EVERY bound placement on the account.
# `program premium`, `top of tower` and `unplaced` describe ONE program — the
# one behind the `next renewal` row. Money columns say whose money: the book's
# per-account figure was once whichever placement renewed next, which showed
# revenue that did not exist. These tests exist to keep the two scopes apart.


@pytest.fixture
def divergent_tower(app_and_org):
    """An account whose next renewal IS file-linked and whose account-wide
    bound premium DIFFERS from that program's own premium.

    No seeded account is both, so this builds one. Only three seeded
    placements carry a program file at all (PLC-0001, PLC-0006, PLC-0028),
    and PLC-0006 is the only one its account's next renewal actually picks —
    Delta Marine's single bound placement, so its program premium and its
    account bound premium are the same number ($4.13M). Written against the
    seed as-is, the scope assertion would pass just as well with `program
    premium` wired straight to the account total: that is precisely the
    vacuous assertion Task 7 shipped. A SECOND bound placement — no program
    file of its own, period far enough out that it never becomes the next
    renewal — is what makes the two figures diverge.

    Returns (client, org, placement, layers) where `placement` is the
    file-linked one the next renewal points at."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit import sync
    from bookkit.repo import placements as placements_repo
    from bookkit.services import renewals

    item = renewals.next_for_org(conn, org.id)
    assert item is not None and item.placement.program_path, (
        "fixture account's next renewal must be file-linked"
    )
    layers = sync.layer_details(conn, item.placement.id)
    assert layers, "fixture account's next renewal must have a real tower"

    placements_repo.create(
        conn, org.id, "2031 Property Program", "2030-01-01", "2031-01-01",
        status="bound", total_premium=900_000_00, commission_bps=1250,
    )
    still = renewals.next_for_org(conn, org.id)
    assert still is not None and still.placement.id == item.placement.id, (
        "the added placement stole the next-renewal slot — move its period out"
    )
    return client, org, item.placement, layers


def test_snapshot_tower_rows_come_from_the_renewal_placement(divergent_tower):
    """program premium / top of tower / unplaced describe ONE program — the
    one behind the next renewal — while bound premium is the account total.
    Mixing the two scopes silently is how the book once showed revenue that
    did not exist, so the two numbers are asserted to be DIFFERENT numbers and
    each to appear against its own label."""
    from bookkit import money
    from bookkit.services import book as book_service

    client, org, placement, layers = divergent_tower
    conn = client.app.state.conn

    program_premium = money.format_cents_compact(
        sum(layer["premium_cents"] for layer in layers if layer["premium_cents"] is not None)
    )
    top_of_tower = money.format_cents_compact(
        max(layer["attach_cents"] + layer["limit_cents"] for layer in layers)
    )
    account_bound = money.format_cents_compact(
        book_service.bound_premium_for_org(conn, org.id)
    )
    assert program_premium != account_bound, (
        "fixture must make the two scopes different numbers or this test "
        "cannot tell them apart"
    )

    response = client.get(f"/accounts/{org.ref}/relationship")
    assert response.status_code == 200

    assert _snapshot_value(response.text, "program premium") == program_premium
    assert _snapshot_value(response.text, "top of tower") == top_of_tower
    assert _snapshot_value(response.text, "bound premium") == account_bound
    # and the panel says WHICH program the three describe, so `program
    # premium` can never be read as an account total. Asserted as the VISIBLE
    # caption element, not as a bare substring: the rows also carry the scope
    # in a `title` attribute, and a substring check passed with the caption
    # deleted entirely — a tooltip nobody hovers is not a label (mutation 4).
    assert (
        f'<p class="snapshot-scope">{placement.program_name} · {placement.ref}</p>'
        in response.text
    )


def test_snapshot_omits_tower_rows_when_no_program_is_linked(app_and_org):
    """layer_details returns [] with no linked file. A zero would be a lie —
    "$0 program premium" reads as a program worth nothing, not as no program.

    The account here has a live renewal, so the omission is about the missing
    FILE and not about a page that rendered nothing: `bound premium` and the
    `next renewal` row are both asserted present alongside."""
    client, _org = app_and_org
    conn = client.app.state.conn
    from bookkit import sync
    from bookkit.repo import orgs
    from bookkit.services import renewals

    unlinked = next(
        (
            (o, item) for o in orgs.list_orgs(conn, kind="client")
            if (item := renewals.next_for_org(conn, o.id)) is not None
            and not sync.layer_details(conn, item.placement.id)
        ),
        None,
    )
    assert unlinked is not None, "no seeded client has an unlinked next renewal"
    org, item = unlinked

    response = client.get(f"/accounts/{org.ref}/relationship")
    assert response.status_code == 200
    assert "bound premium" in response.text
    assert item.renewal_on in response.text
    for omitted in ("program premium", "top of tower", "unplaced"):
        assert omitted not in response.text


def test_unplaced_names_the_layer_and_the_open_share(divergent_tower):
    """'20% on 3rd Excess' — a bare percentage does not say where the hole is,
    and a hole on the primary is a different conversation from a hole at the
    top of the tower.

    The open layer is written through towerkit the way the app writes one
    (add_layer then add_participant), so the share the row prints is the one
    the file actually carries."""
    client, org, placement, _layers = divergent_tower
    conn = client.app.state.conn
    from bookkit import sync

    added = sync.add_layer(
        conn, placement.id, "3rd Excess", ["gl"],
        attach_cents=27_000_000_00, limit_cents=10_000_000_00,
        premium_cents=400_000_00,
    )
    assert added.ok, added.items
    signed = sync.add_participant(conn, placement.id, "3rd-excess", "Markel", 8_000)
    assert signed.ok, signed.items

    response = client.get(f"/accounts/{org.ref}/relationship")
    assert response.status_code == 200
    assert _snapshot_value(response.text, "unplaced") == "20% on 3rd Excess"
    # warn, not danger, and never colour alone — the share and the layer name
    # both read without it
    assert re.search(
        r'class="snapshot-value[^"]*\bis-warn\b[^"]*"[^>]*>20% on 3rd Excess<',
        response.text,
    ), "the unplaced row does not carry the warn treatment"


def test_unplaced_leads_with_the_widest_hole_and_counts_the_rest(divergent_tower):
    """One 296px row cannot list a whole tower's open capacity, so it leads
    with the WIDEST hole — the one being worked — and says how many others
    there are. Leading with the narrowest would put the least urgent gap in
    the only slot the rail has."""
    client, org, placement, _layers = divergent_tower
    conn = client.app.state.conn
    from bookkit import sync

    # stacked, not overlapping: towerkit's validator refuses a layer that
    # overlaps one already in the tower, and a refused add is silent here
    for name, layer_id, attach, share in (
        ("3rd Excess", "3rd-excess", 27_000_000_00, 8_000),
        ("4th Excess", "4th-excess", 37_000_000_00, 5_000),
    ):
        added = sync.add_layer(
            conn, placement.id, name, ["gl"],
            attach_cents=attach, limit_cents=10_000_000_00, premium_cents=400_000_00,
        )
        assert added.ok, added.items
        signed = sync.add_participant(conn, placement.id, layer_id, "Markel", share)
        assert signed.ok, signed.items

    response = client.get(f"/accounts/{org.ref}/relationship")
    # 4th Excess is 50% open against 3rd Excess's 20% — the wider hole leads
    assert _snapshot_value(response.text, "unplaced") == "50% on 4th Excess +1 more"


def test_unplaced_says_none_when_every_layer_is_signed(divergent_tower):
    """A fully placed tower is a real read with a real answer, not an absent
    one — and "none" carries no warn treatment, because there is nothing to
    warn about."""
    client, org, _placement, layers = divergent_tower
    assert all(layer["signed_pct"] == 100 for layer in layers), (
        "the seeded tower is expected to be fully signed"
    )

    response = client.get(f"/accounts/{org.ref}/relationship")
    assert _snapshot_value(response.text, "unplaced") == "none"
    assert not re.search(
        r'class="snapshot-value[^"]*is-warn[^"]*"[^>]*>none<', response.text
    ), "a fully placed tower must not render as a warning"


# --- the scope group: membership, not just a caption (review round 1, item C)


def test_the_snapshot_rail_renders_exactly_these_rows_in_this_order(divergent_tower):
    """The caption's meaning is POSITIONAL — "these rows below are that
    program's" — so nothing but the order pins what it governs. An
    account-scoped row inserted between `program premium` and `top of tower`
    rendered under the program caption, inside the rule, and every test
    passed.

    The whole ordered sequence is asserted, not a subset: a subset check
    cannot see a row that appears where it should not, which is the entire
    failure mode."""
    client, org, _placement, _layers = divergent_tower
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert _snapshot_labels(response.text) == [
        "next renewal",
        "bound premium",
        "program premium",
        "top of tower",
        "unplaced",
        "open work",
        "last touch",
    ]


def test_the_scope_bracket_holds_exactly_the_program_scoped_rows(divergent_tower):
    """Membership in the bracket is STRUCTURAL: the caption and the three rows
    it names are children of one element, and the rule is that element's
    border. So "which rows does this caption govern" has an answer that does
    not depend on render order, and `bound premium` (the ACCOUNT's money)
    cannot end up under a caption naming one program.

    Also pins the two things the caption alone never covered: every row in the
    bracket carries `is-scoped`, and every one carries the scope as a `title`
    on the ROW (hovering the label, not just the number, says whose money it
    is)."""
    client, org, placement, _layers = divergent_tower
    response = client.get(f"/accounts/{org.ref}/relationship")
    scope = f"{placement.program_name} · {placement.ref}"

    group = _scope_group(response.text)
    assert f'<p class="snapshot-scope">{scope}</p>' in group
    assert _snapshot_labels(group) == ["program premium", "top of tower", "unplaced"]

    rows = re.findall(r"<div class=\"snapshot-row[^\"]*\"[^>]*>", group)
    assert len(rows) == 3
    for row in rows:
        assert "is-scoped" in row, f"a row inside the bracket is not marked scoped: {row}"
        assert f'title="{scope}' in row, f"a row inside the bracket names no scope: {row}"

    # and nothing OUTSIDE the bracket claims to be program-scoped
    outside = response.text.replace(group, "")
    assert "is-scoped" not in outside
    assert "snapshot-scope" not in outside
    for account_row in ("bound premium", "open work", "next renewal"):
        assert account_row in _snapshot_labels(outside)


def test_layer_details_is_read_once_per_page(app_and_org, monkeypatch):
    """It opens and parses the towerkit JSON file, so every extra caller is
    another disk read of the same bytes. Called once per render is a claim
    about the code that only a counter can hold: a second call inside
    `_snapshot` left the whole suite green."""
    from bookkit.web.routes import account as account_routes

    client, org = app_and_org
    real = account_routes.sync.layer_details
    calls: list[str] = []

    def counting(conn, placement_id):
        calls.append(placement_id)
        return real(conn, placement_id)

    monkeypatch.setattr(account_routes.sync, "layer_details", counting)

    for tab in ("program", "relationship", "work", "pipeline"):
        calls.clear()
        response = client.get(f"/accounts/{org.ref}/{tab}")
        assert response.status_code == 200
        assert len(calls) == 1, f"{tab} tab read the program file {len(calls)} times"


# --- the two zeros that are lies (review round 1, item B) --------------------
# The omit guard tested for "no program", not for "no data". With a program
# linked but the data absent, both money rows printed a figure that claims
# something false: `$0 program premium` (a program worth nothing) and
# `$0 top of tower` (a tower with no height). One treatment for both — no
# figure is printed, because there is no figure.


def _stub_layers(monkeypatch, layers):
    """Render the rail against a made-up tower. Neither case can be built out
    of the seed: no seeded program leaves a premium unset, and towerkit's
    validator will not let a normal layer sit at zero limit — the statutory
    case that produces `max(attach + limit) == 0` is a WC Part A flag."""
    from bookkit.web.routes import account as account_routes

    monkeypatch.setattr(
        account_routes.sync, "layer_details", lambda conn, placement_id: list(layers)
    )


def _layer(name="Primary", attach=0, limit=10_000_000_00, premium=None, signed=100.0):
    return {
        "name": name, "attach_cents": attach, "limit_cents": limit,
        "premium_cents": premium, "signed_pct": signed,
    }


def test_program_premium_says_no_figure_rather_than_zero(divergent_tower, monkeypatch):
    """Every layer's premium unset sums to nothing and printed `$0` — which
    reads as a program worth nothing, the exact misreading the omit guard was
    written to prevent one level up."""
    client, org, placement, _layers = divergent_tower
    _stub_layers(monkeypatch, [_layer("Primary"), _layer("1st XS", attach=10_000_000_00)])

    response = client.get(f"/accounts/{org.ref}/relationship")
    assert _snapshot_value(response.text, "program premium") == "—"
    assert "$0" not in response.text
    # and the row says WHY there is no figure, not just that there isn't one
    assert (
        f'title="{placement.program_name} · {placement.ref} · no layer carries a premium"'
        in response.text
    )


def test_top_of_tower_says_no_figure_for_statutory_only_cover(
    divergent_tower, monkeypatch
):
    """towerkit: statutory cover is "no dollar limit (WC Part A); limit MUST be
    0". A WC-only program therefore has max(attach + limit) == 0, and `$0 top
    of tower` says the tower has no height when it has no ceiling."""
    client, org, placement, _layers = divergent_tower
    _stub_layers(
        monkeypatch,
        [_layer("WC Part A", attach=0, limit=0, premium=250_000_00)],
    )

    response = client.get(f"/accounts/{org.ref}/relationship")
    assert _snapshot_value(response.text, "top of tower") == "—"
    assert "$0" not in response.text
    assert (
        f'title="{placement.program_name} · {placement.ref} · no dollar limit '
        f'(statutory cover)"' in response.text
    )


def test_a_partly_priced_program_marks_its_premium_as_incomplete(
    divergent_tower, monkeypatch
):
    """Skipping unpriced layers is the spec, but it silently UNDERSTATES a
    money figure, and money columns say what they are. The `~` travels with
    the number (the rail is 296px — a footnote elsewhere would not), and the
    title says how much of the tower is missing."""
    client, org, placement, _layers = divergent_tower
    _stub_layers(
        monkeypatch,
        [
            _layer("Primary", premium=100_000_00),
            _layer("1st XS", attach=10_000_000_00, premium=None),
        ],
    )

    response = client.get(f"/accounts/{org.ref}/relationship")
    assert _snapshot_value(response.text, "program premium") == "~$100K"
    assert (
        f'title="{placement.program_name} · {placement.ref} · 1 of 2 layers '
        f'carry no premium"' in response.text
    )


def test_a_fully_priced_program_carries_no_incompleteness_mark(divergent_tower):
    """The mark has to mean something, so it must be absent when the figure is
    whole."""
    client, org, _placement, _layers = divergent_tower
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert not _snapshot_value(response.text, "program premium").startswith("~")


def test_a_one_basis_point_hole_is_still_a_hole(divergent_tower, monkeypatch):
    """`signed_pct < 100` is the threshold, and it is exact: 10000 is the only
    integer bps whose /100 is exactly 100.0, so no float slack is needed and
    none may be added. towerkit stores a 99.99% signing as 9999 bps — real
    data, not rounding noise — and loosening the comparison to `< 99.99` would
    swallow it while every other test stayed green."""
    client, org, _placement, _layers = divergent_tower
    _stub_layers(monkeypatch, [_layer("Primary", premium=100_000_00, signed=9_999 / 100)])

    response = client.get(f"/accounts/{org.ref}/relationship")
    assert _snapshot_value(response.text, "unplaced") == "0.01% on Primary"


def test_a_fully_signed_layer_is_not_a_hole(divergent_tower, monkeypatch):
    """The other side of the same boundary: 10000 bps is exactly 100.0 and
    reads as placed, so the threshold cannot be widened to `<= 100` either."""
    client, org, _placement, _layers = divergent_tower
    _stub_layers(
        monkeypatch, [_layer("Primary", premium=100_000_00, signed=10_000 / 100)]
    )

    response = client.get(f"/accounts/{org.ref}/relationship")
    assert _snapshot_value(response.text, "unplaced") == "none"


def test_team_and_recent_changes_empty_states_use_canonical_copy(app_and_org):
    """TEAM is addable (the Assign link adds to it) — the spec's addable-list
    phrasing. RECENT CHANGES having nothing yet isn't a problem — the spec's
    attention-list phrasing, not invented copy."""
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert "empty — add the first row" in response.text  # TEAM (no assignments seeded)
    assert "nothing here — that's good" in response.text  # RECENT CHANGES


def test_undo_pill_absent_when_nothing_to_undo(app_and_org):
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/relationship")
    assert "undo-pill" not in response.text
    assert "nothing here — that's good" in response.text


def test_undo_pill_and_recent_change_appear_after_a_batch_and_revert_is_wired(app_and_org):
    client, org = app_and_org
    from bookkit.services import batches

    conn = client.app.state.conn
    with batches.open_batch(
        conn, source="tui", tool="edit_field", summary="premium PLC-0001 → $4.13M",
        org_id=org.id,
    ):
        pass

    response = client.get(f"/accounts/{org.ref}/relationship")
    assert response.status_code == 200
    assert "undo-pill" in response.text
    assert "premium PLC-0001 → $4.13M" in response.text
    # Wired as of Task 15b, and this assertion is the inverse of the one it
    # replaces: the revert affordance USED to be a bare span carrying
    # aria-disabled, and asserting that is now asserting the bug. Both the
    # per-change Revert and the top-bar Undo pill POST the one revert route
    # (routes/changes.py), so both must carry hx-post and neither may carry
    # aria-disabled — that is what the XOR check below enforces generally
    # and what these two assert by name.
    from bookkit.repo import batches as batches_repo

    batch = batches_repo.recent(conn, since="", limit=1)[0]
    # The WHOLE url, not just "/changes/" in it: what the page renders and
    # what tests/test_web_writes.py POSTs are two independent strings, and a
    # template that built a subtly different one (no ?tab=, the ids swapped)
    # would leave both green while every click 404s.
    action = f'hx-post="/accounts/{org.ref}/changes/{batch.ref}/revert?tab=relationship"'

    revert_tag = re.search(r'<button[^>]*class="revert-link"[^>]*>', response.text)
    assert revert_tag, "no revert-link button found"
    assert 'aria-disabled' not in revert_tag.group(0)
    assert action in revert_tag.group(0)

    # the pill is the same POST against the newest unreverted batch — which,
    # with exactly one batch on this account, is this one
    pill_tag = re.search(r'<button[^>]*class="undo-pill"[^>]*>', response.text)
    assert pill_tag, "no undo-pill button found"
    assert 'aria-disabled' not in pill_tag.group(0)
    assert action in pill_tag.group(0)

    # hx-confirm was completely unpinned until review round 2 (C): deleting
    # it from both templates left the whole suite green, over a control that
    # is IRREVERSIBLE — services/batches.revert's own writes carry no
    # batch_id, so there is no undoing the undo on either surface. The TUI
    # gates the identical call behind ConfirmRevertBatch. It is not enough
    # that the attribute exists: it has to NAME what goes back, which is what
    # makes it the one-attribute version of that bar rather than a generic
    # "are you sure?" users learn to click through.
    for tag in (revert_tag.group(0), pill_tag.group(0)):
        confirm = re.search(r'hx-confirm="([^"]*)"', tag)
        assert confirm, f"revert control carries no hx-confirm: {tag}"
        assert "premium PLC-0001 → $4.13M" in confirm.group(1), confirm.group(1)
        assert "cannot be undone" in confirm.group(1), confirm.group(1)


# Every control that isn't wired to anything yet must say so: no href/hx-*
# attribute WITHOUT aria-disabled="true" (looks live, is dead — the bug),
# and no href/hx-* attribute WITH aria-disabled="true" either (a control
# that's actually wired must drop the disabled marker — this is what forces
# the next task that wires one to remove it, or this test breaks).
#
# `row-action-btn` is in the set as of review round 2 (G): it is the class on
# the wired row controls in _requests_panel, _items_panel and _tasks_panel,
# and a substring check would never have reached them — the marker match is
# on the split class list, so "row-action" does not cover "row-action-btn".
# Those four controls sat outside this check entirely, which bounded what the
# F8 fix protected to the two panels that happen to use the bare class.
_INERT_CONTROL_MARKERS = frozenset(
    {"btn-pill", "undo-pill", "revert-link", "rail-action", "row-action", "row-action-btn"}
)

# What counts as "this control does something". NOT a bare `hx-` prefix: htmx
# attributes are mostly modifiers, so `hx-swap`, `hx-confirm` or `hx-target`
# alone used to satisfy the check — deleting `hx-post` from the wired Revert
# button and leaving `hx-swap="none"` behind kept this test green over a
# control that posts nowhere (review round 1, F8). Only verbs count.
_ACTION_ATTRS = ("href=", "hx-post", "hx-get", "hx-delete", "hx-put")


def _assert_inert_controls_are_consistently_marked(html: str) -> int:
    tags = re.findall(r"<[a-zA-Z][^>]*>", html)
    matched = []
    for tag in tags:
        class_match = re.search(r'class="([^"]*)"', tag)
        if class_match and set(class_match.group(1).split()) & _INERT_CONTROL_MARKERS:
            matched.append(tag)
    for tag in matched:
        has_action = any(attr in tag for attr in _ACTION_ATTRS)
        has_disabled = 'aria-disabled="true"' in tag
        assert has_action != has_disabled, (
            f"control is neither clearly pending nor clearly wired: {tag}"
        )
    return len(matched)


def test_inert_controls_carry_aria_disabled_xor_a_real_action(app_and_org):
    client, org = app_and_org
    response = client.get(f"/accounts/{org.ref}/relationship")
    matched = _assert_inert_controls_are_consistently_marked(response.text)
    assert matched >= 5, "expected at least the four header pills plus the Assign link"


def test_inert_controls_stay_marked_with_a_recent_change_present(app_and_org):
    """Same check with the undo-pill and revert-link actually rendered
    (they're conditional on there being a batch to show)."""
    client, org = app_and_org
    from bookkit.services import batches

    conn = client.app.state.conn
    with batches.open_batch(
        conn, source="tui", tool="edit_field", summary="premium PLC-0001 → $4.13M",
        org_id=org.id,
    ):
        pass

    response = client.get(f"/accounts/{org.ref}/relationship")
    assert "undo-pill" in response.text and "revert-link" in response.text
    _assert_inert_controls_are_consistently_marked(response.text)


def test_inert_controls_stay_marked_on_the_pages_with_row_action_buttons(app_and_org):
    """Review round 2, G. `row-action-btn` was never in the marker set, so the
    four wired row controls in _requests_panel, _items_panel and _tasks_panel
    sat outside the XOR check entirely — and the two tests above only ever
    load /relationship, which renders none of them. Adding the class to the
    set changes nothing on its own; this is the test that makes it bite, on
    the work tab and the request detail page where those controls live."""
    client, org = app_and_org
    from bookkit.repo import rfi as rfi_repo
    from bookkit.repo import tasks as tasks_repo

    conn = client.app.state.conn
    if not tasks_repo.open_tasks_for_client(conn, org.id):
        tasks_repo.create(conn, "Chase loss runs", org_id=org.id, due_on="2026-08-20")
    request = rfi_repo.create_request(conn, org.id, "Loss run refresh", "2026-08-10")
    rfi_repo.add_item(conn, request.id, "loss runs 2021-2025", category="Financials")

    work = client.get(f"/accounts/{org.ref}/work")
    assert work.status_code == 200
    assert work.text.count("row-action-btn") >= 3, "the work tab renders none of them"
    _assert_inert_controls_are_consistently_marked(work.text)

    detail = client.get(f"/accounts/{org.ref}/requests/{request.id}")
    assert detail.status_code == 200
    assert "row-action-btn" in detail.text, "the request detail renders none of them"
    _assert_inert_controls_are_consistently_marked(detail.text)
