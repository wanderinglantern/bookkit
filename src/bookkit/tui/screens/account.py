"""Account detail — where most time is spent. Header + tabs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

import subprocess
from datetime import date
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import Screen
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from ...dates import days_until
from ...money import format_cents, format_cents_compact
from ...repo import (
    contacts,
    documents,
    interactions,
    opportunities,
    orgs,
    placements,
    projection,
    submissions,
)
from ...repo import tasks as tasks_repo
from ..widgets.tables import ListTable
from ..widgets.tower_preview import TowerPreview


class AccountScreen(Screen):
    app: BookkitApp
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("a", "add_here", "Add (this tab)"),
        Binding("e", "edit_here", "Edit"),
        Binding("s", "new_submission", "Submission"),
        Binding("d", "task_done", "Done (task)"),
        Binding("p", "mark_primary", "Primary (contact)"),
        Binding("u", "undo", "Undo"),
        Binding("r", "refresh", "Refresh", show=False),
    ]

    def __init__(self, org_id: str) -> None:
        super().__init__()
        self.current_org_id = org_id

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="account-header")
        with TabbedContent():
            with TabPane("Overview", id="tab-overview"):
                with VerticalScroll():
                    yield Static("KEY CONTACTS", classes="pane-title")
                    yield ListTable(id="ov-contacts")
                    yield Static("RECENT INTERACTIONS", classes="pane-title")
                    yield ListTable(id="ov-interactions")
                    yield Static("OPEN TASKS", classes="pane-title")
                    yield ListTable(id="ov-tasks")
                    yield Static("OPEN OPPORTUNITIES", classes="pane-title")
                    yield ListTable(id="ov-opps")
            with TabPane("Contacts", id="tab-contacts"):
                yield ListTable(id="contacts-table")
            with TabPane("Interactions", id="tab-interactions"):
                yield ListTable(id="interactions-table")
            with TabPane("Placements", id="tab-placements"):
                with Horizontal():
                    with Vertical(id="placement-side"):
                        yield ListTable(id="placements-table")
                        yield ListTable(id="carriers-table")
                        yield Static(id="sync-state")
                    yield TowerPreview(id="tower-preview")
            with TabPane("Pipeline", id="tab-pipeline"):
                with VerticalScroll():
                    yield Static("OPPORTUNITIES", classes="pane-title")
                    yield ListTable(id="pipeline-opps")
                    yield Static("SUBMISSIONS", classes="pane-title")
                    yield ListTable(id="pipeline-subs")
            with TabPane("Documents", id="tab-documents"):
                yield ListTable(id="documents-table")
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data()

    def on_screen_resume(self) -> None:
        self.refresh_data()

    def refresh_data(self) -> None:
        conn = self.app.conn
        today = date.today()
        org = orgs.get(conn, self.current_org_id)

        nxt = placements.next_renewal_for_org(conn, org.id, today.isoformat())
        premium = format_cents(nxt.total_premium) if nxt and nxt.total_premium else "—"
        renewal = (
            f"{nxt.period_to} ({days_until(nxt.period_to, today)}d)" if nxt else "none scheduled"
        )
        self.query_one("#account-header", Static).update(
            f"[b]{org.name}[/b]  {org.ref}   status: {org.status}   "
            f"owner: {org.owner or '—'}   premium: {premium}   next renewal: {renewal}"
        )

        roster = contacts.for_org(conn, org.id)
        for table_id, rows in (("#ov-contacts", roster[:5]), ("#contacts-table", roster)):
            table = self.query_one(table_id, ListTable)
            table.clear(columns=True)
            table.add_columns("", "name", "role", "title", "email", "phone")
            for c in rows:
                table.add_row(
                    "p" if c.is_primary else "",
                    c.name, c.role or "—", c.title or "—", c.email or "—",
                    c.phone or c.mobile or "—",
                    key=c.id,
                )

        log = interactions.for_org(conn, org.id)
        for table_id, int_rows in (("#ov-interactions", log[:5]), ("#interactions-table", log)):
            table = self.query_one(table_id, ListTable)
            table.clear(columns=True)
            table.add_columns("date", "type", "subject", "who")
            for i in int_rows:
                who = ", ".join(c.name for c in interactions.attendees(conn, i.id))
                table.add_row(i.occurred_on, i.type, i.subject, who, key=i.id)

        open_tasks = tasks_repo.open_tasks(conn, org_id=org.id)
        table = self.query_one("#ov-tasks", ListTable)
        table.clear(columns=True)
        table.add_columns("due", "task")
        for t in open_tasks:
            table.add_row(t.due_on or "—", t.title, key=t.id)

        opps = opportunities.for_org(conn, org.id, open_only=True)
        table = self.query_one("#ov-opps", ListTable)
        table.clear(columns=True)
        table.add_columns("ref", "title", "stage", "target", "close")
        for o in opps:
            table.add_row(
                o.ref, o.title, o.stage,
                format_cents_compact(o.target_premium) if o.target_premium else "—",
                o.target_effective or "—",
                key=o.id,
            )

        self._refresh_placements(org.id)
        self._refresh_pipeline(org.id)

        docs = documents.for_org(conn, org.id)
        table = self.query_one("#documents-table", ListTable)
        table.clear(columns=True)
        table.add_columns("added", "kind", "title", "path")
        for d in docs:
            table.add_row(d.added_at[:10], d.kind or "—", d.title, d.path, key=d.id)

    def _refresh_placements(self, org_id: str) -> None:
        conn = self.app.conn
        table = self.query_one("#placements-table", ListTable)
        table.clear(columns=True)
        table.add_columns("ref", "program", "period", "status", "premium")
        rows = placements.for_org(conn, org_id)
        for p in rows:
            table.add_row(
                p.ref, p.program_name, f"{p.period_from} → {p.period_to}", p.status,
                format_cents_compact(p.total_premium) if p.total_premium else "—",
                key=p.id,
            )
        if rows:
            self.show_placement(rows[0].id)
        else:
            self.query_one("#sync-state", Static).update("no placements")
            self.query_one("#tower-preview", TowerPreview).show_placeholder()

    def _refresh_pipeline(self, org_id: str) -> None:
        conn = self.app.conn
        table = self.query_one("#pipeline-opps", ListTable)
        table.clear(columns=True)
        table.add_columns("ref", "title", "stage", "target", "prob")
        for o in opportunities.for_org(conn, org_id):
            table.add_row(
                o.ref, o.title, o.stage,
                format_cents_compact(o.target_premium) if o.target_premium else "—",
                f"{o.probability_pct}%",
                key=o.id,
            )
        table = self.query_one("#pipeline-subs", ListTable)
        table.clear(columns=True)
        table.add_columns("market", "sent", "status", "quoted", "response")
        for p in placements.for_org(conn, org_id):
            for s in submissions.for_placement(conn, p.id):
                table.add_row(
                    orgs.get(conn, s.market_org_id).name, s.sent_on, s.status,
                    format_cents_compact(s.quoted_premium) if s.quoted_premium else "—",
                    s.response_on or "—",
                    key=s.id,
                )
        for o in opportunities.for_org(conn, org_id):
            for s in submissions.for_opportunity(conn, o.id):
                table.add_row(
                    orgs.get(conn, s.market_org_id).name, s.sent_on, s.status,
                    format_cents_compact(s.quoted_premium) if s.quoted_premium else "—",
                    s.response_on or "—",
                    key=s.id,
                )

    def show_placement(self, placement_id: str) -> None:
        """Fill the tower preview, carrier list, and sync-state for one placement."""
        conn = self.app.conn
        placement = placements.get(conn, placement_id)

        carriers = self.query_one("#carriers-table", ListTable)
        carriers.clear(columns=True)
        carriers.add_columns("carrier", "layer", "share", "premium")
        for row in projection.participants_for_placement(conn, placement_id):
            carriers.add_row(
                row["carrier"], row["layer_name"],
                f"{row['share_bps'] / 100:g}%",
                format_cents_compact(row["premium"]) if row["premium"] else "—",
            )

        preview = self.query_one("#tower-preview", TowerPreview)
        state = self.query_one("#sync-state", Static)
        if not placement.program_path:
            state.update("○ no program file linked")
            preview.show_placeholder()
            return
        path = Path(placement.program_path)
        from ... import sync

        if not path.exists():
            state.update(f"✗ file missing: {path}")
            preview.show_placeholder()
            return
        if placement.source_sha256 and sync.file_sha256(path) != placement.source_sha256:
            state.update("⚠ file changed on disk — re-sync to update")
        else:
            state.update(f"✓ in sync ({path.name})")
        preview.show_program(path)

    def on_data_table_row_selected(self, event: ListTable.RowSelected) -> None:
        table_id = event.data_table.id
        key = event.row_key.value or ""
        if table_id == "placements-table":
            self.show_placement(key)
        elif table_id == "documents-table":
            doc = next(
                (d for d in documents.for_org(self.app.conn, self.current_org_id) if d.id == key),
                None,
            )
            if doc:
                subprocess.Popen(["open", doc.path])
                self.notify(f"opening {doc.path}")

    def action_mark_primary(self) -> None:
        table = self.query_one("#contacts-table", ListTable)
        if not table.has_focus or table.cursor_row is None or table.row_count == 0:
            return
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        if key:
            contacts.set_primary(self.app.conn, key)
            self.notify("primary contact set")
            self.refresh_data()

    def action_task_done(self) -> None:
        table = self.query_one("#ov-tasks", ListTable)
        if not table.has_focus or table.cursor_row is None or table.row_count == 0:
            return
        key = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        if key:
            tasks_repo.complete(self.app.conn, key)
            self.notify("task done — u to undo")
            self.refresh_data()

    def action_undo(self) -> None:
        self.app.show_undo_result()
        self.refresh_data()

    def action_refresh(self) -> None:
        self.refresh_data()

    # --- add / edit (contextual per tab) --------------------------------------

    def _active_tab(self) -> str:
        return self.query_one(TabbedContent).active

    def _selected_key(self, table_id: str) -> str | None:
        table = self.query_one(f"#{table_id}", ListTable)
        if table.cursor_row is None or table.row_count == 0:
            return None
        return table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value

    def _push_form(self, spec, on_save) -> None:
        from ..widgets.forms import FormModal

        def saved(values):
            if values is not None:
                on_save(values)
                self.refresh_data()

        self.app.push_screen(FormModal(spec), saved)

    def action_add_here(self) -> None:
        from ..widgets import entity_forms as ef

        conn = self.app.conn
        org_id = self.current_org_id
        tab = self._active_tab()
        if tab == "tab-contacts":
            self._push_form(
                ef.contact_form(),
                lambda v: self.notify(
                    f"added {ef.apply_contact(conn, org_id, v).name}"
                ),
            )
        elif tab == "tab-placements":
            self._push_form(
                ef.placement_form(),
                lambda v: self.notify(
                    f"created {ef.apply_placement(conn, v, org_id).ref}"
                ),
            )
        elif tab == "tab-pipeline":
            self._push_form(
                ef.opportunity_form(),
                lambda v: self.notify(
                    f"created {ef.apply_opportunity(conn, v, org_id).ref}"
                ),
            )
        elif tab == "tab-documents":
            self._push_form(
                ef.document_form(),
                lambda v: documents.add(
                    conn, org_id, v["title"], v["path"], kind=v.get("kind")
                ),
            )
        elif tab == "tab-interactions":
            self.app.action_quick_capture()
        else:  # overview → a new task for this account
            self._push_form(
                ef.task_form(),
                lambda v: ef.apply_task(conn, v, org_id=org_id),
            )

    def action_edit_here(self) -> None:
        from ...repo import opportunities as opps_repo
        from ..widgets import entity_forms as ef

        conn = self.app.conn
        tab = self._active_tab()
        if tab == "tab-contacts":
            key = self._selected_key("contacts-table")
            if key:
                existing = contacts.get(conn, key)
                self._push_form(
                    ef.contact_form(existing),
                    lambda v: ef.apply_contact(conn, existing.org_id, v, existing),
                )
        elif tab == "tab-placements":
            key = self._selected_key("placements-table")
            if key:
                placement = placements.get(conn, key)
                if placement.program_path:
                    self.notify(
                        "structure comes from the towerkit file — edit it there; "
                        "status/commission are editable here",
                        severity="warning",
                    )
                self._push_form(
                    ef.placement_form(placement),
                    lambda v: ef.apply_placement(conn, v, placement.org_id, placement),
                )
        elif tab == "tab-pipeline":
            focused = self.focused
            if focused is not None and focused.id == "pipeline-subs":
                key = self._selected_key("pipeline-subs")
                if key:
                    sub = submissions.get(conn, key)
                    self._push_form(
                        ef.response_form(sub),
                        lambda v: ef.apply_response(conn, sub.id, v),
                    )
            else:
                key = self._selected_key("pipeline-opps")
                if key:
                    opp = opps_repo.get(conn, key)
                    self._push_form(
                        ef.opportunity_form(opp),
                        lambda v: ef.apply_opportunity(conn, v, opp.org_id, opp),
                    )
        elif tab == "tab-overview":
            key = self._selected_key("ov-tasks")
            if key and self.focused is not None and self.focused.id == "ov-tasks":
                task = tasks_repo.get(conn, key)
                self._push_form(
                    ef.task_form(task), lambda v: ef.apply_task(conn, v, existing=task)
                )
            else:  # otherwise e edits the account itself
                self._edit_org()
        else:
            self._edit_org()

    def _edit_org(self) -> None:
        from ..widgets import entity_forms as ef

        conn = self.app.conn
        existing = orgs.get(conn, self.current_org_id)
        self._push_form(
            ef.org_form_initial_profile(conn, existing),
            lambda v: ef.apply_org(conn, v, existing),
        )

    def action_new_submission(self) -> None:
        from ...repo import orgs as orgs_repo
        from ..widgets import entity_forms as ef

        conn = self.app.conn
        if not orgs_repo.list_orgs(conn, kind="market"):
            self.notify("no markets on file — create one in the markets screen (m, then a)",
                        severity="warning")
            return
        tab = self._active_tab()
        placement_id = self._selected_key("placements-table") if tab == "tab-placements" else None
        opportunity_id = self._selected_key("pipeline-opps") if tab == "tab-pipeline" else None
        if placement_id is None and opportunity_id is None:
            self.notify("select a placement or opportunity first (s works on those tabs)",
                        severity="warning")
            return
        self._push_form(
            ef.submission_form(conn),
            lambda v: ef.apply_submission(
                conn, v, placement_id=placement_id, opportunity_id=opportunity_id
            ),
        )
