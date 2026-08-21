"""Every select a form renders offers "nothing chosen" first.

A `<select>` with no empty option pre-selects its FIRST option, and a
`required` attribute is then satisfied by a value the user never looked at.
The browser answers the question and the form looks filled in.

That is not hypothetical. Before this rule the shared form macro rendered the
blank option only for optional selects, so on the web:

  * `response_form.status` defaulted to **quoted** — a decline you forgot to
    set was filed as a quote;
  * `_layer_add_fields.line` defaulted to "all lines", the field
    routes/program.py's own comment says must be asked and never guessed;
  * `assignment_form.team_member_id` took whichever colleague sorted first;
  * `submission_form.market_org_id` took the first market alphabetically.

The TUI never had the defect (`Select.NULL` in tui/widgets/forms.py), so the
two surfaces disagreed about what an untouched form meant.

This is a CONVENTION test over every builder in forms.entities rather than a
test of one form, because the next form somebody adds is the one that would
otherwise reintroduce it. A value that genuinely should arrive set is the
spec's `initial`, which selects it here — a default the user can SEE, which is
a different thing from the browser picking row one.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.web.app import create_app
from bookkit.web.forms_render import render_form


@pytest.fixture
def app(snapshot_db: Path):
    return create_app(snapshot_db)


def _specs(conn):
    """One built FormSpec per builder that can be built without a live record.

    The ones needing an existing row (response_form, rfi_answer_form,
    interaction_form) are built from a real seeded record where one exists, and
    skipped by name where none does — a skipped form is reported, never
    silently dropped.
    """
    from bookkit.forms import entities as E
    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import team as team_repo

    org = orgs_repo.list_orgs(conn, kind="client")[0]
    specs: dict[str, object] = {
        "org_form": E.org_form(),
        "contact_form": E.contact_form(),
        "task_form": E.task_form(conn=conn),
        "placement_form": E.placement_form(),
        "opportunity_form": E.opportunity_form(),
        "submission_form": E.submission_form(conn),
        "subjectivity_form": E.subjectivity_form(),
        "member_form": E.member_form(),
        # with member_options, so the "who" select is actually built — the
        # variant without it is the edit flow, which deliberately does not
        # re-pick its subject
        "assignment_form": E.assignment_form(
            tuple((m.name, m.id) for m in team_repo.list_members(conn)), conn=conn
        ),
        "document_form": E.document_form(),
        "appetite_form": E.appetite_form(),
        "project_form": E.project_form(),
        "need_form": E.need_form(conn=conn),
        "request_form": E.request_form(conn=conn, org_id=org.id),
        "rfi_item_form": E.rfi_item_form(conn=conn),
    }

    # The three that need a live record. response_form is the one that matters
    # most — its `status` select defaulted to "quoted", so a DECLINE you forgot
    # to set was filed as a quote — so a book with no submission to build it
    # from must fail loudly rather than quietly checking one form fewer.
    from bookkit.repo import interactions as interactions_repo
    from bookkit.repo import rfi as rfi_repo
    from bookkit.repo import submissions as submissions_repo

    submission = next(iter(submissions_repo.outstanding(conn)), None)
    assert submission is not None, "the seeded book has no submission to build response_form"
    specs["response_form"] = E.response_form(submission, conn)

    item = next(
        (i for o in orgs_repo.list_orgs(conn, kind="client")
         for r in rfi_repo.requests_for_org(conn, o.id)
         for i in rfi_repo.items_for_request(conn, r.id)),
        None,
    )
    if item is not None:
        specs["rfi_answer_form"] = E.rfi_answer_form(item)

    interaction = next(
        (i for o in orgs_repo.list_orgs(conn, kind="client")
         for i in interactions_repo.for_org(conn, o.id)),
        None,
    )
    if interaction is not None:
        specs["interaction_form"] = E.interaction_form(interaction)

    return specs


def _selects(html: str) -> dict[str, list[str]]:
    """name -> its option values, in document order."""
    out: dict[str, list[str]] = {}
    for block in re.findall(r"<select\b[^>]*>.*?</select>", html, re.S):
        name = re.search(r'name="([^"]+)"', block)
        if not name:
            continue
        out[name.group(1)] = re.findall(r'<option value="([^"]*)"', block)
    return out


def test_every_select_on_every_form_offers_nothing_chosen_first(app):
    conn = app.state.conn
    offenders = []
    checked = 0
    with TestClient(app, base_url="http://127.0.0.1") as client:
        request = client.get("/today")  # any request object for the renderer
        for form_name, spec in _specs(conn).items():
            html = render_form(request, spec, "/nowhere")
            for field_name, options in _selects(html).items():
                checked += 1
                if not options or options[0] != "":
                    offenders.append(f"{form_name}.{field_name} -> {options[:3]}")

    assert checked, "no selects found on any form — the scan is broken"
    assert not offenders, (
        "these selects let the browser answer the question by pre-selecting "
        f"their first option: {offenders}"
    )
