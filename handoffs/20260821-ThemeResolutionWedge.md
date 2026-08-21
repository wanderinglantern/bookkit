# `themes/marsh.json` not found — the program is wedged, not the render

Grant, 2026-08-21, on the production machine: assigning a program to the SOI
schedule errored with `themes/marsh.json` not found. He notes the file is at
`~/Scripts/towerkit/themes/` and suspects yesterday's folder move.

He is right about the cause and it is worse than a failed render.

## What is actually happening

`render.theme` is stored RELATIVE by contract — program files are portable, and
towerkit's validator makes an absolute path an outright error. But it is
resolved **relative to the current working directory**:

- `towerkit/theme.py::available_themes` starts from `Path("themes")`;
- `towerkit/theme.py::load_theme` does `Path(path).read_text()`;
- `towerkit/validate.py::_check_render_theme` calls `load_theme(theme)` and
  raises an ERROR when it cannot read, documenting the choice: "resolution is
  relative to the working directory, deliberately, because that is exactly how
  `towerctl render` resolves it".

So the stored value only works while the process runs from a directory that
happens to contain `themes/marsh.json`. Grant's folders moved; nothing pinned
the working directory; every stored theme stopped resolving.

**And the failure is not confined to rendering.** Every bookkit program write
re-validates the whole file (`sync.write_through`), so an unreadable
`render.theme` makes `_check_render_theme` fail EVERY write to that program —
including the SOI-schematic toggle he was trying to set, which is what he saw.
The program is wedged until the theme resolves. CLAUDE.md already records this
exact genus from the picker's first afternoon: "one bad value wedged the whole
program until the JSON was hand-edited."

## The fix, and why it is small

**`marsh.json` SHIPS PACKAGED** — `src/towerkit/themes/marsh.json`. So the file
he needs is present on every machine towerkit is installed on, and
`available_themes()` already reports it (as an absolute packaged path).

The stored value `themes/marsh.json` is, in practice, a NAME. Two callers
already resolve it that way rather than literally:

- bookkit's `web/routes/program.py::_resolve_theme` — "resolved against the
  themes this machine can actually see, by NAME, rather than against whatever
  directory happens to have launched the server";
- the picker in `_theme_choices`, which lists by stem.

The one that does NOT is `towerkit.theme.load_theme`, which the renderer and
the validator both go through. Make it fall back to a by-name match against
`available_themes()` when the literal relative path misses, and all three agree:

- Grant's files resolve immediately, with no data change and no folder move;
- the value stays relative, so portability is untouched;
- the validator's prediction becomes true again — it predicts against the same
  resolution the renderer performs, which is the stated point of the check;
- bookkit's `_resolve_theme` collapses into a call to the shared rule instead
  of being a second copy of it (DRY).

Absolute paths stay an error. That rule is unrelated and correct.

**Bonus this unlocks:** `_theme_choices` currently FILTERS OUT packaged themes,
because storing their absolute path fails validation — so the picker cannot
offer the very themes that ship. With by-name resolution it can offer them as
`themes/<stem>.json`, a portable value that resolves anywhere.

## Immediate unblock, before any code

He cannot fix it from the UI: changing the theme is itself a write, and writes
are refused while the file is wedged. Either

- run `bookctl` from a directory that has `themes/marsh.json` beside it, or
- hand-edit the program JSON's `render.theme` (drop the key to fall back to
  the built-in default).

To confirm the diagnosis on his machine:

    cd <wherever bookctl runs> && python -c "
    from towerkit.theme import available_themes
    print([str(p) for p in available_themes()])"

If that list has an absolute `.../towerkit/themes/marsh.json` and no relative
`themes/marsh.json`, this is exactly the bug.

## Tests to write with the fix

- a program storing `themes/marsh.json`, validated from a CWD with no
  `themes/` dir, is CLEAN and renders (this is the regression);
- an absolute path is still an error;
- a name that matches nothing still errors, and the message still names it;
- the picker offers packaged themes, and every option it offers validates
  (the "offer only what is storable" rule, which is how the picker got its
  filter in the first place).
