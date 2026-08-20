---
name: data-entry-integrity
description: Use when designing or reviewing ANY surface that accepts input in bookkit — a form, an inline cell, a picker, an import, an MCP tool argument. Carries the researched rules for constrained input, validation timing, error wording, grouping, defaults and cross-field consistency, plus the insurance-domain facts about limits and sublimits that decide what a field must be able to say.
---

# Data entry integrity

Researched 2026-08-20 after the Program tab's details row was reported as too
dense. The density turned out to be the wrong diagnosis, and the search that
corrected it also produced the rules below. Sources are listed at the bottom;
every claim here traces to one of them or to a bug this project actually
shipped.

Read this before adding a field, a form, or a picker.

## The rules

### 1. Constrained input beats an open text field

If a value has a knowable set of valid options — now, at runtime, on this
machine — the field is a picker, not a text box. An open field for a
constrained value fails twice: nobody can discover what is valid, and nothing
stops what is not.

This is mistake-proofing's *control* method: make the error impossible rather
than detectable. bookkit already does it well in places (`checked_option` is
the strongest single guard in the codebase — a select's own options are the
authority, and the same check doubles as the account-scope check on a
body-supplied id).

**Offer only what is storable.** A picker that lists a choice the system will
then refuse is worse than no picker. `render.theme` shipped as free text and
could store an absolute path, which towerkit's validator refuses as
non-portable — and because every later write re-validates the file, one bad
theme wedged the whole program until somebody edited the JSON by hand. The
picker now filters to relative paths, which are the only ones a program file
may legally name.

**Check the picker server-side too.** The markup constrains a mouse and nothing
else. Every route is reachable by anything that can POST.

### 2. Always render a blank option

A `<select>` with no empty option pre-selects its first option, and `required`
is then satisfied by a value the user never chose. In bookkit this meant a
market response left untouched filed itself as **quoted**, a layer took "all
lines" (which the route's own comment says must be asked and never guessed),
and an assignment took whichever colleague sorted first.

`required` still refuses an empty submit. A field that genuinely should arrive
with a value sets it as the form's `initial` — a default the user can see,
which is a different thing from the browser picking row one.

### 3. Validation timing: on blur, cleared on keystroke

Validating while typing is actively harmful — two studies (n=77, n=90) found
users made significantly *more* errors when messages appeared as they left a
field prematurely, and there is a name for the result: premature error
blindness, where people stop trusting the messages at all. Validating only at
submit is too late. On blur is the answer, and bookkit's commit model already
lands there.

**The error must clear the moment the input is corrected**, keystroke-level,
not at the next submit. A message that survives the fix makes people read a
valid entry as still broken. The TUI is correct here (auto-dismissing toasts);
the web is not yet — a `.cell-error` outline and its message persist until the
next POST.

### 4. One error signal, not four

A red border *and* a red background *and* an icon *and* an inline message reads
as combative rather than helpful. Pick one visual signal plus the message.
Reserve error styling for actual errors — never for a neutral empty state.

### 5. A refusal names the fix

"What went wrong" is half a message. bookkit's own `date_refusal` is the model
to copy: it names the offending value *and* three forms that would be accepted.
Refusals that only state the objection — `cannot parse money value: '1.2mm'`,
`'x' is not a whole number` — fall short of the standard the date field already
sets.

Never let a refusal name a fix that does not exist.

### 6. Labels stay visible; group by proximity

Placeholder-as-label vanishes on the first keystroke and fails accessibility.
Every input gets a real label, and an in-row add form is not an exception.

Related fields must be grouped and unrelated fields separated, or distinct
*kinds* of fact blend. This is what was actually wrong with the layer details
row: administrative facts, coverage prose, named limits, scope and structure
controls were one inline run, so proximity told the reader they were all the
same kind. The fix was a label rail and one group per grid row — same eight
values, nothing hidden.

### 7. Density is not the enemy; undifferentiated density is

Do not "simplify" a working surface by hiding what an expert frequently needs.
Power users want speed, not simplicity, and progressive disclosure relocates
complexity rather than removing it unless everything frequently needed stays up
front. Nielsen's own caveat. Fix hierarchy first; remove fields only when they
are genuinely not wanted.

### 8. Defaults and prefills are dangerous

People skip pre-filled fields and do not check them. Only default when you are
confident the overwhelming majority would choose that value and changing it is
obvious and cheap.

**Never pre-fill a figure that must come off a document** — a premium, a limit,
a date on a quote. If a template is wanted, it should fill the *names* and
leave every figure visibly empty. That inverts the risk: instead of a plausible
wrong number nobody checks, you get a gap nobody can miss.

### 9. Consistency is the weak category

Insurance data-quality practice sorts checks into completeness, conformance,
consistency and timeliness. bookkit is strong on conformance (types and bounds,
now derived from towerkit's own surface) and on timeliness (the sha guard and
the three-way conflict). **Consistency — this field must relate correctly to
that field — is thin**, and it is where the remaining bugs live: a placement
whose period ends before it starts, a subjectivity marked met with no date, a
quote whose response predates its submission.

