"""Chrome that has to be on screen to do its job.

A keyboard-first app whose Footer renders as a blank line has no
discoverability layer at all, and a modal whose Save button sits outside its
own box has no visible way to commit. Both were width/height rules that only
fail past a threshold, which is why they shipped: they look fine at 200
columns and on a tall terminal.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Footer

from bookkit.tui.app import BookkitApp

WIDE = (140, 45)
SMALL = (80, 24)


def _screen_rows(app: BookkitApp, size: tuple[int, int]) -> list[str]:
    """What the terminal would actually show, row by row."""
    import io

    from rich.console import Console

    console = Console(
        width=size[0], height=size[1], file=io.StringIO(), record=True,
        force_terminal=True, color_system="truecolor", legacy_windows=False,
    )
    console.print(app.screen._compositor)
    rows = console.export_text(styles=False).splitlines()
    rows += [""] * (size[1] - len(rows))
    return rows

# every screen a user can land on, and the key that gets there
SCREENS = [
    ("navigator", []),
    ("today", ["t"]),
    ("book", ["b"]),
    ("calendar", ["c"]),
    ("markets", ["m"]),
    ("pipeline", ["p"]),
    ("team", ["w"]),
]

# reached by selecting a row rather than by one key — and both were measured
# overflowing (account 199 columns, market detail 150) while the guard above
# covered only the seven screens a single key reaches
DEEP_SCREENS = ["account", "market_detail"]


async def _open_deep(app: BookkitApp, pilot, which: str) -> None:
    from bookkit.repo import orgs

    if which == "account":
        app.open_account(orgs.list_orgs(app.conn, kind="client")[0].id)
        await pilot.pause()
        return
    await pilot.press("m")
    await pilot.pause()
    for _ in range(13):
        await pilot.press("down")
    await pilot.press("enter")
    await pilot.pause()


@pytest.mark.parametrize("size", [WIDE, SMALL], ids=["140x45", "80x24"])
@pytest.mark.parametrize("name,keys", SCREENS, ids=[s[0] for s in SCREENS])
async def test_the_footer_actually_renders(
    snapshot_db: Path, name: str, keys: list[str], size: tuple[int, int]
) -> None:
    """Footer is height:1 with overflow-x:auto, so the moment its content
    exceeds the terminal the scrollbar takes its only row and the whole thing
    paints BLANK — not truncated, empty. It was blank on 6 of 9 screens at
    140x45, including the home screen, and the committed baselines recorded
    that as correct."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        for key in keys:
            await pilot.press(key)
            await pilot.pause()

        footer = app.screen.query_one(Footer)
        assert footer.region.height > 0, f"{name}: footer has no height"
        # measure the COMPOSITED row, not the widget in isolation:
        # render_lines() on the widget returns empty whatever the state, so a
        # test built on it passes and fails for the wrong reasons
        painted = _screen_rows(app, size)[footer.region.y].strip()
        assert painted, f"{name} at {size}: footer rendered blank"


@pytest.mark.parametrize("size", [WIDE, SMALL], ids=["140x45", "80x24"])
@pytest.mark.parametrize("name,keys", SCREENS, ids=[s[0] for s in SCREENS])
async def test_the_footer_fits_without_a_scrollbar(
    snapshot_db: Path, name: str, keys: list[str], size: tuple[int, int]
) -> None:
    """The guard that stops this recurring: the next `show=True` binding that
    pushes a screen past its width would otherwise blank the footer again,
    silently, and the snapshots would happily record it."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        for key in keys:
            await pilot.press(key)
            await pilot.pause()
        footer = app.screen.query_one(Footer)
        if size != WIDE:
            return          # 80 columns cannot hold a useful key row; it crops
        # NB assert on CONTENT width, not on show_horizontal_scrollbar: once
        # the Footer overflow is hidden that flag is always False, so a test
        # built on it passes for the wrong reason and would not notice a new
        # binding quietly cropping keys off the right-hand edge.
        assert footer.virtual_size.width <= footer.container_size.width, (
            f"{name} at {size}: footer needs {footer.virtual_size.width} "
            f"columns but has {footer.container_size.width} — keys are being "
            f"cropped. Demote one with show=False."
        )


@pytest.mark.parametrize("which", DEEP_SCREENS)
async def test_the_footer_fits_on_screens_reached_by_selecting_a_row(
    snapshot_db: Path, which: str
) -> None:
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await _open_deep(app, pilot, which)
        footer = app.screen.query_one(Footer)
        assert footer.virtual_size.width <= footer.container_size.width, (
            f"{which}: footer needs {footer.virtual_size.width} columns but has "
            f"{footer.container_size.width} — keys are being cropped"
        )
        assert _screen_rows(app, WIDE)[footer.region.y].strip()


# --- modals keep their own chrome on screen ---------------------------------


# every capped modal in the app and the key that opens it, because the bug is
# in the CSS PATTERN, not in one screen: QuickCapture carried the same
# `max-height: 55vh` inside a capped box long after FormModal was fixed, and a
# test parametrised over one of them said nothing about the other
CAPPED_MODALS = [
    ("form", "ctrl+t", "#form-save"),        # new task: a long-ish form
    ("quick_capture", "n", "#qc-save"),      # log interaction: the longest
]


@pytest.mark.parametrize("size", [WIDE, SMALL], ids=["140x45", "80x24"])
@pytest.mark.parametrize(
    "name,key,save", CAPPED_MODALS, ids=[m[0] for m in CAPPED_MODALS]
)
async def test_a_long_form_keeps_its_save_button_inside_the_box(
    snapshot_db: Path, size: tuple[int, int], name: str, key: str, save: str
) -> None:
    """`.modal-box max-height: 80%` and `.modal-fields max-height: 55vh` add
    up past the box, so below ~34 rows the hint and Save button landed
    OUTSIDE it — invisible, while Tab still reached them and Enter still
    fired."""
    from textual.widgets import Button

    app = BookkitApp(snapshot_db)
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()

        box = app.screen.query_one(".modal-box")
        button = app.screen.query_one(save, Button)
        assert button.region.height > 0, f"{name} {size}: Save button not rendered"
        bottom = button.region.y + button.region.height
        assert bottom <= box.region.y + box.region.height, (
            f"{name} {size}: Save button at y={button.region.y}..{bottom} sits "
            f"below the box ending at {box.region.y + box.region.height}"
        )
        assert button.region.y >= box.region.y


async def test_quick_capture_paints_every_field_at_the_reference_size(
    snapshot_db: Path,
) -> None:
    """A field that exists, is wired, is tested and is below the fold of a
    scroller nobody knows to scroll is not a feature yet — and `who was there`
    shipped that way, together with the roster whose entire job is that you
    cannot type a name you do not know.

    140x45 is the size the rest of this file treats as the app's reference
    terminal, and at that size the modal has 26 rows of field viewport. Its
    content was 41 rows and is 33: only `note` and its TextArea now sit below
    the fold, and that is the field nobody has to be told about. When this
    fails, take rows off the CHROME — the label margins and the account
    picker's height are what paid for it last time — not off the fields."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=WIDE) as pilot:
        await pilot.pause()
        await pilot.press("n")
        await pilot.pause()
        painted = "\n".join(_screen_rows(app, WIDE))

        assert "who was there" in painted, "the attendee field is below the fold"
        assert "on this account" in painted or "no contacts on this account" in painted, (
            "the roster is below the fold — the field it captions asks for names "
            "from a list the user cannot see"
        )
        # not an accident of the account picker: the field ABOVE it has to have
        # made it too, or the modal is just scrolled to a different place
        assert "what happened" in painted, "the subject field is below the fold"


