"""Command-palette providers: jump to any record, or to any screen.

The palette was already bound and already advertised in the Footer, and it
offered theme-change and quit. For a book of business the highest-leverage
thing it can do is put you in front of a client from anywhere — including from
inside a form, where `/` cannot reach (review F10).

Two providers, because they answer two different questions:
  RecordCommands  — "where is Cascade Health?"   (search: needs a query)
  ScreenCommands  — "what can I do from here?"   (discover: shown on open)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.command import DiscoveryHit, Hit, Hits, Provider

if TYPE_CHECKING:
    from .app import BookkitApp

# how a hit's kind reads in the palette — the same vocabulary the app uses
_KIND_LABEL = {
    "org": "account",
    "contact": "contact",
    "interaction": "interaction",
}


_PALETTE_LIMIT = 30


def _score(position: int) -> float:
    """Where a hit sits in the palette, from where the repo already put it.

    Two scales, pointing opposite ways. `SearchHit.rank` is bm25: NEGATIVE,
    and lower sorts first. `Hit.score` is a Textual match strength: 0..1, and
    the palette sorts it DESCENDING. Handing one to the other reversed the
    list — the best FTS hit, being the most negative, scored lowest of all —
    and the `or 0.5` fallback guarding the unranked email hits put them at
    the very top, above every name match.

    repo.search has already ranked and grouped these. The palette's job is to
    keep that order, so the position IS the score, and any future change to
    how the repo ranks is inherited rather than re-derived here.
    """
    return 1.0 - position / (_PALETTE_LIMIT + 1)


class RecordCommands(Provider):
    """Every account, contact and interaction, by name, from anywhere."""

    @property
    def _book(self) -> BookkitApp:
        from .app import BookkitApp

        assert isinstance(self.app, BookkitApp)
        return self.app

    async def search(self, query: str) -> Hits:
        from ..repo import search as search_repo

        if len(query.strip()) < 2:  # the FTS prefix query needs something to go on
            return
        app = self._book
        seen: set[str] = set()
        hits = search_repo.search(app.conn, query, limit=_PALETTE_LIMIT)
        for position, hit in enumerate(hits):
            # one row per ACCOUNT: three contacts at the same client are one
            # destination, and three near-identical rows is not a shortlist
            if hit.org_id in seen:
                continue
            seen.add(hit.org_id)
            kind = _KIND_LABEL.get(hit.kind, hit.kind)
            label = f"{hit.title}  ({kind})"
            yield Hit(
                _score(position),
                label,
                self._opener(hit.org_id),
                text=label,
                help=hit.snippet or None,
            )

    def _opener(self, org_id: str):
        app = self._book

        def open_it() -> None:
            app.open_account(org_id)

        return open_it


class ScreenCommands(Provider):
    """The screens and the standing actions, so the palette also answers
    "what can I do from here" — the discovery half of F10."""

    @property
    def _book(self) -> BookkitApp:
        from .app import BookkitApp

        assert isinstance(self.app, BookkitApp)
        return self.app

    def _commands(self) -> list[tuple[str, str, str]]:
        """(label, help, action) — action is an App/screen action name."""
        return [
            ("Today", "Tasks, renewals, stale accounts and late submissions", "open_today"),
            ("Book", "Every client, filterable", "open_book"),
            ("Calendar", "The renewal year at a glance", "open_calendar"),
            ("Pipeline", "Opportunities by stage", "open_pipeline"),
            ("Markets", "Carriers, appetite and hit rate", "open_markets"),
            ("Team", "Colleagues and who to go to for what", "open_team"),
            ("Home", "Back to the navigator", "go_home"),
            ("Help", "Every key, in one place", "help"),
            ("Undo last change", "The same as u", "undo_last"),
            ("Setup — program file locations", "Where the towerkit files live", "setup"),
        ]

    async def discover(self) -> Hits:
        for label, help_text, action in self._commands():
            yield DiscoveryHit(label, self._runner(action), text=label, help=help_text)

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        for label, help_text, action in self._commands():
            score = matcher.match(label)
            if score > 0:
                yield Hit(
                    score,
                    matcher.highlight(label),
                    self._runner(action),
                    text=label,
                    help=help_text,
                )

    def _runner(self, action: str):
        app = self._book

        def run() -> None:
            app.run_palette_action(action)

        return run
