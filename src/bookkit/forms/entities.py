"""FormSpec builders for every creatable entity, plus the apply helpers the
screens call on save. Screens stay thin: push FormModal(spec, commit=...) so
the matching apply_* runs while the form is still open — a refused or failed
save keeps the form up for correction (the platform default since 2026-08-12)."""

from __future__ import annotations

import sqlite3
import textwrap
from datetime import date
from typing import Any

from ..models import (
    CONTACT_ROLES,
    MARKET_RESPONSE_STATUS_LABELS,
    NEED_STATUSES,
    PROJECT_STATUSES,
    RFI_ITEM_KINDS,
    RFI_ITEM_STATUSES,
    SUBJECTIVITY_OPEN_STATUS,
    SUBJECTIVITY_STATUSES,
    TEAM_ROLES,
    Appetite,
    AppetiteLevel,
    Contact,
    Interaction,
    InteractionType,
    MarketResponse,
    MarketType,
    Opportunity,
    Org,
    OrgKind,
    OrgStatus,
    Placement,
    PlacementStatus,
    Project,
    ProjectNeed,
    RfiItem,
    RfiRequest,
    Subjectivity,
    Submission,
    SubmissionStatus,
    Task,
    TeamAssignment,
    TeamMember,
)
from ..money import format_cents
from ..repo import (
    assignees,
    contacts,
    interactions,
    opportunities,
    orgs,
    placements,
    submissions,
    team,
    vocab,
)
from ..repo import lines as lines_repo
from ..repo import marketing as marketing_repo
from ..repo import projects as projects_repo
from ..repo import rfi as rfi_repo
from ..repo import tasks as tasks_repo
from ..services import consistency, marketing_entry
from .inline import MARKET_RESPONSE_FIELDS
from .spec import BatchSpec, Field, FormNote, FormSpec, dropped

# SELECT OPTIONS ARE READ FROM THE ENUM, NEVER RESTATED (DRY, CLAUDE.md).
# These five were hand-typed copies of StrEnums that models.py already
# defines and that every stored value is validated against — a second spelling
# of a vocabulary, in the one place a user picks from it. A copy that merely
# repeats is noise; a copy that DIFFERS is a picker offering a value the model
# refuses, or hiding one it accepts, and nothing would have caught either.
_STATUS = tuple((s.value, s.value) for s in OrgStatus)
_KINDS = tuple((k.value, k.value) for k in OrgKind)
_MARKET_TYPES = tuple((m.value, m.value) for m in MarketType)
_ROLES = tuple((r, r) for r in CONTACT_ROLES)
_PLACEMENT_STATUS = tuple((s.value, s.value) for s in PlacementStatus)
_PRIORITY = (("1 — high", "1"), ("2 — normal", "2"), ("3 — low", "3"))
_APPETITE = tuple((a.value, a.value) for a in AppetiteLevel)
# Every submission status EXCEPT 'out'. 'out' is the state a submission is
# already in when this form opens — it is not a legal OUTCOME to record, and
# the comment on response_form spells out what offering it would do (Select
# rejects a value missing from its options, so it would be an unfixable form).
_RESPONSE = tuple(
    (s.value, s.value) for s in SubmissionStatus if s is not SubmissionStatus.OUT
)


# --- org ----------------------------------------------------------------------


def org_form(
    existing: Org | None = None,
    default_kind: str = OrgKind.CLIENT.value,
    *,
    conn: sqlite3.Connection | None = None,
) -> FormSpec:
    profile_initial: dict[str, Any] = {}
    owner_sugg = tuple(vocab.owners(conn)) if conn else ()
    industry_sugg = tuple(vocab.industries(conn)) if conn else ()
    return FormSpec(
        "edit account" if existing else "new account",
        [
            # typed-every-time fields first; pre-defaulted selects after
            Field("name", "name", required=True),
            Field("owner", "owner", suggestions=owner_sugg),
            Field("industry", "industry", suggestions=industry_sugg),
            Field("kind", "kind", "select", _KINDS),
            Field("status", "status", "select", _STATUS),
            Field("naics", "naics", "naics"),
            Field("hq_city", "hq city"),
            Field("hq_country", "hq country"),
            Field("website", "website", "url"),
            Field("domain", "email domain", "domain"),
            Field("market_type", "market type (markets)", "select", _MARKET_TYPES,
                  optional_select=True),
            Field("am_best_rating", "AM Best rating (markets)", placeholder="A++ · A- · NR"),
            Field("notes", "notes", "textarea"),
        ],
        initial=(
            {**existing.model_dump(), **profile_initial}
            if existing
            else {
                "kind": default_kind,
                # a NEW client is a prospect; a new market is already in use.
                # Read from the enum like the options above, so a renamed
                # status cannot leave the default naming a value the picker
                # no longer offers — which Select refuses outright.
                "status": (
                    OrgStatus.PROSPECT.value
                    if default_kind == OrgKind.CLIENT.value
                    else OrgStatus.ACTIVE.value
                ),
            }
        ),
    )


def apply_org(
    conn: sqlite3.Connection, values: dict[str, Any], existing: Org | None = None
) -> Org:
    market_type = values.pop("market_type", None)
    rating = values.pop("am_best_rating", None)
    core = dropped(values)
    org = (
        orgs.update(conn, existing.id, **core) if existing else orgs.create(conn, **core)
    )
    if org.kind == OrgKind.MARKET and (market_type or rating):
        profile: dict[str, Any] = {}
        if market_type:
            profile["market_type"] = market_type
        if rating:
            profile["am_best_rating"] = rating
        orgs.set_market_profile(conn, org.id, **profile)
    return org


def org_form_initial_profile(conn: sqlite3.Connection, existing: Org) -> FormSpec:
    """org_form for an edit, with the market profile fields pre-filled."""
    spec = org_form(existing, conn=conn)
    profile = orgs.get_market_profile(conn, existing.id)
    if profile:
        spec.initial["market_type"] = profile.market_type
        spec.initial["am_best_rating"] = profile.am_best_rating
    return spec


# --- contact ------------------------------------------------------------------


def contact_form(existing: Contact | None = None) -> FormSpec:
    return FormSpec(
        "edit contact" if existing else "new contact",
        [
            # signature-block order: name → email → phone, then the rest
            Field("first_name", "first name", required=True),
            Field("last_name", "last name", required=True),
            Field("email", "email", "email"),
            Field("phone", "phone", "phone"),
            Field("mobile", "mobile", "phone"),
            Field("title", "title"),
            Field("role", "role", "select", _ROLES, optional_select=True),
            Field("linkedin", "linkedin", "linkedin"),
            Field("notes", "notes", "textarea"),
        ],
        initial=existing.model_dump() if existing else {},
    )


