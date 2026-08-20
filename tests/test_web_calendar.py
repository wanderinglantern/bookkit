"""The renewal calendar: twelve months out, overdue in its own leading
column — anything overdue is decided by days_remaining < 0, never by where
a cell lands in the grid (the four-surface renewal-date bug's rule)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.services import renewals
from bookkit.web.app import create_app
from bookkit.web.routes import calendar as calendar_route


@pytest.fixture
def client(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app, base_url="http://127.0.0.1") as c:
        yield c


def test_the_grid_shows_twelve_months_and_every_windowed_account(client):
    conn = client.app.state.conn
    items = renewals.upcoming(conn, calendar_route.date.today(), days=365)
    assert items, "the seeded book has no renewals inside a year"

    page = client.get("/calendar").text

    for item in items:
        assert item.org.name in page
    # twelve month headers
    import re

    assert len(re.findall(r"<th class=\"num\">[A-Z][a-z]{2} \d\d</th>", page)) == 12


def test_overdue_lands_in_the_overdue_column_not_a_month(client):
    conn = client.app.state.conn
    items = renewals.upcoming(conn, calendar_route.date.today(), days=365)
    overdue = [i for i in items if i.days_remaining < 0]
    assert overdue, "nothing overdue in the seed — test proves nothing"

    page = client.get("/calendar").text

    for item in overdue:
        assert f"◆ {-item.days_remaining}d" in page


def test_a_chip_carries_status_word_and_date_in_its_title(client):
    """Colour is signal, not the only carrier: the chip's title says the
    status word and the renewal date the chip's day digit comes from."""
    conn = client.app.state.conn
    upcoming = [
        i for i in renewals.upcoming(conn, calendar_route.date.today(), days=365)
        if i.days_remaining >= 0
    ]
    assert upcoming

    page = client.get("/calendar").text

    item = upcoming[0]
    assert f"{item.placement.status}, renews {item.renewal_on}" in page
