"""Quick capture on the web (GET/POST /capture) — the highest-frequency CRM
write, and the seam it forced: attendee resolution moved from the TUI widget
into services.capture.resolve_attendees so the two surfaces cannot drift on
who a typed name resolves to.

Date expectations are computed with the ROUTE module's own `date` attribute —
tests/conftest.py freezes date.today() per module, and a fresh
`from datetime import date` here would compare a frozen answer against the
real clock (see tests/test_web_today.py's pattern note in the task brief).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bookkit.models import InteractionType
from bookkit.repo import batches as batches_repo
from bookkit.repo import contacts as contacts_repo
from bookkit.repo import interactions as interactions_repo
from bookkit.repo import orgs as orgs_repo
from bookkit.repo import tasks as tasks_repo
from bookkit.services import capture as capture_svc
from bookkit.web.app import create_app
from bookkit.web.routes import capture as capture_routes


@pytest.fixture
def app_and_conn(snapshot_db: Path):
    app = create_app(snapshot_db)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client, app.state.conn


def _org_with_contacts(conn: sqlite3.Connection):
    return next(
        org
        for org in orgs_repo.list_orgs(conn, kind="client")
        if len(contacts_repo.for_org(conn, org.id)) >= 1
    )


def _logged(conn: sqlite3.Connection, org, subject: str):
    found = [i for i in interactions_repo.for_org(conn, org.id) if i.subject == subject]
    assert found, f"nothing was logged for {subject!r} — the save was refused"
    return found[0]


def _not_logged(conn: sqlite3.Connection, org, subject: str) -> None:
    found = [i for i in interactions_repo.for_org(conn, org.id) if i.subject == subject]
    assert not found, f"{subject!r} was logged — the refusal wrote anyway"


# --- the form ----------------------------------------------------------------


def test_the_capture_page_renders_the_whole_form(app_and_conn) -> None:
    client, conn = app_and_conn
    page = client.get("/capture")
    assert page.status_code == 200
    for name in ("org_id", "type", "occurred", "subject", "who", "note"):
        assert f'name="{name}"' in page.text
    # every interaction type the TUI's select offers
    for t in InteractionType:
        assert f'value="{t.value}"' in page.text
    # the date pre-fills "today", like the TUI modal
    assert 'value="today"' in page.text
    # the account select lists the client book
    org = _org_with_contacts(conn)
    assert org.name in page.text


def test_org_query_param_preselects_the_account_and_shows_its_roster(
    app_and_conn,
) -> None:
    client, conn = app_and_conn
    org = _org_with_contacts(conn)
    person = contacts_repo.for_org(conn, org.id)[0]
    page = client.get(f"/capture?org={org.ref}")
    assert page.status_code == 200
    assert f'value="{org.id}" selected' in page.text
    # the roster hint — you cannot type a name you do not know — and the
    # datalist the vocabulary rule requires
    assert "on this account:" in page.text
    assert person.name in page.text
    assert 'list="l-who"' in page.text


def test_an_unknown_org_ref_renders_the_form_with_a_sentence_not_a_404(
    app_and_conn,
) -> None:
    client, _ = app_and_conn
    page = client.get("/capture?org=ACC-9999")
    assert page.status_code == 200
    assert "no account matches" in page.text


# --- refusals: HTTP 200, message in the page, EVERY field intact -------------


def test_refused_without_an_account_and_the_typed_text_survives(app_and_conn) -> None:
    client, conn = app_and_conn
    page = client.post(
        "/capture",
        data={
            "org_id": "",
            "type": "meeting",
            "occurred": "today",
            "subject": "Quarterly stewardship",
            "who": "somebody",
            "note": "Walked the risk register.",
        },
    )
    assert page.status_code == 200
    assert "pick an account first" in page.text
    # every field intact — the web's commit-in-place
    assert "Quarterly stewardship" in page.text
    assert "Walked the risk register." in page.text
    assert 'value="somebody"' in page.text
    assert 'value="meeting" selected' in page.text


def test_refused_with_nothing_to_save(app_and_conn) -> None:
    client, conn = app_and_conn
    org = _org_with_contacts(conn)
    page = client.post(
        "/capture",
        data={"org_id": org.id, "type": "note", "occurred": "today",
              "subject": "", "who": "", "note": ""},
    )
    assert page.status_code == 200
    assert "nothing to save" in page.text
    assert f'value="{org.id}" selected' in page.text


def test_an_ambiguous_date_is_refused_never_guessed(app_and_conn) -> None:
    """The TUI modal silently substitutes today for a date it cannot parse;
    the web refuses with forms.spec.date_refusal's sentence instead —
    CLAUDE.md's rule (ambiguous entry is refused) outranks mirroring the
    fallback."""
    client, conn = app_and_conn
    org = _org_with_contacts(conn)
    page = client.post(
        "/capture",
        data={"org_id": org.id, "type": "note", "occurred": "5",
              "subject": "Bare number date", "who": "", "note": "x"},
    )
    assert page.status_code == 200
    assert "is not a date" in page.text
    _not_logged(conn, org, "Bare number date")


def test_a_name_matching_nobody_refuses_the_save_and_keeps_the_note(
    app_and_conn,
) -> None:
    client, conn = app_and_conn
    org = _org_with_contacts(conn)
    page = client.post(
        "/capture",
        data={"org_id": org.id, "type": "note", "occurred": "today",
              "subject": "Typo attendee", "who": "Zzyzx Quux",
              "note": "The note must survive the refusal."},
    )
    assert page.status_code == 200
    assert "no contact on this account matches" in page.text
    assert "The note must survive the refusal." in page.text
    _not_logged(conn, org, "Typo attendee")


def test_a_name_two_people_answer_to_is_refused_rather_than_guessed(
    app_and_conn,
) -> None:
    client, conn = app_and_conn
    org = _org_with_contacts(conn)
    contacts_repo.create(conn, org.id, first_name="Michael", last_name="Brennan")
    contacts_repo.create(conn, org.id, first_name="Michelle", last_name="Brennan")
    page = client.post(
        "/capture",
        data={"org_id": org.id, "type": "call", "occurred": "today",
              "subject": "Ambiguous attendee", "who": "Michel Brennan",
              "note": ""},
    )
    assert page.status_code == 200
    assert "type more of the name" in page.text
    _not_logged(conn, org, "Ambiguous attendee")


# --- success -----------------------------------------------------------------


def test_success_logs_one_web_batch_and_lands_on_the_relationship_tab(
    app_and_conn,
) -> None:
    client, conn = app_and_conn
    org = _org_with_contacts(conn)
    person = contacts_repo.for_org(conn, org.id)[0]
    response = client.post(
        "/capture",
        data={"org_id": org.id, "type": "meeting", "occurred": "today",
              "subject": "Coverage review", "who": person.name,
              "note": "Discussed limits."},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/accounts/{org.ref}/relationship"

    entry = _logged(conn, org, "Coverage review")
    # the route module's own frozen clock, per the conftest pattern
    assert entry.occurred_on == capture_routes.date.today().isoformat()
    assert entry.body == "Discussed limits."
    assert [c.id for c in interactions_repo.attendees(conn, entry.id)] == [person.id]

    # ONE batch, the web's: interaction + attendee links one undo unit
    batch = batches_repo.most_recent(conn)
    assert batch is not None
    assert batch.source == "web"
    assert batch.tool == "log_interaction"
    assert batch.org_id == org.id
    assert batch.summary == "logged meeting — Coverage review"
    assert entry.id in {e.entity_id for e in batches_repo.events_for(conn, batch.id)}

    # the new interaction is visible where the redirect lands
    landed = client.get(response.headers["location"])
    assert "Coverage review" in landed.text


def test_a_missing_subject_falls_back_to_the_first_note_line(app_and_conn) -> None:
    client, conn = app_and_conn
    org = _org_with_contacts(conn)
    response = client.post(
        "/capture",
        data={"org_id": org.id, "type": "note", "occurred": "today",
              "subject": "", "who": "",
              "note": "Sent the binder across.\nMore detail below."},
        follow_redirects=False,
    )
    assert response.status_code == 303
    _logged(conn, org, "Sent the binder across.")


# --- the follow-up offer -----------------------------------------------------


def test_a_followup_phrase_offers_a_task_and_creates_nothing_yet(
    app_and_conn,
) -> None:
    client, conn = app_and_conn
    org = _org_with_contacts(conn)
    note = "Great discussion. Follow up friday."
    response = client.post(
        "/capture",
        data={"org_id": org.id, "type": "call", "occurred": "today",
              "subject": "Renewal kickoff", "who": "", "note": note},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert "Create follow-up task?" in response.text

    entry = _logged(conn, org, "Renewal kickoff")
    suggestion = capture_svc.suggest_task(f"Renewal kickoff {note}")
    assert suggestion is not None, "the cue phrase must produce a suggestion"
    # the editable title, prefilled the way the TUI's ConfirmTask prefills it
    assert f'value="{suggestion.phrase.capitalize()}"' in response.text
    assert suggestion.due_on.isoformat() in response.text
    # [Skip] is a link to the account's relationship tab — a GET, no write
    assert f'href="/accounts/{org.ref}/relationship"' in response.text
    # OFFERED, not created: no task exists yet
    assert not [
        t for t in tasks_repo.open_tasks(conn, org_id=org.id)
        if t.source_interaction_id == entry.id
    ]


def test_accepting_the_offer_creates_the_task_inside_a_task_add_batch(
    app_and_conn,
) -> None:
    client, conn = app_and_conn
    org = _org_with_contacts(conn)
    note = "Solid discussion. Follow up friday."
    offer = client.post(
        "/capture",
        data={"org_id": org.id, "type": "call", "occurred": "today",
              "subject": "Placement strategy", "who": "", "note": note},
    )
    assert offer.status_code == 200
    fields = dict(
        re.findall(r'name="(org_id|interaction_id|due_on|phrase)" value="([^"]*)"', offer.text)
    )
    title = re.search(r'name="title" value="([^"]*)"', offer.text)
    assert title is not None

    response = client.post(
        "/capture/task",
        data={**fields, "title": "Chase the loss runs"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/accounts/{org.ref}/relationship"

    entry = _logged(conn, org, "Placement strategy")
    created = [
        t for t in tasks_repo.open_tasks(conn, org_id=org.id)
        if t.source_interaction_id == entry.id
    ]
    assert len(created) == 1
    task = created[0]
    assert task.title == "Chase the loss runs"
    assert task.due_on == fields["due_on"]
    # INSIDE a batch — deliberately unlike the TUI's ConfirmTask, which
    # writes its task unbatched (a latent TUI bug, not a precedent)
    batch = batches_repo.most_recent(conn)
    assert batch is not None
    assert batch.source == "web"
    assert batch.tool == "task_add"
    assert task.id in {e.entity_id for e in batches_repo.events_for(conn, batch.id)}


def test_the_task_post_refuses_an_interaction_off_another_account(
    app_and_conn,
) -> None:
    """The compound-claim rule: org + interaction are TWO claims and both are
    checked, so a tampered hidden field cannot pin a task to the wrong file."""
    client, conn = app_and_conn
    clients = orgs_repo.list_orgs(conn, kind="client")
    org_a = next(o for o in clients if interactions_repo.for_org(conn, o.id))
    org_b = next(o for o in clients if o.id != org_a.id)
    entry = interactions_repo.for_org(conn, org_a.id)[0]
    response = client.post(
        "/capture/task",
        data={"org_id": org_b.id, "interaction_id": entry.id,
              "due_on": "2026-08-21", "phrase": "follow up", "title": "X"},
    )
    assert response.status_code == 404
    assert not [
        t for t in tasks_repo.open_tasks(conn, org_id=org_b.id)
        if t.source_interaction_id == entry.id
    ]


# --- the way in --------------------------------------------------------------


def test_every_page_carries_the_global_log_link(app_and_conn) -> None:
    client, conn = app_and_conn
    org = _org_with_contacts(conn)
    for path in ("/book", f"/accounts/{org.ref}/relationship", "/capture"):
        page = client.get(path)
        assert page.status_code == 200
        assert 'href="/capture"' in page.text, f"{path} lost the top bar's + Log"


def test_the_account_header_pill_is_a_real_link_again(app_and_conn) -> None:
    """Removed under D4 while inert; it returns as a live link, so it must
    never render aria-disabled again."""
    client, conn = app_and_conn
    org = _org_with_contacts(conn)
    page = client.get(f"/accounts/{org.ref}/relationship")
    assert f'href="/capture?org={org.ref}"' in page.text
    pill = re.search(r"<[^>]*>\+ Log interaction</", page.text)
    assert pill is not None
    assert "aria-disabled" not in pill.group(0)


# --- the seam: one attendee-resolution rule for both surfaces ----------------


def test_the_tui_widget_resolves_attendees_through_the_service() -> None:
    """A green suite proves nothing broke, not that the new path is taken
    (CLAUDE.md): assert the widget actually calls the shared rule and no
    longer carries a fuzzy loop of its own."""
    import inspect

    from bookkit.tui.widgets import quick_capture

    source = inspect.getsource(quick_capture)
    assert "capture.resolve_attendees(" in source
    assert "ATTENDEE_MATCH" not in source, "the threshold moved to services.capture"
    assert "ATTENDEE_MARGIN" not in source
    # the one WRatio left is the ACCOUNT picker's; the attendee loop is gone
    assert source.count("fuzz.WRatio") == 1
    assert "default_process" not in source


def _roster_org(conn: sqlite3.Connection):
    org = orgs_repo.create(conn, name="Meridian Foundry", kind="client")
    rosa = contacts_repo.create(conn, org.id, first_name="Rosa", last_name="Delgado")
    chen = contacts_repo.create(conn, org.id, first_name="Wei", last_name="Chen")
    return org, rosa, chen


def test_resolve_attendees_matches_case_and_partial_names(conn) -> None:
    org, rosa, chen = _roster_org(conn)
    ids, refusal = capture_svc.resolve_attendees(conn, org.id, "DELGADO, wei chen")
    assert refusal is None
    assert ids == [rosa.id, chen.id]


def test_resolve_attendees_refuses_a_typo_and_names_it(conn) -> None:
    org, _, _ = _roster_org(conn)
    ids, refusal = capture_svc.resolve_attendees(conn, org.id, "Zzyzx Quux")
    assert ids == []
    assert refusal is not None
    assert "'Zzyzx Quux'" in refusal
    assert "no contact on this account matches" in refusal


def test_resolve_attendees_refuses_an_ambiguous_name_naming_both(conn) -> None:
    org, _, _ = _roster_org(conn)
    contacts_repo.create(conn, org.id, first_name="Michael", last_name="Brennan")
    contacts_repo.create(conn, org.id, first_name="Michelle", last_name="Brennan")
    ids, refusal = capture_svc.resolve_attendees(conn, org.id, "Michel Brennan")
    assert ids == []
    assert refusal is not None
    assert "Michael Brennan" in refusal and "Michelle Brennan" in refusal
    assert "type more of the name" in refusal