def apply_contact(
    conn: sqlite3.Connection,
    org_id: str,
    values: dict[str, Any],
    existing: Contact | None = None,
) -> Contact:
    core = dropped(values)
    if existing:
        return contacts.update(conn, existing.id, **core)
    return contacts.create(conn, org_id, **core)


# --- task ---------------------------------------------------------------------


def task_form(
    existing: Task | None = None,
    *,
    conn: sqlite3.Connection | None = None,
    default_org_id: str | None = None,
) -> FormSpec:
    """With `conn`, the form carries an account select so a task can attach
    to a client from anywhere (ctrl+t); `default_org_id` pre-selects the
    account you were looking at."""
    initial = existing.model_dump() if existing else {"priority": "2"}
    if existing:
        initial["priority"] = str(existing.priority)
    # WHOSE ACCOUNT THE PEOPLE COME FROM. An existing task's own account
    # wins over the screen's default: editing a task from a list that spans
    # accounts (the navigator's attention pane) must offer the contacts of
    # the account the TASK belongs to, not the one the cursor last sat on.
    people_org_id = (existing.org_id if existing else None) or default_org_id
    # `assignee` is not a Task column — it is one typed string that
    # repo.assignees turns into three (kind + id, or a freeform name). The
    # form pre-fills the QUALIFIED label, because what a form pre-fills has
    # to be a value its own resolver accepts back unchanged.
    assignee_pool = (
        tuple(c.label for c in assignees.candidates(conn, people_org_id))
        if conn else ()
    )
    if conn is not None and existing is not None:
        initial["assignee"] = assignees.label_of(conn, existing)
    # most tasks are title + due — the detail textarea goes last
    fields = [
        Field("title", "title", required=True),
        Field("description", "description", placeholder="one-line summary"),
        Field("category", "category",
              suggestions=tuple(vocab.task_categories(conn)) if conn else ()),
        Field("due_on", "due", "date"),
        Field("assignee", "assignee", suggestions=assignee_pool,
              placeholder="who is chasing this — a name, or anyone"),
        Field("priority", "priority", "select", _PRIORITY),
    ]
    if conn is not None:
        client_options = tuple(
            (org.name, org.id) for org in orgs.list_orgs(conn, kind=OrgKind.CLIENT.value)
        )
        if client_options:
            fields.append(
                Field("org_id", "account", "select", client_options, optional_select=True)
            )
            if default_org_id is not None:
                initial.setdefault("org_id", default_org_id)
    fields.append(Field("detail", "detail", "textarea"))
    return FormSpec(
        "edit task" if existing else "new task",
        fields,
        initial=initial,
    )


def apply_task(
    conn: sqlite3.Connection,
    values: dict[str, Any],
    org_id: str | None = None,
    existing: Task | None = None,
) -> Task:
    core = dropped(values)  # org_id present only when the form chose an account
    if "priority" in core:
        core["priority"] = int(core["priority"])
    # `assignee` never reaches the task table under that name: repo.assignees
    # owns the three columns it becomes, and is the only thing that writes
    # them. Popped BEFORE dropped()'s survivors are handed to the repo — and
    # popped from `values`, not `core`, so that clearing the field (which
    # dropped() strips as a None) still nulls what was there.
    typed = values.get("assignee") if "assignee" in values else None
    core.pop("assignee", None)
    people_org_id = (
        core.get("org_id") or (existing.org_id if existing else None) or org_id
    )
    if "assignee" in values:
        core.update(assignees.columns(conn, typed, org_id=people_org_id))
    if existing:
        return tasks_repo.update(conn, existing.id, **core)
    title = core.pop("title")
    core.setdefault("org_id", org_id)
    return tasks_repo.create(conn, title, **core)


# --- placement ----------------------------------------------------------------


def placement_form(
    existing: Placement | None = None, *, conn: sqlite3.Connection | None = None
) -> FormSpec:
    program_sugg = tuple(vocab.program_names(conn)) if conn else ()
    return FormSpec(
        # "program", not "placement", in anything a user reads (D3,
        # 2026-08-19) — placement stays the code/DB term.
        "edit program" if existing else "new program",
        [
            Field("program_name", "program name", required=True,
                  suggestions=program_sugg),
            Field("period_from", "period from", "date", required=True),
            Field("period_to", "period to (expiry)", "date", required=True),
            Field("status", "status", "select", _PLACEMENT_STATUS),
            Field("total_premium", "premium", "money"),
            Field("total_limit", "total limit", "money"),
            Field("commission_bps", "commission (bps)", "int",
                  placeholder="1250 = 12.5%"),
        ],
        initial=(
            existing.model_dump()
            if existing
            else {"status": PlacementStatus.PROSPECTIVE.value}
        ),
    )


def apply_placement(
    conn: sqlite3.Connection,
    values: dict[str, Any],
    org_id: str,
    existing: Placement | None = None,
) -> Placement:
    core = dropped(values)
    # THE ROW AS IT WILL BE, not the half that was typed: an edit that moves
    # only the expiry has to be compared against the inception already stored.
    # The rule exists twice already — towerkit's validator behind
    # sync.update_program for a linked program, imports/mappers/book.py for a
    # spreadsheet row — and was missing from exactly the door a human types
    # through, because placement_edit.split folds both dates into the book
    # half when there is no program_path and towerkit never sees them.
    consistency.check_placement_period(
        core.get("period_from") or (existing.period_from if existing else None),
        core.get("period_to") or (existing.period_to if existing else None),
    )
    if existing:
        return placements.update(conn, existing.id, **core)
    return placements.create(
        conn,
        org_id,
        core.pop("program_name"),
        core.pop("period_from"),
        core.pop("period_to"),
        **core,
    )


# --- opportunity --------------------------------------------------------------


def opportunity_form(
    existing: Opportunity | None = None, *, conn: sqlite3.Connection | None = None
) -> FormSpec:
    line_sugg = tuple(vocab.lines(conn)) if conn else ()
    return FormSpec(
        "edit opportunity" if existing else "new opportunity",
        [
            Field("title", "title", required=True),
            Field("lines", "lines (cyber, casualty…)", suggestions=line_sugg),
            Field("target_premium", "target premium", "money"),
            Field("target_effective", "target effective", "date"),
            Field("probability_pct", "probability %", "int", placeholder="0–100"),
            Field("source", "source (referral, rfp…)"),
            Field("incumbent_broker", "incumbent broker"),
            Field("competitor", "competitor"),
        ],
        initial=existing.model_dump() if existing else {"probability_pct": 50},
    )


