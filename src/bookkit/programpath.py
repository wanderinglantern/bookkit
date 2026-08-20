"""Where a placement's towerkit file is, and what to store so it stays found.

THE PATH IS NOT THE LINK. `placement.program_path` used to hold an absolute
path, written once by whichever sweep first projected the file. On 2026-08-20
Grant moved his towerkit checkout out of OneDrive — an ordinary thing to do,
and one macOS had already done to him once by relocating `OneDrive - MMC` to
`Library/CloudStorage/OneDrive-MMC` — and all five of his linked programs
became `FileNotFoundError`. Five silent `except Exception: return []` blocks
then rendered that as "the linked file has no layers yet", so the web claimed
the programs were empty while the same files rendered correctly in the TUI's
preview, which reads them by the path he had just typed.

Two changes come out of that, and this module owns both:

  STORE RELATIVE TO A ROOT.  `store()` writes `aalo-2025.json` rather than
  `/Users/.../Scripts/towerkit/programs/aalo-2025.json` when the file sits
  under a configured root. Moving the whole tree is then one `bookctl roots`
  call and every placement follows, instead of a per-row repair.

  RESOLVE, THEN RECOVER.  `resolve()` looks where the stored value says, and
  when that is gone, looks for the same file under the roots. A path that
  moved wholesale — which is what a re-homed checkout looks like — is found
  by matching the longest tail of the stored path that exists under exactly
  one root. `programs/2026/aalo.json` beats `aalo.json`, so a deeper layout
  disambiguates itself before the basename rule is ever reached.

AMBIGUITY IS REFUSED, NEVER GUESSED. Two roots holding `aalo-2025.json` is
two different programs as far as this module can tell, and picking one would
attach a client's tower to the wrong account — the same rule sync's link
review already follows ("suggestions only; a wrong guess attached to the wrong
account is worse than asking"). A refusal here surfaces as a message the
panel prints, not as an empty table.

Recovery is READ-ONLY. `resolve()` never writes the repaired value back:
a read path that quietly rewrites rows turns every render into a migration,
and a wrong recovery would then be permanent. `bookctl relink` is the writer
(services/relink.py), and it shows what it found before it commits.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    import sqlite3


@dataclass(frozen=True)
class Resolved:
    """Where a stored `program_path` points right now.

    `path` is None when nothing on disk answers to it. `error` then says why
    in a sentence a broker can act on — which file is missing, or which roots
    both claim it — because that sentence is what the surfaces print instead
    of the empty state that used to lie about it.

    `moved_from` is set when the file was found somewhere other than where the
    row says. The read succeeds, and the surfaces mention it: a program
    silently answering from a different path than the database records is how
    two placements end up sharing one file without anybody noticing.
    """

    path: Path | None
    error: str | None = None
    moved_from: str | None = None

    @property
    def ok(self) -> bool:
        return self.path is not None


def roots(conn: sqlite3.Connection) -> list[Path]:
    """Where program files live: the saved setting, else the
    BOOKKIT_PROGRAM_ROOTS env var (colon-separated) for scripting.

    ONE definition, because two would diverge exactly where it hurts: this
    module decides how a path is STORED and RESOLVED, and sync.configured_roots
    decides what a sweep SCANS. If those disagreed about the env fallback, a
    sweep would find and link a file under a root that storage then refused to
    make relative — and the disagreement would only show up on the machine
    that used the env var. sync.configured_roots delegates here."""
    import os

    from .repo import settings as settings_repo

    saved = settings_repo.get_program_roots(conn)
    if saved:
        return [Path(r).expanduser() for r in saved]
    raw = os.environ.get("BOOKKIT_PROGRAM_ROOTS", "")
    return [Path(r).expanduser() for r in raw.split(":") if r]


def store(conn: sqlite3.Connection, path: Path) -> str:
    """The value to WRITE into `placement.program_path`.

    Relative to the configured root that contains the file, so that moving the
    root moves every placement with it. Absolute when no root contains it —
    a file kept outside the roots is still a legitimate link, and inventing a
    relative path against a root it does not live under would point at nothing.

    The DEEPEST containing root wins. Roots may nest (`~/towerkit` and
    `~/towerkit/programs` are both plausible entries), and relative-to-the-
    shallower one would survive a move of the deeper one only by accident.
    """
    resolved = path.expanduser().resolve()
    best: str | None = None
    best_depth = -1
    for root in roots(conn):
        try:
            rel = resolved.relative_to(root.resolve())
        except (ValueError, OSError):
            continue
        depth = len(root.resolve().parts)
        if depth > best_depth:
            best, best_depth = str(rel), depth
    return best if best is not None else str(resolved)


def resolve(conn: sqlite3.Connection, stored: str | None) -> Resolved:
    """Where `stored` actually is, with one recovery attempt when it is gone."""
    if not stored:
        return Resolved(None, error=None)

    as_given = Path(stored).expanduser()
    if as_given.is_absolute():
        if as_given.exists():
            return Resolved(as_given)
        return _recover(conn, stored, as_given)

    configured = roots(conn)
    for root in configured:
        candidate = root / as_given
        if candidate.exists():
            return Resolved(candidate)
    if not configured:
        return Resolved(
            None,
            error=(
                f"{stored} is stored relative to a program root and no roots "
                f"are configured — run `bookctl roots <dir>` to say where the "
                f"towerkit files live"
            ),
        )
    return _recover(conn, stored, configured[0] / as_given)


def _recover(conn: sqlite3.Connection, stored: str, expected: Path) -> Resolved:
    """The stored location is empty: look for the same file under the roots.

    Longest tail first, so a program in `programs/2026/aalo.json` is matched
    on those three components before the bare filename is ever considered —
    depth is the only evidence available that two same-named files are or are
    not the same program.
    """
    parts = Path(stored).parts
    configured = roots(conn)
    for length in range(min(len(parts), 4), 0, -1):
        tail = Path(*parts[-length:])
        hits = sorted({(root / tail).resolve() for root in configured if (root / tail).exists()})
        if len(hits) == 1:
            return Resolved(hits[0], moved_from=stored)
        if len(hits) > 1:
            where = ", ".join(str(h) for h in hits)
            return Resolved(
                None,
                error=(
                    f"{tail} is under more than one program root ({where}) — "
                    f"bookkit will not guess which one this placement means; "
                    f"run `bookctl relink` to choose"
                ),
            )
    if not configured:
        return Resolved(
            None,
            error=(
                f"no file at {expected}, and no program roots are configured to "
                f"look under — run `bookctl roots <dir>` then `bookctl relink`"
            ),
        )
    return Resolved(
        None,
        error=(
            f"no file at {expected}, and nothing matching it under "
            f"{', '.join(str(r) for r in configured)} — the file was moved or "
            f"deleted; `bookctl relink` reports every placement in this state"
        ),
    )


def stored_forms(conn: sqlite3.Connection, path: Path | str) -> list[str]:
    """Every spelling a row might legitimately hold for this file.

    A book written before paths were stored relative holds absolute strings;
    one written after holds root-relative ones; a book part-way through holds
    both. Every lookup BY path therefore has to ask for all of them, or a
    `bookctl sync` after the storage rule changed would fail to recognise
    files it linked itself last week and queue them all for review.

    Ordered most-canonical first and de-duplicated, so a caller that wants a
    single answer can take `[0]` and get what `store()` would write.
    """
    given = Path(path).expanduser()
    forms = [store(conn, given)]
    for candidate in (str(given), str(given.resolve())):
        if candidate not in forms:
            forms.append(candidate)
    return forms