# --- the import preview can show its own verdict ----------------------------


async def test_the_import_preview_is_reachable(snapshot_db: Path) -> None:
    """A Static with max-height clamps its VIRTUAL size, so content past the
    cap is not scrollable — it is gone, with no scrollbar and no ellipsis.
    `report()` puts 'ERRORS — cannot commit' last, so the one line the user
    needed was the first to disappear."""
    app = BookkitApp(snapshot_db)
    async with app.run_test(size=(140, 45)) as pilot:
        await pilot.pause()
        await pilot.press("t")
        await pilot.pause()
        await pilot.press("i")               # import screen
        await pilot.pause()

        preview = app.screen.query_one("#import-preview")
        assert preview.can_focus, "the preview cannot be scrolled by keyboard"


# --- the verdict survives a clipped pane ------------------------------------


def test_the_verdict_says_go_or_no_go_on_its_own_line() -> None:
    """report() ends with the go/no-go line, so a clipped pane hid exactly the
    line that decides what happens next. verdict() is what the screens pin
    outside the scroller."""
    from bookkit.imports.staging import (
        Issue,
        Severity,
        StagedImport,
        StagedRecord,
    )

    clean = StagedImport(
        source="book.xlsx",
        sha256="x",
        records=[
            StagedRecord(
                kind="account", key="(row 2)", action="create",
                fields={}, source_row=2,
            )
        ],
        unmapped=[],
    )
    assert "OK to commit" in clean.verdict()

    broken = StagedImport(
        source="book.xlsx",
        sha256="x",
        records=[
            StagedRecord(
                kind="account", key="(row 2)", action="skip", fields={},
                source_row=2,
                issues=[
                    Issue(Severity.ERROR, "expiry", "needs inception and expiry")
                ],
            )
        ],
        unmapped=["Renewal Date", "Expiring Premium"],
    )
    verdict = broken.verdict()
    assert "ERRORS — cannot commit" in verdict
    assert "1 error(s)" in verdict
    assert "2 column(s) ignored" in verdict


def test_attendees_of_one_interaction_come_back_in_a_stable_order(
    conn,
) -> None:
    """Two contacts sharing a surname tied on `ORDER BY last_name`, so SQLite
    returned them in whatever order the rows happened to sit in — which
    differs between processes, because ids carry a random tail. It rendered as
    "Xia Chen, Elena Chen" one run and "Elena Chen, Xia Chen" the next, and
    made the account-overview snapshot flaky."""
    from bookkit.repo import contacts as contacts_repo
    from bookkit.repo import interactions, orgs

    org = orgs.create(conn, kind="client", name="Atomic", status="active")
    xia = contacts_repo.create(conn, org_id=org.id, first_name="Xia", last_name="Chen")
    elena = contacts_repo.create(
        conn, org_id=org.id, first_name="Elena", last_name="Chen"
    )
    made = interactions.log(
        conn, org_id=org.id, type="email", subject="Quarterly review",
        occurred_on="2026-08-07", contact_ids=[xia.id, elena.id],
    )

    names = [c.first_name for c in interactions.attendees(conn, made.id)]
    assert names == sorted(names), f"unstable attendee order: {names}"
    for _ in range(5):
        assert [c.first_name for c in interactions.attendees(conn, made.id)] == names