def apply_opportunity(
    conn: sqlite3.Connection,
    values: dict[str, Any],
    org_id: str,
    existing: Opportunity | None = None,
) -> Opportunity:
    core = dropped(values)
    if existing:
        return opportunities.update(conn, existing.id, **core)
    title = core.pop("title")
    return opportunities.create(conn, org_id, title, **core)


# --- submission ---------------------------------------------------------------


def underwriter_options(conn: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
    """(label, contact id) for every active contact at a market.

    The label carries the market — "Dana Reeve — Travelers" — because five
    identical "Chen" rows is the exact complaint the AE review made about
    search, and a picker repeats it worse: you cannot even hover it.

    One helper, both the new-submission form and the response form, so the
    two can never offer different people."""
    return tuple(
        (f"{row['first_name']} {row['last_name']} — {row['org_name']}", row["id"])
        for row in contacts.at_market_orgs(conn)
    )


def submission_form(conn: sqlite3.Connection) -> FormSpec:
    markets = tuple(
        (o.name, o.id) for o in orgs.list_orgs(conn, kind=OrgKind.MARKET.value)
    )
    return FormSpec(
        "new submission",
        [
            Field("market_org_id", "market", "select", markets, required=True),
            # optional on purpose: you often send to a submissions inbox and
            # learn who picked it up a week later, and a required field here
            # would push people to pick the wrong name to get past it. The
            # response form asks again.
            Field(
                "underwriter_contact_id", "underwriter", "select",
                underwriter_options(conn), optional_select=True,
            ),
            Field("sent_on", "sent", "date", required=True),
            Field("notes", "notes", "textarea"),
        ],
        initial={"sent_on": "today"},
    )


def apply_submission(
    conn: sqlite3.Connection,
    values: dict[str, Any],
    placement_id: str | None = None,
    opportunity_id: str | None = None,
) -> Submission:
    core = dropped(values)
    return submissions.create(
        conn,
        core.pop("market_org_id"),
        core.pop("sent_on"),
        placement_id=placement_id,
        opportunity_id=opportunity_id,
        **core,
    )


_RESPONSE_CELLS: dict[str, Field] = {f.key: f for f in MARKET_RESPONSE_FIELDS}
"""The response's own fields, BORROWED rather than re-declared.

DRY at its plainest: `status` here and `status` on the Marketing panel are one
question with one vocabulary, one label and one parser. They were two — this
form offered the SUBMISSION statuses and the panel the response's — and the
two pickers answered the same question with different words.
"""

# What the Pipeline's Response form asks, in the order a quote letter is read.
# Every key is a `market_response` COLUMN NAME, so the parsed values pass
# straight to the writer with no translation table to drift.
_RESPONSE_KEYS: tuple[str, ...] = (
    "status", "responded_on", "quote_expires_on", "premium", "lim",
    "decline_reason",
)


def response_line_options(
    conn: sqlite3.Connection, existing: Submission
) -> tuple[tuple[str, str], ...]:
    """WHICH LINES OF COVERAGE THIS PACKAGE CAN BE ANSWERED ON.

    A market is approached ONCE with a whole package and answers LINE BY LINE
    (migration 015's header), so the answer being recorded has to say which
    line it is about. NEVER GUESSED, even where there is only one: a response
    silently filed against whichever line sorted first is a quote attributed
    to cover nobody quoted.

    The sources are different on purpose:

    * A PLACEMENT THAT HAS SAID WHAT IT IS MARKETING is answered on those
      lines (`placement_line`, plus any line a response already named — a line
      marketed without an expectation row is still being marketed, which is
      the same union `marketing_report.compose` builds its blocks from).
      Offering a line outside that set would open a block the placement never
      said it was placing, and the Marketing panel has its own control for
      that, behind a near-match guard this form has no business repeating.
    * A PLACEMENT THAT HAS SAID NOTHING gets the book's living vocabulary, and
      recording the answer starts marketing the line picked (`apply_response`
      → `marketing_entry.start_marketing`, in the same batch). NOT a
      refinement: the fallback IS the ordinary case. Not one `placement_line`
      row exists on the seeded book, so the declared-set rule alone refused
      every one of the forty submissions in it — a capability that worked
      before the responses moved onto their own rows, refused on the daily
      driver with a sentence that also contradicted the four live markets
      printed above it (2026-08-26). A broker saying "this quote is for the
      GL" is declaring the line, which is the same move the panel's own assign
      control makes from the same vocabulary (`assign_line_options`).
    * AN OPPORTUNITY has declared nothing and has nowhere to declare it —
      there is no `placement_line` table for it and no marketing panel — so
      the book's living vocabulary is the only knowable set.

    Constrained either way: every option is storable, and the wide list
    narrows to what the placement is marketing the moment it is marketing
    anything. Empty now means the BOOK carries no line of coverage at all, and
    the caller must say so in words: an empty picker is a form nobody can save.
    """
    named = {line.id: line.name for line in lines_repo.all_lines(conn)}
    answered = [r.line_id for r in marketing_repo.responses_for_submission(conn, existing.id)]
    if existing.placement_id is None:
        offered = list(named)
    else:
        offered = [
            pl.line_id
            for pl in marketing_repo.placement_lines(conn, existing.placement_id)
        ]
        if not offered and not answered:
            offered = list(named)
    ordered: list[str] = []
    for line_id in [*offered, *answered]:
        if line_id not in ordered:
            ordered.append(line_id)
    def label(line_id: str) -> str:
        # A RETIRED LINE STILL HAS A NAME. `all_lines` is the living
        # vocabulary; a line this package was already answered on may have
        # been retired since, and the option has to be nameable or the row
        # becomes uneditable from here. Falling back to the id keeps the
        # option pickable rather than crashing the form on a row a foreign key
        # says cannot exist.
        retired = lines_repo.get_any(conn, line_id)
        return named.get(line_id) or (retired.name if retired else line_id)

    return tuple((label(line_id), line_id) for line_id in ordered)


# What a line already answered is called in the picker, so choosing it cannot
# read as recording something new. One string, because the label and the note
# below have to agree about what saving over an answered line DOES.
_CORRECTS = "answered — saving corrects it"


def response_already_said(
    conn: sqlite3.Connection, existing: Submission
) -> tuple[dict[str, str], FormNote | None]:
    """({line_id: one line of prose}, the note the form shows above its fields)
    — WHAT THIS PACKAGE HAS ALREADY ANSWERED.

    THE FORM RE-OPENED COMPLETELY BLANK, and that is what made one wrong pick
    expensive. Nothing on it named the lines this package had answered, no
    option was selected, no value was shown, and `line_id` carried no initial —
    so re-picking was compulsory on every re-open, and picking Property while
    correcting a Chubb GL premium wrote a SECOND response: the client's
    workbook then said Chubb quoted both lines at $1.2M, which is impossible on
    a form that overwrites (r6 blocker 3, 2026-08-26). What was missing was not
    a guard. It was the state.

    READ-ONLY PROSE, never a prefill (`FormNote` says why the distinction is
    the point): every amount below is shown as text and typed into nothing, so
    the form still asks for each figure empty. data-entry-integrity §8 refuses
    a pre-filled figure BECAUSE people wave prefills through; text that cannot
    be saved has no such failure mode, and hiding what the book already holds
    is not what that rule asks for.

    A RETIRED LINE STILL HAS A NAME (`lines_repo.get_any`, the same reading
    `response_line_options` takes): a package answered on a line retired since
    must still be describable here, or the one screen that could correct it
    prints an id.
    """
    responses = marketing_repo.responses_for_submission(conn, existing.id)
    if not responses:
        return {}, None
    named = {line.id: line.name for line in lines_repo.all_lines(conn)}
    said: dict[str, str] = {}
    for r in responses:
        retired = lines_repo.get_any(conn, r.line_id)
        line_name = named.get(r.line_id) or (retired.name if retired else r.line_id)
        parts = [MARKET_RESPONSE_STATUS_LABELS.get(r.status, r.status)]
        if r.responded_on:
            parts.append(f"replied {r.responded_on}")
        # NULL IS NOT ZERO and is not printed as one: a figure nobody has
        # given says nothing here rather than "$0", which is a price.
        if r.premium is not None:
            parts.append(f"premium {format_cents(r.premium)}")
        if r.lim is not None:
            parts.append(f"limit {format_cents(r.lim)}")
        if r.quote_expires_on:
            parts.append(f"expires {r.quote_expires_on}")
        # A SECOND ANSWER ON ONE LINE is a real row shape (two bands of one
        # tower) and `apply_response` refuses to guess between them. Joining
        # rather than overwriting keeps the note honest about that.
        line = f"{line_name} — {' · '.join(parts)}"
        said[r.line_id] = f"{said[r.line_id]}; {line}" if r.line_id in said else line
    return said, FormNote(
        "What this package has already answered",
        tuple(said.values()),
    )


def response_form(existing: Submission, conn: sqlite3.Connection) -> FormSpec:
    """What a market said, recorded where the Pipeline shows it — onto a
    `market_response` row, which is the ONE home for it.

    IT USED TO WRITE THE SUBMISSION'S OWN COLUMNS, and that was the defect:
    `quoted_premium`, `quoted_limit`, `response_on`, `quote_expires_on` and
    `decline_reason` had a second home on `market_response`, so a premium
    entered here was invisible to the Marketing panel and one entered on both
    disagreed four inches apart with nothing saying the other existed (Grant,
    2026-08-26). The submission is now DERIVED from its rows
    (`repo.marketing.roll_up_submission`), exactly as its status already was,
    and the columns are a cache of them.

    NOTHING IS PRE-FILLED, and that is the rule rather than an omission: every
    figure on this form comes off a quote letter, and people do not check
    prefills (data-entry-integrity §8). What is left blank is not written, so
    re-opening the form to correct one figure cannot silently restate the
    others — and `line of coverage` carries no initial either, because a
    pre-selected line is how an answer about the Auto lands on the GL.

    `underwriter_contact_id` and `notes` stay the SUBMISSION's: they are facts
    about the package and about who handled it, not about what one line was
    quoted at, so they are not a second copy of anything.

    WHAT THE MOVE DROPPED, named here so it is not discovered as a mystery:
    `withdrawn`. The outcome picker used to offer the SUBMISSION statuses and
    was the only control on any surface that could withdraw a package; it now
    offers the market-response vocabulary, which has no such word — and
    rightly, because withdrawing is a decision about the submission (we pulled
    it) rather than a summary of what a market said, which is exactly why
    `roll_up_submission` never writes it and never writes over it. It has a
    control of its own now, on the submission's own row on this tab, and
    `submission_withdraw` / `submission_reinstate` on MCP.

    AND WHAT IT SHOWS, which is the other half of "nothing is pre-filled".
    Re-opened, this form used to be BLANK on a package two markets had already
    answered — nothing naming the lines, no option selected, and `line_id`
    carrying no initial, so re-picking was compulsory every time and one wrong
    pick wrote a SECOND response rather than correcting the first. It now shows
    what the package has answered (`response_already_said`, read-only prose,
    never an input), the picker says which lines saving would CORRECT, and
    where exactly one line has been answered that line arrives SELECTED — a
    default the reader can see, on the choice whose default is the safe one,
    because correcting the only answer this package has is what re-opening this
    form nearly always means. Every FIGURE is still empty, which is the rule
    data-entry-integrity §8 actually states.
    """
    said, note = response_already_said(conn, existing)
    options = tuple(
        (f"{label} ({_CORRECTS})" if line_id in said else label, line_id)
        for label, line_id in response_line_options(conn, existing)
    )
    # ONE ANSWERED LINE, AND ONLY ONE. Two answered lines cannot be guessed
    # between, and none means there is nothing to correct — in both cases the
    # question is genuinely open and the blank option is the honest answer.
    initial = {"line_id": next(iter(said))} if len(said) == 1 else {}
    return FormSpec(
        "record market response",
        [
            Field(
                "line_id", "line of coverage", "select", options, required=True,
            ),
            *(_RESPONSE_CELLS[key] for key in _RESPONSE_KEYS),
            # asked here as well as on the submission because this is usually
            # the first moment you know it: the answer arrives from a person,
            # not from the inbox you sent it to.
            Field(
                "underwriter_contact_id", "underwriter", "select",
                underwriter_options(conn), optional_select=True,
            ),
            Field("notes", "notes about the package", "textarea"),
        ],
        initial=initial,
        note=note,
    )


def response_batch(conn: sqlite3.Connection, existing: Submission) -> BatchSpec:
    """The undo unit this save belongs to, and THE SENTENCE THE CHANGE LIST
    PRINTS FOR IT.

    EVERY OTHER WRITE ON THIS FEATURE NAMES ITS SUBJECT — "started marketing
    General Liability", "set Premium on Travelers", "line of coverage for
    Chubb" — and this one did not. Recording Chubb on the GL and then filing a
    second answer against Property by mistake left two entries in the account's
    Recent changes rail both reading `record market response`, same market,
    same minute: nothing on the one surface that can undo the mistake said
    which of them to undo (r6 blocker 3, 2026-08-26).

    THIS IS THE WAY BACK, and it is deliberately the way back rather than a
    delete. The erroneous act is not "a response exists" — it is "we started
    marketing Property AND recorded an answer on it", both in one batch. A
    delete on the response row would reverse half of it and leave the placement
    still declaring it markets Property; Revert reverses the act. It also
    honours the decision this book already recorded against a market_response
    delete verb (`mcpparity`: "a market we approached and then removed is
    history being rewritten"), which a new delete control would have quietly
    reversed. What was missing was never the control — the account's Recent
    changes rail sits on this very tab — it was the name.

    THE TOOL SLUG IS UNCHANGED (`record_market_response`, what
    `BatchSpec.for_title` derives from this form's title on the retired TUI):
    the changes list GROUPS by tool, and correcting a quote is the same control
    as recording one. What differs is what happened, which is the sentence's
    job — and it can only be known once the line is picked, which is why this
    is computed from the posted values rather than from the title.
    """
    named = orgs.names_for_any(conn, {existing.market_org_id or ""})
    market = named.get(existing.market_org_id or "", "a market")

    def sentence(posted: dict[str, Any]) -> str:
        line_id = str(posted.get("line_id") or "")
        if not line_id:
            # apply_response is about to refuse and nothing will be written;
            # the batch still needs a sentence, and a guessed line is worse
            # than none.
            return f"recorded what {market} said"
        line = lines_repo.get_any(conn, line_id)
        answered = any(
            r.line_id == line_id
            for r in marketing_repo.responses_for_submission(conn, existing.id)
        )
        verb = "corrected" if answered else "recorded"
        return f"{verb} what {market} said about {line.name if line else line_id}"

    return BatchSpec(tool="record_market_response", summary=sentence)


# The two fields on that form that belong to the SUBMISSION rather than to the
# line's answer. Named once, so the split cannot be spelled differently in the
# two places it is made.
_SUBMISSION_OWNED: tuple[str, ...] = ("underwriter_contact_id", "notes")


def _who_was_asked(
    conn: sqlite3.Connection, submission: Submission
) -> dict[str, str | None]:
    """WHOSE PAPER, OR WHO CARRIED THE PACKAGE — `marketing_entry`'s rule, read
    from where it lives.

    It was written here, and then a second writer needed it: `assign_line`
    creates the first response on a line for a package that was never given
    one, and the question "was this addressed to the carrier or to the
    wholesaler" is exactly the same question with exactly the same answer. A
    rule stated beside one of two writers is the rule the other writes past
    (CLAUDE.md, DRY), and the way this one would have differed is a wholesaler
    landing in the Market column of a client's workbook as the carrier.
    """
    return marketing_entry.who_was_asked(conn, submission)


def _carried_here(
    submission: Submission,
    already: list[MarketResponse],
    typed: dict[str, Any],
) -> dict[str, Any]:
    """The submission's own figures, carried onto the FIRST response this
    package gets — and nothing carried onto any later one.

    THE SAME ACT THROUGH TWO DOORS MUST NOT HAVE TWO OUTCOMES. The panel's
    `assign a line` and this form both turn "a package that recorded a $1.4M
    quote" into "a row that states it", and only one of them was carrying the
    figures: recording a corrected reply date here made the rows the authority
    for all six columns while the row said nothing about a premium, so a
    $1.4M quote and its $10M limit both went to NULL — on the daily driver's
    Response button, on 30 of the 40 seeded submissions and on every one of
    Grant's, with nothing on the form saying so (r6 A/B, 2026-08-26). The rule
    itself is `marketing_entry.carried_figures`, stated once beside the door
    that already held it.

    ONLY WHILE THIS PACKAGE HAS NO ROWS AT ALL. Once one response exists the
    submission's columns ARE a cache of the rows (`roll_up_submission`), and
    copying a cache forward onto a second row would re-open the second home the
    roll-up was built to close — it would put the GL's premium on the Property
    row. That is the same precondition `assign_line` refuses outside of, read
    from the other side.

    WHAT THE BROKER TYPED WINS, always: `typed` is what the market has just
    said about this line, and a stored figure written over the top of it would
    be this function inventing a disagreement.
    """
    if already:
        return {}
    return marketing_entry.carried_figures(submission, restated=typed)


def apply_response(
    conn: sqlite3.Connection, submission_id: str, values: dict[str, Any]
) -> Submission:
    """Create or correct the ONE response this package has on that line, and
    let the submission be recomputed from its rows.

    `services.marketing_entry.responded` is the home an EDIT goes through —
    the same one the Marketing panel's cells and MCP's `market_responded`
    use — so a date that witnesses an act is judged against a today here too.
    A CREATE goes to `repo.marketing.create_response`, which owns the reply
    guard, the expiry guard and the roll-up.

    The market is the SUBMISSION's, never asked again: the package went to
    that market and this is its answer. Re-scoping an approach onto a
    different carrier is not a correction (CLAUDE.md's team-assignment rule),
    which is why the panel's cells leave the carrier alone as well.

    A FIRST ANSWER ON A LINE THE PLACEMENT HAD NOT DECLARED DECLARES IT
    (`marketing_entry.start_marketing`). The line is still ASKED and never
    guessed — `response_line_options` says where the offered set comes from —
    but a broker who has just said which line a $1.4M quote is for has told
    the placement what it is marketing, and making them go and say it again on
    another tab before the answer can be recorded is how forty live
    submissions ended up with no way to record one at all.

    Two refusals, and each names a fix that exists. There is nothing here to
    guess with.
    """
    core = dropped(values)
    line_id = core.pop("line_id", None)
    sub_fields = {k: core.pop(k) for k in _SUBMISSION_OWNED if k in core}
    if not line_id:
        raise ValueError(
            "a market answers a LINE OF COVERAGE, not a package — pick the one "
            "this answer is about. If the line is not on the list, open it "
            "under Marketing on the Program tab first."
        )
    submission = submissions.get(conn, submission_id)
    already = marketing_repo.responses_for_submission(conn, submission_id)
    on_this_line = [r for r in already if r.line_id == line_id]
    if len(on_this_line) > 1:
        # A TOWER MARKETED AT TWO ATTACHMENTS. The same carrier answering one
        # line twice is a real row shape (`market_response.attach`), and this
        # form has no attachment field to tell them apart — so it refuses
        # rather than picking one, and names the surface where each is edited
        # where it sits.
        raise ValueError(
            "this package has more than one answer recorded on that line of "
            "coverage — they are different bands of the same tower and this "
            "form cannot say which you mean. Correct them where they sit, "
            "under Marketing on the Program tab."
        )
    if core:
        if on_this_line:
            marketing_entry.responded(conn, on_this_line[0].id, core)
        else:
            # ANSWERING A LINE IS MARKETING IT, and the declaration is part of
            # the same act — one batch, reverted as one thing.
            marketing_entry.start_marketing(conn, submission.placement_id, line_id)
            marketing_repo.create_response(
                conn, submission_id, line_id,
                **_who_was_asked(conn, submission),
                **_carried_here(submission, already, core),
                **core,
            )
    if sub_fields:
        submissions.update(conn, submission_id, **sub_fields)
    # The rolled-up parent: status, premium, limit, response date, expiry and
    # decline reason all recomputed from the rows by the writers above.
    return submissions.get(conn, submission_id)


# --- subjectivities -----------------------------------------------------------


_SUBJECTIVITY_STATUS = tuple((s, s) for s in SUBJECTIVITY_STATUSES)
# The one status that OWNS satisfied_on. 'waived' settles a subjectivity
# without satisfying it, so it carries no satisfied date — the same shape
# rfi_item has, where 'waived' carries no received_on.
SUBJECTIVITY_MET_STATUS = "met"


def subjectivity_form(existing: Subjectivity | None = None) -> FormSpec:
    """One condition a market wants cleared before its quote is bindable.

    `status` IS on this form, unlike rfi_item's — there is no `d`-style
    mark-met action on any surface, so leaving it off would make a
    subjectivity impossible to settle. It stays one writer action either
    way: FormModal batches the whole save."""
    return FormSpec(
        "edit subjectivity" if existing else "new subjectivity",
        [
            Field(
                "description", "subjectivity", required=True,
                placeholder="signed application · loss runs to 8/1 · sprinkler cert",
            ),
            Field("due_on", "due", "date"),
            Field(
                "status", "status", "select", _SUBJECTIVITY_STATUS,
                required=True,
            ),
            Field("satisfied_on", "satisfied on", "date"),
            Field("notes", "notes", "textarea"),
        ],
        initial=(
            existing.model_dump()
            if existing
            else {"status": SUBJECTIVITY_OPEN_STATUS}
        ),
    )


def apply_subjectivity(
    conn: sqlite3.Connection,
    values: dict[str, Any],
    submission_id: str,
    existing: Subjectivity | None = None,
) -> Subjectivity:
    core = dropped(values)
    # status OWNS satisfied_on, exactly as it owns received_on on an rfi_item
    # (apply_rfi_item, below, calls the same rule). This form is the only door
    # onto a subjectivity on either surface — there is no `d`-style mark-met
    # key — so without it 'met' with no date and 'outstanding' still carrying a
    # satisfied date both saved, and the datasheet printed a settled date
    # beside a row it also printed as still open.
    status = core.get("status") or (
        existing.status if existing else SUBJECTIVITY_OPEN_STATUS
    )
    core["satisfied_on"] = consistency.settlement_date(
        status,
        values.get("satisfied_on"),
        existing.satisfied_on if existing else None,
        settled_status=SUBJECTIVITY_MET_STATUS,
        date_label="satisfied on",
        today=date.today().isoformat(),
    )
    # NB: due_on and satisfied_on are deliberately NOT compared. Clearing a
    # subjectivity late is the normal case, not an error, and refusing it
    # would make the tool unusable on exactly the accounts it exists for.
    if existing:
        return submissions.update_subjectivity(conn, existing.id, **core)
    return submissions.add_subjectivity(
        conn, submission_id, core.pop("description"), **core
    )


# --- team ---------------------------------------------------------------------

NEW_MEMBER = "__new__"


def member_form(
    existing: TeamMember | None = None, *, conn: sqlite3.Connection | None = None
) -> FormSpec:
    specialty_sugg = tuple(vocab.specialties(conn)) if conn else ()
    line_sugg = tuple(vocab.lines(conn)) if conn else ()
    return FormSpec(
        "edit team member" if existing else "new team member",
        [
            Field("name", "name", required=True),
            Field("title", "title"),
            Field("specialty", "specialty / lines",
                  placeholder="cyber, tech E&O, property",
                  suggestions=specialty_sugg or line_sugg),
            Field("email", "email", "email"),
            Field("phone", "phone", "phone"),
            Field("notes", "notes", "textarea"),
        ],
        initial=existing.model_dump() if existing else {},
    )


def assignment_form(
    member_options: tuple[tuple[str, str], ...] = (),
    *,
    title: str = "assign team member",
    conn: sqlite3.Connection | None = None,
    existing: TeamAssignment | None = None,
) -> FormSpec:
    """With member_options, the form picks WHO; without, the caller already
    knows the member (the Team screen's assign-to-account flow, and every
    edit — you correct an assignment, you do not re-pick its subject).

    `existing` turns this into the edit form. Note what it still does NOT
    offer: the client and the placement. One colleague can hold several
    assignments on one account — a row per line of coverage — so an edit has to
    stay on the row it opened, and moving someone between clients is
    unassign + assign, kept deliberately separate."""
    line_sugg = tuple(vocab.lines(conn)) if conn else ()
    fields = []
    if member_options:
        fields.append(
            Field(
                "team_member_id", "who", "select",
                (*member_options, ("+ new team member…", NEW_MEMBER)),
                required=True,
            )
        )
    fields.extend(
        [
            Field("role", "role", "select", tuple((r, r) for r in TEAM_ROLES),
                  optional_select=True),
            Field("lines", "lines they're placing", placeholder="cyber, D&O",
                  suggestions=line_sugg),
            Field("notes", "notes", "textarea"),
        ]
    )
    return FormSpec(
        title, fields,
        initial=existing.model_dump() if existing else {},
    )


def apply_assignment(
    conn: sqlite3.Connection, values: dict[str, Any], existing: TeamAssignment
) -> TeamAssignment:
    """Role/lines/notes only — `dropped` cannot introduce a scope key here
    because the form never renders one."""
    return team.update_assignment(conn, existing.id, **dropped(values))


# --- document -----------------------------------------------------------------


def document_form() -> FormSpec:
    return FormSpec(
        "add document",
        [
            Field("title", "title", required=True),
            Field("kind", "kind (policy, submission, sov…)"),
            Field("path", "path", required=True),
        ],
    )


# --- appetite -----------------------------------------------------------------


def appetite_form(
    existing: Appetite | None = None, *, conn: sqlite3.Connection | None = None
) -> FormSpec:
    line_sugg = tuple(vocab.lines(conn)) if conn else ()
    return FormSpec(
        "edit appetite" if existing else "add appetite",
        [
            Field("line", "line", required=True, suggestions=line_sugg),
            Field("appetite", "appetite", "select", _APPETITE, required=True),
            Field("class_of_business", "class of business"),
            Field("min_premium", "min premium", "money"),
            Field("max_limit", "max limit", "money"),
            Field("territories", "territories"),
            Field("notes", "notes", "textarea"),
        ],
        initial=existing.model_dump() if existing else {},
    )


# --- project --------------------------------------------------------------------


def project_form(existing: Project | None = None) -> FormSpec:
    initial = existing.model_dump() if existing else {"status": "planned"}
    return FormSpec(
        "edit project" if existing else "new project",
        [
            Field("name", "name", required=True, placeholder="HQ Tower Build"),
            Field("site", "site / location"),
            Field("status", "status", "select", tuple((s, s) for s in PROJECT_STATUSES)),
            Field("start_on", "start", "date"),
            Field("end_on", "end", "date"),
            Field("description", "description", "textarea"),
        ],
        initial=initial,
    )


def apply_project(
    conn: sqlite3.Connection,
    values: dict[str, Any],
    org_id: str,
    existing: Project | None = None,
) -> Project:
    core = dropped(values)
    # This pair leaves the building: services/export_open_items.py prints
    # "HQ Tower (start → end)" into the CLIENT-FACING workbook, so a reversed
    # range is a document sent out with nonsense on it, not an internal
    # untidiness. Compared as the row will be — an edit that moves only the
    # end date is checked against the stored start.
    consistency.check_project_dates(
        core.get("start_on") or (existing.start_on if existing else None),
        core.get("end_on") or (existing.end_on if existing else None),
    )
    if existing:
        return projects_repo.update_project(conn, existing.id, **core)
    name = core.pop("name")
    return projects_repo.create_project(conn, org_id, name, **core)


def need_form(
    existing: ProjectNeed | None = None, *, conn: sqlite3.Connection | None = None
) -> FormSpec:
    line_sugg = tuple(vocab.lines(conn)) if conn else ()
    initial = existing.model_dump() if existing else {"status": "identified"}
    return FormSpec(
        "edit insurance need" if existing else "new insurance need",
        [
            Field("line", "line of coverage", required=True,
                  placeholder="Builder's Risk", suggestions=line_sugg),
            Field("needed_by", "insurance needed by", "date", required=True),
            Field("limit_cents", "limit", "money"),
            Field("premium_indication_cents", "premium indication", "money"),
            Field("status", "status", "select", tuple((s, s) for s in NEED_STATUSES)),
            Field("notes", "notes", "textarea"),
        ],
        initial=initial,
    )


def apply_need(
    conn: sqlite3.Connection,
    values: dict[str, Any],
    project_id: str,
    existing: ProjectNeed | None = None,
) -> ProjectNeed:
    core = dropped(values)
    if existing:
        return projects_repo.update_need(conn, existing.id, **core)
    line = core.pop("line")
    needed_by = core.pop("needed_by")
    return projects_repo.add_need(conn, project_id, line, needed_by, **core)


def request_form(
    existing: RfiRequest | None = None,
    *,
    conn: sqlite3.Connection | None = None,
    org_id: str | None = None,
) -> FormSpec:
    markets: tuple[tuple[str, str], ...] = ()
    if conn is not None:
        markets = tuple(
            (o.name, o.id) for o in orgs.list_orgs(conn, kind=OrgKind.MARKET.value)
        )
    # the scope link lives on the REQUEST and the items inherit it, so it is
    # set here or nowhere; org_id is what makes the client's own placements
    # and projects offerable
    placement_opts: tuple[tuple[str, str], ...] = ()
    project_opts: tuple[tuple[str, str], ...] = ()
    if conn is not None and org_id is not None:
        placement_opts = tuple(
            (f"{p.ref} — {p.program_name}", p.id) for p in placements.for_org(conn, org_id)
        )
        project_opts = tuple(
            (p.name, p.id) for p in projects_repo.projects_for_org(conn, org_id)
        )
    initial = (
        existing.model_dump()
        if existing
        else {"requested_on": date.today().isoformat()}
    )
    # a market merge soft-deletes the loser but the request keeps its FK;
    # handing Select a value its options no longer hold raises
    # InvalidSelectValueError and takes the app down on `e`. Same for a
    # soft-deleted placement or project — but only where the options were
    # actually built: without org_id those tuples are empty and the guard
    # would blank a perfectly live scope instead of a dead one.
    if existing:
        guarded: list[tuple[str, tuple[tuple[str, str], ...]]] = [
            ("market_org_id", markets)
        ]
        if org_id is not None:
            guarded += [
                ("placement_id", placement_opts),
                ("project_id", project_opts),
            ]
        for key, options in guarded:
            if initial.get(key) not in {v for _, v in options}:
                initial = {**initial, key: None}
    return FormSpec(
        "edit information request" if existing else "new information request",
        [
            Field("title", "request", required=True,
                  placeholder="Sompo — property questions"),
            Field("requested_on", "asked on", "date", required=True),
            Field("due_on", "response due", "date"),
            Field("market_org_id", "asked by", "select", markets,
                  optional_select=True),
            Field("placement_id", "about placement", "select", placement_opts,
                  optional_select=True),
            Field("project_id", "about project", "select", project_opts,
                  optional_select=True),
            # withdrawal lives here, not on a key: `d` already means "done"
            # app-wide. Blank = live; a date = withdrawn.
            Field("cancelled_at", "cancelled on", "date"),
            Field("notes", "notes", "textarea"),
        ],
        initial=initial,
    )


def apply_request(
    conn: sqlite3.Connection,
    values: dict[str, Any],
    org_id: str,
    existing: RfiRequest | None = None,
) -> RfiRequest:
    core = dropped(values)
    # dropped() strips None so a blank optional never clobbers on edit — but
    # blanking "cancelled on" is the ONLY way back from a mis-cancelled
    # request, so that one blank has to be written through
    if existing and existing.cancelled_at and not values.get("cancelled_at"):
        core["cancelled_at"] = None
    # same for the scope links: switching a request from a placement to a
    # project means blanking one of them, and a blank that never lands leaves
    # the old link in the row alongside the new one
    if existing:
        if existing.placement_id and not values.get("placement_id"):
            core["placement_id"] = None
        if existing.project_id and not values.get("project_id"):
            core["project_id"] = None
    # the DB CHECK enforces this too, but only as an opaque IntegrityError;
    # FormModal turns a raise into an in-place refusal with the input intact.
    # The row as it WILL be is what the CHECK sees, so that is what to test
    scope = {
        key: core[key] if key in core else (getattr(existing, key) if existing else None)
        for key in ("placement_id", "project_id")
    }
    if scope["placement_id"] and scope["project_id"]:
        raise ValueError("a request is about a placement OR a project, not both")
    # A response due before the request was asked opens life overdue: the
    # chase queue reads `item.due_on or request.due_on` against today, so the
    # queue that exists to say what to chase starts with a row nobody can act
    # on. Equal dates are legal — "asked this morning, need it today".
    consistency.check_request_dates(
        core.get("requested_on") or (existing.requested_on if existing else None),
        core.get("due_on") or (existing.due_on if existing else None),
    )
    if existing:
        return rfi_repo.update_request(conn, existing.id, **core)
    title = core.pop("title")
    requested_on = core.pop("requested_on")
    return rfi_repo.create_request(conn, org_id, title, requested_on, **core)


def rfi_item_form(
    existing: RfiItem | None = None, *, conn: sqlite3.Connection | None = None
) -> FormSpec:
    category_sugg = tuple(vocab.rfi_categories(conn)) if conn else ()
    initial = (
        existing.model_dump()
        if existing
        else {"kind": "question", "status": "outstanding"}
    )
    return FormSpec(
        "edit item" if existing else "new item",
        [
            Field("prompt", "item", required=True,
                  placeholder="loss runs 2021-2025"),
            Field("kind", "type", "select",
                  tuple((k, k) for k in RFI_ITEM_KINDS)),
            Field("category", "group", suggestions=category_sugg,
                  placeholder="Financials"),
            Field("due_on", "needed by", "date"),
            Field("detail", "detail", "textarea"),
            Field("status", "status", "select",
                  tuple((s, s) for s in RFI_ITEM_STATUSES)),
            Field("received_on", "received on", "date"),
            Field("response", "response", "textarea"),
        ],
        initial=initial,
    )


def rfi_answer_form(item: RfiItem) -> FormSpec:
    """The answer alone, in a box with room in it.

    The Response cell edits in place on the row, which is the right shape for
    "confirmed, $4.2M" and the wrong one for three sentences about which
    subsidiary the payroll sits in (Grant, 2026-08-19). Same field, same
    column, roomier door.

    ONE field on purpose. Status is not here and must not be: an answer is
    frequently a note rather than a delivery, and deciding on the operator's
    behalf that the item is now satisfied is the failure that empties a chase
    list nobody has actually cleared. Marking received stays a deliberate
    press on the row."""
    # The title becomes the undo unit's sentence (BatchSpec.for_title), so it
    # carries the item it answered — the changes list groups by the slug ahead
    # of the dash and reads the whole line.
    return FormSpec(
        f"answer item — {textwrap.shorten(item.prompt, width=60, placeholder=' …')}",
        [Field("response", "response", "textarea")],
        initial={"response": item.response or ""},
    )


def apply_rfi_item(
    conn: sqlite3.Connection,
    values: dict[str, Any],
    request_id: str,
    existing: RfiItem | None = None,
) -> RfiItem:
    core = dropped(values)
    if existing:
        # dropped() strips None so a blank optional never clobbers on edit —
        # but on an ITEM every one of these is a value a user legitimately
        # takes back: a group filed under the wrong heading, a date that turned
        # out not to apply, an answer typed against the wrong question. The
        # in-cell editor (i) writes those blanks straight through, so without
        # this the same field cleared two ways gave two different results.
        for key in ("category", "due_on", "detail", "response"):
            if getattr(existing, key) and not values.get(key):
                core[key] = None
    # status OWNS received_on, so the pair can never disagree. `d` sets both
    # together (services.rfi.mark_received) and this form is the only other
    # door — and the TUI's only way to WAIVE an item. Putting an item back to
    # outstanding used to leave the date stamped, so the datasheet rendered an
    # outstanding row carrying a date in its "received" column.
    #
    # The rule moved to services/consistency.settlement_date on 2026-08-20 so
    # a subjectivity could inherit it rather than carry a hand-copied twin —
    # and the move closed two holes this version had: on CREATE, and on an
    # edit of an item that had no date yet, a received date typed BESIDE an
    # outstanding status was written through unchallenged.
    status = core.get("status") or (existing.status if existing else "outstanding")
    core["received_on"] = consistency.settlement_date(
        status,
        values.get("received_on"),
        existing.received_on if existing else None,
        settled_status="received",
        date_label="received on",
        today=date.today().isoformat(),
    )
    # "needed by" against the date the REQUEST was asked. The request's own
    # guard does not reach here: the chase queue prints `item.due_on or
    # request.due_on`, so an item-level date opens life overdue just as easily.
    request_row = rfi_repo.get_request(conn, existing.request_id if existing else request_id)
    consistency.check_item_due(
        request_row.requested_on,
        # `core` already carries an explicit None for a date the user cleared
        # (the blanking loop above), so membership — not truthiness — is what
        # says whether this save changes the field.
        core["due_on"] if "due_on" in core else (existing.due_on if existing else None),
    )
    if existing:
        return rfi_repo.update_item(conn, existing.id, **core)
    prompt = core.pop("prompt")
    return rfi_repo.add_item(conn, request_id, prompt, **core)


# --- interaction ----------------------------------------------------------------

_INTERACTION_TYPES = tuple((t.value, t.value) for t in InteractionType)


def interaction_form(existing: Interaction) -> FormSpec:
    """Edit a logged interaction. Creation stays in quick capture (n), which
    also does account matching and the follow-up-task offer; this is the
    correction path — the body in particular had no way to be read or fixed
    before (review F33)."""
    return FormSpec(
        "edit interaction",
        [
            Field("occurred_on", "when", "date", required=True),
            Field("type", "type", "select", _INTERACTION_TYPES, required=True),
            Field("subject", "subject", required=True),
            Field("body", "note", "textarea"),
        ],
        initial=existing.model_dump(),
    )


def apply_interaction(
    conn: sqlite3.Connection, values: dict[str, Any], existing: Interaction
) -> Interaction:
    core = dropped(values)
    # blanking the note is a legitimate edit, and dropped() would swallow it
    if existing.body and not values.get("body"):
        core["body"] = None
    return interactions.update(conn, existing.id, **core)