Prefer enforcing these at the service layer, where both surfaces already meet.
A DB `CHECK` would be a migration and would refuse to apply against existing
violating rows.

## The domain facts that decide what a field must say

A limit or a sublimit is not a number. Getting the *shape* wrong is worse than
getting the value wrong, because a wrong shape cannot be corrected later.

- **A tower's drawn limit is per-occurrence.** That is what the schematic
  shows and what a layer's `limit` means.
- **Most liability lines carry BOTH per-occurrence and aggregate** — "$1M per
  occurrence / $2M aggregate" for GL. The standard CGL declarations block is
  richer still: each occurrence, general aggregate, products/completed
  operations aggregate, personal & advertising injury, damage to premises,
  medical expense. Excess and umbrella layers carry an occurrence limit and
  usually an aggregate of their own.
- **The two CGL aggregates are independent tanks.** Damages paid under the
  general aggregate do not reduce the products-completed operations aggregate,
  or the reverse.
- **A sublimit is part of, not in addition to, the limit it sits under.** It
  may be a dollar figure or a *percentage* of the otherwise-applicable limit —
  professional liability sublimits usually are.
- **"A sublimit sits inside the occurrence limit" is not universally true**,
  and this matters: personal & advertising injury connects directly to the
  general aggregate and bypasses the occurrence limit entirely, while property
  catastrophe sublimits (flood, earth movement) are typically written on an
  **annual aggregate** basis and carved *out* of the occurrence limit. So a
  naive validator rule of "sublimit ≤ occurrence limit" would produce false
  refusals on the most common real cases. Any such check must know the
  sublimit's basis first — which is why the basis has to be a field.
- Sublimits mainly matter on property and specialty; they are not always
  present, but when they are they are load-bearing on the schedule.

**Current gap, not yet built:** towerkit's `Sublimit` holds name, amount,
applies-to and notes. It cannot say per-occurrence vs aggregate, and it cannot
express a percentage. `Retention` carries both a type and an aggregate;
`Sublimit` carries neither. towerkit validates exactly one thing about a
sublimit — that its lines exist — so a $50M sublimit under a $25M tower
validates clean and prints on a client schedule. Fixing this is a towerkit
model change plus a backfill, and is gated on Grant.

## Sources

Research pass, 2026-08-20.

- Baymard Institute — *Usability Testing of Inline Form Validation*
  <https://baymard.com/blog/inline-form-validation>
- Nielsen Norman Group — *Hostile Patterns in Error Messages*
  <https://www.nngroup.com/articles/hostile-error-messages/>
- Nielsen Norman Group — *Progressive Disclosure*
  <https://www.nngroup.com/videos/progressive-disclosure/>
- UX Movement — *Why Users Make More Errors with Instant Inline Validation*
  <https://uxmovement.com/forms/why-users-make-more-errors-with-instant-inline-validation/>
- Innovaccer Design System — *Key Value Pair, usage*
  <https://design.innovaccer.com/components/keyValuePair/usage/>
- Paul Wallas — *Designing for Data Density*
  <https://paulwallas.medium.com/designing-for-data-density-what-most-ui-tutorials-wont-teach-you-091b3e9b51f4>
- Fresh Consulting — *Manage Data Density, High-Level to Low-Level*
  <https://www.freshconsulting.com/insights/blog/ui-ux-principle-52-manage-data-density-high-level-to-low-level/>
- GOV.UK Design System — *Fieldset*
  <https://design-system.service.gov.uk/components/fieldset>
- IRMI — *Sublimit* <https://www.irmi.com/term/insurance-definitions/sublimit>
- IRMI — *How the Limits Apply in the CGL Policy*
  <https://www.irmi.com/articles/expert-commentary/how-the-limits-apply-in-the-cgl-policy>
- Nomad Data — *Extraction of Limits, Sublimits and Deductibles from Complex
  Policy Schedules*
  <https://www.nomad-data.com/doc-chat/instant-extraction-of-limits-sublimits-and-deductibles-from-complex-policy-schedules-property-homeowners-specialty-lines-marine-general-liability-construction-4c42c>
- Adjusters International — *Difference in Conditions Coverage* (flood/quake
  occurrence vs annual aggregate)
  <https://www.adjustersinternational.com/pubs/adjusting-today/difference-in-conditions-coverage/index.html>
- Qualytics — *Financial Data Quality Management in Insurance* (the
  completeness / conformance / consistency / timeliness framing)
  <https://qualytics.ai/resources/in/financial-data-quality-management-insurance>
- Zuko — *How to use Defaults to Optimize your Form UX*
  <https://www.zuko.io/blog/how-to-use-defaults-to-optimize-your-form-ux>
- Baytech Consulting — *Transforming Data Entry: Eliminating Errors*
  <https://www.baytechconsulting.com/blog/transforming-data-entry-how-to-eliminate-errors-and-maximize-efficiency-2025>
- 6Sigma.us — *Poka-Yoke in Six Sigma*
  <https://www.6sigma.us/lean-tools/poka-yoke-six-sigma/>
