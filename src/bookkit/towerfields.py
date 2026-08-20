"""towerkit's writable field surface, expressed in bookkit's own vocabulary.

`towerkit.mcpsurface.SURFACE` is DERIVED from the pydantic models at import
time: every writable field, its type, its guards, its bounds, whether it can be
cleared, and — for a nested scalar — the container that has to exist first. And
`towerkit.edit.set_field` is THE choke point every scalar write passes through,
where the cross-field guards and the normalisers live.

bookkit reads that surface instead of listing the fields itself. D6 arrived as
seventeen named fields (`web/parity.py:TOWERKIT_MODEL_FIELDS`), and seventeen
bespoke routes would have been seventeen places to edit the day towerkit grows
an eighteenth — which is the exact drift the field ledger exists to catch, and
which it caught silently five times before anybody noticed (Layer.named_limits,
states, limitsDetail, retentionDetail, premiumDetail all grew with every parity
test green). One bridge means a new towerkit field arrives here already parsed,
already refused correctly, and needing only a place to be PUT.

What this module owns is exactly the translation, in both directions:

- an `Entry` becomes a `forms.spec.Field`, so the existing cell and form macros
  render it with no per-field markup;
- typed text becomes the wire value `mcpsurface.parse_value` accepts;
- a stored model value becomes the display string, and the editor's pre-fill.

THE MONEY BOUNDARY IS HERE, and it runs the same way it runs everywhere else in
bookkit: entry accepts CENTS, towerkit files carry WHOLE DOLLARS, and
`money.cents_to_dollars` is the one conversion — it refuses a sub-dollar amount
rather than rounding it away (CLAUDE.md). A caller typing into a towerkit-backed
money cell types what they type into every other money cell in the app.
"""

from __future__ import annotations

import re
from typing import Any

from towerkit import mcpsurface

from .forms.spec import Field
from .money import (
    MoneyParseError,
    cents_to_dollars,
    dollars_to_cents,
    format_cents_compact,
    format_share_pct,
    parse_money_cents,
    parse_share_bps,
)


class FieldRefused(ValueError):
    """A typed value this field cannot take, in the field's own words.

    Distinct from `mcpsurface.BadValue` only so that callers catch ONE type
    whether the refusal came from towerkit's lexicon or from bookkit's money
    parser — the two meet in `to_wire` and a surface should not have to know
    which side of the boundary said no.
    """


# The bookkit form kind each towerkit type is rendered and parsed as. A type
# missing from here is a type this bridge cannot render, and `bookkit_field`
# says so rather than defaulting to a text box that would post a value the
# field refuses — an editor that cannot save is worse than no editor.
_KINDS: dict[str, str] = {
    "text": "text",
    "date": "date",
    "money": "money",
    "enum": "select",
    "bool": "select",
    "share_bps": "share",
    "list_of_strings": "text",
}

# The two wire literals a bool cell posts. Spelled as words in the menu because
# "showTotals: true" is a JSON fact and "totals: yes" is the question the broker
# is actually answering; the VALUE stays the literal `parse_value` demands,
# which refuses a coerced "true" string on purpose (these five decide what a
# saved chart prints).
_BOOL_OPTIONS: tuple[tuple[str, str], ...] = (("yes", "true"), ("no", "false"))

# Human labels where the derived one would be wrong or bare. Everything else
# falls through to `_derived_label`, so a field towerkit grows is legible the
# day it appears rather than waiting for a line here.
_LABELS: dict[str, str] = {
    "layer.limitsDetail": "limits detail",
    "layer.retentionDetail": "retention detail",
    "layer.premiumDetail": "premium detail",
    "layer.states": "states",
    "layer.policyNumber": "policy number",
    "line.abbr": "column label",
    "participant.share_bps": "share",
    "retention.appliesTo": "applies to",
    "sublimit.appliesTo": "applies to",
    "program.render.theme": "theme",
}

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _derived_label(name: str) -> str:
    """`render.showTotals` -> "show totals". Formatting, not parsing: the
    dotted head is dropped because the panel it renders in already names the
    container, and camelCase is split where a lowercase meets an uppercase."""
    tail = name.rsplit(".", 1)[-1]
    return _CAMEL.sub(" ", tail).replace("_", " ").lower()


def resolve(kind: str, name: str) -> mcpsurface.Entry:
    """The entry for one field, or `FieldRefused` naming what is settable.

    Wraps `mcpsurface.resolve`'s KeyError so a mistyped field name reaching a
    route becomes a refusal with the field set in it, not a 500. A DENIED field
    resolves to nothing here for the same reason it does over MCP — the denial
    is data, with a reason written for the caller, so it is printed verbatim.
    """
    reason = mcpsurface.denied_reason(kind, name)
    if reason is not None:
        raise FieldRefused(f"{kind}.{name} is not settable here. {reason}")
    try:
        return mcpsurface.resolve(kind, name)
    except KeyError as exc:
        known = ", ".join(mcpsurface.SURFACE.get(kind, {})) or "nothing"
        raise FieldRefused(f"{exc.args[0]} — {kind} takes: {known}") from exc


def label(entry: mcpsurface.Entry) -> str:
    key = f"{entry.kind}.{entry.field}"
    return _LABELS.get(key, _derived_label(entry.field))


