"""What the launcher checks before it serves — and what it tells you to run.

Grant, 2026-08-21: "I appreciate having the ability to use these CLI commands,
but the launcher should really do much of the maintenance for me when launching
to web."

Two outages that afternoon argued for it, and both are the same shape: a
one-command fix presenting as something else entirely.

* bookkit named `Layer.policy_group`; the towerkit installed beside it did not
  have it. An `AttributeError` inside a route, a 500, and a chevron that looked
  simply dead. Diagnosing it took a traceback.
* `render.theme` resolved against the working directory, so a moved folder made
  `themes/marsh.json` unreadable — and because every program write re-validates
  the file, that WEDGED the program: no write to it would succeed, including
  the write that would have changed the theme.

Neither is a thing a user should have to work out from a stack trace.

WHAT THIS MODULE WILL NOT DO IS FIX ANYTHING. It reports, and every report
names the command. That is not timidity: the repairs are `git pull` (a network
write against a repo that may hold local work) and `./install.sh` (which
DELETES AND REBUILDS .venv). Running either under a launching app, unasked, is
exactly the class of action CLAUDE.md says to confirm first — and a checker
that repaired silently would hide the fact that a repair was needed, which is
how the NEXT skew goes unnoticed.

THE FIX IS A GIT PULL, NOT A REINSTALL — and getting that right took reading
install.sh rather than assuming. Both packages install EDITABLE
(`pip install -e ../towerkit`, `pip install -e .`), so a checkout IS the
running code: pulling towerkit takes effect on the next launch with no
reinstall at all. `./install.sh` is for when DEPENDENCIES change, and it is
what to say then — never `uv sync`, which fails on the corporate machine, and
which is why install.sh and the wheelhouse exist (Grant, 2026-08-21).

That is also why this module checks CAPABILITIES and not timestamps. A first
cut compared `.venv`'s mtime against the newest commit, which is a sound signal
for a non-editable install and pure noise for this one — it fired on a
perfectly current dev checkout, and a doctor that cries wolf is a doctor
nobody reads.

SEVERITY DECIDES WHETHER THE APP STARTS. A `blocked` finding is one where
serving anyway produces a 500 in a route twenty minutes later; those refuse.
Everything else prints above the URL and gets out of the way.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass

BLOCKED = "blocked"
WARNING = "warning"

# Editable installs, so the pull IS the fix. `./install.sh` is named only as
# the follow-up for the case a pull also moved dependencies.
_UPDATE_TOWERKIT = (
    "cd ../towerkit && git pull        "
    "# then ./install.sh only if its dependencies changed"
)


@dataclass(frozen=True)
class Finding:
    """One thing that is wrong, and the command that fixes it.

    `fix` is not optional and not decorative: a report that says "towerkit is
    out of date" and stops has MOVED the diagnosis, not removed it.
    """

    severity: str
    title: str
    detail: str
    fix: str

    def render(self) -> str:
        mark = "✗" if self.severity == BLOCKED else "⚠"
        return f"{mark} {self.title}\n    {self.detail}\n    run:  {self.fix}"


# --- the checks ------------------------------------------------------------------


def towerkit_capabilities() -> Finding | None:
    """Does the INSTALLED towerkit publish every field bookkit names?

    `web/parity.py` already introspects towerkit at runtime and the suite goes
    red when towerkit grows a field the ledger has not covered. This is the
    mirror: bookkit's ledger is the list of fields bookkit KNOWS ABOUT, and a
    name in it that towerkit does not have is a route waiting to raise.

    Reading the ledger rather than a second hand-written list is the whole
    point — a field added to one of them must not need remembering in the
    other, which is the DRY rule that made this bug possible in the first
    place.
    """
    try:
        from pydantic import BaseModel
        from towerkit import model

        from .web.parity import TOWERKIT_MODEL_FIELDS
    except ImportError as exc:  # towerkit missing entirely is its own message
        return Finding(
            BLOCKED, "towerkit is not importable", str(exc), _UPDATE_TOWERKIT
        )

    published = {
        f"{name}.{field}"
        for name, obj in vars(model).items()
        if inspect.isclass(obj)
        and issubclass(obj, BaseModel)
        and obj.__module__ == "towerkit.model"
        and not name.startswith("_")
        for field in obj.model_fields
    }
    # ONE DIRECTION ONLY. A field towerkit has that bookkit's ledger does not
    # is a gap in coverage — the suite's job, and harmless at runtime. The
    # reverse is fatal: bookkit reads a field that is not there.
    missing = sorted(set(TOWERKIT_MODEL_FIELDS) - published)
    if not missing:
        return None
    return Finding(
        BLOCKED,
        "bookkit needs towerkit features this install does not have",
        ", ".join(missing),
        _UPDATE_TOWERKIT,
    )


# ONE CHECK, deliberately. It is the one that catches the failure that cost an
# afternoon, and it cannot produce a false positive: either the installed
# towerkit publishes the field bookkit reads, or a route raises on it.
_CHECKS = (towerkit_capabilities,)


def findings() -> list[Finding]:
    """Every check, in severity order — blockers first, because that is the
    one a reader must not scroll past."""
    found = [finding for check in _CHECKS if (finding := check()) is not None]
    return sorted(found, key=lambda f: 0 if f.severity == BLOCKED else 1)


def report(found: list[Finding]) -> str:
    return "\n".join(finding.render() for finding in found)


def blocked(found: list[Finding]) -> bool:
    return any(finding.severity == BLOCKED for finding in found)
