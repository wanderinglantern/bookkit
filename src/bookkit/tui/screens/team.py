"""Team — internal colleagues, their specialties, and where they're assigned.
The filter answers 'who do I go to for cyber?' against specialties AND the
lines on live assignments."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.coordinate import Coordinate
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Static

from ...repo import team
from ...services.team import find_specialists
from .. import theme
from ..theme import dash
from ..widgets.tables import ListTable


class TeamScreen(Screen):
    app: BookkitApp

    DEFAULT_CSS = """
    TeamScreen #team-hint {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("a", "new_member", "New member"),
        Binding("e", "edit_member", "Edit"),
        Binding("i", "import_colleague", "Import (paste sig)"),
        Binding("w", "assign_selected", "Assign to account"),
        Binding("f", "focus_filter", "Who for…?"),
    ]

    def action_assign_selected(self) -> None:
        """Assign the selected colleague to a client account — the mirror of
        `w` on an account, starting from the member instead."""
        from ...repo import orgs
        from ..widgets.entity_forms import assignment_form
        from ..widgets.forms import FormModal, dropped
        from ..widgets.picker import Picker

        member_id = self._selected_member_id()
        if member_id is None:
            return
        conn = self.app.conn
        member = team.get_member(conn, member_id)
        clients = orgs.list_orgs(conn, kind="client")
        if not clients:
            self.notify("no client accounts yet", severity="warning")
            return

        def picked(org_id: str | None) -> None:
            if org_id is None:
                return
            org = orgs.get(conn, org_id)

            def commit(values: dict) -> str | None:
                team.assign(conn, member_id, org_id=org_id, **dropped(values))
                return None

            def done(values: dict | None) -> None:
                if values is not None:
                    self.notify(f"{member.name} assigned to {org.name}")
                    self.refresh_data(self.query_one("#team-filter", Input).value)

            self.app.push_screen(
                FormModal(
                    assignment_form(title=f"{member.name} → {org.name}", conn=conn),
                    commit=commit,
                ),
                done,
            )

        options = [(org.name, org.id) for org in clients]
        self.app.push_screen(Picker(f"assign {member.name} to…", options), picked)

    def action_import_colleague(self) -> None:
        """Paste a colleague's email signature — same parser as contacts."""
        from ...imports.commit import commit_team_paste
        from ...imports.mappers.team_paste import stage_team_paste
        from ..widgets.paste_import import PasteImportModal

        def stage(text: str):
            return stage_team_paste(self.app.conn, text)

        def commit(staged) -> str:
            commit_team_paste(self.app.conn, staged, self.app.db_file())
            self.refresh_data(self.query_one("#team-filter", Input).value)
            return "colleague saved to the team"

        self.app.push_screen(
            PasteImportModal("paste colleague signature", stage, commit)
        )

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Input(placeholder="who do I go to for… (cyber, marine, D&O)", id="team-filter")
            yield ListTable(id="team-table")
            yield Static(id="team-hint")
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
                member.name, member.title or dash(), member.specialty or dash(),
                where or dash(),
                Text(match, style=theme.DIM) if match else "",
                key=member.id,
            )
        count = f"{len(members)} member" + ("" if len(members) == 1 else "s")
        if query.strip():
            from rich.markup import escape

            count = f"{count} match '{escape(query.strip())}'"
        self.query_one("#team-hint", Static).update(
            f"[{theme.DIM}]{count} — [b]enter[/b] assignments · [b]a[/b] add · "
            f"[b]e[/b] edit · [b]w[/b] assign to account · [b]i[/b] paste signature · "
            f"[b]f[/b] who for…[/]"
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

        def commit(values: dict) -> str | None:
            member = team.create_member(self.app.conn, **dropped(values))
            self.notify(f"added {member.name}")
            return None

        def done(values: dict | None) -> None:
            if values is not None:
                self.refresh_data(self.query_one("#team-filter", Input).value)

        self.app.push_screen(
            FormModal(member_form(conn=self.app.conn), commit=commit), done
        )

    def action_edit_member(self) -> None:
        from ..widgets.entity_forms import member_form
        from ..widgets.forms import FormModal, dropped

        member_id = self._selected_member_id()
        if member_id is None:
            return
        member = team.get_member(self.app.conn, member_id)

        def commit(values: dict) -> str | None:
            team.update_member(self.app.conn, member_id, **dropped(values))
            return None

        def done(values: dict | None) -> None:
            if values is not None:
                self.refresh_data(self.query_one("#team-filter", Input).value)

        self.app.push_screen(
            FormModal(member_form(member, conn=self.app.conn), commit=commit), done
        )

    def action_focus_filter(self) -> None:
        self.query_one("#team-filter", Input).focus()
