# Bound vs proposed, per POLICY — Grant, 2026-08-21

> "bound vs. unbound/proposed status per policy. This extends to the SOI
> reports as well, but showing that individual policies are either bound or
> proposed (i.e. unbound). Policies are bound at a policy level not a program
> level."

Logged. This is the same underlying gap as the renewal-drift brainstorm and
half of the open-items export refinements, and they should probably be settled
together.

## Where the model says "program", and he is saying "policy"

- `models.PlacementStatus` — prospective / submitted / quoted / bound / lapsed
  — is on the PLACEMENT, i.e. the whole program.
- towerkit's `Program.placement` is `bound` / `proposed`, also whole-program.
- A LAYER has `policy_number`, `period`, `auditable` and (as of today)
  `policy_group` — everything about the issued policy EXCEPT whether it is
  actually bound.

So the book cannot currently say "the primary is bound, the excess is still
proposed", which is the ordinary state of a program mid-placement and,
per the renewal-drift note, the ordinary state of a whole account mid-renewal.

## Why this matters beyond a label

Three surfaces already make claims that depend on the answer:

1. **The SOI / client schedule.** He says it directly: the schedule has to mark
   each policy bound or proposed. A schedule that presents proposed cover as
   bound is a document sent under his name saying the client has cover they do
   not have. This is the sharpest consequence and should drive the design.
2. **The open-items export subtotals** — his other request today: show the
   BOUND subtotal as the total, and let unbound total separately, ignored for
   primary SOI purposes. That request is unbuildable until "bound" exists per
   policy; right now the only bound/unbound split available is per PROGRAM.
3. **The book's premium column.** CLAUDE.md: "the book's per-account column is
   bound premium summed over the account". Summed from placement status today.
   If bound becomes per-layer, that sum must follow, or the headline number and
   the schedule will disagree.

## The likely shape, to be confirmed in the brainstorm

A `bound` (or `placement`) status ON THE LAYER, defaulting to the program's,
with the program's value becoming derived — "bound" when every layer is,
"partially bound" otherwise. That keeps existing files valid and gives the SOI
something true to print per row. It is a towerkit model change plus a bookkit
projection change, and `policy_group` means the unit that binds may be a GROUP
of layers rather than a single one (WC Part A and Part B bind together).

## ANSWERED by Grant, 2026-08-21

**1. A POLICY binds, not a layer.**

So `policy_group` is the thing that carries the status and layers inherit from
their group. The consequence that makes this workable: a layer with NO
`policy_group` is a policy of ONE and binds on its own — which is exactly the
reading `edit.link_policy` already takes ("a group left with one member is a
policy with one part, which is the ordinary case"). No layer needs a group
before it can have a status.

Implementation this points at: the status lives on the LAYER, with a validator
rule that every layer sharing a `policy_group` agrees, and a UI that sets it
for the whole group at once. That is cheaper and less disruptive than inventing
a Policy record, and it keeps every existing file valid. Confirm before
building — the alternative (a real Policy entity) is defensible and harder to
retrofit later.

**2. The PROGRAM-level status stays a separate fact he sets.** Not derived, for
now. So `Placement.status` and towerkit's `Program.placement` are unchanged and
keep meaning what they mean; the per-policy status is additive. This is the
low-risk answer and it means nothing existing has to move.

**3. The SOI shows proposed policies in a SEPARATE SUBGROUP, marked.** Not
hidden, and not mixed in. That settles the shape of the schedule: bound cover
in the main body, proposed cover grouped beneath it and labelled. It also
answers the open-items export request — the bound subtotal is THE subtotal, and
unbound totals only within its own group.

**4. The book's bound-premium headline counts BOUND LAYERS ONLY.**

EXPECT THIS TO CHANGE THE NUMBER ON HIS BOOK, visibly, on the day it ships.
Today that column sums `placement.total_premium` over placements whose STATUS
is bound (CLAUDE.md: "the book's per-account column is bound premium summed
over the account"). Moving to bound LAYERS means a program marked bound whose
excess is still proposed will report less than it does now — which is the point,
and is the whole reason he asked. Say so in the changelog and expect the
headline to drop on partially placed accounts; a silently different revenue
figure is worse than a loudly different one.

## Build order, once confirmed

1. towerkit: the per-layer status + the group-agreement validator rule.
2. bookkit: project it, and make the layer/details UI set it per POLICY (i.e.
   for every layer in the group at once).
3. The SOI's proposed subgroup.
4. The book's headline, and the changelog entry that warns the number moved.
5. The open-items export subtotals, which were blocked on all of the above.

## Related

- handoffs/20260821-AssigneeDisplayAndMulti.md — the renewal-drift brainstorm
  at the end of that file. Same gap, different symptom.
- The open-items export refinements Grant listed today, which need this first.
