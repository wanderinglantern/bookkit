# towerkit R66 — the record of how the agreement rule was settled

> **Superseded. The decision now lives in
> `2026-08-17-towerkit-web-conversion.md`, section D2.1**, spliced there on
> 2026-08-18. This file is kept as the record of how it was arrived at, because the
> reasoning is worth more than the conclusion.

## What was settled

Grant approved the two-renderer split on 2026-08-18 — *"HTML fine for the UI and
interface rendering with SVG being able to be exported just as the TUI."* The rule
that came out of it: **the two renderers must agree about the FACTS a block asserts;
they may differ about which candidate string a fitter chose to assert them with.**

That rule survived two independent adversarial passes unchanged, and it is not an
invention — `towerkit/src/towerkit/render/labels.py:1-3` already calls itself the
single authority both existing renderers quote, and those two already fit by
completely different means while being required to say the same things.

## What was deliberately NOT settled, and why

**The agreement test's mechanics.** Two rounds of specification produced a test that
was broken the same way both times: it compared per-block strings, which conflates
*wrapping* — a fit difference the rule explicitly permits — with *fact divergence*.
It would have failed on legitimate differences and taught everyone to weaken it.

The root cause is not carelessness. `towerkit/src/towerkit/render/web.py` **does not
exist**, and neither does `label_visibility`; both are design text in the conversion
spec, despite the ledger and an earlier handoff describing them as built. A test's
mechanics cannot be settled honestly against a module with no signature. Slice 1
writes the test against the real thing.

## What two adversarial rounds actually caught here

Worth keeping, because each is a class of error rather than a one-off:

- **A rejection resting on a false impossibility.** Round 1 rejected comparing the
  SVG export's text on the grounds that it was impossible; matplotlib's SVG backend
  writes the full string as an XML comment before the outlines, in the installed
  package. A rejection whose stated reason is false is worse than no rejection.
- **A test that could not observe what it claimed.** It read `ax.texts` for a layer
  heading the export never draws as its own artist — the winning candidate is a
  combined heading+stack string.
- **A mutation that could not fire**, named as proof that a test was load-bearing.
- **Structure claimed where only convention existed.** Round 2's fix asserted that
  four of the five facts were structurally impossible to diverge; the document's own
  mutation list disproved two of them, because a mutation is only writable if the
  thing can in fact diverge. That is the same defect one level down — the pattern
  that stopped the iteration.

## The one process fact worth carrying

`towerkit` is an **editable** path dependency, and every bookkit worktree resolves
`../towerkit` to the same single checkout. Adding `render/web.py` is instantly live
in every worktree and in the towerkit TUI at once, mid-gate-run included. Slice 1
needs its own towerkit branch and a quiet moment, not just a bookkit worktree.