def bookkit_field(entry: mcpsurface.Entry, key: str | None = None) -> Field:
    """One `Entry` as the Field both renderers already know how to draw.

    `key` overrides the form key: a cell posts under the name its route reads,
    and `render.showTotals` is not a legal HTML form-field name to route on.
    """
    kind = _KINDS.get(entry.type)
    if kind is None:
        raise FieldRefused(
            f"{entry.kind}.{entry.field} is a {entry.type}, which has no inline "
            f"editor — it is edited by the control built for it"
        )
    options: tuple[tuple[str, str], ...] = ()
    if entry.type == "bool":
        options = _BOOL_OPTIONS
    elif entry.type == "enum":
        options = tuple((value, value) for value in entry.values or ())
    return Field(
        key=key or entry.field,
        label=label(entry),
        kind=kind,
        options=options,
        # `required` on the FIELD is the browser refusing an empty submit, so
        # it tracks `entry.required` — whether the MODEL needs a value — and
        # NOT `clearable`, which asks the narrower question of whether the
        # value may be None. `layer.states` is neither required nor clearable:
        # it is a `list[str]` that is legally EMPTY, and marking it required
        # would make clearing the last state unreachable from the keyboard
        # while towerkit accepts it happily.
        required=entry.required,
        placeholder=_placeholder(entry),
    )


def _placeholder(entry: mcpsurface.Entry) -> str:
    if entry.type == "list_of_strings" and entry.accepts_comma_string:
        # towerkit's own syntax, stated by `edit.parse_states`, which is the
        # single definition of it — the TUI enters states this way too.
        return "IL, WI, IN"
    return ""


# --- the wire, both directions --------------------------------------------------


def to_wire(entry: mcpsurface.Entry, text: str) -> Any:
    """Typed text into the value `mcpsurface.parse_value` will accept.

    Two conversions happen here and nowhere else. MONEY is typed in cents and
    handed over in whole dollars, through bookkit's own parser so that "1.5m",
    "250k" and "1,234.56" mean here what they mean in every other money cell —
    and `cents_to_dollars` refuses the sub-dollar remainder rather than
    silently dropping it. A BOOL is the literal `true`/`false` its select
    posted, turned into a real bool, because towerkit refuses a coerced string
    on purpose. Everything else travels as the string it was typed as and is
    parsed by the field's own lexicon, which is what makes the refusals name
    a form that WOULD be accepted.
    """
    stripped = text.strip()
    try:
        if entry.type == "money":
            if not stripped:
                return _cleared(entry)
            return cents_to_dollars(parse_money_cents(stripped))
        if entry.type == "share_bps":
            if not stripped:
                return _cleared(entry)
            return parse_share_bps(stripped)
        if entry.type == "bool":
            if not stripped:
                return _cleared(entry)
            if stripped not in ("true", "false"):
                raise FieldRefused(f"{label(entry)} takes yes or no, not {text!r}")
            return stripped == "true"
        if entry.type == "text" and not stripped:
            # Routed through `_cleared` rather than left to `parse_value`,
            # which refuses an empty required field in the JSON caller's words.
            # Same rule, sentence this surface can act on — see `_cleared`.
            return _cleared(entry)
        if entry.type == "list_of_strings" and not entry.accepts_comma_string:
            raise FieldRefused(
                f"{label(entry)} is a list of lines and is edited by its own "
                f"checkboxes, not as typed text"
            )
        # Everything left goes through the field's OWN lexicon, including the
        # comma-separated list: `edit.parse_states` is the single definition of
        # that syntax and `_parse_list` is the one caller of it. An empty string
        # is an empty LIST there, not a clear, which is what makes removing the
        # last state a normal save.
        return mcpsurface.parse_value(entry, stripped)
    except FieldRefused:
        # Already in this module's own words, and already carrying the label.
        # Without this it falls into the ValueError arm below and comes back
        # as "amount: amount is not optional…".
        raise
    except MoneyParseError as exc:
        raise FieldRefused(f"{label(entry)}: {exc}") from exc
    except mcpsurface.BadValue as exc:
        raise FieldRefused(str(exc)) from exc
    except ValueError as exc:  # parse_share_bps, cents_to_dollars
        raise FieldRefused(f"{label(entry)}: {exc}") from exc


def _cleared(entry: mcpsurface.Entry) -> None:
    """An empty submit, checked against the field's own optionality — so the
    refusal comes back BEFORE a write is attempted and says the same sentence
    towerkit would have said."""
    if not entry.clearable:
        # Deliberately NOT `mcpsurface.VALUE_RULES['clearing']`, which is
        # written for a JSON caller ("send null…") and names a distinction
        # between null and "" that a text input cannot express. The rule is
        # the same; the sentence is the one this surface can act on.
        raise FieldRefused(f"{label(entry)} is required — it cannot be left empty")
    return None


def display(entry: mcpsurface.Entry, value: Any) -> str:
    """The stored value as the display cell shows it — empty for absent, which
    the cell macro renders as an em-dash.

    Money reads COMPACT here, matching the tower drawing and every other money
    cell (D5); `editor_text` below keeps the exact figure for the pre-fill, and
    the split is the whole reason compact display is safe.
    """
    if value is None or value == [] or value == "":
        return ""
    if entry.type == "money":
        return format_cents_compact(dollars_to_cents(int(value)))
    if entry.type == "share_bps":
        return format_share_pct(int(value))
    if entry.type == "bool":
        return "yes" if value else "no"
    if entry.type == "list_of_strings":
        return ", ".join(str(item) for item in value)
    return str(value)


def editor_text(entry: mcpsurface.Entry, value: Any) -> str:
    """The editor's pre-fill: EXACT, never compact.

    A cell that pre-fills "1.5m" and then refuses it, or pre-fills a rounded
    figure its own parser would store as a different number, is unsaveable
    until the value is retyped from memory — the cents lesson (CLAUDE.md,
    2026-08-15) stated for towerkit's side of the boundary.
    """
    if value is None or value == [] or value == "":
        return ""
    if entry.type == "money":
        return str(int(value))
    if entry.type == "bool":
        return "true" if value else "false"
    if entry.type == "list_of_strings":
        return ", ".join(str(item) for item in value)
    if entry.type == "share_bps":
        return format_share_pct(int(value))
    return str(value)
