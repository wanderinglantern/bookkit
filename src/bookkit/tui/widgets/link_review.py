"""Review queue for sync results the user must decide (§5.2 and friends):
unlinked files, ambiguous placement adoptions, and offered opportunities from
proposed programs' TBD lines. One modal, one decision at a time."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..app import BookkitApp

from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from ... import sync
from ...money import format_cents_compact

NEW_PLACEMENT = "__new__"
NEW_MARKET = "__new_market__"
YES = "__yes__"
NO = "__no__"

ReviewItem = (
    sync.LinkSuggestion
    | sync.PlacementSuggestion
    | sync.OpportunityCandidate
    | sync.CarrierSuggestion
)


class LinkReview(ModalScreen):
    app: BookkitApp
    BINDINGS = [Binding("escape", "skip", "Skip this item")]

    def __init__(self, report: sync.SyncReport) -> None:
        super().__init__()
        self.queue: list[ReviewItem] = [
            *report.needs_link,
            *report.needs_placement,
            *report.opportunity_candidates,
            *report.unresolved_carriers,
        ]

    def compose(self) -> ComposeResult:
        with VerticalScroll(classes="modal-box"):
            yield Static("SYNC REVIEW", classes="modal-title")
            yield Static(id="lr-file")
            yield OptionList(id="lr-candidates")
            yield Static("enter decides · esc skips", classes="hint")

    def on_mount(self) -> None:
        self._show_next()

    def _show_next(self) -> None:
        if not self.queue:
            self.dismiss(None)
            return
        item = self.queue[0]
        prompt = self.query_one("#lr-file", Static)
        options = self.query_one("#lr-candidates", OptionList)
        options.clear_options()
        if isinstance(item, sync.LinkSuggestion):
            suggestion = item
            prompt.update(
                f"[b]LINK FILE[/b]\n{suggestion.path}\ninsured: [b]{suggestion.insured}[/b]"
            )
            for org, score in suggestion.candidates:
                options.add_option(Option(f"{org.name}  ({score:.0f}% match)", id=org.id))
            if not suggestion.candidates:
                options.add_option(
                    Option("(no candidates — create the account first)", disabled=True)
                )
        elif isinstance(item, sync.PlacementSuggestion):
            prompt.update(
                f"[b]WHICH PLACEMENT?[/b]\n{item.path.name} — several records "
                "could be this program:"
            )
            for p in item.candidates:
                premium = format_cents_compact(p.total_premium) if p.total_premium else "—"
                options.add_option(
                    Option(
                        f"{p.ref}  {p.program_name}  {p.period_from}→{p.period_to}"
                        f"  [{p.status}]  {premium}",
                        id=p.id,
                    )
                )
            options.add_option(Option("(none of these — create a new placement)", id=NEW_PLACEMENT))
        elif isinstance(item, sync.CarrierSuggestion):
            prompt.update(
                f"[b]UNKNOWN CARRIER[/b]\n[b]{item.carrier}[/b] sits on projected "
                "towers but matches no market — alias it or create the market:"
            )
            for org, score in item.candidates:
                options.add_option(
                    Option(f"alias → {org.name}  ({score:.0f}% match)", id=org.id)
                )
            options.add_option(
                Option(f"create market {item.carrier!r}", id=NEW_MARKET)
            )
        else:  # opportunity offer
            candidate = item
            indicated = (
                f" · indicated {format_cents_compact(candidate.premium)}"
                if candidate.premium
                else ""
            )
            prompt.update(
                f"[b]TRACK AS OPPORTUNITY?[/b]\n{candidate.path.name}: line "
                f"[b]{candidate.line_name}[/b] is unplaced "
                f"({', '.join(candidate.layer_names)}){indicated}\n"
                f"target effective {candidate.target_effective}"
            )
            options.add_option(Option("yes — create the opportunity", id=YES))
            options.add_option(Option("no — skip", id=NO))
        options.focus()
        options.highlighted = 0 if options.option_count else None

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if not event.option.id:
            return
        item = self.queue.pop(0)
        if isinstance(item, sync.LinkSuggestion):
            self._resolve_org(item, event.option.id)
        elif isinstance(item, sync.PlacementSuggestion):
            self._resolve_placement(item, event.option.id)
        elif isinstance(item, sync.CarrierSuggestion):
            if event.option.id == NEW_MARKET:
                market = sync.create_market_for_carrier(self.app.conn, item.carrier)
                self.notify(f"created market {market.name} ({market.ref})")
            else:
                sync.alias_carrier(self.app.conn, item.carrier, event.option.id)
                self.notify(f"aliased {item.carrier!r}")
        elif event.option.id == YES:
            opp = sync.create_opportunity(self.app.conn, item)
            self.notify(f"created {opp.ref} {opp.title}")
        self._show_next()

    def _resolve_org(self, suggestion: sync.LinkSuggestion, org_id: str) -> None:
        try:
            diags = sync.confirm_link(self.app.conn, Path(suggestion.path), org_id)
        except sync.AmbiguousPlacement as exc:
            self.queue.insert(
                0,
                sync.PlacementSuggestion(
                    suggestion.path, org_id, suggestion.insured, exc.candidates
                ),
            )
            self.notify(f"linked {suggestion.path.name} — now pick its placement")
            return
        if diags.ok:
            self.notify(f"linked and projected {suggestion.path.name}")
            self._offer_opportunities(Path(suggestion.path))
        else:
            self.notify(f"linked, but projection failed: {diags.errors[0]}", severity="error")

    def _resolve_placement(self, suggestion: sync.PlacementSuggestion, choice: str) -> None:
        target = None if choice == NEW_PLACEMENT else choice
        diags = sync.confirm_placement(self.app.conn, Path(suggestion.path), target)
        if diags.ok:
            verb = "created a new placement for" if target is None else "adopted into"
            self.notify(f"{verb} {suggestion.path.name}")
            self._offer_opportunities(Path(suggestion.path))
        else:
            self.notify(f"projection failed: {diags.errors[0]}", severity="error")

    def _offer_opportunities(self, path: Path) -> None:
        self.queue.extend(sync.opportunities_for_path(self.app.conn, path))

    def action_skip(self) -> None:
        if self.queue:
            self.queue.pop(0)
        self._show_next()
