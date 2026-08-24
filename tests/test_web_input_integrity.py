"""What a refusal looks like, how long it lasts, and whether a box says what
it wants.

Three researched rules from .claude/skills/data-entry-integrity, all of them
about the moment somebody is typing into this app:

  3. Validate on blur, CLEAR ON KEYSTROKE. A message that survives its own fix
     makes a corrected value read as still broken, and people stop trusting
     the messages at all.
  4. One error signal, not four — and never error styling on a neutral state.
  6. Labels stay visible. An in-row add form is not an exception.

The suite has no JS runtime, so the honest server-side assertions are that the
behaviour is wired, that the page actually loads the script, and that the
class names the listener clears are the ones the templates render. The DOM
effect itself (the outline going away mid-keystroke) is verified by reading
inline-cell.js.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app

WEB = Path(__file__).resolve().parents[1] / "src" / "bookkit" / "web"
STATIC = WEB / "static"
TEMPLATES = WEB / "templates"

# The in-row add forms: every one of them lives inside a table row, and every
# one of them used a placeholder where a label belongs.
IN_ROW_FORMS = (
    "account/_market_add.html",
    "account/_line_add.html",
    "account/_term_form.html",
    "account/_worksheet.html",
)

# What `{{ ... }}` collapses to in the scan below. A dynamic value is not
# comparable to another dynamic value, so the placeholder-vs-label check has
# to be able to tell "these are both expressions" from "these are the same
# words twice".
DYNAMIC = "\x01"


@pytest.fixture
def app_and_org(snapshot_db: Path):
    """An account with a placement, loopback host — web/origin.py refuses
    TestClient's default "testserver"."""
    app = create_app(snapshot_db)
    from bookkit.repo import orgs, placements

    conn = app.state.conn
    org = next(
        o for o in orgs.list_orgs(conn, kind="client") if placements.for_org(conn, o.id)
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, org


def _inline_cell_js() -> str:
    """inline-cell.js with its comments REMOVED.

    Not fussiness: the first mutation round on this file went green against a
    commented-out listener, because the prose in that file names every class
    it clears and an assertion over the raw text is satisfied by the
    explanation of the rule rather than the rule."""
    source = (STATIC / "inline-cell.js").read_text()
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


def _css_block(name: str) -> str:
    """The declarations of one CSS rule, comments stripped — the comments
    explain these rules and must not trip the tests that read them."""
    css = re.sub(r"/\*.*?\*/", "", (STATIC / "app.css").read_text(), flags=re.S)
    match = re.search(re.escape(name) + r"\s*\{(.*?)\}", css, flags=re.S)
    assert match, f"{name} is gone from app.css"
    return match.group(1)


# --- rule 3: the refusal dies on the first keystroke -----------------------


def test_a_refusal_clears_on_the_first_keystroke():
    """The message must go the moment the input is corrected, not at the next
    POST. Delegated `input` listener, because every surface it covers is
    swapped in by htmx long after load — an inline handler per input would be
    a copy of the rule per template.

    JS is not executed here. What is asserted is that the listener exists, is
    delegated off document.body, and clears BOTH halves of a cell refusal
    (the `cell-error` outline class and the `.cell-error-msg` text) as well as
    the whole-form `.form-error`."""
    js = _inline_cell_js()

    assert 'document.body.addEventListener("input"' in js, (
        "nothing clears a refusal while typing, or the listener is not "
        "delegated — htmx-swapped fragments would never be covered by it"
    )
    assert 'classList.remove("cell-error")' in js, "the red outline survives the fix"
    assert ".cell-error-msg" in js and ".form-error" in js, (
        "the refusal TEXT is not cleared — only its outline"
    )


def test_the_input_listener_covers_the_whole_form_as_well_as_the_cell():
    """Both renderers, not just the inline cell. `.form-error` sits at the top
    of macros/form.html's form through the entire correction otherwise."""
    js = _inline_cell_js()
    scope = re.search(r"var ERROR_SCOPE = \"(.*?)\";", js)
    assert scope, "ERROR_SCOPE is gone; the listener has no idea what to clear"
    selectors = {s.strip() for s in scope.group(1).split(",")}

    assert ".cell-editing" in selectors, "the inline cell is not covered"
    assert ".entity-form" in selectors, "the whole-form renderer is not covered"
    assert ".market-add-form" in selectors, "the in-row add forms are not covered"
    # A refused named-limit add renders its message at the top of the
    # layer-details <td>, OUTSIDE the .market-add-form being corrected.
    assert ".layer-details" in selectors, (
        "the details row's own message would survive a correction typed in it"
    )


def test_the_classes_the_listener_clears_are_the_ones_the_templates_render():
    """Anti-drift. The class name IS the contract between the templates and
    inline-cell.js; rename it on either side and the message silently becomes
    permanent again — a failure nobody sees until they mistype something."""
    js = _inline_cell_js()

    cell = (TEMPLATES / "macros" / "cell.html").read_text()
    form = (TEMPLATES / "macros" / "form.html").read_text()

    assert "cell-error" in js
    assert 'class="cell cell-editing{% if error %} cell-error' in cell
    assert ".cell-error-msg" in js and 'class="cell-error-msg"' in cell
    assert ".form-error" in js and 'class="form-error"' in form
    for name in IN_ROW_FORMS:
        source = (TEMPLATES / name).read_text()
        assert 'class="cell-error-msg"' in source, (
            f"{name} renders a refusal the listener cannot find"
        )


def test_the_clearing_script_is_actually_loaded(app_and_org):
    """A green suite proves nothing broke, not that the new path is taken: the
    listener only runs if the page loads the file it lives in."""
    client, org = app_and_org

    page = client.get(f"/accounts/{org.ref}/program")
    asset = client.get("/static/inline-cell.js")

    assert "/static/inline-cell.js" in page.text
    assert asset.status_code == 200
    assert 'addEventListener("input"' in asset.text


def test_the_escape_and_unchanged_guards_survive():
    """Load-bearing, per CLAUDE.md: Escape's own close blurs the editor and
    must not then commit what Escape discarded, and an UNCHANGED value closes
    without writing. Anything added to this file has to leave both standing."""
    js = _inline_cell_js()

    # Marks on the nodes since 2026-08-21, not globals: the global Escape
    # flag reset on a zero-timer, lost the race to its own revert's network
    # round trip, and COMMITTED the discarded value to the database.
    assert "__bkCancelled" in js
    assert "if (!cell || cell.__bkCancelled) return;" in js
    assert "if (!form || form.__bkCommitting) return;" in js
    assert 'cell.getAttribute("data-opened-with")' in js
    assert "revert(cell);" in js
    # the unchanged branch returns BEFORE the submit below it
    unchanged = js.split('cell.getAttribute("data-opened-with")')[1]
    assert unchanged.index("revert(cell);") < unchanged.index("requestSubmit()")


# --- rule 4: one signal, and never a signal on a neutral state -------------


def test_a_carrier_the_book_does_not_know_is_not_an_error():
    """"This carrier is not a market in the book yet" is a NEUTRAL fact about
    work nobody has done — no write was refused and nothing is invalid — and
    it was wearing warn colour AND a warn wash AND a warn border. Error/warn
    styling on a neutral state is the clearest form of the stacking defect."""
    block = _css_block(".market-unlinked")

    assert "--warn" not in block, "a neutral state is still styled as a warning"
    assert "--danger" not in block, "a neutral state is still styled as an error"
    assert "background" not in block, "the wash is still stacked on the border"

    # The word, not the colour, is the signal — and it survives greyscale.
    chip = (TEMPLATES / "account" / "_market_row.html").read_text()
    assert ">new<" in chip
    assert "is not a market in the book" in chip
    assert "border" in block, "the badge stopped being a badge in greyscale"


def test_the_broken_panel_does_not_stack_its_signals():
    """This one IS a real error, so it keeps a strong signal — the red rule
    down its left edge — plus the message. The danger WASH was the redundant
    third: no information the rule and the red heading did not carry, and it
    tinted the one line worth reading (the loader's own words)."""
    block = _css_block(".panel-broken")

    assert "border-left" in block, "the one signal is gone too"
    assert "background" not in block, "border AND wash are still stacked"


# --- rule 6: labels stay visible ------------------------------------------


class _Controls(HTMLParser):
    """Every user-facing control in a fragment, and whether a <label> is open
    around it.

    Nesting the control inside its label is the association these fragments
    need: they are rendered once per layer and once per term, so a for=/id=
    pair would mint duplicate ids down the page."""

    SKIP = {"hidden", "checkbox", "radio", "submit", "button", "reset"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.found: list[tuple[str, dict[str, str | None], bool]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "label":
            self.depth += 1
            return
        if tag not in ("input", "select", "textarea"):
            return
        got = dict(attrs)
        if got.get("type") in self.SKIP:
            return
        self.found.append((tag, got, self.depth > 0))

    def handle_endtag(self, tag: str) -> None:
        if tag == "label" and self.depth:
            self.depth -= 1


def _controls(source: str) -> list[tuple[str, dict[str, str | None], bool]]:
    """Jinja out, HTML in. Comments go FIRST — _layer_details.html's own
    comments talk about <td> and <tr>, which the parser would otherwise read
    as markup."""
    html = re.sub(r"\{#.*?#\}", "", source, flags=re.S)
    html = re.sub(r"\{%.*?%\}", " ", html, flags=re.S)
    html = re.sub(r"\{\{.*?\}\}", DYNAMIC, html, flags=re.S)
    parser = _Controls()
    parser.feed(html)
    return parser.found


@pytest.mark.parametrize("name", IN_ROW_FORMS)
def test_every_in_row_control_has_a_visible_label(name: str):
    """Placeholder-as-label vanishes on the first keystroke, so the row says
    what it wants only until somebody starts answering it — and a placeholder
    is not an accessible name at all. Three of these controls had NO label and
    NO aria-label before this."""
    controls = _controls((TEMPLATES / name).read_text())
    assert controls, f"{name} has no controls; the scan is not looking at it"

    bare = [got.get("name") for _, got, labelled in controls if not labelled]
    assert not bare, f"{name}: control(s) with no visible label: {bare}"


@pytest.mark.parametrize("name", IN_ROW_FORMS)
def test_every_in_row_control_has_an_accessible_name(name: str):
    controls = _controls((TEMPLATES / name).read_text())
    nameless = [got.get("name") for _, got, _ in controls if not got.get("aria-label")]
    assert not nameless, f"{name}: control(s) with no aria-label: {nameless}"


@pytest.mark.parametrize("name", IN_ROW_FORMS)
def test_no_placeholder_repeats_its_own_label(name: str):
    """A placeholder survives only as a FORMAT example ("250,000",
    "1.5m · 250k"). The moment it repeats the label it is the label again,
    wearing a disguise that falls off on the first keystroke.

    A dynamic placeholder or label is skipped rather than guessed at: two
    Jinja expressions are not evidence of the same words twice."""
    for _, got, _ in _controls((TEMPLATES / name).read_text()):
        placeholder = (got.get("placeholder") or "").strip().lower()
        label = (got.get("aria-label") or "").strip().lower()
        if not placeholder or DYNAMIC in placeholder or DYNAMIC in label:
            continue
        assert placeholder != label, (
            f"{name}: {got.get('name')} is labelled by its placeholder"
        )
        assert placeholder not in label, (
            f"{name}: {got.get('name')}'s placeholder is a slice of its own label"
        )


def test_the_labels_reach_the_page(app_and_org):
    """The source scan above is not enough on its own — a template nothing
    renders is a template nothing fixes. These are the fragments the routes
    hand back, fetched for real."""
    client, org = app_and_org
    conn = client.app.state.conn
    from bookkit import sync
    from bookkit.repo import placements

    placement = next(
        p for p in placements.for_org(conn, org.id)
        if p.program_path and sync.layer_details(conn, p.id)
    )
    layer = sync.layer_details(conn, placement.id)[0]
    base = f"/accounts/{org.ref}/program/{placement.id}"

    worksheet = client.get(f"{base}/worksheet?layer={layer['id']}").text
    fragments = {
        "the market add row": worksheet,
        "the named-limit add form": worksheet,
        "the line add form": client.get(f"{base}/lines/new").text,
        "the retention form": client.get(f"{base}/retentions/new").text,
    }

    for what, fragment in fragments.items():
        assert 'class="inline-field"' in fragment, (
            f"{what} still labels with a placeholder"
        )

    assert 'aria-label="named limit name"' in fragments["the named-limit add form"]
    assert 'aria-label="retention type"' in fragments["the retention form"]
    assert 'aria-label="line of coverage"' in fragments["the line add form"]
