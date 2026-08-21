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

## Note on priority

He says PNG "were being exported" — past tense — which may mean PNG stopped
working too, or simply that PNG is what he used to use. Worth clarifying: if
PNG has ALSO regressed then this is a renderer regression rather than an SVG
quirk, and it matters more than "use the PDF for now" suggests.
