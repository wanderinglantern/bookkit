"""Pipeline — kanban by stage, cards movable by keyboard, weighted value per
column. h/l switch columns, </> move the selected card between stages."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, OptionList, Static
from textual.widgets.option_list import Option

from ...money import format_cents_compact, weighted_cents
from ...repo import opportunities, orgs
from ...services import pipeline
from .. import theme


class PipelineScreen(Screen):
    app: BookkitApp
    DEFAULT_CSS = """
    PipelineScreen #pipeline-hint {
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    """
    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("h,left", "prev_column", "◀ column", key_display="h"),
        Binding("l,right", "next_column", "column ▶", key_display="l"),
        Binding("greater_than_sign", "advance_card", "Advance card", key_display=">"),
        Binding("less_than_sign", "close_lost", "Mark lost", key_display="<"),
        Binding("e", "edit_card", "Edit"),
        Binding("u", "undo", "Undo"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.column = 0

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="kanban"):
            for stage in pipeline.OPEN_STAGES:
                with Vertical(classes="kanban-column", id=f"col-{stage}"):
                    yield Static(id=f"title-{stage}", classes="column-title")
                    yield OptionList(id=f"list-{stage}")
        yield Static(
            f"[{theme.DIM}][b]h[/b]/[b]l[/b] columns · [b]↑[/b]/[b]↓[/b] cards · "
            f"[b]>[/b] advance · [b]<[/b] mark lost · [b]e[/b] edit · "
            f"[b]enter[/b] opens account · [b]u[/b] undo[/]",
            id="pipeline-hint",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.refresh_data()
        self._focus_column(0)

    def refresh_data(self) -> None:
        conn = self.app.conn
        for stage in pipeline.OPEN_STAGES:
            opps = opportunities.by_stage(conn, stage)
            weighted = sum(weighted_cents(o.target_premium or 0, o.probability_pct) for o in opps)
            stage_color = theme.STATUS_STYLES.get(stage, theme.FG)
            self.query_one(f"#title-{stage}", Static).update(
                f"[b {stage_color}]{stage.upper()}[/] "
                f"[{theme.DIM}]· {len(opps)} · {format_cents_compact(weighted)}[/]"
            )
            option_list = self.query_one(f"#list-{stage}", OptionList)
            option_list.clear_options()
            # target effective is the date the deal marches toward: soonest
            # first, undated at the bottom
            for o in sorted(opps, key=lambda o: o.target_effective or "9999"):
                org = orgs.get(conn, o.org_id)
                target = format_cents_compact(o.target_premium) if o.target_premium else "—"
                # Text.assemble, not markup: account/title text must render
                # verbatim even if it contains [brackets].
                #
                # And NO baked colours (review F28, reported from use):
                # OptionList paints its highlight BEHIND the prompt and does
                # not override an explicit foreground the way DataTable does,
                # so theme.DIM here rendered at 1.83:1 on the gold cursor —
                # the ref, the probability and the whole lines/effective row
                # vanished from whichever card was selected. Hierarchy comes
                # from weight and indentation, which the cursor cannot erase.
                label = Text.assemble(
                    o.ref, "  ", (org.name, "bold"), "\n",
                    "  ", o.title, " · ", target,
                    f" · {o.probability_pct}%", "\n",
                    f"  {o.lines or 'lines —'} · eff {o.target_effective or '—'}",
                )
                option_list.add_option(Option(label, id=o.id))

    def _focus_column(self, index: int) -> None:
        self.column = max(0, min(index, len(pipeline.OPEN_STAGES) - 1))
        for i, stage in enumerate(pipeline.OPEN_STAGES):
            self.query_one(f"#col-{stage}", Vertical).set_class(
                i == self.column, "focused-column"
            )
        target = self.query_one(f"#list-{pipeline.OPEN_STAGES[self.column]}", OptionList)
        target.focus()
        if target.option_count and target.highlighted is None:
            target.highlighted = 0

    def action_prev_column(self) -> None:
        self._focus_column(self.column - 1)

    def action_next_column(self) -> None:
        self._focus_column(self.column + 1)

    def _selected_opp_id(self) -> str | None:
        option_list = self.query_one(
            f"#list-{pipeline.OPEN_STAGES[self.column]}", OptionList
        )
        if option_list.highlighted is None:
            return None
        return option_list.get_option_at_index(option_list.highlighted).id

    def action_advance_card(self) -> None:
        opp_id = self._selected_opp_id()
        if opp_id is None:
            return
        opp = opportunities.get(self.app.conn, opp_id)
        try:
            nxt = pipeline.allowed_next(opp.stage)[0]
            moved = pipeline.move_stage(self.app.conn, opp_id, nxt)
        except (pipeline.StageError, IndexError):
            self.notify("cannot advance from here", severity="warning")
            return
        self.notify(f"{moved.ref} → {moved.stage}")
        self.refresh_data()
        self._focus_column(self.column)

    def action_close_lost(self) -> None:
        opp_id = self._selected_opp_id()
        if opp_id is None:
            return
        try:
            moved = pipeline.move_stage(self.app.conn, opp_id, "lost")
        except pipeline.StageError:
            self.notify("cannot close from here", severity="warning")
            return
        self.notify(f"{moved.ref} marked lost — u to undo")
        self.refresh_data()
        self._focus_column(self.column)

    def action_edit_card(self) -> None:
        """Edit the selected opportunity in place — lines and target
        effective included; the form stays open on a refused save."""
        from ..widgets.entity_forms import apply_opportunity, opportunity_form
        from ..widgets.forms import FormModal

        opp_id = self._selected_opp_id()
        if opp_id is None:
            return
        opp = opportunities.get(self.app.conn, opp_id)

        def commit(values: dict) -> str | None:
            apply_opportunity(self.app.conn, values, opp.org_id, existing=opp)
            return None

        def done(values: dict | None) -> None:
            if values is not None:
                self.notify(f"updated {opp.ref}")
                self.refresh_data()
                self._focus_column(self.column)

        self.app.push_screen(
            FormModal(opportunity_form(opp, conn=self.app.conn), commit=commit), done
        )

    def action_undo(self) -> None:
        self.app.show_undo_result()
        self.refresh_data()
        self._focus_column(self.column)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            opp = opportunities.get(self.app.conn, event.option.id)
            self.app.open_account(opp.org_id)
