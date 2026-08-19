"""Pipeline tab: quotes in hand, submissions out, opportunities.

Quotes LEAD the tab, above the submissions that produced them. That ordering
is the whole point of the task: the AE review found that bookkit tracked work
sent and work bound and nothing in between, so the moment a market answered,
the row left the past-SLA queue and entered no queue anywhere — and the three
weeks of comparing terms and chasing subjectivities happened off the tool.
The section a broker opens this tab for is the one whose terms are running
out, so it is the section they see first.

Read-only for now, and the panel says so rather than showing controls that do
nothing: quotes are recorded through the TUI's `e` on a submission
(forms.entities.response_form, which both surfaces share). Writing them here
means the whole submission/response form contract on the web, which is its
own task; an inert "+ Add" button would be the dead-nav defect this same
review called the worst thing on the web surface.

Registered BEFORE routes/account.py's generic GET /accounts/{ref}/{tab}
(see web/app.py): Starlette resolves in registration order, not by
specificity, so this module's explicit path is what serves the tab.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from starlette.requests import Request

from ...dates import days_until
from ...models import Org
from ...money import format_cents_compact
from ...repo import opportunities as opportunities_repo
from ...repo import submissions as submissions_repo
from ...services import quotes as quotes_svc
from ..app import TEMPLATES
from .account import _conn, _context, _org

router = APIRouter()


def _money(cents: int | None) -> str:
    """An em dash for nothing, never "$0.00" — the same reading the TUI's
    theme.money_text gives, and the difference matters here: a quote with no
    premium recorded yet is not a quote for nothing."""
    return format_cents_compact(cents) if cents else "—"


def _quote_rows(request: Request, org: Org) -> list[dict[str, object]]:
    """Every quote in hand for this account — expiry or not.

    `services.quotes.for_org`, not `expiring`: the 120-day queue is the
    book-wide chase list, while an account's own tab must show a quote whose
    expiry nobody has recorded yet. That one reads "no expiry", which is a
    prompt to go and ask, not an omission.
    """
    today = date.today()
    rows: list[dict[str, object]] = []
    for quote in quotes_svc.for_org(_conn(request), org.id, org.name, today=today):
        rows.append({
            "market": quote.market_name,
            "about": quote.about,
            "underwriter": quote.underwriter_name,
            "underwriter_email": quote.underwriter_email,
            "premium": _money(quote.submission.quoted_premium),
            "limit": _money(quote.submission.quoted_limit),
            # date and word are read off ONE object: QuoteItem.expires_on and
            # QuoteItem.expiry_word both resolve submission.quote_expires_on,
            # so the template cannot print a date beside a count from
            # somewhere else (CLAUDE.md's "70d over" defect).
            "expires_on": quote.expires_on,
            "expiry_word": quote.expiry_word,
            "expiry_state": quote.expiry_state,
            "open_subjectivities": quote.open_subjectivities,
            "total_subjectivities": quote.total_subjectivities,
        })
    return rows


def _subjectivity_rows(request: Request, org: Org) -> list[dict[str, object]]:
    """What this client still owes a market before a quote can be bound."""
    today = date.today()
    rows: list[dict[str, object]] = []
    for row in submissions_repo.outstanding_subjectivity_rows_for_org(
        _conn(request), org.id
    ):
        days = days_until(row["due_on"], today) if row["due_on"] else None
        rows.append({
            "description": row["description"],
            "market": row["market_name"],
            "about": row["about"],
            "due_on": row["due_on"],
            "days": days,
            "overdue": days is not None and days < 0,
            "status": row["status"],
        })
    return rows


def _submission_rows(request: Request, org: Org) -> list[dict[str, object]]:
    """Still out at market, no answer yet — the queue services/sla.py counts
    days against. Unchanged in meaning; it simply now has a section above it
    for what happens when an answer arrives."""
    today = date.today()
    rows: list[dict[str, object]] = []
    for row in submissions_repo.outstanding_for_org(_conn(request), org.id):
        rows.append({
            "market": row["market_name"],
            "about": row["about"],
            "sent_on": row["sent_on"],
            "days_out": -days_until(row["sent_on"], today),
        })
    return rows


def _opportunity_rows(request: Request, org: Org) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for opp in opportunities_repo.for_org(_conn(request), org.id, open_only=False):
        rows.append({
            "ref": opp.ref,
            "title": opp.title,
            "stage": opp.stage,
            "target_premium": _money(opp.target_premium),
            "probability_pct": opp.probability_pct,
        })
    return rows


@router.get("/accounts/{ref}/pipeline", response_class=HTMLResponse)
def pipeline_tab(request: Request, ref: str) -> HTMLResponse:
    conn = _conn(request)
    org = _org(request, ref)
    context = _context(conn, org, "pipeline", request)
    context["quote_rows"] = _quote_rows(request, org)
    context["subjectivity_rows"] = _subjectivity_rows(request, org)
    context["submission_rows"] = _submission_rows(request, org)
    context["opportunity_rows"] = _opportunity_rows(request, org)
    return TEMPLATES.TemplateResponse(request, "account/pipeline.html", context)
