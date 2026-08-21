# SVG exports are not clean — Grant, 2026-08-21

> ".svg files are not exporting cleanly as .png were being exported and were
> the files that i was mainly using on programs. can use .pdf versions for now,
> but log the bug for fixing later"

Logged, not investigated. PDF is his workaround; this is not blocking.

## What he is saying, as I read it

PNG exports were clean and are what he used. SVG is not clean. He does not say
HOW it is unclean — that is the first thing to establish, because "unclean"
covers at least four different bugs with different fixes:

- text overlapping or clipped (the same class as the tower label overprint he
  reported earlier today, which turned out to be two layers at one attachment
  — check the program is valid first, and the diagnostics strip now says so);
- fonts not embedded, so the SVG renders with fallbacks anywhere but his
  machine;
- geometry differing from the PNG (a different renderer path);
- the file being valid but unusable in whatever he opens it with.

**Ask him for one bad SVG and what he opens it in before touching anything.**
A PNG and an SVG of the same program, side by side, will separate "the tower is
wrong" from "the SVG writer is wrong" in one look.

## Where to look

- `../towerkit/src/towerkit/render/mpl_program.py` — `render_program` takes
  `formats=[...]`; PNG, SVG and PDF all come out of matplotlib here, so a
  format-specific fault is most likely in how matplotlib writes SVG (text as
  paths vs text as `<text>`, font embedding) rather than in the geometry.
- bookkit reaches it through `web/routes/program.py::_export_tower`, which
  passes the format straight through — unlikely to be the culprit, but it is
  the path a browser download takes.
- towerkit's own `towerctl render --format svg` should reproduce it OUTSIDE
  bookkit. If it does, the bug is entirely towerkit's and should be fixed and
  tested there.

## Priority: answered

PNG STILL WORKS; SVG alone is broken (Grant, 2026-08-21). So this is not a
renderer regression and the geometry is fine — the same figure comes out right
in two other formats. That points squarely at how matplotlib WRITES SVG rather
than at anything towerkit computes, and it narrows the four candidates to
essentially two: fonts not embedded (so the file renders with fallbacks
elsewhere), or text emitted as `<text>` with a font nothing else has, rather
than as paths.

`render_program` passes `formats` through to matplotlib's savefig. The knob to
look at first is `matplotlib.rcParams["svg.fonttype"]`: `"path"` converts text
to outlines and is portable everywhere, `"none"` emits `<text>` and relies on
the viewer having the font. towerkit vendors its own faces (Noto Sans, Noto
Serif, JetBrains Mono), which almost nothing else on a corporate machine will
have — so if the default is `"none"`, every SVG is being written to depend on
fonts only that machine has.

Still get a bad SVG and the viewer name before changing anything: the fix is
cheap but the diagnosis should not be guessed.
