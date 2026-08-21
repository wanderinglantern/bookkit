"""The theme picker offers what a program file may legally hold.

Grant, 2026-08-21, after towerkit's theme fix shipped: "we worked on fix for
showing the themes and i applied per above but they are not showing at all now".

He was right and it was half a fix. `_theme_choices` filtered out every
ABSOLUTE path, which meant filtering out every PACKAGED theme — correct while a
program file could only hold a literal relative path, because offering a choice
that then fails validation is worse than not offering it. The cost was
invisible until his folders moved: with no ./themes beside the running process
the packaged set is ALL there is, so the picker came up empty.

towerkit now resolves a stored theme by NAME when the literal path misses, so
the portable spelling `themes/<stem>.json` is a real answer for every theme the
machine can see. That is what the picker offers.

The rule this file really guards is the older one, from the picker's first
afternoon: OFFER ONLY WHAT IS STORABLE. Every option must survive validation,
because every later write to a program file re-validates it — one bad theme
wedges the whole program.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app
from bookkit.web.routes.program import _resolve_theme, _theme_choices


@pytest.fixture
def elsewhere(tmp_path, monkeypatch):
    """A working directory with no ./themes beside it — the state a moved
    folder leaves every process in, and the state Grant reported from."""
    monkeypatch.chdir(tmp_path)
    assert not Path("themes").exists()
    return tmp_path


class TestTheReportedBug:
    def test_the_picker_is_not_empty_with_no_local_themes_directory(
        self, elsewhere
    ) -> None:
        """THE REGRESSION. marsh and default ship packaged; before this they
        were filtered out for being absolute and nothing was left."""
        options = _theme_choices()

        assert options, "the picker came up empty"
        assert "marsh" in {label for label, _value in options}

    def test_every_option_is_stored_as_a_portable_relative_path(
        self, elsewhere
    ) -> None:
        """Program files are portable BY CONTRACT and towerkit makes an
        absolute render.theme an outright error. An option carrying one would
        wedge the file it was chosen on."""
        for _label, value in _theme_choices():
            assert not Path(value).is_absolute(), value
            assert value.startswith("themes/"), value

    def test_every_option_actually_resolves(self, elsewhere) -> None:
        """OFFER ONLY WHAT IS STORABLE, checked rather than asserted in prose.
        A picker listing a theme the renderer cannot find is the exact failure
        this picker had on its first afternoon."""
        for _label, value in _theme_choices():
            assert _resolve_theme(value) is not None

    def test_a_name_nothing_answers_to_still_refuses_and_names_the_control(
        self, elsewhere
    ) -> None:
        with pytest.raises(FileNotFoundError) as missing:
            _resolve_theme("themes/nosuchtheme.json")

        assert "nosuchtheme" in str(missing.value)
        assert "chart strip" in str(missing.value), (
            "the refusal does not say where to fix it"
        )


class TestWhatMustNotChange:
    def test_a_local_theme_still_appears_and_still_wins(
        self, tmp_path, monkeypatch
    ) -> None:
        """./themes is the user's own directory and beats the packaged set on a
        name clash — towerkit.theme.available_themes' rule, which this must not
        undo by listing both."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "themes").mkdir()
        # Only the picker's LISTING is under test here, and that reads
        # filenames — so the file's contents need not be a loadable theme.
        (tmp_path / "themes" / "housestyle.json").write_text('{"name": "House Style"}')

        labels = [label for label, _ in _theme_choices()]

        assert "housestyle" in labels
        assert len(labels) == len(set(labels)), "the same stem offered twice"

    def test_clearing_the_theme_is_still_a_real_answer(self, elsewhere) -> None:
        """A cleared render.theme means towerkit's built-in default. The blank
        option carries it and `_resolve_theme` answers None, not a refusal."""
        assert _resolve_theme("") is None
        assert _resolve_theme(None) is None


class TestOnThePage:
    def test_the_chart_strip_renders_the_options(self, snapshot_db: Path) -> None:
        from bookkit.repo import orgs, placements

        app = create_app(snapshot_db)
        conn = app.state.conn
        org = next(
            o for o in orgs.list_orgs(conn, kind="client")
            if [p for p in placements.for_org(conn, o.id) if p.program_path]
        )
        placement = next(
            p for p in placements.for_org(conn, org.id) if p.program_path
        )
        with TestClient(app, base_url="http://127.0.0.1") as client:
            editor = client.get(
                f"/accounts/{org.ref}/program/{placement.id}"
                f"/field/program/_:_/render.theme/edit"
            ).text

        assert "<select" in editor
        assert 'value="themes/marsh.json"' in editor
        assert '<option value=""' in editor, "no blank option"
