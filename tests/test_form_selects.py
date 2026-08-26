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


# --- and every option distinguishable from every other ------------------------
#
# A select whose options print the same words twice asks a question with two
# identical answers. The write underneath is addressed by an id and lands
# correctly, which is exactly what makes it hard to notice — and useless
# anyway, because the reader cannot tell which one they are choosing.
#
# It is never typed in deliberately; it arrives when a change makes a name
# REPEATABLE. Every line of coverage arrives with a layer called "To be
# placed", so a program with two new lines gave the "same policy as" picker two
# identical options (Grant, 2026-08-24) — reported as a list that had not
# refreshed, because that is what it looks like.
#
# THE SCAN IS ONLY AS GOOD AS THE SHAPE IT WALKS, so this builds the shape
# first rather than hoping the fixture has one. A scan over a book where no
# name repeats proves nothing.


def _select_labels(html: str) -> dict[str, list[str]]:
    """name -> its option LABELS, in document order.

    Labels, where `_selects` above reads values: the value is what the write
    uses and is unique by construction; the label is what the reader chooses
    between, and is the half that can collide.
    """
    out: dict[str, list[str]] = {}
    for block in re.findall(r"<select\b[^>]*>.*?</select>", html, re.S):
        name = re.search(r'name="([^"]+)"', block)
        if not name:
            continue
        labels = [
            re.sub(r"\s+", " ", label).strip()
            for label in re.findall(r"<option[^>]*>(.*?)</option>", block, re.S)
        ]
        out[name.group(1)] = labels
    return out


def _dupes(html: str) -> dict[str, list[str]]:
    """field -> the labels it offers more than once. Blank options excluded:
    every select renders exactly one, on purpose."""
    out: dict[str, list[str]] = {}
    for field, labels in _select_labels(html).items():
        real = [label for label in labels if label]
        repeated = sorted({label for label in real if real.count(label) > 1})
        if repeated:
            out[field] = repeated
    return out


def test_no_select_offers_the_same_label_twice(app):
    """Over the Program tab with a shape that MAKES names repeat: two lines of
    coverage, each arriving with its own 'To be placed' layer."""
    from bookkit import sync
    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import placements as placements_repo

    conn = app.state.conn
    org = next(
        o for o in orgs_repo.list_orgs(conn, kind="client")
        if any(p.program_path for p in placements_repo.for_org(conn, o.id))
    )
    placement = next(
        p for p in placements_repo.for_org(conn, org.id) if p.program_path
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        for name in ("Workers Compensation", "Employers Liability"):
            client.post(
                f"/accounts/{org.ref}/program/{placement.id}/lines",
                data={"name": name},
            )
        repeated = [
            row["name"] for row in sync.layer_details(conn, placement.id)
        ]
        assert repeated.count("To be placed") > 1, (
            "the fixture no longer produces a repeated layer name — this scan "
            "has nothing to catch; give it a shape that repeats one"
        )

        offenders = []
        checked = 0
        for row in sync.layer_details(conn, placement.id):
            page = client.get(
                f"/accounts/{org.ref}/program?layer={row['id']}"
            ).text
            checked += len(_select_labels(page))
            for field, dupes in _dupes(page).items():
                offenders.append(f"{field} on layer {row['id']} -> {dupes}")

    assert checked, "no selects found on the program tab — the scan is broken"
    assert not offenders, (
        "these selects ask a question with two identical answers — qualify the "
        f"ambiguous options with what tells them apart: {sorted(set(offenders))}"
    )


# --- and the offer the scan above structurally CANNOT see ---------------------
#
# A GATE IS ONLY AS GOOD AS WHERE IT LOOKS. The scan above walks
# `/accounts/{ref}/program` pages, so it found the "same policy as" collision
# and was blind to the identical one on the pipeline's bind offer — which is
# the WORSE of the two: there the write is addressed by id and the id is
# correct for whichever option is clicked, so a mis-click puts a real
# participation on the wrong line of coverage through sync.add_participant, in
# a revertible batch nobody knows to revert.
#
# This drives the real route: a market quoted on a linked placement, marked
# bound, which is what makes the app hand back the bind offer.


def test_the_pipeline_bind_offer_tells_two_to_be_placed_layers_apart(app):
    from bookkit import sync
    from bookkit.repo import orgs as orgs_repo
    from bookkit.repo import placements as placements_repo
    from bookkit.repo import submissions as submissions_repo

    conn = app.state.conn
    found = None
    for org in orgs_repo.list_orgs(conn, kind="client"):
        for placement in placements_repo.for_org(conn, org.id):
            if not placement.program_path:
                continue
            live = [
                sub
                for sub in submissions_repo.for_placement(conn, placement.id)
                if str(sub.status) == "quoted"
            ]
            if live:
                found = (org, placement, live[0])
                break
        if found:
            break
    assert found, "the seeded book has no quoted submission on a linked placement"
    org, placement, submission = found

    with TestClient(app, base_url="http://127.0.0.1") as client:
        for name in ("Workers Compensation", "Employers Liability"):
            client.post(
                f"/accounts/{org.ref}/program/{placement.id}/lines",
                data={"name": name},
            )
        repeated = [row["name"] for row in sync.layer_details(conn, placement.id)]
        assert repeated.count("To be placed") > 1, (
            "the fixture no longer produces a repeated layer name — this scan "
            "has nothing to catch"
        )

        # THE ANSWER IS ABOUT A LINE OF COVERAGE (2026-08-26): the Response
        # form writes a `market_response` now, so the placement has to be
        # marketing the line the answer names — the state the panel's
        # `+ line of coverage` control produces.
        from bookkit.repo import marketing as marketing_repo

        line_id = "general-liability"
        marketing_repo.set_placement_line(conn, placement.id, line_id)
        offer = client.post(
            f"/accounts/{org.ref}/pipeline/submissions/{submission.id}/response",
            data={
                "line_id": line_id,
                "status": "bound",
                "responded_on": "2026-08-24",
            },
        ).text

    selects = _select_labels(offer)
    assert "layer_id" in selects, (
        "the bind offer did not render — this test is asserting nothing "
        f"(fields seen: {sorted(selects)})"
    )
    assert not _dupes(offer), (
        "the bind offer asks which layer with two identical answers, and the "
        f"mis-click writes a real participation: {_dupes(offer)}"
    )
