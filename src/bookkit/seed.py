"""Deterministic demo fixture: ~20 accounts, ~15 markets, ~60 contacts,
~200 interactions, ~30 placements, opportunities/submissions/tasks, and three
towerkit program files linked to accounts.

Everything is generated from a seeded RNG relative to `today`, so the demo
always shows a live-looking book (renewals coming due, stale accounts,
submissions past SLA) and tests get stable data from a fixed date.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path
from random import Random
from typing import Any

from towerkit.model import (
    Layer,
    Line,
    Participant,
    Period,
    Program,
    Retention,
    RetentionType,
    dump_program,
)
from towerkit.model import (
    Placement as TkPlacement,
)

from .models import Contact
from .repo import (
    contacts,
    interactions,
    links,
    opportunities,
    orgs,
    placements,
    submissions,
    tasks,
)

CLIENTS = [
    ("Atomic Industries, Inc.", "Manufacturing", "grant"),
    ("Borealis Foods Group", "Food & Beverage", "grant"),
    ("Cascade Health Partners", "Healthcare", "grant"),
    ("Delta Marine Logistics", "Marine & Logistics", "grant"),
    ("Everline Software", "Technology", "grant"),
    ("Foundry Metals Corp", "Metals & Mining", "grant"),
    ("Granite Peak Hotels", "Hospitality", "grant"),
    ("Harborview Utilities", "Utilities", "grant"),
    ("Ironwood Timber", "Forestry", "grant"),
    ("Junction Rail Services", "Transportation", "grant"),
    ("Kestrel Aviation Group", "Aviation", "grant"),
    ("Lumen Data Centers", "Technology", "grant"),
    ("Meridian Pharma", "Life Sciences", "grant"),
    ("Northgate Retail Holdings", "Retail", "grant"),
    ("Orchard Valley Agriculture", "Agriculture", "grant"),
    ("Pinnacle Construction", "Construction", "grant"),
    ("Quartz Financial", "Financial Services", "grant"),
    ("Riverbend Chemicals", "Chemicals", "grant"),
    ("Summit Energy Partners", "Energy", "grant"),
    ("Tidewater Ports Authority", "Marine & Logistics", "grant"),
]

MARKETS = [
    ("Swiss Re", "reinsurer", "A+"),
    ("Munich Re", "reinsurer", "A+"),
    ("Chubb", "carrier", "A++"),
    ("Zurich", "carrier", "A+"),
    ("AIG", "carrier", "A"),
    ("AXA XL", "carrier", "A+"),
    ("Berkshire Hathaway", "carrier", "A++"),
    ("Liberty", "carrier", "A"),
    ("Travelers", "carrier", "A++"),
    ("Beazley", "lloyds", "A"),
    ("Sompo", "carrier", "A+"),
    ("Coalition", "mga", "A-"),
    ("Berkley", "carrier", "A+"),
    ("FM Global", "carrier", "A+"),
    ("Amwins", "wholesaler", None),
]

FIRST = ["Alice", "Bob", "Carmen", "Diego", "Elena", "Frank", "Grace", "Hank",
         "Ivy", "James", "Kara", "Liam", "Mona", "Nate", "Olga", "Pete",
         "Quinn", "Rosa", "Sam", "Tara", "Umar", "Vera", "Walt", "Xia"]
LAST = ["Ng", "Reyes", "Okafor", "Smith", "Johnson", "Lee", "Patel", "Garcia",
        "Kim", "Brown", "Muller", "Rossi", "Tanaka", "Novak", "Silva", "Chen"]
CLIENT_ROLES = ["risk_manager", "cfo", "controller", "general_counsel", "procurement"]
LINES = ["casualty", "property", "cyber", "d&o", "marine", "energy"]
SUBJECTS = [
    "Renewal strategy call", "Exposure data follow-up", "Quarterly review meeting",
    "Claims update", "Market feedback", "Premium indication discussion",
    "Loss run request", "Site visit debrief", "Coverage question",
    "Introduction meeting", "Renewal presentation", "Binder confirmation",
]
INTERACTION_TYPES = ["call", "meeting", "email", "note", "site_visit", "event"]


def _iso(d: date) -> str:
    return d.isoformat()


def seed(
    conn: sqlite3.Connection,
    today: date | None = None,
    programs_dir: Path | None = None,
) -> dict[str, int]:
    """Populate an empty database. Returns row counts by table."""
    rng = Random(42)
    today = today or date.today()

    client_orgs = []
    for name, industry, owner in CLIENTS:
        client_orgs.append(
            orgs.create(
                conn, kind="client", name=name, status="active",
                industry=industry, owner=owner,
                hq_city=rng.choice(["Chicago", "Houston", "Seattle", "Boston", "Denver"]),
                hq_country="US",
            )
        )
    # a couple of prospects and one dormant account round out the book
    orgs.update(conn, client_orgs[13].id, status="prospect")
    orgs.update(conn, client_orgs[14].id, status="prospect")
    orgs.update(conn, client_orgs[8].id, status="dormant")

    market_orgs = []
    for name, market_type, rating in MARKETS:
        market = orgs.create(conn, kind="market", name=name, status="active")
        orgs.set_market_profile(conn, market.id, market_type=market_type, am_best_rating=rating)
        market_orgs.append(market)
        for line in rng.sample(LINES, k=rng.randint(2, 4)):
            orgs.add_appetite(
                conn, market.id, line=line,
                appetite=rng.choice(["target", "target", "will_consider", "selective", "no"]),
                min_premium=rng.choice([25_000_00, 100_000_00, 250_000_00]),
                max_limit=rng.choice([10, 25, 50, 100]) * 1_000_000_00,
                territories="US",
            )

    contact_rows: list[Contact] = []
    for org in client_orgs:
        for i in range(rng.randint(2, 3)):
            contact_rows.append(
                contacts.create(
                    conn, org.id,
                    first_name=rng.choice(FIRST), last_name=rng.choice(LAST),
                    role=rng.choice(CLIENT_ROLES),
                    title=rng.choice(["Director of Risk", "CFO", "Risk Manager", "GC"]),
                    email=f"contact{len(contact_rows)}@example.com",
                    is_primary=1 if i == 0 else 0,
                )
            )
    for market in market_orgs:
        contact_rows.append(
            contacts.create(
                conn, market.id,
                first_name=rng.choice(FIRST), last_name=rng.choice(LAST),
                role="underwriter", title="Senior Underwriter",
                email=f"uw{len(contact_rows)}@example.com", is_primary=1,
            )
        )

    for i in range(200):
        org = rng.choice(client_orgs + market_orgs[:5])
        # skew towards recent, but leave a few accounts stale
        days_ago = rng.randint(0, 45) if org.id not in (
            client_orgs[2].id, client_orgs[6].id, client_orgs[8].id
        ) else rng.randint(70, 300)
        roster = contacts.for_org(conn, org.id)
        interactions.log(
            conn, org.id,
            rng.choice(INTERACTION_TYPES),
            rng.choice(SUBJECTS),
            _iso(today - timedelta(days=days_ago)),
            body=f"Seed interaction #{i}.",
            contact_ids=[c.id for c in rng.sample(roster, k=min(len(roster), rng.randint(1, 2)))],
        )

    placement_rows = []
    for idx, org in enumerate(client_orgs):
        if org.status == "prospect":
            continue
        # renewal month spread across the year; some inside the next 90 days
        expiry = today + timedelta(days=20 + (idx * 37) % 360)
        start = expiry.replace(year=expiry.year - 1)
        premium = rng.choice([350_000, 900_000, 1_500_000, 3_200_000, 8_000_000]) * 100
        placement_rows.append(
            placements.create(
                conn, org.id,
                f"{expiry.year - 1} {rng.choice(['Casualty', 'Property', 'Cyber'])} Program",
                _iso(start), _iso(expiry),
                status=rng.choice(["bound", "bound", "bound", "quoted", "submitted"]),
                total_premium=premium,
                total_limit=rng.choice([25, 50, 100, 200]) * 1_000_000_00,
                commission_bps=rng.choice([1000, 1250, 1500]),
            )
        )
        if idx % 2 == 0:  # prior period too, so history exists
            placement_rows.append(
                placements.create(
                    conn, org.id, f"{expiry.year - 2} Casualty Program",
                    _iso(start.replace(year=start.year - 1)), _iso(start),
                    status="bound" if rng.random() > 0.15 else "lapsed",
                    total_premium=premium * 95 // 100,
                    commission_bps=1250,
                )
            )

    opp_rows = []
    for org in rng.sample(client_orgs, k=10):
        stage = rng.choice(["identified", "qualified", "submitted", "quoted", "presented"])
        opp = opportunities.create(
            conn, org.id, f"New {rng.choice(LINES)} line",
            lines=rng.choice(LINES),
            target_premium=rng.choice([250_000, 500_000, 1_200_000]) * 100,
            target_effective=_iso(today + timedelta(days=rng.randint(30, 180))),
            probability_pct=rng.choice([20, 40, 60, 75]),
            source=rng.choice(["referral", "cross_sell", "rfp"]),
        )
        if stage != "identified":
            from .services import pipeline  # local import: services depend on repo

            order = ["identified", "qualified", "submitted", "quoted", "presented"]
            for next_stage in order[1 : order.index(stage) + 1]:
                pipeline.move_stage(conn, opp.id, next_stage)
        opp_rows.append(opportunities.get(conn, opp.id))

    submission_count = 0
    for placement in rng.sample(placement_rows, k=min(14, len(placement_rows))):
        for market in rng.sample(market_orgs, k=rng.randint(2, 4)):
            sent = today - timedelta(days=rng.randint(3, 30))
            status = rng.choice(["out", "out", "quoted", "declined", "bound"])
            fields: dict[str, Any] = {}
            if status == "quoted" or status == "bound":
                fields = {
                    "quoted_premium": rng.choice([300_000, 800_000, 1_400_000]) * 100,
                    "quoted_limit": rng.choice([10, 25, 50]) * 1_000_000_00,
                    "response_on": _iso(sent + timedelta(days=rng.randint(2, 12))),
                }
            elif status == "declined":
                fields = {
                    "response_on": _iso(sent + timedelta(days=rng.randint(2, 12))),
                    "decline_reason": rng.choice(["capacity", "class of business", "pricing"]),
                }
            submissions.create(
                conn, market.id, _iso(sent), placement_id=placement.id,
                status=status, **fields,
            )
            submission_count += 1

    for _ in range(25):
        org = rng.choice(client_orgs)
        due = today + timedelta(days=rng.randint(-10, 30))
        tasks.create(
            conn,
            rng.choice([
                "Chase loss runs", "Send renewal questionnaire", "Book strategy call",
                "Prepare submission", "Follow up on quote", "Issue binder",
            ]),
            org_id=org.id, due_on=_iso(due), priority=rng.choice([1, 2, 2, 3]),
        )

    from .repo import team

    team_members = []
    for name, title, specialty in [
        ("Dana Okafor", "SVP, Cyber Practice", "cyber, tech E&O"),
        ("Raj Patel", "Property Broker", "property, builders risk"),
        ("Mia Torres", "Casualty Lead", "casualty, umbrella, auto"),
        ("Leo Novak", "Claims Advocate", "claims"),
    ]:
        team_members.append(
            team.create_member(conn, name, title=title, specialty=specialty)
        )
    for org in rng.sample([o for o in client_orgs if o.status == "active"], k=8):
        team.assign(
            conn, rng.choice(team_members).id, org_id=org.id,
            role=rng.choice(["account_lead", "placement_specialist"]),
            lines=rng.choice(LINES),
        )

    program_files = 0
    if programs_dir is not None:
        programs_dir.mkdir(parents=True, exist_ok=True)
        for org, filename in [
            (client_orgs[0], "atomic-casualty.json"),
            (client_orgs[3], "delta-marine.json"),
            (client_orgs[18], "summit-energy.json"),
        ]:
            placement = placements.for_org(conn, org.id)[0]
            path = programs_dir / filename
            program = _demo_program(org.name, placement.period_from, placement.period_to, rng)
            dump_program(program, path)
            links.confirm(conn, str(path), org.id, org.name)
            placements.update(conn, placement.id, program_path=str(path))
            program_files += 1

    return {
        "orgs": len(client_orgs) + len(market_orgs),
        "contacts": len(contact_rows),
        "interactions": 200,
        "placements": len(placement_rows),
        "opportunities": len(opp_rows),
        "submissions": submission_count,
        "tasks": 25,
        "team": len(team_members),
        "program_files": program_files,
    }


def _demo_program(insured: str, period_from: str, period_to: str, rng: Random) -> Program:
    period = Period(start=date.fromisoformat(period_from), end=date.fromisoformat(period_to))
    return Program(
        insured=insured,
        program="Casualty Program",
        placement=TkPlacement.BOUND,
        period=period,
        lines=[
            Line(id="gl", name="General Liability", abbr="GL"),
            Line(id="al", name="Auto Liability", abbr="AL"),
        ],
        layers=[
            Layer(
                id="primary-gl", name="Primary GL", applies_to=["gl"],
                attach=0, limit=2_000_000, premium=rng.choice([900_000, 1_200_000]),
                participants=[Participant(carrier="Zurich", share_bps=10_000)],
            ),
            Layer(
                id="primary-al", name="Primary Auto", applies_to=["al"],
                attach=0, limit=2_000_000, premium=650_000,
                participants=[Participant(carrier="Travelers", share_bps=10_000)],
            ),
            Layer(
                id="umbrella", name="Umbrella", applies_to=["gl", "al"],
                attach=2_000_000, limit=25_000_000, premium=rng.choice([1_800_000, 2_400_000]),
                participants=[
                    Participant(carrier="Swiss Re", share_bps=6_000),
                    Participant(carrier="Chubb", share_bps=4_000),
                ],
            ),
        ],
        retentions=[
            Retention(applies_to=["gl"], type=RetentionType.DEDUCTIBLE, amount=250_000),
            Retention(applies_to=["al"], type=RetentionType.DEDUCTIBLE, amount=100_000),
        ],
    )
