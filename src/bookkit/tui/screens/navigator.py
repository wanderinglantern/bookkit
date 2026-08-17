"""Navigator — the home screen. Attention-first tree on the left; the right
pane is a WORKING DataTable whenever a group is selected: a/e/d/r/l act on
rows through commit-in-place forms, enter jumps to the full screen. Tree for
structure, tables for work (spec 2026-08-12)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..app import BookkitApp

from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.coordinate import Coordinate
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Static, Tree
from textual.widgets.tree import TreeNode

from ...dates import days_until
from ...forms.spec import Field
from ...money import format_cents_compact
from ...repo import batches as batches_repo
from ...repo import contacts, opportunities, orgs, placements
from ...repo import projects as projects_repo
from ...repo import rfi as rfi_repo
from ...repo import tasks as tasks_repo
from ...services import book, renewals, sla
from .. import theme
from ..theme import dash, date_text, days_text, money_text, right, status_text
from ..widgets.entity_actions import batched_write as _batched
from ..widgets.inline_edit import InlineTable
from ..widgets.tables import (
    ListTable,
    grouped_by_category,
    rfi_asker_cell,
    rfi_state_cell,
    task_detail_cell,
)
from ..widgets.tower_preview import TowerPreview

NodeData = tuple[str, Any]

# the second home for the hidden row-action keys: a one-line hint that names
# exactly the actions valid for the rows on screen (help lists them too)
ROW_HINTS = {
    "placements": (
        "[b]a[/b] add · [b]e[/b] edit · [b]r[/b] renew · [b]l[/b] layer · "
        "[b]enter[/b] opens account"
    ),
    "contacts": (
        "[b]i[/b] edit in cell · [b]a[/b] add · [b]e[/b] edit form · "
        "[b]enter[/b] opens account"
    ),
    "opportunities": "[b]a[/b] add · [b]e[/b] edit · [b]enter[/b] opens account",
    "batches": "[b]R[/b] revert this change · [b]enter[/b] before → after",
    "tasks": (
        "[b]i[/b] edit in cell · [b]a[/b] add · [b]e[/b] edit form · "
        "[b]d[/b] done · [b]enter[/b] opens account"
    ),
    "projects": "[b]a[/b] add · [b]e[/b] edit · [b]enter[/b] opens account",
    "overdue": "[b]e[/b] edit · [b]r[/b] renew · [b]l[/b] layer · [b]enter[/b] opens account",
    "renewals": "[b]e[/b] edit · [b]r[/b] renew · [b]l[/b] layer · [b]enter[/b] opens account",
    "needs": "[b]enter[/b] opens account",
    "sla": "[b]enter[/b] opens account",
    "onboarding": "[b]enter[/b] resume onboarding",
    "requests": "[b]a[/b] add · [b]e[/b] edit · [b]enter[/b] opens account",
    "rfi": "[b]e[/b] edit request · [b]enter[/b] opens account",
}
ADDABLE = ("placements", "contacts", "opportunities", "tasks", "projects", "requests")

# columns editable straight in the table (i) — values parse exactly like the
# modal forms, and each commit is one undoable field write
CONTACT_INLINE = {
    2: Field("role", "role"),
    3: Field("title", "title"),
    4: Field("email", "email", "email"),
    5: Field("phone", "phone", "phone"),
}
TASK_INLINE = {
    0: Field("due_on", "due", "date"),
    1: Field("title", "task", required=True),
    2: Field("category", "category"),
    3: Field("description", "description"),
}


def _section(label: str, count: int | None = None) -> str:
    """A tree section header: quiet bold caps, count in dim."""
    suffix = f" [{theme.DIM}]· {count}[/]" if count is not None else ""
    return f"[b {theme.DIM}]{label}[/]{suffix}"


def _walk(node: TreeNode) -> Iterator[TreeNode]:
    """Every node under `node`, depth-first. Children added while walking are
    visited (the restore pass fills accounts in as it expands them)."""
    for child in node.children:
        yield child
        yield from _walk(child)


def _attention_label(key: str, label: str, count: int) -> str:
    """Attention leaves: red+◆ when overdue items exist, dim when empty."""
    if count == 0:
        return f"[{theme.DIM}]{label} · 0[/]"
    if key == "overdue":
        return f"[b {theme.RED}]◆ {label} · {count}[/]"
    return f"{label} [{theme.DIM}]·[/] [b]{count}[/b]"


class ConfirmRevertBatch(ModalScreen):
    """One look before putting a batched MCP change back. On a conflict this
    becomes the refusal list instead of a yes/no — force is an explicit second
    choice, never a default."""

    app: BookkitApp
    BINDINGS = [
        Binding("escape,n", "decline", "No"),
        Binding("y,enter", "accept", "Yes"),
        Binding("f", "force", "Force", show=False),
    ]

    def __init__(self, batch, plan) -> None:
        super().__init__()
        # NOT self.batch: Screen.batch() is a Textual method
        self._batch = batch
        self._plan = plan

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static("REVERT MCP CHANGE", classes="modal-title")
            yield Static(
                f"{self._batch.ref}  {self._batch.tool}\n{self._batch.summary}"
            )
            if self._plan.conflicts:
                lines = "\n".join(
                    f"  {c.change.field}: set {c.change.new_value!r}, "
                    f"now {c.current_value!r}"
                    for c in self._plan.conflicts
                )
                yield Static(
                    f"[{theme.AMBER}]BLOCKED[/] — changed since:\n{lines}"
                )
                clean = (
                    len(self._plan.updates)
                    + len(self._plan.creates)
                    + len(self._plan.deletes)
                )
                yield Static(
                    f"{clean} other change(s) would revert cleanly",
                    classes="hint",
                )
                yield Static("f force the rest · esc cancel", classes="hint")
            else:
                yield Static(
                    "y / enter revert · esc cancel", classes="hint"
                )

    def action_accept(self) -> None:
        if self._plan.conflicts:
            return  # y is inert on a conflicted batch — force is explicit
        self.dismiss("revert")

    def action_force(self) -> None:
        if self._plan.conflicts:
            self.dismiss("force")

    def action_decline(self) -> None:
        self.dismiss(None)


class BatchDetail(ModalScreen):
    """Field-level before → after for one MCP batch, conflicts marked."""

    app: BookkitApp
    BINDINGS = [Binding("escape,enter,q", "close", "Close")]

    def __init__(self, batch, plan) -> None:
        super().__init__()
        # NOT self.batch: Screen.batch() is a Textual method
        self._batch = batch
        self._plan = plan

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static("MCP CHANGE", classes="modal-title")
            yield Static(
                f"{self._batch.ref}  {self._batch.tool}  {self._batch.created_at}"
                f"\n{self._batch.summary}"
            )
            lines: list[str] = []
            for c in self._plan.creates:
                lines.append(f"created {c.entity_type} {c.entity_id}")
            for c in self._plan.deletes:
                lines.append(f"deleted {c.entity_type} {c.entity_id}")
            for c in self._plan.updates:
                lines.append(
                    f"{c.entity_type}.{c.field}: {c.old_value!r} → {c.new_value!r}"
                )
            for conflict in self._plan.conflicts:
                c = conflict.change
                if self._batch.reverted_at:
                    # after a revert, current values legitimately differ from
                    # the batch's — that is history, not external tampering
                    lines.append(
                        f"{c.entity_type}.{c.field}: "
                        f"{c.old_value!r} → {c.new_value!r}"
                    )
                else:
                    lines.append(
                        f"[{theme.AMBER}]{c.entity_type}.{c.field}: "
                        f"{c.old_value!r} → {c.new_value!r} · since changed to "
                        f"{conflict.current_value!r}[/]"
                    )
            yield Static("\n".join(lines) or "no recorded changes")
            state = (
                f"reverted {self._batch.reverted_at}"
                if self._batch.reverted_at else "live · R reverts it"
            )
            yield Static(f"{state} · esc closes", classes="hint")

    def action_close(self) -> None:
        self.dismiss(None)


class NavigatorScreen(Screen):
    app: BookkitApp
    BINDINGS = [
        # screen jumps live in the palette and in ? — the footer is one
        # row and the ROW ACTIONS are what a user needs named there
        # the glance card says "j/k move" and help.py teaches j/k as THE
        # movement keys; they were bound on ListTable only, and the card
        # renders while the TREE has focus — so the first guidance the app
        # ever showed was wrong. Cheaper to make them real than to withdraw
        # the promise, and it makes vim movement uniform.
        Binding("j", "tree_down", "Down", show=False),
        Binding("k", "tree_up", "Up", show=False),
        Binding("t", "open_today", "Today", show=False),
        Binding("b", "open_book", "Book", show=False),
        Binding("c", "open_calendar", "Calendar", show=False),
        Binding("p", "open_pipeline", "Pipeline", show=False),
        Binding("m", "open_markets", "Markets", show=False),
        Binding("w", "open_team", "Team", show=False),
        Binding("comma", "settings", "Setup", key_display=",", show=False),
        Binding("a", "add_row", "Add", show=False),
        Binding("e", "edit_row", "Edit", show=False),
        Binding("d", "task_done", "Done (task)", show=False),
        # D drops the task the cursor is on: `d` completes, and until this
        # existed a task you simply did not want had to be recorded as done
        # (review F34). Shift for the destructive sibling, as on the account
        # screen.
        Binding("D", "drop_row", "Drop (task)", show=False),
        Binding("r", "renew_row", "Renew", show=False),
        Binding("l", "edit_layer_row", "Layer", show=False),
        Binding("o", "onboard", "Onboard client", show=False),
        Binding("x", "export_row", "Export open items"),
        Binding("u", "undo", "Undo"),
        Binding("R", "refresh", "Refresh", show=False),
        Binding("tab", "hop", "tree ⇄ rows", show=False),
        Binding("escape", "back_to_tree", "Back to tree", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._current: NodeData = ("none", None)
        self._row_org: dict[str, str] = {}  # table row key → org id (for enter)
        self._attention: dict[str, list[Any]] = {}
        self._batches: list[Any] = []  # recent MCP batches, newest first
        self._fill_key: Any = None  # which rows the table holds, for cursor keeping
        self._refresh_pending = False  # a refresh deferred while a cell edit is open

    def compose(self) -> ComposeResult:
        yield Static(id="status-bar")
        with Horizontal(id="nav-split"):
            yield Tree("book", id="nav-tree")
            with Vertical(id="nav-pane"):
                yield Static(id="nav-card")
                yield InlineTable(id="nav-table")
                yield Static(id="table-hint")
                yield TowerPreview(id="nav-preview")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#nav-table", InlineTable).inline_initial = self._inline_initial
        self.refresh_data()
        self.query_one("#nav-tree", Tree).focus()

    def on_screen_resume(self) -> None:
        self.refresh_data()

    # -- tree ------------------------------------------------------------------

    def refresh_data(self) -> None:
        conn = self.app.conn
        today = date.today()
        tree = self.query_one("#nav-tree", Tree)
        # tree.clear() throws away expansion, which shortens the tree, which
        # makes Textual clamp cursor_line onto whatever node now sits there —
        # silently teleporting the user (and swapping the pane out from under
        # anything open on it). Put them back once the rebuild is done.
        was_expanded = {n.data for n in _walk(tree.root) if n.is_expanded and n.data}
        was_on = self._current
        tree.clear()
        tree.show_root = False
        tree.root.expand()

        items = renewals.upcoming(conn, today, days=120)
        overdue = [i for i in items if i.days_remaining < 0]
        soon = [i for i in items if i.days_remaining >= 0]
        needs = projects_repo.needs_due(conn, today, days=120)
        due_tasks = tasks_repo.open_tasks(conn, due_by=today.isoformat())
        late = sla.past_sla(conn, today)
        from ...services import onboarding as onboarding_svc
        from ...services import rfi as rfi_svc

        pending_onboarding = onboarding_svc.incomplete_clients(conn, today)
        chases = rfi_svc.outstanding_requests(conn, today, days=120)
        self._attention = {
            "overdue": overdue, "renewals": soon, "needs": needs,
            "tasks": due_tasks, "sla": late, "onboarding": pending_onboarding,
            "rfi": chases,
        }
        att = tree.root.add(_section("ATTENTION"), expand=True, data=("att-root", None))
        for key, label, count in (
            ("overdue", "overdue renewals", len(overdue)),
            ("renewals", "renewals ≤ 120d", len(soon)),
            ("needs", "project needs due", len(needs)),
            ("tasks", "tasks due", len(due_tasks)),
            ("sla", "submissions past SLA", len(late)),
            ("onboarding", "onboarding incomplete", len(pending_onboarding)),
            ("rfi", "requests to chase", len(chases)),
        ):
            att.add_leaf(_attention_label(key, label, count), data=("att", key))

        clients = orgs.list_orgs(conn, kind="client")
        overdue_orgs = {i.org.id for i in overdue}
        accounts = tree.root.add(
            _section("ACCOUNTS", len(clients)), expand=True, data=("accounts-root", None)
        )
        for org in clients:
            badge = f"[{theme.RED}]◆ [/]" if org.id in overdue_orgs else ""
            node = accounts.add(f"{badge}{org.name}", data=("account", org.id))
            node.allow_expand = True
        markets_root = tree.root.add(_section("MARKETS"), data=("markets-root", None))
        for master, kids in orgs.market_families(conn):
            if kids:  # a family: master expands to its issuing companies
                family = markets_root.add(master.name, data=("market", master.id))
                for kid in kids:
                    family.add_leaf(kid.name, data=("market", kid.id))
            else:
                markets_root.add_leaf(master.name, data=("market", master.id))
        self._batches = batches_repo.recent(
            conn, since=(today - timedelta(days=14)).isoformat()
        )
        if self._batches:
            # Its own section, deliberately NOT an attention leaf: attention
            # means "act on this" and carries the 120-day bucket window plus
            # the overdue-never-falls-off rule. This is an audit list — most
            # rows need no action, and it hides itself when empty.
            tree.root.add_leaf(
                _section("MCP CHANGES", len(self._batches)),
                data=("batches", None),
            )
        self._restore_tree_place(tree, was_expanded, was_on)
        self._summary = book.summary(conn)
        self._n_clients = len(clients)
        self._render_status_bar()
        self._render_pane()

    def _restore_tree_place(
        self, tree: Tree, was_expanded: set, was_on: NodeData
    ) -> None:
        """Re-expand what the user had open and put the cursor back on the
        node it was on. Nodes that no longer exist (a deleted account) simply
        do not come back — the cursor then lands wherever Textual puts it."""
        for node in _walk(tree.root):
            if node.data in was_expanded and not node.is_expanded:
                if node.data and node.data[0] == "account":
                    # group leaves normally arrive via on_tree_node_expanded,
                    # which is a message — too late for the cursor lookup below
                    self._add_account_groups(node)
                node.expand()
        target = next((n for n in _walk(tree.root) if n.data == was_on), None)
        if target is not None and target.line >= 0:
            self._current = was_on
            tree.cursor_line = target.line

    def _render_status_bar(self) -> None:
        s = self._summary
        overdue = len(self._attention["overdue"])
        tasks = len(self._attention["tasks"])
        parts = [
            f"[b {theme.GOLD}]bookkit[/]",
            f"{self._n_clients} accounts",
            f"[{theme.GREEN}]{format_cents_compact(s.total_premium)} bound[/]"
            f" [{theme.DIM}]({s.bound_placements} placements)[/]",
        ]
        parts.append(
            f"[b {theme.RED}]◆ {overdue} overdue[/]"
            if overdue
            else f"[{theme.DIM}]nothing overdue[/]"
        )
        parts.append(
            f"[{theme.AMBER}]{tasks} tasks due[/]"
            if tasks
            else f"[{theme.DIM}]no tasks due[/]"
        )
        bar = f"  [{theme.RULE}]·[/]  ".join(parts)
        self.query_one("#status-bar", Static).update(bar)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded) -> None:
        data = event.node.data
        if data is None or data[0] != "account":
            return
        self._add_account_groups(event.node)

    def _add_account_groups(self, node: TreeNode) -> None:
        """The lazy children of an account node: its lists, with counts."""
        if node.children or not node.data:
            return
        conn = self.app.conn
        org_id = node.data[1]
        counts = (
            ("placements", len(placements.for_org(conn, org_id))),
            ("contacts", len(contacts.for_org(conn, org_id))),
            ("opportunities", len(opportunities.for_org(conn, org_id))),
            ("tasks", len(tasks_repo.open_tasks(conn, org_id=org_id))),
            ("projects", len(projects_repo.projects_for_org(conn, org_id))),
            ("requests", len(rfi_repo.requests_for_org(conn, org_id))),
        )
        for group, count in counts:
            node.add_leaf(f"{group} ({count})", data=("group", (group, org_id)))

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted) -> None:
        self._current = event.node.data or ("none", None)
        self._render_pane()

    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if data and data[0] == "account":
            self.app.open_account(data[1])
        elif data and data[0] == "market":
            from .markets import MarketDetailScreen

            self.app.push_screen(MarketDetailScreen(data[1]))
        elif data and data[0] in ("att", "group"):
            # enter on a list node dives into its rows: cursor lands on the
            # table so e/a/d/r/l work immediately; esc hops back out
            self._dive()

    def _dive(self) -> None:
        table = self.query_one("#nav-table", ListTable)
        if table.display and table.row_count:
            table.focus()

    def on_descendant_focus(self, event) -> None:
        # focus lands asynchronously — the hint re-renders when it does,
        # so "tab into rows" flips to "esc back to tree" at the right moment
        self._flush_pending_refresh()
        self._render_hint_for_current()

    def _flush_pending_refresh(self) -> None:
        """A refresh deferred by a live cell edit lands once the editor is
        gone. The editor is mounted on the SCREEN, so its own focus fires
        on_descendant_focus — without the `editing` check the refresh flushed
        straight into the open editor, mid-tab-hop, which is exactly what the
        deferral exists to prevent."""
        table = self.query_one("#nav-table", InlineTable)
        if not self._refresh_pending or table.editing:
            return
        self._refresh_pending = False
        self.refresh_data()

    def on_descendant_blur(self, event) -> None:
        self._render_hint_for_current()

    def _render_hint_for_current(self) -> None:
        kind, payload = self._current
        table = self.query_one("#nav-table", ListTable)
        if kind == "att":
            self._render_hint(payload, table)
        elif kind == "group":
            self._render_hint(payload[0], table)
        elif kind == "batches":
            self._render_hint("batches", table)

    def action_hop(self) -> None:
        """tab bounces between the tree and the rows it points at."""
        table = self.query_one("#nav-table", ListTable)
        if table.has_focus:
            self.query_one("#nav-tree", Tree).focus()
        elif table.display and table.row_count:
            table.focus()

    def action_back_to_tree(self) -> None:
        tree = self.query_one("#nav-tree", Tree)
        if not tree.has_focus:
            tree.focus()

    # -- right pane --------------------------------------------------------------

    def _render_pane(self) -> None:
        kind, payload = self._current
        card = self.query_one("#nav-card", Static)
        table = self.query_one("#nav-table", InlineTable)
        preview = self.query_one("#nav-preview", TowerPreview)
        preview.display = False
        self._row_org.clear()
        # refilling the same rows keeps the cursor on the row you were on
        new_key = (kind, payload)
        keep = table.cursor_row if new_key == self._fill_key and table.display else None
        self._fill_key = new_key
        # the table is about to point at different rows — an open editor is
        # anchored to a cell that will not survive it (card panes never clear())
        table.cancel_edit()
        table.inline_fields = {}
        if kind == "att":
            card.display = False
            table.display = True
            self._fill_attention_table(table, payload)
            self._render_hint(payload, table)
        elif kind == "group":
            card.display = False
            table.display = True
            self._fill_group_table(table, *payload)
            self._render_hint(payload[0], table)
        elif kind == "batches":
            card.display = False
            table.display = True
            self._fill_batches_table(table)
            self._render_hint("batches", table)
        elif kind == "account":
            table.display = False
            card.display = True
            self._render_hint(None)
            card.update(self._account_card(payload))
        elif kind == "market":
            table.display = False
            card.display = True
            self._render_hint(None)
            card.update(self._market_card(payload))
        else:
            table.display = False
            card.display = True
            self._render_hint(None)
            card.update(self._glance_card())
        if kind in ("att", "group", "batches") and keep is not None and table.row_count:
            table.move_cursor(row=min(keep, table.row_count - 1))

    def _fill_batches_table(self, table: InlineTable) -> None:
        """Recent MCP writes, newest first — the audit trail behind R."""
        from ...services import batches as batches_svc

        conn = self.app.conn
        table.clear(columns=True)
        table.add_columns("ref", "when", "account", "tool", "what", "state")
        labels = batches_svc.account_names(conn, self._batches)
        for batch in self._batches:
            account: str | Text = dash()
            if batch.org_id:
                label = labels.get(batch.org_id)
                account = (
                    label if label is not None
                    else Text("(deleted account)", style=theme.DIM)
                )
            state = (
                Text("reverted", style=theme.DIM)
                if batch.reverted_at
                else Text("live", style=theme.GREEN)
            )
            table.add_row(
                batch.ref, batch.created_at[11:16], account, batch.tool,
                Text(batch.summary, style=theme.DIM), state,
                key=f"batch:{batch.id}",
            )

    def _render_hint(self, hint_key: str | None, table: ListTable | None = None) -> None:
        """The hint line under the table — the visible home for hidden keys."""
        hint = self.query_one("#table-hint", Static)
        if hint_key is None:
            hint.display = False
            return
        hint.display = True
        if table is not None and table.row_count == 0:
            text = (
                "empty — [b]a[/b] adds the first row"
                if hint_key in ADDABLE
                else "nothing here — that's good"
            )
            hint.update(f"[{theme.DIM}]{text}[/]")
            return
        actions = ROW_HINTS.get(hint_key, "[b]enter[/b] opens account")
        hop = (
            "[b]esc[/b] back to tree — "
            if table is not None and table.has_focus
            else "[b]enter[/b]/[b]tab[/b] into rows — "
        )
        hint.update(f"[{theme.DIM}]{hop}{actions}[/]")

    def _glance_card(self) -> Text:
        """The landing view: the book at a glance instead of an empty pane.

        Returns a Text with no_wrap + ellipsis rather than a markup string.
        Each renewal used to be one long line in a Static, so every row wrapped
        onto a second line — on the first thing seen at every launch, at every
        terminal width (review F6). Truncating one long account name beats
        destroying the alignment of the whole block."""
        s = self._summary
        card = Text(no_wrap=True, overflow="ellipsis")
        card.append("THE BOOK AT A GLANCE\n", style=f"bold {theme.GOLD}")
        card.append("\n")
        card.append(
            f"  {s.accounts} accounts with bound business · "
            f"{s.bound_placements} bound placements\n"
        )
        card.append("  ")
        card.append(format_cents_compact(s.total_premium), style=theme.GREEN)
        card.append(" premium · ")
        card.append(format_cents_compact(s.total_commission), style=theme.GREEN)
        card.append(" commission\n\n")

        nxt = (self._attention["overdue"] + self._attention["renewals"])[:5]
        if nxt:
            card.append("NEXT RENEWALS\n", style=f"bold {theme.DIM}")
            for item in nxt:
                days = item.days_remaining
                programme = book._program_label(item.placement.program_name)
                # the line of cover is what renews; without projected lines the
                # programme has to stand in for it, and then it must not ALSO
                # be printed as its own column (most of why this row overflowed)
                has_line = bool(item.line_ends)
                what = item.line_ends[0][0] if has_line else programme
                # right-align by padding the PLAIN text and carrying the style
                # across: a Text's own justify= does nothing once it is
                # appended into a longer line
                when = theme.days_text(days)
                money = theme.money_text(item.placement.total_premium)
                card.append(f"  {item.renewal_on or item.placement.period_to}  ")
                card.append(when.plain.rjust(12), style=when.style)
                card.append("  ")
                card.append(f"{item.org.name} — ")
                card.append(what, style="bold")
                if has_line:
                    card.append(f"  {programme}", style=theme.DIM)
                card.append("  ")
                card.append(money.plain.rjust(8), style=money.style)
                card.append("\n")
            card.append("\n")
        card.append(
            "j/k move · space expands · enter opens · ? all keys", style=theme.DIM
        )
        return card

    def _fill_attention_table(self, table: InlineTable, which: str) -> None:
        conn = self.app.conn
        today = date.today()
        table.clear(columns=True)
        if which in ("overdue", "renewals"):
            table.add_columns(
                "renews", right("due in"), "account", "line of cover due",
                "program", "status", right("premium"),
            )
            for item in self._attention[which]:
                key = f"placement:{item.placement.id}"
                self._row_org[key] = item.org.id
                table.add_row(
                    date_text(item.renewal_on, item.days_remaining),
                    days_text(item.days_remaining), item.org.name,
                    theme.lines_text(item.line_ends, today),
                    Text(book._program_label(item.placement.program_name), style=theme.DIM),
                    status_text(item.placement.status),
                    money_text(item.placement.total_premium),
                    key=key,
                )
        elif which == "needs":
            table.add_columns("needed by", right("due in"), "account", "need", "status")
            for need in self._attention["needs"]:
                key = f"need:{need['id']}"
                self._row_org[key] = need["org_id"]
                days = days_until(need["needed_by"], today)
                table.add_row(
                    date_text(need["needed_by"], days), days_text(days),
                    need["org_name"], f"{need['line']} — {need['project_name']}",
                    status_text(need["status"]), key=key,
                )
        elif which == "tasks":
            table.add_columns("due", "task", "category", "description", "detail", "account")
            table.inline_fields = TASK_INLINE
            for task in grouped_by_category(self._attention["tasks"]):
                key = f"task:{task.id}"
                name = ""
                if task.org_id:
                    self._row_org[key] = task.org_id
                    try:  # a task can outlive its soft-deleted account
                        name = orgs.get(conn, task.org_id).name
                    except KeyError:
                        name = "(deleted account)"
                due = (
                    date_text(task.due_on, days_until(task.due_on, today))
                    if task.due_on else dash()
                )
                table.add_row(
                    due, task.title,
                    Text(task.category, style=theme.AMBER) if task.category else dash(),
                    task.description or dash(),
                    task_detail_cell(task), name, key=key,
                )
        elif which == "sla":
            table.add_columns("market", "account", "sent", right("out"))
            for item in self._attention["sla"]:
                key = f"submission:{item.submission.id}"
                self._row_org[key] = item.account.id
                table.add_row(
                    item.market.name, item.account.name,
                    item.submission.sent_on,
                    days_text(-item.days_out), key=key,
                )
        elif which == "onboarding":
            table.add_columns("account", "missing", "since")
            for org, missing in self._attention["onboarding"]:
                key = f"org:{org.id}"
                self._row_org[key] = org.id
                table.add_row(
                    org.name, Text(missing, style=theme.AMBER),
                    org.created_at[:10], key=key,
                )
        elif which == "rfi":
            table.add_columns(
                "response due", right("due in"), "account", "request",
                "asked by", "scope", "open",
            )
            from ...services import rfi as rfi_svc

            for chase in self._attention["rfi"]:
                key = f"rfi:{chase.request.id}"
                self._row_org[key] = chase.request.org_id
                table.add_row(
                    date_text(chase.earliest_due, chase.days_remaining),
                    days_text(chase.days_remaining), chase.org_name,
                    chase.request.title,
                    Text(chase.market_name or "—", style=theme.DIM),
                    Text(rfi_svc.scope_label(conn, chase.request), style=theme.DIM),
                    f"{chase.open_count} of {chase.total_count}",
                    key=key,
                )

    def _fill_group_table(self, table: InlineTable, group: str, org_id: str) -> None:
        conn = self.app.conn
        table.clear(columns=True)
        if group == "placements":
            table.add_columns(
                "ref", "program", "effective", "expires", right("due in"),
                "status", right("premium"),
            )
            rows = sorted(
                placements.for_org(conn, org_id),
                key=lambda p: (days_until(p.period_to) < 0, abs(days_until(p.period_to))),
            )
            for p in rows:
                days = days_until(p.period_to)
                key = f"placement:{p.id}"
                self._row_org[key] = org_id
                table.add_row(
                    p.ref, p.program_name, p.period_from,
                    date_text(p.period_to, days), days_text(days),
                    status_text(p.status), money_text(p.total_premium),
                    key=key,
                )
        elif group == "contacts":
            table.add_columns("", "name", "role", "title", "email", "phone")
            table.inline_fields = CONTACT_INLINE
            for c in contacts.for_org(conn, org_id):
                key = f"contact:{c.id}"
                self._row_org[key] = org_id
                table.add_row(
                    Text("★", style=theme.GOLD) if c.is_primary else "",
                    c.name, c.role.replace("_", " ") if c.role else dash(),
                    c.title or dash(),
                    c.email or dash(), c.phone or c.mobile or dash(), key=key,
                )
        elif group == "opportunities":
            table.add_columns(
                "ref", "title", "stage", right("target"), "eff", right("prob")
            )
            for o in opportunities.for_org(conn, org_id):
                key = f"opportunity:{o.id}"
                self._row_org[key] = org_id
                table.add_row(
                    o.ref, o.title, status_text(o.stage),
                    money_text(o.target_premium),
                    o.target_effective or dash(),
                    Text(f"{o.probability_pct}%", justify="right"), key=key,
                )
        elif group == "tasks":
            table.add_columns("due", "task", "category", "description", "detail", "status")
            table.inline_fields = TASK_INLINE
            today = date.today()
            for task in grouped_by_category(tasks_repo.open_tasks(conn, org_id=org_id)):
                key = f"task:{task.id}"
                self._row_org[key] = org_id
                due = (
                    date_text(task.due_on, days_until(task.due_on, today))
                    if task.due_on else dash()
                )
                table.add_row(
                    due, task.title,
                    Text(task.category, style=theme.AMBER) if task.category else dash(),
                    task.description or dash(),
                    task_detail_cell(task), status_text(task.status), key=key,
                )
        elif group == "projects":
            table.add_columns("ref", "project", "status", "start", "end")
            for project in projects_repo.projects_for_org(conn, org_id):
                key = f"project:{project.id}"
                self._row_org[key] = org_id
                table.add_row(
                    project.ref, project.name, status_text(project.status),
                    project.start_on or dash(), project.end_on or dash(), key=key,
                )
        elif group == "requests":
            # same column order as the account tab's request list: the signal
            # columns lead so a narrow terminal crops the descriptive ones
            table.add_columns("ref", "request", "state", "due", "asked by", "asked")
            for request in rfi_repo.requests_for_org(conn, org_id):
                key = f"rfi:{request.id}"
                self._row_org[key] = org_id
                table.add_row(
                    request.ref, request.title,
                    rfi_state_cell(conn, request),
                    date_text(request.due_on, days_until(request.due_on))
                    if request.due_on else dash(),
                    rfi_asker_cell(conn, request),
                    request.requested_on,
                    key=key,
                )

    def _account_card(self, org_id: str) -> str:
        conn = self.app.conn
        org = orgs.get(conn, org_id)
        nxt = renewals.next_for_org(conn, org_id)
        if nxt is None:
            renewal = f"[{theme.DIM}]none scheduled[/]"
        else:
            what = nxt.line_ends[0][0] if nxt.line_ends else nxt.placement.program_name
            if nxt.days_remaining < 0:
                renewal = (
                    f"[b {theme.RED}]◆ {nxt.renewal_on or nxt.placement.period_to} "
                    f"({-nxt.days_remaining}d overdue — {what})[/]"
                )
            else:
                style = theme.AMBER if nxt.days_remaining <= 60 else theme.FG
                renewal = (
                    f"[{style}]{nxt.renewal_on or nxt.placement.period_to} "
                    f"({nxt.days_remaining}d — {what})[/]"
                )
        bound = [
            p for p in placements.for_org(conn, org_id) if p.status == "bound"
        ]
        premium = sum(p.total_premium or 0 for p in bound)
        n_contacts = len(contacts.for_org(conn, org_id))
        n_tasks = len(tasks_repo.open_tasks(conn, org_id=org_id))
        lines = [
            f"[b]{org.name}[/b]  [{theme.DIM}]{org.ref}[/]",
            "",
            f"  status [b]{org.status}[/b]   owner {org.owner or '—'}",
            f"  [{theme.GREEN}]{format_cents_compact(premium)}[/] bound premium "
            f"across {len(bound)} placements",
            f"  {n_contacts} contacts · {n_tasks} open tasks",
            f"  next renewal  {renewal}",
            "",
            f"[{theme.DIM}][b]space[/b] expands · [b]enter[/b] opens the account screen[/]",
        ]
        return "\n".join(lines)

    def _market_card(self, org_id: str) -> str:
        org = orgs.get(self.app.conn, org_id)
        profile = orgs.get_market_profile(self.app.conn, org_id)
        kind = profile.market_type if profile and profile.market_type else "market"
        rating = (
            f"   AM Best {profile.am_best_rating}"
            if profile and profile.am_best_rating
            else ""
        )
        return f"[b]{org.name}[/b]  ({kind}){rating}\n\nenter opens the market screen"

    # -- table row context -------------------------------------------------------

    def _selected_row(self, quiet: bool = False) -> tuple[str, str] | None:
        """The row under the cursor, or None with a reason said out loud.

        Row actions require the TABLE to hold focus — pressing e/d/r/l while
        browsing the tree must never mutate a row the user isn't looking at.
        The gate is right; returning in SILENCE was the bug. Six of seven row
        actions produced no modal, no message and no change, which is
        indistinguishable from a broken app, and the hint line lists those
        keys on the same screen. `quiet=True` is for callers that already
        have a better message."""
        from textual.coordinate import Coordinate

        table = self.query_one("#nav-table", ListTable)
        if not table.display or table.row_count == 0:
            if not quiet:
                self.notify("nothing to act on here", severity="warning")
            return None
        if not table.has_focus:
            if not quiet:
                self.notify(
                    "press tab or enter to work the rows first", severity="warning"
                )
            return None
        if table.cursor_row is None:
            return None
        value = table.coordinate_to_cell_key(Coordinate(table.cursor_row, 0)).row_key.value
        if not value:
            return None
        kind, _, entity_id = str(value).partition(":")
        return kind, entity_id

    def _export_target_org(self) -> str | None:
        """Resolve the client under the cursor for x: the account tree node
        itself, or a focused table row that carries an org (self._row_org)."""
        if self._current[0] == "account":
            return str(self._current[1])
        row = self._selected_row()
        if row is None:
            return None
        kind, entity_id = row
        return self._row_org.get(f"{kind}:{entity_id}")

    def on_data_table_row_selected(self, event: ListTable.RowSelected) -> None:
        key = str(event.row_key.value or "")
        if key.startswith("batch:"):
            # enter on an MCP change opens its before→after, not the account —
            # the audit detail is what this table exists for
            from ...services import batches as batches_svc

            try:
                batch = batches_repo.get(self.app.conn, key.removeprefix("batch:"))
            except KeyError:
                return
            plan = batches_svc.plan_revert(self.app.conn, batch)
            self.app.push_screen(BatchDetail(batch, plan))
            return
        org_id = self._row_org.get(key)
        if not org_id:
            return
        if key.startswith("org:") and self._current == ("att", "onboarding"):
            from .onboarding import OnboardingScreen

            self.app.push_screen(OnboardingScreen(org_id))
            return
        self.app.open_account(org_id)

    def on_data_table_row_highlighted(self, event: ListTable.RowHighlighted) -> None:
        preview = self.query_one("#nav-preview", TowerPreview)
        key = str(event.row_key.value or "") if event.row_key else ""
        kind, _, entity_id = key.partition(":")
        if kind == "placement":
            try:  # row keys can go stale mid-rebuild (undo, deletes)
                placement = placements.get(self.app.conn, entity_id)
            except KeyError:
                preview.display = False
                return
            if placement.program_path and Path(placement.program_path).exists():
                preview.display = True
                preview.show_program(Path(placement.program_path))
                return
        preview.display = False

    # -- inline cell edits ---------------------------------------------------------

    def _inline_initial(self, row_key: str, field_key: str) -> str:
        kind, _, entity_id = row_key.partition(":")
        conn = self.app.conn
        try:
            if kind == "contact":
                return getattr(contacts.get(conn, entity_id), field_key) or ""
            if kind == "task":
                return getattr(tasks_repo.get(conn, entity_id), field_key) or ""
        except KeyError:
            pass
        return ""

    def on_inline_table_cell_edited(self, event: InlineTable.CellEdited) -> None:
        kind, _, entity_id = event.row_key.partition(":")
        conn = self.app.conn
        if kind == "contact":
            with _batched(
                self, tool="inline_edit", summary=f"edited contact {event.field.key}"
            ):
                contacts.update(
                    conn, entity_id, **{event.field.key: event.value}
                )
        elif kind == "task":
            with _batched(
                self, tool="inline_edit", summary=f"edited task {event.field.key}"
            ):
                tasks_repo.update(
                    conn, entity_id, **{event.field.key: event.value}
                )
        else:
            return
        self.notify(f"{event.field.label} saved — u undoes")
        # refreshing mid-edit would pull rows out from under the editor while
        # tab walks the row; flush when the editor closes and focus returns
        if event.table.editing:
            self._refresh_pending = True
        else:
            self.refresh_data()

    # -- row actions ---------------------------------------------------------------

    def action_tree_down(self) -> None:
        self._move_tree(1)

    def action_tree_up(self) -> None:
        self._move_tree(-1)

    def _move_tree(self, delta: int) -> None:
        from textual.widgets import Tree

        tree = self.query_one("#nav-tree", Tree)
        if not tree.has_focus:
            return          # j/k belong to whichever widget holds focus
        tree.action_cursor_down() if delta > 0 else tree.action_cursor_up()

    def action_add_row(self) -> None:
        from ...forms import entities as ef
        from ..widgets import entity_actions

        kind, payload = self._current
        if kind != "group":
            # `a` is listed in the hint on attention tables that have no
            # account to hang a new row on, so it looked broken rather than
            # inapplicable
            self.notify(
                "a adds to an account's group — open a client first",
                severity="warning",
            )
            return
        group, org_id = payload
        conn = self.app.conn
        if group == "contacts":
            entity_actions.push_form(
                self, ef.contact_form(),
                lambda v: self.notify(f"added {ef.apply_contact(conn, org_id, v).name}"),
            )
        elif group == "placements":
            entity_actions.push_form(
                self, ef.placement_form(conn=conn),
                lambda v: self.notify(f"created {ef.apply_placement(conn, v, org_id).ref}"),
            )
        elif group == "opportunities":
            entity_actions.push_form(
                self, ef.opportunity_form(conn=conn),
                lambda v: self.notify(f"created {ef.apply_opportunity(conn, v, org_id).ref}"),
            )
        elif group == "tasks":
            entity_actions.push_form(
                self, ef.task_form(conn=conn, default_org_id=org_id),
                lambda v: ef.apply_task(conn, v, org_id=org_id),
            )
        elif group == "projects":
            entity_actions.push_form(
                self, ef.project_form(),
                lambda v: self.notify(f"created {ef.apply_project(conn, v, org_id).ref}"),
            )
        elif group == "requests":
            entity_actions.add_request(self, org_id)

    def action_edit_row(self) -> None:
        from ...forms import entities as ef
        from ..widgets import entity_actions

        row = self._selected_row()
        if row is None:
            return
        kind, entity_id = row
        conn = self.app.conn
        if kind == "placement":
            entity_actions.edit_placement(self, placements.get(conn, entity_id))
        elif kind == "contact":
            existing = contacts.get(conn, entity_id)
            entity_actions.push_form(
                self, ef.contact_form(existing),
                lambda v: ef.apply_contact(conn, existing.org_id, v, existing),
            )
        elif kind == "opportunity":
            opp = opportunities.get(conn, entity_id)
            entity_actions.push_form(
                self, ef.opportunity_form(opp, conn=conn),
                lambda v: ef.apply_opportunity(conn, v, opp.org_id, opp),
            )
        elif kind == "task":
            task = tasks_repo.get(conn, entity_id)
            entity_actions.push_form(
                self, ef.task_form(task, conn=conn),
                lambda v: ef.apply_task(conn, v, existing=task),
            )
        elif kind == "project":
            project = projects_repo.get_project(conn, entity_id)
            entity_actions.push_form(
                self, ef.project_form(project),
                lambda v: ef.apply_project(conn, v, project.org_id, project),
            )
        elif kind == "rfi":
            try:
                request = rfi_repo.get_request(conn, entity_id)
            except KeyError:
                self.notify("request no longer exists", severity="error")
                return
            entity_actions.edit_request(self, request)
            return

    def action_task_done(self) -> None:
        row = self._selected_row()
        if row is None or row[0] != "task":
            return
        with _batched(self, tool="task_done", summary="completed a task"):
            tasks_repo.complete(self.app.conn, row[1])
        self.notify("task done — u to undo")
        self.refresh_data()

    def action_drop_row(self) -> None:
        """D — this one is not happening. One field write, so u restores it."""
        row = self._selected_row()
        if row is None or row[0] != "task":
            return
        try:
            task = tasks_repo.get(self.app.conn, row[1])
        except KeyError:  # stale row key after a rebuild
            return
        with _batched(self, tool="task_drop", summary="dropped a task"):
            tasks_repo.drop(self.app.conn, row[1])
        self.notify(f"dropped {task.title!r} — u to undo")
        self.refresh_data()

    def action_renew_row(self) -> None:
        from ..widgets import entity_actions

        row = self._selected_row()
        if row is None or row[0] != "placement":
            return
        entity_actions.renew_placement(
            self, placements.get(self.app.conn, row[1])
        )

    def action_edit_layer_row(self) -> None:
        from ..widgets import entity_actions

        row = self._selected_row()
        if row is None or row[0] != "placement":
            return
        entity_actions.edit_layer(self, placements.get(self.app.conn, row[1]))

    def action_export_row(self) -> None:
        """x — the open-items workbook for the client under the cursor."""
        from ..widgets import entity_actions

        org_id = self._export_target_org()
        if org_id is None:
            self.notify("select a client first", severity="warning")
            return
        entity_actions.export_open_items_flow(self, org_id)

    def action_onboard(self) -> None:
        """o — resume onboarding for the selected client, or start a new one."""
        from ...forms import entities as ef
        from ..screens.onboarding import OnboardingScreen
        from ..widgets.forms import FormModal

        conn = self.app.conn
        kind, payload = self._current
        if kind == "account":
            self.app.push_screen(OnboardingScreen(payload))
            return
        created: list = []

        def done(values: dict | None) -> None:
            if values is not None and created:
                self.app.push_screen(OnboardingScreen(created[-1].id))

        def commit(values: dict) -> str | None:
            # spec's duplicate guard: refuse a near-duplicate name in place —
            # the form stays open so Grant can rename, or esc and resume the
            # existing client from the onboarding attention list instead
            from rapidfuzz import fuzz, process

            existing = {o.name: o for o in orgs.list_orgs(conn, kind="client")}
            match = process.extractOne(
                values["name"], list(existing), scorer=fuzz.WRatio, score_cutoff=87)
            if match:
                dup = existing[match[0]]
                return f"looks like {dup.name} ({dup.ref}) — rename, or esc and resume it"
            created.append(ef.apply_org(conn, values))
            return None

        self.app.push_screen(FormModal(ef.org_form(conn=conn), commit=commit), done)

    # -- navigation -----------------------------------------------------------------

    def action_open_today(self) -> None:
        from .today import TodayScreen

        self.app.push_screen(TodayScreen())

    def action_open_book(self) -> None:
        from .book import BookScreen

        self.app.push_screen(BookScreen())

    def action_open_calendar(self) -> None:
        from .calendar import CalendarScreen

        self.app.push_screen(CalendarScreen())

    def action_open_pipeline(self) -> None:
        from .pipeline import PipelineScreen

        self.app.push_screen(PipelineScreen())

    def action_open_markets(self) -> None:
        from .markets import MarketsScreen

        self.app.push_screen(MarketsScreen())

    def action_open_team(self) -> None:
        from .team import TeamScreen

        self.app.push_screen(TeamScreen())

    def action_settings(self) -> None:
        from ..widgets.settings import SettingsModal

        self.app.push_screen(SettingsModal())

    def action_undo(self) -> None:
        self.app.show_undo_result()
        self.refresh_data()

    def action_refresh(self) -> None:
        """R is dual-role, the same call as x on the account screen: revert on
        a focused MCP CHANGES table, plain refresh everywhere else."""
        if self._current == ("batches", None):
            table = self.query_one("#nav-table", ListTable)
            if table.has_focus and table.cursor_row is not None and table.row_count:
                self._revert_highlighted_batch(table)
                return
        self.refresh_data()

    def _revert_highlighted_batch(self, table: ListTable) -> None:
        from ...services import batches as batches_svc

        key = table.coordinate_to_cell_key(
            Coordinate(table.cursor_row, 0)
        ).row_key.value
        if not key:
            return
        try:
            batch = batches_repo.get(self.app.conn, str(key).removeprefix("batch:"))
        except KeyError:  # row keys can go stale mid-rebuild
            return
        if batch.reverted_at is not None:
            self.notify("already reverted")
            return
        plan = batches_svc.plan_revert(self.app.conn, batch)
        self.app.push_screen(
            ConfirmRevertBatch(batch, plan),
            lambda choice, ref=batch.ref: self._apply_batch_revert(ref, choice),
        )

    def _apply_batch_revert(self, ref: str, choice: str | None) -> None:
        from ...db import utc_now
        from ...services import batches as batches_svc

        if choice is None:
            return
        try:
            result = batches_svc.revert(
                self.app.conn, ref, now=utc_now(), force=(choice == "force")
            )
        except batches_svc.AlreadyReverted:
            # the MCP server got there while the modal was open — fine, the
            # outcome the user wanted already happened
            self.notify("already reverted")
            self.refresh_data()
            return
        except KeyError:
            self.notify("that change no longer exists")
            self.refresh_data()
            return
        except ValueError as exc:
            # a program_* batch wrote a towerkit FILE; services/batches refuses
            # with a message naming the right tool. It used to escape through
            # this dismiss callback — which run_action never covered — and take
            # the whole session down with a traceback.
            self.notify(str(exc), severity="error", timeout=12)
            self.refresh_data()
            return
        if result.applied:
            self.notify(f"{ref} reverted — {len(result.reverted)} change(s)")
        else:
            self.notify(f"{ref} refused — {len(result.refused)} conflict(s)")
        self.refresh_data()
