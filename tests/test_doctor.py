"""The launcher checks before it serves, and names the command.

Grant, 2026-08-21: "the launcher should really do much of the maintenance for
me when launching to web." Two outages that afternoon argued for it, and the
one this catches cost the longest: bookkit read `Layer.policy_group`, the
towerkit installed beside it did not have it, and the result was an
AttributeError inside a route, a 500, and a chevron that looked simply dead.

What is asserted here is the pair of properties that make a doctor worth
reading: it CANNOT cry wolf on a healthy install, and when it does speak it
names a command that works on the machine it is spoken to.
"""

from __future__ import annotations

from bookkit import doctor


class TestAHealthyInstallIsSilent:
    def test_this_checkout_reports_nothing(self) -> None:
        """The property a first cut got wrong. It compared .venv's mtime
        against the newest commit — sound for a NON-editable install, pure
        noise for this one, since install.sh installs both packages with
        `pip install -e` and the checkout IS the running code. It fired on a
        perfectly current dev checkout, and a doctor that cries wolf is a
        doctor nobody reads."""
        assert doctor.findings() == []

    def test_nothing_blocks(self) -> None:
        assert not doctor.blocked(doctor.findings())


class TestTheSkewItExistsFor:
    def test_a_field_bookkit_names_and_towerkit_lacks_blocks_the_launch(
        self, monkeypatch
    ) -> None:
        """Exactly Grant's failure, simulated: bookkit's ledger names a field
        the installed towerkit does not publish."""
        from bookkit.web import parity

        monkeypatch.setitem(
            parity.TOWERKIT_MODEL_FIELDS, "Layer.from_the_future", "a later towerkit"
        )

        found = doctor.findings()

        assert found, "the skew was not detected"
        assert doctor.blocked(found)
        assert "Layer.from_the_future" in found[0].detail

    def test_it_names_a_command_that_works_where_it_is_read(
        self, monkeypatch
    ) -> None:
        """`./install.sh`, never `uv sync` — uv fails on the corporate machine,
        which is why install.sh and the wheelhouse exist. And the PULL is the
        actual fix, because both packages are editable installs; a reinstall is
        only needed when dependencies moved."""
        from bookkit.web import parity

        monkeypatch.setitem(parity.TOWERKIT_MODEL_FIELDS, "Layer.nope", "later")
        fix = doctor.findings()[0].fix

        assert "git pull" in fix
        assert "uv sync" not in fix
        assert "../towerkit" in fix

    def test_every_finding_carries_a_fix(self, monkeypatch) -> None:
        """A report that says "towerkit is out of date" and stops has MOVED the
        diagnosis, not removed it."""
        from bookkit.web import parity

        monkeypatch.setitem(parity.TOWERKIT_MODEL_FIELDS, "Layer.nope", "later")

        for finding in doctor.findings():
            assert finding.fix.strip(), finding.title
            assert finding.detail.strip(), finding.title
            assert "run:" in finding.render()


class TestTheDirectionOfTheCheck:
    def test_a_field_towerkit_has_and_bookkit_does_not_is_NOT_a_launch_problem(
        self, monkeypatch
    ) -> None:
        """One direction only. A field towerkit grew that bookkit's ledger has
        not covered is a gap in COVERAGE — the suite's job, and harmless at
        runtime. The reverse is fatal: bookkit reads a field that is not there.
        Blocking a launch on the harmless direction would stop the app every
        time towerkit shipped anything."""
        from towerkit import model

        class Extra(model.Layer):  # type: ignore[misc, name-defined]
            pass

        monkeypatch.setattr(model, "SomethingNew", Extra, raising=False)

        assert doctor.findings() == []


class TestTheLauncherHonoursIt:
    """`serve` imports portguard and uvicorn INSIDE the function, so these
    patch the real modules rather than attributes on serve."""

    def test_serve_refuses_before_taking_the_port(self, monkeypatch, tmp_path):
        """BEFORE, and that ordering is the point: serving into a broken
        install turns a one-command fix into a traceback twenty minutes later,
        and a port taken on the way is a second thing to clean up."""
        from bookkit.web import portguard
        from bookkit.web import serve as serve_module

        monkeypatch.setattr(
            doctor, "findings",
            lambda: [doctor.Finding(doctor.BLOCKED, "t", "d", "run this")],
        )

        def explode(*_a, **_k):  # pragma: no cover - must not be reached
            raise AssertionError("the port was taken despite a blocked finding")

        monkeypatch.setattr(portguard, "reclaim", explode)

        assert serve_module.serve(tmp_path / "x.db", 8999, open_browser=False) == 1

    def test_a_warning_does_not_stop_the_launch(self, monkeypatch, tmp_path):
        import uvicorn

        from bookkit.web import portguard
        from bookkit.web import serve as serve_module

        monkeypatch.setattr(
            doctor, "findings",
            lambda: [doctor.Finding(doctor.WARNING, "t", "d", "run this")],
        )
        monkeypatch.setattr(portguard, "reclaim", lambda *_a, **_k: None)
        served: list[bool] = []
        monkeypatch.setattr(uvicorn, "run", lambda *_a, **_k: served.append(True))

        assert serve_module.serve(tmp_path / "x.db", 8999, open_browser=False) == 0
        assert served, "a warning stopped the launch"
