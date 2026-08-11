"""Team — internal colleagues, their specialties, and where they're assigned.
The filter answers 'who do I go to for cyber?' against specialties AND the
lines on live assignments."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.screen import Screen
from textual.widgets import Footer, Header, Input

from ...repo import team
from ...services.team import find_specialists
from ..widgets.tables import ListTable


class TeamScreen(Screen):
    app: BookkitApp
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("a", "new_member", "New member"),
        Binding("e", "edit_member", "Edit"),
        Binding("f", "focus_filter", "Who for…?"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Input(placeholder="who do I go to for… (cyber, marine, D&O)", id="team-filter")
            yield ListTable(id="team-table")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data()
        self.query_one("#team-table", ListTable).focus()

    def refresh_data(self, query: str = "") -> None:
        conn = self.app.conn
        table = self.query_one("#team-table", ListTable)
        table.clear(columns=True)
        table.add_columns("name", "title", "specialty", "assignments", "match")
        if query.strip():
            members = [(m.member, f"{m.score:.0f}% · {m.evidence}") for m in
                       find_specialists(conn, query)]
        else:
            members = [(m, "") for m in team.list_members(conn)]
        for member, match in members:
            assignments = team.for_member(conn, member.id)
            where = ", ".join(
                sorted({row["org_name"] for row in assignments if row["org_name"]})
            )
            table.add_row(
                member.name, member.title or "—", member.specialty or "—",
                where or "—", match,
                key=member.id,
            )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "team-filter":
            self.refresh_data(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.query_one("#team-table", ListTable).focus()

    def _selected_member_id(self) -> str | None:
        table = self.query_one("#team-table", ListTable)
        if table.cursor_row is None or table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value

    def on_data_table_row_selected(self, event: ListTable.RowSelected) -> None:
        """Enter shows where they're assigned; picking one opens the account."""
        from ..widgets.picker import Picker

        member_id = event.row_key.value
        if not member_id:
            return
        assignments = team.for_member(self.app.conn, member_id)
        if not assignments:
            self.notify("no assignments yet — assign from an account (w)")
            return
        options = []
        for row in assignments:
            deal = (
                f" · {row['placement_ref']} {row['program_name']}"
                if row["placement_ref"]
                else ""
            )
            label = f"{row['org_name']}{deal} — {row['role'] or '—'}"
            if row["lines"]:
                label += f" ({row['lines']})"
            options.append((label, str(row["resolved_org_id"])))

        def picked(org_id: str | None) -> None:
            if org_id:
                self.app.open_account(org_id)

        member = team.get_member(self.app.conn, member_id)
        self.app.push_screen(Picker(f"{member.name} — assignments", options), picked)

    def action_new_member(self) -> None:
        from ..widgets.entity_forms import member_form
        from ..widgets.forms import FormModal, dropped

        def saved(values: dict | None) -> None:
            if values is not None:
                member = team.create_member(self.app.conn, **dropped(values))
                self.notify(f"added {member.name}")
                self.refresh_data(self.query_one("#team-filter", Input).value)

        self.app.push_screen(FormModal(member_form()), saved)

    def action_edit_member(self) -> None:
        from ..widgets.entity_forms import member_form
        from ..widgets.forms import FormModal, dropped

        member_id = self._selected_member_id()
        if member_id is None:
            return
        member = team.get_member(self.app.conn, member_id)

        def saved(values: dict | None) -> None:
            if values is not None:
                team.update_member(self.app.conn, member_id, **dropped(values))
                self.refresh_data(self.query_one("#team-filter", Input).value)

        self.app.push_screen(FormModal(member_form(member)), saved)

    def action_focus_filter(self) -> None:
        self.query_one("#team-filter", Input).focus()
